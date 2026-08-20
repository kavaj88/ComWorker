#!/usr/bin/env python3
"""ComWorker Docker 部署脚本。

构建运行时基础镜像并通过 docker compose 启动所有服务
（postgres + gateway + manage-front + cca-front-dev）。
支持本地部署和远程服务器部署（通过 SSH）。

用法:
  # 本地部署（默认端口 gateway:8080, manage-front:3081, cca-front:5174）
  python deploy_docker.py

  # 指定服务器 IP（会自动设置 VITE_API_URL）
  python deploy_docker.py --host 192.168.1.160

  # 反向代理场景：前端使用相对路径（如 /api/...）
  python deploy_docker.py --host 117.133.60.219 --relative-api

  # 使用 prod compose 文件
  python deploy_docker.py --host 117.133.60.219 --compose docker-compose.yml.prod

  # 仅构建基础镜像不启动
  python deploy_docker.py --build-only

  # 仅重启服务
  python deploy_docker.py --restart

  # 重建指定服务（逗号分隔，hermes 表示 Hermes 基础镜像）
  python deploy_docker.py --rebuild hermes,gateway,manage-front
  python deploy_docker.py --rebuild gateway
  python deploy_docker.py --rebuild hermes --with-browser

  # 使用缓存快速重建（不使用 --no-cache）
  python deploy_docker.py --rebuild gateway --fast
  python deploy_docker.py --rebuild hermes,gateway --fast

  # 完全清理重建
  python deploy_docker.py --clean
"""

import argparse
import concurrent.futures
import os
import shlex
import shutil
import subprocess
import sys
import time
# ── 颜色输出 ──────────────────────────────────────────────────────────
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
BOLD = "\033[1m"
RESET = "\033[0m"

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTO_START_DOCKER_ENV = "COMWORKER_AUTO_START_DOCKER_DESKTOP"
DEFAULT_APT_DEBIAN_MIRROR = "http://mirrors.ustc.edu.cn/debian"
DEFAULT_APT_SECURITY_MIRROR = "http://mirrors.ustc.edu.cn/debian-security"


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def resolve_vite_api_url(host: str, gateway_port: int, relative_api: bool) -> str:
    """Resolve frontend API base URL for build args.

    - relative_api=True  -> ""  (frontend uses relative path, e.g. /api/auth/login)
    - relative_api=False -> "http://{host}:{gateway_port}"
    """
    if relative_api:
        return ""
    return f"http://{host}:{gateway_port}"


def log(msg: str, color: str = CYAN):
    print(f"{color}{BOLD}▸{RESET} {msg}")


def success(msg: str):
    print(f"{GREEN}✓{RESET} {msg}")


def error(msg: str):
    print(f"{RED}✗{RESET} {msg}")


def warn(msg: str):
    print(f"{YELLOW}⚠{RESET} {msg}")


def run(cmd: str | list[str], cwd: str | None = None, check: bool = True, **kwargs) -> subprocess.CompletedProcess:
    """执行命令并实时输出。"""
    if isinstance(cmd, str):
        cmd_display = cmd
    else:
        cmd_display = " ".join(cmd)
    log(f"执行: {cmd_display}")
    result = subprocess.run(
        cmd if isinstance(cmd, list) else cmd,
        cwd=cwd or PROJECT_DIR,
        shell=isinstance(cmd, str),
        check=False,
        **kwargs,
    )
    if check and result.returncode != 0:
        error(f"命令失败 (exit {result.returncode}): {cmd_display}")
        sys.exit(1)
    return result


