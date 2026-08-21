#!/usr/bin/env bash
set -euo pipefail

repo=https://github.com/anaxonda/uttermux-linux

work=$(mktemp -d "${TMPDIR:-/tmp}/uttermux-install.XXXXXXXX")
trap 'rm -rf -- "$work"' EXIT

if [[ ${EUID:-$(id -u)} -eq 0 ]]; then
  printf '%s\n' 'Run this installer as your normal user; makepkg will invoke sudo when needed.' >&2
  exit 2
fi

if [[ -r /etc/debian_version ]] && command -v apt-get >/dev/null; then
  printf '%s\n' 'Detected Debian/Ubuntu; starting the source package installer.'
  curl --proto '=https' --tlsv1.2 --fail --location --silent --show-error \
    "$repo/raw/main/scripts/install-debian" -o "$work/install-debian"
  bash "$work/install-debian"
  exit $?
fi
if [[ ! -r /etc/arch-release ]] || ! command -v pacman >/dev/null; then
  printf '%s\n' 'Automatic dependency installation supports Arch and Debian-family systems.' >&2
  printf '%s\n' "Use: curl -fsSL $repo/raw/main/scripts/install-source | bash" >&2
  exit 2
fi

for command in curl makepkg; do
  command -v "$command" >/dev/null || {
    printf 'Missing required command: %s\n' "$command" >&2
    exit 2
  }
done

printf '%s\n' 'Downloading the signed-release PKGBUILD…'
api=https://api.github.com/repos/anaxonda/uttermux-linux/releases?per_page=10
curl --proto '=https' --tlsv1.2 --fail --location --silent --show-error \
  "$api" -o "$work/releases.json"
tag=$(sed -n 's/.*"tag_name": "\([^"]*\)".*/\1/p' "$work/releases.json" | head -n 1)
if [[ ! "$tag" =~ ^v[0-9A-Za-z._-]+$ ]]; then
  printf '%s\n' 'Could not resolve a safe UtterMux release tag.' >&2
  exit 1
fi
pkgbuild_url="$repo/releases/download/$tag/PKGBUILD"
curl --proto '=https' --tlsv1.2 --fail --location --silent --show-error \
  "$pkgbuild_url" -o "$work/PKGBUILD"

if grep -Eq 'PROJECT_SHA256|SHERPA_SHA256|(^|[^A-Z])SKIP([^A-Z]|$)' "$work/PKGBUILD"; then
  printf '%s\n' 'The published PKGBUILD contains unresolved checksums; refusing installation.' >&2
  exit 1
fi
grep -Fqx "_release_tag=$tag" "$work/PKGBUILD" || {
  printf '%s\n' 'The published PKGBUILD does not match the selected release.' >&2
  exit 1
}

if [[ ${UTTERMUX_INSTALL_CHECK_ONLY:-0} == 1 ]]; then
  (cd "$work" && makepkg --verifysource)
  printf 'Verified Arch release package: %s\n' "$tag"
  exit 0
fi

(
  cd "$work"
  makepkg --syncdeps --install --needed --noconfirm
)

uttermux setup
uttermux doctor
printf '%s\n' 'UtterMux is installed. Restart Firefox and Zotero before selecting its voices.'
