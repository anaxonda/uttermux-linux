#!/usr/bin/env python3
"""Authenticated loopback adapter for Zotero's remote Read Aloud controller."""

from __future__ import annotations

from array import array
import argparse
from collections import deque
from contextlib import contextmanager
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
import logging
import os
from pathlib import Path
import secrets
import select
import socket
import struct
import threading
import time
import wave
import zlib

MAGIC, VERSION = 0x58544D55, 1
HEADER = struct.Struct("<IHHQI")
LIST_VOICES, VOICE, SYNTHESIZE, AUDIO_START, AUDIO, DONE, CANCEL, ERROR = 2, 3, 4, 5, 6, 7, 8, 9
MAX_TEXT = 8000
CLOUD_SYNTHESIS_SLOTS = threading.BoundedSemaphore(2)


class FairSlot:
    """A FIFO slot for non-reentrant local model runtimes."""

    def __init__(self):
        self.condition = threading.Condition()
        self.queue = deque()

    @contextmanager
    def hold(self, cancelled: threading.Event):
        token = object()
        with self.condition:
            self.queue.append(token)
            while self.queue[0] is not token:
                if cancelled.is_set():
                    self.queue.remove(token)
                    self.condition.notify_all()
                    raise ClientDisconnected()
                self.condition.wait(.1)
            if cancelled.is_set():
                self.queue.popleft()
                self.condition.notify_all()
                raise ClientDisconnected()
        try:
            yield
        finally:
            with self.condition:
                if self.queue and self.queue[0] is token:
                    self.queue.popleft()
                elif token in self.queue:
                    self.queue.remove(token)
                self.condition.notify_all()


LOCAL_SYNTHESIS_SLOT = FairSlot()
LOG = logging.getLogger("uttermux-zotero")
VOICE_HISTORY: dict[str, dict] = {}
VOICE_HISTORY_LOCK = threading.Lock()


class ClientDisconnected(RuntimeError):
    pass


@contextmanager
def synthesis_slot(local: bool, cancelled: threading.Event):
    if local:
        with LOCAL_SYNTHESIS_SLOT.hold(cancelled):
            yield
        return
    while not CLOUD_SYNTHESIS_SLOTS.acquire(timeout=.1):
        if cancelled.is_set():
            raise ClientDisconnected()
    try:
        yield
    finally:
        CLOUD_SYNTHESIS_SLOTS.release()


def runtime_dir() -> Path:
    return Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))


def broker_path() -> str:
    return os.environ.get("UTTERMUX_SOCKET", str(runtime_dir() / "uttermux.sock"))


def token_path() -> Path:
    return runtime_dir() / "uttermux-zotero.token"


def ensure_token() -> str:
    path = token_path()
    try:
        token = path.read_text(encoding="utf-8").strip()
        if token:
            return token
    except OSError:
        pass
    token = secrets.token_urlsafe(32)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(token + "\n")
    os.chmod(path, 0o600)
    return token


def fields(*values: str) -> bytes:
    return b"\0".join(value.encode("utf-8") for value in values) + b"\0"


def broker_request(kind: int, payload: bytes = b"", cancelled: threading.Event | None = None):
    client = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    client.connect(broker_path())
    client.sendall(HEADER.pack(MAGIC, VERSION, kind, 1, len(payload)) + payload)
    finished = threading.Event()
    if cancelled is not None:
        def cancel_connection():
            while not finished.wait(.1):
                if cancelled.is_set():
                    try:
                        client.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    return
        threading.Thread(target=cancel_connection, daemon=True).start()
    try:
        while True:
            try:
                raw = client.recv(65536)
            except OSError:
                if cancelled is not None and cancelled.is_set():
                    raise ClientDisconnected()
                raise
            if not raw:
                if cancelled is not None and cancelled.is_set():
                    raise ClientDisconnected()
                raise RuntimeError("UtterMux broker disconnected")
            magic, version, response, request, size = HEADER.unpack_from(raw)
            if (magic, version, request, size) != (MAGIC, VERSION, 1, len(raw) - HEADER.size):
                raise RuntimeError("invalid UtterMux broker response")
            body = raw[HEADER.size:]
            if response == ERROR:
                raise RuntimeError(body.decode("utf-8", "replace"))
            if response == DONE:
                return
            yield response, body
    finally:
        finished.set()
        client.close()  # Disconnecting cancels a still-running broker job.


def voices() -> list[dict]:
    result = []
    for kind, body in broker_request(LIST_VOICES, fields("zotero")):
        if kind != VOICE:
            continue
        values = [part.decode("utf-8") for part in body.rstrip(b"\0").split(b"\0")]
        if len(values) < 6:
            continue
        voice_id, name, language, provider, model, capabilities = values[:6]
        result.append({"id": voice_id, "name": name, "language": language,
                       "provider": provider, "model": model,
                       "languages": [item for item in capabilities.split(",") if item] or [language]})
    with VOICE_HISTORY_LOCK:
        for record in result:
            VOICE_HISTORY[record["id"]] = record
    return result


def known_voices() -> dict[str, dict]:
    voices()
    with VOICE_HISTORY_LOCK:
        return dict(VOICE_HISTORY)


def cache_version(records: list[dict]) -> int:
    config_root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "uttermux"
    parts = [f"{record['id']}\0{record['model']}" for record in records]
    paths = [config_root / "config.toml"]
    paths.extend(sorted((config_root / "models.d").glob("*.toml")))
    for path in paths:
        try:
            stat = path.stat()
            parts.append(f"{path.name}\0{stat.st_size}\0{stat.st_mtime_ns}")
        except OSError:
            continue
    return zlib.crc32("\n".join(parts).encode("utf-8")) or 1


