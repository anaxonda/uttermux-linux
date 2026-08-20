#!/usr/bin/env python3
"""Offline checks for mistakes that should never reach a release tag."""

import argparse
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
    for script in (ROOT / "scripts").iterdir():
        if script.suffix in {"", ".py"} and script.is_file() and not script.stat().st_mode & 0o111:
            errors.append(f"script is not executable: {script.relative_to(ROOT)}")
    if not (ROOT / "LICENSE").is_file(): errors.append("LICENSE is missing")
    # The repository has no public remote yet, so the Arch source hash cannot
    # be finalized. Keep CI useful but make a release audit fail loudly.
    pkgbuild = (ROOT / "packaging/arch/PKGBUILD").read_text()
    if not args.ci and ("SKIP" in pkgbuild or "github.com/uttermux/uttermux" in pkgbuild):
        errors.append("Arch package still has placeholder repository URL or hashes")
    for error in errors: print(f"ERROR: {error}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__": raise SystemExit(main())
