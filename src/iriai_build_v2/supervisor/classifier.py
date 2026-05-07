from __future__ import annotations

import json
import re
from typing import Any

from .models import (
    ActionLevel,
    ArtifactRecord,
    ClassificationResult,
    EvidencePacket,
    EventRecord,
    FailureClass,
    SupervisorObservation,
)


def classify_observation(observation: SupervisorObservation) -> EvidencePacket:
    classifier = SupervisorClassifier()
    return classifier.classify(observation)


class SupervisorClassifier:
    def classify(self, observation: SupervisorObservation) -> EvidencePacket:
        context = _Context(observation)
        result = (
            self._operator_required(context)
            or self._pipeline_bug_suspected(context)
            or self._deterministic_unblock(context)
            or self._safe_restart_candidate(context)
            or self._normal_product_repair(context)
            or self._healthy_progress(context)
            or self._watch_only(context)
        )
        return EvidencePacket(**result.model_dump())

    def _operator_required(self, context: "_Context") -> ClassificationResult | None:
        embedded = [
            path
            for worktree in context.observation.worktrees
            for path in worktree.embedded_git_paths
        ]
        gitlinks = [path for worktree in context.observation.worktrees for path in worktree.gitlinks]
        forbidden = [
            fact.model_dump()
            for worktree in context.observation.worktrees
            for fact in worktree.forbidden_paths
        ]
        pending = [path for worktree in context.observation.worktrees for path in worktree.pending_paths]
        proposed = [path for worktree in context.observation.worktrees for path in worktree.proposed_paths]
        unwritable = [
            path for worktree in context.observation.worktrees for path in worktree.unwritable_paths
        ]
        if not any((embedded, gitlinks, forbidden, pending, proposed, unwritable)):
            return None
        facts = {
            "embedded_git_paths": embedded,
            "gitlinks": gitlinks,
            "forbidden_paths": forbidden,
            "pending_paths": pending,
            "proposed_paths": proposed,
            "unwritable_paths": unwritable,
        }
        return context.result(
            FailureClass.OPERATOR_REQUIRED,
            0.9,
            facts=facts,
            inference=(
                "Worktree evidence shows repo hygiene or writeability conditions that product "
                "agents should not repair automatically."
            ),
            recommended_action=ActionLevel.STOP_ESCALATE,
            false_positive_checks=[
                "Staged deletions are ignored unless the forbidden path still exists or is staged as an add.",
                "Direct feature repo roots are excluded from embedded .git detection.",
            ],
            citations=context.worktree_citations(),
        )

    def _pipeline_bug_suspected(self, context: "_Context") -> ClassificationResult | None:
        failed_raw = [
            artifact
            for artifact in context.group_artifacts
            if artifact.key.startswith(("dag-verify:", "dag-repair-preflight:"))
            and _looks_failed(artifact.value)
        ]
        checkpoint_artifacts = [
            artifact
            for artifact in context.group_artifacts
            if artifact.key.startswith("dag-group:")
        ]
        checkpoint_events = [
            event
            for event in context.group_events
            if "checkpoint" in event.event_type.lower()
            or "checkpoint" in str(event.content or "").lower()
        ]
        if not failed_raw or not (checkpoint_artifacts or checkpoint_events):
            return None
        successful_gates = [
            artifact
            for artifact in context.group_artifacts
            if artifact.key.startswith(("dag-verify:", "dag-repair-preflight:"))
            and _looks_successful(artifact.value)
        ]
        latest_failed_id = max((artifact.id or 0) for artifact in failed_raw)
        latest_success_id = max(
            ((artifact.id or 0) for artifact in successful_gates),
            default=0,
        )
        if latest_success_id > latest_failed_id:
            return None
        citations = [artifact.citation for artifact in failed_raw[:3]]
        citations.extend(artifact.citation for artifact in checkpoint_artifacts[:3])
        citations.extend(event.citation for event in checkpoint_events[:3])
        return context.result(
            FailureClass.PIPELINE_BUG_SUSPECTED,
            0.95,
            facts={
                "failed_raw_artifacts": [artifact.citation for artifact in failed_raw],
                "checkpoint_artifacts": [artifact.citation for artifact in checkpoint_artifacts],
                "checkpoint_events": [event.citation for event in checkpoint_events],
            },
            inference=(
                "A raw verifier/preflight failure is followed by checkpoint/group evidence, "
                "which contradicts the gate authority described in the supervisor taxonomy."
            ),
            recommended_action=ActionLevel.STOP_ESCALATE,
            false_positive_checks=[
                "Requires both failed raw gate evidence and later checkpoint/group evidence.",
                "Does not treat repeated product verifier failures as a pipeline contradiction.",
            ],
            citations=citations,
        )

    def _safe_restart_candidate(self, context: "_Context") -> ClassificationResult | None:
        bridge = context.observation.bridge
        if bridge is None:
            return None
        active_agent_events = [
            event
            for event in context.observation.events
            if event.event_type in {"agent_start", "agent_invocation_start"}
        ]
        recent_done_events = [
            event
            for event in context.observation.events
            if event.event_type in {"agent_done", "agent_invocation_done"}
        ]
        active = (
            len(active_agent_events) > len(recent_done_events)
            or bool(context.observation.current and context.observation.current.active_agents)
        )
        dead_state = bridge.process_state in {"dead", "stopped", "crashed", "unreachable"}
        wedged = any(_bridge_line_suggests_restart(line) for line in bridge.errors)
        if not (dead_state or wedged):
            return None
        action = ActionLevel.RECOMMEND if active else ActionLevel.ACT_GUARDED
        return context.result(
            FailureClass.SAFE_RESTART_CANDIDATE,
            0.78 if active else 0.86,
            facts={
                "bridge_state": bridge.process_state,
                "bridge_errors": bridge.errors[:5],
                "active_agent_event_count": len(active_agent_events),
                "done_agent_event_count": len(recent_done_events),
                "current_active_agents": (
                    list(context.observation.current.active_agents)
                    if context.observation.current is not None
                    else []
                ),
            },
            inference=(
                "Bridge status/log evidence indicates a dead or wedged bridge; restart is "
                "safe only at a boundary or when no active invocation is evident."
            ),
            recommended_action=action,
            false_positive_checks=[
                "Slack/bridge noise is not treated as workflow truth.",
                "Active invocation evidence downgrades automatic action to recommendation.",
            ],
            citations=["dashboard:/api/bridge/status", "dashboard:/api/bridge/logs"],
        )

    def _deterministic_unblock(self, context: "_Context") -> ClassificationResult | None:
        commit_artifacts = [
            artifact
            for artifact in context.group_artifacts
            if artifact.key.startswith("dag-commit-failure:")
        ]
        commit_events = [
            event
            for event in context.group_events
            if event.event_type == "dag_commit_failed"
            or "dag_commit_failed" in str(event.content or "")
            or "WorkflowCommitError" in str(event.content or "")
        ]
        stale_artifacts = [
            artifact
            for artifact in context.group_artifacts
            if artifact.key.startswith(
                (
                    "dag-repair-preflight:",
                    "dag-authority-gate:",
                    "dag-task-reconcile:",
                    "dag-task-spec-reconcile:",
                )
            )
            and _looks_stale_or_path_problem(artifact.value)
        ]
        if (commit_artifacts or commit_events) and not _has_newer_progress_event(
            context,
            [*commit_artifacts, *commit_events],
        ):
            citations = [artifact.citation for artifact in commit_artifacts]
            citations.extend(event.citation for event in commit_events)
            return context.result(
                FailureClass.DETERMINISTIC_UNBLOCK,
                0.88,
                facts={
                    "commit_failure_artifacts": [
                        artifact.citation for artifact in commit_artifacts
                    ],
                    "commit_failure_events": [event.citation for event in commit_events],
                    "commit_targets": _extract_paths(
                        [artifact.value for artifact in commit_artifacts]
                    ),
                },
                inference=(
                    "Commit/hook failure evidence is a deterministic direct-route blocker; "
                    "focused commit hygiene is preferred over broad product re-verification."
                ),
                recommended_action=ActionLevel.RECOMMEND,
                false_positive_checks=[
                    "Does not recommend bypassing hooks.",
                    "Mixed product verifier failures should remain in normal repair.",
                ],
                citations=citations,
            )
        if stale_artifacts and not _has_newer_progress_event(context, stale_artifacts):
            return context.result(
                FailureClass.DETERMINISTIC_UNBLOCK,
                0.84,
                facts={
                    "stale_or_path_problem_artifacts": [
                        artifact.citation for artifact in stale_artifacts
                    ],
                    "paths": _extract_paths([artifact.value for artifact in stale_artifacts]),
                },
                inference=(
                    "Stale derived DAG/task or generated projection evidence points to "
                    "host-side reconciliation instead of repeated product repair."
                ),
                recommended_action=ActionLevel.RECOMMEND,
                false_positive_checks=[
                    "Historical/advisory mentions alone are insufficient; "
                    "matching artifacts must be task-bearing.",
                    "Canonical replacement paths do not block by themselves.",
                ],
                citations=[artifact.citation for artifact in stale_artifacts],
            )
        return None

    def _normal_product_repair(self, context: "_Context") -> ClassificationResult | None:
        failed_verify = [
            artifact
            for artifact in context.group_artifacts
            if artifact.key.startswith("dag-verify:") and _looks_failed(artifact.value)
        ]
        if not failed_verify:
            return None
        successful_verify = [
            artifact
            for artifact in context.group_artifacts
            if artifact.key.startswith("dag-verify:") and _looks_successful(artifact.value)
        ]
        latest_success_id = max(
            ((artifact.id or 0) for artifact in successful_verify),
            default=0,
        )
        failed_verify = [
            artifact for artifact in failed_verify if (artifact.id or 0) > latest_success_id
        ]
        if not failed_verify:
            return None
        return context.result(
            FailureClass.NORMAL_PRODUCT_REPAIR,
            0.78,
            facts={"failed_verify_artifacts": [artifact.citation for artifact in failed_verify]},
            inference=(
                "Latest verifier concerns are product-level failures without a deterministic "
                "workflow contradiction or repo hygiene blocker."
            ),
            recommended_action=ActionLevel.OBSERVE,
            false_positive_checks=[
                "No checkpoint contradiction was present.",
                "No commit-only direct-route artifact was present.",
                "No operator-only worktree blocker was present.",
            ],
            citations=[artifact.citation for artifact in failed_verify],
        )

    def _healthy_progress(self, context: "_Context") -> ClassificationResult | None:
        current = context.observation.current
        current_active = (
            current is not None
            and current.group_idx == context.group_idx
            and (
                bool(current.active_agents)
                or current.state in {"implementing", "verifying"}
            )
        )
        progress_events = [
            event
            for event in context.group_events
            if event.event_type
            in {
                "agent_start",
                "agent_done",
                "agent_invocation_start",
                "agent_invocation_done",
                "dag_verify_start",
                "dag_verify_finish",
                "phase_start",
                "phase_transition",
            }
        ]
        successful_verify = [
            artifact
            for artifact in context.group_artifacts
            if artifact.key.startswith("dag-verify:") and _looks_successful(artifact.value)
        ]
        if not progress_events and not successful_verify and not current_active:
            return None
        citations = [event.citation for event in progress_events[:5]]
        citations.extend(artifact.citation for artifact in successful_verify[:3])
        if current_active and current is not None:
            citations.extend(current.citations)
        return context.result(
            FailureClass.HEALTHY_PROGRESS,
            0.74 if current_active else 0.7,
            facts={
                "progress_events": [event.citation for event in progress_events],
                "successful_verify_artifacts": [artifact.citation for artifact in successful_verify],
                "active_agents": list(current.active_agents) if current_active and current is not None else [],
                "queued_agents": list(current.queued_agents) if current_active and current is not None else [],
            },
            inference=(
                f"Current workflow snapshot shows active G{context.group_idx} work, "
                "with no deterministic blocker for that selected group."
                if current_active
                else "Recent runner or verifier evidence shows progress and no deterministic blocker."
            ),
            recommended_action=ActionLevel.DIGEST,
            false_positive_checks=[
                "Bridge delivery errors alone are excluded from workflow health.",
                "Historical artifacts from older groups cannot override the current workflow snapshot.",
            ],
            citations=citations,
        )

    def _watch_only(self, context: "_Context") -> ClassificationResult:
        current = context.observation.current
        bridge_activity = ""
        if current is not None and current.group_idx is not None:
            bridge_activity = (
                f" Current snapshot is G{current.group_idx}"
                f" state={current.state or 'unknown'}"
                f" active={current.active_agents} queued={current.queued_agents}."
            )
        return context.result(
            FailureClass.WATCH_ONLY,
            0.55,
            facts={"event_count": len(context.events), "artifact_count": len(context.artifacts)},
            inference=(
                "No material new failure signature is present in the current evidence window."
                f"{bridge_activity}"
            ),
            recommended_action=ActionLevel.OBSERVE,
            false_positive_checks=[
                "Absence of new evidence does not imply failure.",
                "Current workflow snapshot takes priority over historical artifact rows.",
            ],
            citations=list(current.citations) if current is not None else [],
        )


