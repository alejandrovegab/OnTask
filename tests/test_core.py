"""Timing and rule tests. Everything runs on a fake clock, so it is instant."""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ontask.core.config import Config, Profile, merge_edits, read_existing
from ontask.core.engine import (
    BLOCKED,
    CADENCE,
    DISTRACTION,
    Answered,
    Engine,
    RealertPrompt,
    SessionChanged,
    ShowPrompt,
    SuggestApprove,
    format_duration,
)
from ontask.core.ladder import Ladder
from ontask.core.matching import APPROVED, DISAPPROVED, UNAPPROVED, Rule, classify
from ontask.focus import FocusTarget

APPROVED_APP = FocusTarget(app_name="Code", bundle_id="com.microsoft.VSCode")
UNLISTED_APP = FocusTarget(app_name="Messages", bundle_id="com.apple.MobileSMS")
BLOCKED_SITE = FocusTarget(
    app_name="Safari", bundle_id="com.apple.Safari", url="https://www.youtube.com/watch?v=x"
)
OTHER_UNLISTED = FocusTarget(
    app_name="Safari", bundle_id="com.apple.Safari", url="https://example.com/a"
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
    """Drives the engine on a fake clock, collecting events."""

    def __init__(self, cfg=None, step=2.0):
        self.cfg = cfg or make_config()
        self.engine = Engine(self.cfg, now=0.0)
        self.t = 0.0
        self.step = step
        self.engine.start(self.t)

    def run(self, seconds, target, answer=None):
        """Advance the clock, optionally answering any prompt that appears."""
        events = []
        end = self.t + seconds
        while self.t < end:
            self.t += self.step
            for ev in self.engine.tick(self.t, target):
                events.append(ev)
                if isinstance(ev, ShowPrompt) and answer is not None:
                    events.extend(self.engine.answer(answer, self.t))
        return events

    def run_until_prompt(self, target, limit=3600):
        start = self.t
        while self.t - start < limit:
            self.t += self.step
            for ev in self.engine.tick(self.t, target):
                if isinstance(ev, ShowPrompt):
                    return ev, self.t - start
        raise AssertionError("no prompt within limit")


class LadderTest(unittest.TestCase):
    def test_default_table_matches_spec(self):
        ladder = Ladder()
        seen = [ladder.interval_minutes]
        for _ in range(11):
            ladder.on_yes()
            seen.append(ladder.interval_minutes)
        # 1 yes to leave 3, then 2, 2, 3, 3 to climb to the 20 minute cap.
        self.assertEqual(seen, [3, 5, 5, 7, 7, 10, 10, 10, 14, 14, 14, 20])

    def test_caps_at_max(self):
        ladder = Ladder()
        for _ in range(50):
            ladder.on_yes()
        self.assertEqual(ladder.interval_minutes, 20)
        self.assertTrue(ladder.at_max)

    def test_reset_returns_to_base(self):
        ladder = Ladder()
        for _ in range(6):
            ladder.on_yes()
        ladder.reset()
        self.assertEqual(ladder.interval_minutes, 3)

    def test_custom_ladder_from_settings(self):
        ladder = Ladder(intervals_minutes=[1, 2, 4], advance_after_yes=[1, 1])
        ladder.on_yes()
        self.assertEqual(ladder.interval_minutes, 2)
        ladder.on_yes()
        self.assertEqual(ladder.interval_minutes, 4)
        ladder.on_yes()
        self.assertEqual(ladder.interval_minutes, 4)


class MatchingTest(unittest.TestCase):
    def test_bare_rules_infer_kind(self):
        self.assertEqual(Rule.parse("Slack").kind, "app")
        self.assertEqual(Rule.parse("youtube.com").kind, "site")

    def test_subdomains_match(self):
        target = FocusTarget(app_name="Safari", url="https://mail.google.com/u/0")
        self.assertEqual(classify(target, ["site:google.com"], []).status, APPROVED)

    def test_bundle_id_and_name_both_work(self):
        self.assertEqual(classify(APPROVED_APP, ["app:com.microsoft.VSCode"], []).status, APPROVED)
        self.assertEqual(classify(APPROVED_APP, ["Code"], []).status, APPROVED)

    def test_unlisted_is_unapproved(self):
        self.assertEqual(classify(UNLISTED_APP, ["app:Code"], []).status, UNAPPROVED)

    def test_more_specific_rule_wins(self):
        trending = FocusTarget(app_name="Safari", url="https://github.com/trending")
        repo = FocusTarget(app_name="Safari", url="https://github.com/anthropics/repo")
        approved, blocked = ["site:github.com"], ["site:github.com/trending"]
        self.assertEqual(classify(trending, approved, blocked).status, DISAPPROVED)
        self.assertEqual(classify(repo, approved, blocked).status, APPROVED)

    def test_path_prefix_does_not_match_sibling(self):
        target = FocusTarget(app_name="Safari", url="https://reddit.com/r/pythonista")
        self.assertEqual(classify(target, [], ["site:reddit.com/r/python"]).status, UNAPPROVED)

    def test_unknown_target_is_left_alone(self):
        self.assertEqual(classify(FocusTarget(), ["app:Code"], []).status, APPROVED)

    def test_browser_app_rule_covers_all_tabs(self):
        self.assertEqual(classify(OTHER_UNLISTED, ["app:Safari"], []).status, APPROVED)


class CadenceTest(unittest.TestCase):
    def test_first_prompt_after_three_minutes(self):
        h = Harness()
        _, waited = h.run_until_prompt(APPROVED_APP)
        self.assertAlmostEqual(waited, 180, delta=3)

    def test_yes_answers_walk_the_ladder(self):
        h = Harness()
        gaps = []
        for _ in range(12):
            _, waited = h.run_until_prompt(APPROVED_APP)
            gaps.append(round(waited / 60))
            h.engine.answer(True, h.t)
        self.assertEqual(gaps, [3, 5, 5, 7, 7, 10, 10, 10, 14, 14, 14, 20])

    def test_no_resets_to_three_minutes(self):
        h = Harness()
        for _ in range(4):
            h.run_until_prompt(APPROVED_APP)
            h.engine.answer(True, h.t)
        self.assertEqual(h.engine.ladder.interval_minutes, 7)
        h.run_until_prompt(APPROVED_APP)
        h.engine.answer(False, h.t)
        self.assertEqual(h.engine.ladder.interval_minutes, 3)
        _, waited = h.run_until_prompt(APPROVED_APP)
        self.assertAlmostEqual(waited, 180, delta=3)


class DistractionTest(unittest.TestCase):
    def test_unlisted_app_prompts_after_grace(self):
        h = Harness()
        ev, waited = h.run_until_prompt(UNLISTED_APP)
        self.assertEqual(ev.prompt.kind, DISTRACTION)
        self.assertAlmostEqual(waited, 60, delta=3)

    def test_blocked_site_prompts_after_ten_seconds(self):
        h = Harness()
        ev, waited = h.run_until_prompt(BLOCKED_SITE)
        self.assertEqual(ev.prompt.kind, BLOCKED)
        self.assertAlmostEqual(waited, 10, delta=3)

    def test_yes_while_distracted_does_not_escalate(self):
        h = Harness()
        h.run_until_prompt(UNLISTED_APP)
        h.engine.answer(True, h.t)
        self.assertEqual(h.engine.ladder.interval_minutes, 3)
        # Next nudge comes one base interval later, not one grace period later.
        _, waited = h.run_until_prompt(UNLISTED_APP)
        self.assertAlmostEqual(waited, 180, delta=4)
        self.assertEqual(h.engine.ladder.interval_minutes, 3)

    def test_cadence_timer_freezes_while_distracted(self):
        h = Harness()
        h.run(120, APPROVED_APP)  # 60s left on the 3 min cadence
        h.run(600, UNLISTED_APP, answer=True)  # ten minutes off task
        remaining_before = h.engine.cadence_remaining
        self.assertAlmostEqual(remaining_before, 60, delta=4)
        _, waited = h.run_until_prompt(APPROVED_APP)
        self.assertAlmostEqual(waited, 60, delta=4)

    def test_distraction_accumulates_across_unlisted_apps(self):
        h = Harness()
        h.run(40, UNLISTED_APP)
        ev, waited = h.run_until_prompt(OTHER_UNLISTED)
        self.assertEqual(ev.prompt.kind, DISTRACTION)
        self.assertAlmostEqual(waited, 20, delta=4)

    def test_returning_to_approved_clears_distraction(self):
        h = Harness()
        h.run(50, UNLISTED_APP)
        h.run(10, APPROVED_APP)
        _, waited = h.run_until_prompt(UNLISTED_APP)
        self.assertAlmostEqual(waited, 60, delta=4)

    def test_no_while_distracted_resets_ladder(self):
        h = Harness()
        for _ in range(3):
            h.run_until_prompt(APPROVED_APP)
            h.engine.answer(True, h.t)
        self.assertEqual(h.engine.ladder.interval_minutes, 7)
        h.run_until_prompt(BLOCKED_SITE)
        h.engine.answer(False, h.t)
        self.assertEqual(h.engine.ladder.interval_minutes, 3)

    def test_three_consecutive_yes_offers_to_approve(self):
        h = Harness()
        events = []
        for _ in range(3):
            h.run_until_prompt(UNLISTED_APP)
            events.extend(h.engine.answer(True, h.t))
        suggestions = [e for e in events if isinstance(e, SuggestApprove)]
        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0].rule, "app:Messages")

    def test_suggestion_uses_site_rule_for_browser_tabs(self):
        cfg = make_config()
        cfg.profiles[0].disapproved = []
        h = Harness(cfg)
        events = []
        for _ in range(3):
            h.run_until_prompt(BLOCKED_SITE)
            events.extend(h.engine.answer(True, h.t))
        suggestions = [e for e in events if isinstance(e, SuggestApprove)]
        self.assertEqual(suggestions[0].rule, "site:youtube.com")


