# Changelog

Notable changes to OnTask, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/). Nothing has been released yet; the
first release will be 0.1.0.

## [Unreleased]

### Added

- A macOS menu bar app that asks "Still on task?" on a ladder that spaces
  check-ins out with each *yes* (3 → 5 → 7 → 10 → 14 → 20 minutes) and resets
  on a *no*.
- Drift detection: a check-in after one minute, in total, on anything not
  approved, or ten seconds on something disapproved.
- Active-tab tracking in Safari, the Chromium family (Chrome, Arc, Brave, Edge,
  Vivaldi, Opera, Dia) and Firefox and its forks (Zen, LibreWolf, Floorp,
  Waterfox, Tor, Mullvad).
- Browsers chosen on first launch from the ones installed, or added from Finder;
  each is identified by its bundle ID, and the way its tab is read is worked out
  from the app itself.
- Approve and disapprove rules for apps and sites, down to a path, where the
  most specific rule wins; profiles with their own lists.
- An offer to approve an unlisted app or site after repeated *yes* answers.
- Check-ins as a floating window, a notification banner with Yes/No buttons, or
  a banner that escalates to the window if ignored.
- An optional session-time penalty for answering *no*.
- Statistics: session and off-task time, the biggest distractions, best and
  worst hours, and a daily chart, over today, 7 or 30 days, or all time.
- Global hotkeys to start a session and answer check-ins.
- The menu's focus line works with no session running: switch to an app or
  site and open the menu to see how your rules classify it.
- No window reading between sessions or while paused, and no once-a-second
  redraw; OnTask looks at the front window only when you open its menu.
- Approving, disapproving and removing the current app or site from the menu.
  The menu offers only changes that would change its status, names the rule it
  would remove, and confirms with plain names ("Added Messages to the
  disapproved list for Deep Work.").
- **Permissions...** in the menu, showing each grant and how to fix a missing
  one.
- A signed, double-clickable `OnTask.app` built by `scripts/build-mac-app.sh`.
- Windows and Linux: a control window with app-level tracking.

### Security

- Bundle IDs are validated before they reach AppleScript, closing a
  script-injection path.
- Settings and statistics are written atomically and readable by your account
  only; existing files are tightened when OnTask next uses them.
- External tools run by absolute path.
- Check-in banners on the lock screen hide the site name.
- Dependencies are pinned with hashes on every platform.

### Development

- Code organized under `src/` into a platform-neutral core, per-OS platform
  code and the UI.
- CI on every pull request: lint, formatting, Bandit, pip-audit and the test
  suite on macOS, Windows and Linux, plus a weekly dependency audit and
  Dependabot.

[Unreleased]: https://github.com/alejandrovegab/OnTask/commits/main
