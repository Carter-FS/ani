#!/bin/sh
# Builds the per-platform tool bundles (ffmpeg, ffprobe, alass-cli) the add-on downloads on first
# run, into dist/. Upload them to the release named in addon/__init__.py and update the checksums.
# Needs curl, unzip, tar and cargo (alass has no macOS release, so it is built here; run on Apple Silicon).
set -eu
cd "$(dirname "$0")/.."
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p dist
notice="ffmpeg (GPL): https://ffmpeg.org/download.html
alass (GPL-3.0): https://github.com/kaegi/alass"

bundle() {  # bundle <name> <dir>: zip a flat folder of tools
    printf '%s\n' "$notice" > "$2/SOURCES.txt"
    rm -f "dist/ani-tools-$1.zip"
    (cd "$2" && zip -q -X "$OLDPWD/dist/ani-tools-$1.zip" ./*)
}

# macOS, Apple Silicon and Intel (needs: rustup target add x86_64-apple-darwin)
export CFLAGS_x86_64_apple_darwin="-arch x86_64"  # else alass's bundled C code builds for the host arch
for arch in arm64:arm64:aarch64 x64:amd64:x86_64; do  # bundle name : ffmpeg build : Rust target
    IFS=: read -r name ff rust <<EOT
$arch
EOT
    d="$work/mac-$name"; mkdir -p "$d"
    for t in ffmpeg ffprobe; do  # the redirect 404s now and then, so retry on any error
        curl -fsSL --retry 5 --retry-all-errors -o "$work/$t.zip" \
            "https://ffmpeg.martin-riedl.de/redirect/latest/macos/$ff/release/$t.zip"
        unzip -q -o "$work/$t.zip" -d "$d"
    done
    cargo install alass-cli --locked --target "$rust-apple-darwin" --root "$work/alass-$name" -q
    cp "$work/alass-$name/bin/alass-cli" "$d/"
    bundle "mac-$name" "$d"
done

# Windows x64
d="$work/win64"; mkdir -p "$d"
curl -fsSL -o "$work/ff.zip" https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip
unzip -q -j -o "$work/ff.zip" '*/bin/ffmpeg.exe' '*/bin/ffprobe.exe' -d "$d"
curl -fsSL -o "$work/alass.zip" https://github.com/kaegi/alass/releases/download/v2.0.0/alass-windows64.zip
unzip -q -j -o "$work/alass.zip" '*/bin/alass-cli.exe' -d "$d"
bundle win64 "$d"

# Linux x64
d="$work/linux64"; mkdir -p "$d"
curl -fsSL https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz \
    | tar -xJ -C "$work" --strip-components 1 '*/ffmpeg' '*/ffprobe'  # macOS bsdtar matches globs
mv "$work/ffmpeg" "$work/ffprobe" "$d/"
curl -fsSL -o "$d/alass-cli" https://github.com/kaegi/alass/releases/download/v2.0.0/alass-linux64
bundle linux64 "$d"

shasum -a 256 dist/ani-tools-*.zip