class _Context:
    def __init__(self, observation: SupervisorObservation) -> None:
        self.observation = observation
        self.artifacts = observation.artifacts
        self.events = observation.events
        self.current_authoritative = (
            observation.current is not None
            and observation.current.group_idx is not None
        )
        if self.current_authoritative and observation.current is not None:
            self.group_idx = observation.current.group_idx
            self.retry = observation.current.retry
        else:
            self.group_idx, self.retry = _group_retry(self.artifacts, self.events)
        if self.group_idx is None:
            self.group_artifacts = self.artifacts
            self.group_events = self.events
        else:
            scoped_artifacts = [
                artifact
                for artifact in self.artifacts
                if _artifact_group(artifact.key) == self.group_idx
            ]
            scoped_events = [
                event
                for event in self.events
                if _event_group(event) == self.group_idx
            ]
            if self.current_authoritative:
                self.group_artifacts = scoped_artifacts
                self.group_events = scoped_events
            else:
                self.group_artifacts = scoped_artifacts or self.artifacts
                self.group_events = scoped_events or self.events

    def result(
        self,
        classification: FailureClass,
        confidence: float,
        *,
        facts: dict[str, Any],
        inference: str,
        recommended_action: ActionLevel,
        false_positive_checks: list[str],
        citations: list[str],
    ) -> ClassificationResult:
        return ClassificationResult(
            feature_id=self.observation.feature_id,
            group_idx=self.group_idx,
            retry=self.retry,
            phase=self.observation.phase,
            observed_at=self.observation.observed_at,
            classification=classification,
            confidence=confidence,
            facts=facts
            | {
                "current_workflow": (
                    self.observation.current.model_dump(mode="json")
                    if self.observation.current is not None
                    else None
                ),
                "cursor": self.observation.cursor,
                "next_cursor": self.observation.next_cursor,
                "event_cursor": self.observation.event_cursor,
                "next_event_cursor": self.observation.next_event_cursor,
                "artifact_cursor": self.observation.artifact_cursor,
                "next_artifact_cursor": self.observation.next_artifact_cursor,
                "bridge_log_cursor": (
                    self.observation.bridge.log_cursor
                    if self.observation.bridge is not None
                    else self.observation.bridge_log_cursor
                ),
            },
            inference=inference,
            recommended_action=recommended_action,
            false_positive_checks=false_positive_checks,
            citations=_dedupe(citations),
        )

    def worktree_citations(self) -> list[str]:
        return [f"git:{worktree.root}" for worktree in self.observation.worktrees]


