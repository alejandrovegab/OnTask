"""Tests for the clock penalty, the off-task accounting, and the stats store.

Timing runs on the same fake clock as test_core, so these are instant and do
not depend on the machine's real time.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ontask import ipc
from ontask.config import ClockPenalty, Config, Profile
from ontask.engine import (
    BLOCKED,
    CADENCE,
    DISTRACTION,
    Answered,
    Engine,
    OffTaskLogged,
    Recovered,
    ShowPrompt,
)
from ontask.focus import FocusTarget
from ontask.stats import MAX_EVENTS, Stats, Summary, format_span

APPROVED_APP = FocusTarget(app_name="Code", bundle_id="com.microsoft.VSCode")
UNLISTED_APP = FocusTarget(app_name="Messages", bundle_id="com.apple.MobileSMS")
OTHER_UNLISTED = FocusTarget(
    app_name="Safari", bundle_id="com.apple.Safari", url="https://example.com/a"
)
BLOCKED_SITE = FocusTarget(
    app_name="Safari", bundle_id="com.apple.Safari", url="https://www.youtube.com/x"
)


def make_config() -> Config:
    cfg = Config()
    cfg.profiles = [
        Profile(
            name="Test", approved=["app:Code", "site:github.com"], disapproved=["site:youtube.com"]
        )
    ]
    cfg.active_profile = "Test"
    cfg.normalize()
    return cfg


class Harness:
    def __init__(self, cfg=None, step=2.0):
        self.cfg = cfg or make_config()
        self.engine = Engine(self.cfg, now=0.0)
        self.t = 0.0
        self.step = step
        self.engine.start(self.t)

    def run(self, seconds, target, answer=None):
        events = []
        end = self.t + seconds
        while self.t < end:
            self.t += self.step
            for ev in self.engine.tick(self.t, target):
                events.append(ev)
                if isinstance(ev, ShowPrompt) and answer is not None:
                    events.extend(self.engine.answer(answer, self.t))
        return events

    def prompt_of(self, target, limit=3600):
        start = self.t
        while self.t - start < limit:
            self.t += self.step
            for ev in self.engine.tick(self.t, target):
                if isinstance(ev, ShowPrompt):
                    return ev.prompt
        raise AssertionError("no prompt within limit")


class ClockPenaltyTest(unittest.TestCase):
    """A No takes time off the clock, sized to what the stretch actually was."""

    def _answer_no(self, target, kind):
        h = Harness()
        prompt = h.prompt_of(target)
        self.assertEqual(prompt.kind, kind)
        before = h.engine.elapsed_seconds
        events = h.engine.answer(False, h.t)
        answered = next(e for e in events if isinstance(e, Answered))
        return before, h.engine.elapsed_seconds, answered

    def test_blocked_no_costs_the_blocked_wait(self):
        before, after, answered = self._answer_no(BLOCKED_SITE, BLOCKED)
        self.assertEqual(answered.penalty_seconds, 10.0)
        self.assertAlmostEqual(before - after, 10.0)

    def test_off_task_no_costs_the_off_task_wait(self):
        before, after, answered = self._answer_no(UNLISTED_APP, DISTRACTION)
        self.assertEqual(answered.penalty_seconds, 60.0)
        self.assertAlmostEqual(before - after, 60.0)

    def test_approved_no_costs_the_approved_amount(self):
        before, after, answered = self._answer_no(APPROVED_APP, CADENCE)
        self.assertEqual(answered.penalty_seconds, 60.0)
        self.assertAlmostEqual(before - after, 60.0)

    def test_a_fixed_penalty_applies_to_every_kind(self):
        cfg = make_config()
        cfg.reminder.clock_penalty = ClockPenalty(
            enabled=True, match_situation=False, fixed_seconds=25.0
        )
        for target in (APPROVED_APP, UNLISTED_APP, BLOCKED_SITE):
            h = Harness(cfg)
            # A blocked check-in arrives ten seconds in, so without some time on
            # the clock first there would be nothing to take off.
            h.run(60, APPROVED_APP)
            h.prompt_of(target)
            events = h.engine.answer(False, h.t)
            answered = next(e for e in events if isinstance(e, Answered))
            self.assertEqual(answered.penalty_seconds, 25.0, target.label())

    def test_disabled_penalty_leaves_the_clock_alone(self):
        cfg = make_config()
        cfg.reminder.clock_penalty.enabled = False
        h = Harness(cfg)
        h.prompt_of(BLOCKED_SITE)
        before = h.engine.elapsed_seconds
        events = h.engine.answer(False, h.t)
        self.assertEqual(next(e for e in events if isinstance(e, Answered)).penalty_seconds, 0.0)
        self.assertEqual(h.engine.elapsed_seconds, before)

    def test_yes_never_costs_anything(self):
        h = Harness()
        h.prompt_of(BLOCKED_SITE)
        before = h.engine.elapsed_seconds
        events = h.engine.answer(True, h.t)
        self.assertEqual(next(e for e in events if isinstance(e, Answered)).penalty_seconds, 0.0)
        self.assertEqual(h.engine.elapsed_seconds, before)

    def test_the_clock_stops_at_zero_rather_than_going_negative(self):
        cfg = make_config()
        cfg.reminder.clock_penalty = ClockPenalty(
            enabled=True, match_situation=False, fixed_seconds=9999.0
        )
        h = Harness(cfg)
        h.prompt_of(BLOCKED_SITE)
        h.engine.answer(False, h.t)
        self.assertEqual(h.engine.elapsed_seconds, 0.0)


class OffTaskTimerTest(unittest.TestCase):
    """The off-task wait is cumulative, and only approved work resets it."""

    def test_switching_between_unapproved_targets_keeps_counting(self):
        h = Harness()
        h.run(40, UNLISTED_APP)
        # Past the 60s grace in total, but split across two apps.
        events = h.run(30, OTHER_UNLISTED)
        self.assertTrue(any(isinstance(e, ShowPrompt) for e in events))

    def test_returning_to_approved_work_resets_the_wait(self):
        h = Harness()
        h.run(55, UNLISTED_APP)  # nearly out of grace
        h.run(10, APPROVED_APP)  # back on task
        events = h.run(55, UNLISTED_APP)  # a fresh 60s should be granted
        self.assertFalse(any(isinstance(e, ShowPrompt) for e in events))

    def test_the_blocked_wait_is_per_target_and_needs_to_be_continuous(self):
        h = Harness()
        h.run(6, BLOCKED_SITE)  # under the 10s blocked grace
        h.run(6, APPROVED_APP)
        events = h.run(6, BLOCKED_SITE)  # timer restarted, so still no prompt
        self.assertFalse(any(isinstance(e, ShowPrompt) for e in events))


class OffTaskAccountingTest(unittest.TestCase):
    """Time is attributed to the target it was actually spent on."""

    def test_time_is_reported_per_target_when_the_stretch_ends(self):
        h = Harness()
        h.run(20, UNLISTED_APP)
        events = h.run(10, APPROVED_APP)
        logged = [e for e in events if isinstance(e, OffTaskLogged)]
        self.assertEqual(len(logged), 1)
        self.assertEqual(logged[0].key, UNLISTED_APP.key())
        self.assertAlmostEqual(logged[0].seconds, 20.0, places=1)
        self.assertFalse(logged[0].blocked)

    def test_blocked_time_is_marked_as_blocked(self):
        h = Harness()
        h.run(6, BLOCKED_SITE)
        events = h.run(4, APPROVED_APP)
        logged = [e for e in events if isinstance(e, OffTaskLogged)]
        self.assertTrue(logged and logged[0].blocked)

    def test_ending_a_session_banks_the_open_stretch(self):
        h = Harness()
        h.run(30, UNLISTED_APP)
        logged = [e for e in h.engine.stop(h.t) if isinstance(e, OffTaskLogged)]
        self.assertEqual(len(logged), 1)
        self.assertAlmostEqual(logged[0].seconds, 30.0, places=1)

    def test_returning_to_work_after_a_check_in_counts_as_a_recovery(self):
        h = Harness()
        h.prompt_of(UNLISTED_APP)
        h.engine.answer(False, h.t)
        events = h.run(4, APPROVED_APP)
        self.assertTrue(any(isinstance(e, Recovered) for e in events))

    def test_a_late_return_is_not_credited_to_the_check_in(self):
        h = Harness()
        h.prompt_of(UNLISTED_APP)
        # Yes buys a quiet interval, so no second check-in re-arms the window.
        h.engine.answer(True, h.t)
        h.run(130, UNLISTED_APP)  # longer than the 120s recovery window
        events = h.run(4, APPROVED_APP)
        self.assertFalse(any(isinstance(e, Recovered) for e in events))

    def test_a_later_check_in_re_arms_the_recovery_window(self):
        # Going back to work is credited to the check-in that preceded it, so a
        # fresh check-in restarts the clock on that credit.
        h = Harness()
        h.prompt_of(UNLISTED_APP)
        h.engine.answer(True, h.t)
        h.run(200, OTHER_UNLISTED, answer=True)
        events = h.run(4, APPROVED_APP)
        self.assertTrue(any(isinstance(e, Recovered) for e in events))


class StatsStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "stats.json"
        self.now = 1_700_000_000.0
        self.stats = Stats(path=self.path, clock=lambda: self.now)

    def test_records_survive_a_round_trip(self):
        self.stats.record_session(1800, "Deep Work")
        self.stats.record_answer("cadence", True, False, "app:Code", "Code", "Deep Work")
        self.stats.save()
        reloaded = Stats.load(self.path)
        self.assertEqual(len(reloaded.events), 2)
        self.assertEqual(reloaded.summary().session_seconds, 1800)

    def test_trivial_amounts_are_not_recorded(self):
        self.stats.record_session(0.4, "Deep Work")
        self.stats.record_off_task("k", "l", 0.2, False, "Deep Work")
        self.assertEqual(self.stats.events, [])

    def test_the_log_is_capped(self):
        for _ in range(MAX_EVENTS + 50):
            self.stats.record("answer", kind="cadence", yes=True)
        self.assertEqual(len(self.stats.events), MAX_EVENTS)

    def test_a_corrupt_file_yields_an_empty_store(self):
        self.path.write_text("{ not json")
        self.assertEqual(Stats.load(self.path).events, [])

    def test_saving_is_debounced_until_forced(self):
        self.stats.record_session(60, "Deep Work")
        self.stats.maybe_save()  # too soon after construction? still first write
        self.assertTrue(self.path.exists())
        first = self.path.read_text()
        self.stats.record_session(60, "Deep Work")
        self.stats.maybe_save()
        self.assertEqual(self.path.read_text(), first, "debounced")
        self.stats.maybe_save(force=True)
        self.assertNotEqual(self.path.read_text(), first)

    def test_a_range_filters_the_events(self):
        self.stats.record_session(60, "A")
        self.now += 10_000
        self.stats.record_session(120, "B")
        recent = self.stats.summary(since=self.now - 5)
        self.assertEqual(recent.session_seconds, 120)
        self.assertEqual(list(recent.seconds_by_profile), ["B"])


class StatsResetTest(unittest.TestCase):
    """A reset in the statistics window must survive the running app's next save."""

    def setUp(self):
        from ontask.app import Controller

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config_path = Path(self.tmp.name) / "config.json"
        Config().save(self.config_path)
        with mock.patch("ontask.app.get_provider") as provider:
            provider.return_value.current.return_value = FocusTarget()
            self.controller = Controller(config_path=self.config_path)
        self.stats_path = self.controller.stats.path

    def test_the_running_app_drops_what_the_window_cleared(self):
        self.controller.stats.record_session(1800, "Deep Work")
        self.controller.stats.maybe_save(force=True)

        # The statistics window, in its own process, clears the log and nudges.
        window_copy = Stats.load(self.stats_path)
        window_copy.clear()
        ipc.Signal(ipc.runtime_dir(self.config_path), ipc.STATS_CLEARED).send()

        self.controller.poll()
        self.assertEqual(self.controller.stats.events, [])
        self.assertEqual(Stats.load(self.stats_path).events, [])

    def test_events_after_the_reset_are_kept(self):
        stats = Stats(path=self.stats_path, clock=lambda: 100.0)
        stats.record_session(60, "Old")
        stats.clock = lambda: 300.0
        stats.record_session(60, "New")
        stats.forget_before(200.0)
        self.assertEqual([e["profile"] for e in stats.events], ["New"])


