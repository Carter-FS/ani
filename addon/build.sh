#!/bin/sh
# Builds dist/ani.ankiaddon (install from file) and dist/ani-ankiweb.zip (upload to AnkiWeb).
# Each one's manifest names the other install as a conflict, so Anki turns the other copy off;
# an add-on must never name itself, or Anki disables it on reinstall.
# For development, link dist/ani into Anki's addons21 folder; rebuilding keeps its user_files.
set -eu
cd "$(dirname "$0")/.."
files="__init__.py manifest.json server.py index.html LICENSE"
mkdir -p dist/ani
cp addon/__init__.py addon/manifest.json server.py index.html LICENSE dist/ani/
rm -f dist/ani.ankiaddon dist/ani-ankiweb.zip
(cd dist/ani && zip -q ../ani.ankiaddon $files)
web=$(mktemp -d)
trap 'rm -rf "$web"' EXIT
cp addon/__init__.py server.py index.html LICENSE "$web/"
sed 's/"conflicts": \["719365920"\]/"conflicts": ["ani"]/' addon/manifest.json > "$web/manifest.json"
grep -q '"conflicts": \["ani"\]' "$web/manifest.json"
(cd "$web" && zip -q "$OLDPWD/dist/ani-ankiweb.zip" $files)
echo dist/ani.ankiaddon dist/ani-ankiweb.zip
