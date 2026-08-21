import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("catalog_builder", ROOT / "scripts/catalog/build_catalog.py")
builder = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(builder)


class CatalogGeneratorTest(unittest.TestCase):
    def test_curated_catalog_has_stable_variants_and_qwen_platform(self):
        document = builder.build(ROOT / "catalog/catalog.toml", None)
        variants = {item["id"]: item for item in document["variants"]}
        self.assertEqual(2, document["schemaVersion"])
        self.assertEqual(["linux"], variants["qwen3-tts-0.6b-customvoice"]["platforms"])
        self.assertEqual("qwen-safetensors", variants["qwen3-tts-0.6b-customvoice"]["runtimeId"])
        self.assertIn("local/kokoro-multi-lang-v1_0/af-bella", {v["id"] for v in document["voices"]})

    def test_piper_snapshot_expands_speakers_and_hides_unverified_bundle(self):
        source = [{"key":"xx_XX-test-low","name":"test","language":"xx_XX","language_name":"Test","country":"Test","quality":"low","speakers":2,"speaker_ids":{},"model_file":"test.onnx","sample_url":"https://example.test/speaker_0.mp3","download_url":"","download_size":10,"sha256":""}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "piper.json"
            path.write_text(json.dumps(source))
            document = builder.build(ROOT / "catalog/catalog.toml", path)
        variant = next(v for v in document["variants"] if v["id"] == "vits-piper-xx_XX-test-low")
        voices = [v for v in document["voices"] if v["variantId"] == variant["id"]]
        self.assertEqual("unavailable", variant["status"])
        self.assertEqual(2, len(voices))
        self.assertEqual("https://example.test/speaker_1.mp3", voices[1]["previewUrl"])

    def test_generated_files_are_checkable(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "catalog.json"
            docs = Path(directory) / "MODELS.md"
            document = builder.build(ROOT / "catalog/catalog.toml", None)
            output.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            docs.write_text(builder.render_markdown(document))
            self.assertEqual(document, json.loads(output.read_text()))


if __name__ == "__main__":
    unittest.main()
