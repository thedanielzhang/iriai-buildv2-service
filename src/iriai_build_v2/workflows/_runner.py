from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from iriai_compose import (
    DefaultWorkflowRunner,
    Feature,
    Workflow,
)
from iriai_compose.actors import AgentActor
from pydantic import BaseModel

if TYPE_CHECKING:
    from iriai_compose.runner import AgentRuntime

    from ..storage.features import PostgresFeatureStore

logger = logging.getLogger(__name__)


class TrackedWorkflowRunner(DefaultWorkflowRunner):
    """Extends DefaultWorkflowRunner to log phase transitions to Postgres.

    Supports an optional **secondary_runtime** for adversarial multi-model
    execution.  Roles with ``metadata["runtime"] == "secondary"`` are routed
    to the secondary runtime; all others use the primary (default) runtime.
    """

    def __init__(
        self,
        *,
        feature_store: PostgresFeatureStore,
        secondary_runtime: AgentRuntime | None = None,
        services: dict | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(services=services, **kwargs)  # type: ignore[arg-type]
        self.feature_store = feature_store
        self.secondary_runtime = secondary_runtime

    # ── Multi-runtime routing ───────────────────────────────────────

    async def resolve(
        self,
        actor: Any,
        prompt: str,
        *,
        feature: Feature,
        context_keys: list[str] | None = None,
        output_type: type[BaseModel] | None = None,
        kind: Literal["approve", "choose", "respond"] | None = None,
        options: list[str] | None = None,
        continuation: bool = False,
    ) -> Any:
        # ── Workspace resolution ─────────────────────────────────────
        # Priority:
        # 1. Per-actor metadata "workspace_override" (most specific — e.g., repos/iriai-compose/)
        # 2. Phase-level "worktree_root" service (set once by implementation phase — repos/)
        # 3. Default runner workspace (main workspace — only for non-implementation phases)
        workspace_override = None
        original_workspace = None
        if isinstance(actor, AgentActor):
            from iriai_compose import Workspace

            ws_path = actor.role.metadata.get("workspace_override")
            if not ws_path:
                worktree_root = self.services.get("worktree_root")
                if worktree_root:
                    ws_path = str(worktree_root)

            if ws_path:
                original_workspace = self._workspaces.get(feature.workspace_id)
                workspace_override = Workspace(
                    id=feature.workspace_id,
                    path=Path(ws_path),
                )
                self._workspaces[feature.workspace_id] = workspace_override

        try:
            # ── Secondary runtime routing ───────────────────────────
            if (
                isinstance(actor, AgentActor)
                and self.secondary_runtime
                and actor.role.metadata.get("runtime") == "secondary"
            ):
                runtime_name = getattr(self.secondary_runtime, "name", "secondary")
                logger.info(
                    "Routing %s to secondary runtime (%s)",
                    actor.name, runtime_name,
                )
                primary = self.agent_runtime
                self.agent_runtime = self.secondary_runtime
                try:
                    return await super().resolve(
                        actor, prompt,
                        feature=feature,
                        context_keys=context_keys,
                        output_type=output_type,
                        kind=kind,
                        options=options,
                        continuation=continuation,
                    )
                finally:
                    self.agent_runtime = primary

            return await super().resolve(
                actor, prompt,
                feature=feature,
                context_keys=context_keys,
                output_type=output_type,
                kind=kind,
                options=options,
                continuation=continuation,
            )
        finally:
            # Restore original workspace
            if original_workspace is not None:
                self._workspaces[feature.workspace_id] = original_workspace
            elif workspace_override is not None:
                self._workspaces.pop(feature.workspace_id, None)

    # ── Workflow execution with phase tracking ──────────────────────

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
                logger.warning(
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