def check_prerequisites():
    """检查 docker 和 docker compose 是否可用。"""
    log("检查前置依赖...")

    if env_flag(AUTO_START_DOCKER_ENV):
        if not docker_result.ok:
            error(docker_result.error or "Docker daemon 未运行，请先启动 Docker")
            sys.exit(1)
        if docker_result.started_desktop:
            success("Docker Desktop 已通过 PowerShell 启动")

    for cmd, name in [("docker --version", "Docker"), ("docker compose version", "Docker Compose")]:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if result.returncode != 0:
            error(f"{name} 未安装或无法访问")
            sys.exit(1)
        success(f"{name}: {result.stdout.strip()}")

    # 检查 docker daemon
    result = subprocess.run("docker info", shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        error(
            "Docker daemon 未运行，请先启动 Docker；WSL 本机场景可显式设置 "
            f"{AUTO_START_DOCKER_ENV}=1 后再运行"
        )
        sys.exit(1)
    success("Docker daemon 运行中")


def check_env_file():
    """检查 .env 文件是否存在且包含至少一个 API Key。"""
    env_path = os.path.join(PROJECT_DIR, ".env")
    if not os.path.exists(env_path):
        warn(".env 文件不存在，将使用默认配置")
        warn("建议创建 .env 文件并配置至少一个 LLM API Key")
        return

    with open(env_path, "r", encoding="utf-8") as f:
        content = f.read()

    key_vars = [
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "DEEPSEEK_API_KEY",
        "OPENROUTER_API_KEY",
        "DASHSCOPE_API_KEY",
        "AIHUBMIX_API_KEY",
        "EVOLINK_API_KEY",
        "MOONSHOT_API_KEY",
        "ZHIPU_API_KEY",
        "HOSTED_VLLM_API_KEY",
    ]
    found_keys = []
    for var in key_vars:
        for line in content.splitlines():
            line = line.strip()
            if line.startswith(f"{var}=") and not line.endswith("=") and "xxxx" not in line:
                found_keys.append(var)
                break

    if found_keys:
        success(f".env 已配置 API Key: {', '.join(found_keys)}")
    else:
        warn(".env 中未找到有效的 API Key，请确认配置")

    # Check admin account config
    admin_user = ""
    admin_pass = ""
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("ADMIN_USERNAME=") and not line.endswith("="):
            admin_user = line.split("=", 1)[1].strip().strip("'\"")
        if line.startswith("ADMIN_PASSWORD=") and not line.endswith("="):
            admin_pass = line.split("=", 1)[1].strip().strip("'\"")
    if admin_user and admin_pass:
        success(f"管理员账号已配置: {admin_user}")
    else:
        warn("未配置管理员账号 (ADMIN_USERNAME / ADMIN_PASSWORD)，管理后台将无法登录")


def sync_deploy_copy_to_hermes():
    """将 deploy_copy 内容复制到 hermes-agent/deploy_copy/，供 Hermes 镜像构建打包。"""
    deploy_dir = os.path.join(PROJECT_DIR, "deploy_copy")
    if not os.path.isdir(deploy_dir):
        return

    dst = os.path.join(PROJECT_DIR, "hermes-agent", "deploy_copy")
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(deploy_dir, dst)
    success("deploy_copy → hermes-agent/deploy_copy/ 已同步")


def sync_deploy_copy_to_platform():
    """将 gateway 需要的 agent metadata 复制到 platform/deploy_copy/。"""
    deploy_dir = os.path.join(PROJECT_DIR, "deploy_copy")
    if not os.path.isdir(deploy_dir):
        return

    dst = os.path.join(PROJECT_DIR, "platform", "deploy_copy")
    os.makedirs(dst, exist_ok=True)

    for name in os.listdir(dst):
        if name == ".gitkeep":
            continue
        path = os.path.join(dst, name)
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        else:
            os.remove(path)

    for name in ("comworker_defaults.json", "Agents"):
        src = os.path.join(deploy_dir, name)
        if not os.path.exists(src):
            continue
        target = os.path.join(dst, name)
        if os.path.isdir(src):
            shutil.copytree(src, target)
        else:
            shutil.copy2(src, target)

    success("deploy_copy agent metadata → platform/deploy_copy/ 已同步")


def _hermes_build_arg_flags(with_browser: bool) -> list[str]:
    apt_debian_mirror = os.environ.get("APT_DEBIAN_MIRROR", DEFAULT_APT_DEBIAN_MIRROR).strip()
    apt_security_mirror = os.environ.get("APT_SECURITY_MIRROR", DEFAULT_APT_SECURITY_MIRROR).strip()
    flags: list[str] = [
        f"--build-arg APT_DEBIAN_MIRROR={shlex.quote(apt_debian_mirror)}",
        f"--build-arg APT_SECURITY_MIRROR={shlex.quote(apt_security_mirror)}",
    ]
    if with_browser:
        flags.append("--build-arg HERMES_INSTALL_BROWSER=true")
        download_host = os.environ.get("PLAYWRIGHT_DOWNLOAD_HOST", "").strip()
        if download_host:
            flags.append(f"--build-arg PLAYWRIGHT_DOWNLOAD_HOST={shlex.quote(download_host)}")
    install_whatsapp_bridge = os.environ.get("HERMES_INSTALL_WHATSAPP_BRIDGE", "").strip().lower()
    if install_whatsapp_bridge in {"1", "true", "yes", "on"}:
        flags.append("--build-arg HERMES_INSTALL_WHATSAPP_BRIDGE=true")
    return flags


def _hermes_build_command(*, use_cache: bool = False, with_browser: bool = False) -> str:
    parts = ["docker build"]
    if not use_cache:
        parts.append("--no-cache")
    parts.extend(_hermes_build_arg_flags(with_browser))
    parts.extend(
        [
            "-f hermes-agent/Dockerfile.bridge",
            "-t comworker-hermes-agent:latest",
            "hermes-agent/",
        ]
    )
    return " ".join(parts)


def build_hermes_image(*, with_browser: bool = False):
    """单次构建 Hermes dedicated bridge 镜像（Dockerfile.bridge 内含多 stage）。"""
    log("构建 comworker-hermes-agent:latest dedicated bridge 镜像...")
    if with_browser:
        log("Hermes browser tools enabled; Playwright Chromium will be installed during build.")
    run(_hermes_build_command(use_cache=False, with_browser=with_browser))
    success("comworker-hermes-agent:latest 构建完成")


def build_hermes_image_fast(*, with_browser: bool = False):
    """使用缓存单次构建 Hermes dedicated bridge 镜像（Dockerfile.bridge 内含多 stage）。"""
    log("构建 comworker-hermes-agent:latest dedicated bridge 镜像（使用缓存）...")
    if with_browser:
        log("Hermes browser tools enabled; Playwright Chromium will be installed during build.")
    run(_hermes_build_command(use_cache=True, with_browser=with_browser))
    success("comworker-hermes-agent:latest 构建完成")


def _build_task(name: str, cmd: str):
    """在子线程中执行构建命令，返回 (name, returncode, elapsed)。"""
    log(f"[并行] 开始构建: {name}")
    start = time.time()
    result = subprocess.run(cmd, shell=True, cwd=PROJECT_DIR)
    elapsed = time.time() - start
    if result.returncode == 0:
        success(f"[并行] {name} 构建完成 ({elapsed:.0f}s)")
    else:
        error(f"[并行] {name} 构建失败 (exit {result.returncode}, {elapsed:.0f}s)")
    return name, result.returncode, elapsed


def build_and_start(compose_file: str, host: str, gateway_port: int, frontend_port: int):
    """构建并启动所有 compose 服务。"""
    api_url = f"http://{host}:{gateway_port}"
    log(f"Frontend VITE_API_URL = {api_url}")
    os.environ["VITE_API_URL"] = api_url
    os.environ.setdefault("APT_DEBIAN_MIRROR", DEFAULT_APT_DEBIAN_MIRROR)
    os.environ.setdefault("APT_SECURITY_MIRROR", DEFAULT_APT_SECURITY_MIRROR)

    compose_args = f"-f {compose_file}"

    log(f"使用 {compose_file} 并行构建所有镜像...")
    run(f"docker compose {compose_args} build --parallel")
    run(f"docker compose {compose_args} up -d")
    success("所有服务已启动")


def rebuild_service(compose_file: str, service: str, host: str | None = None, gateway_port: int | None = None, use_cache: bool = False):
    """重建并重启指定服务。"""
    if host and gateway_port:
        api_url = f"http://{host}:{gateway_port}"
        os.environ["VITE_API_URL"] = api_url
        log(f"VITE_API_URL = {api_url}")
    compose_args = f"-f {compose_file}"
    log(f"重建服务: {service}...")
    cache_flag = "" if use_cache else "--no-cache"
    run(f"docker compose {compose_args} build {cache_flag} {service}")
    run(f"docker compose {compose_args} up -d {service}")
    success(f"服务 {service} 已重建并启动")


def restart_services(compose_file: str):
    """重启所有服务。"""
    compose_args = f"-f {compose_file}"
    log("重启所有服务...")
    run(f"docker compose {compose_args} restart")
    success("所有服务已重启")


def clean_all(compose_file: str):
    """停止所有服务并清理数据。"""
    compose_args = f"-f {compose_file}"
    warn("即将停止所有服务并删除数据卷...")

    response = input("确认要清理所有数据？(y/N): ").strip().lower()
    if response != "y":
        log("取消操作")
        return

    log("停止 compose 服务并删除卷...")
    run(f"docker compose {compose_args} down -v", check=False)

    log("清理用户容器...")
    result = subprocess.run(
        'docker ps -a --filter "name=hermes-user-" -q',
        shell=True, capture_output=True, text=True, cwd=PROJECT_DIR,
    )
    container_ids = result.stdout.strip()
    if container_ids:
        run(f"docker rm -f {container_ids}", check=False)
        success("用户容器已清理")
    else:
        log("无用户容器需要清理")

    success("清理完成")


def health_check(host: str, gateway_port: int, frontend_port: int, retries: int = 30):
    """等待服务就绪并检查健康状态。"""
    import json
    import urllib.request

    log("等待服务就绪...")

    # 等待 gateway
    gateway_url = f"http://{host}:{gateway_port}/api/ping"
    for i in range(1, retries + 1):
        try:
            req = urllib.request.Request(gateway_url)
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read())
                if data.get("message") == "pong":
                    success(f"Gateway 就绪: {gateway_url}")
                    break
        except Exception:
            pass
        if i < retries:
            sys.stdout.write(f"\r  等待 Gateway... ({i}/{retries})")
            sys.stdout.flush()
            time.sleep(2)
    else:
        print()
        error(f"Gateway 未就绪: {gateway_url}")
        return False

    # 等待 frontend
    frontend_url = f"http://{host}:{frontend_port}"
    for i in range(1, retries + 1):
        try:
            req = urllib.request.Request(frontend_url)
            with urllib.request.urlopen(req, timeout=3) as resp:
                if resp.status < 400:
                    success(f"Frontend 就绪: {frontend_url}")
                    break
        except Exception:
            pass
        if i < retries:
            sys.stdout.write(f"\r  等待 Frontend... ({i}/{retries})")
            sys.stdout.flush()
            time.sleep(2)
    else:
        print()
        error(f"Frontend 未就绪: {frontend_url}")
        return False

    return True


