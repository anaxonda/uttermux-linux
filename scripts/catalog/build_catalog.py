#!/usr/bin/env python3
"""Build UtterMux's cross-platform catalog and human-readable model index.

The reviewed Linux TOML catalog is the source for curated/non-sherpa entries.
The Piper snapshot is an optional discovery source.  Outputs are deterministic;
network access is deliberately outside this tool so releases remain reproducible.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tomllib
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
ENGINE_RUNTIME = {
    "kokoro": "sherpa-onnx",
    "kitten": "sherpa-onnx",
    "vits": "sherpa-onnx",
    "matcha": "sherpa-onnx",
    "supertonic": "sherpa-onnx",
    "pocket": "sherpa-onnx",
    "zipvoice": "sherpa-onnx",
    "qwen": "qwen-safetensors",
    "qwen-gguf": "qwen3-tts.cpp",
    "moss": "moss-onnx",
}
COMPANION_PROVIDER = {"qwen": "qwen-local", "qwen-gguf": "qwen-local", "moss": "moss-local"}


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")


def unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_model_records(path: Path) -> list[dict[str, Any]]:
    with path.open("rb") as stream:
        document = tomllib.load(stream)
    if document.get("schema_version") != 1:
        raise ValueError(f"unsupported source catalog schema: {document.get('schema_version')}")
    return document.get("model", [])


def artifact_records(item: dict[str, Any]) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    if item.get("url") and item.get("sha256"):
        artifacts.append({
            "id": "bundle", "role": "bundle", "url": item["url"],
            "sha256": item["sha256"], "sizeBytes": int(item.get("size", 0)),
        })
    for asset in item.get("assets", []):
        artifacts.append({
            "id": f"asset-{slug(asset['file'])}", "role": "asset",
            "path": asset["file"], "url": asset["url"],
            "sha256": asset["sha256"], "sizeBytes": int(asset.get("size", 0)),
        })
    return artifacts


def load_piper(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("Piper source must be a JSON array")
    return data


def build(source: Path, piper_source: Path | None, platform_source: Path | None = None) -> dict[str, Any]:
    source_models = source_model_records(source)
    if platform_source and platform_source.is_file():
        source_models += source_model_records(platform_source)
    providers: dict[str, dict[str, Any]] = {
        "local": {"id": "local", "name": "On-device", "network": False, "cost": "free"},
        "qwen-local": {"id": "qwen-local", "name": "Qwen on-device", "network": False, "cost": "free"},
        "moss-local": {"id": "moss-local", "name": "MOSS on-device", "network": False, "cost": "free"},
    }
    models: dict[str, dict[str, Any]] = {}
    variants: dict[str, dict[str, Any]] = {}
    voices: dict[str, dict[str, Any]] = {}

    def add(item: dict[str, Any]) -> None:
        engine = item["engine"]
        provider_id = COMPANION_PROVIDER.get(engine, "local")
        family = item.get("family", engine)
        model_id = slug(family)
        languages = item.get("languages") or unique([
            voice.get("language", "und") for voice in item.get("voices", [])
        ])
        capabilities = unique(list(item.get("capabilities", [])))
        models.setdefault(model_id, {
            "id": model_id, "library": family,
            "languages": [], "capabilities": [],
            "sourceUrl": item.get("source_url", item.get("url", "")),
        })
        models[model_id]["languages"] = unique(models[model_id]["languages"] + languages)
        models[model_id]["capabilities"] = unique(models[model_id]["capabilities"] + capabilities)
        variant_id = item["id"]
        variant = {
            "id": variant_id, "modelId": model_id, "providerId": provider_id,
            "runtimeId": ENGINE_RUNTIME.get(engine, engine), "engine": engine,
            "location": "on-device", "platforms": item.get("platforms", ["linux", "android"]),
            "status": item.get("status", "downloadable"), "languages": languages,
            "capabilities": capabilities,
            "downloadSizeMb": round(int(item.get("size", 0)) / 1024 / 1024),
            "estimatedRamMb": int(item.get("estimated_ram_mb", 0)),
            "quantization": item.get("quantization", ""),
            "performanceClass": item.get("performance_class", "unknown"),
            "license": item.get("license", ""),
            "sourceUrl": item.get("source_url", item.get("url", "")),
            "files": item.get("files", {}), "artifacts": artifact_records(item),
        }
        if item.get("external_installer"):
            variant["externalInstaller"] = item["external_installer"]
            variant["platforms"] = item.get("platforms", ["linux"])
        variants[variant_id] = variant
        for voice in item.get("voices", []):
            voice_id = (f"{provider_id}/{voice['id']}" if provider_id != "local" else
                        f"local/{variant_id}/{voice['id']}")
            voices[voice_id] = {
                "id": voice_id, "variantId": variant_id,
                "speakerId": str(voice.get("speaker_id", 0)), "name": voice["name"],
                "languages": voice.get("languages", [voice.get("language", "und")]),
                "gender": voice.get("gender", ""),
                "previewUrl": voice.get("preview_url", ""),
                "referenceFile": voice.get("reference_file", ""),
            }

    for model in source_models:
        add(model)

    # Piper discovery augments the curated catalog. Entries lacking a verified
    # sherpa bundle remain documented but are not downloadable.
    for entry in load_piper(piper_source):
        variant_id = f"vits-piper-{entry['key']}"
        if variant_id in variants:
            continue
        item = {
            "id": variant_id, "engine": "vits", "family": "Piper",
            "description": f"Piper {entry['name']} {entry['quality']}",
            "url": entry.get("download_url", ""), "sha256": entry.get("sha256", ""),
            "size": int(entry.get("download_size", 0)),
            "estimated_ram_mb": round(int(entry.get("download_size", 0)) / 1024 / 1024 * 2 + 64),
            "quantization": "ONNX", "performance_class": "fast",
            "license": entry.get("license", "Model-specific"),
            "source_url": "https://github.com/rhasspy/piper",
            "files": {"model": entry.get("model_file", ""), "tokens": "tokens.txt", "data_dir": "espeak-ng-data"},
            "voices": [],
        }
        named = entry.get("speaker_ids", {})
        pairs = list(named.items()) if named else [
            ((f"speaker-{sid}" if int(entry.get("speakers", 1)) > 1 else entry["name"]), sid)
            for sid in range(max(1, int(entry.get("speakers", 1))))
        ]
        for name, sid in pairs:
            item["voices"].append({
                "id": name, "name": name.replace("_", " ").title(),
                "language": entry["language"].replace("_", "-"), "speaker_id": sid,
                "preview_url": entry.get("sample_url", "").replace("speaker_0.mp3", f"speaker_{sid}.mp3"),
            })
        add(item)
        if not item["url"] or not item["sha256"]:
            variants[variant_id]["status"] = "unavailable"

    return {
        "schemaVersion": 2,
        "providers": sorted(providers.values(), key=lambda row: row["id"]),
        "models": sorted(models.values(), key=lambda row: row["id"]),
        "variants": sorted(variants.values(), key=lambda row: row["id"]),
        "voices": sorted(voices.values(), key=lambda row: row["id"]),
        "provenance": {
            "curatedSource": source.name, "curatedSha256": sha256(source),
            "piperSource": piper_source.name if piper_source and piper_source.is_file() else "",
            "piperSha256": sha256(piper_source) if piper_source and piper_source.is_file() else "",
            "platformSource": platform_source.name if platform_source and platform_source.is_file() else "",
            "platformSha256": sha256(platform_source) if platform_source and platform_source.is_file() else "",
        },
    }


def render_markdown(document: dict[str, Any]) -> str:
    variants = document["variants"]
    voices_by_variant: dict[str, int] = {}
    for voice in document["voices"]:
        voices_by_variant[voice["variantId"]] = voices_by_variant.get(voice["variantId"], 0) + 1
    piper = [item for item in variants if item["modelId"] == "piper"]
    curated = [item for item in variants if item["modelId"] != "piper"]
    piper_voice_count = sum(voices_by_variant.get(item["id"], 0) for item in piper)
    piper_languages = sorted({language for item in piper for language in item["languages"]})
    piper_ready = sum(item["status"] == "downloadable" for item in piper)
    lines = [
        "# Local artifact catalog", "",
        "This page is generated from release-pinned UtterMux catalog inputs. Do not edit it by hand.", "",
        "It describes local artifacts in the shared interoperability catalog. It is not a cloud-voice list, "
        "an inventory of installed files, or a claim that every catalog voice is exposed by every platform. "
        "Applications may expand a multi-speaker artifact from its model metadata or expose a reviewed subset. "
        "A platform name means that an integration path exists; benchmark results and recommendations remain "
        "specific to an artifact, runtime, and hardware profile.", "",
        f"The machine-readable catalog contains {len(document['models'])} families, {len(variants)} artifact variants, "
        f"and {len(document['voices'])} explicit voice records. Of those, Piper contributes {len(piper)} variants "
        f"and {piper_voice_count} speaker records.", "",
        "## Curated runtime variants", "",
        "`Voice records` counts entries stored in the shared catalog, not every speaker that a platform can derive "
        "from an artifact. Zero is expected for profile-based cloning models and platform-expanded speaker tables.", "",
        "| Variant | Runtime | Platforms | Languages | Voice records | Download | Est. RAM | Precision | Release status | License |",
        "|---|---|---|---|---:|---:|---:|---|---|---|",
    ]
    for item in curated:
        source = item.get("sourceUrl", "")
        label = f"[{item['id']}]({source})" if source else f"`{item['id']}`"
        lines.append(
            f"| {label} | {item['runtimeId']} | {', '.join(item['platforms'])} | "
            f"{', '.join(item['languages']) or '—'} | {voices_by_variant.get(item['id'], 0)} | "
            f"{item['downloadSizeMb']} MiB | {item['estimatedRamMb']} MiB | "
            f"{item['quantization'] or '—'} | {item['status']} | {item['license'] or '—'} |"
        )
    lines.extend([
        "", "## Piper snapshot", "",
        f"The pinned Piper source contributes {len(piper)} variants across {len(piper_languages)} BCP-47 language "
        f"tags. {piper_ready} have checksum-pinned downloadable artifacts; the remaining "
        f"{len(piper) - piper_ready} records preserve upstream identity but are marked `unavailable`.", "",
        "The full per-variant URLs, checksums, sizes, speaker IDs, preview URLs, licenses, and platform flags are "
        "in [`catalog/v2/catalog.json`](../catalog/v2/catalog.json). Keeping the thousands of generated Piper "
        "speaker rows in JSON avoids turning this human-readable overview into an unwieldy table.", "",
        "## Interpreting the fields", "",
        "- **Download** is compressed transfer size rounded to MiB; zero means no verified downloadable artifact.",
        "- **Est. RAM** is advisory catalog metadata, not a minimum requirement or a benchmark result.",
        "- **Release status** describes catalog availability (`downloadable`, `device-preview`, or `unavailable`); it "
        "does not predict real-time performance on a particular computer or phone.",
        "- Online providers are discovered at runtime and are documented separately in "
        "[`cloud-providers.md`](cloud-providers.md).",
    ])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "catalog/catalog.toml")
    parser.add_argument("--piper-source", type=Path, default=ROOT / "catalog/sources/piper.json")
    parser.add_argument("--platform-source", type=Path, default=ROOT / "catalog/platform-variants.toml")
    parser.add_argument("--output", type=Path, default=ROOT / "catalog/v2/catalog.json")
    parser.add_argument("--docs-output", type=Path, default=ROOT / "docs/MODELS.generated.md")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    document = build(args.source, args.piper_source, args.platform_source)
    payload = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    docs = render_markdown(document)
    if args.check:
        if not args.output.is_file() or args.output.read_text(encoding="utf-8") != payload:
            raise SystemExit(f"generated catalog is stale: {args.output}")
        if not args.docs_output.is_file() or args.docs_output.read_text(encoding="utf-8") != docs:
            raise SystemExit(f"generated documentation is stale: {args.docs_output}")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.docs_output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload, encoding="utf-8")
    args.docs_output.write_text(docs, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
