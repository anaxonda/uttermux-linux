import importlib.util
from array import array
from pathlib import Path
import json
from http.client import HTTPConnection
import struct
import threading
import time
import unittest
from unittest import mock
import wave
from io import BytesIO


def load_bridge():
    path = Path(__file__).parents[1] / "bridge/zotero_bridge.py"
    spec = importlib.util.spec_from_file_location("zotero_bridge", path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


class ZoteroBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bridge = load_bridge()

    def test_voice_listing_preserves_broker_filter(self):
        body = self.bridge.fields("sherpa/mary", "Mary · Local", "en-US", "local", "pocket", "en-US")
        with mock.patch.object(self.bridge, "broker_request", return_value=iter([(self.bridge.VOICE, body)])):
            self.assertEqual(self.bridge.voices()[0]["id"], "sherpa/mary")

    def test_float_pcm_is_returned_as_valid_wav(self):
        start = struct.pack("<IB", 24000, 1)
        pcm = array("f", [0.0, 0.5, -0.5]).tobytes()
        packets = [(self.bridge.AUDIO_START, start), (self.bridge.AUDIO, pcm)]
        with mock.patch.object(self.bridge, "broker_request", return_value=iter(packets)):
            result = self.bridge.synthesize("hello", "sherpa/mary", 1.0, "en-US")
        with wave.open(BytesIO(result), "rb") as source:
            self.assertEqual((source.getframerate(), source.getnchannels(), source.getnframes()),
                             (24000, 1, 3))

    def test_local_slot_cancels_a_queued_request(self):
        first_cancelled = threading.Event()
        second_cancelled = threading.Event()
        outcome = []
        entered = threading.Event()
        release = threading.Event()

        def first():
            with self.bridge.LOCAL_SYNTHESIS_SLOT.hold(first_cancelled):
                entered.set(); release.wait(2)

        def second():
            try:
                with self.bridge.LOCAL_SYNTHESIS_SLOT.hold(second_cancelled):
                    outcome.append("entered")
            except self.bridge.ClientDisconnected:
                outcome.append("cancelled")

        one = threading.Thread(target=first); one.start(); self.assertTrue(entered.wait(1))
        two = threading.Thread(target=second); two.start(); time.sleep(.05); second_cancelled.set()
        two.join(1); release.set(); one.join(1)
        self.assertEqual(outcome, ["cancelled"])

    def test_local_broker_disconnect_is_retried_once(self):
        calls = 0
        start = struct.pack("<IB", 24000, 2)

        def request(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                def failed():
                    raise RuntimeError("UtterMux broker disconnected")
                    yield
                return failed()
            return iter([(self.bridge.AUDIO_START, start), (self.bridge.AUDIO, b"\0\0")])

        with mock.patch.object(self.bridge, "broker_request", side_effect=request):
            result = self.bridge.synthesize("hello", "sherpa/mary", 1, "en-US", local=True)
        self.assertEqual(calls, 2)
        self.assertTrue(result.startswith(b"RIFF"))

    def test_http_authentication_and_cache_policy(self):
        try:
            server = self.bridge.ThreadingHTTPServer(("127.0.0.1", 0), self.bridge.Handler)
        except PermissionError:
            self.skipTest("test sandbox does not permit loopback sockets")
        server.token = "test-token"
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        local = {"id": "sherpa/mary", "name": "Mary", "language": "en-US",
                 "provider": "local", "model": "pocket", "languages": ["en-US"]}
        try:
            connection = HTTPConnection("127.0.0.1", server.server_port)
            connection.request("GET", "/health")
            self.assertEqual(connection.getresponse().status, 401)
            headers = {"Authorization": "Bearer test-token", "Content-Type": "application/json"}
            with mock.patch.object(self.bridge, "known_voices", return_value={local["id"]: local}), \
                 mock.patch.object(self.bridge, "synthesize", return_value=b"RIFFaudio"):
                connection.request("POST", "/v1/audio/speech", json.dumps({
                    "voice": local["id"], "input": "Hello", "language": "en-US"}), headers)
                response = connection.getresponse(); response.read()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.getheader("Cache-Control"), "private")
        finally:
            server.shutdown(); server.server_close(); thread.join(1)

    def test_ipv6_loopback_host_is_accepted(self):
        handler = object.__new__(self.bridge.Handler)
        handler.headers = {"Host": "[::1]:8766", "Authorization": "Bearer test-token"}
        handler.server = type("Server", (), {"token": "test-token"})()
        self.assertTrue(handler.authenticated())


if __name__ == "__main__":
    unittest.main()
