from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = ROOT / "delivery" / "offline"


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
        shell = self.read_template("install.sh")
        powershell = self.read_template("install.ps1")

        self.assertIn("docker load", shell)
        self.assertIn("[ ! -f", shell)
        self.assertIn("docker compose", shell)
        self.assertIn("docker load", powershell)
        self.assertIn("Test-Path", powershell)
        self.assertIn("$Compose = @('compose'", powershell)
        self.assertIn("Invoke-Docker @Compose up -d", powershell)


if __name__ == "__main__":
    unittest.main()
