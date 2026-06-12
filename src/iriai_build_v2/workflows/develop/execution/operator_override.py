"""Operator task-override marker — first-class, audited gate/task fiat-completion.

Formalizes the 2026-06-11 operator precedent (live artifacts row 2248683,
feature 5b280bb4: a hand-INSERTed ``dag-task:TASK-RCAN-00-UPSTREAM-GATES`` row
with ``status="completed"`` and an OPERATOR-OVERRIDE summary) as a supported
two-step mechanism:

1. **Write** — the ``iriai-build-v2 override-task`` CLI subcommand records a
   durable :class:`OperatorTaskOverride` marker at
   ``dag-task-operator-override:{task_id}`` via the normal
   ``PostgresArtifactStore.put`` path (append-only INSERT; ``get`` returns the
   newest row — ``storage/artifacts.py:97-106`` / ``386-404``). The marker
   captures the operator's authorization (target status, reason,
   authorized_by, timestamp provenance) WITHOUT touching the engine-owned
   ``dag-task:*`` namespace.

2. **Consume** — the implementation dispatch loop's per-task resume block
   (``phases/implementation.py``, ``_implement_dag_dispatch_loop``) checks for
   a valid marker BEFORE dispatching the task. On consumption it persists the
   terminal ``dag-task:{task_id}`` row exactly the way the engine does on
   normal completion (``await put(f"dag-task:{task.id}",
   result.model_dump_json(), feature=feature)`` —
   ``phases/implementation.py:11072``), with operator-override provenance in
   the summary/notes, and skips execution. Consumption is single-shot: once
   the terminal ``dag-task:*`` row exists, later boots short-circuit on THAT
   row (recognized via :func:`result_is_operator_override`) and never
   re-consume the marker.

The marker-key shape mirrors the existing per-task marker convention
(``_pending_merge_queue_marker_key`` → ``dag-task-pending-merge:{task_id}``,
``phases/implementation.py:5394``). The synthesized result deliberately does
NOT carry the ``canonical_mutation=pending_durable_merge_queue`` note
(``phases/implementation.py:5380``), so it never trips the durable-merge-queue
resume blocker; like the precedent row it has empty
``files_created``/``files_modified``/``commit_hash``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from ....models.outputs import ImplementationResult

# Marker-key namespace for operator-authorized task overrides. Deliberately
# OUTSIDE the engine-owned `dag-task:` prefix so writing a marker can never be
# mistaken for (or clobber) a real task result; the engine alone writes the
# terminal `dag-task:{task_id}` row when it consumes the marker.
OPERATOR_OVERRIDE_MARKER_PREFIX = "dag-task-operator-override:"

# Machine-readable token stamped into the synthesized ImplementationResult's
# `notes` on consumption. Later boots recognize the terminal `dag-task:*` row
# as operator-overridden via this token (`result_is_operator_override`) and
# short-circuit it WITHOUT the contract-lineage revalidation a normally
# dispatched completion must pass (an override has no contract verdict or
# sandbox lineage to revalidate).
OPERATOR_OVERRIDE_RESULT_NOTE = "operator_override=fiat_completed"

# Provenance value for markers written by the supported CLI path.
OPERATOR_OVERRIDE_CLI_SOURCE = "iriai-build-v2 override-task"

# The only override target status supported today. The precedent (row 2248683)
# was a fiat COMPLETION; other terminal states have no consumption semantics
# defined and are refused at both the write (CLI) and consume (loop) ends.
SUPPORTED_OVERRIDE_STATUSES: tuple[str, ...] = ("completed",)


def operator_override_marker_key(task_id: str) -> str:
    """``dag-task-operator-override:{task_id}`` (mirrors the
    ``dag-task-pending-merge:{task_id}`` per-task marker convention)."""
    return f"{OPERATOR_OVERRIDE_MARKER_PREFIX}{task_id}"


class OperatorTaskOverride(BaseModel):
    """Durable, audited record of an operator-authorized task override.

    Stored verbatim (``model_dump_json``) as the value of the
    ``dag-task-operator-override:{task_id}`` artifact row. All fields are flat
    primitives per the project's flat-structured-output rule.
    """

    schema_version: int = 1
    task_id: str
    target_status: Literal["completed"] = "completed"
    reason: str
    authorized_by: str = "operator"
    feature_id: str = ""
    # ISO-8601 UTC timestamp recorded by the WRITER (the artifact row's own
    # `created_at` is the store-side timestamp; this one survives copies).
    created_at: str = ""
    source: str = OPERATOR_OVERRIDE_CLI_SOURCE

    @field_validator("task_id")
    @classmethod
    def _task_id_non_empty(cls, value: str) -> str:
        if not str(value or "").strip():
            raise ValueError("task_id must be non-empty")
        return value.strip()

    @field_validator("reason")
    @classmethod
    def _reason_non_empty(cls, value: str) -> str:
        if not str(value or "").strip():
            raise ValueError(
                "reason must be non-empty — an operator override is an audited "
                "action and requires a recorded justification"
            )
        return value.strip()


def new_operator_override(
    *,
    task_id: str,
    reason: str,
    authorized_by: str,
    feature_id: str,
    target_status: str = "completed",
) -> OperatorTaskOverride:
    """Build a fully-populated override record with a fresh UTC timestamp."""
    if target_status not in SUPPORTED_OVERRIDE_STATUSES:
        raise ValueError(
            f"unsupported override target_status {target_status!r}; supported: "
            f"{', '.join(SUPPORTED_OVERRIDE_STATUSES)}"
        )
    return OperatorTaskOverride(
        task_id=task_id,
        target_status="completed",
        reason=reason,
        authorized_by=authorized_by or "operator",
        feature_id=feature_id,
        created_at=datetime.now(timezone.utc).isoformat(),
        source=OPERATOR_OVERRIDE_CLI_SOURCE,
    )


def parse_operator_override(raw: object) -> OperatorTaskOverride:
    """Strictly parse a stored marker value. Raises ``ValueError`` on any
    malformed payload — the consumer fails LOUD, never silently skips."""
    text = str(raw or "").strip()
    if not text:
        raise ValueError("operator override marker is empty")
    try:
        return OperatorTaskOverride.model_validate_json(text)
    except Exception as exc:  # noqa: BLE001 - normalized to one loud type.
        raise ValueError(
            f"operator override marker is not a valid OperatorTaskOverride: {exc}"
        ) from exc


def overrides_equivalent(
    a: OperatorTaskOverride, b: OperatorTaskOverride
) -> bool:
    """Idempotency comparison: same intent regardless of write timestamp."""
    return (
        a.task_id == b.task_id
        and a.target_status == b.target_status
        and a.reason == b.reason
        and a.authorized_by == b.authorized_by
        and a.feature_id == b.feature_id
    )


def build_override_result(override: OperatorTaskOverride) -> ImplementationResult:
    """Synthesize the terminal ``ImplementationResult`` the dispatch loop
    persists to ``dag-task:{task_id}`` when it consumes the marker.

    Shape mirrors the operator precedent row 2248683: ``status="completed"``,
    empty file lists / commit hash, OPERATOR-OVERRIDE provenance up front in
    the summary, machine-readable provenance in ``notes``. The notes carry
    :data:`OPERATOR_OVERRIDE_RESULT_NOTE` (second-boot recognition) and NEVER
    the pending-durable-merge-queue token.
    """
    provenance = {
        "marker_key": operator_override_marker_key(override.task_id),
        "authorized_by": override.authorized_by,
        "authorized_at": override.created_at,
        "source": override.source,
        "feature_id": override.feature_id,
    }
    return ImplementationResult(
        task_id=override.task_id,
        summary=(
            "OPERATOR-OVERRIDE TASK COMPLETION (operator-authorized; "
            f"authorized_by={override.authorized_by}; "
            f"recorded {override.created_at or 'at marker write'}). "
            f"Reason: {override.reason}"
        ),
        status="completed",
        files_created=[],
        files_modified=[],
        commit_hash="",
        notes=(
            f"{OPERATOR_OVERRIDE_RESULT_NOTE}; "
            f"provenance={json.dumps(provenance, sort_keys=True)}"
        ),
    )


def result_is_operator_override(result: ImplementationResult) -> bool:
    """True when a terminal ``dag-task:*`` result row was produced by
    operator-override consumption (recognized on later boots)."""
    return OPERATOR_OVERRIDE_RESULT_NOTE in str(result.notes or "")
