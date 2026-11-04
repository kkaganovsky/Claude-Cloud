# Pomo Plan — morning priority list for Pomo

A separate, sandboxed app (no network) that plans your day and hands it to Pomo.

- **Morning prompt:** the first time your Mac is in use after 5:00 each day, the planner window opens. Closing it or pressing "Skip Today" stops it from asking again until tomorrow. To open it any time: Pomo menu → **Plan My Day…**, or open the app again.
- **Each task:** a title, an **Urgent** slider and an **Important** slider (0–10), and how many 🍅 sessions it needs.
- **Ranking:** score = 0.6 × Important + 0.4 × Urgent, so importance counts more. Each task shows its Eisenhower label: Do first, Schedule, Quick / delegate, or Maybe drop.
- **🔥 Ruthless mode:** cuts anything with Important below 5, keeps the **top 3** tasks, and fits them into a session budget you set (default 6). Cut tasks appear crossed out with the reason, and only kept tasks go to Pomo.
- **Send to Pomo:** Pomo's goal box comes pre-filled with the next unfinished task. A pop-up offers the rest, and Pomo's menu shows "Today's Plan" with progress (e.g. `1/2 🍅`). Clicking a task in that list starts a session with it.
- Unfinished tasks carry over to the next morning. Delete the ones you finished.
- **Idle cost:** zero. It wakes only on system wake, on day change, or from one timer set for 5:00 the next morning.

## How the two apps talk (and why it's safe)

Sandboxed apps can't read each other's data. So Pomo Plan sends the plan to Pomo by opening a local `pomo://plan?...` link. macOS delivers it directly to Pomo, and it never touches the internet. Pomo accepts only today's date, at most 30 tasks of up to 100 characters each, strips control characters, and caps sessions at 8. Any app on your Mac could send such a link, but the worst it could do is change today's suggested goals.

## Build

```bash
./build.sh
mv "build/Pomo Plan.app" /Applications/ && open "/Applications/Pomo Plan.app"
```

Rebuild Pomo too (`../pomodoro-menubar/build.sh`): it gained the `pomo://` receiver and the plan menu. Requires macOS 13 or later.
