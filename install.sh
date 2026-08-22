#!/usr/bin/env bash
set -euo pipefail

repo=https://github.com/anaxonda/uttermux-linux

work=$(mktemp -d "${TMPDIR:-/tmp}/uttermux-install.XXXXXXXX")
trap 'rm -rf -- "$work"' EXIT

install_arch_package() {
  local archive=$1 entry target
  local -a overwrite_args=()

  # Releases before the native packages were installed with `cmake --install`.
  # Those files have no pacman owner, so let this package adopt only exact paths
  # that it contains. Files owned by any other package remain protected by
  # pacman's normal conflict handling.
  while IFS= read -r entry; do
    [[ -n $entry && $entry != */ && $entry != .* ]] || continue
    entry=${entry#./}
    target=/$entry
    if [[ -e $target || -L $target ]] && ! pacman -Qo -- "$target" >/dev/null 2>&1; then
      overwrite_args+=(--overwrite "$entry")
    fi
  done < <(bsdtar -tf "$archive")

  if ((${#overwrite_args[@]})); then
    printf 'Adopting %d files from an earlier source installation…\n' \
      "$(( ${#overwrite_args[@]} / 2 ))"
  fi
  sudo pacman -U --needed --noconfirm "${overwrite_args[@]}" "$archive"
}

if [[ ${EUID:-$(id -u)} -eq 0 ]]; then
  printf '%s\n' 'Run this installer as your normal user; makepkg will invoke sudo when needed.' >&2
  exit 2
fi

if [[ -r /etc/debian_version ]] && command -v apt-get >/dev/null; then
  printf '%s\n' 'Detected Debian/Ubuntu; starting the package installer.'
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

printf '%s\n' 'Resolving the latest published UtterMux release…'
api=https://api.github.com/repos/anaxonda/uttermux-linux/releases?per_page=10
curl --proto '=https' --tlsv1.2 --fail --location --silent --show-error \
  "$api" -o "$work/releases.json"
tag=$(sed -n 's/.*"tag_name": "\([^"]*\)".*/\1/p' "$work/releases.json" | head -n 1)
if [[ ! "$tag" =~ ^v[0-9A-Za-z._-]+$ ]]; then
  printf '%s\n' 'Could not resolve a safe UtterMux release tag.' >&2
  exit 1
fi
version=${tag#v}
machine=$(uname -m)
if [[ $machine == x86_64 && ${UTTERMUX_FORCE_SOURCE:-0} != 1 ]]; then
  package="uttermux-$version-arch-x86_64.pkg.tar.zst"
  printf 'Downloading prebuilt Arch package %s…\n' "$package"
  if curl --proto '=https' --tlsv1.2 --fail --location --silent --show-error \
      "$repo/releases/download/$tag/$package" -o "$work/$package" &&
     curl --proto '=https' --tlsv1.2 --fail --location --silent --show-error \
      "$repo/releases/download/$tag/$package.sha256" -o "$work/$package.sha256"; then
    (cd "$work" && sha256sum --check "$package.sha256")
    if [[ ${UTTERMUX_INSTALL_CHECK_ONLY:-0} == 1 ]]; then
      printf 'Verified Arch binary package: %s\n' "$tag"
      exit 0
    fi
    install_arch_package "$work/$package"
    uttermux setup
    uttermux doctor
    printf '%s\n' 'UtterMux is installed. Restart applications that cache system voice lists.'
    exit 0
  fi
  printf '%s\n' 'No prebuilt package was published for this release; falling back to a source package build.' >&2
fi
printf '%s\n' 'Downloading the checksum-resolved release PKGBUILD…'
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
