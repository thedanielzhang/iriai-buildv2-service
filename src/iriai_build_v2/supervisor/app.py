from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from .actions import ActionPolicy
from .classifier import classify_observation
from .evidence import ArtifactStoreReader, DashboardClient, FeatureStoreReader, collect_evidence
from .models import (
    EvidencePacket,
    SupervisorDecision,
    SupervisorMode,
    decision_key,
    observation_key,
)
from .tools import SupervisorEvidenceToolbox


class SupervisorApp:
    def __init__(
        self,
        *,
        feature_store: FeatureStoreReader,
        artifact_store: ArtifactStoreReader | Any,
        mode: SupervisorMode = SupervisorMode.READ_ONLY,
        dashboard_url: str | None = None,
        dashboard_client: DashboardClient | None = None,
        worktree_roots: list[str | Path] | None = None,
        forbidden_paths: list[str] | None = None,
        action_policy: ActionPolicy | None = None,
    ) -> None:
        self.feature_store = feature_store
        self.artifact_store = artifact_store
        self.mode = mode
        self.dashboard_url = dashboard_url
        self.dashboard_client = dashboard_client
        self.worktree_roots = worktree_roots or []
        self.forbidden_paths = forbidden_paths or []
        self.action_policy = action_policy

    def evidence_toolbox(self, feature_id: str) -> SupervisorEvidenceToolbox:
        return SupervisorEvidenceToolbox(
            feature_id=feature_id,
            feature_store=self.feature_store,
            artifact_store=self.artifact_store,
            dashboard_url=self.dashboard_url,
            dashboard_client=self.dashboard_client,
            worktree_roots=self.worktree_roots,
            forbidden_paths=self.forbidden_paths,
        )

    async def run_once(
        self,
        *,
        feature_id: str,
        cursor: int = 0,
        event_cursor: int | None = None,
        artifact_cursor: int | None = None,
        bridge_log_cursor: int = 0,
        persist: bool = True,
    ) -> EvidencePacket:
        feature = await self.feature_store.get_feature(feature_id)
        observation = await collect_evidence(
            feature_id=feature_id,
            feature_store=self.feature_store,
            artifact_store=self.artifact_store,
            cursor=cursor,
            event_cursor=event_cursor,
            artifact_cursor=artifact_cursor,
            dashboard_url=self.dashboard_url,
            dashboard_client=self.dashboard_client,
            bridge_log_cursor=bridge_log_cursor,
            worktree_roots=self.worktree_roots,
            forbidden_paths=self.forbidden_paths,
        )
        packet = classify_observation(observation)
        if persist and feature is not None and hasattr(self.artifact_store, "put"):
            observation_artifact_key = observation_key(
                feature_id,
                observation.next_cursor,
                event_cursor=observation.next_event_cursor,
                artifact_cursor=observation.next_artifact_cursor,
                bridge_log_cursor=observation.bridge_log_cursor,
                observed_at=observation.observed_at,
            )
            decision_artifact_key = decision_key(
                feature_id,
                observation.next_cursor,
                event_cursor=observation.next_event_cursor,
                artifact_cursor=observation.next_artifact_cursor,
                bridge_log_cursor=observation.bridge_log_cursor,
                observed_at=observation.observed_at,
            )
            await self.artifact_store.put(
                observation_artifact_key,
                observation.model_dump_json(),
                feature=feature,
            )
            decision = SupervisorDecision(
                feature_id=feature_id,
                cursor=observation.next_cursor,
                observation_key=observation_artifact_key,
                packet=packet,
            )
            await self.artifact_store.put(
                decision_artifact_key,
                decision.model_dump_json(),
                feature=feature,
            )
        return packet

    async def watch(
        self,
        *,
        feature_id: str,
        interval_seconds: float = 5.0,
        start_cursor: int = 0,
    ) -> None:
        event_cursor = start_cursor
        artifact_cursor = start_cursor
        while True:
            packet = await self.run_once(
                feature_id=feature_id,
                event_cursor=event_cursor,
                artifact_cursor=artifact_cursor,
            )
            event_cursor = int(packet.facts.get("next_event_cursor") or event_cursor)
            artifact_cursor = int(packet.facts.get("next_artifact_cursor") or artifact_cursor)
            if self.action_policy is not None:
                await self.action_policy.maybe_restart(packet)
            await asyncio.sleep(interval_seconds)
