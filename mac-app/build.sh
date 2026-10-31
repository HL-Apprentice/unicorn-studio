#!/bin/bash
# Build Unicorn Studio.app (universal arm64 + x86_64), hardened runtime, ad-hoc signed, zipped into dist/.
#   ./build.sh              -> build/Unicorn Studio.app + dist/Unicorn-Studio-<version>-macOS-universal.zip
#   INSTALL=1 ./build.sh    -> also copy the app to ~/Applications (the old copy is backed up first,
#                              to $BACKUP_DIR if set)
#   ARCHS=arm64 ./build.sh  -> single-architecture build
set -euo pipefail
cd "$(dirname "$0")"
APP="Unicorn Studio"
BUNDLE_ID="${BUNDLE_ID:-io.github.unicorn-studio}"
VERSION="${VERSION:-2.0.0}"
ARCHS="${ARCHS:-arm64 x86_64}"
MIN_OS="13.0"
B=build; D=dist
rm -rf "$B/$APP.app"; mkdir -p "$B" "$D"
bins=()
for a in $ARCHS; do
  echo "compiling ${a}..."
  xcrun --sdk macosx swiftc -O -parse-as-library -target "$a-apple-macos$MIN_OS" \
    Sources/Core.swift Sources/App.swift -o "$B/UnicornStudio-$a"
  bins+=("$B/UnicornStudio-$a")
done
C="$B/$APP.app/Contents"
mkdir -p "$C/MacOS" "$C/Resources"
lipo -create "${bins[@]}" -output "$C/MacOS/UnicornStudio"
if [ -d ../animations/json ]; then mkdir -p "$C/Resources/Animations"; cp -R ../animations/json/. "$C/Resources/Animations/"; fi
[ -f AppIcon.icns ] && cp AppIcon.icns "$C/Resources/"
cat > "$C/Info.plist" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>$APP</string>
  <key>CFBundleDisplayName</key><string>$APP</string>
  <key>CFBundleIdentifier</key><string>$BUNDLE_ID</string>
  <key>CFBundleExecutable</key><string>UnicornStudio</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>LSMinimumSystemVersion</key><string>$MIN_OS</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>LSApplicationCategoryType</key><string>public.app-category.graphics-design</string>
  <key>NSLocalNetworkUsageDescription</key><string>Unicorn Studio talks to your MQTT broker on your local network to send displays to your Unicorn board.</string>
</dict></plist>
PL
codesign --force --deep -o runtime -s - "$C/.."
codesign --verify --strict "$C/.." && echo "signature ok (ad-hoc, hardened runtime)"
lipo -info "$C/MacOS/UnicornStudio"
Z="$D/Unicorn-Studio-$VERSION-macOS-$( [ "$ARCHS" = "arm64 x86_64" ] && echo universal || echo "${ARCHS// /-}").zip"
rm -f "$Z"; ditto -c -k --keepParent "$B/$APP.app" "$Z"
(cd "$D" && shasum -a 256 "$(basename "$Z")") | tee "$Z.sha256"
if [ "${INSTALL:-0}" = 1 ]; then
  DST="$HOME/Applications/$APP.app"
  if [ -d "$DST" ]; then BKD="${BACKUP_DIR:-$HOME/Applications}"; mkdir -p "$BKD"; BK="$BKD/$APP.app.bak-$(date +%Y%m%d-%H%M%S)"; mv "$DST" "$BK"; echo "old app backed up to $BK"; fi
  mkdir -p "$HOME/Applications"; ditto "$B/$APP.app" "$DST"; echo "installed $DST"
fi