class NoResponseTest(unittest.TestCase):
    def test_three_alerts_then_counted_as_no(self):
        h = Harness()
        for _ in range(5):
            h.run_until_prompt(APPROVED_APP)
            h.engine.answer(True, h.t)
        self.assertEqual(h.engine.ladder.interval_minutes, 10)
        h.run_until_prompt(APPROVED_APP)
        events = h.run(200, APPROVED_APP)
        realerts = [e for e in events if isinstance(e, RealertPrompt)]
        answers = [e for e in events if isinstance(e, Answered)]
        self.assertEqual(len(realerts), 2)
        self.assertEqual(len(answers), 1)
        self.assertTrue(answers[0].ignored)
        self.assertFalse(answers[0].yes)
        self.assertEqual(h.engine.ladder.interval_minutes, 3)

    def test_wait_policy_never_auto_answers(self):
        cfg = make_config()
        cfg.reminder.no_response.policy = "wait"
        h = Harness(cfg)
        h.run_until_prompt(APPROVED_APP)
        events = h.run(900, APPROVED_APP)
        self.assertEqual([e for e in events if isinstance(e, Answered)], [])
        self.assertTrue(h.engine.snapshot().prompt_open)

    def test_pause_policy_pauses_session(self):
        cfg = make_config()
        cfg.reminder.no_response.policy = "pause_session"
        h = Harness(cfg)
        h.run_until_prompt(APPROVED_APP)
        h.run(120, APPROVED_APP)
        self.assertEqual(h.engine.phase, "paused")


