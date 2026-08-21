import importlib.machinery
import importlib.util
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest import mock
import wave

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("sherpa_voice", str(ROOT / "cli/sherpa-voice"))
spec = importlib.util.spec_from_loader(loader.name, loader)
sv = importlib.util.module_from_spec(spec)
loader.exec_module(sv)
ut_loader = importlib.machinery.SourceFileLoader("uttermux_cli", str(ROOT / "cli/uttermux"))
ut_spec = importlib.util.spec_from_loader(ut_loader.name, ut_loader)
ut = importlib.util.module_from_spec(ut_spec)
ut_loader.exec_module(ut)
profile_loader = importlib.machinery.SourceFileLoader("uttermux_profiles_test", str(ROOT / "python/uttermux_profiles.py"))
profile_spec = importlib.util.spec_from_loader(profile_loader.name, profile_loader)
profiles = importlib.util.module_from_spec(profile_spec); profile_loader.exec_module(profiles)


class CliTests(unittest.TestCase):
    def test_cpu_recommendations_are_advisory_and_memory_aware(self):
        hardware = {"logicalCores": 8, "totalRamMb": 8000, "availableRamMb": 6000}
        self.assertEqual(ut.recommend_model({"location": "on-device", "estimatedRamMb": 180,
            "performanceClass": "fast"}, hardware)[0], "recommended")
        self.assertEqual(ut.recommend_model({"location": "on-device", "estimatedRamMb": 7000,
            "performanceClass": "heavy"}, hardware)[0], "insufficient-memory")
        self.assertEqual(ut.recommend_model({"location": "cloud"}, hardware)[0], "available")
        self.assertEqual(ut.hardware_profile()["inferenceProviders"], ["CPU"])

    def test_preview_uses_broker_sample_format_not_channel_count(self):
        packets = [(ut.AUDIO_START, __import__("struct").pack("<IB", 24000, 2)),
                   (ut.AUDIO, b"\x00\x01")]
        process = mock.Mock()
        process.stdin = mock.Mock()
        process.poll.return_value = 0
        process.wait.return_value = 0
        with mock.patch.object(ut, "voice_records", return_value=[{
                "id": "edge/test", "name": "Test", "native_language": "en-US"}]), \
             mock.patch.object(ut, "transact", return_value=iter(packets)), \
             mock.patch.object(ut, "GLib_find_program", return_value="/usr/bin/paplay"), \
             mock.patch.object(ut.subprocess, "Popen", return_value=process) as popen:
            ut.cmd_preview(__import__("argparse").Namespace(
                voice="edge/test", language="", text="Preview"))
        self.assertIn("--format=s16le", popen.call_args.args[0])
        self.assertIn("--channels=1", popen.call_args.args[0])

    def test_benchmark_reports_first_audio_duration_and_rtf(self):
        packets = [(ut.AUDIO_START, __import__("struct").pack("<IB", 1000, 2)),
                   (ut.AUDIO, b"\0" * 2000)]
        with mock.patch.object(ut, "transact", return_value=iter(packets)), \
             mock.patch.object(ut.time, "perf_counter", side_effect=(10.0, 10.1, 10.25, 10.5)):
            result = ut.benchmark_once("sherpa/test", "Test", "en-US")
        self.assertEqual(result["firstAudioMs"], 250.0)
        self.assertEqual(result["audioSeconds"], 1.0)
        self.assertEqual(result["rtf"], 0.5)

    def test_benchmark_record_has_machine_and_summary(self):
        run = {"firstAudioMs": 25.0, "wallMs": 500.0, "audioSeconds": 1.0,
               "rtf": 0.5, "sampleRate": 24000}
        args = __import__("argparse").Namespace(voice="Test", text="Hello", language="",
                                                runs=2, json=True, save=False, output=None)
        record = {"id": "sherpa/test/voice", "name": "Test", "native_language": "en-US",
                  "provider": "local", "model": "test"}
        with mock.patch.object(ut, "voice_records", return_value=[record]), \
             mock.patch.object(ut, "benchmark_once", return_value=run), \
             mock.patch.object(ut, "hardware_profile", return_value={"inferenceProviders": ["CPU"]}), \
             mock.patch("builtins.print") as output:
            ut.cmd_benchmark(args)
        document = __import__("json").loads(output.call_args.args[0])
        self.assertEqual(document["schemaVersion"], 2)
        self.assertEqual(document["summary"]["medianRtf"], 0.5)
        self.assertTrue(document["summary"]["continuousReading"])

    def test_schema_two_preserves_language_routes(self):
        rendered = ut.render_config({
            "default_voice": "edge/libby", "fallback_voice": "local/lessac",
            "routing": {"voices": {"fr": ["elevenlabs/bill", "edge/denise"]}},
        })
        self.assertIn("schema_version = 2", rendered)
        self.assertIn('[routing.voices]', rendered)
        self.assertIn('"fr" = ["elevenlabs/bill", "edge/denise"]', rendered)

    def test_render_preserves_advanced_tuning(self):
        rendered = ut.render_config({
            "local_threads": 2, "local_silence_scale": .1,
            "pocket_num_steps": 5, "pocket_chunk_size": 8,
            "zipvoice_num_steps": 6, "moss_threads": 2, "moss_batch_frames": 4,
        })
        self.assertIn("local_threads = 2", rendered)
        self.assertIn("local_silence_scale = 0.1", rendered)
        self.assertIn("pocket_num_steps = 5", rendered)
        self.assertIn("pocket_chunk_size = 8", rendered)
        self.assertIn("zipvoice_num_steps = 6", rendered)
        self.assertIn("moss_threads = 2", rendered)
        self.assertIn("moss_batch_frames = 4", rendered)

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
            self.assertEqual(catalog["moss-tts-nano-100m-onnx"]["external_installer"], "install-moss")
            self.assertEqual(catalog["kokoro-multi-lang-v1_0"]["voices"][0]["language"], "en-US")
            for item in catalog.values():
                if not item.get("external_installer"):
                    self.assertRegex(item["sha256"], r"^[0-9a-f]{64}$")
                self.assertGreater(item["size"], 0)
                for asset in item.get("assets", []):
                    self.assertRegex(asset["sha256"], r"^[0-9a-f]{64}$")
                    self.assertFalse(Path(asset["file"]).is_absolute())
                    self.assertNotIn("..", Path(asset["file"]).parts)
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
        self.assertIn("max_chunk_characters = 360", manifest)

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

    def test_pocket_profile_round_trip_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "source.wav"
            with wave.open(str(source), "wb") as output:
                output.setnchannels(1); output.setsampwidth(2); output.setframerate(24000)
                output.writeframes((b"\0\x20" * 24000 * 2))
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(root / "data")}), \
                 mock.patch.object(profiles.subprocess, "run", wraps=profiles.subprocess.run):
                item = profiles.create_local("pocket", "My Voice", "en_US", source)
                self.assertTrue(Path(item["referencePath"]).is_file())
                artifact = root / "embedding.bin"; artifact.write_bytes(b"prepared embedding")
                profiles.register_artifact(item["id"], "speaker-embedding", artifact)
                bundle = profiles.export_profile(item["id"], root / "voice")
                profiles.delete_profile(item["id"])
                imported = profiles.import_profile(bundle)
                self.assertEqual(imported["name"], "My Voice")
                self.assertEqual(imported["language"], "en-US")
                self.assertNotEqual(imported["id"], item["id"])
                loaded = profiles.find_profile(imported["id"])
                self.assertIn("speaker-embedding", loaded["artifactPaths"])

    def test_zipvoice_requires_exact_transcript(self):
        with self.assertRaisesRegex(ValueError, "exact transcript"):
            profiles.create_local("zipvoice", "Voice", "en-US", Path("missing.wav"))

    def test_reference_normalization_preserves_internal_pauses(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "paused.wav"; target = root / "normalized.wav"
            with wave.open(str(source), "wb") as output:
                output.setnchannels(1); output.setsampwidth(2); output.setframerate(24000)
                output.writeframes(b"\0\x20" * 24000)
                output.writeframes(b"\0\0" * 24000)
                output.writeframes(b"\0\x20" * 24000)
            profiles.normalize_reference(source, target)
            with wave.open(str(target), "rb") as normalized:
                self.assertGreater(normalized.getnframes() / normalized.getframerate(), 2.5)


if __name__ == "__main__":
    unittest.main()
