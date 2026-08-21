#!/usr/bin/env python3
"""Offline checks for mistakes that should never reach a release tag."""

import argparse
import json
from pathlib import Path
import re
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--ci", action="store_true")
    args = parser.parse_args(); errors = []
    with (ROOT / "catalog/catalog.toml").open("rb") as stream: catalog = tomllib.load(stream)
    seen = set()
    for model in catalog.get("model", []):
        model_id = model.get("id", "")
        if not model_id or model_id in seen: errors.append(f"duplicate/empty model id: {model_id!r}")
        seen.add(model_id)
        if not model.get("license"): errors.append(f"{model_id}: missing license")
        if not model.get("source_url", "").startswith("https://"):
            errors.append(f"{model_id}: source_url must be HTTPS")
        if model.get("external_installer"):
            installer = ROOT / "scripts" / model["external_installer"]
            if not installer.is_file(): errors.append(f"{model_id}: missing {installer.name}")
        elif not re.fullmatch(r"[0-9a-f]{64}", model.get("sha256", "")):
            errors.append(f"{model_id}: missing immutable SHA-256")
        for asset in model.get("assets", []):
            if not re.fullmatch(r"[0-9a-f]{64}", asset.get("sha256", "")):
                errors.append(f"{model_id}/{asset.get('file')}: missing SHA-256")
    generated = ROOT / "catalog/v2/catalog.json"
    if not generated.is_file():
        errors.append("generated catalog v2 is missing")
    else:
        document = json.loads(generated.read_text(encoding="utf-8"))
        if document.get("schemaVersion") != 2: errors.append("generated catalog has wrong schema")
        variant_ids = [item.get("id") for item in document.get("variants", [])]
        voice_ids = [item.get("id") for item in document.get("voices", [])]
        if len(variant_ids) != len(set(variant_ids)): errors.append("generated catalog has duplicate variants")
        if len(voice_ids) != len(set(voice_ids)): errors.append("generated catalog has duplicate voices")
        known = set(variant_ids)
        for voice in document.get("voices", []):
            if voice.get("variantId") not in known:
                errors.append(f"{voice.get('id')}: unknown variant {voice.get('variantId')}")
    for script in (ROOT / "scripts").iterdir():
        if script.suffix in {"", ".py"} and script.is_file() and not script.stat().st_mode & 0o111:
            errors.append(f"script is not executable: {script.relative_to(ROOT)}")
    installer = ROOT / "install.sh"
    if not installer.is_file() or not installer.stat().st_mode & 0o111:
        errors.append("install.sh is missing or not executable")
    if not (ROOT / "LICENSE").is_file(): errors.append("LICENSE is missing")
    pkgbuild = (ROOT / "packaging/arch/PKGBUILD").read_text()
    placeholders = ("PACKAGE_VERSION", "RELEASE_VERSION", "RELEASE_TAG", "PROJECT_SHA256", "SHERPA_SHA256")
    if any(item in pkgbuild for item in placeholders) or "github.com/anaxonda/uttermux-linux" not in pkgbuild:
        errors.append("checked-in Arch PKGBUILD has unresolved release metadata")
    template = (ROOT / "packaging/arch/PKGBUILD.in").read_text()
    for item in placeholders:
        if item not in template: errors.append(f"Arch release template is missing {item}")
    workflow = (ROOT / ".github/workflows/release.yml").read_text()
    if "packaging/arch/PKGBUILD.in" not in workflow:
        errors.append("release workflow does not render the Arch package template")
    for error in errors: print(f"ERROR: {error}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__": raise SystemExit(main())
