#!/bin/bash
# Builds "HeavyPomo.app" (universal: Apple silicon + Intel). Needs Xcode Command Line Tools:
#   xcode-select --install
set -euo pipefail
cd "$(dirname "$0")"
APP="build/HeavyPomo.app"
rm -rf build && mkdir -p "$APP/Contents/MacOS"
for arch in arm64 x86_64; do
  swiftc -O -target "$arch-apple-macos13.0" Sources/main.swift -o "build/HeavyPomo-$arch"
done
lipo -create build/HeavyPomo-arm64 build/HeavyPomo-x86_64 -output "$APP/Contents/MacOS/HeavyPomo"
cp Info.plist "$APP/Contents/Info.plist"
# Ad-hoc sign with hardened runtime + sandbox (no network entitlement).
codesign --force --options runtime --entitlements HeavyPomo.entitlements --sign - "$APP"
codesign --verify --strict "$APP"
codesign -d --entitlements - "$APP"
(cd build && ditto -c -k --keepParent "HeavyPomo.app" HeavyPomo.zip)
shasum -a 256 build/HeavyPomo.zip | tee build/HeavyPomo.zip.sha256
echo "Built $APP and build/HeavyPomo.zip"
echo "Install:  mv \"build/HeavyPomo.app\" /Applications/ && open \"/Applications/HeavyPomo.app\""