class IgnoredCheckinNoticeTest(unittest.TestCase):
    def _announce(self, penalty):
        from ontask.app import Controller

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            Config().save(path)
            with mock.patch("ontask.app.get_provider"):
                controller = Controller(shell=mock.Mock(), config_path=path)
            controller._announce(
                Answered(
                    kind=DISTRACTION,
                    yes=False,
                    interval_minutes=3,
                    ignored=True,
                    penalty_seconds=penalty,
                )
            )
            return controller.shell.notify.call_args[0][1]

    def test_the_notice_says_when_time_came_off_the_clock(self):
        message = self._announce(150)
        self.assertIn("2:30 taken off the session clock", message)

    def test_the_notice_stays_short_without_a_penalty(self):
        self.assertEqual(self._announce(0), "No answer - reminders reset to the shortest interval.")


class SuggestionMessageTest(unittest.TestCase):
    def _message(self, count):
        from ontask.app import Controller

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            cfg = Config()
            cfg.reminder.suggest_approve_after_yes = count
            cfg.save(path)
            with mock.patch("ontask.app.get_provider"):
                controller = Controller(config_path=path)
            return controller.suggestion_message(UNLISTED_APP, "app:Messages")

    def test_the_count_comes_from_the_setting(self):
        self.assertIn("Messages 5 times.", self._message(5))
        self.assertIn("Messages twice.", self._message(2))
        self.assertIn("Messages once.", self._message(1))
        self.assertIn("Add app:Messages to the approved list for Deep Work?", self._message(3))


