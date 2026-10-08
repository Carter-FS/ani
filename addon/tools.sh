#!/bin/sh
# Builds the per-platform tool bundles (ffmpeg, ffprobe, alass-cli) the add-on downloads on first
# run, into dist/. Every source is pinned by version and SHA-256, so a rebuild has the same tools.
# The zips themselves still differ (timestamps, the alass build), so publish a rebuild under a NEW
# tag (tools-2, ...) and update RELEASE and the checksums in addon/__init__.py: re-uploading to a
# published tag breaks every install that has the old checksums.
# Runs on macOS with curl, unzip and cargo (alass has no macOS release, so it is built here).
set -eu
cd "$(dirname "$0")/.."
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p dist
notice="ffmpeg (GPL): https://ffmpeg.org/download.html
alass (GPL-3.0): https://github.com/kaegi/alass"
mr=https://ffmpeg.martin-riedl.de/download/macos

fetch() {  # fetch <file> <sha256> <url>...: the first URL that answers, checked
    out=$1 sum=$2; shift 2
    for url; do
        curl -fsSL --retry 5 --retry-all-errors -o "$out" "$url" && break
    done
    echo "$sum  $out" | shasum -a 256 -c -s || { echo "checksum mismatch: $out" >&2; exit 1; }
}

bundle() {  # bundle <name> <dir>: zip a flat folder of tools
    printf '%s\n' "$notice" > "$2/SOURCES.txt"
    rm -f "dist/ani-tools-$1.zip"
    (cd "$2" && zip -q -X "$OLDPWD/dist/ani-tools-$1.zip" ./*)
}

# macOS: ffmpeg 9.0.2 from martin-riedl.de; alass 2.0.0 built for each arch
export CFLAGS_x86_64_apple_darwin="-arch x86_64"  # else alass's bundled C code builds for the host arch
rustup target add x86_64-apple-darwin >/dev/null 2>&1 || true
mac() {  # mac <bundle name> <Rust arch> <build path> <ffmpeg sha> <ffprobe sha>
    d="$work/mac-$1"; mkdir -p "$d"
    fetch "$work/ffmpeg.zip" "$4" "$mr/$3/ffmpeg.zip"
    fetch "$work/ffprobe.zip" "$5" "$mr/$3/ffprobe.zip"
    unzip -q -o "$work/ffmpeg.zip" -d "$d"
    unzip -q -o "$work/ffprobe.zip" -d "$d"
    cargo install alass-cli --version 2.0.0 --locked --target "$2-apple-darwin" --root "$work/alass-$1" -q
    cp "$work/alass-$1/bin/alass-cli" "$d/"
    bundle "mac-$1" "$d"
}
mac arm64 aarch64 arm64/1789931890_9.0.2 \
    c8ed4c4e6978a03c485edbfe4e0a5dc2380f8a30bba5150531b31b094492d924 \
    fcbe839537485eaee7a7a8bc5cbc0f90d53617e80943e8a5b2e31cb851197ea6
mac x64 x86_64 amd64/1789931006_9.0.2 \
    7c6b4125b191cbf773832dc51f424cf2b6bb7da43007d1e066f95909e47cacd4 \
    2322438ed2f6319a691291b247d09c69dcaa3a982460d1f269a7e1af335cfdfd

# Windows x64: ffmpeg 9.0.2 essentials from gyan.dev; alass 2.0.0 release
d="$work/win64"; mkdir -p "$d"
fetch "$work/ff.zip" 60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba \
    https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/ffmpeg-9.0.2-essentials_build.zip
unzip -q -j -o "$work/ff.zip" '*/bin/ffmpeg.exe' '*/bin/ffprobe.exe' -d "$d"
fetch "$work/alass.zip" e81a72f97f592910e909a2352d6b8c0de0801c51ac1383bad4ebf3f2ecdd2fd8 \
    https://github.com/kaegi/alass/releases/download/v2.0.0/alass-windows64.zip
unzip -q -j -o "$work/alass.zip" '*/bin/alass-cli.exe' -d "$d"
bundle win64 "$d"

# Linux x64: static ffmpeg 7.0.2 from johnvansickle.com (moved to old-releases once superseded); alass 2.0.0
d="$work/linux64"; mkdir -p "$d"
fetch "$work/ff.tar.xz" abda8d77ce8309141f83ab8edf0596834087c52467f6badf376a6a2a4c87cf67 \
    https://johnvansickle.com/ffmpeg/releases/ffmpeg-7.0.2-amd64-static.tar.xz \
    https://johnvansickle.com/ffmpeg/old-releases/ffmpeg-7.0.2-amd64-static.tar.xz
tar -xJf "$work/ff.tar.xz" -C "$work" --strip-components 1 '*/ffmpeg' '*/ffprobe'  # macOS bsdtar matches globs
mv "$work/ffmpeg" "$work/ffprobe" "$d/"
fetch "$d/alass-cli" 7bd0b9ae7e035d3ba940eacffb21243614df36231d47f21f0b4ce42001ab7fcd \
    https://github.com/kaegi/alass/releases/download/v2.0.0/alass-linux64
bundle linux64 "$d"

shasum -a 256 dist/ani-tools-*.zip
