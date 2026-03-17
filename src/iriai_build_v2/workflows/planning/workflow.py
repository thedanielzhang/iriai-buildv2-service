from __future__ import annotations

from iriai_compose import Phase, Workflow

from .phases import (
    ArchitecturePhase,
    DesignPhase,
    PMPhase,
    PlanReviewPhase,
    TaskPlanningPhase,
)


class PlanningWorkflow(Workflow):
    name = "planning"

    def build_phases(self) -> list[type[Phase]]:
        return [PMPhase, DesignPhase, ArchitecturePhase, PlanReviewPhase, TaskPlanningPhase]
