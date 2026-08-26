import importlib.util
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = ROOT / "delivery" / "offline"
MODULE_PATH = ROOT / "tools" / "build_offline_package.py"
MODULE_SPEC = importlib.util.spec_from_file_location("build_offline_package", MODULE_PATH)
if MODULE_SPEC is None or MODULE_SPEC.loader is None:
    raise RuntimeError(f"无法加载构建器: {MODULE_PATH}")
package = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = package
MODULE_SPEC.loader.exec_module(package)


class OfflineTemplatePolicyTests(unittest.TestCase):
    def read_template(self, name: str) -> str:
        return (TEMPLATE_DIR / name).read_text(encoding="utf-8")

    def test_compose_uses_only_packaged_images(self):
        compose = self.read_template("compose.yml")

        self.assertNotIn("build:", compose)
        self.assertNotIn("../", compose)
        for source_root in ("platform/", "cca-front/", "manage_front/", "hermes-agent/"):
            self.assertNotIn(source_root, compose)
        self.assertGreaterEqual(compose.count("pull_policy: never"), 5)
        for service in ("skills-init:", "postgres:", "gateway:", "manage:", "client:"):
            self.assertIn(service, compose)

    def test_compose_keeps_internal_services_private_and_healthy(self):
        compose = self.read_template("compose.yml")

        self.assertIn("BUILTIN_SKILLS_VOLUME: comworker-builtin-skills", compose)
        self.assertIn("name: comworker-builtin-skills", compose)
        self.assertNotIn('"15432:5432"', compose)
        self.assertNotIn('"8080:8080"', compose)
        self.assertGreaterEqual(compose.count("healthcheck:"), 4)

    def test_installers_load_images_preserve_secrets_and_start_compose(self):
        compose = self.read_template("compose.yml")
        shell = self.read_template("install.sh")
        powershell = self.read_template("install.ps1")

        self.assertIn("PLATFORM_ADMIN_PASSWORD: ${ADMIN_PASSWORD:?", compose)
        self.assertNotIn("PLATFORM_ADMIN_PASSWORD_FILE", compose)
        self.assertIn("docker load", shell)
        self.assertIn("[ ! -f", shell)
        self.assertIn('export ADMIN_PASSWORD="$(cat secrets/admin_password)"', shell)
        self.assertIn("docker compose", shell)
        self.assertIn("docker load", powershell)
        self.assertIn("Test-Path", powershell)
        self.assertIn("$env:ADMIN_PASSWORD = Get-Content -Raw secrets/admin_password", powershell)
        self.assertIn("$Compose = @('compose'", powershell)
        self.assertIn("Invoke-Docker @Compose up -d", powershell)


