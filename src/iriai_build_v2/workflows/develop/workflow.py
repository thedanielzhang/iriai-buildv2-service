from __future__ import annotations

from iriai_compose import Phase, Workflow

from ..planning.phases import (
    ArchitecturePhase,
    DesignPhase,
    PMPhase,
    PlanReviewPhase,
    TaskPlanningPhase,
)
from .phases import ImplementationPhase


class FullDevelopWorkflow(Workflow):
    name = "full-develop"

    def build_phases(self) -> list[type[Phase]]:
        return [PMPhase, DesignPhase, ArchitecturePhase, PlanReviewPhase, TaskPlanningPhase, ImplementationPhase]
