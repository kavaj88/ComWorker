#!/usr/bin/env python3
"""ComWorker 离线部署脚本。

用于无法访问外网的服务器。分三步操作：

  步骤 1 — 在有网络的机器上构建所有镜像
    python offline_deploy.py pack --host <目标服务器IP>

  步骤 2 — 将生成的 tar 包和项目文件拷贝到目标服务器
    （脚本会输出具体的 scp/rsync 命令）

  步骤 3 — 在目标服务器上导入镜像并启动
    python offline_deploy.py deploy --host <本机IP>

完整示例：
  # 机器 A（有网络）
  python offline_deploy.py pack --host 192.168.1.100
  scp comworker-images.tar user@192.168.1.100:/data/server/comworker/

  # 机器 B（目标服务器，无网络）
  cd /data/server/comworker
  python offline_deploy.py deploy --host 192.168.1.100
"""

import argparse
import os
import shutil
import subprocess
import sys
import time
import concurrent.futures

# ── 颜色输出 ──────────────────────────────────────────────────────────
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
BOLD = "\033[1m"
RESET = "\033[0m"

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))

# 所有需要导出的镜像
COMPOSE_IMAGES = ["comworker-gateway", "comworker-manage"]
BASE_IMAGES = ["hermes-base:latest", "comworker-hermes-agent:latest"]
EXTERNAL_IMAGES = ["postgres:16-alpine"]

ALL_IMAGES = COMPOSE_IMAGES + BASE_IMAGES + EXTERNAL_IMAGES

TAR_FILENAME = "comworker-images.tar"


def log(msg: str, color: str = CYAN):
    print(f"{color}{BOLD}▸{RESET} {msg}")


def success(msg: str):
    print(f"{GREEN}✓{RESET} {msg}")


def error(msg: str):
    print(f"{RED}✗{RESET} {msg}")


def warn(msg: str):
    print(f"{YELLOW}⚠{RESET} {msg}")


def run(cmd: str, cwd: str | None = None, check: bool = True, **kwargs) -> subprocess.CompletedProcess:
    log(f"执行: {cmd}")
    result = subprocess.run(cmd, cwd=cwd or PROJECT_DIR, shell=True, check=False, **kwargs)
    if check and result.returncode != 0:
        error(f"命令失败 (exit {result.returncode}): {cmd}")
        sys.exit(1)
    return result


def check_docker():
    for cmd, name in [("docker --version", "Docker"), ("docker compose version", "Docker Compose")]:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if result.returncode != 0:
            error(f"{name} 未安装或无法访问")
            sys.exit(1)
        success(f"{name}: {result.stdout.strip()}")


def _build_task(name: str, cmd: str):
    log(f"[并行] 开始构建: {name}")
    start = time.time()
    result = subprocess.run(cmd, shell=True, cwd=PROJECT_DIR)
    elapsed = time.time() - start
    if result.returncode == 0:
        success(f"[并行] {name} 构建完成 ({elapsed:.0f}s)")
    else:
        error(f"[并行] {name} 构建失败 (exit {result.returncode}, {elapsed:.0f}s)")
    return name, result.returncode, elapsed


# =====================================================================
# 步骤 1：打包（在有网络的机器上执行）
# =====================================================================

