from __future__ import annotations

from typing import TYPE_CHECKING

from iriai_compose import (
    DefaultWorkflowRunner,
    Feature,
    Workflow,
)
from pydantic import BaseModel

if TYPE_CHECKING:
    from ..storage.features import PostgresFeatureStore


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
