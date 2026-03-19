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
        await workflow.on_start(self, feature, state)
        try:
            for phase_cls in workflow.build_phases():
                phase = phase_cls()
                await self.feature_store.transition_phase(feature.id, phase.name)
                await phase.on_start(self, feature, state)
                try:
                    state = await phase.execute(self, feature, state)
                except Exception:
                    await phase.on_done(self, feature, state)
                    raise
                await phase.on_done(self, feature, state)
            await self.feature_store.transition_phase(feature.id, "complete")
        except Exception:
            await workflow.on_done(self, feature, state)
            raise
        await workflow.on_done(self, feature, state)
        return state

    async def resume_workflow(
        self,
        workflow: Workflow,
        feature: Feature,
        state: BaseModel,
        *,
        resume_from_phase: str,
    ) -> BaseModel:
        """Resume a workflow, skipping phases before *resume_from_phase*."""
        phases = workflow.build_phases()
        phase_names = [cls().name for cls in phases]

        if resume_from_phase not in phase_names:
            raise RuntimeError(
                f"Cannot resume: phase '{resume_from_phase}' not found. "
                f"Valid phases: {phase_names}"
            )

        resume_idx = phase_names.index(resume_from_phase)

        await workflow.on_start(self, feature, state)

        # Re-host artifacts from prior phases so browser review URLs work
        hosting = self.services.get("hosting")
        if hosting and hasattr(hosting, "rehost_existing"):
            try:
                count = await hosting.rehost_existing(
                    feature.id, label_prefix=f"{feature.name} — ",
                )
                if count:
                    await self.feature_store.log_event(
                        feature.id, "artifacts_rehosted", "resume", str(count),
                    )
            except Exception:
                import logging
                logging.getLogger(__name__).warning(
                    "Failed to re-host artifacts for %s", feature.id, exc_info=True,
                )

        try:
            for i, phase_cls in enumerate(phases):
                phase = phase_cls()
                if i < resume_idx:
                    await self.feature_store.log_event(
                        feature.id, "phase_skipped", "resume", phase.name
                    )
                    continue

                await self.feature_store.transition_phase(feature.id, phase.name)
                await phase.on_start(self, feature, state)
                try:
                    state = await phase.execute(self, feature, state)
                except Exception:
                    await phase.on_done(self, feature, state)
                    raise
                await phase.on_done(self, feature, state)
            await self.feature_store.transition_phase(feature.id, "complete")
        except Exception:
            await workflow.on_done(self, feature, state)
            raise
        await workflow.on_done(self, feature, state)
        return state
