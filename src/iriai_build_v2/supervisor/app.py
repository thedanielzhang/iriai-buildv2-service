from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any

from .actions import ActionPolicy
from .classifier import classify_observation
from .evidence import ArtifactStoreReader, DashboardClient, FeatureStoreReader, collect_evidence
from .models import (
    EvidencePacket,
    SupervisorArtifactRef,
    SupervisorBridgeDigest,
    SupervisorDbPressureDigest,
    SupervisorDecision,
    SupervisorMode,
    SupervisorObservation,
    SupervisorObservationDigest,
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
                compact_observation_digest(observation).model_dump_json(),
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


_MAX_PERSISTED_OBSERVATION_BYTES = 64 * 1024
_MAX_DIGEST_ARTIFACT_REFS = 80
_MAX_DIGEST_EVENT_REFS = 80
_MAX_DIGEST_LOG_LINES = 40
_MAX_DIGEST_ERROR_LINES = 20
_MAX_DIGEST_STALE_INVOCATIONS = 5
_MAX_DIGEST_LINE_CHARS = 700
_LOW_DISK_PRESSURE_BYTES = 5 * 1024 * 1024 * 1024


def compact_observation_digest(
    observation: SupervisorObservation,
) -> SupervisorObservationDigest:
    digest = _observation_digest(
        observation,
        artifact_limit=_MAX_DIGEST_ARTIFACT_REFS,
        event_limit=_MAX_DIGEST_EVENT_REFS,
        log_limit=_MAX_DIGEST_LOG_LINES,
        error_limit=_MAX_DIGEST_ERROR_LINES,
    )
    if _json_size(digest) <= _MAX_PERSISTED_OBSERVATION_BYTES:
        return digest
    digest = _observation_digest(
        observation,
        artifact_limit=25,
        event_limit=25,
        log_limit=12,
        error_limit=8,
        truncated=True,
    )
    if _json_size(digest) <= _MAX_PERSISTED_OBSERVATION_BYTES:
        return digest
    return _observation_digest(
        observation,
        artifact_limit=8,
        event_limit=8,
        log_limit=4,
        error_limit=4,
        truncated=True,
        minimal=True,
    )


def _observation_digest(
    observation: SupervisorObservation,
    *,
    artifact_limit: int,
    event_limit: int,
    log_limit: int,
    error_limit: int,
    truncated: bool = False,
    minimal: bool = False,
) -> SupervisorObservationDigest:
    artifacts = sorted(
        observation.artifacts,
        key=lambda artifact: artifact.id or 0,
        reverse=True,
    )
    events = sorted(
        observation.events,
        key=lambda event: event.id or 0,
        reverse=True,
    )
    bridge = _bridge_digest(
        observation,
        log_limit=log_limit,
        error_limit=error_limit,
        minimal=minimal,
    )
    return SupervisorObservationDigest(
        feature_id=observation.feature_id,
        phase=observation.phase,
        observed_at=observation.observed_at,
        event_cursor=observation.event_cursor,
        next_event_cursor=observation.next_event_cursor,
        artifact_cursor=observation.artifact_cursor,
        next_artifact_cursor=observation.next_artifact_cursor,
        bridge_log_cursor=observation.bridge_log_cursor,
        cursor=observation.cursor,
        next_cursor=observation.next_cursor,
        current=observation.current,
        artifact_refs=[
            SupervisorArtifactRef(
                id=artifact.id,
                key=artifact.key,
                citation=artifact.citation,
                created_at=artifact.created_at,
                stored_bytes=artifact.stored_bytes,
                summary_only=artifact.summary_only,
            )
            for artifact in artifacts[:artifact_limit]
        ],
        event_refs=[event.citation for event in events[:event_limit]],
        bridge=bridge,
        stale_codex_invocations=[
            {
                "actor": item.actor,
                "pid": item.pid,
                "child_pids": item.child_pids[:8],
                "group_idx": item.group_idx,
                "retry": item.retry,
                "trace_path": item.trace_path,
                "output_path": item.output_path,
                "elapsed_seconds": item.elapsed_seconds,
                "idle_seconds": item.idle_seconds,
                "evidence_token": item.evidence_token,
                "citations": item.citations[:8],
            }
            for item in observation.stale_codex_invocations[:_MAX_DIGEST_STALE_INVOCATIONS]
        ],
        db_pressure=_disk_pressure_digest(),
        truncated=(
            truncated
            or len(artifacts) > artifact_limit
            or len(events) > event_limit
        ),
        source_observation_artifact_count=len(observation.artifacts),
        source_observation_event_count=len(observation.events),
    )


def _bridge_digest(
    observation: SupervisorObservation,
    *,
    log_limit: int,
    error_limit: int,
    minimal: bool,
) -> SupervisorBridgeDigest | None:
    bridge = observation.bridge
    if bridge is None:
        return None
    status = dict(bridge.status or {})
    if minimal:
        status = {
            key: status.get(key)
            for key in ("running", "state", "process_state", "pid")
            if key in status
        }
    return SupervisorBridgeDigest(
        ok=bridge.ok,
        process_state=bridge.process_state,
        status=status,
        log_cursor=bridge.log_cursor,
        recent_log_lines=[
            _shorten_line(line) for line in list(bridge.log_lines)[-log_limit:]
        ],
        recent_errors=[
            _shorten_line(line) for line in list(bridge.errors)[-error_limit:]
        ],
        truncated_log_line_count=bridge.truncated_log_line_count
        + max(0, len(bridge.log_lines) - log_limit),
        truncated_error_count=bridge.truncated_error_count
        + max(0, len(bridge.errors) - error_limit),
    )


def _disk_pressure_digest(path: str = "/") -> SupervisorDbPressureDigest:
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return SupervisorDbPressureDigest(path=path, pressure=True)
    return SupervisorDbPressureDigest(
        free_bytes=usage.free,
        total_bytes=usage.total,
        used_bytes=usage.used,
        path=path,
        pressure=usage.free < _LOW_DISK_PRESSURE_BYTES,
    )


def _json_size(value: SupervisorObservationDigest) -> int:
    return len(value.model_dump_json().encode("utf-8"))


def _shorten_line(value: Any) -> str:
    text = " ".join(str(value).split())
    if len(text) <= _MAX_DIGEST_LINE_CHARS:
        return text
    return f"{text[: _MAX_DIGEST_LINE_CHARS - 16]}... [truncated]"
