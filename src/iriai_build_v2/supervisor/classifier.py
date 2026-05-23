from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from .classifier_mapping import TypedClassification, classify_typed_snapshot
from .models import (
    ActionLevel,
    ArtifactRecord,
    ClassificationResult,
    EvidencePacket,
    EventRecord,
    FailureClass,
    SupervisorObservation,
)

# doc 10 § "Supervisor Classifier Mapping" — the merge-queue healthy-progress
# row's active statuses ("`merge_queue.status in ('queued', 'leased',
# 'applying', 'verifying', 'committing', 'integrated', 'checkpointing')` and no
# open fatal failure -> healthy_progress / observe").
_TYPED_HEALTHY_MERGE_QUEUE_STATUSES = frozenset(
    {
        "queued",
        "leased",
        "applying",
        "verifying",
        "committing",
        "integrated",
        "checkpointing",
    }
)

# `ExecutionAttemptSummary.status` non-terminal values (the typed attempt is
# live executor work). The typed `status` Literal is started/succeeded/failed/
# cancelled/incomplete; started + incomplete are non-terminal.
_TYPED_ACTIVE_ATTEMPT_STATUSES = frozenset({"started", "incomplete"})

# Terminal work statuses for sandbox leases / runtime bindings — any other
# (non-empty) status counts as live executor work.
_TYPED_TERMINAL_WORK_STATUSES = frozenset(
    {
        "done",
        "failed",
        "cancelled",
        "canceled",
        "released",
        "expired",
        "complete",
        "completed",
        "cleaned",
        "retained",
        "terminal",
        "resolved",
    }
)


def classify_observation(observation: SupervisorObservation) -> EvidencePacket:
    classifier = SupervisorClassifier()
    return classifier.classify(observation)


