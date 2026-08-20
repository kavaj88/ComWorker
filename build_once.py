#!/usr/bin/env python3
"""One-shot Docker build and start for the local ComWorker stack.

The goal is a predictable cold build in China-facing Docker Desktop networks:
pre-pull all external base/runtime images through known mirror fallbacks, then
build local images in dependency order, start compose, and verify health.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.request import urlopen


PROJECT_DIR = Path(__file__).resolve().parent

DOCKERHUB_MIRRORS = [
    "docker.m.daocloud.io",
    "docker.xuanyuan.me",
    "docker.1ms.run",
]

DEFAULT_APT_DEBIAN_MIRROR = "http://mirrors.ustc.edu.cn/debian"
DEFAULT_APT_SECURITY_MIRROR = "http://mirrors.ustc.edu.cn/debian-security"


@dataclass(frozen=True)
class ImageSpec:
    image: str
    mirrorable: bool = True
    pull_timeout: int = 60


BASE_IMAGES = [
    ImageSpec("postgres:16-alpine"),
    ImageSpec("node:22-alpine"),
    ImageSpec("nginx:alpine"),
    ImageSpec("debian:13.4"),
    ImageSpec("python:3.13-slim-bookworm"),
]


def log(message: str) -> None:
    print(f"▸ {message}", flush=True)


def ok(message: str) -> None:
    print(f"✓ {message}", flush=True)


def warn(message: str) -> None:
    print(f"⚠ {message}", flush=True)


def run(
    args: list[str],
    *,
    cwd: Path = PROJECT_DIR,
    timeout: int | None = None,
    capture: bool = False,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    merged_env = os.environ.copy()
    merged_env.setdefault("PYTHONUTF8", "1")
    merged_env.setdefault("PYTHONIOENCODING", "utf-8")
    merged_env.setdefault("DOCKER_BUILDKIT", "1")
    if env:
        merged_env.update(env)
    return subprocess.run(
        args,
        cwd=str(cwd),
        check=False,
        timeout=timeout,
        text=True,
        capture_output=capture,
        env=merged_env,
    )


def run_checked(args: list[str], *, timeout: int | None = None, env: dict[str, str] | None = None) -> None:
    log(" ".join(args))
    proc = run(args, timeout=timeout, env=env)
    if proc.returncode != 0:
        raise SystemExit(proc.returncode)


def image_exists(image: str) -> bool:
    proc = run(["docker", "image", "inspect", image], capture=True, timeout=30)
    return proc.returncode == 0


def dockerhub_mirror_ref(image: str, mirror: str) -> str:
    name, sep, digest = image.partition("@")
    if "/" in name and not name.startswith("library/"):
        namespace = name
    else:
        namespace = f"library/{name}"
    return f"{mirror}/{namespace}{sep}{digest}"


def pull_image(spec: ImageSpec) -> None:
    if image_exists(spec.image):
        ok(f"{spec.image} already available")
        return

    candidates: list[str] = []
    if spec.mirrorable:
        candidates.extend(dockerhub_mirror_ref(spec.image, mirror) for mirror in DOCKERHUB_MIRRORS)
    candidates.append(spec.image)

    errors: list[str] = []
    for candidate in candidates:
        log(f"pull {candidate}")
        try:
            proc = run(["docker", "pull", candidate], timeout=spec.pull_timeout, capture=True)
        except subprocess.TimeoutExpired:
            errors.append(f"{candidate}: timed out after {spec.pull_timeout}s")
            continue
        if proc.returncode == 0:
            if candidate != spec.image:
                tag = run(["docker", "tag", candidate, spec.image], timeout=60, capture=True)
                if tag.returncode != 0:
                    errors.append(f"{candidate}: tag failed: {tag.stderr.strip()}")
                    continue
            ok(f"{spec.image} ready")
            return
        tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-8:])
        errors.append(f"{candidate}: {tail}")

    raise RuntimeError(f"Could not pull {spec.image}\n" + "\n".join(errors))


def pre_pull_images() -> None:
    log("pre-pulling external base images")
    for spec in BASE_IMAGES:
        pull_image(spec)


def log_wrapped(command: list[str], name: str, timeout: int) -> None:
    log_path = Path(tempfile.gettempdir()) / f"comworker-{name}.log"
    log(f"{' '.join(command)} (log: {log_path})")
    with log_path.open("w", encoding="utf-8") as handle:
        proc = subprocess.run(
            command,
            cwd=str(PROJECT_DIR),
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "DOCKER_BUILDKIT": "1"},
        )
    tail = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()[-120:]
    if tail:
        print("\n".join(tail))
    if proc.returncode != 0:
        raise SystemExit(proc.returncode)


def sync_deploy_copy() -> None:
    import deploy_docker

    deploy_docker.sync_deploy_copy_to_bridge()
    deploy_docker.sync_deploy_copy_to_hermes()


def build_images(host: str, gateway_port: int, relative_api: bool) -> None:
    api_url = "" if relative_api else f"http://{host}:{gateway_port}"
    env = {
        "VITE_API_URL": api_url,
        "APT_DEBIAN_MIRROR": os.environ.get("APT_DEBIAN_MIRROR", DEFAULT_APT_DEBIAN_MIRROR),
        "APT_SECURITY_MIRROR": os.environ.get("APT_SECURITY_MIRROR", DEFAULT_APT_SECURITY_MIRROR),
    }
    sync_deploy_copy()
    log_wrapped(
        [
            "docker",
            "build",
            "--progress=plain",
            "--build-arg",
            f"APT_DEBIAN_MIRROR={env['APT_DEBIAN_MIRROR']}",
            "--build-arg",
            f"APT_SECURITY_MIRROR={env['APT_SECURITY_MIRROR']}",
            "-t",
            "hermes-base:latest",
            "-f",
            "hermes-agent/Dockerfile",
            "hermes-agent/",
        ],
        "hermes-base",
        2700,
    )
    log_wrapped(
        ["docker", "build", "--progress=plain", "-t", "comworker-hermes-agent:latest", "-f", "hermes-agent/Dockerfile.bridge", "hermes-agent/"],
        "hermes-bridge",
        1200,
    )
    old_env = os.environ.copy()
    os.environ.update(env)
    try:
        log_wrapped(
            ["docker", "compose", "-f", "docker-compose.yml", "--progress", "plain", "build", "--parallel"],
            "compose-build",
            1800,
        )
    finally:
        os.environ.clear()
        os.environ.update(old_env)


def start_and_verify(host: str, gateway_port: int, frontend_port: int) -> None:
    run_checked(["docker", "compose", "-f", "docker-compose.yml", "up", "-d"], timeout=600)

    gateway_url = f"http://{host}:{gateway_port}/api/ping"
    frontend_url = f"http://{host}:{frontend_port}"
    deadline = time.time() + 120
    while time.time() < deadline:
        try:
            with urlopen(gateway_url, timeout=3) as resp:
                if resp.status == 200:
                    ok(f"gateway ready: {gateway_url}")
                    break
        except Exception:
            time.sleep(2)
    else:
        run_checked(["docker", "logs", "--tail", "160", "comworker-gateway"], timeout=30)
        raise RuntimeError(f"Gateway not ready: {gateway_url}")

    with urlopen(frontend_url, timeout=10) as resp:
        if resp.status >= 400:
            raise RuntimeError(f"Frontend unhealthy: HTTP {resp.status}")
    ok(f"frontend ready: {frontend_url}")
    run_checked(["docker", "compose", "-f", "docker-compose.yml", "ps"], timeout=30)


def clean(remove_volumes: bool) -> None:
    args = ["docker", "compose", "-f", "docker-compose.yml", "down", "--remove-orphans"]
    if remove_volumes:
        args.append("-v")
    run_checked(args, timeout=120)
    for image in [
        "hermes-base:latest",
        "comworker-hermes-agent:latest",
        "comworker-gateway:latest",
        "comworker-manage-front:latest",
        "comworker-share-comworker-front:latest",
    ]:
        run(["docker", "image", "rm", "-f", image], timeout=60, capture=True)
    run(["docker", "builder", "prune", "-f"], timeout=120)


def main() -> None:
    parser = argparse.ArgumentParser(description="Predictable one-shot local Docker build/start")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--gateway-port", type=int, default=8080)
    parser.add_argument("--frontend-port", type=int, default=3080)
    parser.add_argument("--relative-api", action="store_true")
    parser.add_argument("--clean", action="store_true", help="Remove project containers/images/build cache before building")
    parser.add_argument("--clean-volumes", action="store_true", help="Also remove compose volumes")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--skip-start", action="store_true")
    args = parser.parse_args()

    if shutil.which("docker") is None:
        raise SystemExit("docker not found")
    info = run(["docker", "info"], timeout=30, capture=True)
    if info.returncode != 0:
        raise SystemExit((info.stderr or info.stdout or "Docker daemon is not running").strip())
    ok("Docker daemon is running")

    started = time.monotonic()
    if args.clean:
        clean(args.clean_volumes)
    pre_pull_images()
    if not args.skip_build:
        build_images(args.host, args.gateway_port, args.relative_api)
    if not args.skip_start:
        start_and_verify(args.host, args.gateway_port, args.frontend_port)
    ok(f"completed in {time.monotonic() - started:.0f}s")


if __name__ == "__main__":
    main()