class SessionTest(unittest.TestCase):
    def test_elapsed_tracks_running_time_only(self):
        h = Harness()
        h.run(60, APPROVED_APP)
        h.engine.pause(h.t)
        h.run(60, APPROVED_APP)
        self.assertAlmostEqual(h.engine.snapshot().elapsed_seconds, 60, delta=3)
        h.engine.resume(h.t)
        h.run(30, APPROVED_APP)
        self.assertAlmostEqual(h.engine.snapshot().elapsed_seconds, 90, delta=3)

    def test_sleep_gap_does_not_bank_time(self):
        h = Harness()
        h.run(60, APPROVED_APP)
        h.engine.tick(h.t + 7200, APPROVED_APP)  # laptop slept for two hours
        self.assertLess(h.engine.snapshot().elapsed_seconds, 80)

    def test_restart_resets_ladder_and_elapsed(self):
        h = Harness()
        for _ in range(4):
            h.run_until_prompt(APPROVED_APP)
            h.engine.answer(True, h.t)
        h.engine.stop(h.t)
        h.engine.start(h.t)
        self.assertEqual(h.engine.ladder.interval_minutes, 3)
        self.assertEqual(h.engine.snapshot().elapsed_seconds, 0)

    def test_no_prompts_while_idle(self):
        cfg = make_config()
        engine = Engine(cfg, now=0.0)
        events = []
        for i in range(1, 400):
            events.extend(engine.tick(float(i * 2), UNLISTED_APP))
        self.assertEqual(events, [])

    def test_switching_profile_applies_new_lists(self):
        cfg = make_config()
        cfg.profiles.append(Profile(name="Loose", approved=["app:Messages"], disapproved=[]))
        cfg.normalize()
        h = Harness(cfg)
        h.engine.set_profile("Loose")
        events = h.run(300, UNLISTED_APP)
        kinds = [e.prompt.kind for e in events if isinstance(e, ShowPrompt)]
        # Messages is approved in this profile, so it is nudged on the normal
        # cadence and never treated as a distraction.
        self.assertNotIn(DISTRACTION, kinds)
        self.assertIn(CADENCE, kinds)

    def test_pausing_keeps_the_time_the_clock_already_showed(self):
        # Polls at 0 and 2 s; the menu bar reads 3.5 s just before the pause.
        engine = Engine(make_config(), now=0.0)
        engine.start(0.0)
        engine.tick(2.0, APPROVED_APP)
        shown = engine.snapshot(3.5).elapsed_seconds
        engine.pause(3.5)
        self.assertEqual(engine.snapshot(3.5).elapsed_seconds, shown)
        self.assertEqual(engine.snapshot(9.0).elapsed_seconds, 3.5)

    def test_ending_a_session_banks_the_time_since_the_last_poll(self):
        engine = Engine(make_config(), now=0.0)
        engine.start(0.0)
        engine.tick(2.0, APPROVED_APP)
        events = engine.stop(3.5)
        ended = [e for e in events if isinstance(e, SessionChanged)]
        self.assertEqual(ended[-1].elapsed_seconds, 3.5)

    def test_the_countdown_shown_off_task_does_not_run_ahead(self):
        # Off approved work the next check-in is on hold, so the display must
        # not count it down only for the next poll to put it back.
        engine = Engine(make_config(), now=0.0)
        engine.start(0.0)
        engine.tick(2.0, UNLISTED_APP)
        held = engine.snapshot(2.0).next_prompt_seconds
        self.assertEqual(engine.snapshot(3.5).next_prompt_seconds, held)
        engine.tick(4.0, APPROVED_APP)
        self.assertEqual(engine.snapshot(5.0).next_prompt_seconds, held - 3.0)

    def test_pausing_on_work_keeps_the_countdown_shown(self):
        engine = Engine(make_config(), now=0.0)
        engine.start(0.0)
        engine.tick(2.0, APPROVED_APP)
        shown = engine.snapshot(3.5).next_prompt_seconds
        engine.pause(3.5)
        engine.resume(10.0)
        self.assertEqual(engine.snapshot(10.0).next_prompt_seconds, shown)

    def test_format_duration(self):
        self.assertEqual(format_duration(59), "0:59")
        self.assertEqual(format_duration(605), "10:05")
        self.assertEqual(format_duration(3725), "1:02:05")


