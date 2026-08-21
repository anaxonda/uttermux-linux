#!/usr/bin/env bash
set -euo pipefail

repo=https://github.com/anaxonda/uttermux-linux

if [[ ${EUID:-$(id -u)} -eq 0 ]]; then
  printf '%s\n' 'Run this installer as your normal user; makepkg will invoke sudo when needed.' >&2
  exit 2
fi

if [[ ! -r /etc/arch-release ]] || ! command -v pacman >/dev/null; then
  printf '%s\n' 'The one-line installer currently supports Arch Linux only.' >&2
  printf '%s\n' 'Other distributions can use the source-build instructions in the README.' >&2
  exit 2
fi

for command in curl makepkg; do
  command -v "$command" >/dev/null || {
    printf 'Missing required command: %s\n' "$command" >&2
    exit 2
  }
done

work=$(mktemp -d "${TMPDIR:-/tmp}/uttermux-install.XXXXXXXX")
trap 'rm -rf -- "$work"' EXIT

printf '%s\n' 'Downloading the signed-release PKGBUILD…'
api=https://api.github.com/repos/anaxonda/uttermux-linux/releases?per_page=10
curl --proto '=https' --tlsv1.2 --fail --location --silent --show-error \
  "$api" -o "$work/releases.json"
pkgbuild_url=$(sed -n 's/.*"browser_download_url": "\([^"]*\/PKGBUILD\)".*/\1/p' \
  "$work/releases.json" | head -n 1)
if [[ -z "$pkgbuild_url" ]]; then
  printf '%s\n' 'No published UtterMux release contains a PKGBUILD.' >&2
  exit 1
fi
curl --proto '=https' --tlsv1.2 --fail --location --silent --show-error \
  "$pkgbuild_url" -o "$work/PKGBUILD"

if grep -Eq 'PROJECT_SHA256|SHERPA_SHA256|(^|[^A-Z])SKIP([^A-Z]|$)' "$work/PKGBUILD"; then
  printf '%s\n' 'The published PKGBUILD contains unresolved checksums; refusing installation.' >&2
  exit 1
fi

(
  cd "$work"
  makepkg --syncdeps --install --needed --noconfirm
)

uttermux setup
uttermux doctor
printf '%s\n' 'UtterMux is installed. Restart Firefox and Zotero before selecting its voices.'
