"""OnTask's every-few-seconds check stays cheap. See perf_poll.py for what is measured."""

import tempfile
import unittest
from pathlib import Path

import perf_poll
from perf_poll import BUDGET_MS, make_controller, measure, time_polls

from ontask.core.engine import RUNNING

# A shared CI machine can stall for a whole run. A slow result is measured
# again before it counts, so only a check that is slow every time fails.
ATTEMPTS = 3


class PollBudgetTest(unittest.TestCase):
    def assert_within_budget(self, session: bool) -> None:
        for _ in range(ATTEMPTS):
            timing = measure(session)
            if timing.median_ms < BUDGET_MS:
                return
        self.fail(f"poll() is over its {BUDGET_MS} ms budget: {timing}")

    def test_a_poll_in_session_is_within_budget(self):
        self.assert_within_budget(session=True)

    def test_a_poll_with_no_session_is_within_budget(self):
        self.assert_within_budget(session=False)

    def test_the_benchmark_exercises_focus_and_rules(self):
        # Guards the measurement itself: a poll that stopped asking for focus,
        # or a profile that lost its rules, would pass the budget for nothing.
        with tempfile.TemporaryDirectory() as folder:
            controller = make_controller(Path(folder), session=True)
            time_polls(controller, polls=10, warmup=0)
        self.assertEqual(controller.focus.calls, 10)
        profile = controller.config.profile()
        self.assertEqual(profile.name, "Benchmark")
        self.assertEqual(len(profile.approved), perf_poll.RULES_PER_LIST)
        self.assertEqual(len(profile.disapproved), perf_poll.RULES_PER_LIST)
        self.assertEqual(controller.engine.phase, RUNNING)


if __name__ == "__main__":
    unittest.main(verbosity=2)