class ConfigTest(unittest.TestCase):
    def test_roundtrip(self):
        cfg = make_config()
        again = Config.from_dict(cfg.to_dict())
        self.assertEqual(again.to_dict(), cfg.to_dict())

    def test_advance_list_is_resized_to_match_intervals(self):
        cfg = Config()
        cfg.reminder.intervals_minutes = [2, 4, 8, 16]
        cfg.reminder.advance_after_yes = [1]
        cfg.normalize()
        self.assertEqual(len(cfg.reminder.advance_after_yes), 3)

    def test_bad_values_are_clamped(self):
        cfg = Config()
        cfg.general.poll_seconds = 0.01
        cfg.reminder.disapproved_grace_seconds = -5
        cfg.reminder.no_response.policy = "nonsense"
        cfg.normalize()
        self.assertGreaterEqual(cfg.general.poll_seconds, 0.5)
        self.assertGreaterEqual(cfg.reminder.disapproved_grace_seconds, 1)
        self.assertEqual(cfg.reminder.no_response.policy, "renag_then_no")

    def test_missing_active_profile_falls_back(self):
        cfg = Config.from_dict({"active_profile": "Gone", "profiles": [{"name": "Only"}]})
        self.assertEqual(cfg.active_profile, "Only")

    def test_save_and_load(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            cfg = make_config()
            cfg.reminder.intervals_minutes = [1, 2, 3]
            cfg.normalize()
            cfg.save(path)
            loaded = Config.load(path)
            self.assertEqual(loaded.reminder.intervals_minutes, [1, 2, 3])
            self.assertEqual(loaded.active_profile, "Test")


class MergeEditsTest(unittest.TestCase):
    """Settings saves only what changed in its window, over the file as it is now.

    `base` is what the window opened with, `mine` what it holds at Save, and
    `theirs` the file after the menu wrote to it meanwhile.
    """

    def setUp(self):
        self.base = Config()
        self.mine = copy.deepcopy(self.base)
        self.theirs = copy.deepcopy(self.base)

    def merged(self, origins=None):
        if origins is None:
            origins = {p.name: p.name for p in self.base.profiles}
        return merge_edits(self.base, self.mine, self.theirs, origins)

    def test_a_menu_rule_survives_an_unrelated_setting_change(self):
        self.theirs.profile("Deep Work").approved.append("site:example.com")
        self.mine.general.poll_seconds = 3
        out = self.merged()
        self.assertIn("site:example.com", out.profile("Deep Work").approved)
        self.assertEqual(out.general.poll_seconds, 3)

    def test_rules_added_on_both_sides_are_all_kept(self):
        self.theirs.profile("Deep Work").approved.append("site:example.com")
        self.mine.profile("Deep Work").approved.append("site:reddit.com/r/python")
        approved = self.merged().profile("Deep Work").approved
        self.assertIn("site:example.com", approved)
        self.assertIn("site:reddit.com/r/python", approved)

    def test_a_rule_removed_in_the_window_stays_removed(self):
        self.mine.profile("Deep Work").disapproved.remove("site:x.com")
        self.theirs.profile("Deep Work").disapproved.append("app:Messages")
        disapproved = self.merged().profile("Deep Work").disapproved
        self.assertNotIn("site:x.com", disapproved)
        self.assertIn("app:Messages", disapproved)

    def test_a_menu_move_survives_when_the_window_left_both_lists_alone(self):
        # The menu disapproves an approved site: it leaves one list, joins the other.
        dw = self.theirs.profile("Deep Work")
        dw.approved.remove("site:github.com")
        dw.disapproved.append("site:github.com")
        out = self.merged().profile("Deep Work")
        self.assertNotIn("site:github.com", out.approved)
        self.assertIn("site:github.com", out.disapproved)

    def test_the_windows_order_is_kept_when_the_menu_did_not_touch_the_list(self):
        self.mine.profile("Deep Work").approved.reverse()
        self.assertEqual(
            self.merged().profile("Deep Work").approved, self.mine.profile("Deep Work").approved
        )

    def test_a_rename_keeps_rules_the_menu_added_under_the_old_name(self):
        self.theirs.profile("Deep Work").approved.append("site:example.com")
        self.mine.profile("Deep Work").name = "Focus"
        self.mine.active_profile = "Focus"
        out = self.merged({"Focus": "Deep Work", "Writing": "Writing"})
        self.assertEqual(out.profile_names(), ["Focus", "Writing"])
        self.assertIn("site:example.com", out.profile("Focus").approved)
        self.assertEqual(out.active_profile, "Focus")

    def test_the_menus_profile_switch_stands(self):
        self.theirs.active_profile = "Writing"
        self.mine.general.play_sound = not self.base.general.play_sound
        self.assertEqual(self.merged().active_profile, "Writing")

    def test_the_menus_profile_switch_follows_a_rename(self):
        self.theirs.active_profile = "Writing"
        self.mine.profile("Writing").name = "Essays"
        self.assertEqual(
            self.merged({"Deep Work": "Deep Work", "Essays": "Writing"}).active_profile, "Essays"
        )

    def test_a_deleted_profile_stays_deleted(self):
        self.theirs.profile("Writing").approved.append("app:Ulysses")
        self.mine.profiles = [p for p in self.mine.profiles if p.name != "Writing"]
        out = self.merged({"Deep Work": "Deep Work"})
        self.assertEqual(out.profile_names(), ["Deep Work"])

    def test_deleting_the_active_profile_moves_to_the_window_choice(self):
        self.mine.profiles = [p for p in self.mine.profiles if p.name != "Deep Work"]
        self.mine.active_profile = "Writing"
        out = self.merged({"Writing": "Writing"})
        self.assertEqual(out.active_profile, "Writing")

    def test_a_new_profile_is_added(self):
        self.mine.profiles.append(Profile(name="Reading", approved=["app:Books"]))
        out = self.merged()
        self.assertEqual(out.profile("Reading").approved, ["app:Books"])

    def test_restoring_defaults_replaces_the_menus_rules(self):
        self.base.profile("Deep Work").approved.append("app:Figma")
        self.theirs = copy.deepcopy(self.base)
        self.theirs.profile("Deep Work").approved.append("site:example.com")
        self.mine = Config()
        out = self.merged({})
        self.assertEqual(out.to_dict(), Config().to_dict())

    def test_the_window_wins_a_setting_both_changed(self):
        self.theirs.general.poll_seconds = 5
        self.mine.general.poll_seconds = 3
        self.assertEqual(self.merged().general.poll_seconds, 3)

    def test_a_setting_the_window_left_alone_keeps_the_files_value(self):
        self.theirs.general.poll_seconds = 5
        self.mine.reminder.clock_penalty.fixed_seconds = 45
        out = self.merged()
        self.assertEqual(out.general.poll_seconds, 5)
        self.assertEqual(out.reminder.clock_penalty.fixed_seconds, 45)

    def test_first_run_finishing_meanwhile_is_kept(self):
        self.theirs.setup_complete = True
        self.mine.general.play_sound = not self.base.general.play_sound
        self.assertTrue(self.merged().setup_complete)

    def test_reading_never_creates_or_moves_a_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "config.json"
            self.assertIsNone(read_existing(missing))
            self.assertFalse(missing.exists())
            missing.write_text("{not json")
            self.assertIsNone(read_existing(missing))
            self.assertEqual(missing.read_text(), "{not json")


class ConfigMigrationTest(unittest.TestCase):
    def test_new_configs_check_in_after_a_minute_off_task(self):
        self.assertEqual(Config().reminder.distraction_grace_seconds, 60)

    def test_an_untouched_old_default_moves_to_the_new_one(self):
        cfg = Config.from_dict({"version": 1, "reminder": {"distraction_grace_seconds": 150}})
        self.assertEqual(cfg.reminder.distraction_grace_seconds, 60)
        self.assertEqual(cfg.version, 2)

    def test_a_chosen_value_survives_the_migration(self):
        cfg = Config.from_dict({"version": 1, "reminder": {"distraction_grace_seconds": 90}})
        self.assertEqual(cfg.reminder.distraction_grace_seconds, 90)

    def test_choosing_150_after_the_migration_sticks(self):
        cfg = Config.from_dict({"version": 2, "reminder": {"distraction_grace_seconds": 150}})
        self.assertEqual(cfg.reminder.distraction_grace_seconds, 150)

    def test_the_migration_is_written_back_once(self):
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"reminder": {"distraction_grace_seconds": 150}}))
            Config.load(path)
            saved = json.loads(path.read_text())
            self.assertEqual(saved["version"], 2)
            self.assertEqual(saved["reminder"]["distraction_grace_seconds"], 60)


