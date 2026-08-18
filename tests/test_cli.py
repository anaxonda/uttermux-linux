import importlib.machinery
import importlib.util
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("sherpa_voice", str(ROOT / "cli/sherpa-voice"))
spec = importlib.util.spec_from_loader(loader.name, loader)
sv = importlib.util.module_from_spec(spec)
loader.exec_module(sv)
ut_loader = importlib.machinery.SourceFileLoader("uttermux_cli", str(ROOT / "cli/uttermux"))
ut_spec = importlib.util.spec_from_loader(ut_loader.name, ut_loader)
ut = importlib.util.module_from_spec(ut_spec)
ut_loader.exec_module(ut)


class CliTests(unittest.TestCase):
    def test_schema_two_preserves_language_routes(self):
        rendered = ut.render_config({
            "default_voice": "edge/libby", "fallback_voice": "local/lessac",
            "routing": {"voices": {"fr": ["elevenlabs/bill", "edge/denise"]}},
        })
        self.assertIn("schema_version = 2", rendered)
        self.assertIn('[routing.voices]', rendered)
        self.assertIn('"fr" = ["elevenlabs/bill", "edge/denise"]', rendered)

    def test_short_text_is_not_auto_detected(self):
        language, confidence, reason = ut.detect_text("Bonjour.")
        self.assertEqual((language, confidence, reason), ("", 0, "insufficient-text"))

    def test_language_matching_accepts_null_provider_metadata(self):
        self.assertFalse(ut.language_matches(None, "fr-FR"))

    def test_catalog_is_valid(self):
        old = os.environ.get("SHERPA_VOICE_CATALOG")
        os.environ["SHERPA_VOICE_CATALOG"] = str(ROOT / "catalog/catalog.toml")
        try:
            catalog = sv.load_catalog()
            self.assertIn("kokoro-multi-lang-v1_0", catalog)
            self.assertEqual(catalog["kokoro-multi-lang-v1_0"]["voices"][0]["language"], "en-US")
            for item in catalog.values():
                self.assertRegex(item["sha256"], r"^[0-9a-f]{64}$")
                self.assertGreater(item["size"], 0)
        finally:
            if old is None: os.environ.pop("SHERPA_VOICE_CATALOG", None)
            else: os.environ["SHERPA_VOICE_CATALOG"] = old

    def test_safe_extract_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            archive = base / "bad.tar"
            payload = base / "payload"
            payload.write_text("bad")
            with tarfile.open(archive, "w") as tf:
                tf.add(payload, arcname="../escape")
            with self.assertRaises(RuntimeError):
                sv.safe_extract(archive, base / "out")

    def test_model_id_rejects_path_traversal(self):
        with self.assertRaises(RuntimeError):
            sv.model_root("../../outside")

    def test_manifest_has_hyphenated_language(self):
        item = sv.load_catalog()["kokoro-multi-lang-v1_0"]
        manifest = sv.render_manifest(item, Path("/tmp/model"))
        self.assertIn('language = "en-US"', manifest)
        self.assertNotIn("en_US", manifest)

    def test_enable_module_is_idempotent_for_existing_config(self):
        with tempfile.TemporaryDirectory() as d:
            old = os.environ.get("XDG_CONFIG_HOME")
            os.environ["XDG_CONFIG_HOME"] = d
            try:
                target = Path(d) / "speech-dispatcher/speechd.conf"
                target.parent.mkdir(parents=True)
                target.write_text('# user setting\nLogLevel 3\n')
                sv.enable_module()
                sv.enable_module()
                content = target.read_text()
                self.assertEqual(content.count("# BEGIN speech-dispatcher-sherpa"), 1)
                self.assertIn("LogLevel 3", content)
            finally:
                if old is None: os.environ.pop("XDG_CONFIG_HOME", None)
                else: os.environ["XDG_CONFIG_HOME"] = old

    def test_install_and_interrupted_manifest_recovery(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            env = {"XDG_CONFIG_HOME": str(base / "config"),
                   "XDG_DATA_HOME": str(base / "data"),
                   "XDG_CACHE_HOME": str(base / "cache")}
            archive = base / "cache" / sv.APP / "downloads" / "test.tar.bz2"
            archive.parent.mkdir(parents=True)
            source = base / "source" / "test-model"
            source.mkdir(parents=True)
            for name in ("model.onnx", "voices.bin", "tokens.txt"):
                (source / name).write_text(name)
            with tarfile.open(archive, "w:bz2") as tf:
                tf.add(source, arcname="test-model")
            item = {"id": "test-model", "engine": "kokoro",
                    "url": "https://invalid.example/test.tar.bz2",
                    "sha256": sv.sha256(archive),
                    "files": {"model": "model.onnx", "voices": "voices.bin", "tokens": "tokens.txt"},
                    "voices": [{"id": "voice", "name": "Test Voice", "language": "en-US", "speaker_id": 0}]}
            with mock.patch.dict(os.environ, env), mock.patch.object(sv, "load_catalog", return_value={"test-model": item}), mock.patch.object(sv.subprocess, "run"):
                sv.install_model("test-model", True)
                manifest = sv.manifest_dir() / "test-model.toml"
                self.assertTrue(manifest.is_file())
                manifest.unlink()
                sv.install_model("test-model", True)
                self.assertTrue(manifest.is_file())


if __name__ == "__main__":
    unittest.main()
