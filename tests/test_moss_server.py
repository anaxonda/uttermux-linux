import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("moss_server", ROOT / "providers" / "moss_server.py")
MOSS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOSS)


class FakeBase:
    def __init__(self):
        self.tts_meta_path = Path("/models/tts/meta.json")
        self.codec_meta_path = Path("/models/codec/meta.json")
        self.tts_meta = {"files": {
            "prefill": "prefill.onnx",
            "decode_step": "decode.onnx",
            "local_fixed_sampled_frame": "fixed.onnx",
            "local_decoder": "unused-local.onnx",
        }}
        self.codec_meta = {"files": {
            "decode_step": "codec-step.onnx",
            "decode_full": "unused-full.onnx",
            "encode": "unused-encode.onnx",
        }}

    def _session(self, path):
        return str(path)


class MossRuntimeTests(unittest.TestCase):
    def test_streaming_runtime_loads_only_required_graphs(self):
        runtime = MOSS.streaming_runtime_class(FakeBase)()
        sessions = runtime._create_sessions()
        self.assertEqual(set(sessions), {
            "prefill", "decode", "local_fixed_sampled_frame", "codec_decode_step"})
        self.assertFalse(any("unused" in path for path in sessions.values()))

    def test_streaming_runtime_requires_fixed_sampler(self):
        class MissingFixed(FakeBase):
            def __init__(self):
                super().__init__()
                del self.tts_meta["files"]["local_fixed_sampled_frame"]

        with self.assertRaisesRegex(RuntimeError, "fixed-sampling"):
            MOSS.streaming_runtime_class(MissingFixed)()._create_sessions()


if __name__ == "__main__":
    unittest.main()
