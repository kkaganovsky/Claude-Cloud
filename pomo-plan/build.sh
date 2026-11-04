#!/bin/bash
# Builds "Pomo Plan.app" (universal: Apple silicon + Intel). Needs Xcode Command Line Tools:
#   xcode-select --install
set -euo pipefail
cd "$(dirname "$0")"
APP="build/Pomo Plan.app"
rm -rf build && mkdir -p "$APP/Contents/MacOS"
for arch in arm64 x86_64; do
  swiftc -O -target "$arch-apple-macos13.0" Sources/main.swift -o "build/PomoPlan-$arch"
done
lipo -create build/PomoPlan-arm64 build/PomoPlan-x86_64 -output "$APP/Contents/MacOS/PomoPlan"
cp Info.plist "$APP/Contents/Info.plist"
# Ad-hoc sign with hardened runtime + sandbox (no network entitlement).
codesign --force --options runtime --entitlements PomoPlan.entitlements --sign - "$APP"
codesign --verify --strict "$APP"
codesign -d --entitlements - "$APP"
(cd build && ditto -c -k --keepParent "Pomo Plan.app" PomoPlan.zip)
shasum -a 256 build/PomoPlan.zip | tee build/PomoPlan.zip.sha256
echo "Built $APP and build/PomoPlan.zip"
echo "Install:  mv \"build/Pomo Plan.app\" /Applications/ && open \"/Applications/Pomo Plan.app\""
