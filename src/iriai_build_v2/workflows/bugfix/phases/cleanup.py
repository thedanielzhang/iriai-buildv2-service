from __future__ import annotations

import logging

from iriai_compose import Feature, Phase, WorkflowRunner

from ....models.state import BugFixState
from ....tasks.preview import CleanupMode, CleanupPreviewTask

logger = logging.getLogger(__name__)


class CleanupPhase(Phase):
    name = "cleanup"

    async def execute(
        self, runner: WorkflowRunner, feature: Feature, state: BugFixState
    ) -> BugFixState:
        if not state.project:
            logger.info("No project set, skipping cleanup")
            return state

        try:
            await runner.run(
                CleanupPreviewTask(
                    project=state.project,
                    mode=CleanupMode.TEARDOWN,
                ),
                feature,
                phase_name=self.name,
            )
            logger.info("Preview environment torn down for project=%s", state.project)
        except Exception as exc:
            logger.warning("Preview cleanup failed: %s", exc)

        return state
