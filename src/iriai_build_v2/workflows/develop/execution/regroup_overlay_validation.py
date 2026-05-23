"""The 13-step deterministic regroup-overlay validator (Slice 09b-2).

Slice 09 generalizes the one-off ``G45-G73`` derived-DAG regroup into a
reusable typed *overlay*. 09a delivered the typed models (``regroup_overlay``),
09b delivered the store (``regroup_overlay_store``). This module (``09b-2``)
delivers :func:`validate_overlay` — the deterministic 13-step
``validate_overlay`` algorithm from
``docs/execution-control-plane/09-regroup-overlay-and-scheduler-feedback.md``
§ "Validation Algorithm".

**Why a sibling module (not ``regroup_overlay.py``).** doc 09 § "Proposed
Interfaces/Types" lists validation as something ``regroup_overlay.py`` "owns",
but the validator must call :class:`~iriai_build_v2.execution_control.regroup_overlay_store.RegroupOverlayStore`
(to load ``source_dag_key`` in step 2 and persist in step 13), and
``regroup_overlay_store`` already imports ``regroup_overlay`` — putting the
validator in ``regroup_overlay.py`` would be a hard circular import. The
09a/09b no-refactor discipline (STATUS.md "Loop discipline") forbids editing
the working ``regroup_overlay.py`` 09a model module, so the validator lands in
this sibling leaf module, importing both the 09a models and the 09b store.
The split is recorded in the implementation journal.

**Determinism.** The algorithm uses only sorted keys, fixed iteration order,
and no clock / random source. The ``validation_digest`` is
:func:`~iriai_build_v2.execution_control.models.stable_digest` over the
canonical normalized overlay form plus the sorted rejection reason, so two runs
over the same inputs always produce the same digest. A non-deterministic digest
would be a P1 — see the determinism note on :func:`_compute_validation_digest`.

**Scope boundary.** Validation NEVER widens a wave because of scheduler
metrics; that is Slice 09d's separate concern. This validator is the *gate*: it
only ever rejects or normalizes; it cannot rewrite an overlay's waves.

The 13 steps (doc 09 § "Validation Algorithm", numbered exactly):

1. Parse the typed overlay / compatibility ``DerivedDAGArtifact``, normalize
   group ids to absolute indexes, normalize/sort write sets, compute the
   canonical overlay sha; reject malformed/wrong-schema/mismatched-key.
2. Load ``source_dag_key`` through the store; reject unless the loaded id+sha
   exactly match ``base_dag_artifact_id`` / ``base_dag_sha256``.
3. Verify ``checkpointed_group + 1 == group_idx_offset``, the checkpointed
   group exists, ``original_execution_order`` equals the base suffix.
4. Build task-id multisets; reject missing / extra / duplicate / unknown.
5. Compare task-definition fingerprints — only group placement may change.
6. Compare ``remaining_dependency_edges`` to the base suffix exactly.
7. Build ``derived_group_by_task``; reject unknown / same-wave / after-dependent
   dependencies.
8. Validate ``original_to_new_group_mapping``.
9. Compile hard barriers; reject a derived group mixing hard barriers.
10. Compile authoritative write sets; reject same-wave overlap + widened waves
    with any ``unknown_write`` task.
11. Validate the activation + rollback contracts against the normalized first
    derived wave.
12. Validate :class:`~iriai_build_v2.workflows.develop.execution.regroup_overlay.RegroupActiveMarker`
    on activation/resolver checks — any mismatch fails closed.
13. Emit a typed ``execution_regroup_validations`` row + a
    ``dag-regroup-validation:*`` compatibility artifact in ONE transaction via
    :meth:`RegroupOverlayStore.record_validation`; idempotent on
    ``(overlay_id, validation_digest)``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from ....execution_control.models import stable_digest
from ....models.outputs import DerivedDAGArtifact, ImplementationDAG, ImplementationTask
from .regroup_overlay import (
    RegroupActiveMarker,
    RegroupOverlay,
)

if TYPE_CHECKING:  # pragma: no cover - typing only (avoids circular import)
    from ....execution_control.regroup_overlay_store import (
        OverlayValidationRecord,
        RegroupOverlayStore,
    )

__all__ = [
    "OverlayValidationResult",
    "OverlayValidationContext",
    "validate_overlay",
]


# ── Result + context models ─────────────────────────────────────────────────


class OverlayValidationResult(BaseModel):
    """The structured outcome of :func:`validate_overlay` (doc 09 § "Validation").

    doc 09 § "Validation Algorithm": "``validate_overlay(candidate,
    base_context, activation_check=False)`` returns ``OverlayValidationResult(
    valid, reason, details, evidence_ids, normalized)``."

    Fields:

    - ``valid`` — True only when all applicable steps passed.
    - ``reason`` — ``""`` on success; otherwise the deterministic
      machine-stable rejection code (e.g. ``dag_regroup_base_dag_hash_mismatch``)
      naming the first failing step.
    - ``details`` — bounded structured detail of the failure / success summary.
    - ``evidence_ids`` — typed evidence node ids the validation cited (sorted).
    - ``normalized`` — the normalized canonical :class:`RegroupOverlay` (the
      candidate with group ids absolutized, write sets sorted, and
      ``overlay_sha256`` recomputed). ``None`` only when parsing in step 1
      failed before a typed overlay could be built.
    - ``validation_digest`` — the deterministic digest used as the
      ``(overlay_id, validation_digest)`` idempotency key in step 13. It is
      reproducible across runs over identical inputs.
    - ``failed_step`` — the 1-based step index that produced ``reason`` (``0``
      on success). Diagnostic only.

    This is not agent-facing structured output, so the flat-structured-output
    rule does not apply — the nested ``normalized`` overlay is intentional.
    """

    valid: bool
    reason: str = ""
    details: list[dict[str, Any]] = Field(default_factory=list)
    evidence_ids: list[int] = Field(default_factory=list)
    normalized: RegroupOverlay | None = None
    validation_digest: str = ""
    failed_step: int = 0


class OverlayValidationContext(BaseModel):
    """The ``base_context`` argument to :func:`validate_overlay`.

    Carries everything the deterministic algorithm needs that is *not* on the
    candidate overlay itself. The validator loads the source DAG through the
    store (step 2), but the rest of the base context — the typed overlay row,
    the active-marker projection, presence flags for boundary checkpoints — is
    supplied here so the function stays a pure deterministic gate over its
    inputs.

    - ``feature_id`` — the feature whose overlay is being validated. Must match
      ``candidate.feature_id``.
    - ``boundary_checkpoint_exists`` — whether ``dag-group:{group_idx_offset}``
      already exists. doc 09 § "Activation And Rollback Constraints": the next
      checkpoint must be *absent* for an overlay that has not started. Step 3
      treats a present boundary checkpoint as a rejection.
    - ``checkpointed_group_exists`` — whether ``dag-group:{checkpointed_group}``
      exists. Step 3 requires it.
    - ``active_marker`` — the :class:`RegroupActiveMarker` projected for this
      overlay, when ``activation_check=True``. Step 12 validates it field by
      field against the typed overlay row. ``None`` is allowed only when
      ``activation_check`` is False.
    - ``overlay_row_id`` — the ``execution_regroup_overlays`` row id, when the
      overlay is already persisted (step 13 records the validation against it).
    - ``latest_successful_validation_digest`` — the overlay row's recorded
      ``validation_digest`` from its latest successful validation, when one
      exists. Step 12 confirms the active marker references it.
    """

    feature_id: str
    boundary_checkpoint_exists: bool = False
    checkpointed_group_exists: bool = True
    active_marker: RegroupActiveMarker | None = None
    overlay_row_id: int | None = None
    latest_successful_validation_digest: str | None = None


# ── Internal: a parse failure short-circuits the algorithm ──────────────────


class _Reject(Exception):
    """An internal control-flow signal that a step rejected the overlay.

    Carries the deterministic rejection code, the 1-based step index, and the
    bounded detail payload. :func:`validate_overlay` catches it and converts it
    to a failed :class:`OverlayValidationResult`. Using an exception keeps each
    step a straight-line function that can ``raise`` on the first violation
    without threading an early-return sentinel through 13 steps.
    """

    def __init__(
        self, reason: str, step: int, details: list[dict[str, Any]] | None = None
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.step = step
        self.details = details or []


# ── Step 1 helpers — parse + normalize ──────────────────────────────────────

_DETAIL_CAP = 25  # bounded detail payload (mirrors implementation.py [:20])


def _coerce_typed_overlay(candidate: Any) -> RegroupOverlay:
    """Step 1 (part A): parse the candidate into a typed :class:`RegroupOverlay`.

    Accepts an already-typed :class:`RegroupOverlay`, a ``dict`` body, a JSON
    string, or a compatibility :class:`DerivedDAGArtifact` (typed or dict/JSON).
    A compatibility ``DerivedDAGArtifact`` is *not* a full overlay — only the
    typed :class:`RegroupOverlay` carries the activation/rollback contracts,
    barriers, and speed index needed by steps 9-12 — so a bare
    ``DerivedDAGArtifact`` is rejected with ``dag_regroup_overlay_not_typed``.
    The validator's input is the typed overlay; the ``DerivedDAGArtifact`` form
    is the compatibility *projection* (doc 09: it is generated *from* the typed
    overlay), and step 1 only consumes it for the
    ``speed_index["overlay"]``-identity cross-check.
    """

    if isinstance(candidate, RegroupOverlay):
        return candidate
    if isinstance(candidate, DerivedDAGArtifact):
        raise _Reject(
            "dag_regroup_overlay_not_typed",
            1,
            [{
                "detail": (
                    "a bare DerivedDAGArtifact is the compatibility projection, "
                    "not the typed overlay; validate_overlay requires the typed "
                    "RegroupOverlay"
                ),
                "artifact_key": candidate.artifact_key,
            }],
        )
    payload: Any = candidate
    if isinstance(candidate, (str, bytes)):
        try:
            return RegroupOverlay.model_validate_json(candidate)
        except Exception as exc:  # noqa: BLE001 - any parse error is a reject
            raise _Reject(
                "dag_regroup_overlay_malformed_json",
                1,
                [{"error": str(exc)}],
            ) from exc
    if isinstance(candidate, dict):
        # A DerivedDAGArtifact-shaped dict has no overlay-only fields; reject it
        # the same as the typed-projection case so callers cannot smuggle a
        # compatibility body in as a dict.
        if "activation_contract" in candidate and isinstance(
            candidate.get("activation_contract"), list
        ):
            raise _Reject(
                "dag_regroup_overlay_not_typed",
                1,
                [{
                    "detail": (
                        "dict body has a list-valued activation_contract — that "
                        "is the DerivedDAGArtifact projection shape, not the "
                        "typed RegroupOverlay"
                    ),
                }],
            )
        payload = candidate
    try:
        return RegroupOverlay.model_validate(payload)
    except Exception as exc:  # noqa: BLE001 - any parse error is a reject
        raise _Reject(
            "dag_regroup_overlay_malformed",
            1,
            [{"error": str(exc)}],
        ) from exc


def _normalize_overlay(overlay: RegroupOverlay) -> RegroupOverlay:
    """Step 1 (part B): produce the canonical normalized overlay.

    Normalization is deterministic and idempotent:

    - ``derived_execution_order`` / ``original_execution_order`` waves keep
      their list order (wave *order* is meaningful) but within-wave task ids are
      sorted, so two equivalent overlays normalize identically.
    - ``original_to_new_group_mapping`` target lists are de-duplicated + sorted.
    - ``write_sets`` path lists are de-duplicated + sorted.
    - ``remaining_dependency_edges`` dependency lists are de-duplicated +
      sorted.
    - ``barriers[*].task_ids`` are sorted.

    Group ids are already absolute integers on the typed overlay (the
    ``int``-keyed ``original_to_new_group_mapping`` and the absolute
    ``group_idx_offset``); "normalize group ids to absolute indexes" (doc 09
    step 1) is therefore a no-op for the typed form and the cross-check in
    :func:`_check_compat_projection_identity` covers the projection form.
    """

    norm_derived = [sorted(wave) for wave in overlay.derived_execution_order]
    norm_original = [sorted(wave) for wave in overlay.original_execution_order]
    norm_mapping = {
        int(orig): sorted({int(g) for g in targets})
        for orig, targets in overlay.original_to_new_group_mapping.items()
    }
    norm_write_sets = {
        str(owner): sorted({str(p) for p in paths if str(p)})
        for owner, paths in overlay.write_sets.items()
    }
    norm_dep_edges = {
        str(task): sorted({str(d) for d in deps})
        for task, deps in overlay.remaining_dependency_edges.items()
    }
    norm_barriers = [
        barrier.model_copy(update={"task_ids": sorted(barrier.task_ids)})
        for barrier in overlay.barriers
    ]
    normalized = overlay.model_copy(
        update={
            "derived_execution_order": norm_derived,
            "original_execution_order": norm_original,
            "original_to_new_group_mapping": norm_mapping,
            "write_sets": norm_write_sets,
            "remaining_dependency_edges": norm_dep_edges,
            "barriers": norm_barriers,
        }
    )
    # Recompute the canonical overlay sha from the normalized typed form (doc 09
    # step 1: "compute the canonical overlay sha from the typed normalized
    # form"). overlay_sha256 / validation_digest are excluded from the hashed
    # body so the sha is a pure function of the overlay's substance.
    canonical_sha = _canonical_overlay_sha(normalized)
    return normalized.model_copy(update={"overlay_sha256": canonical_sha})


def _canonical_overlay_sha(overlay: RegroupOverlay) -> str:
    """Deterministic SHA-256 over the normalized overlay substance.

    Excludes ``overlay_sha256`` itself (it is the output), ``validation_digest``
    (a downstream value), and the mutable timestamp/audit fields
    (``created_at`` / ``activated_at`` / ``rolled_back_at`` / ``reason`` /
    ``validation_evidence_ids`` / ``scheduler_feedback_ids`` / ``status``) so
    the sha is stable across a staged→active status flip and across re-running
    validation at different times.

    It also excludes ``activation_contract.required_overlay_sha256`` — that
    field is *defined* to equal ``overlay_sha256`` (it is a self-reference, and
    step 11 enforces the equality). Hashing it in would make the sha depend on
    a value derived from the sha, so there would be no fixed point. Excluding
    it makes ``overlay_sha256`` a pure function of the overlay's *substance*.

    Hashed via :func:`~iriai_build_v2.execution_control.models.stable_digest`
    (sorted-keys compact JSON).
    """

    body = overlay.model_dump(mode="json")
    for volatile in (
        "overlay_sha256",
        "validation_digest",
        "created_at",
        "activated_at",
        "rolled_back_at",
        "reason",
        "validation_evidence_ids",
        "scheduler_feedback_ids",
        "status",
    ):
        body.pop(volatile, None)
    # Drop the activation contract's self-referential overlay-sha field (see
    # docstring) so the canonical sha has a fixed point.
    contract = body.get("activation_contract")
    if isinstance(contract, dict):
        contract.pop("required_overlay_sha256", None)
    return stable_digest(body)


def _check_compat_projection_identity(
    overlay: RegroupOverlay, compatibility_projection: Any
) -> None:
    """Step 1 (part C): the optional compatibility-projection identity cross-check.

    doc 09 § "Validation Algorithm" step 1: "reject ... a compatibility artifact
    whose ``speed_index["overlay"]`` identity disagrees with the typed
    projection link." When the caller passes a :class:`DerivedDAGArtifact`
    compatibility projection alongside the typed overlay, its
    ``speed_index["overlay"]`` must carry the typed overlay's ``overlay_id`` and
    ``overlay_sha256``. A disagreement is ``dag_regroup_projection_identity_
    mismatch`` — a corrupt / mismatched compatibility artifact.
    """

    if compatibility_projection is None:
        return
    projection = compatibility_projection
    if isinstance(projection, (str, bytes)):
        try:
            projection = DerivedDAGArtifact.model_validate_json(projection)
        except Exception as exc:  # noqa: BLE001
            raise _Reject(
                "dag_regroup_projection_malformed",
                1,
                [{"error": str(exc)}],
            ) from exc
    elif isinstance(projection, dict):
        try:
            projection = DerivedDAGArtifact.model_validate(projection)
        except Exception as exc:  # noqa: BLE001
            raise _Reject(
                "dag_regroup_projection_malformed",
                1,
                [{"error": str(exc)}],
            ) from exc
    if not isinstance(projection, DerivedDAGArtifact):
        raise _Reject(
            "dag_regroup_projection_malformed",
            1,
            [{"detail": "compatibility_projection is not a DerivedDAGArtifact"}],
        )
    overlay_identity = projection.speed_index.get("overlay")
    if not isinstance(overlay_identity, dict):
        raise _Reject(
            "dag_regroup_projection_identity_missing",
            1,
            [{
                "detail": (
                    "compatibility projection speed_index has no 'overlay' "
                    "identity block"
                ),
            }],
        )
    projected_id = str(overlay_identity.get("overlay_id") or "")
    projected_sha = str(overlay_identity.get("overlay_sha256") or "")
    mismatches: list[dict[str, Any]] = []
    if projected_id != overlay.overlay_id:
        mismatches.append({
            "field": "overlay_id",
            "typed": overlay.overlay_id,
            "projection": projected_id,
        })
    if projected_sha != overlay.overlay_sha256:
        mismatches.append({
            "field": "overlay_sha256",
            "typed": overlay.overlay_sha256,
            "projection": projected_sha,
        })
    if mismatches:
        raise _Reject(
            "dag_regroup_projection_identity_mismatch", 1, mismatches
        )
    # The projected artifact_key must also be the overlay's canonical key.
    if projection.artifact_key != overlay.compatibility_keys.canonical_artifact_key:
        raise _Reject(
            "dag_regroup_projection_key_mismatch",
            1,
            [{
                "expected_artifact_key": (
                    overlay.compatibility_keys.canonical_artifact_key
                ),
                "actual_artifact_key": projection.artifact_key,
            }],
        )


def _step1_parse_and_normalize(
    candidate: Any,
    base_context: OverlayValidationContext,
    compatibility_projection: Any,
) -> RegroupOverlay:
    """Step 1: parse, normalize, compute the canonical sha, key/status checks.

    doc 09 § "Validation Algorithm" step 1. Rejections:

    - malformed JSON / wrong schema version / unparseable body —
      ``dag_regroup_overlay_malformed*`` (schema-version drift surfaces as a
      Pydantic ``Literal[1]`` validation error during parse).
    - ``artifact_key`` is not the overlay's own canonical key —
      ``dag_regroup_overlay_artifact_key_mismatch``.
    - the overlay status is not one the validator may consider —
      ``dag_regroup_overlay_status_unvalidatable`` (only ``staged`` / ``active``
      overlays are validatable; ``rolled_back`` / ``superseded`` / ``rejected``
      overlays are terminal).
    - ``feature_id`` disagrees with ``base_context`` —
      ``dag_regroup_overlay_feature_mismatch``.
    - the compatibility-projection identity cross-check (part C).
    """

    overlay = _coerce_typed_overlay(candidate)
    if overlay.feature_id != base_context.feature_id:
        raise _Reject(
            "dag_regroup_overlay_feature_mismatch",
            1,
            [{
                "context_feature_id": base_context.feature_id,
                "overlay_feature_id": overlay.feature_id,
            }],
        )
    if overlay.artifact_key != overlay.compatibility_keys.canonical_artifact_key:
        raise _Reject(
            "dag_regroup_overlay_artifact_key_mismatch",
            1,
            [{
                "artifact_key": overlay.artifact_key,
                "canonical_artifact_key": (
                    overlay.compatibility_keys.canonical_artifact_key
                ),
            }],
        )
    if overlay.status not in ("staged", "active"):
        raise _Reject(
            "dag_regroup_overlay_status_unvalidatable",
            1,
            [{"status": overlay.status}],
        )
    normalized = _normalize_overlay(overlay)
    _check_compat_projection_identity(normalized, compatibility_projection)
    return normalized


# ── Step 2 — load + match the source DAG ────────────────────────────────────


async def _step2_load_base_dag(
    overlay: RegroupOverlay,
    store: "RegroupOverlayStore",
) -> ImplementationDAG:
    """Step 2: load ``source_dag_key`` through the store and exact-match it.

    doc 09 § "Validation Algorithm" step 2. The loaded artifact's row id and
    canonical SHA-256 must *exactly* equal the overlay's ``base_dag_artifact_id``
    and ``base_dag_sha256``. A missing artifact is
    ``dag_regroup_base_dag_missing``; an id mismatch is
    ``dag_regroup_base_dag_artifact_mismatch``; a hash mismatch is
    ``dag_regroup_base_dag_hash_mismatch`` (the doc-09 § "Tests" rejection
    codes). The DAG body must parse to an :class:`ImplementationDAG`.
    """

    loaded = await store.load_dag_artifact(
        overlay.feature_id, overlay.source_dag_key
    )
    if loaded is None:
        raise _Reject(
            "dag_regroup_base_dag_missing",
            2,
            [{
                "feature_id": overlay.feature_id,
                "source_dag_key": overlay.source_dag_key,
            }],
        )
    if loaded.id != overlay.base_dag_artifact_id:
        raise _Reject(
            "dag_regroup_base_dag_artifact_mismatch",
            2,
            [{
                "expected_base_dag_artifact_id": overlay.base_dag_artifact_id,
                "actual_base_dag_artifact_id": loaded.id,
            }],
        )
    if loaded.sha256 != overlay.base_dag_sha256:
        raise _Reject(
            "dag_regroup_base_dag_hash_mismatch",
            2,
            [{
                "expected_base_dag_sha256": overlay.base_dag_sha256,
                "actual_base_dag_sha256": loaded.sha256,
            }],
        )
    try:
        return ImplementationDAG.model_validate_json(loaded.value)
    except Exception as exc:  # noqa: BLE001
        raise _Reject(
            "dag_regroup_base_dag_unparseable",
            2,
            [{"error": str(exc)}],
        ) from exc


# ── Step 3 — offset / checkpoint / suffix ───────────────────────────────────


def _step3_offset_and_suffix(
    overlay: RegroupOverlay,
    base_dag: ImplementationDAG,
    base_context: OverlayValidationContext,
) -> list[list[str]]:
    """Step 3: offset arithmetic, checkpoint presence, and the base suffix.

    doc 09 § "Validation Algorithm" step 3:

    - ``checkpointed_group + 1 == group_idx_offset`` — else
      ``dag_regroup_offset_mismatch``.
    - ``group_idx_offset`` is in ``[0, len(base_dag.execution_order)]`` — else
      ``dag_regroup_offset_out_of_range``.
    - ``dag-group:{checkpointed_group}`` exists — else
      ``dag_regroup_boundary_checkpoint_missing`` (the boundary checkpoint the
      overlay resumes after).
    - ``dag-group:{group_idx_offset}`` does *not* exist — else
      ``dag_regroup_boundary_checkpoint_exists`` (the next group has already
      checkpointed, so this overlay is stale; doc 09 § "Activation And Rollback
      Constraints").
    - ``original_execution_order == base_dag.execution_order[group_idx_offset:]``
      — else ``dag_regroup_original_execution_order_mismatch``.

    Returns the normalized base suffix (the slice, with each wave sorted) so
    later steps compare against one canonical form.
    """

    offset = overlay.group_idx_offset
    if overlay.checkpointed_group + 1 != offset:
        raise _Reject(
            "dag_regroup_offset_mismatch",
            3,
            [{
                "checkpointed_group": overlay.checkpointed_group,
                "group_idx_offset": offset,
            }],
        )
    if offset < 0 or offset > len(base_dag.execution_order):
        raise _Reject(
            "dag_regroup_offset_out_of_range",
            3,
            [{
                "group_idx_offset": offset,
                "base_group_count": len(base_dag.execution_order),
            }],
        )
    if not base_context.checkpointed_group_exists:
        raise _Reject(
            "dag_regroup_boundary_checkpoint_missing",
            3,
            [{"required_checkpoint": f"dag-group:{overlay.checkpointed_group}"}],
        )
    if base_context.boundary_checkpoint_exists:
        raise _Reject(
            "dag_regroup_boundary_checkpoint_exists",
            3,
            [{"forbidden_checkpoint": f"dag-group:{offset}"}],
        )
    base_suffix = [sorted(wave) for wave in base_dag.execution_order[offset:]]
    # overlay.original_execution_order is already normalized (step 1).
    if overlay.original_execution_order != base_suffix:
        raise _Reject(
            "dag_regroup_original_execution_order_mismatch",
            3,
            [{
                "expected_group_count": len(base_suffix),
                "actual_group_count": len(overlay.original_execution_order),
            }],
        )
    return base_suffix


# ── Step 4 — task-id multisets ──────────────────────────────────────────────


def _multiset_duplicates(items: list[str]) -> list[str]:
    """Sorted list of values that appear more than once in ``items``."""

    seen: dict[str, int] = {}
    for item in items:
        seen[item] = seen.get(item, 0) + 1
    return sorted(value for value, count in seen.items() if count > 1)


def _step4_task_multisets(
    overlay: RegroupOverlay,
    base_suffix: list[list[str]],
) -> set[str]:
    """Step 4: build + reconcile the base-suffix / derived task-id multisets.

    doc 09 § "Validation Algorithm" step 4: "Build multisets for base suffix
    task ids, derived task definitions, and derived execution-order ids. Reject
    missing, extra, duplicate, or unknown task ids."

    - ``dag_regroup_duplicate_task_ids`` — a task id appears twice in the
      derived ``original_execution_order`` suffix, in the derived
      ``derived_execution_order`` waves, or in
      ``task_definition_fingerprints`` is not a 1:1 set.
    - ``dag_regroup_task_preservation_mismatch`` — the derived task set is not
      exactly the base-suffix task set (missing / extra ids).

    Returns the canonical base-suffix task-id set, the authority all later
    steps treat as "the remaining tasks".
    """

    base_ids = [tid for wave in base_suffix for tid in wave]
    derived_ids = [tid for wave in overlay.derived_execution_order for tid in wave]
    base_dups = _multiset_duplicates(base_ids)
    derived_dups = _multiset_duplicates(derived_ids)
    if base_dups or derived_dups:
        raise _Reject(
            "dag_regroup_duplicate_task_ids",
            4,
            [{
                "duplicate_in_original_suffix": base_dups[:_DETAIL_CAP],
                "duplicate_in_derived_order": derived_dups[:_DETAIL_CAP],
            }],
        )
    base_set = set(base_ids)
    derived_set = set(derived_ids)
    fingerprint_ids = set(overlay.task_definition_fingerprints)
    missing = sorted(base_set - derived_set)
    extra = sorted(derived_set - base_set)
    if missing or extra:
        raise _Reject(
            "dag_regroup_task_preservation_mismatch",
            4,
            [{
                "missing_task_ids": missing[:_DETAIL_CAP],
                "extra_task_ids": extra[:_DETAIL_CAP],
            }],
        )
    # The fingerprint map must cover exactly the remaining task set — step 5
    # then compares the values.
    fp_missing = sorted(base_set - fingerprint_ids)
    fp_extra = sorted(fingerprint_ids - base_set)
    if fp_missing or fp_extra:
        raise _Reject(
            "dag_regroup_fingerprint_coverage_mismatch",
            4,
            [{
                "missing_fingerprint_task_ids": fp_missing[:_DETAIL_CAP],
                "extra_fingerprint_task_ids": fp_extra[:_DETAIL_CAP],
            }],
        )
    return base_set


# ── Step 5 — task-definition fingerprints ───────────────────────────────────


def _task_definition_fingerprint(task: ImplementationTask) -> str:
    """Deterministic fingerprint over a task definition *excluding* scheduling.

    doc 09 § "Validation Algorithm" step 5: "Do not allow prompt, files, team,
    requirement coverage, dependency list, or task id mutation. The only allowed
    change is group placement." The fingerprint therefore hashes the *whole*
    task model dump minus ``dependencies`` (compared separately, step 6) and
    minus ``team`` (a scheduling field — wave membership, doc-09's "group
    placement"). Everything else — prompt/description, ``files``, ``file_scope``,
    requirement / step / journey ids, acceptance criteria, etc. — is frozen.

    Mirrors ``implementation._regroup_task_definition_for_compare`` (which pops
    ``dependencies``) but additionally drops ``team`` because the typed overlay
    legitimately re-teams tasks when it re-waves them, and hashes the result for
    a compact stable comparison value.
    """

    payload = task.model_dump(mode="json")
    payload.pop("dependencies", None)
    payload.pop("team", None)
    return stable_digest(payload)


def _step5_fingerprints(
    overlay: RegroupOverlay,
    base_dag: ImplementationDAG,
    remaining_ids: set[str],
) -> None:
    """Step 5: the overlay fingerprints must equal the base task definitions.

    doc 09 § "Validation Algorithm" step 5. For every remaining task the
    overlay's recorded ``task_definition_fingerprints[task_id]`` must equal the
    fingerprint freshly computed from the base DAG's task definition. A
    disagreement means the overlay mutated a task definition (not just its
    placement) — ``dag_regroup_task_definition_mismatch``. A base task missing
    from the base DAG is ``dag_regroup_base_task_missing``.
    """

    base_by_id = {task.id: task for task in base_dag.tasks}
    missing_base = sorted(tid for tid in remaining_ids if tid not in base_by_id)
    if missing_base:
        raise _Reject(
            "dag_regroup_base_task_missing",
            5,
            [{"missing_task_ids": missing_base[:_DETAIL_CAP]}],
        )
    mismatches: list[dict[str, Any]] = []
    for task_id in sorted(remaining_ids):
        expected = _task_definition_fingerprint(base_by_id[task_id])
        recorded = overlay.task_definition_fingerprints.get(task_id, "")
        if expected != recorded:
            mismatches.append({
                "task_id": task_id,
                "expected_fingerprint": expected,
                "recorded_fingerprint": recorded,
            })
    if mismatches:
        raise _Reject(
            "dag_regroup_task_definition_mismatch",
            5,
            mismatches[:_DETAIL_CAP],
        )


# ── Step 6 — remaining dependency edges ─────────────────────────────────────


def _step6_dependency_edges(
    overlay: RegroupOverlay,
    base_dag: ImplementationDAG,
    remaining_ids: set[str],
) -> dict[str, set[str]]:
    """Step 6: ``remaining_dependency_edges`` must equal the base suffix exactly.

    doc 09 § "Validation Algorithm" step 6: "Compare ``remaining_dependency_
    edges`` to the base DAG suffix exactly. Edges between remaining tasks must
    be preserved with no additions or drops. Dependencies to already
    checkpointed tasks are treated as satisfied evidence and must not reappear
    as executable tasks."

    For every remaining task the *base-suffix-restricted* dependency set —
    base task ``dependencies`` ∩ ``remaining_ids`` — must equal the overlay's
    recorded ``remaining_dependency_edges`` for that task. An added edge, a
    dropped edge, or an edge naming an already-checkpointed (non-remaining) task
    is ``dag_regroup_dependency_preservation_mismatch``. Returns the canonical
    remaining-edge map (sets) for step 7.
    """

    base_by_id = {task.id: task for task in base_dag.tasks}
    canonical_edges: dict[str, set[str]] = {}
    mismatches: list[dict[str, Any]] = []
    for task_id in sorted(remaining_ids):
        base_task = base_by_id.get(task_id)
        base_remaining_deps = {
            str(d)
            for d in (base_task.dependencies if base_task else [])
            if str(d) in remaining_ids
        }
        canonical_edges[task_id] = base_remaining_deps
        recorded = {str(d) for d in overlay.remaining_dependency_edges.get(task_id, [])}
        # A recorded edge to a non-remaining (checkpointed) task is a hard
        # reject: doc 09 step 6 — checkpointed deps must not reappear.
        checkpointed_refs = sorted(recorded - remaining_ids)
        added = sorted(recorded & remaining_ids - base_remaining_deps)
        dropped = sorted(base_remaining_deps - recorded)
        if checkpointed_refs or added or dropped:
            mismatches.append({
                "task_id": task_id,
                "added_dependencies": added,
                "dropped_dependencies": dropped,
                "checkpointed_task_dependencies": checkpointed_refs,
            })
    # An overlay edge keyed on a task not in the remaining set is also invalid.
    for task_id in overlay.remaining_dependency_edges:
        if task_id not in remaining_ids:
            mismatches.append({
                "task_id": task_id,
                "reason": "dependency_edge_for_non_remaining_task",
            })
    if mismatches:
        raise _Reject(
            "dag_regroup_dependency_preservation_mismatch",
            6,
            mismatches[:_DETAIL_CAP],
        )
    return canonical_edges


# ── Step 7 — derived group placement of dependencies ────────────────────────


def _step7_derived_group_dependencies(
    overlay: RegroupOverlay,
    canonical_edges: dict[str, set[str]],
) -> dict[str, int]:
    """Step 7: build ``derived_group_by_task`` and check dependency placement.

    doc 09 § "Validation Algorithm" step 7: "Build ``derived_group_by_task``.
    Reject unknown dependencies, dependencies in the same derived wave, and
    dependencies scheduled after their dependents."

    The derived group index is *relative* (0-based within
    ``derived_execution_order``); a dependency must sit in a strictly *earlier*
    derived wave than its dependent. Rejections:

    - ``dag_regroup_dependency_unknown`` — a dependency has no derived wave.
    - ``dag_regroup_dependency_same_wave`` — a dependency shares its dependent's
      derived wave.
    - ``dag_regroup_dependency_after_dependent`` — a dependency is scheduled in
      a *later* derived wave than its dependent.

    Returns ``derived_group_by_task`` (relative indexes) for steps 8-11.
    """

    derived_group_by_task: dict[str, int] = {}
    for group_idx, wave in enumerate(overlay.derived_execution_order):
        for task_id in wave:
            derived_group_by_task[task_id] = group_idx
    unknown: list[dict[str, Any]] = []
    same_wave: list[dict[str, Any]] = []
    after: list[dict[str, Any]] = []
    for task_id in sorted(canonical_edges):
        task_group = derived_group_by_task.get(task_id)
        for dependency_id in sorted(canonical_edges[task_id]):
            dep_group = derived_group_by_task.get(dependency_id)
            if dep_group is None:
                unknown.append({
                    "task_id": task_id,
                    "dependency_id": dependency_id,
                })
            elif task_group is not None and dep_group == task_group:
                same_wave.append({
                    "task_id": task_id,
                    "dependency_id": dependency_id,
                    "derived_group": task_group,
                })
            elif task_group is not None and dep_group > task_group:
                after.append({
                    "task_id": task_id,
                    "dependency_id": dependency_id,
                    "task_derived_group": task_group,
                    "dependency_derived_group": dep_group,
                })
    if unknown:
        raise _Reject("dag_regroup_dependency_unknown", 7, unknown[:_DETAIL_CAP])
    if same_wave:
        raise _Reject(
            "dag_regroup_dependency_same_wave", 7, same_wave[:_DETAIL_CAP]
        )
    if after:
        raise _Reject(
            "dag_regroup_dependency_after_dependent", 7, after[:_DETAIL_CAP]
        )
    return derived_group_by_task


# ── Step 8 — original_to_new_group_mapping ──────────────────────────────────


def _step8_group_mapping(
    overlay: RegroupOverlay,
    base_dag: ImplementationDAG,
    derived_group_by_task: dict[str, int],
) -> None:
    """Step 8: validate ``original_to_new_group_mapping``.

    doc 09 § "Validation Algorithm" step 8: "every original suffix group is
    present, every mapped new group is in ``[group_idx_offset, group_idx_offset
    + len(derived_execution_order) - 1]``, and every task in each derived group
    belongs to one of the mapped original groups. A task may not be assigned to
    a new group unless its original group maps to that new group."

    The mapping keys are *absolute* original group indexes; the mapping values
    are *absolute* new (derived) group indexes. Rejections:

    - ``dag_regroup_mapping_group_coverage_mismatch`` — the key set is not
      exactly the original suffix group index set.
    - ``dag_regroup_mapping_target_out_of_range`` — a mapped new group is
      outside the derived-group absolute range, or a target list is empty.
    - ``dag_regroup_mapping_task_membership_mismatch`` — a task's original
      group does not map to the derived (absolute) group the task actually sits
      in, OR an original group's tasks are not all reachable through its mapped
      derived groups.
    """

    offset = overlay.group_idx_offset
    derived_len = len(overlay.derived_execution_order)
    expected_keys = set(range(offset, len(base_dag.execution_order)))
    actual_keys = set(overlay.original_to_new_group_mapping)
    missing_keys = sorted(expected_keys - actual_keys)
    extra_keys = sorted(actual_keys - expected_keys)
    if missing_keys or extra_keys:
        raise _Reject(
            "dag_regroup_mapping_group_coverage_mismatch",
            8,
            [{
                "missing_original_groups": missing_keys[:_DETAIL_CAP],
                "extra_original_groups": extra_keys[:_DETAIL_CAP],
            }],
        )
    derived_min = offset
    derived_max = offset + max(0, derived_len - 1)
    bad_targets: list[dict[str, Any]] = []
    for original_group, new_groups in sorted(
        overlay.original_to_new_group_mapping.items()
    ):
        if not new_groups:
            bad_targets.append({
                "original_group": original_group,
                "reason": "empty_target_list",
            })
            continue
        for new_group in new_groups:
            if new_group < derived_min or new_group > derived_max:
                bad_targets.append({
                    "original_group": original_group,
                    "new_group": new_group,
                    "valid_min": derived_min,
                    "valid_max": derived_max,
                    "reason": "target_out_of_range",
                })
    if bad_targets:
        raise _Reject(
            "dag_regroup_mapping_target_out_of_range",
            8,
            bad_targets[:_DETAIL_CAP],
        )
    # Membership: a task's original (base) group must map to the derived
    # (absolute) group the task actually sits in.
    original_group_by_task = {
        task_id: group_idx
        for group_idx, group in enumerate(base_dag.execution_order)
        for task_id in group
    }
    membership: list[dict[str, Any]] = []
    for task_id in sorted(derived_group_by_task):
        original_group = original_group_by_task.get(task_id)
        if original_group is None:
            # A derived task with no base group is already rejected at step 4 /
            # 5; guard defensively so step 8 cannot KeyError.
            membership.append({
                "task_id": task_id,
                "reason": "task_has_no_original_group",
            })
            continue
        absolute_derived_group = offset + derived_group_by_task[task_id]
        mapped_targets = set(
            overlay.original_to_new_group_mapping.get(original_group, [])
        )
        if absolute_derived_group not in mapped_targets:
            membership.append({
                "task_id": task_id,
                "original_group": original_group,
                "derived_group": absolute_derived_group,
                "mapped_targets": sorted(mapped_targets),
                "reason": "task_derived_group_not_in_original_mapping",
            })
    if membership:
        raise _Reject(
            "dag_regroup_mapping_task_membership_mismatch",
            8,
            membership[:_DETAIL_CAP],
        )


# ── Step 9 — hard barriers ──────────────────────────────────────────────────


def _step9_hard_barriers(
    overlay: RegroupOverlay,
    base_dag: ImplementationDAG,
    remaining_ids: set[str],
) -> dict[str, list[str]]:
    """Step 9: compile hard barriers; reject a derived group mixing them.

    doc 09 § "Validation Algorithm" step 9: "Compile hard barriers from Slice 3
    contracts first, then overlay barriers, then legacy speed metadata. Reject a
    derived group that mixes hard barriers. Soft barrier merges must be explicit
    in the overlay and included in validation notes."

    Slice 3 task contracts are not modelled on the typed overlay, so the typed
    overlay's own ``barriers`` (with their ``hard`` flag) are the primary
    authority; the ``speed_index`` ``barrier`` metadata is the legacy fallback.
    Precedence per task: a hard ``OverlayBarrier`` membership wins over a
    speed-index ``barrier`` label. Only ``hard`` barriers can violate; ``hard=
    False`` (soft) barriers never trigger a rejection. A derived wave that
    contains tasks under two *different* hard barriers is
    ``dag_regroup_barrier_violation``.

    Returns ``{task_id: [hard_barrier_id]}`` diagnostics (for the success
    details). Soft-merge validation notes are not re-derived here — the doc
    requires them to be *present* in the overlay, which they are by
    construction; this step only enforces the hard-mix rejection.
    """

    hard_barrier_by_task: dict[str, str] = {}
    # Primary: typed overlay barriers flagged hard.
    for barrier in overlay.barriers:
        if not barrier.hard:
            continue
        for task_id in barrier.task_ids:
            if task_id in remaining_ids:
                hard_barrier_by_task.setdefault(str(task_id), barrier.barrier_id)
    # Fallback: legacy speed_index barrier metadata for tasks with no typed
    # hard barrier yet. The typed speed_index maps task_id ->
    # OverlayTaskSpeedMetadata whose ``barrier`` is a string label.
    for task_id, metadata in overlay.speed_index.items():
        if task_id in hard_barrier_by_task or task_id not in remaining_ids:
            continue
        barrier_label = (metadata.barrier or "").strip()
        if barrier_label and barrier_label != "unknown":
            hard_barrier_by_task[str(task_id)] = barrier_label
    del base_dag  # Slice 3 contracts are not on the typed overlay (see docstring)
    violations: list[dict[str, Any]] = []
    for group_idx, wave in enumerate(overlay.derived_execution_order):
        wave_barriers = sorted({
            hard_barrier_by_task[task_id]
            for task_id in wave
            if task_id in hard_barrier_by_task
        })
        if len(wave_barriers) > 1:
            violations.append({
                "derived_group": overlay.group_idx_offset + group_idx,
                "barriers": wave_barriers,
                "task_ids": sorted(wave),
            })
    if violations:
        raise _Reject(
            "dag_regroup_barrier_violation", 9, violations[:_DETAIL_CAP]
        )
    return {tid: [bid] for tid, bid in sorted(hard_barrier_by_task.items())}


# ── Step 10 — authoritative write sets ──────────────────────────────────────


def _task_declared_write_paths(task: ImplementationTask) -> set[str]:
    """The write paths a task declares (mirrors implementation helper).

    Mirrors ``implementation._regroup_task_declared_write_paths``: the task's
    ``files`` plus every non-``read_only`` ``file_scope`` path, and for each, a
    ``repo_path``-prefixed variant when the task carries a ``repo_path``. Read
    paths do not contribute (they are not authoritative *writes*).
    """

    paths: set[str] = set()
    repo_path = str(task.repo_path or "").strip().strip("/")

    def _add(raw: str) -> None:
        path = str(raw or "").strip()
        if not path:
            return
        paths.add(path)
        if repo_path and not path.startswith(f"{repo_path}/"):
            paths.add(f"{repo_path}/{path.lstrip('/')}")

    for path in task.files:
        _add(path)
    for scope in task.file_scope:
        action = str(scope.action or "").strip().lower()
        if action and action != "read_only":
            _add(scope.path)
    return paths


def _step10_write_sets(
    overlay: RegroupOverlay,
    base_dag: ImplementationDAG,
    remaining_ids: set[str],
    derived_group_by_task: dict[str, int],
) -> dict[str, set[str]]:
    """Step 10: compile authoritative write sets and reject conflicts.

    doc 09 § "Validation Algorithm" step 10: "Compile authoritative write sets
    from task contracts, task file scopes, declared task files, and overlay
    additions. Overlay additions may add paths but may not remove, rename,
    narrow, or mask authoritative paths. If a derived group merges tasks from
    multiple original groups, every task in that group must have write-set
    coverage. Reject same-wave write-set overlap after path canonicalization and
    reject widened waves containing any ``unknown_write`` task."

    The authoritative write set per task is the base task's declared write paths
    (the contract/scope/files authority) *unioned* with the overlay's
    ``write_sets`` additions. Rejections:

    - ``dag_regroup_write_set_removes_authoritative_path`` — the overlay's
      ``write_sets`` for a task is missing a path the base task declares
      (additions may add, never remove/narrow/mask).
    - ``dag_regroup_write_set_conflict`` — two tasks in the same derived wave
      have overlapping authoritative write sets.
    - ``dag_regroup_missing_write_set_coverage`` — a derived wave merges tasks
      from >1 original group and a task in it has an empty authoritative write
      set.
    - ``dag_regroup_unknown_write_in_widened_wave`` — a derived wave with >1
      task (a "widened" wave) contains an ``unknown_write`` task (per the
      overlay ``speed_index``).

    Returns the per-task authoritative write sets.
    """

    base_by_id = {task.id: task for task in base_dag.tasks}
    base_declared: dict[str, set[str]] = {
        task_id: _task_declared_write_paths(base_by_id[task_id])
        for task_id in remaining_ids
        if task_id in base_by_id
    }
    # Authoritative = base declared ∪ overlay additions. Overlay additions may
    # not REMOVE a base-declared path.
    removed: list[dict[str, Any]] = []
    authoritative: dict[str, set[str]] = {}
    for task_id in sorted(remaining_ids):
        base_paths = base_declared.get(task_id, set())
        overlay_paths = {str(p) for p in overlay.write_sets.get(task_id, [])}
        # If the overlay declares a write set for this task at all, it must be a
        # superset of the base-declared paths (no narrowing/masking).
        if overlay_paths and not base_paths.issubset(overlay_paths):
            removed.append({
                "task_id": task_id,
                "removed_paths": sorted(base_paths - overlay_paths)[:_DETAIL_CAP],
            })
        authoritative[task_id] = base_paths | overlay_paths
    if removed:
        raise _Reject(
            "dag_regroup_write_set_removes_authoritative_path",
            10,
            removed[:_DETAIL_CAP],
        )
    # Same-wave overlap.
    conflicts: list[dict[str, Any]] = []
    for group_idx, wave in enumerate(overlay.derived_execution_order):
        owners = [tid for tid in sorted(wave) if authoritative.get(tid)]
        for i, left in enumerate(owners):
            for right in owners[i + 1:]:
                overlap = sorted(authoritative[left] & authoritative[right])
                if overlap:
                    conflicts.append({
                        "derived_group": overlay.group_idx_offset + group_idx,
                        "left": left,
                        "right": right,
                        "overlap": overlap[:_DETAIL_CAP],
                    })
    if conflicts:
        raise _Reject(
            "dag_regroup_write_set_conflict", 10, conflicts[:_DETAIL_CAP]
        )
    # Merged-original-group coverage + unknown-write-in-widened-wave.
    original_group_by_task = {
        task_id: group_idx
        for group_idx, group in enumerate(base_dag.execution_order)
        for task_id in group
    }
    missing_coverage: list[dict[str, Any]] = []
    unknown_widened: list[dict[str, Any]] = []
    for group_idx, wave in enumerate(overlay.derived_execution_order):
        absolute_group = overlay.group_idx_offset + group_idx
        source_groups = {
            original_group_by_task[tid]
            for tid in wave
            if tid in original_group_by_task
        }
        widened = len(wave) > 1
        if len(source_groups) > 1:
            uncovered = sorted(
                tid for tid in wave if not authoritative.get(tid)
            )
            if uncovered:
                missing_coverage.append({
                    "derived_group": absolute_group,
                    "source_original_groups": sorted(source_groups),
                    "uncovered_task_ids": uncovered[:_DETAIL_CAP],
                })
        if widened:
            unknown_writers = sorted(
                tid
                for tid in wave
                if (overlay.speed_index.get(tid) is not None
                    and overlay.speed_index[tid].unknown_write)
            )
            if unknown_writers:
                unknown_widened.append({
                    "derived_group": absolute_group,
                    "unknown_write_task_ids": unknown_writers[:_DETAIL_CAP],
                })
    if missing_coverage:
        raise _Reject(
            "dag_regroup_missing_write_set_coverage",
            10,
            missing_coverage[:_DETAIL_CAP],
        )
    if unknown_widened:
        raise _Reject(
            "dag_regroup_unknown_write_in_widened_wave",
            10,
            unknown_widened[:_DETAIL_CAP],
        )
    del derived_group_by_task  # absolute group recomputed locally for clarity
    return authoritative


# ── Step 11 — activation + rollback contracts ───────────────────────────────


def _step11_activation_rollback_contracts(
    overlay: RegroupOverlay,
) -> None:
    """Step 11: activation + rollback contracts vs. the normalized first wave.

    doc 09 § "Validation Algorithm" step 11: "Validate activation and rollback
    contracts against the normalized first derived wave: required checkpoint
    key, forbidden checkpoint key, forbidden ``dag-task:*`` keys, forbidden
    group artifact prefixes, forbidden group event metadata, forbidden typed
    attempts, forbidden merge queue items, required base dag id/hash, and
    required overlay sha."

    This step checks the *internal consistency* of the typed
    :class:`RegroupActivationContract` / :class:`RegroupRollbackPlan` against
    the overlay's own offset / checkpoint / base-DAG identity / normalized first
    derived wave — it does not re-query the DB (step 12 / Slice 09c own the live
    artifact-absence checks). Rejections all share
    ``dag_regroup_activation_contract_invalid`` /
    ``dag_regroup_rollback_plan_invalid`` with a per-field detail.
    """

    contract = overlay.activation_contract
    offset = overlay.group_idx_offset
    first_wave = (
        overlay.derived_execution_order[0]
        if overlay.derived_execution_order
        else []
    )
    activation_errors: list[dict[str, Any]] = []
    if contract.required_checkpoint_key != f"dag-group:{overlay.checkpointed_group}":
        activation_errors.append({
            "field": "required_checkpoint_key",
            "expected": f"dag-group:{overlay.checkpointed_group}",
            "actual": contract.required_checkpoint_key,
        })
    if contract.forbidden_checkpoint_key != f"dag-group:{offset}":
        activation_errors.append({
            "field": "forbidden_checkpoint_key",
            "expected": f"dag-group:{offset}",
            "actual": contract.forbidden_checkpoint_key,
        })
    if contract.forbidden_group_event_idx != offset:
        activation_errors.append({
            "field": "forbidden_group_event_idx",
            "expected": offset,
            "actual": contract.forbidden_group_event_idx,
        })
    if contract.required_base_dag_artifact_id != overlay.base_dag_artifact_id:
        activation_errors.append({
            "field": "required_base_dag_artifact_id",
            "expected": overlay.base_dag_artifact_id,
            "actual": contract.required_base_dag_artifact_id,
        })
    if contract.required_base_dag_sha256 != overlay.base_dag_sha256:
        activation_errors.append({
            "field": "required_base_dag_sha256",
            "expected": overlay.base_dag_sha256,
            "actual": contract.required_base_dag_sha256,
        })
    if contract.required_overlay_sha256 != overlay.overlay_sha256:
        activation_errors.append({
            "field": "required_overlay_sha256",
            "expected": overlay.overlay_sha256,
            "actual": contract.required_overlay_sha256,
        })
    # The forbidden first-wave task keys must be exactly the dag-task:* keys for
    # the normalized first derived wave.
    expected_first_wave_keys = sorted(f"dag-task:{tid}" for tid in first_wave)
    actual_first_wave_keys = sorted(contract.forbidden_first_wave_task_keys)
    if expected_first_wave_keys != actual_first_wave_keys:
        activation_errors.append({
            "field": "forbidden_first_wave_task_keys",
            "expected": expected_first_wave_keys[:_DETAIL_CAP],
            "actual": actual_first_wave_keys[:_DETAIL_CAP],
        })
    if activation_errors:
        raise _Reject(
            "dag_regroup_activation_contract_invalid",
            11,
            activation_errors[:_DETAIL_CAP],
        )

    rollback = overlay.rollback_plan
    rollback_errors: list[dict[str, Any]] = []
    if rollback.restore_source_dag_key != overlay.source_dag_key:
        rollback_errors.append({
            "field": "restore_source_dag_key",
            "expected": overlay.source_dag_key,
            "actual": rollback.restore_source_dag_key,
        })
    if rollback.restore_from_checkpoint_group != overlay.checkpointed_group:
        rollback_errors.append({
            "field": "restore_from_checkpoint_group",
            "expected": overlay.checkpointed_group,
            "actual": rollback.restore_from_checkpoint_group,
        })
    if rollback.allowed_until_group_idx != offset:
        rollback_errors.append({
            "field": "allowed_until_group_idx",
            "expected": offset,
            "actual": rollback.allowed_until_group_idx,
        })
    for field_name in (
        "forbidden_started_event_group_idx",
        "forbidden_typed_attempt_group_idx",
        "forbidden_merge_queue_group_idx",
    ):
        value = getattr(rollback, field_name)
        if value != offset:
            rollback_errors.append({
                "field": field_name,
                "expected": offset,
                "actual": value,
            })
    expected_started_keys = sorted(f"dag-task:{tid}" for tid in first_wave)
    actual_started_keys = sorted(rollback.forbidden_started_keys)
    if expected_started_keys != actual_started_keys:
        rollback_errors.append({
            "field": "forbidden_started_keys",
            "expected": expected_started_keys[:_DETAIL_CAP],
            "actual": actual_started_keys[:_DETAIL_CAP],
        })
    if rollback.rollback_marker_key != overlay.compatibility_keys.rollback_artifact_key:
        rollback_errors.append({
            "field": "rollback_marker_key",
            "expected": overlay.compatibility_keys.rollback_artifact_key,
            "actual": rollback.rollback_marker_key,
        })
    if rollback_errors:
        raise _Reject(
            "dag_regroup_rollback_plan_invalid",
            11,
            rollback_errors[:_DETAIL_CAP],
        )


# ── Step 12 — RegroupActiveMarker (activation / resolver checks only) ───────


def _step12_active_marker(
    overlay: RegroupOverlay,
    base_context: OverlayValidationContext,
) -> None:
    """Step 12: validate :class:`RegroupActiveMarker` on activation/resolver runs.

    doc 09 § "Validation Algorithm" step 12: "During activation or resolver
    checks, validate ``RegroupActiveMarker`` against the typed overlay row,
    projection link, canonical artifact body sha, latest successful validation
    digest, base DAG id/hash, and rollback projection. Any missing, stale,
    inactive, or mismatched marker fails closed and quiesces dispatch before the
    affected group."

    This step runs *only* when ``activation_check=True`` (the caller is
    activating or the resolver is selecting an overlay). A non-activation
    validation skips it.

    Fail-closed rejections (``dag_regroup_active_marker_*``):

    - ``..._missing`` — ``activation_check`` is set but ``base_context`` carries
      no marker.
    - ``..._inactive`` — the marker's status is not ``active``.
    - ``..._field_mismatch`` — any of overlay_id / overlay_slug / feature_id /
      base DAG id+hash / checkpointed group / group offset / canonical &
      rollback artifact keys / validation digest disagrees between the marker
      and the typed overlay.
    - ``..._validation_digest_stale`` — the marker's ``validation_digest`` does
      not equal the overlay row's ``latest_successful_validation_digest``.
    """

    marker = base_context.active_marker
    if marker is None:
        raise _Reject(
            "dag_regroup_active_marker_missing",
            12,
            [{
                "detail": (
                    "activation_check=True but base_context.active_marker is "
                    "None — fail closed"
                ),
            }],
        )
    if marker.status != "active":
        raise _Reject(
            "dag_regroup_active_marker_inactive",
            12,
            [{"marker_status": marker.status}],
        )
    mismatches: list[dict[str, Any]] = []

    def _check(field: str, marker_value: Any, overlay_value: Any) -> None:
        if marker_value != overlay_value:
            mismatches.append({
                "field": field,
                "marker": marker_value,
                "overlay": overlay_value,
            })

    _check("overlay_id", marker.overlay_id, overlay.overlay_id)
    _check("overlay_slug", marker.overlay_slug, overlay.overlay_slug)
    _check("feature_id", marker.feature_id, overlay.feature_id)
    _check("source_dag_key", marker.source_dag_key, overlay.source_dag_key)
    _check(
        "base_dag_artifact_id",
        marker.base_dag_artifact_id,
        overlay.base_dag_artifact_id,
    )
    _check("base_dag_sha256", marker.base_dag_sha256, overlay.base_dag_sha256)
    _check(
        "checkpointed_group",
        marker.checkpointed_group,
        overlay.checkpointed_group,
    )
    _check("group_idx_offset", marker.group_idx_offset, overlay.group_idx_offset)
    _check(
        "canonical_artifact_key",
        marker.canonical_artifact_key,
        overlay.compatibility_keys.canonical_artifact_key,
    )
    _check(
        "rollback_artifact_key",
        marker.rollback_artifact_key,
        overlay.compatibility_keys.rollback_artifact_key,
    )
    _check(
        "active_marker_key",
        marker.active_marker_key,
        overlay.compatibility_keys.active_marker_key,
    )
    _check("validation_digest", marker.validation_digest, overlay.validation_digest)
    if base_context.overlay_row_id is not None:
        _check("overlay_row_id", marker.overlay_row_id, base_context.overlay_row_id)
    if mismatches:
        raise _Reject(
            "dag_regroup_active_marker_field_mismatch",
            12,
            mismatches[:_DETAIL_CAP],
        )
    # The marker's validation digest must equal the overlay row's latest
    # successful validation digest, when the caller supplied it.
    expected_digest = base_context.latest_successful_validation_digest
    if expected_digest is not None and marker.validation_digest != expected_digest:
        raise _Reject(
            "dag_regroup_active_marker_validation_digest_stale",
            12,
            [{
                "marker_validation_digest": marker.validation_digest,
                "latest_successful_validation_digest": expected_digest,
            }],
        )


# ── validation digest (deterministic, step 13 idempotency key input) ────────


def _compute_validation_digest(
    overlay: RegroupOverlay,
    *,
    valid: bool,
    reason: str,
    activation_check: bool,
) -> str:
    """The deterministic ``validation_digest`` for step 13's idempotency key.

    **Determinism is load-bearing — a non-deterministic digest is a P1.** The
    digest is :func:`~iriai_build_v2.execution_control.models.stable_digest`
    (sorted-keys compact JSON, sha256) over a fixed tuple:

    - the normalized overlay's canonical ``overlay_sha256`` (itself a pure
      function of the overlay substance — see :func:`_canonical_overlay_sha`),
    - ``overlay_id`` (the overlay identity),
    - the ``valid`` boolean,
    - the rejection ``reason`` (``""`` on success),
    - the ``activation_check`` flag (an activation-mode validation and a
      structural-only validation of the same overlay are distinct evidence).

    No clock, no random, no iteration-order-dependent value enters the digest,
    so two runs of :func:`validate_overlay` over byte-identical inputs always
    produce the same digest — exactly what the
    ``(overlay_id, validation_digest)`` idempotency contract in step 13 needs.
    """

    return stable_digest(
        {
            "overlay_id": overlay.overlay_id,
            "overlay_sha256": overlay.overlay_sha256,
            "valid": valid,
            "reason": reason,
            "activation_check": activation_check,
        }
    )


# ── public entry point ──────────────────────────────────────────────────────


async def validate_overlay(
    candidate: Any,
    base_context: OverlayValidationContext,
    store: "RegroupOverlayStore",
    *,
    activation_check: bool = False,
    compatibility_projection: Any = None,
    persist: bool = True,
) -> OverlayValidationResult:
    """Run the deterministic 13-step regroup-overlay validation.

    doc 09 § "Validation Algorithm". This is the single entry point for Slice
    09's overlay validation — artifact repair/update validation, CLI
    activation, and the resolver safety check (Slice 09c) all funnel through it.

    Parameters
    ----------
    candidate:
        The overlay to validate — a typed :class:`RegroupOverlay`, a ``dict``
        body, or a JSON string. A bare compatibility :class:`DerivedDAGArtifact`
        is rejected (step 1): the typed overlay is the validator's input.
    base_context:
        The :class:`OverlayValidationContext` — feature id, boundary-checkpoint
        presence flags, the active marker (for ``activation_check``), the
        persisted overlay row id, and the latest successful validation digest.
    store:
        The :class:`~iriai_build_v2.execution_control.regroup_overlay_store.RegroupOverlayStore`
        bound to the caller's connection. Used to load ``source_dag_key`` (step
        2) and to persist the validation row + compatibility artifact (step 13).
    activation_check:
        When True, step 12 validates the :class:`RegroupActiveMarker`. A
        structural-only validation leaves it False.
    compatibility_projection:
        An optional compatibility :class:`DerivedDAGArtifact` projection whose
        ``speed_index["overlay"]`` identity step 1 cross-checks against the
        typed overlay.
    persist:
        When True (default) and ``base_context.overlay_row_id`` is set, step 13
        records the typed validation row + ``dag-regroup-validation:*``
        compatibility artifact through :meth:`RegroupOverlayStore.record_
        validation`. Set False for a pure dry-run validation (no DB write).

    Returns
    -------
    OverlayValidationResult
        ``valid`` / ``reason`` / ``details`` / ``evidence_ids`` / ``normalized``
        / ``validation_digest`` / ``failed_step``. On a rejection the result is
        ``valid=False`` with the deterministic ``reason`` of the first failing
        step; the algorithm short-circuits at that step.

    Locking
    -------
    For ``activation_check`` runs the caller must already hold the feature
    advisory lock (``RegroupOverlayStore.acquire_feature_lock`` — Slice 09b);
    this function does not acquire it (the lock spans the wider activation /
    rollback flow, doc 09 § "Activation And Rollback Constraints"). It asserts
    nothing about the lock — the contract is the caller's, matching how the
    Slice 08 merge-queue worker holds the lock around the store calls.

    Determinism / idempotency
    --------------------------
    Every step uses sorted keys and fixed iteration order; no clock or random
    source is consulted. ``validation_digest`` (see
    :func:`_compute_validation_digest`) is reproducible across runs over
    identical inputs, so step 13's ``(overlay_id, validation_digest)``
    idempotency holds: a re-run with the same inputs reuses the existing row;
    a *different* digest for the same overlay id is rejected fail-closed by
    :meth:`RegroupOverlayStore.record_validation` (raising
    :class:`~iriai_build_v2.execution_control.regroup_overlay_store.RegroupOverlayValidationConflict`).
    """

    normalized: RegroupOverlay | None = None
    evidence_ids: list[int] = []
    try:
        # Step 1 — parse + normalize + key/status/projection-identity checks.
        normalized = _step1_parse_and_normalize(
            candidate, base_context, compatibility_projection
        )
        # Step 2 — load + exact-match the source DAG.
        base_dag = await _step2_load_base_dag(normalized, store)
        # Step 3 — offset arithmetic, checkpoint presence, base suffix.
        base_suffix = _step3_offset_and_suffix(
            normalized, base_dag, base_context
        )
        # Step 4 — task-id multisets.
        remaining_ids = _step4_task_multisets(normalized, base_suffix)
        # Step 5 — task-definition fingerprints.
        _step5_fingerprints(normalized, base_dag, remaining_ids)
        # Step 6 — remaining dependency edges.
        canonical_edges = _step6_dependency_edges(
            normalized, base_dag, remaining_ids
        )
        # Step 7 — derived group placement of dependencies.
        derived_group_by_task = _step7_derived_group_dependencies(
            normalized, canonical_edges
        )
        # Step 8 — original_to_new_group_mapping.
        _step8_group_mapping(normalized, base_dag, derived_group_by_task)
        # Step 9 — hard barriers.
        _step9_hard_barriers(normalized, base_dag, remaining_ids)
        # Step 10 — authoritative write sets.
        _step10_write_sets(
            normalized, base_dag, remaining_ids, derived_group_by_task
        )
        # Step 11 — activation + rollback contracts.
        _step11_activation_rollback_contracts(normalized)
        # Step 12 — RegroupActiveMarker (activation / resolver checks only).
        if activation_check:
            _step12_active_marker(normalized, base_context)
    except _Reject as rejection:
        digest = (
            _compute_validation_digest(
                normalized,
                valid=False,
                reason=rejection.reason,
                activation_check=activation_check,
            )
            if normalized is not None
            else stable_digest(
                {
                    "valid": False,
                    "reason": rejection.reason,
                    "activation_check": activation_check,
                    "parse_failed": True,
                }
            )
        )
        result = OverlayValidationResult(
            valid=False,
            reason=rejection.reason,
            details=rejection.details,
            evidence_ids=[],
            normalized=normalized,
            validation_digest=digest,
            failed_step=rejection.step,
        )
        await _maybe_persist(result, normalized, base_context, store, persist)
        return result

    # All applicable steps passed.
    assert normalized is not None  # all 13 steps ran => step 1 produced it
    digest = _compute_validation_digest(
        normalized, valid=True, reason="", activation_check=activation_check
    )
    result = OverlayValidationResult(
        valid=True,
        reason="",
        details=[{
            "task_count": sum(
                len(w) for w in normalized.derived_execution_order
            ),
            "derived_group_count": len(normalized.derived_execution_order),
            "group_idx_offset": normalized.group_idx_offset,
            "checkpointed_group": normalized.checkpointed_group,
            "activation_check": activation_check,
        }],
        evidence_ids=evidence_ids,
        normalized=normalized,
        validation_digest=digest,
        failed_step=0,
    )
    await _maybe_persist(result, normalized, base_context, store, persist)
    return result


async def _maybe_persist(
    result: OverlayValidationResult,
    normalized: RegroupOverlay | None,
    base_context: OverlayValidationContext,
    store: "RegroupOverlayStore",
    persist: bool,
) -> None:
    """Step 13: record the typed validation row + compatibility artifact.

    doc 09 § "Validation Algorithm" step 13: "Emit a typed validation record and
    compatibility validation artifact in the same transaction. Re-validating the
    same overlay id with the same digest is idempotent; a different digest for
    the same overlay id is rejected."

    The whole transaction (typed ``execution_regroup_validations`` row + the
    ``dag-regroup-validation:*`` ``artifacts`` row) is owned by
    :meth:`RegroupOverlayStore.record_validation` (Slice 09b) — this function
    only calls it. The ``(overlay_id, validation_digest)`` idempotency and the
    fail-closed :class:`~iriai_build_v2.execution_control.regroup_overlay_store.RegroupOverlayValidationConflict`
    (a different digest for the same overlay id) are enforced inside
    ``record_validation``; ``validate_overlay`` deliberately does *not* swallow
    that conflict — a conflicting digest is a real evidence inconsistency the
    caller must see.

    Persistence is skipped when ``persist=False`` (a dry run) or when
    ``base_context.overlay_row_id`` is None (the overlay is not yet a row, so
    there is nothing to record the FK-bearing validation against). After a
    successful record the validation row id is appended to
    ``result.evidence_ids``.
    """

    if not persist or normalized is None:
        return
    if base_context.overlay_row_id is None:
        return
    record: "OverlayValidationRecord" = await store.record_validation(
        feature_id=normalized.feature_id,
        overlay_id=normalized.overlay_id,
        overlay_row_id=base_context.overlay_row_id,
        valid=result.valid,
        validation_digest=result.validation_digest,
        reason=result.reason,
        details={"steps": result.details, "failed_step": result.failed_step},
        evidence_ids=result.evidence_ids,
        compatibility_artifact=normalized if result.valid else None,
    )
    if record.id not in result.evidence_ids:
        result.evidence_ids = sorted({*result.evidence_ids, record.id})
