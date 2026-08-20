#!/usr/bin/env python3
"""Optional compatibility API for KOReader's TTS.koplugin."""

from __future__ import annotations

from collections import OrderedDict
from array import array
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import threading
import time
import wave

MAGIC, VERSION = 0x58544D55, 1
HEADER = struct.Struct("<IHHQI")
LIST_VOICES, VOICE, SYNTHESIZE, AUDIO_START, AUDIO, DONE, ERROR = 2, 3, 4, 5, 6, 7, 9
CACHE: OrderedDict[str, "Audio"] = OrderedDict()
LOCK = threading.Lock()


def broker_socket():
    value = os.environ.get("UTTERMUX_SOCKET")
    return value or str(Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "uttermux.sock")


def request(kind, payload=b""):
    client = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET); client.connect(broker_socket())
    client.sendall(HEADER.pack(MAGIC, VERSION, kind, 1, len(payload)) + payload)
    try:
        while True:
            raw = client.recv(65536); _m, _v, response, _rid, _size = HEADER.unpack_from(raw)
            body = raw[HEADER.size:]
            if response == ERROR: raise RuntimeError(body.decode(errors="replace"))
            if response == DONE: return
            yield response, body
    finally: client.close()


class Audio:
    def __init__(self, wav: bytes, duration: float):
        self.wav, self.duration, self.process, self.started_at = wav, duration, None, None
        self.position, self.generation = 0.0, 0

    def _remaining_wav(self):
        source, output = BytesIO(self.wav), BytesIO()
        with wave.open(source, "rb") as reader:
            params = reader.getparams(); frame = min(reader.getnframes(), round(self.position * reader.getframerate()))
            reader.setpos(frame); remaining = reader.readframes(reader.getnframes() - frame)
        with wave.open(output, "wb") as writer:
            writer.setparams(params); writer.writeframes(remaining)
        return output.getvalue()

    def play(self):
        if self.process and self.process.poll() is None:
            return
        if self.position >= self.duration - .05: self.position = 0.0
        self.generation += 1; generation = self.generation
        self.process = subprocess.Popen(["paplay", "--stream-name=UtterMux KOReader"], stdin=subprocess.PIPE)
        self.started_at = time.monotonic()
        threading.Thread(target=self._feed, args=(generation, self._remaining_wav()), daemon=True).start()

    def _feed(self, generation, content):
        process = self.process
        try: process.communicate(content)
        except (BrokenPipeError, OSError): pass
        if generation == self.generation and self.started_at is not None:
            self.position = min(self.duration, self.position + time.monotonic() - self.started_at)
            self.started_at = None

    def stop(self):
        if self.process and self.process.poll() is None:
            if self.started_at is not None:
                self.position = min(self.duration, self.position + time.monotonic() - self.started_at)
            self.generation += 1; self.process.terminate()
        self.process, self.started_at = None, None

    def remaining(self):
        position = self.position + (time.monotonic() - self.started_at if self.started_at is not None else 0)
        return self.started_at is not None, max(0.0, self.duration - position)


def synthesize(text, voice, speed):
    # KOReader persists old voice choices in its plugin settings. Following the
    # broker default keeps it aligned with Waybar and desktop selection changes.
    if os.environ.get("UTTERMUX_KOREADER_FOLLOW_DEFAULT", "1").casefold() not in {"0", "false", "no"}:
        voice = ""
    voice_id = voice if not voice or "/" in voice else f"edge/{voice}"
    payload = b"\0".join((voice_id.encode(), str(speed).encode(), text.encode())) + b"\0"
    pcm, sample_rate, sample_width = BytesIO(), 0, 0
    for kind, body in request(SYNTHESIZE, payload):
        if kind == AUDIO_START: sample_rate, fmt = struct.unpack("<IB", body); sample_width = 2
        elif kind == AUDIO:
            if fmt == 1:
                values = array("f"); values.frombytes(body)
                converted = array("h", (max(-32768, min(32767, round(value * 32767))) for value in values))
                pcm.write(converted.tobytes())
            else: pcm.write(body)
    output = BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1); wav.setsampwidth(sample_width); wav.setframerate(sample_rate); wav.writeframes(pcm.getvalue())
    return Audio(output.getvalue(), len(pcm.getvalue()) / sample_width / sample_rate)


class Handler(BaseHTTPRequestHandler):
    def json(self):
        size = int(self.headers.get("Content-Length", "0")); return json.loads(self.rfile.read(size) or b"{}")

    def reply(self, value, content_type="text/plain"):
        body = value.encode() if isinstance(value, str) else json.dumps(value).encode()
        self.send_response(200); self.send_header("Content-Type", content_type); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        if self.path != "/voices": self.send_error(404); return
        result = {}
        for kind, body in request(LIST_VOICES):
            if kind != VOICE: continue
            values = [x.decode() for x in body.rstrip(b"\0").split(b"\0")]
            if len(values) < 4: continue
            voice_id, _name, language, _provider = values[:4]
            result.setdefault(language.replace("-", "_", 1), []).append(voice_id)
        self.reply(result, "application/json")

    def do_POST(self):
        try:
            data = self.json()
            if self.path == "/":
                text, voice = data.get("text", "").strip(), data.get("voice", "")
                if not text: raise ValueError("No text provided")
                scale = float(data.get("length_scale") or 1); key = hashlib.sha256(json.dumps([text, voice, scale]).encode()).hexdigest()[:20]
                with LOCK:
                    if key not in CACHE: CACHE[key] = synthesize(text, voice, 1 / scale)
                    CACHE.move_to_end(key)
                    while len(CACHE) > 20: CACHE.popitem(last=False)[1].stop()
                self.reply(key)
            elif self.path == "/play": CACHE[data["handle"]].play(); self.reply("")
            elif self.path == "/stop": CACHE[data["handle"]].stop(); self.reply("")
            elif self.path == "/remaining":
                started, remaining = CACHE[data["handle"]].remaining(); self.reply({"started": started, "remaining": remaining}, "application/json")
            else: self.send_error(404)
        except Exception as error: self.send_error(500, str(error))

    def log_message(self, fmt, *args): pass


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--host", default="127.0.0.1"); parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args(); ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__": main()
