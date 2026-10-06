# OnTask

**Stay on task, automatically.**

[![CI](https://github.com/alejandrovegab/OnTask/actions/workflows/ci.yml/badge.svg)](https://github.com/alejandrovegab/OnTask/actions/workflows/ci.yml)
![macOS 15+](https://img.shields.io/badge/macOS-15%2B-black)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)

OnTask is a macOS menu bar app that watches what you actually have in front of
you and checks in when you drift: no rigid timers, no guilt trips, just a quick
**"Still on task?"** when it matters. Stay focused and the check-ins space out;
wander somewhere off-limits and it notices within seconds.

## What it does

- **Adaptive check-ins.** A simple Yes/No prompt. Each *yes* stretches the gap
  to the next one (3 → 5 → 7 → 10 → 14 → 20 minutes), so deep focus earns fewer
  interruptions. A *no* resets it.
- **Notices drift.** One minute on an app or site that isn't on your approved
  list triggers a check-in; something you've blocked gets ten seconds.
- **Knows the site, not just the browser.** Reads the active tab in Safari,
  Chrome, Arc, Brave, Edge, Firefox, Zen and other browsers, so `github.com` and
  `youtube.com` are treated differently even in the same window.
- **Precise rules and profiles.** Approve or block apps and sites down to a
  path (`reddit.com/r/python`); the most specific rule wins. Keep separate lists
  for "Deep Work", "Writing", or whatever you need.
- **Learns from you.** Keep saying yes to the same unlisted app and OnTask
  offers to approve it.
- **Two ways to be asked.** A floating window, a notification banner with
  Yes/No buttons, or a banner that escalates to the window if ignored.
- **Statistics.** Session time, time lost to distractions and where it went,
  your best and worst hours, and the trend over days. Stored locally.
- **Global hotkeys** to answer or start a session without touching the mouse.

**What it isn't:** OnTask doesn't block sites, record your screen, or log what
you type, and it never sends anything over the network.

## How it works

While you're on approved work, OnTask asks "Still on task?" on an escalating
schedule:

```
 3 min --1 yes--> 5 min --2 yes--> 7 min --2 yes--> 10 min --3 yes--> 14 min --3 yes--> 20 min
```

The timer only runs while you're on approved work. Off-task time is cumulative,
so 40 seconds in Messages plus 20 in an unlisted tab still triggers the
one-minute check-in. Saying *yes* while somewhere unlisted buys a few quiet
minutes but never moves you up the ladder, so being distracted can't earn
longer gaps. Answering *no* resets the ladder and, by default, takes that
stretch back off the session clock. Every number is adjustable.

The full rules, every menu item and every setting are in the
[user guide](docs/guide.md).

## Install

OnTask runs on **macOS 15 (Sequoia) or later** with **Python 3.11+**.
Downloadable releases are coming; for now, run it from source:

```sh
git clone https://github.com/alejandrovegab/OnTask.git
cd OnTask
./run.sh
```

`run.sh` creates a virtual environment, installs the pinned dependencies and
starts OnTask in the menu bar. To build a double-clickable `OnTask.app` instead:

```sh
./scripts/build-mac-app.sh
open dist/OnTask.app
```

Windows and Linux currently run with a small control window and app-level
tracking only; full support is on the [roadmap](#roadmap).

### Permissions

All three are optional. OnTask works with less when one is refused, rather than
breaking or asking again.

| Permission | Used for | If refused |
| --- | --- | --- |
| **Automation** | Reading the active tab in Safari and Chromium browsers | That browser is tracked at app level |
| **Accessibility** | Global hotkeys; reading the address bar in Firefox-based browsers | Hotkeys off; those browsers tracked at app level |
| **Notifications** | Banner check-ins with Yes/No buttons | Check-ins use the floating window |

## Privacy

- **What it reads:** the name of the frontmost app and, in browsers you've
  enabled, the address of the active tab. Nothing else.
- **What it keeps:** your settings and a statistics log, on your Mac only, in
  files readable by your account alone.
- **What it sends:** nothing. OnTask has no network code.

## Under the hood

- **A testable core.** The reminder logic is a state machine with no UI or OS
  calls, driven by a clock that tests replace, so 179 tests covering timing,
  rules and platform behavior run in about a second.
- **Browser tab detection.** AppleScript for Safari and Chromium browsers,
  addressed by bundle ID, with the right dialect detected per browser, and a
  bounded walk of the macOS accessibility tree for Firefox and its forks.
  Browsers are identified from their app bundle, not by name.
- **Security.**
  - Bundle IDs are validated before they reach AppleScript, closing a
    script-injection path.
  - Files are written atomically with owner-only permissions.
  - External tools run by absolute path.
  - Dependencies are pinned with hashes across all three platforms.
  - Lock-screen notifications hide the site name.
- **Continuous integration.** Every pull request runs lint, formatting,
  static security analysis (Bandit), dependency vulnerability scanning
  (pip-audit) and the full suite on macOS, Windows and Linux. Merging is blocked
  until all of it passes, and the audit reruns weekly.
- **Packaging.** A code-signed `.app` built with py2app, with its launcher
  rebuilt against the current macOS SDK.

## Roadmap

Planned work, roughly in order. Each item lands as its own reviewed pull request.

- **Performance.** Event-driven focus detection, in-process AppleScript and
  zero work between sessions, aiming for under 0.5% CPU even on an old machine.
- **Native macOS interface.** Settings and Statistics rebuilt in AppKit, with
  the Liquid Glass look on macOS 26 and smooth native scrolling. Settings gains
  search, per-setting reset, and undo for any change.
- **Hotkeys without permissions.** System-registered shortcuts in place of a
  global keyboard listener, so no Accessibility access is needed. Adds a
  click-and-press shortcut editor and more actions.
- **Check-in refinements.**
  - The session clock pauses while a check-in is open.
  - A guard against answering by accident while typing.
  - Configurable sounds.
  - An offer to block a site after repeated *no* answers.
- **Private statistics.** Opt-in, aggregated per day and hour, domains only,
  encrypted with AES-256 using a key kept in the macOS keychain, and deletable
  by site or date range.
- **Onboarding.** A guided first run for permissions and browsers. A choice of
  living in the menu bar, the Dock, or a floating timer.
- **Windows.**
  - A tray, taskbar or floating timer.
  - Site tracking through UI Automation.
  - Distribution via the Microsoft Store and an installer.
- **Linux.**
  - Wayland-first, with a GTK4 interface.
  - Site tracking through the accessibility bus, and a GNOME Shell extension.
  - Packages for Flatpak, AUR, `.deb` and `.rpm`.
- **Releases.** Downloadable builds from GitHub Releases.

## How it's built

OnTask is heavily vibe-coded: most of the code was written by Claude,
Anthropic's AI. My part is deciding what it should do and how, reviewing the
results, and testing every change by hand. Each change lands as its own pull
request and must pass CI (lint, security scans and the full test suite on
three operating systems) before it merges.

## Documentation

- [User guide](docs/guide.md): every rule, menu item, browser detail and setting.
- [Development](docs/development.md): setup, tests, tooling and code layout.
- [Maintaining](docs/maintaining.md): signing, dependencies and release chores.
- [Changelog](CHANGELOG.md): what changed in each version.
- [Security policy](SECURITY.md): how to report a vulnerability privately.

## License

No license has been granted: the source is public to read, but all rights are
reserved.