def _looks_failed(value: Any) -> bool:
    lowered = _text(value).lower()
    if any(token in lowered for token in ('"approved": true', '"approved":true')):
        return False
    positive = any(token in lowered for token in ("failed", "failure", "error", "blocked"))
    negative = any(
        token in lowered
        for token in ("status: passed", '"status": "passed"', '"ok": true')
    )
    return positive and not negative


def _looks_successful(value: Any) -> bool:
    lowered = _text(value).lower()
    if any(token in lowered for token in ('"approved": true', '"approved":true')):
        return True
    return any(token in lowered for token in ("passed", "succeeded", '"ok": true')) and "failed" not in lowered


def _looks_stale_or_path_problem(value: Any) -> bool:
    lowered = _text(value).lower()
    return any(
        token in lowered
        for token in (
            "retired path",
            "retired_paths",
            "stale",
            "legacy path",
            "manifest-forbidden",
            "path problem",
            "generated snapshot",
            "task spec",
        )
    )


def _extract_paths(values: list[Any]) -> list[str]:
    paths: list[str] = []
    pattern = re.compile(r"(?P<path>(?:[\w.-]+/)+[\w.@+-]+(?:\.[\w+-]+)?)")
    for value in values:
        for match in pattern.finditer(_text(value)):
            path = match.group("path").strip("`'\".,)")
            if "/" in path and not path.startswith(("http://", "https://")):
                paths.append(path)
    return _dedupe(paths)[:20]


