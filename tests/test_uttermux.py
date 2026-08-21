import importlib.util
import ctypes
import io
import json
from pathlib import Path
import struct
import tempfile
import threading
import unittest
from unittest import mock
from collections import OrderedDict


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

    def test_kokoro_long_text_is_split_without_overlap_or_tm_symbol(self):
        text = (("First sentence is deliberately long enough to form a useful group. " * 4) +
                "Second paragraph™ ends here. Final sentence remains present.")
        chunks = self.u.synthesis_chunks(text, 120)
        self.assertGreater(len(chunks), 2)
        self.assertTrue(all(len(chunk) <= 120 for chunk in chunks))
        joined = " ".join(chunks)
        self.assertNotIn("™", joined)
        self.assertEqual(joined.count("Final sentence remains present."), 1)

    def test_shared_normalization_does_not_apply_external_chunking(self):
        self.assertEqual(self.u.normalize_synthesis_text("A\u00adB™\n\nC"), "AB C")

    def test_kokoro_chunks_share_one_audio_header(self):
        broker = object.__new__(self.u.Broker)
        model = {"id": "kokoro", "engine": "kokoro"}; voice = {"speaker_id": 2}
        engine = mock.MagicMock(); engine.lock = mock.MagicMock()
        def synthesize(_text, _speaker, _speed, emit, _cancelled, _profile):
            emit(self.u.packet(self.u.AUDIO_START, 0, struct.pack("<IB", 24000, 1)))
            emit(self.u.packet(self.u.AUDIO, 0, b"pcm"))
        engine.synthesize.side_effect = synthesize; broker.engine = mock.Mock(return_value=engine)
        emitted = []
        broker.synthesize_local(model, voice, "One sentence. " * 80, 1.0,
                                emitted.append, threading.Event())
        kinds = [self.u.HEADER.unpack_from(raw)[2] for raw in emitted]
        self.assertEqual(kinds.count(self.u.AUDIO_START), 1)
        self.assertGreater(kinds.count(self.u.AUDIO), 1)

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
        broker.runtime_lock = threading.Lock()
        broker.runtime = {"status": "idle", "activeVoice": "", "routedVoice": "",
                          "language": "", "fallbackReason": ""}
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

    def test_qwen_uses_streaming_local_endpoint_and_language(self):
        provider = object.__new__(self.u.QwenProvider)
        provider.port = 17872
        provider.lock = threading.Lock(); provider.idle_seconds = 0; provider.idle_generation = 0; provider.idle_timer = None
        provider._ensure_server = mock.Mock()
        response = mock.MagicMock()
        response.__enter__.return_value = io.BytesIO(b"\0\1" * 100)
        emitted = []
        with mock.patch.object(self.u.urllib.request, "urlopen", return_value=response) as open_url:
            provider.synthesize("qwen/ryan", "Bonjour", 1.1, emitted.append,
                                threading.Event(), "fr-FR")
        body = json.loads(open_url.call_args.args[0].data)
        self.assertEqual((body["speaker"], body["language"]), ("ryan", "French"))
        self.assertTrue(emitted)

    def test_heavy_runtime_idle_generation_does_not_stop_reused_process(self):
        provider = object.__new__(self.u.QwenProvider)
        provider.lock = threading.Lock(); provider.idle_generation = 2; provider.idle_timer = None
        process = provider.process = mock.MagicMock(); process.poll.return_value = None
        provider._stop_if_idle(1)
        process.terminate.assert_not_called()
        provider._stop_if_idle(2)
        process.terminate.assert_called_once()
        self.assertIsNone(provider.process)

    def test_sherpa_forwards_completed_audio_when_engine_does_not_stream(self):
        samples = (ctypes.c_float * 3)(.25, -.5, .75)
        generated = self.u.GeneratedAudio(samples, 3, 24000)
        api = mock.MagicMock()
        api.SherpaOnnxOfflineTtsGenerateWithConfig.return_value = ctypes.pointer(generated)
        engine = object.__new__(self.u.SherpaEngine)
        engine.api, engine.handle, engine.sample_rate = api, object(), 24000
        engine.lock = threading.RLock()
        engine.engine_type, engine.silence_scale = "pocket", .1
        engine.pocket_num_steps, engine.pocket_chunk_size = 5, 8
        emitted = []
        engine.synthesize("hello", 0, 1.0, emitted.append, threading.Event())
        kinds = [self.u.HEADER.unpack_from(raw)[2] for raw in emitted]
        self.assertEqual(kinds, [self.u.AUDIO_START, self.u.AUDIO])
        self.assertEqual(emitted[1][self.u.HEADER.size:], bytes(samples))
        generation = api.SherpaOnnxOfflineTtsGenerateWithConfig.call_args.args[2]._obj
        self.assertEqual(generation.num_steps, 5)
        self.assertAlmostEqual(generation.silence_scale, .1)
        self.assertEqual(json.loads(generation.extra), {"max_reference_audio_len": 10.0, "chunk_size": 8})
        api.SherpaOnnxDestroyOfflineTtsGeneratedAudio.assert_called_once()

    def test_preload_warms_only_the_configured_local_voice(self):
        broker = object.__new__(self.u.Broker)
        model = {"id": "kokoro", "engine": "kokoro"}
        broker.config = {"preload_default_voice": True, "default_voice": "local/bella"}
        broker.voices = {"local/bella": (model, {"id": "bella"})}
        loaded = mock.MagicMock()
        broker.engine = mock.Mock(return_value=loaded)
        broker.preload_default_voice()
        broker.engine.assert_called_once_with(model)
        loaded.lock.release.assert_called_once()

    def test_broker_applies_global_local_tuning_to_new_engine(self):
        broker = object.__new__(self.u.Broker)
        broker.config = {"local_threads": 2, "pocket_threads": 3, "local_silence_scale": .1,
                         "pocket_num_steps": 5, "pocket_chunk_size": 8,
                         "zipvoice_num_steps": 6}
        broker.engine_lock = threading.Lock(); broker.engines = OrderedDict()
        broker.max_loaded_models = 2; broker.api = mock.Mock()
        fake = mock.MagicMock(); fake.lock = threading.RLock()
        model = {"id": "pocket", "engine": "pocket", "root": "/tmp", "files": {}, "num_threads": 9}
        with mock.patch.object(self.u, "SherpaEngine", return_value=fake) as constructor:
            result = broker.engine(model)
        effective = constructor.call_args.args[1]
        self.assertIs(result, fake)
        self.assertEqual((effective["num_threads"], effective["silence_scale"]), (3, .1))
        self.assertEqual((effective["pocket_num_steps"], effective["pocket_chunk_size"]), (5, 8))
        self.assertEqual(effective["zipvoice_num_steps"], 6)
        result.lock.release()

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

    def test_koreader_play_is_idempotent_and_stop_preserves_position(self):
        bridge = load_bridge(); audio = bridge.Audio(b"RIFF-invalid-for-mocked-feed", 10)
        process = mock.MagicMock(); process.poll.return_value = None
        with mock.patch.object(bridge.subprocess, "Popen", return_value=process), \
             mock.patch.object(bridge.threading, "Thread") as thread, \
             mock.patch.object(audio, "_remaining_wav", return_value=b"wav"), \
             mock.patch.object(bridge.time, "monotonic", side_effect=[100.0, 102.5]):
            audio.play(); audio.play(); audio.stop()
        self.assertEqual(thread.call_count, 1)
        self.assertAlmostEqual(audio.position, 2.5)
        process.terminate.assert_called_once()


if __name__ == "__main__": unittest.main()
