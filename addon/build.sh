#!/bin/sh
# Builds dist/ani.ankiaddon. For development, link dist/ani into Anki's addons21 folder;
# rebuilding keeps its user_files (state and downloaded tools).
set -eu
cd "$(dirname "$0")/.."
files="__init__.py manifest.json server.py index.html LICENSE"
mkdir -p dist/ani
cp addon/__init__.py addon/manifest.json server.py index.html LICENSE dist/ani/
rm -f dist/ani.ankiaddon
(cd dist/ani && zip -q ../ani.ankiaddon $files)
echo dist/ani.ankiaddon
