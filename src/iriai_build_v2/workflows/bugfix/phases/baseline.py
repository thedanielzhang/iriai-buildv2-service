from __future__ import annotations

import json
import logging
from pathlib import Path

from iriai_compose import Ask, Feature, Phase, WorkflowRunner, to_str

from ....models.outputs import Verdict
from ....models.state import BugFixState
from ....roles import smoke_tester
from ....tasks.playwright import RunE2ETestTask

logger = logging.getLogger(__name__)


class BaselinePhase(Phase):
    name = "baseline"

    async def execute(
        self, runner: WorkflowRunner, feature: Feature, state: BugFixState
    ) -> BugFixState:
        results: list[str] = []

        # Run existing e2e tests if they exist
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
                    f"E2E Tests: {e2e_result.passed} passed, "
                    f"{e2e_result.failed} failed, {e2e_result.errors} errors\n"
                    f"Summary: {e2e_result.summary}"
                )
            except Exception as exc:
                logger.warning("E2E tests failed to run: %s", exc)
                results.append(f"E2E Tests: failed to run ({exc})")
        else:
            results.append("E2E Tests: no test directory found or no preview URL")

        # Quick smoke test of the preview
        if state.preview_url:
            smoke_verdict: Verdict = await runner.run(
                Ask(
                    actor=smoke_tester,
                    prompt=(
                        f"Run a quick smoke test of the preview at {state.preview_url}. "
                        "Check that the application loads, key pages render, and there "
                        "are no obvious errors. This is a baseline before a bug fix."
                    ),
                    output_type=Verdict,
                ),
                feature,
                phase_name=self.name,
            )
            results.append(f"Smoke Test:\n{to_str(smoke_verdict)}")

        baseline_text = "\n\n".join(results)
        await runner.artifacts.put("baseline", baseline_text, feature=feature)
        state.baseline = baseline_text

        logger.info("Baseline captured: %s", baseline_text[:200])
        return state
