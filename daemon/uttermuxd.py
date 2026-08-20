#!/usr/bin/env python3
"""UtterMux provider broker.

The daemon is intentionally independent of Speech Dispatcher.  It exposes a
small framed Unix-socket protocol and owns persistent provider state.
"""

from __future__ import annotations

import argparse
import asyncio
from array import array
from collections import OrderedDict
import ctypes
import json
import os
from pathlib import Path
import re
import socket
import struct
import subprocess
import sys
import threading
import time
import tomllib
from typing import Callable
import urllib.error
import urllib.request
import wave

for _common in reversed((Path(__file__).resolve().parents[1] / "python", Path("/usr/lib/uttermux"),
                         Path("/usr/local/lib/uttermux"))):
    if (_common / "uttermux_profiles.py").is_file() and str(_common) not in sys.path:
        sys.path.insert(0, str(_common))
try:
    import uttermux_profiles
except ImportError:
    uttermux_profiles = None

MAGIC = 0x58544D55
VERSION = 1
HEADER = struct.Struct("<IHHQI")
MAX_PACKET = 64 * 1024
HELLO, LIST_VOICES, VOICE, SYNTHESIZE, AUDIO_START, AUDIO, DONE, CANCEL, ERROR, HEALTH, STATUS, STATE = range(1, 13)

ELEVEN_FLASH_LANGUAGES = (
    "ar", "bg", "cs", "da", "de", "el", "en", "es", "fi", "fil", "fr", "hi", "hr",
    "hu", "id", "it", "ja", "ko", "ms", "nl", "no", "pl", "pt", "ro", "ru", "sk",
    "sv", "ta", "tr", "uk", "vi", "zh",
)
GROK_LANGUAGES = (
    "en", "ar-EG", "ar-SA", "ar-AE", "bn", "zh", "fr", "de", "hi", "id",
    "it", "ja", "ko", "pt-BR", "pt-PT", "ru", "es-MX", "es-ES", "tr", "vi",
)
LANGUAGE_RE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")


def normalize_language(value: str) -> str:
    value = value.strip().replace("_", "-")
    if not value or value.casefold() in {"und", "null"} or not LANGUAGE_RE.fullmatch(value):
        return ""
    parts = value.split("-")
    normalized = [parts[0].lower()]
    for part in parts[1:]:
        normalized.append(part.upper() if len(part) == 2 and part.isalpha() else part.title())
    return "-".join(normalized)


def language_matches(capability: str, requested: str) -> bool:
    capability, requested = normalize_language(capability), normalize_language(requested)
    return bool(capability and requested and
                (capability == requested or capability.split("-", 1)[0] == requested.split("-", 1)[0]))


def packet(kind: int, request_id: int, payload: bytes = b"") -> bytes:
    if len(payload) + HEADER.size > MAX_PACKET:
        raise ValueError("UtterMux packet is too large")
    return HEADER.pack(MAGIC, VERSION, kind, request_id, len(payload)) + payload


def unpack(raw: bytes) -> tuple[int, int, bytes]:
    if len(raw) < HEADER.size:
        raise ValueError("truncated UtterMux packet")
    magic, version, kind, request_id, size = HEADER.unpack_from(raw)
    if magic != MAGIC or version != VERSION or size != len(raw) - HEADER.size:
        raise ValueError("invalid UtterMux packet header")
    return kind, request_id, raw[HEADER.size:]


def fields(*values: str) -> bytes:
    return b"\0".join(v.encode("utf-8") for v in values) + b"\0"


def split_fields(payload: bytes) -> list[str]:
    values = payload.split(b"\0")
    if values and values[-1] == b"":
        values.pop()
    return [v.decode("utf-8") for v in values]


class VitsConfig(ctypes.Structure):
    _fields_ = [(n, ctypes.c_char_p) for n in ("model", "lexicon", "tokens", "data_dir")] + [
        ("noise_scale", ctypes.c_float), ("noise_scale_w", ctypes.c_float),
        ("length_scale", ctypes.c_float), ("dict_dir", ctypes.c_char_p)]


class MatchaConfig(ctypes.Structure):
    _fields_ = [(n, ctypes.c_char_p) for n in ("acoustic_model", "vocoder", "lexicon", "tokens", "data_dir")] + [
        ("noise_scale", ctypes.c_float), ("length_scale", ctypes.c_float), ("dict_dir", ctypes.c_char_p)]


class KokoroConfig(ctypes.Structure):
    _fields_ = [(n, ctypes.c_char_p) for n in ("model", "voices", "tokens", "data_dir")] + [
        ("length_scale", ctypes.c_float)] + [(n, ctypes.c_char_p) for n in ("dict_dir", "lexicon", "lang")]


class KittenConfig(ctypes.Structure):
    _fields_ = [(n, ctypes.c_char_p) for n in ("model", "voices", "tokens", "data_dir")] + [("length_scale", ctypes.c_float)]


class ZipvoiceConfig(ctypes.Structure):
    _fields_ = [(n, ctypes.c_char_p) for n in ("tokens", "encoder", "decoder", "vocoder", "data_dir", "lexicon")] + [
        (n, ctypes.c_float) for n in ("feat_scale", "t_shift", "target_rms", "guidance_scale")]


