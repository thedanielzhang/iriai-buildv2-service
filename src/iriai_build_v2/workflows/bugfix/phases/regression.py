from __future__ import annotations

import logging

from iriai_compose import Ask, Feature, Phase, WorkflowRunner, to_str

from ....models.outputs import Verdict
from ....models.state import BugFixState
from ....roles import smoke_tester
from ....tasks.playwright import RunE2ETestTask

logger = logging.getLogger(__name__)


class RegressionPhase(Phase):
    name = "regression"

    async def execute(
        self, runner: WorkflowRunner, feature: Feature, state: BugFixState
    ) -> BugFixState:
        results: list[str] = []

        # Re-run e2e tests (same as baseline)
        workspace = runner._workspaces["main"].path
        test_dirs = ["tests/e2e", "e2e", "tests"]
        test_dir = next(
            (d for d in test_dirs if (workspace / d).is_dir()),
            None,
        )

        if test_dir and state.preview_url:
            try:
                e2e_result = await runner.run(
                    RunE2ETestTask(
                        test_dir=test_dir,
                        base_url=state.preview_url,
                    ),
                    feature,
                    phase_name=self.name,
                )
                results.append(
                    f"E2E Tests (post-fix): {e2e_result.passed} passed, "
                    f"{e2e_result.failed} failed, {e2e_result.errors} errors\n"
                    f"Summary: {e2e_result.summary}"
                )
            except Exception as exc:
                logger.warning("E2E tests failed to run: %s", exc)
                results.append(f"E2E Tests (post-fix): failed to run ({exc})")
        else:
            results.append("E2E Tests (post-fix): no test directory found or no preview URL")

        # Re-run smoke test
        if state.preview_url:
            smoke_verdict: Verdict = await runner.run(
                Ask(
                    actor=smoke_tester,
                    prompt=(
                        f"Run a smoke test of the preview at {state.preview_url}. "
                        "Check that the application loads, key pages render, and there "
                        "are no obvious errors. This is a regression check after a bug fix."
                    ),
                    output_type=Verdict,
                ),
                feature,
                phase_name=self.name,
            )
            results.append(f"Smoke Test (post-fix):\n{to_str(smoke_verdict)}")

        # Compare against baseline
        regression_text = "\n\n".join(results)
        comparison = (
            f"## Baseline (before fix)\n{state.baseline}\n\n"
            f"## Post-Fix Results\n{regression_text}\n\n"
            "## Analysis\n"
            "Compare baseline vs post-fix results above. "
            "Previously passing tests that now fail are REGRESSIONS. "
            "Previously failing tests that now pass are IMPROVEMENTS."
        )

        await runner.artifacts.put("regression", comparison, feature=feature)
        state.regression = comparison

        logger.info("Regression check complete")
        return state
