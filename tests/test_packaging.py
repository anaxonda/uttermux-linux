from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]


class PackagingTests(unittest.TestCase):
    def test_all_builds_use_private_runtime(self):
        arch = (ROOT / "packaging/arch/PKGBUILD.in").read_text()
        debian = (ROOT / "packaging/debian/build-deb").read_text()
        source = (ROOT / "scripts/install-source").read_text()
        self.assertIn("/usr/lib/uttermux/runtime", arch)
        self.assertIn("/usr/lib/uttermux/runtime", debian)
        self.assertIn('$prefix/lib/uttermux/runtime', source)
        self.assertNotIn("onnxruntime-cpu", arch)
        self.assertIn("SHERPA_ONNX_USE_PRE_INSTALLED_ONNXRUNTIME_IF_AVAILABLE=OFF", arch)

    def test_zotero_unit_can_create_runtime_token(self):
        unit = (ROOT / "systemd/uttermux-zotero.service").read_text()
        self.assertIn("ProtectSystem=strict", unit)
        self.assertIn("ReadWritePaths=%t", unit)

    def test_installers_restart_enabled_zotero_bridge(self):
        for path in (ROOT / "install.sh", ROOT / "scripts/install-debian",
                     ROOT / "scripts/install-source"):
            text = path.read_text()
            self.assertIn("is-enabled --quiet uttermux-zotero.service", text)
            self.assertIn("restart uttermux-zotero.service", text)


if __name__ == "__main__":
    unittest.main()