class PocketConfig(ctypes.Structure):
    _fields_ = [(n, ctypes.c_char_p) for n in ("lm_flow", "lm_main", "encoder", "decoder", "text_conditioner", "vocab_json", "token_scores_json")] + [("voice_embedding_cache_capacity", ctypes.c_int32)]


class SupertonicConfig(ctypes.Structure):
    _fields_ = [(n, ctypes.c_char_p) for n in ("duration_predictor", "text_encoder", "vector_estimator", "vocoder", "tts_json", "unicode_indexer", "voice_style")]


class ModelConfig(ctypes.Structure):
    _fields_ = [("vits", VitsConfig), ("num_threads", ctypes.c_int32), ("debug", ctypes.c_int32),
                ("provider", ctypes.c_char_p), ("matcha", MatchaConfig), ("kokoro", KokoroConfig),
                ("kitten", KittenConfig), ("zipvoice", ZipvoiceConfig), ("pocket", PocketConfig),
                ("supertonic", SupertonicConfig)]


class TtsConfig(ctypes.Structure):
    _fields_ = [("model", ModelConfig), ("rule_fsts", ctypes.c_char_p),
                ("max_num_sentences", ctypes.c_int32), ("rule_fars", ctypes.c_char_p),
                ("silence_scale", ctypes.c_float)]


class GenerationConfig(ctypes.Structure):
    _fields_ = [("silence_scale", ctypes.c_float), ("speed", ctypes.c_float), ("sid", ctypes.c_int32),
                ("reference_audio", ctypes.POINTER(ctypes.c_float)), ("reference_audio_len", ctypes.c_int32),
                ("reference_sample_rate", ctypes.c_int32), ("reference_text", ctypes.c_char_p),
                ("num_steps", ctypes.c_int32), ("extra", ctypes.c_char_p)]


class GeneratedAudio(ctypes.Structure):
    _fields_ = [("samples", ctypes.POINTER(ctypes.c_float)), ("n", ctypes.c_int32), ("sample_rate", ctypes.c_int32)]


PROGRESS = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.POINTER(ctypes.c_float), ctypes.c_int32, ctypes.c_float, ctypes.c_void_p)


