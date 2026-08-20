#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
inject_connector_env.py — 把连接器 OAuth 预注册密钥注入 hermes 容器的 /opt/data/.env

背景
----
部分 OAuth 连接器（qqmail、tencent-docs-oa）在 mcp.json 里用 ``${VAR}`` 占位符
声明 client_id / client_secret（例如 ``${QQMAIL_CLIENT_ID}``）。运行期 hermes 进程在
import 时调用 ``load_hermes_dotenv()`` 把 ``$HERMES_HOME/.env`` 载入 ``os.environ``，
中继 ``_resolve_env_val`` 再从中解析 ``${VAR}``。平台本身**不会**把这些密钥写进容器，
所以需要一个离带外的注入手段——本脚本就是干这个的。

原理
----
1. 把密钥追加进每个目标容器内的 ``/opt/data/.env``（幂等：已存在的键不重复写）。
2. 保持文件权限 600、属主 hermes:hermes（hermes 进程以 uid 10000 运行，必须能读）。
3. 重启 hermes 容器（或等下次重建），使 ``load_hermes_dotenv()`` 重新加载 .env。

哪些连接器需要
--------------
- qqmail              → QQMAIL_CLIENT_ID / QQMAIL_CLIENT_SECRET
- tencent-docs-oa     → TENCENT_DOCS_OA_CLIENT_ID / TENCENT_DOCS_OA_CLIENT_SECRET
- tencent_docs/lexiang → 走 DCR，无需注入
- ima                 → api_key（Bearer 头），无需注入

用法
----
  # 1) 先把密钥放进一个本地文件（不要提交进 git！）
  cat > /secure/connector_oauth_secrets.env <<'EOF'
  QQMAIL_CLIENT_ID=1018xxxxxx
  QQMAIL_CLIENT_SECRET=abcdefxxxx
  TENCENT_DOCS_OA_CLIENT_ID=oa_xxxx
  TENCENT_DOCS_OA_CLIENT_SECRET=oa_secret_xxxx
  EOF
  chmod 600 /secure/connector_oauth_secrets.env

  # 2) 干跑预览（不改动任何容器）
  python3 inject_connector_env.py --env-file /secure/connector_oauth_secrets.env --dry-run

  # 3) 真正注入（仅写入 .env，不重启）
  python3 inject_connector_env.py --env-file /secure/connector_oauth_secrets.env

  # 4) 注入并立即重启容器（会中断在跑的会话，建议在维护窗口执行）
  python3 inject_connector_env.py --env-file /secure/connector_oauth_secrets.env --restart

也可用 --set 多次指定单键，或 --name 只处理某个容器，或 --prefix 改匹配前缀。