class SupervisorClassifier:
    def classify(self, observation: SupervisorObservation) -> EvidencePacket:
        context = _Context(observation)
        # Slice 10c-2 — typed failure/route decisions are PRIMARY (doc 10
        # § "Supervisor Classifier Mapping" step 5). When `evidence_mode ==
        # "typed"` the typed control-plane snapshot's failure/route rows drive
        # the classification through the doc-10 mapping table; the legacy
        # artifact classifiers below run ONLY as the fallback for
        # `evidence_mode != "typed"` (a legacy feature with no typed rows, or a
        # typed query that degraded). Typed-primary, legacy-fallback.
        result = self._classify_typed_primary(context) or (
            self._operator_required(context)
            or self._pipeline_bug_suspected(context)
            or self._deterministic_unblock(context)
            or self._stale_codex_invocation(context)
            or self._safe_restart_candidate(context)
            or self._normal_product_repair(context)
            or self._healthy_progress(context)
            or self._watch_only(context)
        )
        return EvidencePacket(**result.model_dump())

    # ── Slice 10c-2 — the typed-primary classification path ────────────────

    def _classify_typed_primary(
        self,
        context: "_Context",
    ) -> ClassificationResult | None:
        """Classify from the typed control-plane snapshot (doc-10 mapping).

        Returns ``None`` when this is not a typed-evidence observation — the
        caller then runs the legacy artifact classifiers as the fallback (doc
        10 § "Refactoring Steps" step 5: "gate them behind ``evidence_mode !=
        'typed'``"). Returns ``None`` even in typed mode when the typed
        snapshot carries no failure/merge-queue signal that maps; the typed
        snapshot is then treated as "no typed verdict" and the fallback runs —
        never a silent default.

        The doc-10 classifier-priority list interleaves the typed verdict
        (levels 1-3, 6-7) with bridge/process evidence (levels 4-5):

          1-3. typed checkpoint contradiction / operator-required / pipeline
               safety stop / deterministic unblock with budget — these
               OUTRANK the bridge-restart and stale-Codex rows, so a typed
               verdict at priority <= 3 returns immediately.
          4-5. safe bridge restart / stale Codex — bridge & process evidence,
               handled by the existing methods; only consulted when the typed
               verdict is a lower-priority product-repair / healthy row.
          6-7. typed product repair / typed healthy progress.
        """

        if not context.is_typed_evidence:
            return None
        snapshot = context.typed_snapshot
        if snapshot is None:
            return None

        typed_verdict = classify_typed_snapshot(snapshot)
        merge_verdict = self._typed_healthy_merge_progress(context, snapshot)

        # doc-10 priority 1-3: a typed failure verdict at priority <= 3
        # (checkpoint contradiction, operator-required, the pipeline-bug
        # safety stops, a deterministic unblock with budget) outranks the
        # bridge-restart / stale-Codex rows — return it directly.
        if typed_verdict is not None and typed_verdict.row.priority <= 3:
            return self._typed_result(context, typed_verdict)

        # doc-10 priority 4-5: bridge restart / stale Codex. doc-10's priority
        # list gates these on "no typed deterministic route, active queue
        # lease, or active dispatcher attempt" — the bridge/process rows must
        # NOT outrank live typed control-plane work. In typed mode the legacy
        # `control_plane_snapshot` dict is absent, so the existing methods'
        # `_has_active_control_plane_work` guard would not see it; check the
        # TYPED snapshot's active work here and skip levels 4-5 when present.
        if not _typed_snapshot_has_active_work(snapshot):
            bridge_or_process = (
                self._stale_codex_invocation(context)
                or self._safe_restart_candidate(context)
            )
            if bridge_or_process is not None:
                return bridge_or_process

        # doc-10 priority 6: a typed product-repair verdict.
        if typed_verdict is not None and (
            typed_verdict.classification is FailureClass.NORMAL_PRODUCT_REPAIR
        ):
            return self._typed_result(context, typed_verdict)

        # doc-10 priority 6 (cont.) / lower: any remaining typed failure
        # verdict (a watch-only runtime/provider/resource row).
        if typed_verdict is not None:
            return self._typed_result(context, typed_verdict)

        # doc-10 priority 7: typed healthy progress from the merge queue.
        if merge_verdict is not None:
            return merge_verdict

        # No typed failure row and no active merge-queue progress — fall back.
        return None

    def _typed_result(
        self,
        context: "_Context",
        verdict: TypedClassification,
    ) -> ClassificationResult:
        """Build a :class:`ClassificationResult` from a typed verdict."""

        return context.result(
            verdict.classification,
            verdict.confidence,
            facts=verdict.facts,
            inference=verdict.inference,
            recommended_action=verdict.action,
            false_positive_checks=verdict.false_positive_checks,
            citations=verdict.citations,
        )

    def _typed_healthy_merge_progress(
        self,
        context: "_Context",
        snapshot: Any,
    ) -> ClassificationResult | None:
        """The doc-10 merge-queue healthy-progress row.

        doc 10 § "Supervisor Classifier Mapping": "`merge_queue.status in
        ('queued', 'leased', 'applying', 'verifying', 'committing',
        'integrated', 'checkpointing')` and no open fatal failure ->
        healthy_progress / observe". `integrated` means committed/clean lane
        waiting for group checkpoint coordination.
        """

        merge_items = list(_typed_attr(snapshot, "merge_queue", []) or [])
        active = [
            item
            for item in merge_items
            if str(_typed_attr(item, "status", "") or "").strip().lower()
            in _TYPED_HEALTHY_MERGE_QUEUE_STATUSES
        ]
        if not active:
            return None
        # "no open fatal failure" — a fatal open typed failure is already
        # caught at priority <= 3 above, so reaching here means there is none.
        statuses = sorted(
            {
                str(_typed_attr(item, "status", "") or "").strip().lower()
                for item in active
            }
        )
        citations = [
            f"control-plane:merge-queue:item={_typed_attr(item, 'item_id', '')}"
            for item in active[:5]
        ]
        return context.result(
            FailureClass.HEALTHY_PROGRESS,
            0.74,
            facts={
                "merge_queue_active_count": len(active),
                "merge_queue_statuses": statuses,
                "snapshot_version": str(
                    _typed_attr(snapshot, "snapshot_version", "") or ""
                ),
                "snapshot_source": str(
                    _typed_attr(snapshot, "source", "") or ""
                ),
            },
            inference=(
                "Typed control-plane merge queue is the live progress source: "
                f"{len(active)} active item(s) ({', '.join(statuses)}) and no "
                "open fatal typed failure."
            ),
            recommended_action=ActionLevel.OBSERVE,
            false_positive_checks=[
                "A fatal/quiesce typed failure outranks merge-queue progress.",
                "Merge-queue state is read from typed rows, not artifact "
                "bodies.",
            ],
            citations=citations,
        )

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
        operator_artifacts = [
            artifact
            for artifact in context.group_artifacts
            if artifact.key.startswith(_OPERATOR_REQUIRED_ARTIFACT_PREFIXES)
            and _looks_operator_required(_artifact_signal(artifact))
        ]
        operator_events = _dedupe_operator_required_runtime_failures([
            event
            for event in context.group_events
            if _is_operator_required_runtime_failure(event)
        ])
        operator_artifacts = [
            artifact
            for artifact in operator_artifacts
            if not _operator_required_blocker_cleared(context, artifact)
        ]
        operator_events = [
            event
            for event in operator_events
            if not _operator_required_blocker_cleared(context, event)
        ]
        if not any((
            embedded,
            gitlinks,
            forbidden,
            pending,
            proposed,
            unwritable,
            operator_artifacts,
            operator_events,
        )):
            return None
        facts = {
            "embedded_git_paths": embedded,
            "gitlinks": gitlinks,
            "forbidden_paths": forbidden,
            "pending_paths": pending,
            "proposed_paths": proposed,
            "unwritable_paths": unwritable,
            "operator_required_artifacts": [
                artifact.citation for artifact in operator_artifacts
            ],
            "operator_required_paths": _extract_paths([
                _artifact_signal(artifact) for artifact in operator_artifacts
            ]),
            "operator_required_runtime_failures": [
                _runtime_failure_event_summary(event) for event in operator_events
            ],
        }
        return context.result(
            FailureClass.OPERATOR_REQUIRED,
            0.94 if operator_artifacts or operator_events else 0.9,
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
            citations=[
                *context.worktree_citations(),
                *[artifact.citation for artifact in operator_artifacts],
                *[_runtime_failure_event_citation(event) for event in operator_events],
            ],
        )

    def _pipeline_bug_suspected(self, context: "_Context") -> ClassificationResult | None:
        pipeline_runtime_events = _dedupe_runtime_failures([
            event
            for event in context.group_events
            if _is_pipeline_bug_runtime_failure(event)
        ])
        if pipeline_runtime_events:
            return context.result(
                FailureClass.PIPELINE_BUG_SUSPECTED,
                0.96,
                facts={
                    "pipeline_runtime_failures": [
                        _runtime_failure_event_summary(event)
                        for event in pipeline_runtime_events
                    ],
                },
                inference=(
                    "Typed control-plane runtime evidence reports a quiesced workflow "
                    "safety stop that requires scheduler or pipeline correction before "
                    "product repair or restart guidance."
                ),
                recommended_action=ActionLevel.STOP_ESCALATE,
                false_positive_checks=[
                    "Checkpoint contradictions classify from typed evidence without legacy artifact corroboration.",
                    "Quiesced deterministic or product routes are not retried as normal repair/unblock.",
                ],
                citations=[
                    _runtime_failure_event_citation(event)
                    for event in pipeline_runtime_events
                ],
            )
        failed_raw = [
            artifact
            for artifact in context.group_artifacts
            if artifact.key.startswith(_CHECKPOINT_GATE_FAILURE_ARTIFACT_PREFIXES)
            and _looks_failed_gate_artifact(artifact)
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
            if artifact.key.startswith(_CHECKPOINT_GATE_FAILURE_ARTIFACT_PREFIXES)
            and _looks_successful_gate_artifact(artifact)
        ]
        latest_failed_id = max((artifact.id or 0) for artifact in failed_raw)
        latest_success_id = max(
            ((artifact.id or 0) for artifact in successful_gates),
            default=0,
        )
        if latest_success_id > latest_failed_id:
            return None
        checkpoint_artifacts = [
            artifact
            for artifact in checkpoint_artifacts
            if _checkpoint_artifact_after_failed_gate(artifact, failed_raw)
        ]
        checkpoint_events = [
            event
            for event in checkpoint_events
            if _checkpoint_event_after_failed_gate(event, failed_raw)
        ]
        if not (checkpoint_artifacts or checkpoint_events):
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

    def _stale_codex_invocation(self, context: "_Context") -> ClassificationResult | None:
        stale = context.observation.stale_codex_invocations
        if not stale:
            return None
        if _has_active_control_plane_work(context.control_plane_snapshot):
            return None
        item = stale[0]
        facts = {
            "stale_codex_invocation": item.model_dump(mode="json"),
            "stale_codex_invocation_count": len(stale),
            "actor": item.actor,
            "pid": item.pid,
            "child_pids": item.child_pids,
            "trace_path": item.trace_path,
            "output_path": item.output_path,
            "elapsed_seconds": item.elapsed_seconds,
            "idle_seconds": item.idle_seconds,
            "stdout_events": item.stdout_events,
            "stderr_lines": item.stderr_lines,
            "output_bytes": item.output_bytes,
            "stable_heartbeat_count": item.stable_heartbeat_count,
            "evidence_token": item.evidence_token,
        }
        return context.result(
            FailureClass.STALE_CODEX_INVOCATION,
            0.93,
            facts=facts,
            inference=(
                "A Codex invocation is still alive but has repeated identical heartbeats "
                "with no output growth or substantive progress. Reset the exact Codex "
                "process tree, not the bridge."
            ),
            recommended_action=ActionLevel.RECOMMEND,
            false_positive_checks=[
                "Requires at least two identical heartbeats for the same pid/trace.",
                "Requires a live local process scoped to the feature workspace.",
                "Does not fire for active commands with changing stdout/output evidence.",
            ],
            citations=item.citations,
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
        active_control_plane_routes = _active_control_plane_routes(context.control_plane_snapshot)
        active_control_plane_leases = _active_control_plane_leases(context.control_plane_snapshot)
        active_control_plane_attempts = _active_control_plane_dispatcher_attempts(
            context.control_plane_snapshot
        )
        active_runtime_bindings = _active_runtime_workspace_bindings(
            context.control_plane_snapshot
        )
        active = (
            active
            or bool(active_control_plane_routes)
            or bool(active_control_plane_leases)
            or bool(active_control_plane_attempts)
            or bool(active_runtime_bindings)
        )
        action = ActionLevel.RECOMMEND
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
                "active_control_plane_routes": active_control_plane_routes,
                "active_control_plane_leases": active_control_plane_leases,
                "active_control_plane_dispatcher_attempts": active_control_plane_attempts,
                "active_runtime_workspace_bindings": active_runtime_bindings,
            },
            inference=(
                "Bridge status/log evidence indicates a dead or wedged bridge; restart is "
                "recommend-only in read-only supervisor mode and must account for active "
                "typed runtime routes, sandbox leases, and merge queue work."
            ),
            recommended_action=action,
            false_positive_checks=[
                "Slack/bridge noise is not treated as workflow truth.",
                "Active invocation evidence downgrades automatic action to recommendation.",
                "Typed deterministic routes, active dispatcher attempts, runtime workspace "
                "bindings, and sandbox/merge queue leases block guarded restart.",
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
            if artifact.key.startswith(_DETERMINISTIC_UNBLOCK_ARTIFACT_PREFIXES)
            and _looks_deterministic_unblock_artifact(artifact)
            and not _clears_deterministic_blocker(artifact)
        ]
        workflow_blocker_artifacts = [
            artifact
            for artifact in _workflow_blocker_context_artifacts(context)
            if _looks_workflow_blocker_artifact(artifact)
            and not _clears_deterministic_blocker(artifact)
        ]
        deterministic_runtime_events = _dedupe_runtime_failures([
            event
            for event in context.group_events
            if _is_deterministic_unblock_runtime_failure(event)
        ])
        if (commit_artifacts or commit_events) and not _has_newer_material_evidence(
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
                        [_artifact_signal(artifact) for artifact in commit_artifacts]
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
        if workflow_blocker_artifacts and not _has_newer_material_evidence(
            context,
            workflow_blocker_artifacts,
            deterministic_blocker=True,
        ):
            return context.result(
                FailureClass.DETERMINISTIC_UNBLOCK,
                0.86,
                facts={
                    "workflow_blocker_artifacts": [
                        artifact.citation for artifact in workflow_blocker_artifacts
                    ],
                    "workflow_blocker_failure_classes": _workflow_blocker_failure_classes(
                        workflow_blocker_artifacts
                    ),
                    "paths": _extract_paths([
                        _artifact_signal(artifact)
                        for artifact in workflow_blocker_artifacts
                    ]),
                },
                inference=(
                    "Verifier/runtime evidence encodes a workflow-class blocker, not a "
                    "product defect for normal repair."
                ),
                recommended_action=ActionLevel.RECOMMEND,
                false_positive_checks=[
                    "Product-defect verifier payloads remain in normal product repair.",
                    "Requires workflow-blocker/dag-runtime-failure evidence or a typed marker "
                    "such as SANDBOX_WORKFLOW_BLOCKER, verifier_provider, or runtime_context.",
                    "A later checkpoint alone does not clear this blocker; requires typed "
                    "resolution or successful verifier evidence.",
                ],
                citations=[artifact.citation for artifact in workflow_blocker_artifacts],
            )
        if deterministic_runtime_events and not _has_newer_material_evidence(
            context,
            deterministic_runtime_events,
            deterministic_blocker=True,
        ):
            return context.result(
                FailureClass.DETERMINISTIC_UNBLOCK,
                0.87,
                facts={
                    "runtime_failure_events": [
                        _runtime_failure_event_summary(event)
                        for event in deterministic_runtime_events
                    ],
                    "workflow_blocker_failure_classes": _dedupe([
                        _runtime_failure_failure_class(event)
                        for event in deterministic_runtime_events
                    ]),
                },
                inference=(
                    "Typed control-plane runtime failure evidence is deterministic and "
                    "routes to verifier/runtime retry before product repair."
                ),
                recommended_action=ActionLevel.RECOMMEND,
                false_positive_checks=[
                    "Product-defect runtime failures remain normal product repair.",
                    "Requires deterministic typed runtime failure metadata and a retry-verifier route.",
                    "Operator-required runtime failures without deterministic metadata still escalate.",
                ],
                citations=[
                    _runtime_failure_event_citation(event)
                    for event in deterministic_runtime_events
                ],
            )
        if stale_artifacts and not _has_newer_material_evidence(
            context,
            stale_artifacts,
            deterministic_blocker=True,
        ):
            return context.result(
                FailureClass.DETERMINISTIC_UNBLOCK,
                0.84,
                facts={
                    "stale_or_path_problem_artifacts": [
                        artifact.citation for artifact in stale_artifacts
                    ],
                    "paths": _extract_paths([_artifact_signal(artifact) for artifact in stale_artifacts]),
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
        product_runtime_events = _dedupe_runtime_failures([
            event
            for event in context.group_events
            if _is_product_defect_runtime_failure(event)
        ])
        successful_verify = [
            artifact
            for artifact in context.group_artifacts
            if _looks_successful_verify_artifact(artifact)
        ]
        product_runtime_events = [
            event for event in product_runtime_events
            if (
                not _product_runtime_cleared_by_successful_verify(event, successful_verify)
                and not _has_newer_material_evidence(context, [event])
            )
        ]
        failed_verify = [
            artifact
            for artifact in context.group_artifacts
            if _looks_product_defect_verify_failure_artifact(artifact)
        ]
        if not failed_verify and not product_runtime_events:
            return None
        latest_success_id = max(
            ((artifact.id or 0) for artifact in successful_verify),
            default=0,
        )
        failed_verify = [
            artifact for artifact in failed_verify if (artifact.id or 0) > latest_success_id
        ]
        if not failed_verify and not product_runtime_events:
            return None
        if product_runtime_events and not failed_verify:
            return context.result(
                FailureClass.NORMAL_PRODUCT_REPAIR,
                0.78,
                facts={
                    "product_defect_runtime_failures": [
                        _runtime_failure_event_summary(event)
                        for event in product_runtime_events
                    ],
                },
                inference=(
                    "Typed control-plane runtime evidence routes to product repair, "
                    "without a deterministic workflow contradiction or repo hygiene blocker."
                ),
                recommended_action=ActionLevel.OBSERVE,
                false_positive_checks=[
                    "Verifier/runtime retry routes are handled by deterministic unblock.",
                    "No operator-only worktree blocker was present.",
                ],
                citations=[
                    _runtime_failure_event_citation(event)
                    for event in product_runtime_events
                ],
            )
        failed_verify = sorted(failed_verify, key=lambda artifact: artifact.id or 0)
        latest_failed_verify = failed_verify[-1:]
        latest_material_artifacts = _latest_material_artifacts(
            context,
            after_id=latest_success_id,
            limit=8,
        )
        citations = [artifact.citation for artifact in latest_failed_verify]
        citations.extend(artifact.citation for artifact in latest_material_artifacts)
        return context.result(
            FailureClass.NORMAL_PRODUCT_REPAIR,
            0.78,
            facts={
                "failed_verify_count_since_latest_success": len(failed_verify),
                "latest_failed_verify_artifacts": [
                    artifact.citation for artifact in latest_failed_verify
                ],
                "latest_material_artifacts": [
                    artifact.citation for artifact in latest_material_artifacts
                ],
            },
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
            citations=citations,
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
            if _looks_successful_verify_artifact(artifact)
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
        self.control_plane_snapshot = (
            observation.control_plane_snapshot
            if isinstance(observation.control_plane_snapshot, dict)
            else None
        )
        # Slice 10c-2 — the typed control-plane snapshot + evidence mode.
        # `is_typed_evidence` gates the typed-primary classifier path;
        # `typed_snapshot` is the bounded typed `ControlPlaneSnapshot`
        # (Pydantic model or dict — read by attribute access). doc 10
        # § "Refactoring Steps" step 5: the legacy artifact classifiers run
        # only when `evidence_mode != "typed"`.
        self.evidence_mode = str(
            getattr(observation, "evidence_mode", "") or ""
        ).strip().lower()
        self.typed_snapshot = getattr(observation, "control_plane", None)
        self.is_typed_evidence = (
            self.evidence_mode == "typed" and self.typed_snapshot is not None
        )
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
                "control_plane_snapshot_version": (
                    self.control_plane_snapshot.get("snapshot_version")
                    if self.control_plane_snapshot
                    else None
                ),
                "control_plane_source": (
                    self.control_plane_snapshot.get("source")
                    if self.control_plane_snapshot
                    else None
                ),
                "control_plane_query": (
                    self.control_plane_snapshot.get("query")
                    if self.control_plane_snapshot
                    else None
                ),
                "control_plane_budget": (
                    self.control_plane_snapshot.get("budget")
                    or self.control_plane_snapshot.get("budgets")
                    if self.control_plane_snapshot
                    else None
                ),
                "control_plane": _compact_control_plane_fact(self.control_plane_snapshot),
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


def _looks_operator_required(value: Any) -> bool:
    lowered = _text(value).lower()
    return any(
        token in lowered
        for token in (
            "operator_required=true",
            '"operator_required": true',
            '"operator_required":true',
            "writeability_denied",
            "writeability_file_denied",
            "writeability_directory_denied",
            "workspace acl normalization blocked",
            "parent directory is not writable by repair agent",
            "chmod failed",
            "chown",
        )
    )


def _looks_operator_cleared(value: Any) -> bool:
    lowered = _text(value).lower()
    return any(
        token in lowered
        for token in (
            '"operator_required": false',
            '"operator_required":false',
            '"approved": true',
            '"approved":true',
        )
    )


def _is_operator_required_runtime_failure(event: EventRecord) -> bool:
    if not _is_control_plane_runtime_failure(event):
        return False
    if _runtime_failure_is_terminal(event):
        return False
    metadata = event.metadata or {}
    if _runtime_failure_has_deterministic_repair_budget(event):
        return False
    value = _runtime_failure_typed_metadata_value(event, "operator_required")
    if value is not None:
        return _metadata_bool({"operator_required": value}, "operator_required")
    route = _runtime_failure_route(event)
    failure_class = _runtime_failure_failure_class(event)
    failure_type = _runtime_failure_failure_type(event)
    return (
        route in _OPERATOR_REQUIRED_RUNTIME_ROUTES
        or failure_class in _OPERATOR_REQUIRED_RUNTIME_FAILURE_CLASSES
        or failure_type in _OPERATOR_REQUIRED_RUNTIME_FAILURE_TYPES
    )


def _is_control_plane_runtime_failure(event: EventRecord) -> bool:
    return event.event_type == "control_plane_runtime_failure"


def _metadata_bool(metadata: dict[str, Any], key: str) -> bool:
    value = metadata.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "t", "1", "yes"}
    return bool(value)


def _metadata_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        parsed = _json_value(value)
        if isinstance(parsed, dict):
            return parsed
    return {}


def _runtime_failure_typed_metadata_value(event: EventRecord, key: str) -> Any:
    metadata = event.metadata or {}
    route_decision = _metadata_dict(metadata.get("route_decision"))
    if route_decision.get(key) is not None:
        return route_decision.get(key)
    return metadata.get(key)


def _runtime_failure_typed_bool(event: EventRecord, key: str) -> bool | None:
    value = _runtime_failure_typed_metadata_value(event, key)
    if value is None:
        return None
    return _metadata_bool({key: value}, key)


def _metadata_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        parsed = _json_value(value)
        if isinstance(parsed, list):
            return parsed
    return []


def _runtime_failure_failure_class(event: EventRecord) -> str:
    metadata = event.metadata or {}
    route_decision = _metadata_dict(metadata.get("route_decision"))
    return str(
        route_decision.get("failure_class")
        or metadata.get("failure_class")
        or ""
    ).strip().lower()


def _runtime_failure_failure_type(event: EventRecord) -> str:
    metadata = event.metadata or {}
    route_decision = _metadata_dict(metadata.get("route_decision"))
    return str(
        route_decision.get("failure_type")
        or metadata.get("failure_type")
        or ""
    ).strip().lower()


def _runtime_failure_route(event: EventRecord) -> str:
    metadata = event.metadata or {}
    retry_budget = _metadata_dict(metadata.get("retry_budget"))
    route_decision = _metadata_dict(metadata.get("route_decision"))
    return str(
        route_decision.get("route")
        or metadata.get("route")
        or retry_budget.get("route")
        or ""
    ).strip().lower()


def _runtime_failure_status(event: EventRecord) -> str:
    return str((event.metadata or {}).get("status") or "").strip().lower()


def _runtime_failure_is_terminal(event: EventRecord) -> bool:
    return _runtime_failure_status(event) in _TERMINAL_RUNTIME_FAILURE_STATUSES


def _runtime_failure_has_deterministic_repair_budget(event: EventRecord) -> bool:
    metadata = event.metadata or {}
    retry_budget = _metadata_dict(metadata.get("retry_budget"))
    route_decision = _metadata_dict(metadata.get("route_decision"))
    routes = {
        _runtime_failure_route(event),
        str(retry_budget.get("route") or "").strip().lower(),
        str(route_decision.get("route") or "").strip().lower(),
    }
    if not any(route in _DETERMINISTIC_RUNTIME_RETRY_ROUTES for route in routes):
        return False
    retryable = _runtime_failure_typed_metadata_value(event, "retryable")
    if retryable is not None:
        return _metadata_bool({"retryable": retryable}, "retryable")
    return _retry_budget_has_remaining(retry_budget)


def _retry_budget_has_remaining(budget: dict[str, Any]) -> bool:
    if not budget:
        return False
    remaining = _intish(budget.get("remaining_attempts"))
    if remaining is not None:
        return remaining > 0
    retry = _intish(budget.get("retry"))
    max_retries = _intish(budget.get("max_retries"))
    if retry is not None and max_retries is not None:
        return retry < max_retries
    max_attempts = _intish(budget.get("max_attempts"))
    if retry is not None and max_attempts is not None:
        return retry + 1 < max_attempts
    return bool(budget)


def _intish(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _is_product_defect_runtime_failure(event: EventRecord) -> bool:
    if not _is_control_plane_runtime_failure(event):
        return False
    if _runtime_failure_is_terminal(event):
        return False
    failure_class = _runtime_failure_failure_class(event)
    route = _runtime_failure_route(event)
    if route in _NON_PRODUCT_REPAIR_RUNTIME_ROUTES:
        return False
    if failure_class in _PRODUCT_DEFECT_FAILURE_CLASSES:
        return True
    return (
        failure_class in _PRODUCT_REPAIR_CONTRACT_FAILURE_CLASSES
        and _runtime_failure_has_product_evidence(event)
    )


def _runtime_failure_has_product_evidence(event: EventRecord) -> bool:
    metadata = event.metadata or {}
    route_decision = _metadata_dict(metadata.get("route_decision"))
    evidence_maps = [metadata, route_decision]
    for values in evidence_maps:
        for key in _PRODUCT_EVIDENCE_BOOL_FIELDS:
            value = values.get(key)
            if value is not None and _metadata_bool({key: value}, key):
                return True
        for key in _PRODUCT_EVIDENCE_LIST_FIELDS:
            if _metadata_list(values.get(key)):
                return True
    lowered = " ".join(
        str(part or "")
        for part in (
            event.content,
            metadata.get("summary"),
            route_decision.get("summary"),
        )
    ).lower()
    return any(token in lowered for token in _PRODUCT_EVIDENCE_TEXT_TOKENS)


def _is_pipeline_bug_runtime_failure(event: EventRecord) -> bool:
    if not _is_control_plane_runtime_failure(event):
        return False
    if _runtime_failure_is_terminal(event):
        return False
    if _is_operator_required_runtime_failure(event):
        return False
    failure_class = _runtime_failure_failure_class(event)
    route = _runtime_failure_route(event)
    severity = str((event.metadata or {}).get("severity") or "").strip().lower()
    if failure_class in _PIPELINE_BUG_RUNTIME_FAILURE_CLASSES:
        return True
    if route != "quiesce":
        return False
    return (
        severity == "fatal"
        or failure_class in _PIPELINE_BUG_QUIESCE_FAILURE_CLASSES
    )


def _is_deterministic_unblock_runtime_failure(event: EventRecord) -> bool:
    if not _is_control_plane_runtime_failure(event):
        return False
    if _runtime_failure_is_terminal(event):
        return False
    if _is_product_defect_runtime_failure(event):
        return False
    if _is_operator_required_runtime_failure(event):
        return False
    metadata = event.metadata or {}
    route = _runtime_failure_route(event)
    failure_class = _runtime_failure_failure_class(event)
    failure_type = _runtime_failure_failure_type(event)
    deterministic = _runtime_failure_typed_bool(event, "deterministic")
    if (
        not deterministic
        and route not in _DETERMINISTIC_RUNTIME_RETRY_ROUTES
    ):
        return False
    if (
        route in _NON_UNBLOCK_RUNTIME_ROUTES
        or failure_class in _NON_UNBLOCK_RUNTIME_FAILURE_CLASSES
    ):
        return False
    if route in _DETERMINISTIC_RUNTIME_RETRY_ROUTES:
        return True
    if failure_class in _DETERMINISTIC_UNBLOCK_RUNTIME_FAILURE_CLASSES:
        return True
    if (
        bool(_runtime_failure_typed_bool(event, "retryable"))
        and failure_class in _DETERMINISTIC_UNBLOCK_RUNTIME_FAILURE_CLASSES
    ):
        return True
    return failure_type in {
        "context_materialization_failed",
        "verifier_context_materialization_failed",
        "stale_projection",
        "stale_projection_snapshot",
        "worktree_alias",
        "worktree_alias_path",
        "commit_hygiene",
        "contract_compile",
        "contract_compile_failed",
        "merge_conflict",
        "sandbox_lease_active",
        "sandbox_binding_failed",
        "writeability_denied",
        "writeability_directory_denied",
        "writeability_file_denied",
    }


def _runtime_failure_event_citation(event: EventRecord) -> str:
    metadata = event.metadata or {}
    evidence_node_id = metadata.get("evidence_node_id")
    if evidence_node_id is not None:
        return f"event:control_plane_runtime_failure:evidence_node:{evidence_node_id}"
    return event.citation


def _runtime_failure_event_summary(event: EventRecord) -> dict[str, Any]:
    metadata = event.metadata or {}
    return {
        "citation": _runtime_failure_event_citation(event),
        "evidence_node_id": metadata.get("evidence_node_id"),
        "group_idx": metadata.get("group_idx"),
        "attempt_id": metadata.get("attempt_id"),
        "failure_class": metadata.get("failure_class"),
        "failure_type": metadata.get("failure_type"),
        "route": _runtime_failure_route(event) or None,
        "deterministic": _runtime_failure_typed_metadata_value(event, "deterministic"),
        "retryable": _runtime_failure_typed_metadata_value(event, "retryable"),
        "content": event.content or "",
    }


def _dedupe_runtime_failures(events: list[EventRecord]) -> list[EventRecord]:
    seen: set[str] = set()
    result: list[EventRecord] = []
    for event in events:
        citation = _runtime_failure_event_citation(event)
        if citation in seen:
            continue
        seen.add(citation)
        result.append(event)
    return result


def _dedupe_operator_required_runtime_failures(events: list[EventRecord]) -> list[EventRecord]:
    return _dedupe_runtime_failures(events)


def _typed_attr(obj: Any, name: str, default: Any = None) -> Any:
    """Read ``name`` off a typed snapshot (Pydantic model or dict) uniformly.

    Slice 10c-2: the typed ``ControlPlaneSnapshot`` and its summary models are
    Pydantic models in production, but a test double or a ``model_dump()``ed
    snapshot is a plain ``dict``. Attribute-access both shapes the same way so
    the classifier never needs the ``workflows.develop.execution`` import edge.
    """

    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _typed_snapshot_has_active_work(snapshot: Any) -> bool:
    """True iff a typed ``ControlPlaneSnapshot`` carries live executor work.

    doc 10's classifier-priority list gates the bridge-restart / stale-Codex
    rows on "no typed deterministic route, active queue lease, or active
    dispatcher attempt". This checks the TYPED snapshot for: a non-terminal
    ``active_attempts`` row, an active ``merge_queue`` item, a non-terminal
    ``sandbox_leases`` lease, or a non-terminal ``runtime_bindings`` binding.
    A live deterministic-unblock typed FAILURE row is already caught at
    priority <= 3 above, so it does not need to be re-checked here.
    """

    if snapshot is None:
        return False
    for attempt in _typed_attr(snapshot, "active_attempts", []) or []:
        status = str(_typed_attr(attempt, "status", "") or "").strip().lower()
        if status in _TYPED_ACTIVE_ATTEMPT_STATUSES:
            return True
    for item in _typed_attr(snapshot, "merge_queue", []) or []:
        status = str(_typed_attr(item, "status", "") or "").strip().lower()
        if status in _TYPED_HEALTHY_MERGE_QUEUE_STATUSES:
            return True
    for lease in _typed_attr(snapshot, "sandbox_leases", []) or []:
        status = str(_typed_attr(lease, "status", "") or "").strip().lower()
        if status and status not in _TYPED_TERMINAL_WORK_STATUSES:
            return True
    for binding in _typed_attr(snapshot, "runtime_bindings", []) or []:
        status = str(_typed_attr(binding, "status", "") or "").strip().lower()
        if status and status not in _TYPED_TERMINAL_WORK_STATUSES:
            return True
    return False


def _compact_control_plane_fact(snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
    if not snapshot:
        return None
    merge_queue = snapshot.get("merge_queue") if isinstance(snapshot.get("merge_queue"), dict) else {}
    return {
        "snapshot_version": snapshot.get("snapshot_version"),
        "source": snapshot.get("source"),
        "degraded": bool(snapshot.get("degraded")),
        "query": snapshot.get("query"),
        "budget": snapshot.get("budget") or snapshot.get("budgets"),
        "runtime_failure_count": len(snapshot.get("runtime_failures") or []),
        "active_dispatcher_attempt_count": len(
            _active_control_plane_dispatcher_attempts(snapshot)
        ),
        "active_sandbox_lease_count": len(_active_control_plane_leases(snapshot)),
        "active_runtime_workspace_binding_count": len(
            _active_runtime_workspace_bindings(snapshot)
        ),
        "merge_queue_pending_count": int(merge_queue.get("pending_count") or 0),
    }


def _has_active_control_plane_work(snapshot: dict[str, Any] | None) -> bool:
    return bool(
        _active_control_plane_routes(snapshot)
        or _active_control_plane_dispatcher_attempts(snapshot)
        or _active_control_plane_leases(snapshot)
        or _active_runtime_workspace_bindings(snapshot)
    )


def _active_control_plane_routes(snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not snapshot:
        return []
    routes: list[dict[str, Any]] = []
    for failure in snapshot.get("runtime_failures") or []:
        if not isinstance(failure, dict):
            continue
        route = str(failure.get("route") or "").strip().lower()
        failure_class = str(failure.get("failure_class") or "").strip().lower()
        failure_type = str(failure.get("failure_type") or "").strip().lower()
        status = str(failure.get("status") or "").strip().lower()
        if status in {"approved", "resolved", "cleared", "succeeded", "passed"}:
            continue
        if (
            route in _DETERMINISTIC_RUNTIME_RETRY_ROUTES
            or failure_class in _RUNTIME_WORKFLOW_BLOCKER_FAILURE_CLASSES
            or failure_type in _DETERMINISTIC_RUNTIME_FAILURE_TYPES
        ):
            routes.append({
                "evidence_node_id": failure.get("id"),
                "attempt_id": failure.get("attempt_id"),
                "group_idx": failure.get("group_idx"),
                "route": route,
                "failure_class": failure_class,
                "failure_type": failure_type,
                "status": status,
            })
    return routes[:10]


def _active_control_plane_dispatcher_attempts(
    snapshot: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not snapshot:
        return []
    attempts: list[dict[str, Any]] = []
    for attempt in snapshot.get("attempts") or []:
        if not isinstance(attempt, dict):
            continue
        status = str(attempt.get("status") or "").strip().lower()
        dispatcher_state = str(attempt.get("dispatcher_state") or "").strip().lower()
        if status in _TERMINAL_CONTROL_PLANE_WORK_STATUSES:
            continue
        if not (
            status in _ACTIVE_CONTROL_PLANE_WORK_STATUSES
            or dispatcher_state in _ACTIVE_DISPATCHER_STATES
        ):
            continue
        attempts.append({
            "attempt_id": attempt.get("id"),
            "entry_type": attempt.get("entry_type"),
            "status": status,
            "dispatcher_state": dispatcher_state,
            "actor": attempt.get("actor"),
            "group_idx": attempt.get("group_idx"),
            "task_id": attempt.get("task_id"),
        })
    return attempts[:10]


def _active_control_plane_leases(snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not snapshot:
        return []
    leases: list[dict[str, Any]] = []
    for lease in snapshot.get("sandbox_snapshots") or []:
        if not isinstance(lease, dict):
            continue
        status = str(lease.get("status") or "").strip().lower()
        if status in _ACTIVE_CONTROL_PLANE_WORK_STATUSES:
            leases.append({
                "sandbox_lease_id": lease.get("id"),
                "group_idx": lease.get("group_idx"),
                "attempt_no": lease.get("attempt_no"),
                "status": status,
                "lease_owner": lease.get("lease_owner"),
            })
    merge_queue = (
        snapshot.get("merge_queue")
        if isinstance(snapshot.get("merge_queue"), dict)
        else {}
    )
    merge_pending_count = int(merge_queue.get("pending_count") or 0)
    merge_items = [
        item
        for item in merge_queue.get("items") or []
        if isinstance(item, dict)
        and str(item.get("status") or "").strip().lower()
        in _ACTIVE_CONTROL_PLANE_WORK_STATUSES
    ]
    if merge_pending_count > 0 or merge_items:
        leases.append({
            "source": "merge_queue",
            "pending_count": merge_pending_count,
            "active_item_count": len(merge_items),
        })
    return leases[:10]


def _active_runtime_workspace_bindings(snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not snapshot:
        return []
    bindings: list[dict[str, Any]] = []
    binding_sources = [
        snapshot.get("runtime_workspace_bindings"),
        snapshot.get("workspace_bindings"),
    ]
    for source in binding_sources:
        for binding in source or []:
            if not isinstance(binding, dict):
                continue
            status = str(binding.get("status") or "").strip().lower()
            if status in _TERMINAL_CONTROL_PLANE_WORK_STATUSES:
                continue
            if status and status not in _ACTIVE_CONTROL_PLANE_WORK_STATUSES:
                continue
            bindings.append({
                "binding_id": binding.get("id") or binding.get("binding_id"),
                "attempt_id": binding.get("attempt_id"),
                "group_idx": binding.get("group_idx"),
                "status": status,
                "lease_owner": binding.get("lease_owner") or binding.get("owner"),
                "runtime": binding.get("runtime"),
            })
    for attempt in snapshot.get("attempts") or []:
        if not isinstance(attempt, dict):
            continue
        binding_id = attempt.get("runtime_workspace_binding_id")
        binding = _metadata_dict(attempt.get("runtime_workspace_binding"))
        if binding_id is None and not binding:
            continue
        status = str(attempt.get("status") or "").strip().lower()
        if status in _TERMINAL_CONTROL_PLANE_WORK_STATUSES:
            continue
        bindings.append({
            "binding_id": binding_id or binding.get("id"),
            "attempt_id": attempt.get("id"),
            "group_idx": attempt.get("group_idx"),
            "status": status,
            "runtime": attempt.get("runtime") or binding.get("runtime"),
        })
    return bindings[:10]


def _artifact_signal(artifact: ArtifactRecord) -> Any:
    if artifact.summary_only and artifact.value_preview:
        return artifact.value_preview
    return artifact.value


def _looks_successful_verify_artifact(artifact: ArtifactRecord) -> bool:
    signal = _artifact_signal(artifact)
    if artifact.key.startswith("dag-verify-graph:"):
        return _looks_approved_verification_graph(signal)
    return artifact.key.startswith("dag-verify:") and _looks_successful(signal)


def _looks_successful_gate_artifact(artifact: ArtifactRecord) -> bool:
    if artifact.key.startswith(_VERIFY_ARTIFACT_PREFIXES):
        return _looks_successful_verify_artifact(artifact)
    return _looks_successful(_artifact_signal(artifact))


def _looks_failed_gate_artifact(artifact: ArtifactRecord) -> bool:
    signal = _artifact_signal(artifact)
    if artifact.key.startswith("dag-verify-graph:"):
        return _looks_failed(signal) or _looks_rejected_verification_graph(signal)
    return _looks_failed(signal)


def _looks_product_defect_verify_failure_artifact(artifact: ArtifactRecord) -> bool:
    signal = _artifact_signal(artifact)
    if artifact.key.startswith("dag-verify:"):
        return _looks_failed(signal)
    if artifact.key.startswith("dag-verify-graph:"):
        return (
            _looks_failed_gate_artifact(artifact)
            and _looks_product_defect_verification_signal(signal)
        )
    return False


def _looks_rejected_verification_graph(value: Any) -> bool:
    parsed = _json_value(value) if isinstance(value, str) else value
    if isinstance(parsed, dict):
        aggregate = (
            parsed.get("aggregate")
            if isinstance(parsed.get("aggregate"), dict)
            else {}
        )
        aggregate_node = (
            parsed.get("aggregate_node")
            if isinstance(parsed.get("aggregate_node"), dict)
            else {}
        )
        if parsed.get("approved") is False or aggregate.get("approved") is False:
            return True
        statuses = {
            str(parsed.get("status") or "").strip().lower(),
            str(aggregate.get("status") or "").strip().lower(),
            str(aggregate_node.get("status") or "").strip().lower(),
        }
        if statuses & {"blocked", "failed", "rejected"}:
            return True
    lowered = _text(value).lower()
    return any(
        token in lowered
        for token in (
            '"approved": false',
            '"approved":false',
            '"status": "rejected"',
            '"status":"rejected"',
            "aggregate verdict is not approved",
        )
    ) and not _looks_approved_verification_graph(value)


def _looks_approved_verification_graph(value: Any) -> bool:
    parsed = _json_value(value) if isinstance(value, str) else value
    if isinstance(parsed, dict):
        aggregate = (
            parsed.get("aggregate")
            if isinstance(parsed.get("aggregate"), dict)
            else {}
        )
        aggregate_node = (
            parsed.get("aggregate_node")
            if isinstance(parsed.get("aggregate_node"), dict)
            else {}
        )
        if parsed.get("approved") is True:
            return True
        if parsed.get("approved") is False or aggregate.get("approved") is False:
            return False
        aggregate_status = str(aggregate.get("status") or "").strip().lower()
        aggregate_node_status = str(aggregate_node.get("status") or "").strip().lower()
        return (
            aggregate.get("approved") is True
            and aggregate_status not in {"blocked", "failed", "rejected"}
            and aggregate_node_status not in {"blocked", "failed", "rejected"}
        )
    return _looks_successful(value)


def _looks_product_defect_verification_signal(value: Any) -> bool:
    failure_classes = _failure_class_values(value)
    if any(item in _WORKFLOW_BLOCKER_FAILURE_CLASSES for item in failure_classes):
        return False
    if any(item in _PRODUCT_DEFECT_FAILURE_CLASSES for item in failure_classes):
        return True
    lowered = _text(value).lower()
    return any(token in lowered for token in _PRODUCT_EVIDENCE_TEXT_TOKENS)


def _workflow_blocker_context_artifacts(context: "_Context") -> list[ArtifactRecord]:
    artifacts = list(context.group_artifacts)
    artifacts.extend(
        artifact
        for artifact in context.artifacts
        if artifact.key.startswith(_WORKFLOW_BLOCKER_ARTIFACT_PREFIXES)
        and _artifact_group(artifact.key) is None
    )
    by_key: dict[tuple[int | None, str], ArtifactRecord] = {}
    for artifact in artifacts:
        by_key[(artifact.id, artifact.key)] = artifact
    return sorted(by_key.values(), key=lambda artifact: artifact.id or 0)


def _looks_workflow_blocker_artifact(artifact: ArtifactRecord) -> bool:
    signal = _artifact_signal(artifact)
    if artifact.key.startswith(_VERIFY_ARTIFACT_PREFIXES):
        return _looks_failed_gate_artifact(artifact) and _looks_workflow_blocker_signal(signal)
    if artifact.key.startswith("workflow-blocker:"):
        return True
    if artifact.key.startswith("dag-runtime-failure:"):
        if _runtime_failure_artifact_is_source_push_workflow_blocker(artifact):
            return True
        if _runtime_failure_artifact_is_non_unblock(signal):
            return False
        lowered = _text(signal).lower()
        return _looks_workflow_blocker_signal(signal) or any(
            token in lowered
            for token in (
                "blocked_before_product_repair",
                "blocked_before_checkpoint",
                "deterministic_workflow_blocker",
                "quiesce_workflow",
                "typed_runtime_blocker",
            )
        )
    if artifact.key.startswith("dag-task-pending-merge:"):
        lowered = _text(signal).lower()
        return any(
            token in lowered
            for token in (
                "canonical_mutation=pending_durable_merge_queue",
                "pending durable merge queue",
                "durable merge queue",
            )
        )
    return False


def _looks_workflow_blocker_signal(value: Any) -> bool:
    failure_classes = _failure_class_values(value)
    if any(item in _WORKFLOW_BLOCKER_FAILURE_CLASSES for item in failure_classes):
        return True
    failure_types = _metadata_field_values(
        value,
        {"failure_type", "blocking_failure_type"},
    )
    if any(item in _WORKFLOW_BLOCKER_FAILURE_TYPES for item in failure_types):
        return True
    if failure_classes and all(item in _PRODUCT_DEFECT_FAILURE_CLASSES for item in failure_classes):
        return False
    lowered = _text(value).lower()
    if "sandbox_workflow_blocker" in lowered:
        return True
    return any(token in lowered for token in _WORKFLOW_BLOCKER_TEXT_TOKENS)


def _workflow_blocker_failure_classes(artifacts: list[ArtifactRecord]) -> list[str]:
    values: list[str] = []
    for artifact in artifacts:
        values.extend(_failure_class_values(_artifact_signal(artifact)))
    return _dedupe(values)


def _failure_class_values(value: Any) -> list[str]:
    return _metadata_field_values(value, {"failure_class", "blocking_failure_class"})


def _metadata_field_values(value: Any, field_names: set[str]) -> list[str]:
    parsed = _json_value(value) if isinstance(value, str) else value
    values: list[str] = []

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                key_text = str(key)
                if key_text in field_names and child is not None:
                    values.append(str(child).strip().lower())
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(parsed)
    return _dedupe(values)


def _json_value(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _runtime_failure_artifact_is_non_unblock(value: Any) -> bool:
    routes = _metadata_field_values(value, {"route", "decision_route"})
    failure_classes = _failure_class_values(value)
    failure_types = _metadata_field_values(value, {"failure_type", "blocking_failure_type"})
    return (
        any(route in _NON_UNBLOCK_RUNTIME_ROUTES for route in routes)
        or any(
            failure_class in _NON_UNBLOCK_RUNTIME_FAILURE_CLASSES
            for failure_class in failure_classes
        )
        or any(
            failure_type in _NON_UNBLOCK_RUNTIME_FAILURE_TYPES
            for failure_type in failure_types
        )
    )


def _runtime_failure_artifact_is_source_push_workflow_blocker(artifact: ArtifactRecord) -> bool:
    if not artifact.key.startswith("dag-runtime-failure:source-push"):
        return False
    signal = _artifact_signal(artifact)
    failure_types = _metadata_field_values(signal, {"failure_type", "blocking_failure_type"})
    routes = _metadata_field_values(signal, {"route", "decision_route"})
    parsed = _json_value(signal) if isinstance(signal, str) else signal
    blocked_before_checkpoint = False
    if isinstance(parsed, dict):
        blocked_before_checkpoint = _metadata_bool(parsed, "blocked_before_checkpoint")
    return (
        "quiesce_workflow" in routes
        and any(
            failure_type.startswith("source_push")
            or failure_type.startswith("post_test_source_push")
            for failure_type in failure_types
        )
        and blocked_before_checkpoint
    )


def _looks_successful(value: Any) -> bool:
    lowered = _text(value).lower()
    if any(token in lowered for token in ('"approved": true', '"approved":true')):
        return True
    return any(token in lowered for token in ("passed", "succeeded", '"ok": true')) and "failed" not in lowered


def _looks_reconcile_successful(value: Any) -> bool:
    if _looks_failed(value):
        return False
    if _looks_successful(value):
        return True
    lowered = _text(value).lower()
    return any(
        token in lowered
        for token in (
            '"applied": true',
            '"applied":true',
            '"reconciled": true',
            '"reconciled":true',
            "reconcile applied",
            "applied stale",
            "applied canonical",
            "updated task spec",
            "canonical replacement applied",
        )
    )


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
            "worktree alias",
            "worktree_alias",
            "non-canonical worktree",
            "non-canonical repo alias",
            "dag-worktree-alias",
            "canonical_product_repair",
            "focused canonical repair",
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
_VERIFY_ARTIFACT_PREFIXES = (
    "dag-verify:",
    "dag-verify-graph:",
)
_CHECKPOINT_GATE_FAILURE_ARTIFACT_PREFIXES = (
    *_VERIFY_ARTIFACT_PREFIXES,
    "dag-repair-preflight:",
)
_OPERATOR_REQUIRED_ARTIFACT_PREFIXES = (
    "dag-direct-repair-route:",
    "dag-workspace-acl-normalization:",
    "dag-workspace-permission-repair:",
    "dag-writeability-preflight:",
    "workspace-authority-",
)
_OPERATOR_REQUIRED_RUNTIME_ROUTES = {
    "operator_required",
}
_OPERATOR_REQUIRED_RUNTIME_FAILURE_CLASSES = {
    "workspace_permission",
}
_OPERATOR_REQUIRED_RUNTIME_FAILURE_TYPES = {
    "writeability_denied",
    "writeability_file_denied",
    "writeability_directory_denied",
}
_TERMINAL_RUNTIME_FAILURE_STATUSES = {
    "approved",
    "cleared",
    "passed",
    "resolved",
    "succeeded",
}
_TERMINAL_CONTROL_PLANE_WORK_STATUSES = {
    "approved",
    "cancelled",
    "canceled",
    "cleared",
    "complete",
    "completed",
    "done",
    "failed",
    "finished",
    "passed",
    "retained",
    "resolved",
    "succeeded",
    "terminal",
}
_ACTIVE_CONTROL_PLANE_WORK_STATUSES = {
    "active",
    "allocating",
    "allocated",
    "bound",
    "binding",
    "capturing",
    "applying",
    "checkpointing",
    "claimed",
    "committing",
    "integrated",
    "in_progress",
    "leased",
    "pending",
    "queued",
    "rebasing",
    "requested",
    "running",
    "started",
    "verifying",
}
_ACTIVE_DISPATCHER_STATES = {
    "attempt_started",
    "context_prepared",
    "dispatch_requested",
    "evidence_recording",
    "runtime_invocation_started",
    "runtime_invoked",
    "runtime_running",
}
_WORKFLOW_BLOCKER_ARTIFACT_PREFIXES = (
    "dag-runtime-failure:",
    "dag-task-pending-merge:",
    "workflow-blocker:",
)
_WORKFLOW_BLOCKER_FAILURE_CLASSES = {
    "acl_workability",
    "aggregate.conflict",
    "checkpoint_contradiction",
    "commit_hygiene",
    "contract_compile",
    "evidence_corruption",
    "merge_queue",
    "merge_conflict",
    "runtime_context",
    "runtime_structured_output",
    "sandbox_binding",
    "stale_context",
    "stale_projection",
    "verifier_context",
    "verifier_provider",
    "workspace_dirty",
    "worktree_alias",
}
_WORKFLOW_BLOCKER_FAILURE_TYPES = {
    "checkpoint_after_failed_gate",
    "checkpoint_contradiction",
    "commit_hygiene",
    "commit_hook_failed",
    "context_materialization_failed",
    "contract_compile",
    "contract_compile_failed",
    "merge_conflict",
    "pending_durable_merge_queue",
    "projection_body_conflict",
    "sandbox_binding_failed",
    "sandbox_lease_active",
    "stale_context",
    "stale_projection",
    "stale_projection_snapshot",
    "unwritable_runtime_path",
    "verifier_context_materialization_failed",
    "worktree_alias",
    "worktree_alias_path",
}
_RUNTIME_WORKFLOW_BLOCKER_FAILURE_CLASSES = {
    *_WORKFLOW_BLOCKER_FAILURE_CLASSES,
    "acl_workability",
    "aggregate.conflict",
    "commit_hygiene",
    "contract_compile",
    "evidence_corruption",
    "merge_queue",
    "merge_conflict",
    "runtime_provider",
    "runtime_timeout",
    "runtime_structured_output",
    "sandbox_binding",
    "stale_context",
    "stale_projection",
    "resource_exhausted",
    "worktree_alias",
    "verifier_context",
    "workspace_dirty",
}
_DETERMINISTIC_UNBLOCK_RUNTIME_FAILURE_CLASSES = {
    "acl_workability",
    "commit_hygiene",
    "contract_compile",
    "merge_queue",
    "merge_conflict",
    "runtime_context",
    "runtime_structured_output",
    "sandbox_allocation",
    "sandbox_binding",
    "sandbox_capture",
    "sandbox_cleanup",
    "stale_context",
    "stale_projection",
    "worktree_alias",
    "verifier_context",
    "workspace_dirty",
}
_NON_UNBLOCK_RUNTIME_FAILURE_CLASSES = {
    "resource_exhausted",
    "runtime_provider",
    "runtime_timeout",
    "verifier_provider",
}
_NON_UNBLOCK_RUNTIME_FAILURE_TYPES = {
    "provider_crash",
    "provider_rate_limited",
    "resource_exhausted",
    "runtime_timeout",
}
_DETERMINISTIC_RUNTIME_RETRY_ROUTES = {
    "commit_hygiene",
    "contract_compile",
    "deterministic_unblock",
    "host_reconcile",
    "merge_queue",
    "merge_conflict",
    "run_canonicalization_repair",
    "run_commit_hygiene_repair",
    "run_contract_repair",
    "run_workspace_repair",
    "retry_context",
    "retry_contract",
    "retry_dispatch",
    "retry_merge",
    "retry_projection",
    "retry_sandbox",
    "retry_sandbox_capture",
    "run_sandbox_cleanup",
    "stale_projection",
    "worktree_alias",
    "workflow_unblock",
}
_NON_UNBLOCK_RUNTIME_ROUTES = {
    "quiesce",
    "quiesce_workflow",
    "resource_exhausted",
}
_DETERMINISTIC_RUNTIME_FAILURE_TYPES = {
    "commit_hygiene",
    "context_materialization_failed",
    "contract_compile",
    "contract_compile_failed",
    "merge_conflict",
    "pending_durable_merge_queue",
    "dirty_snapshot_before_dispatch",
    "sandbox_binding_failed",
    "sandbox_allocation_failed",
    "sandbox_capture_failed",
    "sandbox_cleanup_failed",
    "sandbox_lease_active",
    "stale_context",
    "stale_projection",
    "stale_projection_snapshot",
    "verifier_context_materialization_failed",
    "unwritable_runtime_path",
    "writeability_denied",
    "writeability_directory_denied",
    "writeability_file_denied",
    "worktree_alias",
    "worktree_alias_path",
}
_PRODUCT_DEFECT_FAILURE_CLASSES = {
    "product_defect",
}
_PRODUCT_REPAIR_CONTRACT_FAILURE_CLASSES = {
    "contract_violation",
}
_PRODUCT_EVIDENCE_BOOL_FIELDS = {
    "product_evidence",
    "product_defect",
    "semantic_product_failure",
}
_PRODUCT_EVIDENCE_LIST_FIELDS = {
    "affected_product_files",
    "canonical_product_files",
    "concerns",
    "issues",
    "product_files",
    "product_paths",
    "test_failures",
}
_PRODUCT_EVIDENCE_TEXT_TOKENS = (
    "assertion failed",
    "canonical product file",
    "component assertion",
    "product file",
    "product regression",
    "pytest failure",
    "semantic verifier rejected",
    "test failure",
)
_NON_PRODUCT_REPAIR_RUNTIME_ROUTES = {
    *_NON_UNBLOCK_RUNTIME_ROUTES,
}
_PIPELINE_BUG_RUNTIME_FAILURE_CLASSES = {
    "checkpoint_contradiction",
    "dispatcher_internal",
    "evidence_corruption",
    "sandbox_isolation",
}
_PIPELINE_BUG_QUIESCE_FAILURE_CLASSES = {
    *_PIPELINE_BUG_RUNTIME_FAILURE_CLASSES,
    "acl_workability",
    "checkpoint_contradiction",
    "commit_hygiene",
    "contract_compile",
    "contract_violation",
    "merge_conflict",
    "product_defect",
    "regroup_invalid",
    "resource_exhausted",
    "runtime_context",
    "runtime_structured_output",
    "sandbox_allocation",
    "sandbox_capture",
    "sandbox_cleanup",
    "stale_projection",
    "unknown",
    "worktree_alias",
}
_WORKFLOW_BLOCKER_TEXT_TOKENS = (
    "acl_workability",
    "aggregate.conflict",
    "checkpoint contradiction",
    "checkpoint_contradiction",
    "commit_hygiene",
    "context materialization failed",
    "context_materialization_failed",
    "contract_compile",
    "deterministic_workflow_blocker",
    "durable merge queue",
    "evidence_corruption",
    "merge_conflict",
    "pending durable merge queue",
    "pending_durable_merge_queue",
    "projection_body_conflict",
    "runtime context",
    "runtime_context",
    "sandbox_binding",
    "stale context",
    "stale_context",
    "stale projection",
    "stale_projection",
    "verifier context",
    "verifier provider",
    "verifier_context",
    "verifier_context_materialization_failed",
    "verifier_provider",
    "worktree_alias",
    '"failure_class": "verifier_provider"',
    '"failure_class":"verifier_provider"',
    "'failure_class': 'verifier_provider'",
    "'failure_class':'verifier_provider'",
    '"blocking_failure_class": "verifier_provider"',
    '"blocking_failure_class":"verifier_provider"',
    "'blocking_failure_class': 'verifier_provider'",
    "'blocking_failure_class':'verifier_provider'",
    '"failure_class": "runtime_context"',
    '"failure_class":"runtime_context"',
    "'failure_class': 'runtime_context'",
    "'failure_class':'runtime_context'",
    '"blocking_failure_class": "runtime_context"',
    '"blocking_failure_class":"runtime_context"',
    "'blocking_failure_class': 'runtime_context'",
    "'blocking_failure_class':'runtime_context'",
)
_DETERMINISTIC_UNBLOCK_ARTIFACT_PREFIXES = (
    "dag-repair-preflight:",
    "dag-authority-gate:",
    "dag-task-reconcile:",
    "dag-task-spec-reconcile:",
    "dag-merge-queue:",
    *_WORKFLOW_BLOCKER_ARTIFACT_PREFIXES,
    "dag-worktree-alias-preflight:",
    "dag-worktree-alias-canonicalization:",
    "workspace-authority-",
    "runtime-workspace-binding:",
    "dag-runtime-workspace-binding:",
    *_OPERATOR_REQUIRED_ARTIFACT_PREFIXES,
)
_MATERIAL_SUPERSEDING_ARTIFACT_PREFIXES = (
    "dag-authority-gate:",
    *_OPERATOR_REQUIRED_ARTIFACT_PREFIXES,
    "dag-repair-preflight:",
    "dag-verify:",
    "dag-verify-graph:",
    "dag-repair-lens:",
    "dag-repair-expanded-verify:",
    "dag-verify-rca:",
    "dag-fix:",
    "dag-direct-repair-route:",
    "dag-task-reconcile:",
    "dag-task-spec-reconcile:",
    "dag-task-product-reconcile:",
    "dag-merge-queue:",
    *_WORKFLOW_BLOCKER_ARTIFACT_PREFIXES,
    "dag-worktree-alias-preflight:",
    "dag-worktree-alias-canonicalization:",
    "dag-group:",
    "finding-ledger",
)


def _latest_material_artifacts(
    context: "_Context",
    *,
    after_id: int = 0,
    limit: int = 8,
) -> list[ArtifactRecord]:
    artifacts = [
        artifact
        for artifact in context.group_artifacts
        if (artifact.id or 0) > after_id
        and not artifact.key.startswith("dag-verify:")
        and not artifact.key.startswith("dag-verify-graph:")
        and artifact.key.startswith(_MATERIAL_SUPERSEDING_ARTIFACT_PREFIXES)
    ]
    return sorted(artifacts, key=lambda artifact: artifact.id or 0)[-limit:]


def _looks_deterministic_unblock_artifact(artifact: ArtifactRecord) -> bool:
    signal = _artifact_signal(artifact)
    if (
        artifact.key.startswith(_OPERATOR_REQUIRED_ARTIFACT_PREFIXES)
        and _looks_operator_required(signal)
    ):
        return False
    if artifact.key.startswith("dag-runtime-failure:") and _runtime_failure_artifact_is_non_unblock(signal):
        return False
    if _looks_stale_or_path_problem(signal):
        return True
    lowered = _text(signal).lower()
    if artifact.key.startswith(
        (
            "workspace-authority-",
            "runtime-workspace-binding:",
            "dag-runtime-workspace-binding:",
        )
    ):
        return _looks_failed(signal) or any(
            token in lowered
            for token in (
                "drift",
                "mismatch",
                "stale",
                "blocked",
                "missing",
                "registry_digest",
                "workspace_snapshot",
                "runtime_workspace_binding",
            )
        )
    return any(
        token in lowered
        for token in (
            '"deterministic": true',
            '"deterministic":true',
            "deterministic workflow",
            "deterministic_unblock",
            "control-plane",
            "control_plane",
            "workspace authority",
            "workspace_authority",
            "runtime workspace",
            "runtime_workspace",
            "registry_digest",
            "projection mismatch",
            "idempotency mismatch",
        )
    )


def _operator_required_blocker_cleared(
    context: "_Context",
    blocker: ArtifactRecord | EventRecord,
) -> bool:
    return any(
        _record_is_newer(clearance, blocker)
        for clearance in _operator_clearance_artifacts(context)
    )


def _operator_clearance_artifacts(context: "_Context") -> list[ArtifactRecord]:
    artifacts: list[ArtifactRecord] = []
    for artifact in context.group_artifacts:
        signal = _artifact_signal(artifact)
        if _looks_successful_verify_artifact(artifact):
            artifacts.append(artifact)
            continue
        if (
            artifact.key.startswith(_OPERATOR_REQUIRED_ARTIFACT_PREFIXES)
            and _looks_operator_cleared(signal)
            and not _looks_operator_required(signal)
        ):
            artifacts.append(artifact)
    return artifacts


def _checkpoint_artifact_after_failed_gate(
    checkpoint: ArtifactRecord,
    failed_raw: list[ArtifactRecord],
) -> bool:
    latest_failed_id = max((artifact.id or 0) for artifact in failed_raw)
    if latest_failed_id and (checkpoint.id or 0) > latest_failed_id:
        return True
    latest_failed_time = _latest_record_time(failed_raw)
    return (
        latest_failed_time is not None
        and checkpoint.created_at is not None
        and checkpoint.created_at > latest_failed_time
    )


def _checkpoint_event_after_failed_gate(
    checkpoint: EventRecord,
    failed_raw: list[ArtifactRecord],
) -> bool:
    latest_failed_time = _latest_record_time(failed_raw)
    return (
        latest_failed_time is not None
        and checkpoint.created_at is not None
        and checkpoint.created_at > latest_failed_time
    )


def _latest_record_time(records: list[ArtifactRecord | EventRecord]) -> datetime | None:
    return max(
        (
            record.created_at
            for record in records
            if record.created_at is not None
        ),
        default=None,
    )


def _record_is_newer(left: ArtifactRecord | EventRecord, right: ArtifactRecord | EventRecord) -> bool:
    if isinstance(left, ArtifactRecord) and isinstance(right, ArtifactRecord):
        left_id = left.id or 0
        right_id = right.id or 0
        if left_id and right_id:
            return left_id > right_id
    left_time = getattr(left, "created_at", None)
    right_time = getattr(right, "created_at", None)
    return left_time is not None and right_time is not None and left_time > right_time


def _product_runtime_cleared_by_successful_verify(
    event: EventRecord,
    successful_verify: list[ArtifactRecord],
) -> bool:
    if not successful_verify:
        return False
    event_time = event.created_at
    if event_time is None:
        return True
    dated_success = [
        artifact
        for artifact in successful_verify
        if artifact.created_at is not None
    ]
    if dated_success:
        return any(artifact.created_at > event_time for artifact in dated_success)
    return False


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


def _has_newer_material_evidence(
    context: "_Context",
    records: list[Any],
    *,
    deterministic_blocker: bool = False,
) -> bool:
    if not deterministic_blocker and _has_newer_progress_event(context, records):
        return True
    latest_artifact_id = max(
        (
            record.id or 0
            for record in records
            if isinstance(record, ArtifactRecord)
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
    current = context.observation.current
    if current is not None and current.group_idx == context.group_idx:
        if (
            not deterministic_blocker
            and latest_artifact_id
            and current.latest_artifact_id is not None
            and current.latest_artifact_id > latest_artifact_id
        ):
            return True
        if not deterministic_blocker and current.active_agents:
            return True
    for artifact in context.group_artifacts:
        if not artifact.key.startswith(_MATERIAL_SUPERSEDING_ARTIFACT_PREFIXES):
            continue
        if deterministic_blocker and not _clears_deterministic_blocker(artifact):
            continue
        if latest_artifact_id and (artifact.id or 0) > latest_artifact_id:
            return True
        if (
            latest_time is not None
            and artifact.created_at is not None
            and artifact.created_at > latest_time
        ):
            return True
    return False


def _clears_deterministic_blocker(artifact: ArtifactRecord) -> bool:
    signal = _artifact_signal(artifact)
    if _looks_successful_verify_artifact(artifact):
        return True
    if (
        artifact.key.startswith(
            (
                "dag-worktree-alias-preflight:",
                "dag-worktree-alias-canonicalization:",
            )
        )
        and _looks_successful(signal)
    ):
        return True
    if (
        artifact.key.startswith(
            (
                "dag-task-reconcile:",
                "dag-task-spec-reconcile:",
            )
        )
        and _looks_reconcile_successful(signal)
    ):
        return True
    if artifact.key.startswith(_OPERATOR_REQUIRED_ARTIFACT_PREFIXES):
        return _looks_operator_cleared(signal) and not _looks_operator_required(signal)
    if artifact.key.startswith(_WORKFLOW_BLOCKER_ARTIFACT_PREFIXES):
        return _looks_explicit_resolution_successful(signal)
    if artifact.key.startswith("dag-merge-queue:"):
        return _looks_explicit_resolution_successful(signal)
    return False


def _looks_explicit_resolution_successful(value: Any) -> bool:
    if _looks_successful(value):
        return True
    lowered = _text(value).lower()
    resolved = any(
        token in lowered
        for token in (
            '"resolved": true',
            '"resolved":true',
            '"cleared": true',
            '"cleared":true',
            '"blocked": false',
            '"blocked":false',
            '"status": "resolved"',
            '"status":"resolved"',
            '"status": "cleared"',
            '"status":"cleared"',
            '"resolution": "resolved"',
            '"resolution":"resolved"',
            '"resolution_status": "resolved"',
            '"resolution_status":"resolved"',
        )
    )
    if not resolved:
        return False
    return not any(
        token in lowered
        for token in (
            '"approved": false',
            '"approved":false',
            '"cleared": false',
            '"cleared":false',
            '"resolved": false',
            '"resolved":false',
            '"status": "blocked"',
            '"status":"blocked"',
            '"status": "failed"',
            '"status":"failed"',
        )
    )


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