class SummaryTest(unittest.TestCase):
    def _events(self):
        # 09:00 local on a fixed day, so hour bucketing is deterministic.
        import time as _time

        base = _time.mktime((2026, 3, 2, 9, 0, 0, 0, 0, -1))
        return [
            {"t": base, "e": "session", "seconds": 3600, "profile": "Deep Work"},
            {"t": base, "e": "answer", "kind": "cadence", "yes": True, "ignored": False},
            {"t": base, "e": "answer", "kind": "cadence", "yes": True, "ignored": False},
            {"t": base, "e": "answer", "kind": "cadence", "yes": True, "ignored": False},
            {"t": base, "e": "answer", "kind": "distraction", "yes": False, "ignored": False},
            {"t": base, "e": "answer", "kind": "cadence", "yes": False, "ignored": True},
            {"t": base, "e": "offtask", "label": "youtube.com", "seconds": 600, "blocked": False},
            {"t": base, "e": "offtask", "label": "reddit.com", "seconds": 300, "blocked": True},
            {"t": base, "e": "recovered", "key": "site:youtube.com"},
        ]

    def test_totals_and_rates(self):
        s = Summary.build(self._events())
        self.assertEqual(s.session_count, 1)
        self.assertEqual(s.yes_count, 3)
        self.assertEqual(s.no_count, 1)
        self.assertEqual(s.ignored_count, 1)
        self.assertEqual(s.recovered_count, 1)
        self.assertEqual(s.off_task_seconds, 900)
        self.assertEqual(s.blocked_seconds, 300)
        self.assertAlmostEqual(s.yes_rate, 0.75)
        self.assertAlmostEqual(s.distraction_rate, 0.25)
        self.assertAlmostEqual(s.average_session_seconds, 3600)

    def test_worst_hour_follows_off_task_time(self):
        s = Summary.build(self._events())
        self.assertEqual(s.worst_hour(), 9)
        self.assertEqual(s.best_hour(), 9)

    def test_targets_are_ranked(self):
        s = Summary.build(self._events())
        self.assertEqual(s.top_targets(1), [("youtube.com", 600.0)])

    def test_an_empty_log_reports_empty(self):
        self.assertTrue(Summary.build([]).is_empty)

    def test_durations_read_naturally(self):
        self.assertEqual(format_span(45), "45s")
        self.assertEqual(format_span(90), "1m 30s")
        self.assertEqual(format_span(3600 * 2 + 840), "2h 14m")


class IpcTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_a_second_instance_is_refused(self):
        first = ipc.Lock(self.dir)
        self.addCleanup(first.release)
        self.assertTrue(first.acquire())
        self.assertFalse(ipc.Lock(self.dir).acquire())

    def test_a_leftover_lock_file_does_not_block_startup(self):
        # A quit that skipped cleanup leaves the file, and maybe a pid that a
        # later process reuses; neither may stop the next launch.
        (self.dir / ipc.LOCK_NAME).write_text(str(os.getppid()))
        lock = ipc.Lock(self.dir)
        self.addCleanup(lock.release)
        self.assertTrue(lock.acquire())

    def test_acquiring_twice_is_harmless(self):
        lock = ipc.Lock(self.dir)
        self.addCleanup(lock.release)
        self.assertTrue(lock.acquire())
        self.assertTrue(lock.acquire())

    def test_a_signal_is_seen_once(self):
        reader = ipc.Signal(self.dir, "marker")
        self.assertFalse(reader.received())
        ipc.Signal(self.dir, "marker").send()
        self.assertTrue(reader.received())
        self.assertFalse(reader.received())

    def test_releasing_lets_the_next_instance_start(self):
        lock = ipc.Lock(self.dir)
        lock.acquire()
        lock.release()
        nxt = ipc.Lock(self.dir)
        self.addCleanup(nxt.release)
        self.assertTrue(nxt.acquire())

    def test_the_lock_file_is_private(self):
        lock = ipc.Lock(self.dir)
        self.addCleanup(lock.release)
        lock.acquire()
        if os.name != "nt":
            self.assertEqual((self.dir / ipc.LOCK_NAME).stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main(verbosity=2)