注意：本脚本只处理“已运行”的容器。停止中的容器请先启动再跑（或等平台重建）。
"""

import argparse
import os
import shlex
import subprocess
import sys
import tempfile

# 已知连接器需要的变量名（用于 --known 模式的安全过滤，避免把无关环境变量倒进容器）
KNOWN_VARS = {
    "QQMAIL_CLIENT_ID",
    "QQMAIL_CLIENT_SECRET",
    "TENCENT_DOCS_OA_CLIENT_ID",
    "TENCENT_DOCS_OA_CLIENT_SECRET",
}

DOTENV_PATH = "/opt/data/.env"


def run(cmd, check=True, capture=True):
    """执行命令，返回 CompletedProcess。"""
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=True,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"命令失败 {shlex.join(cmd)}: rc={proc.returncode}\n{proc.stderr or ''}"
        )
    return proc


def docker_available(docker_bin):
    try:
        run([docker_bin, "version", "--format", "{{.Server.Version}}"], check=False)
        return True
    except FileNotFoundError:
        return False


def list_target_containers(docker_bin, prefix, name, all_containers):
    if name:
        # 验证该容器存在
        p = run([docker_bin, "inspect", "-f", "{{.Id}}", name], check=False)
        if p.returncode != 0:
            print(f"[warn] 容器不存在: {name}", file=sys.stderr)
            return []
        return [name]
    filters = ["--filter", f"name={prefix}"]
    if not all_containers:
        filters = ["--filter", "status=running"] + filters
    p = run([docker_bin, "ps", "-a", "--format", "{{.Names}}"] + filters, check=False)
    names = [n for n in p.stdout.splitlines() if n.strip()]
    return names


def container_running(docker_bin, name):
    p = run(
        [docker_bin, "inspect", "-f", "{{.State.Running}}", name],
        check=False,
    )
    return p.stdout.strip() == "true"


def read_existing_dotenv(docker_bin, name):
    """读容器内 .env 内容；不存在返回空串。"""
    p = run(
        [docker_bin, "exec", "-u", "root", name, "cat", DOTENV_PATH],
        check=False,
    )
    if p.returncode != 0:
        return ""
    return p.stdout


def parse_env_file(path):
    vars_dict = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            vars_dict[k.strip()] = v
    return vars_dict


def inject_into_container(docker_bin, name, vars_dict, restart, dry_run):
    existing = read_existing_dotenv(docker_bin, name)
    existing_keys = set()
    for line in existing.splitlines():
        if "=" in line:
            existing_keys.add(line.split("=", 1)[0].strip())

    to_add = {k: v for k, v in vars_dict.items() if k not in existing_keys}
    already = [k for k in vars_dict if k in existing_keys]

    if not to_add:
        print(f"[skip] {name}: 全部变量已存在，无需改动 ({', '.join(sorted(vars_dict)) or '无'})")
        return "skip"

    if dry_run:
        print(f"[dry-run] {name}: 将追加 {len(to_add)} 个变量: {', '.join(sorted(to_add))}")
        return "dry-run"

    # 把待追加内容写成本地临时文件，再 docker cp 进容器，避免 heredoc 引号问题
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".env", delete=False, encoding="utf-8"
    ) as tf:
        tf.write("\n".join(f"{k}={v}" for k, v in to_add.items()) + "\n")
        local_tmp = tf.name
    try:
        run([docker_bin, "cp", local_tmp, f"{name}:/tmp/connector_append.env"], check=True)
        run(
            [
                docker_bin, "exec", "-u", "root", name, "sh", "-c",
                f"cat /tmp/connector_append.env >> {DOTENV_PATH} && rm -f /tmp/connector_append.env",
            ],
            check=True,
        )
        # 保持权限与属主：hermes 进程以 uid 10000 运行，必须可读
        run([docker_bin, "exec", "-u", "root", name, "chown", "hermes:hermes", DOTENV_PATH], check=True)
        run([docker_bin, "exec", "-u", "root", name, "chmod", "600", DOTENV_PATH], check=True)
    finally:
        os.unlink(local_tmp)

    if already:
        print(f"[ok] {name}: 追加 {sorted(to_add)} (已存在跳过 {sorted(already)})")
    else:
        print(f"[ok] {name}: 追加 {sorted(to_add)}")
    if restart:
        print(f"[restart] {name}: 重启容器使 .env 生效 ...")
        run([docker_bin, "restart", name], check=True)
    else:
        print(f"[info] {name}: 需重启容器（或等平台重建）后密钥才会被 load_hermes_dotenv() 加载")
    return "injected"


def main():
    ap = argparse.ArgumentParser(
        description="把连接器 OAuth 预注册密钥注入 hermes 容器的 /opt/data/.env",
    )
    ap.add_argument("--env-file", help="含 KEY=VALUE 的本地 .env 文件（推荐）")
    ap.add_argument("--set", action="append", default=[], help="单条 KEY=VALUE，可重复")
    ap.add_argument("--known", action="store_true",
                    help="只注入 KNOWN_VARS 中出现在环境/文件里的变量（安全过滤）")
    ap.add_argument("--prefix", default="hermes-user-",
                    help="容器名前缀（默认 hermes-user-）")
    ap.add_argument("--name", help="只处理指定容器名")
    ap.add_argument("--all", action="store_true",
                    help="也处理停止中的容器（仍要求可 exec，即实际运行中）")
    ap.add_argument("--restart", action="store_true",
                    help="注入后重启容器（会中断在跑的会话）")
    ap.add_argument("--dry-run", action="store_true", help="只预览，不改动")
    ap.add_argument("--docker", default="docker", help="docker 可执行文件（默认 docker）")
    args = ap.parse_args()

    if not args.env_file and not args.set:
        ap.error("必须提供 --env-file 或至少一个 --set")

    docker_bin = args.docker
    if not docker_available(docker_bin):
        print(f"[error] 找不到 docker（{docker_bin}），请在装有 docker 的操作机执行。", file=sys.stderr)
        sys.exit(2)

    # 收集变量
    vars_dict = {}
    if args.env_file:
        vars_dict.update(parse_env_file(args.env_file))
    for item in args.set:
        if "=" not in item:
            ap.error(f"--set 必须是 KEY=VALUE 形式: {item}")
        k, v = item.split("=", 1)
        vars_dict[k.strip()] = v
    if not vars_dict:
        print("[error] 没有任何变量可注入", file=sys.stderr)
        sys.exit(2)

    if args.known:
        filtered = {k: v for k, v in vars_dict.items() if k in KNOWN_VARS}
        dropped = set(vars_dict) - set(filtered)
        if dropped:
            print(f"[info] --known 模式忽略非连接器变量: {', '.join(sorted(dropped))}")
        vars_dict = filtered
        if not vars_dict:
            print("[error] --known 模式下没有已知连接器变量可注入", file=sys.stderr)
            sys.exit(2)

    targets = list_target_containers(docker_bin, args.prefix, args.name, args.all)
    if not targets:
        print("[error] 未匹配到任何容器。检查 --prefix / --name。", file=sys.stderr)
        sys.exit(1)

    print(f"==> 目标容器 {len(targets)} 个，变量 {sorted(vars_dict)}")
    summary = {"injected": 0, "skip": 0, "dry-run": 0, "error": 0}
    for name in targets:
        if not container_running(docker_bin, name):
            print(f"[warn] {name}: 容器未运行，跳过（先启动或等平台重建后再跑）", file=sys.stderr)
            summary["error"] += 1
            continue
        try:
            res = inject_into_container(docker_bin, name, vars_dict, args.restart, args.dry_run)
            summary[res] = summary.get(res, 0) + 1
        except Exception as e:  # noqa: BLE001
            print(f"[error] {name}: {e}", file=sys.stderr)
            summary["error"] += 1

    print(
        f"==> 完成: 注入 {summary['injected']} / 跳过 {summary['skip']} / "
        f"干跑 {summary['dry-run']} / 异常 {summary['error']}"
    )
    if not args.restart and summary["injected"]:
        print("[提醒] 已注入的容器需重启（--restart）或等平台重建，密钥才会生效。")


if __name__ == "__main__":
    main()
