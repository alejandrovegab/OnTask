# Developing OnTask

Setup, tests, tooling and code layout. Release and signing chores are in
[maintaining.md](maintaining.md).

```sh
./.venv/bin/pip install --require-hashes -r requirements.txt   # pinned dependencies and tools
./.venv/bin/pip install --no-deps -e .       # OnTask itself
./scripts/update-lock.sh                     # after changing dependencies in pyproject.toml
./.venv/bin/python -m pytest                 # the suite: fake clock, runs in about a second
./.venv/bin/ruff check src tests             # lint
./.venv/bin/ruff format src tests            # format
./.venv/bin/python -m ontask --headless      # watch focus detection live
./.venv/bin/python -m ontask --settings      # settings window on its own
```

Tests never touch your real settings: `tests/conftest.py` points them at a
throwaway config directory.

Dependencies are locked in `requirements.txt`: exact versions with file
hashes, for every platform. Edit `pyproject.toml`, then run
`./scripts/update-lock.sh`; CI checks that the two agree.

Every pull request runs CI (`.github/workflows/ci.yml`), and it also runs weekly:
- lint, formatting and a `bandit` security scan,
- the tests on macOS, Windows and Linux,
- `pip-audit` of each platform's dependencies.

## Performance

OnTask checks the frontmost app every couple of seconds for as long as it runs,
so that check, `Controller.poll()`, has a budget: a typical poll must take under
**1 ms**. `tests/test_performance.py` enforces it on every pull request, using
a fake focus provider and a profile with 100 rules per list. To see the numbers:

```sh
./.venv/bin/python tests/perf_poll.py
```

## Code layout

```
src/ontask/
  app.py           controller wiring the engine to a UI shell
  hotkeys.py       global hotkeys, marshalled onto the UI thread
  ipc.py           single-instance lock and nudges between the app and its windows
  stats.py         the event log behind the statistics window
  headless.py      no-UI mode for checking focus detection
  core/            platform-neutral: no UI, no OS calls, fake-clock testable
    engine.py      the reminder state machine
    ladder.py      the escalating interval
    matching.py    rule syntax and app/site classification
    config.py      settings model, defaults, migrations, atomic save
    browsers.py    browser identity: bundle inspection and URL-route detection
  focus/           FocusTarget and per-platform provider selection
  platform/
    macos/         menubar.py, prompt.py (check-in panel), notify.py,
                   focus.py (NSWorkspace + AppleScript), ax.py (accessibility
                   address bar), icons.py
    windows/       focus.py (Win32)
    linux/         focus.py (X11 via xdotool)
  ui/tk/           settings.py, stats.py, browser_setup.py (picker + first run),
                   shell.py (window shell), prompt.py, window.py
packaging/macos/   setup_app.py (py2app)
tests/             the suite; conftest.py keeps it off your real config,
                   perf_poll.py measures poll()
```

`core/engine.py` holds every timing rule and touches nothing platform-specific, which
is what lets the whole suite run on a fake clock in well under a second.