_PROGRESS_EVENT_TYPES = {
    "agent_start",
    "agent_done",
    "agent_invocation_start",
    "agent_invocation_done",
    "dag_verify_start",
    "dag_verify_finish",
    "dag_repair_cycle_start",
    "dag_expanded_verify_start",
    "dag_expanded_verify_finish",
    "dag_rca_start",
    "dag_rca_finish",
    "phase_start",
    "phase_transition",
}


def _has_newer_progress_event(context: "_Context", records: list[Any]) -> bool:
    latest_event_id = max(
        (
            record.id or 0
            for record in records
            if isinstance(record, EventRecord)
        ),
        default=0,
    )
    latest_time = max(
        (
            record.created_at
            for record in records
            if getattr(record, "created_at", None) is not None
        ),
        default=None,
    )
    for event in context.group_events:
        if event.event_type not in _PROGRESS_EVENT_TYPES:
            continue
        if latest_event_id and (event.id or 0) > latest_event_id:
            return True
        if (
            latest_time is not None
            and event.created_at is not None
            and event.created_at > latest_time
        ):
            return True
    return False


def _bridge_line_suggests_restart(line: str) -> bool:
    lowered = line.lower()
    slack_transport_noise = (
        "slack_sdk.socket_mode",
        "socket_mode",
        "closing transport",
        "current session",
        "new session",
        "old session",
        "reconnect",
        "ping message",
        "clientconnectionreseterror",
    )
    if any(token in lowered for token in slack_transport_noise):
        return False
    usage_probe_noise = (
        "readiness probe failed",
        "claude availability probe failed",
        "does not have access to claude",
        "api_error_status\":403",
    )
    if any(token in lowered for token in usage_probe_noise):
        return False
    if lowered.startswith("traceback"):
        return False
    return any(
        token in lowered
        for token in (
            "resumed workflow failed",
            "workflow failed",
            "workflow crashed",
            "process exited",
            "runtimeerror",
            "fatal",
            "uncaught exception",
        )
    )


