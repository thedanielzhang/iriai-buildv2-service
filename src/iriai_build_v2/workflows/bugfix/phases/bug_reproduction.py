from __future__ import annotations

import logging

from iriai_compose import Ask, Feature, Phase, WorkflowRunner, to_str

from ....models.outputs import ReproductionResult
from ....models.state import BugFixState
from ....roles import bug_reproducer

logger = logging.getLogger(__name__)


class BugReproductionPhase(Phase):
    name = "bug-reproduction"

    async def execute(
        self, runner: WorkflowRunner, feature: Feature, state: BugFixState
    ) -> BugFixState:
        result: ReproductionResult = await runner.run(
            Ask(
                actor=bug_reproducer,
                prompt=(
                    f"Reproduce the following bug against the preview environment.\n\n"
                    f"## Preview URL\n{state.preview_url}\n\n"
                    f"## Bug Report\n{state.bug_report}\n\n"
                    "Follow the reproduction steps using all available channels: "
                    "browser (Playwright), API calls (curl), database queries (Postgres), "
                    "and deployment status checks (Preview MCP). "
                    "Record your observations at every step."
                ),
                output_type=ReproductionResult,
            ),
            feature,
            phase_name=self.name,
        )

        reproduction_text = to_str(result)
        await runner.artifacts.put("reproduction", reproduction_text, feature=feature)
        state.reproduction = reproduction_text

        if not result.reproduced:
            logger.warning(
                "Bug could not be reproduced. Continuing anyway — "
                "root cause agents may find issues in code."
            )

        return state
