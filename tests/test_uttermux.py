import importlib.util
import io
import json
from pathlib import Path
import struct
import tempfile
import threading
import unittest
from unittest import mock


def load_daemon():
    path = Path(__file__).parents[1] / "daemon/uttermuxd.py"
    spec = importlib.util.spec_from_file_location("uttermuxd", path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def load_bridge():
    path = Path(__file__).parents[1] / "bridge/koreader_bridge.py"
    spec = importlib.util.spec_from_file_location("koreader_bridge", path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


class ProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.u = load_daemon()

    def test_packet_round_trip(self):
        raw = self.u.packet(self.u.SYNTHESIZE, 19, self.u.fields("voice", "1", "hello", "fr-FR"))
        kind, request, payload = self.u.unpack(raw)
        self.assertEqual((kind, request), (self.u.SYNTHESIZE, 19))
        self.assertEqual(self.u.split_fields(payload), ["voice", "1", "hello", "fr-FR"])

    def broker(self):
        broker = object.__new__(self.u.Broker)
        broker.config = {
            "default_voice": "edge/libby", "fallback_voice": "local/lessac",
            "routing": {"auto_detect": True, "minimum_characters": 40,
                        "minimum_confidence": .8, "default_language": "en-US",
                        "provider_order": ["elevenlabs", "edge", "local"],
                        "cross_language_fallback": True,
                        "voices": {"fr": ["elevenlabs/bill"]}},
        }
        broker.voice_meta = {
            "edge/libby": ("edge/libby", "Libby", "en-GB", "edge", "Edge", ("en-GB",), True),
            "elevenlabs/bill": ("elevenlabs/bill", "Bill", "en-US", "elevenlabs", "flash", ("en", "fr"), True),
            "edge/denise": ("edge/denise", "Denise", "fr-FR", "edge", "Edge", ("fr-FR",), False),
            "local/lessac": ("local/lessac", "Lessac", "en-US", "local", "piper", ("en-US",), True),
        }
        return broker

    def test_declared_language_wins_and_preserves_compatible_persona(self):
        broker = self.broker()
        broker._detect_language = mock.Mock(return_value="fr")
        language, route = broker._route("edge/libby", "en_US", "texte français suffisamment long")
        self.assertEqual(language, "en-US")
        self.assertEqual(route[0], "edge/libby")
        broker._detect_language.assert_not_called()

    def test_french_route_uses_bill_then_edge_then_global_fallback(self):
        language, route = self.broker()._route("edge/libby", "fr-FR", "Bonjour tout le monde")
        self.assertEqual(language, "fr-FR")
        self.assertEqual(route, ["elevenlabs/bill", "edge/denise", "local/lessac"])

    def test_no_fallback_after_audio_started(self):
        broker = self.broker()
        attempted = []
        def synthesize(voice, _text, _speed, _language, emit, _cancelled):
            attempted.append(voice)
            emit(self.u.packet(self.u.AUDIO_START, 0, b"\x00" * 5))
            raise RuntimeError("stream broke")
        broker._synthesize_voice = synthesize
        with self.assertRaises(RuntimeError):
            broker.synthesize("", "Bonjour tout le monde", 1, lambda _raw: None,
                              threading.Event(), "fr")
        self.assertEqual(attempted, ["elevenlabs/bill"])

    def test_grok_uses_provider_auto_language_and_pcm(self):
        voice_response = mock.MagicMock()
        voice_response.__enter__.return_value = io.BytesIO(
            b'{"voices":[{"voice_id":"eve","name":"Eve","language":"multilingual"}]}')
        audio_response = mock.MagicMock()
        audio_response.__enter__.return_value = io.BytesIO(b"\0\1" * 20)
        with mock.patch("pathlib.Path.read_text", return_value="secret\n"), \
             mock.patch.object(self.u.urllib.request, "urlopen",
                               side_effect=[voice_response, audio_response]) as open_url:
            provider = self.u.GrokProvider({"automatic_language": True})
            emitted = []
            provider.synthesize("grok/eve", "Bonjour", 1.0, emitted.append,
                                threading.Event(), "fr-FR")
        request = open_url.call_args_list[1].args[0]
        body = json.loads(request.data)
        self.assertEqual(body["language"], "auto")
        self.assertEqual(body["output_format"], {"codec": "pcm", "sample_rate": 24000})
        self.assertTrue(emitted)

    def test_rejects_bad_protocol_version(self):
        raw = bytearray(self.u.packet(self.u.HEALTH, 1)); raw[4:6] = (99).to_bytes(2, "little")
        with self.assertRaises(ValueError): self.u.unpack(bytes(raw))

    def test_manifest_loading_accepts_migration_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); manifests = root / "speech-dispatcher-sherpa/models.d"; manifests.mkdir(parents=True)
            (manifests / "voice.toml").write_text('id="voice"\nengine="vits"\nroot="/tmp/model"\n')
            old = self.u.Broker._manifest_dirs
            try:
                self.u.Broker._manifest_dirs = staticmethod(lambda: [manifests])
                instance = object.__new__(self.u.Broker)
                self.assertIn("voice", instance._load_models())
            finally: self.u.Broker._manifest_dirs = old

    def test_koreader_empty_voice_delegates_to_broker_default(self):
        bridge, captured = load_bridge(), []
        def responses(_kind, payload=b""):
            captured.append(payload.split(b"\0")[:-1])
            yield bridge.AUDIO_START, struct.pack("<IB", 24000, 2)
        with mock.patch.object(bridge, "request", responses):
            bridge.synthesize("hello", "", 1)
        self.assertEqual(captured[0][0], b"")

    def test_koreader_stale_voice_delegates_to_broker_default(self):
        bridge, captured = load_bridge(), []
        def responses(_kind, payload=b""):
            captured.append(payload.split(b"\0")[:-1])
            yield bridge.AUDIO_START, struct.pack("<IB", 24000, 2)
        with mock.patch.object(bridge, "request", responses), \
             mock.patch.dict("os.environ", {}, clear=False):
            bridge.synthesize("hello", "elevenlabs/old-voice", 1)
        self.assertEqual(captured[0][0], b"")


if __name__ == "__main__": unittest.main()
