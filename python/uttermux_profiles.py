"""Versioned, engine-specific UtterMux voice profile storage.

The module intentionally has no GTK or broker dependencies so the CLI, daemon,
and import/export tooling all use the same validation rules.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import uuid
import zipfile

SCHEMA_VERSION = 2
READABLE_SCHEMA_VERSIONS = {1, 2}
LANGUAGE_RE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
ENGINES = {
    "pocket": "sherpa-onnx-pocket-tts-int8-2026-01-26",
    "zipvoice": "sherpa-onnx-zipvoice-distill-int8-zh-en-emilia",
}


def data_root() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "uttermux"


def profile_root() -> Path:
    return data_root() / "voice-profiles"


def normalize_language(value: str) -> str:
    value = value.strip().replace("_", "-")
    if not LANGUAGE_RE.fullmatch(value):
        raise ValueError(f"invalid BCP-47 language tag: {value!r}")
    parts = value.split("-")
    return "-".join([parts[0].lower(), *[
        part.upper() if len(part) == 2 and part.isalpha() else part.title()
        for part in parts[1:]
    ]])


def _safe_name(value: str) -> str:
    value = " ".join(value.strip().split())
    if not value or len(value) > 100 or any(ord(ch) < 32 for ch in value):
        raise ValueError("voice name must contain 1-100 printable characters")
    return value


def _atomic_json(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".profile-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(document, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try: os.unlink(temporary)
        except FileNotFoundError: pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(256 * 1024): digest.update(chunk)
    return digest.hexdigest()


def _declared_artifacts(document: dict) -> dict[str, dict]:
    """Return normalized named artifacts, including legacy schema-1 bundles."""
    result = {}
    for kind, value in document.get("artifacts", {}).items():
        if isinstance(kind, str) and isinstance(value, dict) and isinstance(value.get("file"), str):
            result[kind] = value
    legacy = document.get("artifactFile", "")
    if isinstance(legacy, str) and legacy and "runtime" not in result:
        result["runtime"] = {"file": legacy}
    return result


def register_artifact(profile_id: str, kind: str, source: Path) -> dict:
    """Attach a runtime-prepared artifact without discarding the source recording."""
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", kind):
        raise ValueError("artifact kind must be a lowercase identifier")
    item = find_profile(profile_id)
    if not source.is_file():
        raise ValueError(f"artifact does not exist: {source}")
    directory = Path(item["directory"])
    suffix = source.suffix if source.suffix and len(source.suffix) <= 12 else ".bin"
    filename = f"artifact-{kind}{suffix}"
    target = directory / filename
    temporary = directory / f".{filename}.tmp"
    shutil.copyfile(source, temporary)
    os.chmod(temporary, 0o600)
    os.replace(temporary, target)
    path = directory / "profile.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["schemaVersion"] = SCHEMA_VERSION
    document.pop("artifactFile", None)
    artifacts = _declared_artifacts(document)
    artifacts[kind] = {"file": filename, "sha256": _sha256(target)}
    document["artifacts"] = artifacts
    _atomic_json(path, document)
    return document | {"directory": str(directory), "artifactPath": str(target)}


def normalize_reference(source: Path, target: Path, max_seconds: int = 30) -> None:
    if not source.is_file():
        raise ValueError(f"reference audio does not exist: {source}")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    command = [
        "ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(source),
        # Trim the two ends independently. stop_periods=1 would terminate at
        # the first ordinary pause inside a spoken reference.
        "-af", ("silenceremove=start_periods=1:start_threshold=-50dB,"
                "areverse,silenceremove=start_periods=1:start_threshold=-50dB,"
                f"areverse,atrim=duration={max_seconds}"),
        "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le", str(target),
    ]
    subprocess.run(command, check=True)
    os.chmod(target, 0o600)
    probe = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(target),
    ], text=True, capture_output=True, check=True)
    duration = float(probe.stdout.strip())
    if duration < 1.0:
        target.unlink(missing_ok=True)
        raise ValueError("reference needs at least one second of clear speech")


def create_local(engine: str, name: str, language: str, source: Path,
                 transcript: str = "") -> dict:
    if engine not in ENGINES:
        raise ValueError(f"unsupported local clone engine: {engine}")
    language = normalize_language(language)
    if engine == "pocket" and language.split("-", 1)[0] != "en":
        raise ValueError("Pocket currently supports English clone profiles")
    if engine == "zipvoice" and language.split("-", 1)[0] not in {"en", "zh"}:
        raise ValueError("ZipVoice currently supports English and Chinese")
    transcript = " ".join(transcript.strip().split())
    if engine == "zipvoice" and not transcript:
        raise ValueError("ZipVoice requires an exact transcript of the reference audio")
    profile_id = str(uuid.uuid4())
    directory = profile_root() / engine / profile_id
    directory.mkdir(parents=True, mode=0o700)
    reference = directory / "reference.wav"
    try:
        normalize_reference(source, reference, 10 if engine == "pocket" else 30)
        document = {
            "schemaVersion": SCHEMA_VERSION,
            "id": profile_id,
            "voiceId": f"sherpa/{ENGINES[engine]}/custom-{profile_id}@{language}",
            "name": _safe_name(name),
            "language": language,
            "engine": engine,
            "modelVersion": ENGINES[engine],
            "referenceFile": "reference.wav",
            "referenceSha256": _sha256(reference),
            "referenceText": transcript,
            "createdAt": int(time.time() * 1000),
            "localOnly": True,
        }
        _atomic_json(directory / "profile.json", document)
        return document | {"directory": str(directory), "referencePath": str(reference)}
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise


def profiles() -> list[dict]:
    result = []
    root = profile_root()
    if not root.is_dir(): return result
    for manifest in sorted(root.glob("*/*/profile.json")):
        try:
            document = json.loads(manifest.read_text(encoding="utf-8"))
            if document.get("schemaVersion") not in READABLE_SCHEMA_VERSIONS: continue
            reference = manifest.parent / document.get("referenceFile", "")
            artifacts = _declared_artifacts(document)
            artifact_paths = {kind: str(manifest.parent / value["file"])
                              for kind, value in artifacts.items()
                              if (manifest.parent / value["file"]).is_file()}
            document["directory"] = str(manifest.parent)
            document["referencePath"] = str(reference) if reference.is_file() else ""
            document["artifactPaths"] = artifact_paths
            document["artifactPath"] = next(iter(artifact_paths.values()), "")
            document["available"] = reference.is_file() or bool(artifact_paths)
            result.append(document)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return result


def find_profile(profile_id: str) -> dict:
    match = next((item for item in profiles()
                  if item["id"] == profile_id or item.get("voiceId") == profile_id), None)
    if not match: raise ValueError(f"unknown voice profile: {profile_id}")
    return match


def delete_profile(profile_id: str) -> None:
    item = find_profile(profile_id)
    directory = Path(item["directory"]).resolve()
    root = profile_root().resolve()
    if directory.parent.parent != root:
        raise ValueError("unsafe profile directory")
    shutil.rmtree(directory)


def rename_profile(profile_id: str, name: str) -> dict:
    item = find_profile(profile_id)
    path = Path(item["directory"]) / "profile.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["name"] = _safe_name(name)
    _atomic_json(path, document)
    return document


def export_profile(profile_id: str, destination: Path) -> Path:
    item = find_profile(profile_id)
    directory = Path(item["directory"])
    destination = destination.with_suffix(".uttermux-voice") if destination.suffix != ".uttermux-voice" else destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        asset_names = [value["file"] for value in _declared_artifacts(item).values()]
        for name in ("profile.json", item.get("referenceFile", ""), *asset_names):
            if name and (directory / name).is_file(): archive.write(directory / name, name)
    return destination


def import_profile(bundle: Path) -> dict:
    with zipfile.ZipFile(bundle) as archive:
        names = set(archive.namelist())
        if "profile.json" not in names or any(Path(name).is_absolute() or ".." in Path(name).parts for name in names):
            raise ValueError("invalid UtterMux voice bundle")
        if sum(item.file_size for item in archive.infolist()) > 512 * 1024 * 1024:
            raise ValueError("voice bundle is larger than the 512 MB safety limit")
        document = json.loads(archive.read("profile.json"))
        if document.get("schemaVersion") not in READABLE_SCHEMA_VERSIONS or document.get("engine") not in set(ENGINES) | {"qwen"}:
            raise ValueError("unsupported voice bundle schema or engine")
        allowed = {"profile.json", document.get("referenceFile", ""),
                   *[value["file"] for value in _declared_artifacts(document).values()]}
        if any(name not in allowed for name in names):
            raise ValueError("voice bundle contains an undeclared asset")
        profile_id = str(uuid.uuid4())
        engine = document["engine"]
        directory = profile_root() / engine / profile_id
        directory.mkdir(parents=True, mode=0o700)
        try:
            for name in names:
                if name == "profile.json": continue
                target = directory / name
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with archive.open(name) as source, target.open("wb") as output: shutil.copyfileobj(source, output)
                os.chmod(target, 0o600)
            document["id"] = profile_id
            document["name"] = _safe_name(document["name"])
            document["language"] = normalize_language(document["language"])
            document["createdAt"] = int(time.time() * 1000)
            document["schemaVersion"] = SCHEMA_VERSION
            if engine in ENGINES:
                document["voiceId"] = f"sherpa/{ENGINES[engine]}/custom-{profile_id}@{document['language']}"
            reference = directory / document.get("referenceFile", "")
            if reference.is_file() and document.get("referenceSha256") != _sha256(reference):
                raise ValueError("voice bundle reference checksum mismatch")
            for value in _declared_artifacts(document).values():
                artifact = directory / value["file"]
                if not artifact.is_file() or (value.get("sha256") and value["sha256"] != _sha256(artifact)):
                    raise ValueError("voice bundle artifact checksum mismatch")
            _atomic_json(directory / "profile.json", document)
            return document | {"directory": str(directory), "referencePath": str(reference) if reference.is_file() else ""}
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise
