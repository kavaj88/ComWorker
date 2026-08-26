#!/usr/bin/env python3
"""构建 ComWorker 无源码离线交付包。"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = ROOT / "delivery" / "offline"
VERSION_MARKER = "__COMWORKER_VERSION__"
FORBIDDEN_ROOTS = {
    ".git",
    "secrets",
    "platform",
    "cca-front",
    "manage_front",
    "hermes-agent",
    "tests",
}


class PackageError(RuntimeError):
    """离线包构建错误。"""


def log(message: str) -> None:
    print(f"==> {message}", flush=True)


def run(
    command: list[str],
    *,
    cwd: Path = ROOT,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    log("执行: " + " ".join(command))
    result = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=capture_output,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise PackageError(
            f"命令失败 ({result.returncode}): {command[0]}"
            + (f"\n{detail}" if detail else "")
        )
    return result


def normalize_platform(value: str) -> tuple[str, str]:
    parts = value.strip().lower().split("/")
    if len(parts) != 2 or parts[0] != "linux" or parts[1] not in {"amd64", "x86_64"}:
        raise PackageError("首版离线包仅支持 linux/amd64")
    return "linux", "amd64"


def validate_version(value: str) -> str:
    if value != value.strip() or not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._-]*", value):
        raise PackageError("版本号只能包含字母、数字、点、下划线和连字符")
    return value


def image_references(version: str) -> list[str]:
    version = validate_version(version)
    return [
        f"comworker-gateway:{version}",
        f"comworker-manage:{version}",
        f"comworker-cca-front:{version}",
        f"comworker-hermes-agent:{version}",
        "comworker-postgres:16-alpine",
    ]


def render_template(content: str, version: str) -> str:
    rendered = content.replace(VERSION_MARKER, validate_version(version))
    unresolved = re.findall(r"__[A-Z][A-Z0-9_]*__", rendered)
    if unresolved:
        raise PackageError(f"模板仍有未解析变量: {', '.join(sorted(set(unresolved)))}")
    return rendered


def find_forbidden_entries(root: Path) -> list[str]:
    forbidden: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        parts = relative.parts
        name = path.name
        is_forbidden = (
            relative.as_posix() == ".env"
            or any(part in FORBIDDEN_ROOTS for part in parts)
            or name == "Dockerfile"
            or name.startswith("Dockerfile.")
            or name.endswith((".pyc", ".pyo"))
        )
        if is_forbidden:
            forbidden.append(relative.as_posix())
    return forbidden


def zip_compression_for(path: Path) -> int:
    return zipfile.ZIP_STORED if path.name.endswith(".tar.zst") else zipfile.ZIP_DEFLATED


def build_commands(version: str, platform: str) -> list[list[str]]:
    validate_version(version)
    os_name, arch = normalize_platform(platform)
    target = f"{os_name}/{arch}"
    return [
        [
            "docker",
            "build",
            "--platform",
            target,
            "-f",
            "hermes-agent/Dockerfile",
            "-t",
            f"hermes-base:{version}",
            "hermes-agent",
        ],
        [
            "docker",
            "build",
            "--platform",
            target,
            "--build-arg",
            f"BASE_TAG={version}",
            "-f",
            "hermes-agent/Dockerfile.bridge",
            "-t",
            f"comworker-hermes-agent:{version}",
            "hermes-agent",
        ],
        [
            "docker",
            "build",
            "--platform",
            target,
            "-f",
            "platform/Dockerfile",
            "-t",
            f"comworker-gateway:{version}",
            "platform",
        ],
        [
            "docker",
            "build",
            "--platform",
            target,
            "-f",
            "manage_front/Dockerfile",
            "-t",
            f"comworker-manage:{version}",
            "manage_front",
        ],
        [
            "docker",
            "build",
            "--platform",
            target,
            "--build-arg",
            "VITE_API_URL=",
            "-f",
            "cca-front/Dockerfile",
            "-t",
            f"comworker-cca-front:{version}",
            "cca-front",
        ],
    ]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package_root_name(version: str, arch: str = "amd64") -> str:
    validate_version(version)
    if arch != "amd64":
        raise PackageError("首版离线包仅支持 amd64")
    return f"ComWorker-{version}-offline-{arch}"


def sync_build_metadata() -> None:
    source = ROOT / "deploy_copy"
    platform_target = ROOT / "platform" / "deploy_copy"
    platform_target.mkdir(parents=True, exist_ok=True)
    for child in platform_target.iterdir():
        if child.name == ".gitkeep":
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    for name in ("comworker_defaults.json", "Agents"):
        item = source / name
        if not item.exists():
            continue
        destination = platform_target / name
        if item.is_dir():
            shutil.copytree(item, destination)
        else:
            shutil.copy2(item, destination)

    hermes_target = ROOT / "hermes-agent" / "deploy_copy"
    if hermes_target.exists():
        shutil.rmtree(hermes_target)
    shutil.copytree(source, hermes_target)


def ensure_docker() -> None:
    run(["docker", "info"], capture_output=True)
    run(["docker", "compose", "version"], capture_output=True)


def build_images(version: str, platform: str) -> None:
    sync_build_metadata()
    for command in build_commands(version, platform):
        run(command)
    run(["docker", "pull", "postgres:16-alpine"])
    run(["docker", "tag", "postgres:16-alpine", "comworker-postgres:16-alpine"])


def inspect_images(version: str, platform: str) -> list[dict[str, object]]:
    expected_os, expected_arch = normalize_platform(platform)
    metadata: list[dict[str, object]] = []
    for reference in image_references(version):
        result = run(
            ["docker", "image", "inspect", reference, "--format", "{{json .}}"],
            capture_output=True,
        )
        data = json.loads(result.stdout)
        actual_arch = "amd64" if data.get("Architecture") == "x86_64" else data.get("Architecture")
        if data.get("Os") != expected_os or actual_arch != expected_arch:
            raise PackageError(
                f"镜像平台不匹配: {reference}={data.get('Os')}/{data.get('Architecture')}"
            )
        metadata.append(
            {
                "ref": reference,
                "id": data["Id"],
                "os": data["Os"],
                "architecture": actual_arch,
                "size": data.get("Size", 0),
            }
        )
    return metadata


def compress_zstd(raw_archive: Path, compressed_archive: Path) -> None:
    zstd = shutil.which("zstd")
    if zstd:
        run([zstd, "-T0", "-3", "-f", str(raw_archive), "-o", str(compressed_archive)])
        return

    tar = shutil.which("tar")
    if tar:
        version = run([tar, "--version"], capture_output=True).stdout.lower()
        if "bsdtar" in version and "zstd" in version:
            with tempfile.TemporaryDirectory(dir=raw_archive.parent) as temp_dir:
                extracted = Path(temp_dir) / "docker-archive"
                extracted.mkdir()
                run([tar, "-xf", str(raw_archive), "-C", str(extracted)])
                run([tar, "-a", "-cf", str(compressed_archive), "-C", str(extracted), "."])
            return

    mount = f"{raw_archive.parent.resolve()}:/work"
    run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            mount,
            "--entrypoint",
            "sh",
            "comworker-postgres:16-alpine",
            "-c",
            (
                "apk add --no-cache zstd >/dev/null && "
                f"zstd -T0 -3 -f /work/{raw_archive.name} -o /work/{compressed_archive.name}"
            ),
        ]
    )


def export_images(version: str, work_dir: Path) -> Path:
    raw_archive = work_dir / "comworker-images.tar"
    compressed_archive = work_dir / "comworker-images.tar.zst"
    run(["docker", "save", "-o", str(raw_archive), *image_references(version)])
    compress_zstd(raw_archive, compressed_archive)
    raw_archive.unlink()
    return compressed_archive


def copy_rendered_templates(destination: Path, version: str) -> None:
    for source in sorted(TEMPLATE_DIR.rglob("*")):
        if not source.is_file():
            continue
        relative = source.relative_to(TEMPLATE_DIR)
        if relative.as_posix() == "env.example":
            relative = Path(".env.example")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        content = source.read_text(encoding="utf-8")
        target.write_text(render_template(content, version), encoding="utf-8", newline="\n")
        if target.suffix == ".sh":
            target.chmod(0o755)


def write_checksums(root: Path) -> Path:
    checksum_path = root / "checksums.sha256"
    lines: list[str] = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path != checksum_path:
            lines.append(f"{sha256_file(path)}  {path.relative_to(root).as_posix()}")
    checksum_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return checksum_path


def create_delivery_tree(
    *,
    work_dir: Path,
    version: str,
    platform: str,
    archive: Path,
    images: list[dict[str, object]],
) -> Path:
    _, arch = normalize_platform(platform)
    root = work_dir / package_root_name(version, arch)
    root.mkdir(parents=True)
    copy_rendered_templates(root, version)
    shutil.copy2(ROOT / "LICENSE", root / "LICENSE")
    images_dir = root / "images"
    images_dir.mkdir()
    packaged_archive = images_dir / archive.name
    shutil.copy2(archive, packaged_archive)
    manifest = {
        "product": "ComWorker",
        "version": version,
        "platform": f"linux/{arch}",
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "archive": {
            "path": packaged_archive.relative_to(root).as_posix(),
            "sha256": sha256_file(packaged_archive),
            "size": packaged_archive.stat().st_size,
        },
        "images": images,
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    forbidden = find_forbidden_entries(root)
    if forbidden:
        raise PackageError("交付目录包含禁止内容: " + ", ".join(forbidden))
    write_checksums(root)
    return root


def create_zip(delivery_root: Path, output_path: Path) -> None:
    if output_path.exists():
        output_path.unlink()
    with zipfile.ZipFile(output_path, "w", allowZip64=True) as archive:
        for path in sorted(delivery_root.rglob("*")):
            if not path.is_file():
                continue
            arcname = (Path(delivery_root.name) / path.relative_to(delivery_root)).as_posix()
            archive.write(
                path,
                arcname,
                compress_type=zip_compression_for(path),
                compresslevel=None if zip_compression_for(path) == zipfile.ZIP_STORED else 6,
            )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--platform", default="linux/amd64")
    parser.add_argument("--output-dir", default="dist")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--keep-work-dir", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    version = validate_version(args.version)
    os_name, arch = normalize_platform(args.platform)
    platform = f"{os_name}/{arch}"
    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = ROOT / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir = output_dir / f".offline-build-{version}"
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir()

    try:
        ensure_docker()
        if not args.skip_build:
            build_images(version, platform)
        images = inspect_images(version, platform)
        archive = export_images(version, work_dir)
        delivery_root = create_delivery_tree(
            work_dir=work_dir,
            version=version,
            platform=platform,
            archive=archive,
            images=images,
        )
        output_path = output_dir / f"{package_root_name(version, arch)}.zip"
        create_zip(delivery_root, output_path)
        log(f"离线包: {output_path}")
        log(f"大小: {output_path.stat().st_size / (1024 ** 3):.2f} GiB")
        log(f"SHA-256: {sha256_file(output_path)}")
    finally:
        if work_dir.exists() and not args.keep_work_dir:
            shutil.rmtree(work_dir)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PackageError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        raise SystemExit(1)