class SherpaEngine:
    def __init__(self, api: ctypes.CDLL, model: dict):
        self.api, self.model, self.lock = api, model, threading.RLock()
        root = Path(model["root"])
        file_cfg = model["files"]
        self._bytes: list[bytes] = []

        def b(value: str | Path | None) -> bytes:
            encoded = str(value or "").encode()
            self._bytes.append(encoded)
            return encoded

        def path(key: str) -> bytes:
            value = file_cfg.get(key, "")
            return b(root / value if value else "")

        cfg = TtsConfig()
        cfg.model.num_threads = int(model.get("num_threads", 4))
        cfg.model.provider = b(model.get("provider", "cpu"))
        cfg.max_num_sentences = 1
        cfg.silence_scale = 0.2
        cfg.rule_fsts = b(",".join(str(root / x) for x in file_cfg.get("rule_fsts", [])))
        engine = model["engine"]
        if engine == "kokoro":
            cfg.model.kokoro = KokoroConfig(path("model"), path("voices"), path("tokens"), path("data_dir"),
                float(model.get("length_scale", 1)), b(""), path("lexicon"), b(""))
        elif engine == "kitten":
            cfg.model.kitten = KittenConfig(path("model"), path("voices"), path("tokens"), path("data_dir"), float(model.get("length_scale", 1)))
        elif engine in {"vits", "piper"}:
            cfg.model.vits = VitsConfig(path("model"), path("lexicon"), path("tokens"), path("data_dir"),
                float(model.get("noise_scale", .667)), float(model.get("noise_scale_w", .8)),
                float(model.get("length_scale", 1)), b(""))
        elif engine == "matcha":
            cfg.model.matcha = MatchaConfig(path("acoustic_model"), path("vocoder"), path("lexicon"),
                path("tokens"), path("data_dir"), float(model.get("noise_scale", .667)),
                float(model.get("length_scale", 1)), b(""))
        elif engine == "zipvoice":
            cfg.model.zipvoice = ZipvoiceConfig(path("tokens"), path("encoder"), path("decoder"),
                path("vocoder"), path("data_dir"), path("lexicon"), 0, 0, 0, 0)
        elif engine == "pocket":
            cfg.model.pocket = PocketConfig(path("lm_flow"), path("lm_main"), path("encoder"),
                path("decoder"), path("text_conditioner"), path("vocab_json"),
                path("token_scores_json"), int(model.get("voice_embedding_cache_capacity", 50)))
        elif engine == "supertonic":
            cfg.model.supertonic = SupertonicConfig(path("duration_predictor"), path("text_encoder"),
                path("vector_estimator"), path("vocoder"), path("tts_json"), path("unicode_indexer"),
                path("voice_style"))
        else:
            raise ValueError(f"unsupported sherpa engine: {engine}")
        self.handle = api.SherpaOnnxCreateOfflineTts(ctypes.byref(cfg))
        if not self.handle:
            raise RuntimeError(f"sherpa rejected model {model['id']}")
        self.sample_rate = api.SherpaOnnxOfflineTtsSampleRate(self.handle)

    def synthesize(self, text: str, speaker: int, speed: float, emit: Callable[[bytes], None],
                   cancelled: threading.Event, profile: dict | None = None) -> None:
        started = False

        @PROGRESS
        def callback(samples, count, _progress, _opaque):
            nonlocal started
            if cancelled.is_set():
                return 0
            if not started:
                emit(packet(AUDIO_START, 0, struct.pack("<IB", self.sample_rate, 1)))
                started = True
            raw = ctypes.string_at(samples, count * ctypes.sizeof(ctypes.c_float))
            max_payload = MAX_PACKET - HEADER.size
            for offset in range(0, len(raw), max_payload):
                emit(packet(AUDIO, 0, raw[offset:offset + max_payload]))
            return 0 if cancelled.is_set() else 1

        generation = GenerationConfig(silence_scale=.2, speed=speed, sid=speaker)
        reference_samples = None
        reference_text = None
        extra = None
        if profile:
            with wave.open(profile["referencePath"], "rb") as source:
                if source.getnchannels() != 1 or source.getsampwidth() != 2:
                    raise RuntimeError("clone reference must be mono PCM16 WAV")
                pcm = array("h"); pcm.frombytes(source.readframes(source.getnframes()))
                reference_samples = (ctypes.c_float * len(pcm))(*(sample / 32768.0 for sample in pcm))
                generation.reference_audio = reference_samples
                generation.reference_audio_len = len(pcm)
                generation.reference_sample_rate = source.getframerate()
            if profile.get("referenceText"):
                reference_text = profile["referenceText"].encode("utf-8")
                generation.reference_text = reference_text
            if profile.get("engine") == "pocket":
                generation.num_steps = 3
                extra = b'{"max_reference_audio_len":10.0,"chunk_size":4}'
            elif profile.get("engine") == "zipvoice":
                generation.num_steps = 4
                extra = b'{"min_char_in_sentence":10}'
            generation.extra = extra
        with self.lock:
            audio = self.api.SherpaOnnxOfflineTtsGenerateWithConfig(
                self.handle, text.encode("utf-8"), ctypes.byref(generation), callback, None)
            if audio:
                try:
                    # Some engines (notably Pocket in sherpa-onnx 1.13.x)
                    # return complete audio but never invoke the progress
                    # callback. Forward that result only when no incremental
                    # samples were emitted, otherwise it would repeat speech.
                    result = audio.contents
                    if not started and result.samples and result.n > 0 and not cancelled.is_set():
                        emit(packet(AUDIO_START, 0, struct.pack("<IB", result.sample_rate, 1)))
                        started = True
                        raw = ctypes.string_at(result.samples, result.n * ctypes.sizeof(ctypes.c_float))
                        max_payload = MAX_PACKET - HEADER.size
                        for offset in range(0, len(raw), max_payload):
                            if cancelled.is_set(): break
                            emit(packet(AUDIO, 0, raw[offset:offset + max_payload]))
                finally:
                    self.api.SherpaOnnxDestroyOfflineTtsGeneratedAudio(audio)
        if not started and not cancelled.is_set():
            emit(packet(AUDIO_START, 0, struct.pack("<IB", self.sample_rate, 1)))

    def close(self) -> None:
        with self.lock:
            if self.handle:
                self.api.SherpaOnnxDestroyOfflineTts(self.handle)
                self.handle = None


def load_config() -> dict:
    root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "uttermux"
    path = Path(os.environ.get("UTTERMUX_CONFIG", root / "config.toml"))
    if not path.is_file():
        return {"fallback_voice": "sherpa/vits-piper-en_US-lessac-medium/lessac", "providers": {}}
    with path.open("rb") as stream:
        return tomllib.load(stream)


def import_edge_tts():
    vendor = Path(__file__).resolve().parent / "vendor"
    if vendor.is_dir():
        sys.path.insert(0, str(vendor))
    try:
        import edge_tts
        return edge_tts
    except ImportError:
        # Development migration path only; packaged UtterMux carries its own
        # pinned edge-tts module and never relies on this KOReader environment.
        roots = Path.home().glob(".local/share/koreader-tts-server/venv/lib/python*/site-packages")
        for root in roots:
            sys.path.insert(0, str(root))
            try:
                import edge_tts
                return edge_tts
            except ImportError:
                continue
        raise


