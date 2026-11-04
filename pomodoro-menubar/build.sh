#!/bin/bash
# Builds Pomo.app (universal: Apple silicon + Intel). Needs Xcode Command Line Tools:
#   xcode-select --install
set -euo pipefail
cd "$(dirname "$0")"
APP=build/Pomo.app
rm -rf build && mkdir -p "$APP/Contents/MacOS"
for arch in arm64 x86_64; do
  swiftc -O -target "$arch-apple-macos13.0" Sources/main.swift -o "build/Pomo-$arch"
done
lipo -create build/Pomo-arm64 build/Pomo-x86_64 -output "$APP/Contents/MacOS/Pomo"
cp Info.plist "$APP/Contents/Info.plist"
# Ad-hoc sign with hardened runtime + sandbox (no network entitlement).
codesign --force --options runtime --entitlements Pomo.entitlements --sign - "$APP"
codesign --verify --strict "$APP"
codesign -d --entitlements - "$APP"
(cd build && ditto -c -k --keepParent Pomo.app Pomo.zip)
shasum -a 256 build/Pomo.zip | tee build/Pomo.zip.sha256
echo "Built $APP and build/Pomo.zip"
echo "Install:  mv build/Pomo.app /Applications/ && open /Applications/Pomo.app"
