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
codesign --force --sign - "$APP"          # ad-hoc signature
(cd build && ditto -c -k --keepParent Pomo.app Pomo.zip)
echo "Built $APP and build/Pomo.zip"
echo "Install:  mv build/Pomo.app /Applications/ && open /Applications/Pomo.app"
