import json
from pathlib import Path
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "daemon"))
import cloud_providers as cloud


class CloudProviderTests(unittest.TestCase):
    def test_every_documented_provider_has_discoverable_voice_metadata(self):
        configurations = {"resemble": {"voices": "voice-id"},
                          "custom": {"voices": "reader"}}
        for provider_id, provider_type in cloud.PROVIDERS.items():
            provider = provider_type(configurations.get(provider_id, {}))
            self.assertTrue(provider.voices(), provider_id)
            self.assertTrue(all(record[3] == provider_id for record in provider.voices()))

    def test_openai_compatible_request_emits_raw_pcm_packets(self):
        provider = cloud.OpenAiProvider({"api_key": "secret", "endpoint": "https://example.test",
                                         "model": "speech-model"})
        emitted = []
        with mock.patch.object(cloud, "request", return_value=(b"\0\1" * 20, "audio/pcm")) as send:
            provider.synthesize("openai/alloy", "Hello", 1.1, emitted.append, threading.Event(), "en-US")
        body = json.loads(send.call_args.kwargs["data"] if isinstance(send.call_args.kwargs["data"], bytes)
                          else json.dumps(send.call_args.kwargs["data"]))
        self.assertEqual(body["model"], "speech-model")
        self.assertEqual(body["voice"], "alloy")
        self.assertEqual(cloud.HEADER.unpack_from(emitted[0])[2], cloud.AUDIO_START)
        self.assertEqual(cloud.HEADER.unpack_from(emitted[1])[2], cloud.AUDIO)

    def test_custom_endpoint_is_constrained_to_json_and_pcm(self):
        provider = cloud.CustomProvider({"endpoint": "https://example.test/tts",
                                         "token": "secret", "voices": "reader"})
        emitted = []
        with mock.patch.object(cloud, "request", return_value=(b"\0\0", "audio/pcm")) as send:
            provider.synthesize("custom/reader", "Text", 1.0, emitted.append, threading.Event(), "fr-FR")
        self.assertEqual(send.call_args.args[0], "https://example.test/tts")
        self.assertEqual(send.call_args.kwargs["headers"]["Authorization"], "Bearer secret")
        self.assertEqual(send.call_args.kwargs["data"]["language"], "fr-FR")

    def test_custom_endpoint_rejects_cleartext_http(self):
        provider = cloud.CustomProvider({"endpoint": "http://example.test/tts", "voices": "reader"})
        with self.assertRaisesRegex(RuntimeError, "HTTPS"):
            provider.synthesize("custom/reader", "Text", 1.0, lambda _raw: None,
                                threading.Event(), "en-US")

    def test_polly_uses_supported_pcm_rate_and_announces_it(self):
        provider = cloud.AwsProvider({})
        provider.config.update({"access_key": "access", "secret_key": "secret"})
        emitted = []
        with mock.patch.object(cloud, "request", return_value=(b"\0\0", "audio/pcm")) as send:
            provider.synthesize("aws/Joanna/neural@en-US", "Text", 1.0, emitted.append,
                                threading.Event(), "en-US")
        body = json.loads(send.call_args.kwargs["data"])
        self.assertEqual(body["SampleRate"], "16000")
        self.assertEqual(cloud.HEADER.unpack_from(emitted[0])[2], cloud.AUDIO_START)
        self.assertEqual(cloud.struct.unpack("<IB", emitted[0][cloud.HEADER.size:])[0], 16000)
        request_body = json.loads(send.call_args.kwargs["data"])
        self.assertNotIn("LanguageCode", request_body)

    def test_qwen_maps_bcp47_to_documented_language_name(self):
        provider = cloud.QwenApiProvider({"api_key": "secret"})
        calls = []
        def reply(url, **kwargs):
            calls.append((url, kwargs))
            if len(calls) == 1:
                return json.dumps({"output": {"audio": {"url": "https://example.test/audio.wav"}}}).encode(), "application/json"
            return b"wav", "audio/wav"
        with mock.patch.object(cloud, "request", side_effect=reply), \
             mock.patch.object(cloud, "decoded_pcm", return_value=b"\0\0"):
            provider.synthesize("qwen-api/Cherry@zh-CN", "Bonjour", 1.0,
                                lambda _raw: None, threading.Event(), "fr-FR")
        self.assertEqual(calls[0][1]["data"]["input"]["language_type"], "French")
        self.assertNotIn("parameters", calls[0][1]["data"])

    def test_azure_resource_endpoint_uses_required_tts_prefix(self):
        provider = cloud.AzureProvider({"endpoint": "https://demo.cognitiveservices.azure.com"})
        self.assertEqual(provider.endpoint("voices/list"),
                         "https://demo.cognitiveservices.azure.com/tts/cognitiveservices/voices/list")
        regional = cloud.AzureProvider({"region": "eastus"})
        self.assertEqual(regional.endpoint("v1"),
                         "https://eastus.tts.speech.microsoft.com/cognitiveservices/v1")

    def test_deepgram_and_playht_apply_documented_speed_ranges(self):
        deepgram = cloud.DeepgramProvider({"api_key": "secret"}); play = cloud.PlayHtProvider({})
        play.config.update({"api_key": "secret", "user_id": "user"})
        with mock.patch.object(cloud, "request", return_value=(b"wav", "audio/wav")) as send, \
             mock.patch.object(cloud, "decoded_pcm", return_value=b"\0\0"):
            deepgram.synthesize("deepgram/aura-2-thalia-en", "Text", 3.0,
                                lambda _raw: None, threading.Event(), "en-US")
            self.assertIn("speed=1.5", send.call_args.args[0])
            play.synthesize("playht/default", "Texte", 9.0,
                            lambda _raw: None, threading.Event(), "fr-FR")
            self.assertEqual(send.call_args.kwargs["data"]["speed"], 5)
            self.assertEqual(send.call_args.kwargs["data"]["language"], "french")

    def test_cartesia_uses_api_key_header_and_paginates(self):
        pages = [
            (json.dumps({"data": [{"id": "one", "name": "One", "language": "en"}],
                         "has_more": True}).encode(), "application/json"),
            (json.dumps({"data": [{"id": "two", "name": "Two", "language": "fr"}],
                         "has_more": False}).encode(), "application/json"),
        ]
        with mock.patch.object(cloud, "request", side_effect=pages) as send:
            provider = cloud.CartesiaProvider({"api_key": "secret"})
        self.assertEqual(len(provider.voices()), 2)
        self.assertEqual(send.call_args_list[0].kwargs["headers"]["X-API-Key"], "secret")
        self.assertNotIn("Authorization", send.call_args_list[0].kwargs["headers"])
        self.assertIn("starting_after=one", send.call_args_list[1].args[0])


if __name__ == "__main__":
    unittest.main()
