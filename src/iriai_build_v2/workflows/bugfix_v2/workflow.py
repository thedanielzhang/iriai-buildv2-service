from __future__ import annotations

from iriai_compose import Phase

from ..planning.workflow import PlanningWorkflow
from .phases import BugflowQueuePhase, BugflowSetupPhase


class BugFixV2Workflow(PlanningWorkflow):
    name = "bugfix-v2"

    def build_phases(self) -> list[type[Phase]]:
        return [BugflowSetupPhase, BugflowQueuePhase]
