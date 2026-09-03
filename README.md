# OnTask

A menu bar app that periodically asks whether you are still on task, and watches
what you actually have in front of you. Answer yes and the reminders space out;
answer no and they tighten back up. Drift onto something that isn't on your
approved list and it notices.

macOS is the primary target (menu bar, per-tab URL tracking). Windows and Linux
get a smaller control window and app-level tracking.

## How the reminders work

**The ladder.** While you are on an approved app or site, OnTask asks "Still on
task?" on an escalating schedule. Each yes moves you toward a longer interval:

```
 3 min  --1 yes-->  5 min  --2 yes-->  7 min  --2 yes--> 10 min  --3 yes--> 14 min  --3 yes--> 20 min (max)
```

A **no** drops you straight back to 3 minutes. Every number above is editable in
Settings → Reminders, including the rungs themselves and how many yes answers
each rung requires.

**Drifting off.** If the frontmost window is not on the approved list, OnTask
waits 2.5 minutes and then checks in. Saying yes buys you one base interval
(3 minutes) of quiet but **never** advances the ladder, so being distracted can
never earn you longer gaps. Time spent off task is cumulative: 100 seconds in
Messages and then 50 in an unlisted tab still trips at 150.

**Blocked apps.** Anything on the blocked list gets a much shorter fuse: 10
seconds after you focus it.

**Offering to approve.** Say yes three times in a row about the same app or
site and OnTask offers to add it to the approved list for you.

**Ignoring a check-in.** The window stays up and re-alerts every 60 seconds.
After 3 alerts with no answer it counts as a no and resets you to 3 minutes.
You can switch this to "wait indefinitely" or "pause the session" in Settings.

The cadence timer only counts down while you are on an approved target, so a
long detour does not leave a check-in queued up the instant you get back.

## Install and run

```sh
./run.sh
```

That creates a virtualenv, installs dependencies, and starts the app. Or by hand:

```sh
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/python -m ontask
```

A `*` and the session timer appear in the menu bar once a session is running.

### macOS permissions

Two prompts on first use, both expected:

- **Automation** — one prompt per browser, the first time OnTask reads a tab.
  Decline it and that browser silently drops to app-level tracking; OnTask stops
  asking. Grant it later in System Settings → Privacy & Security → Automation.
- **Accessibility** — needed by `pynput` for the global hotkeys. Without it the
  app works fine, you just have to click the buttons.

Running from source means the prompts name your terminal. Build a bundle
(`python setup_app.py py2app`) and they name OnTask instead, which also enables
notification banners.

## Using it

Everything hangs off the menu bar icon:

| Item | What it does |
| --- | --- |
| Start / End Session | Begins or ends timing. Ending resets the ladder. |
| Pause / Resume | Stops the clock and the reminders without losing elapsed time. |
| Profile | Switch work mode; each has its own lists. |
| Approve *thing* | Adds whatever you are looking at to the approved list. |
| Block *thing* | Adds it to the blocked list instead. |
| Settings... | Opens the settings window. |

The top of the menu always shows elapsed time, current profile, where you are on
the ladder, time until the next check-in, and how the current window classifies.

### Hotkeys

| Default | Action |
| --- | --- |
| `⌃⌥⌘O` | Start / end session |
| `⌃⌥⌘Y` | Answer yes |
| `⌃⌥⌘N` | Answer no |

