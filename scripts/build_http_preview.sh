#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

release=4.8-beta4
release_url="https://github.com/godotengine/godot-builds/releases/download/$release"
cache="$PWD/build/godot-$release"
destination="$PWD/build/web/http-preview"
editor_name="Godot_v${release}_linux.x86_64"
template_dir="$cache/data/godot/export_templates/4.8.beta4"

mkdir -p "$cache" "$template_dir" "$PWD/build/web"
staging=$(mktemp -d "$PWD/build/http-preview-XXXXXX")
trap 'rm -rf -- "$staging"' EXIT

download() {
  local filename=$1 checksum=$2
  if [[ ! -f "$cache/$filename" ]]; then
    curl --fail --location --connect-timeout 15 --retry 2 \
      --output "$staging/$filename" "$release_url/$filename"
    printf '%s  %s\n' "$checksum" "$staging/$filename" | sha256sum --check --status
    mv -- "$staging/$filename" "$cache/$filename"
  fi
}

if [[ ! -x "$cache/$editor_name" ]]; then
  download "$editor_name.zip" 5579ef508cf88ccae6a029910265b7a8277a24d4d6abd9fe59378e110cc10bef
  unzip -jo "$cache/$editor_name.zip" "$editor_name" -d "$cache"
  chmod +x "$cache/$editor_name"
fi
if [[ ! -s "$template_dir/web_nothreads_release.zip" ]]; then
  templates="Godot_v${release}_export_templates.tpz"
  download "$templates" 646467ea2fef2fe5bbfdf932edc46b591589ecdf8425ccd7bf35865d9828ced6
  unzip -jo "$cache/$templates" 'templates/web_nothreads_release.zip' -d "$template_dir"
fi

editor=("$cache/$editor_name")
if ! editor_version=$("${editor[@]}" --version 2>&1); then
  if [[ "$editor_version" != *GLIBC_*'not found'* ]]; then
    printf '%s\n' "$editor_version" >&2
    exit 1
  fi

  # Run only the beta editor with the Jammy loader and libraries.
  libc_dir="$cache/jammy-libc"
  lib_path="$libc_dir/lib/x86_64-linux-gnu"
  loader="$lib_path/ld-linux-x86-64.so.2"
  if [[ ! -x "$loader" ]]; then
    libc_package="libc6_2.35-0ubuntu3.18_amd64.deb"
    package_path="$cache/$libc_package"
    if [[ ! -s "$package_path" ]]; then
      for mirror in https://mirrors.tuna.tsinghua.edu.cn/ubuntu \
          https://mirrors.ustc.edu.cn/ubuntu https://mirrors.aliyun.com/ubuntu \
          https://security.ubuntu.com/ubuntu https://archive.ubuntu.com/ubuntu; do
        printf 'Fetching Jammy runtime from %s\n' "$mirror"
        if curl --fail --location --connect-timeout 8 --max-time 60 \
            --output "$staging/$libc_package" "$mirror/pool/main/g/glibc/$libc_package" \
            && dpkg-deb --info "$staging/$libc_package" >/dev/null; then
          mv -- "$staging/$libc_package" "$package_path"
          break
        fi
      done
    fi
    if [[ ! -s "$package_path" ]]; then
      printf 'Could not fetch Jammy runtime. Download %s on another machine and copy it to:\n%s\n' \
        "$libc_package" "$package_path" >&2
      exit 1
    fi
    if [[ $(dpkg-deb --field "$package_path" Package) != libc6 ||
          $(dpkg-deb --field "$package_path" Version) != 2.35-0ubuntu3.18 ||
          $(dpkg-deb --field "$package_path" Architecture) != amd64 ]]; then
      printf 'Unexpected package metadata in %s\n' "$package_path" >&2
      exit 1
    fi
    dpkg-deb --extract "$package_path" "$staging/jammy-libc"
    test -x "$staging/jammy-libc/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2"
    mv -T -- "$staging/jammy-libc" "$libc_dir"
  fi
  editor=("$loader" --library-path "$lib_path" "$cache/$editor_name")
  editor_version=$("${editor[@]}" --version)
fi
printf 'Using Godot %s\n' "$editor_version"

mkdir -p "$staging/frontend" "$staging/site"
tar --exclude='./.godot' -C frontend -cf - . | tar -C "$staging/frontend" -xf -
export XDG_DATA_HOME="$cache/data"
"${editor[@]}" --headless --path "$staging/frontend" --import
"${editor[@]}" --headless --path "$staging/frontend" --export-release Web "$staging/site/index.html"

for filename in index.html index.js index.wasm index.pck; do
  test -s "$staging/site/$filename"
done
if [[ -d "$destination" ]]; then
  for asset in "$staging/site"/*; do
    name=${asset##*/}
    if [[ "$name" != index.html ]] && ! cmp -s "$asset" "$destination/$name"; then
      mv -f -- "$asset" "$destination/$name"
    fi
  done
  mv -f -- "$staging/site/index.html" "$destination/index.html"
else
  mv -T -- "$staging/site" "$destination"
fi
printf 'HTTP preview ready at /http-preview/ on the existing Web server.\n'
