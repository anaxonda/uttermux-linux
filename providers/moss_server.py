#!/usr/bin/env python3
"""Small persistent streaming server for the official MOSS-TTS-Nano ONNX export."""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import queue
import sys
import threading
import types


def streaming_runtime_class(base):
    """Return an upstream runtime limited to the fixed-sample streaming graph set."""
    class StreamingOnnxTtsRuntime(base):
        def _create_sessions(self):
            tts_dir = self.tts_meta_path.parent
            codec_dir = self.codec_meta_path.parent
            fixed_frame = self.tts_meta["files"].get("local_fixed_sampled_frame")
            if not fixed_frame:
                raise RuntimeError("MOSS fixed-sampling graph is missing")
            return {
                "prefill": self._session(tts_dir / self.tts_meta["files"]["prefill"]),
                "decode": self._session(tts_dir / self.tts_meta["files"]["decode_step"]),
                "local_fixed_sampled_frame": self._session(tts_dir / fixed_frame),
                "codec_decode_step": self._session(
                    codec_dir / self.codec_meta["files"]["decode_step"]),
            }

    return StreamingOnnxTtsRuntime


def load_runtime(source: str, models: str, threads: int):
    # Upstream imports PyTorch solely for custom reference-audio loading.  The
    # built-in prompt-code voices need only NumPy, SentencePiece and ORT.
    torch = types.ModuleType("torch"); torch.Tensor = object; torch.float32 = object()
    torchaudio = types.ModuleType("torchaudio")
    sys.modules.setdefault("torch", torch); sys.modules.setdefault("torchaudio", torchaudio)
    sys.path.insert(0, source)
    from onnx_tts_runtime import OnnxTtsRuntime
    runtime = streaming_runtime_class(OnnxTtsRuntime)
    return runtime(model_dir=models, thread_count=threads,
                   sample_mode="fixed", do_sample=True)


class MossService:
    def __init__(self, runtime):
        self.runtime = runtime
        self.lock = threading.Lock()
        self.state_lock = threading.Lock()
        self.cancel_event: threading.Event | None = None

    def cancel(self):
        with self.state_lock:
            if self.cancel_event:
                self.cancel_event.set()

    def stream(self, text: str, voice: str, batch_frames: int = 4):
        with self.lock:
            cancel = threading.Event()
            with self.state_lock:
                self.cancel_event = cancel
            frames: queue.Queue = queue.Queue(maxsize=24)
            audio: queue.Queue = queue.Queue(maxsize=16)
            error: list[BaseException] = []

            def put(target, value):
                while not cancel.is_set():
                    try:
                        target.put(value, timeout=.1); return True
                    except queue.Full:
                        pass
                return False

            def finish(target, value):
                # Terminal markers must be delivered even after cancellation;
                # otherwise a consumer waiting on an empty queue can deadlock.
                while True:
                    try:
                        target.put(value, timeout=.1); return
                    except queue.Full:
                        if cancel.is_set():
                            try: target.get_nowait()
                            except queue.Empty: pass

            def produce():
                try:
                    prompt = self.runtime.resolve_prompt_audio_codes(
                        voice=voice, prompt_audio_path=None)
                    chunks = self.runtime.split_voice_clone_text(text, max_tokens=75)
                    for chunk in chunks:
                        if cancel.is_set(): break
                        put(frames, ("start", None))
                        tokens = self.runtime.encode_text(chunk)
                        rows = self.runtime.build_voice_clone_request_rows(prompt, tokens)

                        def on_frame(_all, _index, frame):
                            if cancel.is_set() or not put(frames, ("frame", list(frame))):
                                raise InterruptedError("MOSS synthesis cancelled")

                        self.runtime.generate_audio_frames(rows, on_frame=on_frame)
                        put(frames, ("end", None))
                except InterruptedError:
                    pass
                except BaseException as exc:
                    error.append(exc); cancel.set()
                finally:
                    put(frames, ("done", None))

            def decode():
                pending = []

                def flush():
                    nonlocal pending
                    if not pending: return
                    decoded = self.runtime.codec_streaming_session.run_frames(pending)
                    pending = []
                    if decoded is None: return
                    samples, length = decoded
                    if length <= 0: return
                    # Official codec output is [batch, channels, samples].
                    mono = samples[0, :, :length].mean(axis=0).astype("<f4", copy=False)
                    put(audio, mono.tobytes())

                try:
                    while not cancel.is_set():
                        kind, value = frames.get()
                        if kind == "start":
                            pending = []; self.runtime.codec_streaming_session.reset()
                        elif kind == "frame":
                            pending.append(value)
                            if len(pending) >= batch_frames: flush()
                        elif kind == "end":
                            flush(); self.runtime.codec_streaming_session.reset()
                        elif kind == "done":
                            break
                except BaseException as exc:
                    error.append(exc); cancel.set()
                finally:
                    finish(audio, None)

            producer = threading.Thread(target=produce, name="moss-generate", daemon=True)
            decoder = threading.Thread(target=decode, name="moss-decode", daemon=True)
            producer.start(); decoder.start()
            try:
                while True:
                    item = audio.get()
                    if item is None: break
                    yield item
                if error and not cancel.is_set(): raise error[0]
                if error and not isinstance(error[0], InterruptedError): raise error[0]
            finally:
                cancel.set(); producer.join(timeout=2); decoder.join(timeout=2)
                with self.state_lock:
                    self.cancel_event = None


def handler(service: MossService):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def log_message(self, fmt, *args):
            return

        def send_json(self, value, status=200):
            raw = json.dumps(value).encode()
            self.send_response(status); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)

        def do_GET(self):
            if self.path == "/v1/health": self.send_json({"status": "ok"})
            elif self.path == "/v1/voices": self.send_json(service.runtime.list_builtin_voices())
            else: self.send_json({"error": "not found"}, 404)

        def do_POST(self):
            if self.path == "/v1/cancel":
                service.cancel(); self.send_json({"status": "cancelled"}); return
            if self.path != "/v1/tts/stream":
                self.send_json({"error": "not found"}, 404); return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(size))
                self.send_response(200); self.send_header("Content-Type", "application/octet-stream")
                self.send_header("X-Sample-Rate", "48000"); self.end_headers()
                for chunk in service.stream(str(body.get("text", "")),
                                            str(body.get("voice", "Adam")),
                                            max(1, int(body.get("batch_frames", 4)))):
                    self.wfile.write(chunk); self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                service.cancel()
            except Exception as exc:
                service.cancel()
                print(f"moss-server: {exc}", file=sys.stderr)
    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True); parser.add_argument("--models", required=True)
    parser.add_argument("--threads", type=int, default=2); parser.add_argument("--port", type=int, default=17873)
    args = parser.parse_args()
    runtime = load_runtime(args.source, args.models, max(1, args.threads))
    ThreadingHTTPServer(("127.0.0.1", args.port), handler(MossService(runtime))).serve_forever()


if __name__ == "__main__": main()
