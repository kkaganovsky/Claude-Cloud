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