class OfflinePackageHelperTests(unittest.TestCase):
    def test_normalize_platform_accepts_linux_amd64(self):
        self.assertEqual(package.normalize_platform("linux/amd64"), ("linux", "amd64"))

    def test_normalize_platform_rejects_unsupported_targets(self):
        for value in ("windows/amd64", "linux/arm64", "linux", ""):
            with self.subTest(value=value), self.assertRaises(package.PackageError):
                package.normalize_platform(value)

    def test_validate_version_rejects_unsafe_values(self):
        for value in ("", " 1.0.0", "1.0.0 ", "release/1", "a b"):
            with self.subTest(value=value), self.assertRaises(package.PackageError):
                package.validate_version(value)
        self.assertEqual(package.validate_version("1.2.3"), "1.2.3")

    def test_image_references_are_complete_and_versioned(self):
        self.assertEqual(
            package.image_references("1.2.3"),
            [
                "comworker-gateway:1.2.3",
                "comworker-manage:1.2.3",
                "comworker-cca-front:1.2.3",
                "comworker-hermes-agent:1.2.3",
                "comworker-postgres:16-alpine",
            ],
        )

    def test_render_template_resolves_version_marker(self):
        rendered = package.render_template("image: app:__COMWORKER_VERSION__", "2.0.0")
        self.assertEqual(rendered, "image: app:2.0.0")
        with self.assertRaises(package.PackageError):
            package.render_template("__UNRESOLVED__", "2.0.0")

    def test_forbidden_scan_rejects_sources_and_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            allowed = root / ".env.example"
            allowed.write_text("COMWORKER_VERSION=1.0.0", encoding="utf-8")
            (root / "install.sh").write_text("docker compose up -d", encoding="utf-8")
            (root / ".env").write_text("SECRET=value", encoding="utf-8")
            (root / "Dockerfile").write_text("FROM scratch", encoding="utf-8")
            (root / "platform").mkdir()
            (root / "platform" / "app.py").write_text("print('source')", encoding="utf-8")
            (root / "secrets").mkdir()
            (root / "secrets" / "jwt_secret").write_text("secret", encoding="utf-8")

            forbidden = package.find_forbidden_entries(root)

            self.assertNotIn(".env.example", forbidden)
            self.assertIn(".env", forbidden)
            self.assertIn("Dockerfile", forbidden)
            self.assertIn("platform/app.py", forbidden)
            self.assertIn("secrets/jwt_secret", forbidden)

    def test_zip_compression_avoids_recompressing_zstd(self):
        self.assertEqual(package.zip_compression_for(Path("images.tar.zst")), zipfile.ZIP_STORED)
        self.assertEqual(package.zip_compression_for(Path("安装说明.md")), zipfile.ZIP_DEFLATED)

    def test_build_commands_are_platform_pinned_and_secret_free(self):
        commands = package.build_commands("1.2.3", "linux/amd64")
        flattened = [" ".join(command) for command in commands]

        self.assertIn("hermes-agent/Dockerfile", flattened[0])
        self.assertIn("hermes-base:1.2.3", flattened[0])
        self.assertIn("hermes-agent/Dockerfile.bridge", flattened[1])
        self.assertIn("BASE_TAG=1.2.3", flattened[1])
        self.assertTrue(all("--platform linux/amd64" in command for command in flattened))
        self.assertTrue(all(".env" not in command for command in flattened))
        self.assertTrue(any("comworker-cca-front:1.2.3" in command for command in flattened))

    def test_sync_build_metadata_populates_both_image_contexts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "deploy_copy"
            source.mkdir()
            (source / "comworker_defaults.json").write_text("{}", encoding="utf-8")
            (source / "Agents").mkdir()
            (source / "Agents" / "main.txt").write_text("main", encoding="utf-8")
            (root / "platform" / "deploy_copy").mkdir(parents=True)
            (root / "platform" / "deploy_copy" / ".gitkeep").touch()
            (root / "hermes-agent").mkdir()

            with mock.patch.object(package, "ROOT", root):
                package.sync_build_metadata()

            self.assertTrue((root / "platform" / "deploy_copy" / "Agents" / "main.txt").is_file())
            self.assertTrue((root / "hermes-agent" / "deploy_copy" / "Agents" / "main.txt").is_file())

    def test_delivery_tree_contains_manifest_and_checksums_without_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory) / "work"
            work.mkdir()
            archive = Path(directory) / "comworker-images.tar.zst"
            archive.write_bytes(b"fake-zstd-archive")
            images = [{"ref": "comworker-gateway:1.0.0", "id": "sha256:abc"}]

            root = package.create_delivery_tree(
                work_dir=work,
                version="1.0.0",
                platform="linux/amd64",
                archive=archive,
                images=images,
            )

            manifest = package.json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["version"], "1.0.0")
            self.assertEqual(manifest["images"], images)
            self.assertTrue((root / "checksums.sha256").is_file())
            self.assertTrue((root / ".env.example").is_file())
            self.assertFalse((root / ".env").exists())
            self.assertEqual(package.find_forbidden_entries(root), [])


if __name__ == "__main__":
    unittest.main()