def cmd_pack(args):
    print(f"\n{BOLD}📦 步骤 1：构建并打包镜像{RESET}\n")

    check_docker()

    host = args.host
    gateway_port = args.gateway_port
    compose_file = args.compose

    # 设置前端构建参数
    if args.relative_api:
        api_url = ""
    else:
        api_url = f"http://{host}:{gateway_port}"
    os.environ["VITE_API_URL"] = api_url
    log(f"VITE_API_URL = {api_url or '<relative>'}")

    compose_args = f"-f {compose_file}"

    # 并行构建 hermes 基础镜像 + compose 服务
    log("并行构建所有镜像...")
    tasks = {
        "hermes-base:latest": "docker build --no-cache -f hermes-agent/Dockerfile -t hermes-base:latest hermes-agent/",
        "comworker-hermes-agent:latest": "docker build --no-cache -f hermes-agent/Dockerfile.bridge -t comworker-hermes-agent:latest hermes-agent/",
        "compose services": f"docker compose {compose_args} build --parallel",
    }
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        futures = {pool.submit(_build_task, name, cmd): name for name, cmd in tasks.items()}
        failed = []
        for future in concurrent.futures.as_completed(futures):
            name, rc, elapsed = future.result()
            if rc != 0:
                failed.append(name)
    if failed:
        error(f"以下构建失败: {', '.join(failed)}")
        sys.exit(1)
    success("所有镜像构建完成")

    # 确保 postgres 镜像已拉取
    log("拉取外部依赖镜像...")
    for img in EXTERNAL_IMAGES:
        run(f"docker pull {img}", check=False)
    success("外部镜像就绪")

    # 导出所有镜像到单个 tar
    tar_path = os.path.join(PROJECT_DIR, TAR_FILENAME)
    images_str = " ".join(ALL_IMAGES)
    log(f"导出镜像到 {TAR_FILENAME} ...")

    # 显示镜像大小
    for img in ALL_IMAGES:
        result = subprocess.run(
            f'docker image inspect {img} --format "{{{{.Size}}}}" 2>/dev/null',
            shell=True, capture_output=True, text=True,
        )
        size = result.stdout.strip()
        if size and size.isdigit():
            size_mb = int(size) / (1024 * 1024)
            log(f"  {img}: {size_mb:.0f} MB")

    run(f"docker save -o {TAR_FILENAME} {images_str}")

    tar_size = os.path.getsize(tar_path) / (1024 * 1024)
    success(f"镜像包已生成: {TAR_FILENAME} ({tar_size:.0f} MB)")

    # 打印后续步骤
    print(f"\n{BOLD}{'=' * 60}{RESET}")
    print(f"{BOLD}  📋 步骤 2：将以下文件拷贝到目标服务器{RESET}")
    print(f"{'=' * 60}")
    print()
    print(f"  需要拷贝的文件/目录：")
    print(f"    1. {TAR_FILENAME} ({tar_size:.0f} MB) — 所有 Docker 镜像")
    print(f"    2. 项目目录（包含 docker-compose.yml, .env 等配置）")
    print()
    print(f"  推荐命令：")
    print(f"    # 方式 1：单独拷贝镜像包")
    print(f"    scp {tar_path} user@{host}:/data/server/comworker/")
    print()
    print(f"    # 方式 2：rsync 整个项目（包含镜像包）")
    print(f"    rsync -avz --progress {PROJECT_DIR}/ user@{host}:/data/server/comworker/")
    print()
    print(f"  拷贝完成后，在目标服务器上执行：")
    print(f"    cd /data/server/comworker")
    print(f"    python offline_deploy.py deploy --host {host}")
    print(f"{'=' * 60}\n")


# =====================================================================
# 步骤 3：部署（在目标服务器上执行）
# =====================================================================