When the check-in window is focused, plain `Y` and `N` work too. Rebind or blank
them out in Settings → General using [pynput syntax](https://pynput.readthedocs.io/en/latest/keyboard.html#global-hotkeys),
e.g. `<ctrl>+<alt>+f`.

### Rules

One per line, in either list:

| Rule | Matches |
| --- | --- |
| `app:Slack` | app by name |
| `app:com.tinyspeck.slackmacgap` | app by bundle id |
| `site:github.com` | github.com **and** every subdomain |
| `site:*.google.com` | explicit wildcard |
| `site:reddit.com/r/python` | that path and anything under it |
| `Xcode` | bare, no dot or slash, so an app |
| `youtube.com` | bare, has a dot, so a site |

The **most specific matching rule wins**, so approving `github.com` while
blocking `github.com/trending` does what you would expect. On a tie, the block
list wins. Approving a browser itself (`app:Safari`) approves every tab in it.

### Profiles

Each profile is a named pair of lists — "Deep Work", "Writing", whatever you
need. Switching profiles applies the new lists immediately without disturbing
the running session. Add, duplicate, rename, and delete them in Settings.

## Settings reference

Settings live in a JSON file you can also edit by hand; the running app picks up
changes within one poll, no restart needed.

- macOS: `~/Library/Application Support/OnTask/config.json`
- Linux: `~/.config/OnTask/config.json`
- Windows: `%APPDATA%\OnTask\config.json`
- Override with `ONTASK_CONFIG=/path/to/config.json`

| Setting | Default | Meaning |
| --- | --- | --- |
| `intervals_minutes` | `3, 5, 7, 10, 14, 20` | The ladder rungs. |
| `advance_after_yes` | `1, 2, 2, 3, 3` | Yes answers needed at each rung. Resized automatically to match the rungs. |
| `distraction_grace_seconds` | `150` | Off-task time before a check-in. |
| `disapproved_grace_seconds` | `10` | Time on a blocked app before a check-in. |
| `suggest_approve_after_yes` | `3` | Consecutive yes answers before offering to approve. `0` disables. |
| `no_response.policy` | `renag_then_no` | Or `wait`, or `pause_session`. |
| `no_response.renag_seconds` | `60` | Gap between re-alerts. |
| `no_response.max_alerts` | `3` | Alerts before it counts as a no. |
| `poll_seconds` | `2.0` | How often the frontmost window is sampled. |
| `prompt_ui` | `window` | Or `notification`, or `both` (banner, escalating to the window). |
| `browsers` | Safari, Chrome, Arc, Brave, Edge | Which browsers get URL tracking. |
| `start_session_on_launch` | `false` | Begin a session at startup. |
| `play_sound` | `true` | Sound with each check-in. |
| `show_elapsed_in_menu_bar` | `true` | Show the timer in the menu bar. |

Bad values are clamped rather than rejected, and a corrupt config is moved aside
to `config.json.bad` so the app still starts.

## Cross-platform notes

| | macOS | Windows | Linux |
| --- | --- | --- | --- |
| Shell | Menu bar | Control window | Control window |
| App tracking | NSWorkspace | Win32 API | `xdotool` |
| Tab URLs | Safari + Chromium browsers | not available | not available |

Firefox has no AppleScript URL support anywhere, so it is always tracked
app-level only. Where focus detection is unavailable, OnTask treats the target
as on-task and falls back to plain ladder reminders rather than nagging.

## Development

```sh
./.venv/bin/python -m unittest discover -s tests   # 50 tests, fake clock, instant
./.venv/bin/python -m ontask --headless            # watch focus detection live
./.venv/bin/python -m ontask --settings            # settings window on its own
```

Layout:

```
ontask/
  config.py      settings model, defaults, atomic save
  ladder.py      the escalating interval
  matching.py    rule syntax and app/site classification
  engine.py      the state machine (no UI, no OS calls, fake-clock testable)
  app.py         controller wiring engine to a UI shell
  hotkeys.py     pynput hotkeys, marshalled onto the UI thread
  browsers.py    browser catalogue
  focus/         macos.py (NSWorkspace + AppleScript), fallback.py (Win32/xdotool)
  ui/            menubar_macos.py, prompt_macos.py, app_tk.py, prompt_tk.py, settings_app.py
```

`engine.py` holds every timing rule and touches nothing platform-specific, which
is what lets the whole suite run on a fake clock in well under a second.