def synthesize(text: str, voice: str, speed: float, language: str, *, local: bool = False,
               cancelled: threading.Event | None = None) -> bytes:
    cancelled = cancelled or threading.Event()
    with synthesis_slot(local, cancelled):
        attempts = 2 if local else 1
        for attempt in range(attempts):
            pcm, sample_rate, sample_format = BytesIO(), 0, 0
            try:
                for kind, body in broker_request(
                        SYNTHESIZE, fields(voice, str(speed), text, language), cancelled):
                    if kind == AUDIO_START:
                        if len(body) not in (4, 5):
                            raise RuntimeError("invalid audio format")
                        sample_rate = struct.unpack_from("<I", body)[0]
                        sample_format = body[4] if len(body) == 5 else 1
                    elif kind == AUDIO:
                        if sample_format == 1:
                            values = array("f"); values.frombytes(body)
                            converted = array("h", (max(-32768, min(32767, round(value * 32767))) for value in values))
                            pcm.write(converted.tobytes())
                        elif sample_format == 2:
                            pcm.write(body)
                        else:
                            raise RuntimeError("unsupported audio format")
                break
            except ClientDisconnected:
                raise
            except (OSError, RuntimeError) as error:
                retryable = isinstance(error, OSError) or "broker disconnected" in str(error).lower()
                if not retryable or attempt + 1 >= attempts:
                    raise
                if cancelled.wait(.25):
                    raise ClientDisconnected()
    if not sample_rate or not pcm.tell():
        raise RuntimeError("UtterMux returned no audio")
    output = BytesIO()
    with wave.open(output, "wb") as target:
        target.setnchannels(1); target.setsampwidth(2); target.setframerate(sample_rate)
        target.writeframes(pcm.getvalue())
    return output.getvalue()


class Handler(BaseHTTPRequestHandler):
    server_version = "UtterMuxZotero/1"

    def authenticated(self) -> bool:
        authority = self.headers.get("Host", "").strip()
        if authority.startswith("[") and "]" in authority:
            host = authority[1:authority.index("]")].casefold()
        else:
            host = authority.rsplit(":", 1)[0].casefold()
        if host not in {"127.0.0.1", "localhost", "::1"}:
            return False
        supplied = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        return bool(supplied and hmac.compare_digest(supplied, self.server.token))

    def reply(self, status: int, body=b"", content_type="application/json",
              cache_control="no-store"):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache_control)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        if not self.authenticated():
            self.reply(401, {"error": "unauthorized"}); return
        try:
            if self.path == "/health":
                self.reply(200, {"status": "ok", "schemaVersion": 1})
            elif self.path == "/v1/voices":
                records = voices()
                self.reply(200, {"schemaVersion": 1, "cacheVersion": cache_version(records),
                                 "voices": records})
            else:
                self.reply(404, {"error": "not found"})
        except Exception as error:
            self.reply(503, {"error": str(error)})

    def do_POST(self):
        if not self.authenticated():
            self.reply(401, {"error": "unauthorized"}); return
        if self.path != "/v1/audio/speech":
            self.reply(404, {"error": "not found"}); return
        started = time.monotonic()
        provider = "unknown"
        text_length = 0
        cancelled = threading.Event()
        request_done = threading.Event()

        def watch_client():
            while not request_done.wait(.1):
                try:
                    readable, _, _ = select.select([self.connection], [], [], 0)
                    if readable and not self.connection.recv(
                            1, socket.MSG_PEEK | socket.MSG_DONTWAIT):
                        cancelled.set()
                        return
                except (OSError, ValueError):
                    cancelled.set()
                    return

        threading.Thread(target=watch_client, daemon=True).start()
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size <= 0 or size > MAX_TEXT * 4 + 4096:
                raise ValueError("invalid request size")
            data = json.loads(self.rfile.read(size))
            text, voice = str(data.get("input", "")).strip(), str(data.get("voice", "")).strip()
            text_length = len(text)
            if not text or len(text) > MAX_TEXT:
                raise ValueError("text must contain 1 to 8000 characters")
            known = known_voices()
            if voice not in known:
                raise ValueError("voice is not exposed to Zotero")
            provider = known[voice]["provider"]
            language = str(data.get("language") or known[voice]["language"])
            speed = max(.5, min(2.0, float(data.get("speed", 1.0))))
            local = provider == "local"
            audio = synthesize(text, voice, speed, language, local=local, cancelled=cancelled)
            if cancelled.is_set():
                raise ClientDisconnected()
            self.reply(200, audio, "audio/wav", "private" if local else "no-store")
            LOG.info("synthesis completed provider=%s chars=%d seconds=%.3f bytes=%d",
                     provider, text_length, time.monotonic() - started, len(audio))
        except ClientDisconnected:
            LOG.info("synthesis canceled provider=%s chars=%d seconds=%.3f",
                     provider, text_length, time.monotonic() - started)
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            LOG.warning("request rejected provider=%s chars=%d error=%s",
                        provider, text_length, error)
            self.reply(400, {"error": str(error)})
        except Exception as error:
            LOG.exception("synthesis failed provider=%s chars=%d seconds=%.3f",
                          provider, text_length, time.monotonic() - started)
            self.reply(503, {"error": str(error)})
        finally:
            request_done.set()

    def log_message(self, _format, *_args):
        pass


class BridgeServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    server = BridgeServer((args.host, args.port), Handler)
    server.token = ensure_token()
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