def _artifact_group(key: str) -> int | None:
    match = re.search(r"(?:^|:)g(?P<group>\d+)(?::|$)", key)
    if match:
        return int(match.group("group"))
    match = re.search(r"^dag-group:(?P<group>\d+)", key)
    return int(match.group("group")) if match else None


def _event_group(event: Any) -> int | None:
    metadata = getattr(event, "metadata", {}) or {}
    for key in ("group_idx", "group", "group_id"):
        group = metadata.get(key)
        if group is not None:
            try:
                return int(str(group).removeprefix("g").removeprefix("G"))
            except ValueError:
                return None
    text = (
        f"{getattr(event, 'event_type', '')} {getattr(event, 'source', '')} "
        f"{getattr(event, 'content', '')} {json.dumps(metadata, sort_keys=True, default=str)}"
    )
    match = re.search(r"\bg(?P<group>\d+)\b", text)
    if match:
        return int(match.group("group"))
    match = re.search(r"\bgroup[=:\s-]+(?P<group>\d+)\b", text, re.IGNORECASE)
    if match:
        return int(match.group("group"))
    return None


def _group_retry(artifacts: list[ArtifactRecord], events: list[Any]) -> tuple[int | None, int | None]:
    for event in reversed(events):
        metadata = getattr(event, "metadata", {}) or {}
        group = _event_group(event)
        retry = metadata.get("retry")
        if group is not None:
            return int(group), int(retry) if isinstance(retry, int) else _retry_number(str(retry))
    for artifact in reversed(artifacts):
        group = _artifact_group(artifact.key)
        if group is not None:
            retry_match = re.search(r"(?:^|:)retry-(?P<retry>\d+)(?::|$)", artifact.key)
            if retry_match:
                return group, int(retry_match.group("retry"))
            if artifact.key.endswith(":initial") or artifact.key.endswith(":retry-initial"):
                return group, 0
            return group, None
    return None, None


def _retry_number(text: str) -> int | None:
    if text in {"initial", "retry-initial"}:
        return 0
    match = re.search(r"(\d+)", text)
    return int(match.group(1)) if match else None


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, default=str)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result
