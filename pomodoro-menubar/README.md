# Pomo — tiny menu-bar pomodoro for macOS

- Lives in the menu bar only (no Dock icon). Registers itself to start at login on first launch (toggle in its menu).
- **Focus (25 min):** asks your goal each time; one click reuses the last goal. A small pie in the menu bar shrinks and shifts **blue → teal → amber → orange → red** as time runs out, next to an `mm:ss` countdown.
- **Break (5 min):** starts automatically with a green pie and `Break m:ss` countdown. When it ends, a chime plays and it asks for the next goal (Cancel = stay idle).
- Pause / Resume, Finish early, Stop, and a "Sessions today" count in the menu.
- Lightweight: one ~1 s timer (with tolerance so macOS can coalesce wake-ups) runs only during a session; the icon is redrawn only ~100 times per session; nothing runs while idle.

## Build (on a Mac)

```bash
xcode-select --install   # once, if you don't have the Command Line Tools
./build.sh
mv build/Pomo.app /Applications/ && open /Applications/Pomo.app
```

Or download `Pomo-app` from the GitHub Actions run for this branch, unzip, move to /Applications. Since it is ad-hoc signed (not notarized), the first time: right-click → Open, or run
`xattr -dr com.apple.quarantine /Applications/Pomo.app`.

Requires macOS 13 (Ventura) or later.

## Security

- **No internet, enforced by macOS.** The app is built with the App Sandbox on and *no* network entitlement (`Pomo.entitlements`), so the OS blocks every incoming and outgoing connection. It also can't read your files outside its own container. The code itself contains no networking.
- **Hardened Runtime** signing (blocks code injection and unsigned library loading).
- **What it stores:** your last goal text and today's session count, in its sandboxed preferences only (`~/Library/Containers/com.kkaganovsky.pomo/`).
- **Verify it yourself:** `codesign -d --entitlements - /Applications/Pomo.app` should show only `com.apple.security.app-sandbox`.
- **CI build:** read-only token, no secrets, GitHub actions pinned to exact commit SHAs, credentials not kept after checkout, only the app folder checked out. A SHA-256 checksum ships with the zip (`shasum -a 256 Pomo.zip` to compare).
- Building locally with `./build.sh` (about 150 lines of Swift you can read) avoids trusting any downloaded binary.