class ShellInterfaceTest(unittest.TestCase):
    def test_tk_shell_implements_every_shell_callback(self):
        # The controller calls Shell methods unconditionally, so a shell that
        # does not inherit the defaults crashes the first time a new one lands.
        try:
            from ontask.ui.tk.shell import TkShell
        except ImportError as exc:  # pragma: no cover - no Tk on this machine
            self.skipTest(f"Tk unavailable: {exc}")
        from ontask.app import Shell

        self.assertTrue(issubclass(TkShell, Shell))


class IdleSamplingTest(unittest.TestCase):
    """Only a running session reads the frontmost window on every poll."""

    def setUp(self):
        from ontask.app import Controller

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "config.json"
        make_config().save(path)
        with mock.patch("ontask.app.get_provider") as provider:
            self.focus = provider.return_value
            self.focus.current.return_value = APPROVED_APP
            self.controller = Controller(config_path=path)

    def test_no_session_reads_nothing(self):
        self.controller.poll()
        self.controller.poll()
        self.focus.current.assert_not_called()

    def test_a_running_session_reads_every_poll(self):
        self.controller.start_session()
        self.controller.poll()
        self.controller.poll()
        self.assertEqual(self.focus.current.call_count, 2)
        self.assertEqual(self.controller.target, APPROVED_APP)

    def test_a_paused_session_reads_nothing(self):
        self.controller.start_session()
        self.controller.pause_or_resume()
        self.controller.poll()
        self.focus.current.assert_not_called()
        self.controller.pause_or_resume()
        self.controller.poll()
        self.assertEqual(self.focus.current.call_count, 1)

    def test_look_now_reads_once_even_when_idle(self):
        # What the menu does as it opens, so its focus line is always current.
        self.controller.look_now()
        self.assertEqual(self.focus.current.call_count, 1)
        self.assertIn("Code - approved", self.controller.current_status_text())

    def test_look_now_survives_a_failing_provider(self):
        self.focus.current.side_effect = RuntimeError("browser gone")
        self.controller.look_now()
        self.assertTrue(self.controller.target.is_unknown)

    def test_the_status_lines_show_focus_only_while_watching(self):
        self.controller.start_session()
        self.controller.poll()
        self.assertTrue(any(line.startswith("Focus:") for line in self.controller.status_lines()))
        self.controller.pause_or_resume()
        self.assertFalse(any(line.startswith("Focus:") for line in self.controller.status_lines()))


