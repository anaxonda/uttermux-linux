"""HTTP cloud TTS providers shared by the Linux broker.

Provider-specific authentication and request bodies live here; lifecycle,
routing, caching, and Speech Dispatcher framing remain in uttermuxd.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import html
import json
from pathlib import Path
import subprocess
import struct
import urllib.error
import urllib.parse
import urllib.request

MAGIC = 0x58544D55
HEADER = struct.Struct("<IHHQI")
AUDIO_START, AUDIO = 5, 6


def packet(kind, request_id, payload=b""):
    return HEADER.pack(MAGIC, 1, kind, request_id, len(payload)) + payload


def request(url, *, data=None, headers=None, method=None, timeout=60):
    body = None if data is None else (data if isinstance(data, bytes) else json.dumps(data).encode())
    values = {"Accept": "application/json", **(headers or {})}
    if body is not None and not any(key.lower() == "content-type" for key in values):
        values["Content-Type"] = "application/json"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=body, headers=values,
                                                          method=method), timeout=timeout) as response:
            return response.read(), response.headers.get_content_type()
    except urllib.error.HTTPError as error:
        detail = error.read(4096).decode("utf-8", "replace")
        raise RuntimeError(f"cloud TTS HTTP {error.code}: {detail}") from None


def decoded_pcm(data: bytes, input_format: str = "") -> bytes:
    command = ["ffmpeg", "-nostdin", "-loglevel", "error"]
    if input_format:
        command += ["-f", input_format]
    command += ["-i", "pipe:0", "-f", "s16le", "-acodec", "pcm_s16le",
                "-ar", "24000", "-ac", "1", "pipe:1"]
    result = subprocess.run(command, input=data, capture_output=True)
    if result.returncode or not result.stdout:
        raise RuntimeError("cloud audio decode failed: " + result.stderr.decode("utf-8", "replace")[:1000])
    return result.stdout


def voice(identifier, name, language, provider, model, languages=None):
    return (f"{provider}/{identifier}", f"{name} · {provider_title(provider)}", language,
            provider, model, tuple(languages or (language,)), True)


def provider_title(provider):
    return {"openai": "OpenAI", "azure": "Azure", "qwen-api": "Qwen",
            "google": "Google", "aws": "Polly", "deepgram": "Deepgram",
            "cartesia": "Cartesia", "playht": "PlayHT", "resemble": "Resemble",
            "custom": "Custom"}.get(provider, provider.title())


class Provider:
    id = ""
    model = ""
    defaults = ()

    def __init__(self, config):
        self.config = config
        self._voices = list(self.defaults)
        self.refresh()

    def value(self, key, default=""):
        return str(self.config.get(key, default)).strip()

    def require(self, key):
        value = self.value(key)
        if not value:
            raise RuntimeError(f"{provider_title(self.id)} {key.replace('_', ' ')} is not configured")
        return value

    def voices(self):
        return tuple(self._voices)

    def refresh(self):
        pass

    def external(self, voice_id):
        return voice_id.removeprefix(self.id + "/").split("@", 1)[0]

    def emit(self, data, emit, cancelled, *, encoded=False, input_format="", sample_rate=24000):
        if cancelled.is_set():
            return
        pcm = decoded_pcm(data, input_format) if encoded else data
        if not pcm:
            raise RuntimeError(f"{provider_title(self.id)} returned no audio")
        emit(packet(AUDIO_START, 0, struct.pack("<IB", sample_rate, 2)))
        for offset in range(0, len(pcm), 32768):
            if cancelled.is_set():
                return
            emit(packet(AUDIO, 0, pcm[offset:offset + 32768]))


class OpenAiProvider(Provider):
    id, model = "openai", "gpt-4o-mini-tts"
    defaults = tuple(voice(name, name.title(), "en-US", "openai", "gpt-4o-mini-tts",
                           ("en", "fr", "de", "es", "it", "pt", "ja", "ko", "zh"))
                     for name in ("alloy", "ash", "ballad", "coral", "echo", "fable",
                                  "nova", "onyx", "sage", "shimmer", "verse"))

    def synthesize(self, voice_id, text, speed, emit, cancelled, language=""):
        endpoint = self.value("endpoint", "https://api.openai.com").rstrip("/")
        body = {"model": self.value("model", self.model), "voice": self.external(voice_id),
                "input": text, "response_format": "pcm", "speed": max(.25, min(4, speed))}
        data, _ = request(endpoint + "/v1/audio/speech", data=body,
                          headers={"Authorization": "Bearer " + self.require("api_key"),
                                   "Accept": "audio/pcm"})
        self.emit(data, emit, cancelled)


class AzureProvider(Provider):
    id, model = "azure", "Azure Neural"
    defaults = (voice("en-US-JennyNeural@en-US", "Jenny", "en-US", id, model),
                voice("fr-FR-DeniseNeural@fr-FR", "Denise", "fr-FR", id, model))

    def base(self):
        return self.value("endpoint") or f"https://{self.require('region')}.tts.speech.microsoft.com"

    def refresh(self):
        if not self.value("api_key") or not self.value("region"):
            return
        data, _ = request(self.base().rstrip("/") + "/cognitiveservices/voices/list",
                          headers={"Ocp-Apim-Subscription-Key": self.value("api_key")})
        found = []
        for item in json.loads(data):
            locale, name = item.get("Locale", "en-US"), item.get("ShortName", "")
            if name:
                found.append(voice(f"{name}@{locale}", name.removesuffix("Neural").split("-")[-1],
                                   locale, self.id, self.model))
        if found:
            self._voices = found

    def synthesize(self, voice_id, text, speed, emit, cancelled, language=""):
        name = self.external(voice_id); rate = round((speed - 1) * 100)
        ssml = (f"<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' "
                f"xml:lang='{html.escape(language or 'en-US')}'><voice name='{html.escape(name)}'>"
                f"<prosody rate='{rate:+d}%'>{html.escape(text)}</prosody></voice></speak>").encode()
        data, _ = request(self.base().rstrip("/") + "/cognitiveservices/v1", data=ssml, method="POST",
                          headers={"Ocp-Apim-Subscription-Key": self.require("api_key"),
                                   "Content-Type": "application/ssml+xml",
                                   "X-Microsoft-OutputFormat": "raw-24khz-16bit-mono-pcm",
                                   "User-Agent": "UtterMux", "Accept": "audio/pcm"})
        self.emit(data, emit, cancelled)


class GoogleProvider(Provider):
    id, model = "google", "Google Cloud TTS"
    defaults = (voice("en-US-Chirp3-HD-Charon@en-US", "Charon", "en-US", id, "Chirp 3 HD"),
                voice("fr-FR-Chirp3-HD-Aoede@fr-FR", "Aoede", "fr-FR", id, "Chirp 3 HD"))

    def endpoint(self, path):
        if self.value("auth_mode", "direct") == "proxy":
            return self.require("proxy").rstrip("/") + path, self.proxy_headers()
        key = urllib.parse.quote(self.require("api_key"), safe="")
        separator = "&" if "?" in path else "?"
        return "https://texttospeech.googleapis.com" + path + separator + "key=" + key, {}

    def proxy_headers(self):
        return {"Authorization": "Bearer " + self.value("token")} if self.value("token") else {}

    def refresh(self):
        try:
            url, headers = self.endpoint("/v1/voices")
        except RuntimeError:
            return
        data, _ = request(url, headers=headers)
        root = json.loads(data); items = root.get("voices", root if isinstance(root, list) else [])
        found = []
        for item in items:
            name = item.get("name") or item.get("id", "")
            for locale in item.get("languageCodes", [item.get("language", "en-US")]):
                if name:
                    found.append(voice(f"{name}@{locale}", name, locale, self.id, "Google Cloud TTS"))
        if found:
            self._voices = found

    def synthesize(self, voice_id, text, speed, emit, cancelled, language=""):
        if self.value("auth_mode", "direct") == "proxy":
            url, headers = self.endpoint("/v1/synthesize")
            data, _ = request(url, data={"text": text, "voice": self.external(voice_id),
                                        "language": language, "speed": speed}, headers=headers)
            self.emit(data, emit, cancelled); return
        url, headers = self.endpoint("/v1/text:synthesize")
        body = {"input": {"text": text}, "voice": {"name": self.external(voice_id),
                "languageCode": language or "en-US"}, "audioConfig": {"audioEncoding": "LINEAR16",
                "sampleRateHertz": 24000, "speakingRate": max(.25, min(2, speed))}}
        data, _ = request(url, data=body, headers=headers)
        self.emit(base64.b64decode(json.loads(data)["audioContent"]), emit, cancelled, encoded=True)


class AwsProvider(Provider):
    id, model = "aws", "Polly neural"
    defaults = (voice("Joanna/neural@en-US", "Joanna", "en-US", id, model),
                voice("Amy/neural@en-GB", "Amy", "en-GB", id, model),
                voice("Lea/neural@fr-FR", "Lea", "fr-FR", id, model))

    def __init__(self, config):
        self.temporary = None
        super().__init__(config)

    def region(self): return self.value("region", "us-east-1")
    def mode(self): return self.value("auth_mode", "direct")
    def credentials(self):
        if self.mode() != "cognito":
            return self.require("access_key"), self.require("secret_key"), ""
        now = datetime.datetime.now(datetime.timezone.utc).timestamp()
        if self.temporary and self.temporary[3] - now > 60:
            return self.temporary[:3]
        endpoint = f"https://cognito-identity.{self.region()}.amazonaws.com/"
        def call(target, body):
            data, _ = request(endpoint, data=body, headers={"X-Amz-Target": "AWSCognitoIdentityService." + target,
                              "Content-Type": "application/x-amz-json-1.1"})
            return json.loads(data)
        identity = call("GetId", {"IdentityPoolId": self.require("identity_pool")})["IdentityId"]
        result = call("GetCredentialsForIdentity", {"IdentityId": identity})["Credentials"]
        expires = float(result.get("Expiration", now + 1800))
        self.temporary = (result["AccessKeyId"], result["SecretKey"], result["SessionToken"], expires)
        return self.temporary[:3]

    @staticmethod
    def _hash(value): return hashlib.sha256(value).hexdigest()

    def signed(self, method, url, body=b""):
        access, secret, token = self.credentials(); parsed = urllib.parse.urlsplit(url)
        now = datetime.datetime.now(datetime.timezone.utc); timestamp = now.strftime("%Y%m%dT%H%M%SZ"); day = now.strftime("%Y%m%d")
        payload_hash = self._hash(body); headers = {"host": parsed.netloc,
            "x-amz-content-sha256": payload_hash, "x-amz-date": timestamp}
        if method != "GET": headers["content-type"] = "application/json"
        if token: headers["x-amz-security-token"] = token
        canonical_headers = "".join(f"{key}:{headers[key].strip()}\n" for key in sorted(headers))
        signed_headers = ";".join(sorted(headers)); pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        canonical_query = urllib.parse.urlencode(sorted(pairs), quote_via=urllib.parse.quote, safe="~")
        canonical = "\n".join((method, parsed.path or "/", canonical_query, canonical_headers,
                                signed_headers, payload_hash))
        scope = f"{day}/{self.region()}/polly/aws4_request"
        to_sign = f"AWS4-HMAC-SHA256\n{timestamp}\n{scope}\n{self._hash(canonical.encode())}"
        def sign(key, value): return hmac.new(key, value.encode(), hashlib.sha256).digest()
        key = sign(sign(sign(sign(("AWS4" + secret).encode(), day), self.region()), "polly"), "aws4_request")
        signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
        output = {key.title(): value for key, value in headers.items()}
        output["Authorization"] = (f"AWS4-HMAC-SHA256 Credential={access}/{scope}, "
                                   f"SignedHeaders={signed_headers}, Signature={signature}")
        return output

    def proxy_headers(self):
        return {"Authorization": "Bearer " + self.value("token")} if self.value("token") else {}

    def refresh(self):
        try:
            if self.mode() == "proxy":
                data, _ = request(self.require("proxy").rstrip("/") + "/v1/voices", headers=self.proxy_headers())
                items = json.loads(data)
                self._voices = [voice(f"{item['id']}/{item.get('model','neural')}@{item.get('language','en-US')}",
                                item["id"], item.get("language", "en-US"), self.id, "Polly") for item in items]
                return
            found, token = [], ""
            while True:
                url = f"https://polly.{self.region()}.amazonaws.com/v1/voices"
                if token: url += "?" + urllib.parse.urlencode({"NextToken": token})
                data, _ = request(url, headers=self.signed("GET", url))
                result = json.loads(data)
                for item in result.get("Voices", []):
                    for engine in item.get("SupportedEngines", ["standard"]):
                        locale = item.get("LanguageCode", "en-US")
                        found.append(voice(f"{item['Id']}/{engine}@{locale}", item["Id"], locale,
                                           self.id, f"Polly {engine}"))
                token = result.get("NextToken", "")
                if not token: break
            if found: self._voices = found
        except RuntimeError:
            if self.mode() != "direct" or self.value("access_key") or self.value("proxy") or self.value("identity_pool"):
                raise

    def synthesize(self, voice_id, text, speed, emit, cancelled, language=""):
        external = voice_id.removeprefix("aws/").split("@", 1)[0]
        name, engine = external.split("/", 1)
        if self.mode() == "proxy":
            data, _ = request(self.require("proxy").rstrip("/") + "/v1/synthesize",
                              data={"text": text, "voice": name, "model": engine,
                                    "language": language, "speed": speed}, headers=self.proxy_headers())
            self.emit(data, emit, cancelled); return
        body = json.dumps({"Text": text, "TextType": "text", "OutputFormat": "pcm",
                           "SampleRate": "16000", "VoiceId": name, "Engine": engine,
                           "LanguageCode": language or "en-US"}, separators=(",", ":")).encode()
        url = f"https://polly.{self.region()}.amazonaws.com/v1/speech"
        data, _ = request(url, data=body, method="POST", headers=self.signed("POST", url, body))
        self.emit(data, emit, cancelled, sample_rate=16000)


class DeepgramProvider(Provider):
    id, model = "deepgram", "Aura 2"
    defaults = tuple(voice(f"aura-2-{name}-en@en-US", name.title(), "en-US", "deepgram", "Aura 2")
                     for name in ("thalia", "apollo", "asteria", "orion", "luna", "zeus"))

    def synthesize(self, voice_id, text, speed, emit, cancelled, language=""):
        query = urllib.parse.urlencode({"model": self.external(voice_id), "encoding": "linear16",
                                        "sample_rate": 24000, "container": "wav"})
        data, _ = request("https://api.deepgram.com/v1/speak?" + query, data={"text": text},
                          headers={"Authorization": "Token " + self.require("api_key")})
        self.emit(data, emit, cancelled, encoded=True)


class CartesiaProvider(Provider):
    id, model = "cartesia", "Sonic 3"
    defaults = (voice("694f9389-aac1-45b6-b726-9d9369183238@en-US", "Default", "en-US", id, model,
                      ("en", "fr", "de", "es", "pt", "zh", "ja", "ko")),)
    headers_version = "2026-03-01"

    def headers(self):
        return {"Authorization": "Bearer " + self.require("api_key"),
                "Cartesia-Version": self.headers_version}

    def refresh(self):
        if not self.value("api_key"):
            return
        data, _ = request("https://api.cartesia.ai/voices?limit=100", headers=self.headers())
        root = json.loads(data); found = []
        for item in root.get("data", root if isinstance(root, list) else []):
            identifier = item.get("id", ""); locale = item.get("language", "en")
            if identifier and (item.get("is_public", True) or item.get("is_owner")):
                found.append(voice(f"{identifier}@{locale}", item.get("name", "Voice"), locale,
                                   self.id, self.model, (locale,)))
        if found:
            self._voices = found

    def synthesize(self, voice_id, text, speed, emit, cancelled, language=""):
        body = {"model_id": self.value("model", "sonic-3"), "transcript": text,
                "voice": {"id": self.external(voice_id)},
                "language": (language or "en").split("-", 1)[0],
                "output_format": {"container": "raw", "encoding": "pcm_s16le", "sample_rate": 24000},
                "generation_config": {"speed": max(.6, min(1.5, speed))}}
        data, _ = request("https://api.cartesia.ai/tts/bytes", data=body, headers=self.headers())
        self.emit(data, emit, cancelled)


class PlayHtProvider(Provider):
    id, model = "playht", "Play3.0-mini"
    defaults = (voice("default@en-US", "Default", "en-US", id, model),)

    def headers(self):
        return {"AUTHORIZATION": self.require("api_key"), "X-USER-ID": self.require("user_id")}

    def refresh(self):
        if not self.value("api_key") or not self.value("user_id"):
            return
        data, _ = request("https://api.play.ht/api/v2/voices", headers=self.headers())
        root = json.loads(data); found = []
        for item in root.get("voices", root if isinstance(root, list) else []):
            identifier = str(item.get("id", "")); locale = item.get("language_code", "en-US")
            if identifier:
                found.append(voice(f"{urllib.parse.quote(identifier, safe='')}@{locale}",
                                   item.get("name", "Voice"), locale, self.id,
                                   item.get("voice_engine", self.model)))
        if found:
            self._voices = found

    def synthesize(self, voice_id, text, speed, emit, cancelled, language=""):
        body = {"text": text, "voice": urllib.parse.unquote(self.external(voice_id)),
                "voice_engine": self.value("model", self.model), "output_format": "wav",
                "sample_rate": 24000, "speed": speed}
        data, _ = request("https://api.play.ht/api/v2/tts/stream", data=body,
                          headers={**self.headers(), "Accept": "audio/wav"})
        self.emit(data, emit, cancelled, encoded=True)


class ResembleProvider(Provider):
    id, model = "resemble", "Resemble"

    def __init__(self, config):
        names = [item.strip() for item in str(config.get("voices", "")).replace("\n", ",").split(",") if item.strip()]
        self.defaults = tuple(voice(f"{item}@en-US", item, "en-US", self.id, self.model, ("multilingual",))
                              for item in names)
        super().__init__(config)

    def synthesize(self, voice_id, text, speed, emit, cancelled, language=""):
        body = {"voice_uuid": self.external(voice_id), "data": text, "precision": "PCM_16",
                "sample_rate": 24000}
        if self.value("project"):
            body["project_uuid"] = self.value("project")
        data, _ = request(self.value("endpoint", "https://f.cluster.resemble.ai/stream"), data=body,
                          headers={"Authorization": "Bearer " + self.require("api_key")})
        self.emit(data, emit, cancelled, encoded=True)


class CustomProvider(Provider):
    id, model = "custom", "Custom PCM"

    def __init__(self, config):
        names = [item.strip() for item in str(config.get("voices", "default")).replace("\n", ",").split(",") if item.strip()]
        self.defaults = tuple(voice(f"{item}@multilingual", item, "multilingual", self.id, self.model,
                                    ("multilingual",)) for item in names)
        super().__init__(config)

    def synthesize(self, voice_id, text, speed, emit, cancelled, language=""):
        endpoint = self.require("endpoint")
        if urllib.parse.urlsplit(endpoint).scheme != "https":
            raise RuntimeError("Custom PCM endpoint must use HTTPS")
        headers = {"Accept": "audio/pcm"}
        if self.value("token"):
            headers["Authorization"] = "Bearer " + self.value("token")
        data, _ = request(endpoint, data={"text": text, "voice": self.external(voice_id),
                          "language": language, "speed": speed}, headers=headers)
        self.emit(data, emit, cancelled)


class QwenApiProvider(Provider):
    id, model = "qwen-api", "qwen3-tts-flash"
    languages = ("zh", "en", "de", "it", "pt", "es", "ja", "ko", "fr", "ru")
    defaults = tuple(voice(f"{name}@zh-CN", name, "zh-CN", "qwen-api", "qwen3-tts-flash",
                           ("zh", "en", "de", "it", "pt", "es", "ja", "ko", "fr", "ru"))
                     for name in ("Cherry", "Serena", "Ethan", "Chelsie"))

    def synthesize(self, voice_id, text, speed, emit, cancelled, language=""):
        region = self.value("region", "singapore").lower()
        base = "https://dashscope.aliyuncs.com" if region == "beijing" else "https://dashscope-intl.aliyuncs.com"
        language_name = {"zh": "Chinese", "en": "English", "de": "German", "it": "Italian",
                         "pt": "Portuguese", "es": "Spanish", "ja": "Japanese", "ko": "Korean",
                         "fr": "French", "ru": "Russian"}.get((language or "").split("-", 1)[0], "Auto")
        body = {"model": self.value("model", self.model),
                "input": {"text": text, "voice": self.external(voice_id),
                          "language_type": language_name}}
        headers = {"Authorization": "Bearer " + self.require("api_key"),
                   "X-DashScope-Async": "disable"}
        if self.value("workspace"):
            headers["X-DashScope-WorkSpace"] = self.value("workspace")
        data, _ = request(base + "/api/v1/services/aigc/multimodal-generation/generation",
                          data=body, headers=headers, timeout=120)
        result = json.loads(data); output = result.get("output", {})
        audio = output.get("audio", {}) if isinstance(output, dict) else {}
        url = audio.get("url", "") if isinstance(audio, dict) else (audio if isinstance(audio, str) else "")
        if not url:
            raise RuntimeError("Qwen returned no audio URL: " + data.decode("utf-8", "replace")[:1000])
        encoded, _ = request(url, headers={"Accept": "audio/*"})
        self.emit(encoded, emit, cancelled, encoded=True)


PROVIDERS = {
    "openai": OpenAiProvider, "azure": AzureProvider, "qwen-api": QwenApiProvider,
    "google": GoogleProvider, "aws": AwsProvider, "deepgram": DeepgramProvider, "cartesia": CartesiaProvider,
    "playht": PlayHtProvider, "resemble": ResembleProvider, "custom": CustomProvider,
}
