from __future__ import annotations

from iriai_compose import Phase

from ..planning.workflow import PlanningWorkflow
from .phases import ImplementationPhase


class FullDevelopWorkflow(PlanningWorkflow):
    name = "full-develop"

    def build_phases(self) -> list[type[Phase]]:
        return super().build_phases() + [ImplementationPhase]
