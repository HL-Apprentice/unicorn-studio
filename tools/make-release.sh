#!/bin/bash
# Collect release assets into dist/: app zip (built by mac-app/build.sh), firmware, HA package, docs.
set -euo pipefail
cd "$(dirname "$0")/.."
V="${VERSION:-2.2.0}"
[ -f "mac-app/dist/Unicorn-Studio-$V-macOS-universal.zip" ] || (cd mac-app && ./build.sh)
R="dist/unicorn-studio-$V"; rm -rf "$R"; mkdir -p "$R"
cp "mac-app/dist/Unicorn-Studio-$V-macOS-universal.zip" "mac-app/dist/Unicorn-Studio-$V-macOS-universal.zip.sha256" dist/
cp firmware/main.py firmware/secrets.example.py "$R/"
cp homeassistant/unicorn_agenda.yaml cli/unicorn cli/config.env.example "$R/"
cp docs/INSTALL.md LICENSE LICENSE-ASSETS NOTICE CONTRIBUTORS.md SECURITY.md "$R/"
(cd dist && rm -f "unicorn-studio-$V-firmware-ha-cli.zip" && zip -qr "unicorn-studio-$V-firmware-ha-cli.zip" "unicorn-studio-$V" \
  && shasum -a 256 "unicorn-studio-$V-firmware-ha-cli.zip" > "unicorn-studio-$V-firmware-ha-cli.zip.sha256")
ls -1 dist
