from __future__ import annotations

from typing import TYPE_CHECKING

from iriai_compose import (
    DefaultWorkflowRunner,
    Feature,
    Phase,
    Workflow,
)
from pydantic import BaseModel

from .phases import (
    ApprovalPhase,
    ArchitecturePhase,
    BaselinePhase,
    BugIntakePhase,
    BugReproductionPhase,
    CleanupPhase,
    DesignPhase,
    DiagnosisAndFixPhase,
    EnvironmentSetupPhase,
    ImplementationPhase,
    PMPhase,
    PlanReviewPhase,
    RegressionPhase,
    TaskPlanningPhase,
)

if TYPE_CHECKING:
    from .storage.features import PostgresFeatureStore


class PlanningWorkflow(Workflow):
    name = "planning"

    def build_phases(self) -> list[type[Phase]]:
        return [PMPhase, DesignPhase, ArchitecturePhase, PlanReviewPhase, TaskPlanningPhase]


class FullBuildWorkflow(Workflow):
    name = "full-build"

    def build_phases(self) -> list[type[Phase]]:
        return [PMPhase, DesignPhase, ArchitecturePhase, PlanReviewPhase, TaskPlanningPhase, ImplementationPhase]


class BugFixWorkflow(Workflow):
    name = "bugfix"

    def build_phases(self) -> list[type[Phase]]:
        return [
            BugIntakePhase,
            EnvironmentSetupPhase,
            BaselinePhase,
            BugReproductionPhase,
            DiagnosisAndFixPhase,
            RegressionPhase,
            ApprovalPhase,
            CleanupPhase,
        ]


class TrackedWorkflowRunner(DefaultWorkflowRunner):
    """Extends DefaultWorkflowRunner to log phase transitions to Postgres."""

    def __init__(
        self,
        *,
        feature_store: PostgresFeatureStore,
        services: dict | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(services=services, **kwargs)  # type: ignore[arg-type]
        self.feature_store = feature_store

    async def execute_workflow(
        self,
        workflow: Workflow,
        feature: Feature,
        state: BaseModel,
    ) -> BaseModel:
        for phase_cls in workflow.build_phases():
            phase = phase_cls()
            await self.feature_store.transition_phase(feature.id, phase.name)
            state = await phase.execute(self, feature, state)
        await self.feature_store.transition_phase(feature.id, "complete")
        return state
