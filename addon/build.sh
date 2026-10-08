#!/bin/sh
# Builds dist/ani.ankiaddon. For development, link dist/ani into Anki's addons21 folder.
set -eu
cd "$(dirname "$0")/.."
rm -rf dist/ani dist/ani.ankiaddon
mkdir -p dist/ani
cp addon/__init__.py addon/manifest.json server.py index.html LICENSE dist/ani/
(cd dist/ani && zip -q -r ../ani.ankiaddon .)
echo dist/ani.ankiaddon
