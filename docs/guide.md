# OnTask user guide

Everything about using OnTask day to day, and every setting. For what OnTask
is and how to install it, see the [README](../README.md).

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
waits 1 minute and then checks in. Saying yes buys you one base interval
(3 minutes) of quiet but **never** advances the ladder, so being distracted can
never earn you longer gaps. Time spent off task is cumulative: 40 seconds in
Messages and then 20 in an unlisted tab still trips at 60.

**Disapproved apps.** Anything on the disapproved list gets a much shorter
fuse: 10 seconds after you focus it. OnTask never blocks anything; it only
checks in sooner.

**Offering to approve.** Say yes three times in a row about the same app or
site and OnTask offers to add it to the approved list for you.

**Ignoring a check-in.** The window stays up and re-alerts every 60 seconds.
After 3 alerts with no answer it counts as a no and resets you to 3 minutes.
You can switch this to "wait indefinitely" or "pause the session" in Settings.

The cadence timer only counts down while you are on an approved target, so a
long detour does not leave a check-in queued up the instant you get back.


## Running OnTask

- A `*` and the session timer appear in the menu bar once a session is running,
  counting every second.
- Launching OnTask while it is already running opens its Settings rather than
  starting a second copy. It runs as a menu bar accessory, so it has no Dock
  icon.
- OnTask asks for notification permission on its first launch, whatever the
  check-in style, since banners also confirm what you approve or disapprove.
  If you turn banners off, nothing else replaces those confirmations; the
  menu's focus line shows the new status. **Permissions...** in the menu shows
  every grant's state and how to fix a missing one.
- Run from source, macOS permission prompts name your terminal and banners say
  "Python". The built app (`./scripts/build-mac-app.sh`) says OnTask, and keeps
  its permissions across rebuilds once the signing certificate in
  [maintaining.md](maintaining.md) is set up.

## Using it

Everything hangs off the menu bar icon:

| Item | What it does |
| --- | --- |
| Start / End Session | Begins or ends timing. Ending resets the ladder. |
| Pause / Resume | Stops the clock and the reminders without losing elapsed time. |
| Profile | Switch work mode; each has its own lists. |
| Approve *thing* | Adds whatever you are looking at to the approved list. |
| Disapprove *thing* | Adds it to the disapproved list instead. |
| Settings... | Opens the settings window. |
| Permissions... | Shows which grants are active and how to fix the missing ones. |

The top of the menu always shows elapsed time, current profile, where you are on
the ladder, time until the next check-in, and how the current window classifies.

That last line works with no session running too: OnTask reads the window you
were in at the moment you open the menu. To check a rule, switch to the app or
site and click the menu bar icon. Between sessions, and while paused, that is
the only time OnTask looks at your windows.

### Hotkeys

| Default | Action |
| --- | --- |
| `⌃⌥⌘O` | Start / end session |
| `⌃⌥⌘Y` | Answer yes |
| `⌃⌥⌘N` | Answer no |