class WordingTest(unittest.TestCase):
    """What OnTask calls things on screen. It reminds; it never blocks anything."""

    def setUp(self):
        from ontask.app import Controller

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "config.json"
        make_config().save(self.path)
        with mock.patch("ontask.app.get_provider") as provider:
            self.focus = provider.return_value
            self.controller = Controller(shell=mock.Mock(), config_path=self.path)

    def _status_of(self, target):
        self.focus.current.return_value = target
        self.controller.look_now()
        return self.controller.current_status_text()

    def test_the_focus_line_names_each_status(self):
        self.assertTrue(self._status_of(APPROVED_APP).endswith(" - approved"))
        self.assertTrue(self._status_of(BLOCKED_SITE).endswith(" - disapproved"))
        self.assertTrue(self._status_of(UNLISTED_APP).endswith(" - not listed"))

    def test_the_check_in_names_the_disapproved_list(self):
        from ontask.core.engine import ActivePrompt

        question = ActivePrompt(kind=BLOCKED, target=BLOCKED_SITE, opened_at=0.0).question()
        self.assertIn("is on your disapproved list", question)
        self.assertNotIn("blocked", question.lower())

    def _run(self, verb):
        action = next(a for a in self.controller.rule_actions() if a.verb == verb)
        self.controller.run_rule_action(action)

    def test_disapproving_adds_the_rule_and_says_which_list(self):
        self._status_of(UNLISTED_APP)
        self._run("disapprove")
        self.assertIn("app:Messages", Config.load(self.path).profile("Test").disapproved)
        self.controller.shell.notify.assert_called_with(
            "OnTask", "Added Messages to the disapproved list for Test."
        )

    def test_approving_says_which_list(self):
        self._status_of(UNLISTED_APP)
        self._run("approve")
        self.controller.shell.notify.assert_called_with(
            "OnTask", "Added Messages to the approved list for Test."
        )

    def test_removing_takes_the_rule_off_and_says_which_list(self):
        self._status_of(BLOCKED_SITE)
        self._run("remove")
        self.assertNotIn("site:youtube.com", Config.load(self.path).profile("Test").disapproved)
        self.controller.shell.notify.assert_called_with(
            "OnTask", "Removed youtube.com from the disapproved list for Test."
        )
        self.assertTrue(self.controller.current_status_text().endswith(" - not listed"))

    def test_menu_titles_name_the_thing_and_the_list(self):
        self._status_of(APPROVED_APP)
        titles = [self.controller.rule_action_title(a) for a in self.controller.rule_actions()]
        self.assertEqual(titles, ["Disapprove Code", "Remove Code from Approved"])

    def test_the_actions_follow_a_list_change(self):
        self._status_of(UNLISTED_APP)
        self._run("approve")
        verbs = [a.verb for a in self.controller.rule_actions()]
        self.assertEqual(verbs, ["disapprove", "remove"])

    def test_the_settings_file_keeps_its_words(self):
        # Only the screen wording changed; existing config files must load as-is.
        saved = make_config().to_dict()
        self.assertIn("disapproved", saved["profiles"][0])
        self.assertIn("disapproved_grace_seconds", saved["reminder"])