class EdgeProvider:
    def __init__(self, config: dict):
        self.config = config
        self.edge_tts = import_edge_tts()
        self.exposed_locales = set(config.get("locales", ["en-US", "en-GB"]))
        self._voices: dict[str, dict] = {}
        for voice in asyncio.run(self.edge_tts.list_voices()):
            if voice.get("Locale") and voice.get("ShortName"):
                self._voices[voice["ShortName"]] = voice

    def voices(self):
        for short_name, voice in sorted(self._voices.items()):
            gender = voice.get("Gender", "")
            label = f"{short_name} · Edge" + (f" ({gender})" if gender else "")
            locale = normalize_language(voice["Locale"])
            yield (f"edge/{short_name}", label, locale, "edge", "Edge", (locale,),
                   locale in self.exposed_locales)

    def synthesize(self, voice_id: str, text: str, speed: float, emit,
                   cancelled: threading.Event, _language: str = ""):
        short_name = voice_id.removeprefix("edge/")
        if short_name not in self._voices:
            raise ValueError(f"unknown Edge voice: {voice_id}")

        async def generate():
            process = subprocess.Popen([
                "ffmpeg", "-nostdin", "-loglevel", "error", "-f", "mp3", "-i", "pipe:0",
                "-f", "s16le", "-acodec", "pcm_s16le", "-ar", "24000", "-ac", "1", "pipe:1"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            started = threading.Event()

            def decode():
                try:
                    while not cancelled.is_set():
                        data = process.stdout.read(32768)
                        if not data:
                            return
                        if not started.is_set():
                            emit(packet(AUDIO_START, 0, struct.pack("<IB", 24000, 2)))
                            started.set()
                        emit(packet(AUDIO, 0, data))
                finally:
                    if cancelled.is_set() and process.poll() is None:
                        process.terminate()

            reader = threading.Thread(target=decode, daemon=True); reader.start()
            communicate = self.edge_tts.Communicate(
                text, short_name, rate=f"{(speed - 1) * 100:+.0f}%")
            try:
                async for chunk in communicate.stream():
                    if cancelled.is_set():
                        break
                    if chunk["type"] == "audio":
                        process.stdin.write(chunk["data"]); process.stdin.flush()
            finally:
                process.stdin.close(); reader.join(timeout=10)
                if process.poll() is None: process.terminate()
                return_code = process.wait(timeout=5)
                if return_code and not cancelled.is_set():
                    details = process.stderr.read().decode("utf-8", "replace").strip()
                    raise RuntimeError(f"Edge audio decoder failed: {details or return_code}")
            if not started.is_set() and not cancelled.is_set():
                raise RuntimeError("Edge returned no audio")

        asyncio.run(generate())


class ElevenLabsProvider:
    def __init__(self, config: dict):
        self.config = config
        credential = Path(config.get("credential_file", Path.home() / ".config/uttermux/credentials/elevenlabs-api-key"))
        self.api_key = credential.read_text(encoding="utf-8").strip()
        if not self.api_key:
            raise RuntimeError("ElevenLabs credential is empty")
        self.model = config.get("model", "eleven_flash_v2_5")
        defaults = (ELEVEN_FLASH_LANGUAGES
                    if self.model in {"eleven_flash_v2_5", "eleven_turbo_v2_5"} else ("en",))
        self.languages = tuple(normalize_language(x) for x in config.get("languages", defaults))
        self._voices = {voice["id"]: voice for voice in config.get("voices", [])}

    def voices(self):
        for voice_id, voice in self._voices.items():
            yield (f"elevenlabs/{voice_id}", f"{voice['name']} · ElevenLabs",
                   normalize_language(voice["language"]), "elevenlabs", self.model,
                   self.languages, True)

    def synthesize(self, voice_id: str, text: str, speed: float, emit,
                   cancelled: threading.Event, language: str = ""):
        external_id = voice_id.removeprefix("elevenlabs/")
        if external_id not in self._voices:
            raise ValueError(f"unknown ElevenLabs voice: {voice_id}")
        url = (f"https://api.elevenlabs.io/v1/text-to-speech/{external_id}/stream"
               "?output_format=pcm_24000")
        request_body = {"text": text, "model_id": self.model,
                        "voice_settings": {"speed": max(.7, min(1.2, speed))}}
        if language:
            request_body["language_code"] = language.split("-", 1)[0]
        body = json.dumps(request_body).encode()
        request = urllib.request.Request(url, data=body, method="POST", headers={
            "xi-api-key": self.api_key, "Content-Type": "application/json", "Accept": "audio/pcm"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                started = False
                while not cancelled.is_set():
                    chunk = response.read(32768)
                    if not chunk:
                        break
                    if not started:
                        emit(packet(AUDIO_START, 0, struct.pack("<IB", 24000, 2))); started = True
                    emit(packet(AUDIO, 0, chunk))
                if not started and not cancelled.is_set():
                    raise RuntimeError("ElevenLabs returned no audio")
        except urllib.error.HTTPError as error:
            detail = error.read(2048).decode("utf-8", "replace")
            raise RuntimeError(f"ElevenLabs HTTP {error.code}: {detail}") from None


class GrokProvider:
    """xAI's multilingual text-to-speech API."""

    def __init__(self, config: dict):
        self.config = config
        credential = Path(config.get(
            "credential_file", Path.home() / ".config/uttermux/credentials/grok-key"))
        self.api_key = credential.read_text(encoding="utf-8").strip()
        if not self.api_key:
            raise RuntimeError("Grok credential is empty")
        self.automatic_language = bool(config.get("automatic_language", True))
        request = urllib.request.Request("https://api.x.ai/v1/tts/voices", headers={
            "Authorization": f"Bearer {self.api_key}", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                document = json.load(response)
        except urllib.error.HTTPError as error:
            detail = error.read(2048).decode("utf-8", "replace")
            raise RuntimeError(f"Grok HTTP {error.code}: {detail}") from None


        voices = document.get("voices", document if isinstance(document, list) else [])
        self._voices = {}
        for voice in voices:
            voice_id = voice.get("voice_id") or voice.get("id")
            if voice_id:
                self._voices[voice_id] = voice

    def voices(self):
        languages = tuple(normalize_language(value) for value in GROK_LANGUAGES)
        for voice_id, voice in sorted(self._voices.items()):
            name = voice.get("name") or voice_id.title()
            gender = voice.get("gender", "")
            label = f"{name} · Grok" + (f" ({gender.title()})" if gender else "")
            yield (f"grok/{voice_id}", label, "en-US", "grok", "xAI TTS",
                   languages, True)

    def synthesize(self, voice_id: str, text: str, speed: float, emit,
                   cancelled: threading.Event, language: str = ""):
        external_id = voice_id.removeprefix("grok/")
        if external_id not in self._voices:
            raise ValueError(f"unknown Grok voice: {voice_id}")
        request_body = {
            "text": text, "voice_id": external_id,
            "language": "auto" if self.automatic_language else (language or "en"),
            "output_format": {"codec": "pcm", "sample_rate": 24000},
            "speed": max(.7, min(1.5, speed)), "text_normalization": True,
        }
        request = urllib.request.Request(
            "https://api.x.ai/v1/tts", data=json.dumps(request_body).encode(), method="POST",
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json", "Accept": "audio/pcm"})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                started = False
                while not cancelled.is_set():
                    chunk = response.read(32768)
                    if not chunk:
                        break
                    if not started:
                        emit(packet(AUDIO_START, 0, struct.pack("<IB", 24000, 2))); started = True
                    emit(packet(AUDIO, 0, chunk))
                if not started and not cancelled.is_set():
                    raise RuntimeError("Grok returned no audio")
        except urllib.error.HTTPError as error:
            detail = error.read(2048).decode("utf-8", "replace")
            raise RuntimeError(f"Grok HTTP {error.code}: {detail}") from None


class QwenProvider:
    """Persistent local Qwen3-TTS companion using its streaming HTTP API."""

    SPEAKERS = ("ryan", "serena", "vivian", "uncle_fu", "aiden", "ono_anna",
                "sohee", "eric", "dylan")
    LANGUAGES = ("en", "zh", "ja", "ko", "de", "fr", "ru", "pt", "es", "it")
    LANGUAGE_NAMES = {"en": "English", "zh": "Chinese", "ja": "Japanese",
                      "ko": "Korean", "de": "German", "fr": "French",
                      "ru": "Russian", "pt": "Portuguese", "es": "Spanish",
                      "it": "Italian"}

    def __init__(self, config: dict):
        data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "uttermux"
        self.binary = Path(config.get("binary", Path.home() / ".local/lib/uttermux/qwen3-tts/qwen_tts"))
        self.model = Path(config.get("model_dir", data / "models/qwen3-tts-0.6b"))
        if not self.binary.is_file() or not os.access(self.binary, os.X_OK):
            raise RuntimeError(f"Qwen runtime is not installed: {self.binary}")
        if not (self.model / "model.safetensors").is_file():
            raise RuntimeError(f"Qwen model is not installed: {self.model}")
        self.port = int(config.get("port", 17872))
        self.threads = max(1, int(config.get("threads", 4)))
        self.quantization = str(config.get("quantization", "int8"))
        self.process: subprocess.Popen | None = None
        self.lock = threading.Lock()

    def voices(self):
        capabilities = tuple(normalize_language(item) for item in self.LANGUAGES)
        for speaker in self.SPEAKERS:
            yield (f"qwen/{speaker}", f"{speaker.replace('_', ' ').title()} · Qwen3-TTS",
                   "en-US", "qwen", "Qwen3-TTS 0.6B", capabilities, True)

    def _health(self) -> bool:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/v1/health", timeout=.5) as response:
                return response.status == 200
        except (OSError, urllib.error.URLError):
            return False

    def _ensure_server(self, cancelled: threading.Event) -> None:
        with self.lock:
            if self.process and self.process.poll() is None and self._health():
                return
            if self.process and self.process.poll() is None:
                self.process.terminate()
            command = [str(self.binary), "-d", str(self.model), "-j", str(self.threads)]
            if self.quantization in {"int8", "int4"}:
                command.append(f"--{self.quantization}")
            command += ["--serve", str(self.port)]
            self.process = subprocess.Popen(command, cwd=self.binary.parent,
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline and not cancelled.is_set():
                if self.process.poll() is not None:
                    raise RuntimeError(f"Qwen server failed to start (exit {self.process.returncode})")
                if self._health():
                    return
                time.sleep(.25)
            if cancelled.is_set():
                self.process.terminate()
                raise RuntimeError("Qwen startup cancelled")
            raise RuntimeError("Qwen model did not become ready within 180 seconds")

    def synthesize(self, voice_id: str, text: str, speed: float, emit,
                   cancelled: threading.Event, language: str = ""):
        self._ensure_server(cancelled)
        speaker = voice_id.removeprefix("qwen/")
        if speaker not in self.SPEAKERS:
            raise ValueError(f"unknown Qwen voice: {voice_id}")
        code = normalize_language(language).split("-", 1)[0]
        body = {"text": text, "speaker": speaker,
                "language": self.LANGUAGE_NAMES.get(code, "English"),
                "rate": max(.5, min(2.0, speed))}
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}/v1/tts/stream",
            data=json.dumps(body).encode(), method="POST", headers={"Content-Type": "application/json"})
        started = False
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                while not cancelled.is_set():
                    chunk = response.read(32768)
                    if not chunk:
                        break
                    if not started:
                        emit(packet(AUDIO_START, 0, struct.pack("<IB", 24000, 2))); started = True
                    emit(packet(AUDIO, 0, chunk))
        except urllib.error.HTTPError as error:
            detail = error.read(2048).decode("utf-8", "replace")
            raise RuntimeError(f"Qwen HTTP {error.code}: {detail}") from None
        finally:
            # The companion is single-request. Closing the HTTP response does
            # not interrupt its inference loop, so kill it on cancellation to
            # make Speech Dispatcher's stop operation immediate.
            if cancelled.is_set():
                with self.lock:
                    if self.process and self.process.poll() is None:
                        self.process.terminate()
        if not started and not cancelled.is_set():
            raise RuntimeError("Qwen returned no audio")


class Broker:
    def __init__(self):
        self.config = load_config()
        self.api = ctypes.CDLL("libsherpa-onnx-c-api.so")
        self.api.SherpaOnnxCreateOfflineTts.argtypes = [ctypes.POINTER(TtsConfig)]
        self.api.SherpaOnnxCreateOfflineTts.restype = ctypes.c_void_p
        self.api.SherpaOnnxDestroyOfflineTts.argtypes = [ctypes.c_void_p]
        self.api.SherpaOnnxOfflineTtsSampleRate.argtypes = [ctypes.c_void_p]
        self.api.SherpaOnnxOfflineTtsSampleRate.restype = ctypes.c_int32
        self.api.SherpaOnnxOfflineTtsGenerateWithConfig.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(GenerationConfig), PROGRESS, ctypes.c_void_p]
        self.api.SherpaOnnxOfflineTtsGenerateWithConfig.restype = ctypes.POINTER(GeneratedAudio)
        self.api.SherpaOnnxDestroyOfflineTtsGeneratedAudio.argtypes = [ctypes.POINTER(GeneratedAudio)]
        self.models, self.voices, self.voice_meta = self._load_models(), {}, {}
        self.engines: OrderedDict[str, SherpaEngine] = OrderedDict()
        self.engine_lock = threading.Lock()
        self.max_loaded_models = max(1, int(self.config.get("max_loaded_models", 2)))
        self.online_voices, self.online_providers = {}, {}
        self.audio_cache: OrderedDict[tuple[str, str, str, float], list[bytes]] = OrderedDict()
        self.audio_cache_bytes = 0
        self.audio_cache_limit = max(0, int(self.config.get("audio_cache_mb", 64))) * 1024 * 1024
        self.audio_cache_lock = threading.Lock()
        self.runtime_lock = threading.Lock()
        self.runtime = {"status": "idle", "activeVoice": "", "routedVoice": "",
                        "language": "", "fallbackReason": ""}
        for model in self.models.values():
            for voice in model.get("voice", []):
                voice_id = f"sherpa/{model['id']}/{voice['id']}"
                self.voices[voice_id] = (model, voice)
                native = normalize_language(voice["language"])
                capabilities = tuple(normalize_language(x) for x in
                    voice.get("languages", model.get("languages", [native])))
                self.voice_meta[voice_id] = (voice_id, f"{voice['name']} · Local", native,
                    "local", model["id"], capabilities, True)
        self.profiles = {}
        for voice_id, (model, voice) in self.voices.items():
            if voice.get("reference_file"):
                self.profiles[voice_id] = {
                    "id": voice["id"], "voiceId": voice_id, "name": voice["name"],
                    "language": normalize_language(voice["language"]), "engine": model["engine"],
                    "modelVersion": model["id"],
                    "referencePath": str(Path(model["root"]) / voice["reference_file"]),
                    "referenceText": voice.get("reference_text", ""),
                }
        if uttermux_profiles:
            for profile in uttermux_profiles.profiles():
                model = self.models.get(profile.get("modelVersion", ""))
                if not model or not profile.get("referencePath"):
                    continue
                voice_id = profile["voiceId"]
                voice = {"id": f"custom-{profile['id']}", "name": profile["name"],
                         "language": profile["language"], "speaker_id": 0}
                self.voices[voice_id] = (model, voice)
                self.profiles[voice_id] = profile
                self.voice_meta[voice_id] = (voice_id, f"{profile['name']} · {profile['engine'].title()}",
                    profile["language"], "local", model["id"], (profile["language"],), True)
        provider_config = self.config.get("providers", {})
        for provider_name, provider_type in (("edge", EdgeProvider), ("elevenlabs", ElevenLabsProvider),
                                             ("grok", GrokProvider), ("qwen", QwenProvider)):
            cfg = provider_config.get(provider_name, {})
            if not cfg.get("enabled", provider_name == "qwen"):
                continue
            try:
                provider = provider_type(cfg)
                self.online_providers[provider_name] = provider
                for record in provider.voices():
                    self.online_voices[record[0]] = (provider, record)
                    self.voice_meta[record[0]] = record
            except Exception as error:
                print(f"uttermuxd: {provider_name} unavailable: {error}", file=sys.stderr)

    @staticmethod
    def _manifest_dirs() -> list[Path]:
        config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        return [config / "uttermux/models.d", config / "speech-dispatcher-sherpa/models.d"]

    def _load_models(self) -> dict[str, dict]:
        models: dict[str, dict] = {}
        for directory in self._manifest_dirs():
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.toml")):
                with path.open("rb") as stream:
                    model = tomllib.load(stream)
                model.setdefault("files", {})
                models[model["id"]] = model
        return models

    def engine(self, model: dict) -> SherpaEngine:
        model_id = model["id"]
        with self.engine_lock:
            if model_id in self.engines:
                self.engines.move_to_end(model_id)
                engine = self.engines[model_id]
                engine.lock.acquire()
                return engine
            engine = SherpaEngine(self.api, model)
            self.engines[model_id] = engine
            while len(self.engines) > self.max_loaded_models:
                _, evicted = self.engines.popitem(last=False)
                evicted.close()
            engine.lock.acquire()
            return engine

    def synthesize_local(self, model, voice, text, speed, emit, cancelled, profile=None):
        engine = self.engine(model)
        try:
            engine.synthesize(text, int(voice.get("speaker_id", 0)), speed, emit, cancelled, profile)
        finally:
            engine.lock.release()

    def list_voices(self, management: bool = False):
        fallback = self.config.get("default_voice", self.config.get("fallback_voice", ""))
        records = sorted(self.voice_meta.values(), key=lambda record: record[0] != fallback)
        for voice_id, name, native, provider, model, capabilities, exposed in records:
            if management or exposed:
                yield voice_id, name, native, provider, model, ",".join(capabilities)

    def _supports(self, voice_id: str, language: str) -> bool:
        return voice_id in self.voice_meta and any(
            language_matches(capability, language) for capability in self.voice_meta[voice_id][5])

    def _detect_language(self, text: str) -> str:
        routing = self.config.get("routing", {})
        if not routing.get("auto_detect", True):
            return ""
        if sum(character.isalpha() for character in text) < int(routing.get("minimum_characters", 40)):
            return ""
        try:
            data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
            for vendor in (Path(__file__).resolve().parent / "vendor", data_home / "uttermux/vendor"):
                if vendor.is_dir() and str(vendor) not in sys.path:
                    sys.path.insert(0, str(vendor))
            from py3langid.langid import LanguageIdentifier, MODEL_FILE
            if not hasattr(self, "_language_detector"):
                self._language_detector = LanguageIdentifier.from_pickled_model(MODEL_FILE, norm_probs=True)
            language, confidence = self._language_detector.classify(text)
            if float(confidence) >= float(routing.get("minimum_confidence", .8)):
                return normalize_language(language)
        except (ImportError, OSError, ValueError) as error:
            print(f"uttermuxd: language detection unavailable: {error}", file=sys.stderr)
        return ""

    def _route(self, requested_voice: str, requested_language: str, text: str) -> tuple[str, list[str]]:
        routing = self.config.get("routing", {})
        language = normalize_language(requested_language) or self._detect_language(text)
        if not language:
            language = normalize_language(routing.get("default_language", "en-US")) or "en-US"
        persona = requested_voice or self.config.get("default_voice", "")
        candidates: list[str] = []

        def add(voice_id: str, require_compatible: bool = True):
            if (voice_id in self.voice_meta and voice_id not in candidates and
                    (not require_compatible or self._supports(voice_id, language))):
                candidates.append(voice_id)

        add(persona)
        route_table = routing.get("voices", {})
        for key in (language, language.split("-", 1)[0]):
            route = route_table.get(key, [])
            if isinstance(route, str):
                route = [route]
            for voice_id in route:
                add(voice_id)
        attempted_providers = {self.voice_meta[item][3] for item in candidates}
        for provider in routing.get("provider_order", ["elevenlabs", "grok", "edge", "local"]):
            if provider in attempted_providers:
                continue
            provider_config = self.config.get("providers", {}).get(provider, {})
            preferred = provider_config.get("default_voice", "")
            if preferred and "/" not in preferred:
                preferred = f"{provider}/{preferred}"
            add(preferred)
            for voice_id, metadata in self.voice_meta.items():
                if metadata[3] == provider and not any(
                        self.voice_meta[item][3] == provider for item in candidates):
                    add(voice_id)
        if routing.get("cross_language_fallback", True):
            add(self.config.get("fallback_voice", ""), require_compatible=False)
        return language, candidates

    def _synthesize_voice(self, voice_id: str, text: str, speed: float,
                          language: str, emit, cancelled):
        if voice_id in self.voices:
            model, voice = self.voices[voice_id]
            self.synthesize_local(model, voice, text, speed, emit, cancelled,
                                  self.profiles.get(voice_id))
            return
        if voice_id not in self.online_voices:
            raise ValueError(f"unknown voice: {voice_id}")
        provider, _record = self.online_voices[voice_id]
        cache_key = (voice_id, language, text, round(speed, 3))
        with self.audio_cache_lock:
            cached = self.audio_cache.get(cache_key)
            if cached:
                self.audio_cache.move_to_end(cache_key)
        if cached:
            for raw in cached:
                if cancelled.is_set(): break
                emit(raw)
            return
        emitted_audio = False
        captured: list[bytes] = []

        def tracked(raw: bytes):
            nonlocal emitted_audio
            _magic, _version, kind, _rid, _size = HEADER.unpack_from(raw)
            emitted_audio = emitted_audio or kind in (AUDIO_START, AUDIO)
            if kind in (AUDIO_START, AUDIO): captured.append(raw)
            emit(raw)

        try:
            provider.synthesize(voice_id, text, speed, tracked, cancelled, language)
            if captured and not cancelled.is_set() and self.audio_cache_limit:
                size = sum(map(len, captured))
                if size <= self.audio_cache_limit:
                    with self.audio_cache_lock:
                        self.audio_cache[cache_key] = captured
                        self.audio_cache_bytes += size
                        while self.audio_cache_bytes > self.audio_cache_limit and self.audio_cache:
                            _, removed = self.audio_cache.popitem(last=False)
                            self.audio_cache_bytes -= sum(map(len, removed))
        except Exception:
            if emitted_audio:
                raise
            raise

    def synthesize(self, voice_id: str, text: str, speed: float, emit, cancelled,
                   requested_language: str = ""):
        language, candidates = self._route(voice_id, requested_language, text)
        if not candidates:
            raise RuntimeError(f"no route for language {language}")
        with self.runtime_lock:
            self.runtime.update(status="warming", activeVoice="", routedVoice=candidates[0],
                                language=language, fallbackReason="")
        last_error = None
        try:
            for index, candidate in enumerate(candidates):
                emitted = False
                with self.runtime_lock:
                    self.runtime.update(status="warming", activeVoice="", routedVoice=candidate,
                        fallbackReason="" if index == 0 else str(last_error or "previous route unavailable"))

                def tracked(raw: bytes):
                    nonlocal emitted
                    _magic, _version, kind, _rid, _size = HEADER.unpack_from(raw)
                    emitted = emitted or kind in (AUDIO_START, AUDIO)
                    if kind == AUDIO_START:
                        with self.runtime_lock:
                            self.runtime.update(status="speaking", activeVoice=candidate)
                    emit(raw)

                try:
                    self._synthesize_voice(candidate, text, speed, language, tracked, cancelled)
                    return
                except Exception as error:
                    last_error = error
                    if emitted or cancelled.is_set(): raise
                    print(f"uttermuxd: route {candidate} failed before audio: {error}", file=sys.stderr)
            raise RuntimeError(f"all routes for {language} failed: {last_error}")
        finally:
            with self.runtime_lock:
                self.runtime["status"] = "stopped" if cancelled.is_set() else "idle"
                self.runtime["activeVoice"] = ""

    def status(self) -> dict:
        with self.runtime_lock: return dict(self.runtime)


def client_loop(connection: socket.socket, broker: Broker) -> None:
    send_lock, jobs = threading.Lock(), {}

    def send(raw: bytes, request_id: int | None = None) -> None:
        if request_id is not None:
            magic, version, kind, _old, size = HEADER.unpack_from(raw)
            raw = HEADER.pack(magic, version, kind, request_id, size) + raw[HEADER.size:]
        with send_lock:
            connection.sendall(raw)

    try:
        while True:
            raw = connection.recv(MAX_PACKET)
            if not raw:
                break
            kind, request_id, payload = unpack(raw)
            if kind in (HELLO, HEALTH):
                send(packet(DONE, request_id, fields("uttermuxd", "1")))
            elif kind == LIST_VOICES:
                purpose = split_fields(payload)
                for voice in broker.list_voices(management=bool(purpose and purpose[0] == "management")):
                    send(packet(VOICE, request_id, fields(*voice)))
                send(packet(DONE, request_id))
            elif kind == STATUS:
                send(packet(STATE, request_id, json.dumps(broker.status()).encode("utf-8")))
                send(packet(DONE, request_id))
            elif kind == CANCEL:
                if request_id in jobs:
                    jobs[request_id].set()
            elif kind == SYNTHESIZE:
                values = split_fields(payload)
                if len(values) not in (3, 4):
                    send(packet(ERROR, request_id, b"invalid synthesis request"))
                    continue
                voice_id, speed_text, text = values[:3]
                language = values[3] if len(values) == 4 else ""
                cancelled = jobs[request_id] = threading.Event()

                def run(rid=request_id, voice=voice_id, content=text, speed=float(speed_text),
                        event=cancelled, requested_language=language):
                    try:
                        broker.synthesize(voice, content, speed, lambda raw: send(raw, rid), event,
                                          requested_language)
                        send(packet(DONE, rid))
                    except Exception as error:
                        send(packet(ERROR, rid, str(error).encode("utf-8", "replace")))
                    finally:
                        jobs.pop(rid, None)

                threading.Thread(target=run, daemon=True).start()
            else:
                send(packet(ERROR, request_id, b"unsupported message"))
    except (BrokenPipeError, ConnectionResetError):
        pass
    finally:
        for event in list(jobs.values()):
            event.set()
        connection.close()


def listening_socket(path: Path) -> socket.socket:
    listen_fds = int(os.environ.get("LISTEN_FDS", "0"))
    listen_pid = int(os.environ.get("LISTEN_PID", "0"))
    if listen_fds >= 1 and listen_pid == os.getpid():
        return socket.socket(fileno=3)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    server = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    server.bind(str(path)); os.chmod(path, 0o600); server.listen(16)
    return server


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", type=Path, default=Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "uttermux.sock")
    args = parser.parse_args()
    broker = Broker()
    server = listening_socket(args.socket)
    while True:
        connection, _ = server.accept()
        threading.Thread(target=client_loop, args=(connection, broker), daemon=True).start()


if __name__ == "__main__":
    raise SystemExit(main())
