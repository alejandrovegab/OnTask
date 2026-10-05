"""Timing and rule tests. Everything runs on a fake clock, so it is instant."""

import unittest
from pathlib import Path

from ontask.core.config import Config, Profile
from ontask.core.engine import (
    BLOCKED,
    CADENCE,
    DISTRACTION,
    Answered,
    Engine,
    RealertPrompt,
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
