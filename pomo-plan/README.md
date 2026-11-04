# Pomo Plan — morning priority list for Pomo

A separate, sandboxed app (no network) that plans your day and hands it to Pomo.

- **Morning prompt:** the first time your Mac is in use after 5:00 each day, the planner window opens. Closing it or pressing "Skip Today" stops it from asking again until tomorrow. To open it any time: Pomo menu → **Plan My Day…**, or open the app again.
- **Each task:** a title, an **Urgent** slider and an **Important** slider (0–10), and how many 🍅 sessions it needs.
- **Ranking:** score = 0.6 × Important + 0.4 × Urgent, so importance counts more. Each task shows its Eisenhower label: Do first, Schedule, Quick / delegate, or Maybe drop.
- **🔥 Ruthless mode:** cuts anything with Important below 5, keeps the **top 3** tasks, and fits them into a session budget you set (default 6). Cut tasks appear crossed out with the reason, and only kept tasks go to Pomo.
- **Send to Pomo:** Pomo's goal box comes pre-filled with the next unfinished task. A pop-up offers the rest, and Pomo's menu shows "Today's Plan" with progress (e.g. `1/2 🍅`). Clicking a task in that list starts a session with it.
- **Projects tab (multi-day efforts):** each project has an importance slider, an optional deadline, an Active switch, and a list of **steps**, each estimated in 🍅 (aim for 1–4).
  - The project's **next unfinished step** joins your daily list automatically.
  - Its **urgency is computed**: 🍅 left ÷ days to the deadline, compared with a fair share of your daily budget (budget ÷ 3). It rises as the deadline nears and is at least 8 within 2 days.
  - Each step shows **estimated vs actually used** 🍅, counted automatically from Pomo, plus a progress bar.
  - **Review warnings** appear for: more than 3 active projects, a project with no next step, a project past its deadline, or one that can't fit before its deadline at your budget.
- **Summary tab (end of day):** opens automatically at a time you choose (default 5:30 PM) if you did any sessions. You can also open it from Pomo's menu → **Today's Summary…**. It shows total focus time and, per goal: 🍅 and minutes, whether it was planned, over plan, or unplanned. It also lists what you completed, what you planned but didn't start, each project's progress and 🍅 left, and anything that ran over its estimate. A **Copy** button copies it as text.
- Unfinished tasks carry over to the next morning. Delete the ones you finished.
- **Idle cost:** zero. It wakes only on system wake, on day change, or from one timer set for 5:00 the next morning.

## Where the practices come from

Things this app copies from common practice (via web sources, mostly practitioner guides and forums, not peer-reviewed research):
- Estimate work in pomodoros and compare against actuals to improve estimates (Pomodoro Technique, per secondary summaries of Cirillo's method).
- Break projects into session-sized steps and keep one **next action** per project, checked in a weekly review (GTD-style practice).
- Limit work in progress. Personal Kanban sources suggest starting around 3 and tuning from there.

The specific numbers are my design choices, not taken from those sources: the 0.6/0.4 weighting, the cut-off of 5, the urgency formula, and the WIP limit of 3. Change them in `Sources/main.swift`.

## How the two apps talk (and why it's safe)

Sandboxed apps can't read each other's data. So Pomo Plan sends the plan to Pomo by opening a local `pomo://plan?...` link, and Pomo reports each finished session back with `pomoplan://done?...` (title and minutes only). macOS delivers it directly to Pomo, and it never touches the internet. Pomo accepts only today's date, at most 30 tasks of up to 100 characters each, strips control characters, and caps sessions at 8. Any app on your Mac could send such a link, but the worst it could do is change today's suggested goals.

## Build

```bash
./build.sh
mv "build/Pomo Plan.app" /Applications/ && open "/Applications/Pomo Plan.app"
```

Rebuild Pomo too (`../pomodoro-menubar/build.sh`): it gained the `pomo://` receiver and the plan menu. Requires macOS 13 or later.