def _actions(target, approved, disapproved):
    from ontask.core.matching import rule_actions

    return [(a.verb, a.rule, a.listname) for a in rule_actions(target, approved, disapproved)]


class RuleActionsTest(unittest.TestCase):
    """The menu offers only list changes that change the status of what's in front."""

    TRENDING = FocusTarget(
        app_name="Safari", bundle_id="com.apple.Safari", url="https://github.com/trending"
    )
    DOCS = FocusTarget(
        app_name="Safari", bundle_id="com.apple.Safari", url="https://docs.google.com/d/1"
    )

    def test_not_listed_offers_both_lists(self):
        self.assertEqual(
            _actions(UNLISTED_APP, [], []),
            [
                ("approve", "app:Messages", "approved"),
                ("disapprove", "app:Messages", "disapproved"),
            ],
        )

    def test_approved_offers_disapprove_and_remove(self):
        self.assertEqual(
            _actions(APPROVED_APP, ["app:Code"], []),
            [("disapprove", "app:Code", "disapproved"), ("remove", "app:Code", "approved")],
        )

    def test_disapproved_offers_approve_and_remove(self):
        self.assertEqual(
            _actions(BLOCKED_SITE, [], ["site:youtube.com"]),
            [
                ("approve", "site:youtube.com", "approved"),
                ("remove", "site:youtube.com", "disapproved"),
            ],
        )

    def test_a_more_specific_rule_is_moved_rather_than_adding_the_site(self):
        # Approving github.com would change nothing: the trending rule outranks it.
        actions = _actions(self.TRENDING, ["site:github.com"], ["site:github.com/trending"])
        self.assertEqual(
            actions,
            [
                ("approve", "site:github.com/trending", "approved"),
                ("remove", "site:github.com/trending", "disapproved"),
            ],
        )

    def test_a_broader_rule_offers_an_exception_or_the_whole_site(self):
        actions = _actions(self.DOCS, [], ["site:google.com"])
        self.assertEqual(
            actions,
            [
                ("approve", "site:docs.google.com", "approved"),
                ("approve", "site:google.com", "approved"),
                ("remove", "site:google.com", "disapproved"),
            ],
        )

    def test_it_works_the_same_from_the_approved_side(self):
        actions = _actions(self.DOCS, ["site:google.com"], [])
        self.assertEqual(
            [(verb, rule) for verb, rule, _ in actions],
            [
                ("disapprove", "site:docs.google.com"),
                ("disapprove", "site:google.com"),
                ("remove", "site:google.com"),
            ],
        )

    def test_an_app_rule_deciding_a_site_offers_the_site_or_the_app(self):
        actions = _actions(BLOCKED_SITE, [], ["app:Safari"])
        self.assertEqual(
            [(verb, rule) for verb, rule, _ in actions],
            [("approve", "site:youtube.com"), ("approve", "app:Safari"), ("remove", "app:Safari")],
        )

    def test_an_approved_browser_offers_nothing_about_the_browser(self):
        actions = _actions(BLOCKED_SITE, ["app:Safari"], [])
        self.assertEqual(actions, [("disapprove", "site:youtube.com", "disapproved")])

    def test_the_same_rule_written_differently_is_offered_once(self):
        google = FocusTarget(app_name="Safari", url="https://www.google.com/search")
        for written in ("google.com", "site:https://google.com", "site:google.com"):
            with self.subTest(written=written):
                self.assertEqual(
                    _actions(google, [], [written]),
                    [
                        ("approve", written, "approved"),
                        ("remove", written, "disapproved"),
                    ],
                )

    def test_an_approved_rule_written_differently_is_offered_once(self):
        # A tie goes to the disapproved list, so here both spellings would work.
        # "app:code" is the suggested "app:Code" with different capitals.
        self.assertEqual(
            _actions(APPROVED_APP, ["app:code"], []),
            [("disapprove", "app:code", "disapproved"), ("remove", "app:code", "approved")],
        )

    def test_pages_are_offered_only_when_a_page_rule_decided(self):
        page = FocusTarget(app_name="Safari", url="https://reddit.com/r/python/comments/1")
        rules = [rule for _, rule, _ in _actions(page, [], ["site:reddit.com"])]
        self.assertEqual(rules, ["site:reddit.com", "site:reddit.com"])

    def test_remove_names_the_rule_that_decided(self):
        actions = _actions(self.TRENDING, ["site:github.com"], [])
        self.assertIn(("remove", "site:github.com", "approved"), actions)

    def test_every_offered_change_flips_the_status(self):
        from ontask.core.matching import classify, moved_rule, rule_actions

        cases = [
            (self.TRENDING, ["site:github.com"], ["site:github.com/trending"]),
            (self.DOCS, [], ["site:google.com"]),
            (BLOCKED_SITE, ["app:Safari"], ["site:youtube.com"]),
            (BLOCKED_SITE, ["site:youtube.com/watch"], []),
            (APPROVED_APP, ["app:com.microsoft.VSCode"], []),
        ]
        for target, approved, disapproved in cases:
            before = classify(target, approved, disapproved).status
            for action in rule_actions(target, approved, disapproved):
                if action.verb == "remove":
                    continue
                after = classify(
                    target, *moved_rule(approved, disapproved, action.rule, action.listname)
                )
                self.assertNotEqual(after.status, before, (target, action))

    def test_no_switch_is_offered_when_none_would_work(self):
        # Two equally specific rules: moving either leaves a tie, and a tie
        # goes to the disapproved list.
        actions = _actions(APPROVED_APP, [], ["app:code", "app:Code"])
        self.assertEqual([verb for verb, _, _ in actions], ["remove"])

    def test_nothing_detected_offers_nothing(self):
        self.assertEqual(_actions(FocusTarget(), ["app:Code"], []), [])


class FriendlyNameTest(unittest.TestCase):
    def _name(self, rule, target=None):
        from ontask.core.matching import friendly_name

        return friendly_name(rule, target)

    def test_prefixes_and_schemes_are_dropped(self):
        self.assertEqual(self._name("app:Messages"), "Messages")
        self.assertEqual(self._name("site:youtube.com"), "youtube.com")
        self.assertEqual(self._name("site:https://reddit.com/r/python"), "reddit.com/r/python")
        self.assertEqual(self._name("Slack"), "Slack")

    def test_a_bundle_id_rule_names_the_app_it_matched(self):
        self.assertEqual(self._name("app:com.microsoft.VSCode", APPROVED_APP), "Code")
        self.assertEqual(self._name("app:com.microsoft.VSCode"), "com.microsoft.VSCode")

    def test_wildcards_stay_visible(self):
        self.assertEqual(self._name("app:Cod*", APPROVED_APP), "Cod*")


if __name__ == "__main__":
    unittest.main(verbosity=2)