def cmd_deploy(args):
    print(f"\n{BOLD}🚀 步骤 3：导入镜像并启动服务{RESET}\n")

    check_docker()

    host = args.host
    gateway_port = args.gateway_port
    frontend_port = args.frontend_port
    compose_file = args.compose

    tar_path = os.path.join(PROJECT_DIR, TAR_FILENAME)

    # 导入镜像
    if os.path.exists(tar_path):
        tar_size = os.path.getsize(tar_path) / (1024 * 1024)
        log(f"导入镜像: {TAR_FILENAME} ({tar_size:.0f} MB) ...")
        run(f"docker load -i {TAR_FILENAME}")
        success("镜像导入完成")
    else:
        error(f"镜像包不存在: {tar_path}")
        print(f"  请先将 {TAR_FILENAME} 拷贝到当前目录")
        sys.exit(1)

    # 验证镜像
    log("验证镜像...")
    missing = []
    for img in ALL_IMAGES:
        result = subprocess.run(
            f"docker image inspect {img}",
            shell=True, capture_output=True, text=True,
        )
        if result.returncode != 0:
            missing.append(img)
        else:
            success(f"  {img}")
    if missing:
        error(f"以下镜像缺失: {', '.join(missing)}")
        sys.exit(1)
    success("所有镜像验证通过")

    # 检查 .env
    env_path = os.path.join(PROJECT_DIR, ".env")
    if not os.path.exists(env_path):
        warn(".env 文件不存在，将使用默认配置")
        warn("建议创建 .env 文件并配置 API Key 和管理员账号")
    else:
        success(".env 文件存在")

    # 设置环境变量
    if args.relative_api:
        os.environ["VITE_API_URL"] = ""
    else:
        os.environ["VITE_API_URL"] = f"http://{host}:{gateway_port}"

    # 启动服务
    compose_args = f"-f {compose_file}"
    log("启动所有服务...")
    run(f"docker compose {compose_args} up -d")
    success("所有服务已启动")

    # 健康检查
    if not args.skip_health:
        import urllib.request
        import json

        log("等待服务就绪...")
        check_host = "localhost"

        # 检查 gateway
        gateway_url = f"http://{check_host}:{gateway_port}/api/ping"
        for i in range(1, 31):
            try:
                req = urllib.request.Request(gateway_url)
                with urllib.request.urlopen(req, timeout=3) as resp:
                    data = json.loads(resp.read())
                    if data.get("message") == "pong":
                        success(f"Gateway 就绪")
                        break
            except Exception:
                pass
            sys.stdout.write(f"\r  等待 Gateway... ({i}/30)")
            sys.stdout.flush()
            time.sleep(2)
        else:
            print()
            warn("Gateway 未在预期时间内就绪，请手动检查")

        # 检查 frontend
        frontend_url = f"http://{check_host}:{frontend_port}"
        for i in range(1, 16):
            try:
                req = urllib.request.Request(frontend_url)
                with urllib.request.urlopen(req, timeout=3) as resp:
                    if resp.status < 400:
                        success(f"Frontend 就绪")
                        break
            except Exception:
                pass
            sys.stdout.write(f"\r  等待 Frontend... ({i}/15)")
            sys.stdout.flush()
            time.sleep(2)
        else:
            print()
            warn("Frontend 未在预期时间内就绪，请手动检查")

    # 状态摘要
    print(f"\n{BOLD}{'=' * 60}{RESET}")
    print(f"{BOLD}  ComWorker 离线部署完成{RESET}")
    print(f"{'=' * 60}")
    print(f"  用户前端:     http://{host}:{frontend_port}")
    print(f"  管理员前端:   http://{host}:3081")
    print(f"  Gateway:      http://{host}:{gateway_port}")
    print(f"  Compose 文件: {compose_file}")
    print(f"{'=' * 60}\n")

    run(f"docker compose {compose_args} ps", check=False)
    print(f"\n{BOLD}======== [ 🎉 离线部署完成！ ] ========{RESET}\n")


# =====================================================================
# 入口
# =====================================================================

def main():
    parser = argparse.ArgumentParser(
        description="ComWorker 离线部署脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # pack
    p_pack = sub.add_parser("pack", help="步骤 1: 构建镜像并导出为 tar 包（在有网络的机器上执行）")
    p_pack.add_argument("--host", required=True, help="目标服务器 IP（用于设置 VITE_API_URL）")
    p_pack.add_argument("--compose", default="docker-compose.yml", help="compose 文件 (默认: docker-compose.yml)")
    p_pack.add_argument("--gateway-port", type=int, default=8080, help="Gateway 端口 (默认: 8080)")
    p_pack.add_argument("--relative-api", action="store_true", help="前端使用相对 API 路径")

    # deploy
    p_deploy = sub.add_parser("deploy", help="步骤 3: 导入镜像并启动服务（在目标服务器上执行）")
    p_deploy.add_argument("--host", required=True, help="本机 IP 或域名（用于状态显示）")
    p_deploy.add_argument("--compose", default="docker-compose.yml", help="compose 文件 (默认: docker-compose.yml)")
    p_deploy.add_argument("--gateway-port", type=int, default=8080, help="Gateway 端口 (默认: 8080)")
    p_deploy.add_argument("--frontend-port", type=int, default=3080, help="Frontend 端口 (默认: 3080)")
    p_deploy.add_argument("--relative-api", action="store_true", help="前端使用相对 API 路径")
    p_deploy.add_argument("--skip-health", action="store_true", help="跳过健康检查")

    args = parser.parse_args()
    os.chdir(PROJECT_DIR)

    if args.command == "pack":
        cmd_pack(args)
    elif args.command == "deploy":
        cmd_deploy(args)


if __name__ == "__main__":
    main()