When the check-in window is focused, plain `Y` and `N` work too. Rebind or blank
them out in Settings → General using [pynput syntax](https://pynput.readthedocs.io/en/latest/keyboard.html#global-hotkeys),
e.g. `<ctrl>+<alt>+f`.

### Browsers

On first launch OnTask asks which browsers you use, listing the ones macOS
reports as installed with their own icons. Change the answer at any time in
**Settings → General**, and use **Add from Finder...** there for a browser that
is not detected. **Safari is the only one enabled if you skip the question.**

Identifying a browser by its app rather than by a typed name is the point:
OnTask reads the
`CFBundleIdentifier` and display name straight out of the bundle, then matches
the frontmost app on that bundle id. Names are not reliable identity — Zen is
called `Zen` but its process reports `zen` — so id matching is what makes
tracking work for it. It also survives the app being renamed or moved.

Picking the app also decides *how* the URL is read, from the bundle's own
contents rather than a hardcoded list:

- **Safari and the Chromium family** (Chrome, Arc, Brave, Edge, Vivaldi, Opera,
  Dia) are read with AppleScript, addressed by bundle id. Exact, cheap, and
  gives the real tab URL.
- **Firefox and other Gecko browsers** (Zen, LibreWolf, Floorp, Waterfox, Tor,
  Mullvad) expose no AppleScript URL, so OnTask walks the accessibility tree to
  read the address bar. Any Gecko fork is recognised by the `application.ini`
  in its bundle, so a browser OnTask has never heard of still lands on the
  right route. This is best effort: it depends on the browser's internal view
  hierarchy and can break across releases. When it fails, that browser falls
  back to app-level tracking rather than erroring.
- **Anything else scriptable** is probed once on first use — both AppleScript
  dialects are tried and whichever answers is remembered for the rest of the
  run.
- **An app with no way to read its tab** is still addable, after a warning; it
  is tracked at app level only.

Address bar text that is not a URL — a half-typed search, `about:blank` — is
ignored rather than guessed at. Remove a browser from the list in
Settings → General to stop URL tracking for it entirely.

### The check-in window

The floating window sizes itself to the question, so a long domain name wraps
rather than being clipped, and it follows the system light and dark themes.

- **It gives focus back.** The window has to take focus for `Y` and `N` to work,
  so OnTask remembers the app that was in front and returns you to it once the
  check-in is answered.
- **It confirms the answer.** Whichever way you answer - button, hotkey or
  notification - the matching button lights up briefly before the window goes,
  optionally with a sound. Answers apply the moment you press the key rather
  than at the next poll.
- **It can sit anywhere.** Centre by default, or any corner, or top or bottom
  middle, from Settings → General.

Sounds are separate settings: one for the check-in appearing, one for answering.

### Statistics

**Statistics...** in the menu opens a report over all time, the last 30 or 7
days, or today:

- time in sessions, how many, and the average length, broken down by profile;
- time spent off task, how much of that was on disapproved apps and sites, and
  what share of your session time it came to;
- check-ins answered yes, no, and ignored, and how many were followed by a
  return to approved work within two minutes;
- which apps and sites pull you away the most;
- which hour of the day you stay on task best and which you lose most time in;
- a chart of session time against off-task time per day, which is where a trend
  in either direction shows up.

Everything is derived from an event log in `stats.json`, kept beside
`config.json` and capped at 20,000 events. **Reset statistics** in that window
clears it.

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
disapproving `github.com/trending` does what you would expect. On a tie, the
disapproved list wins. Approving a browser itself (`app:Safari`) approves every
tab in it.

### Answering No

A No means the stretch that just ended was not really work, so by default that
time comes back off the session clock - as much as the stretch actually was:
the disapproved wait on a disapproved site, the off-task wait on something not
listed, or a minute on an approved one. Turn it off, or charge a flat amount
instead, under **Settings → Reminders → When you answer No**. The clock stops at
zero rather than going negative.

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
| `distraction_grace_seconds` | `60` | Off-task time before a check-in. |
| `disapproved_grace_seconds` | `10` | Time on a disapproved app before a check-in. |
| `suggest_approve_after_yes` | `3` | Consecutive yes answers before offering to approve. `0` disables. |
| `no_response.policy` | `renag_then_no` | Or `wait`, or `pause_session`. |
| `no_response.renag_seconds` | `60` | Gap between re-alerts. |
| `no_response.max_alerts` | `3` | Alerts before it counts as a no. |
| `clock_penalty.enabled` | `true` | Take time off the session clock when you answer No. |
| `clock_penalty.match_situation` | `true` | Take off as much as the stretch you were in: the disapproved wait on a disapproved site, the off-task wait on something not listed, `approved_seconds` on an approved one. |
| `clock_penalty.approved_seconds` | `60` | Taken off for a No during a normal cadence check-in. |
| `clock_penalty.fixed_seconds` | `60` | Taken off for every No when `match_situation` is off. |
| `poll_seconds` | `2.0` | How often the frontmost window is sampled during a session. With no session, or a paused one, it isn't sampled at all. |
| `prompt_ui` | `window` | Or `notification` (banner with Yes/No buttons), or `both` (banner, escalating to the window if ignored). |
| `browsers` | Safari only | Browsers that get URL tracking. Each entry records `name`, `bundle_id`, `flavour` and `app_path`; add more from Finder in Settings. |
| `start_session_on_launch` | `false` | Begin a session at startup. |
| `play_sound` | `true` | Sound when a check-in appears. |
| `play_answer_sound` | `true` | Sound when you answer one. |
| `prompt_position` | `center` | Or `top_left`, `top_center`, `top_right`, `bottom_left`, `bottom_center`, `bottom_right`. |
| `setup_complete` | `false` | Set once the first-run browser picker has been answered. |
| `show_elapsed_in_menu_bar` | `true` | Show the timer in the menu bar. |

Bad values are clamped rather than rejected, and a corrupt config is moved aside
to `config.json.bad` so the app still starts.

## Cross-platform notes

| | macOS | Windows | Linux |
| --- | --- | --- | --- |
| Shell | Menu bar | Control window | Control window |
| App tracking | NSWorkspace | Win32 API | `xdotool` |
| Tab URLs | Safari, Chromium browsers, Gecko browsers | not available | not available |
| Check-in | Floating panel or notification | Window | Window |

Where focus detection is unavailable, OnTask treats the target as on-task and
falls back to plain ladder reminders rather than nagging.