def show_status(compose_file: str, host: str, gateway_port: int, client_port: int = 5174):
    """显示部署状态摘要。"""
    compose_args = f"-f {compose_file}"
    print(f"\n{BOLD}{'=' * 50}{RESET}")
    print(f"{BOLD}  ComWorker 部署状态{RESET}")
    print(f"{'=' * 50}")
    print(f"  客户端前端:      http://{host}:{client_port}")
    print(f"  管理员前端:      http://{host}:3081")
    print(f"  platform网关:    http://{host}:{gateway_port}")
    print(f"  使用的compose文件: {compose_file}")
    print(f"{'=' * 50}\n")

    run(f"docker compose {compose_args} ps", check=False)
    print()


def main():
    parser = argparse.ArgumentParser(
        description="ComWorker Docker 部署脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--host", default="localhost", help="服务器 IP 或域名 (默认: localhost)")
    parser.add_argument("--compose", default="docker-compose.yml", help="compose 文件 (默认: docker-compose.yml)")
    parser.add_argument("--gateway-port", type=int, default=None, help="Gateway 端口 (默认: 从 compose 文件读取)")
    parser.add_argument("--frontend-port", type=int, default=5174, help="客户端前端(cca-front)端口 (默认: 5174)")
    parser.add_argument(
        "--relative-api",
        action="store_true",
        help="前端使用相对 API 路径（VITE_API_URL 为空，适合反向代理/SSL 场景）",
    )
    parser.add_argument("--build-only", action="store_true", help="仅构建镜像，不启动服务")
    parser.add_argument("--restart", action="store_true", help="仅重启服务")
    parser.add_argument("--rebuild", metavar="SERVICES", help="重建指定服务，逗号分隔 (hermes,gateway,manage-front,cca-front)")
    parser.add_argument("--clean", action="store_true", help="停止所有服务并清理数据")
    parser.add_argument("--skip-base", action="store_true", help="跳过构建 Hermes dedicated bridge 基础镜像")
    parser.add_argument("--skip-health", action="store_true", help="跳过健康检查")
    parser.add_argument("--status", action="store_true", help="仅显示当前状态")
    parser.add_argument("--fast", action="store_true", help="使用 Docker 缓存加快构建速度（不使用 --no-cache）")
    parser.add_argument(
        "--with-browser",
        action="store_true",
        help="构建 Hermes 镜像时预装 Playwright Chromium（默认跳过以加快构建）",
    )
    args = parser.parse_args()

    # 推断 gateway 端口
    if args.gateway_port is None:
        if "prod" in args.compose:
            args.gateway_port = 8100
        else:
            args.gateway_port = 8080

    os.chdir(PROJECT_DIR)

    print(f"\n{BOLD}🚀 ComWorker Docker 部署{RESET}\n")

    # 仅显示状态
    if args.status:
        show_status(args.compose, args.host, args.gateway_port, args.frontend_port)
        return

    check_prerequisites()

    # 清理
    if args.clean:
        clean_all(args.compose)
        return

    # 重启
    if args.restart:
        restart_services(args.compose)
        show_status(args.compose, args.host, args.gateway_port, args.frontend_port)
        return

    # 重建指定服务（逗号分隔）
    if args.rebuild:
        services = [s.strip() for s in args.rebuild.split(",") if s.strip()]

        # 同步 deploy_copy
        sync_deploy_copy_to_hermes()

        if "comworker" in services:
            warn("当前 hermes 分支默认不再重建 comworker 基础镜像，已忽略 comworker 关键字")
            services.remove("comworker")

        if "hermes" in services:
            if args.fast:
                build_hermes_image_fast(with_browser=args.with_browser)
            else:
                build_hermes_image(with_browser=args.with_browser)
            services.remove("hermes")

        # 设置 VITE_API_URL（frontend 构建需要）
        if args.host and args.gateway_port:
            api_url = resolve_vite_api_url(args.host, args.gateway_port, args.relative_api)
            os.environ["VITE_API_URL"] = api_url
            log("VITE_API_URL = <relative path>" if args.relative_api else f"VITE_API_URL = {api_url}")
            os.environ.setdefault("APT_DEBIAN_MIRROR", DEFAULT_APT_DEBIAN_MIRROR)
            os.environ.setdefault("APT_SECURITY_MIRROR", DEFAULT_APT_SECURITY_MIRROR)

        # 重建 compose 服务
        if services:
            compose_args = f"-f {args.compose}"
            services_str = " ".join(services)
            log(f"重建服务: {services_str}...")
            cache_flag = "" if args.fast else "--no-cache"
            run(f"docker compose {compose_args} build --parallel {cache_flag} {services_str}")
            run(f"docker compose {compose_args} up -d {services_str}")
            success(f"服务 {services_str} 已重建并启动")

        show_status(args.compose, args.host, args.gateway_port, args.frontend_port)
        return

    check_env_file()

    # 同步 deploy_copy 到 runtime 构建目录
    sync_deploy_copy_to_hermes()
    sync_deploy_copy_to_platform()

    # 设置 VITE_API_URL（frontend 构建需要）
    api_url = resolve_vite_api_url(args.host, args.gateway_port, args.relative_api)
    os.environ["VITE_API_URL"] = api_url
    os.environ.setdefault("APT_DEBIAN_MIRROR", DEFAULT_APT_DEBIAN_MIRROR)
    os.environ.setdefault("APT_SECURITY_MIRROR", DEFAULT_APT_SECURITY_MIRROR)
    log("VITE_API_URL = <relative path>" if args.relative_api else f"VITE_API_URL = {api_url}")

    compose_args = f"-f {args.compose}"

    if not args.skip_base:
        # 并行构建: Hermes dedicated bridge 基础镜像 + compose 服务
        log("并行构建 Hermes dedicated bridge 基础镜像 + compose 服务...")
        tasks = {
            "comworker-hermes-agent:latest": _hermes_build_command(
                use_cache=False,
                with_browser=args.with_browser,
            ),
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
        success("所有镜像并行构建完成")
    else:
        # 仅构建 compose 服务
        log(f"使用 {args.compose} 构建 compose 服务...")
        run(f"docker compose {compose_args} build --parallel")

    if args.build_only:
        log("仅构建模式，跳过启动")
        return

    # 启动服务
    run(f"docker compose {compose_args} up -d")
    success("所有服务已启动")

    # 健康检查
    if not args.skip_health:
        check_host = "localhost" if args.host in ("0.0.0.0",) else args.host
        health_check(check_host, args.gateway_port, args.frontend_port)

    show_status(args.compose, args.host, args.gateway_port, args.frontend_port)


if __name__ == "__main__":
    main()
