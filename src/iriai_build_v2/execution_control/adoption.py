"""Slice 12d -- in-flight adoption workflow command + marker + resume guard.

This module owns the doc-12 *release-control* adoption surface: the
operator-facing command that adopts a legacy in-flight feature into the typed
control plane, the adoption marker key + body shape, and the resume guard
that refuses to enter the typed control-plane resume path unless the marker
is present.

doc 12 Section "In-Flight Cutover Policy" lines 60-78:

    The runner must never infer adoption from the presence of typed tables,
    configuration flags, or a restarted bridge. Adoption is explicit and
    durable:

    1. The legacy feature reaches a safe boundary.
    2. The adoption command reconstructs completed groups as sealed typed
       evidence, imports the active regroup overlay if one exists, compiles
       contracts and workspace snapshots for remaining work, initializes an
       empty merge queue, and validates projection parity.
    3. The store writes an adoption marker, for example
       ``execution-control-adoption:{feature_id}``, with the candidate commit,
       deploy artifact id, feature id, ...
    4. Resume sees the adoption marker, verifies it against current Postgres
       and workspace state, and only then enters the control-plane resume
       path.

    If any adoption check fails, the feature remains on the legacy executor
    or quiesced before the next group.

**Module scope.** This module lands the **mechanism**: the typed adoption
command + the marker writer + the resume guard skeleton. The full per-
feature evidence reconstruction (regroup overlay import, merge queue
initialization, projection parity validation) is the **Slice 12e PR 11.13**
final-landing wiring scope -- Slice 12d focuses on the typed contract +
marker + resume guard so the slice-end review can pin the API surface.

**Fail-closed defaults (the prompt hard rule).**

* :func:`adopt_in_flight_feature` refuses unless the supplied
  :class:`AtomicLandingGateResult` is ``passed=True`` AND
  ``operational_decision="go"`` -- a no-go landing record cannot be the
  basis for in-flight adoption.
* :func:`adopt_in_flight_feature` refuses on any mismatch between the
  caller-supplied ``candidate_commit`` / ``deploy_artifact_id`` and the
  landing record's values -- the adoption MUST be against the exact
  go-approved candidate.
* :func:`adopt_in_flight_feature` is idempotent: a second call with the
  same ``(feature_id, candidate_commit, deploy_artifact_id)`` triple re-
  finds the existing adoption marker via :func:`read_adoption_marker` and
  returns the persisted record WITHOUT double-writing.
* :func:`assert_feature_adopted_or_legacy` is pass-through when the
  ``IRIAI_EXEC_CONTROL_PLANE_ENABLED`` env flag is :attr:`EnvFlagState.
  UNSET` or :attr:`EnvFlagState.DISABLED` (legacy mode; NO automatic
  migration per doc 12 line 76-77). When the flag is :attr:`EnvFlagState.
  ENABLED` and the marker is absent, the guard raises
  :class:`ControlPlaneAdoptionError` (NOT a silent migration).

**No back-imports.** This module MUST NOT import from
``workflows.develop.phases.implementation`` (the compatibility arrow points
IN, never OUT). Imports are limited to:

* stdlib + Pydantic
* sibling :mod:`iriai_build_v2.execution_control.atomic_landing` (the typed
  contract)
* sibling :mod:`iriai_build_v2.execution_control.startup` (the Slice-12c
  env-flag helper)
* :mod:`iriai_compose` (the ``Feature`` type)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol

from pydantic import ValidationError

from .atomic_landing import (
    AtomicLandingGateResult,
    InFlightAdoptionRecord,
    RollbackDisposition,
)
from .startup import (
    EnvFlagState,
    read_control_plane_env_flag,
)


__all__ = [
    # Exceptions
    "ControlPlaneAdoptionError",
    "AdoptionMarkerCorruptError",
    # Marker key + body helpers
    "ADOPTION_MARKER_KEY_PREFIX",
    "adoption_marker_artifact_key",
    "is_adoption_marker_key",
    # Protocols
    "AdoptionArtifactStore",
    # Commands + guards
    "adopt_in_flight_feature",
    "read_adoption_marker",
    "assert_feature_adopted_or_legacy",
]


logger = logging.getLogger(__name__)


# --- Exceptions -----------------------------------------------------------


class ControlPlaneAdoptionError(RuntimeError):
    """Raised when in-flight adoption fails closed.

    Doc 12 Section "In-Flight Cutover Policy" line 76-78: "If any adoption
    check fails, the feature remains on the legacy executor or quiesced
    before the next group. Failure to adopt is not a product failure and
    must not route to RCA or broad repair."

    This is the typed signal for "the operator's adoption attempt cannot
    proceed under the typed control plane" -- callers MUST NOT silently
    swallow it or fall back to legacy without recording the cause; the
    legacy fallback is a SEPARATE explicit code path (see
    :func:`assert_feature_adopted_or_legacy` for the env-flag short-circuit
    semantics).
    """


class AdoptionMarkerCorruptError(RuntimeError):
    """Raised when an adoption marker artifact body cannot be parsed.

    Distinct from :class:`ControlPlaneAdoptionError`: a corrupt marker is a
    DATA-integrity blocker (the store has bytes under the marker key that do
    not deserialize into an :class:`InFlightAdoptionRecord`). Callers must
    surface this loudly -- silently ignoring a corrupt marker would risk
    treating a partially-adopted feature as never-adopted.
    """


# --- Adoption marker key/body shape ---------------------------------------


ADOPTION_MARKER_KEY_PREFIX: str = "execution-control-adoption:"
"""Doc 12 Section "In-Flight Cutover Policy" line 69: the adoption marker key
pattern is ``execution-control-adoption:{feature_id}``.

The prefix is exposed as a module-level constant so test code + the resume
guard read the SAME pattern as the writer (no string-literal duplication).
The legacy ``_execution_control_marker_payload`` reader at
``workflows/develop/phases/implementation.py:2472`` already consumes markers
under this exact key; Slice 12d is the canonical WRITER that ensures the
marker body has the typed :class:`InFlightAdoptionRecord` shape."""


def adoption_marker_artifact_key(feature_id: str) -> str:
    """Return the doc-12 adoption marker artifact key for ``feature_id``.

    Per doc 12 line 69: ``execution-control-adoption:{feature_id}``. The
    ``feature_id`` is the iriai-compose ``Feature.id`` short identifier
    (typically 8 hex chars).

    Fail-closed: an empty/blank ``feature_id`` is rejected -- a marker key
    without a feature id cannot be used as an idempotency key.
    """

    if not isinstance(feature_id, str) or not feature_id.strip():
        raise ValueError(
            "adoption_marker_artifact_key requires a non-empty feature_id "
            f"string, got {feature_id!r}"
        )
    return f"{ADOPTION_MARKER_KEY_PREFIX}{feature_id}"


def is_adoption_marker_key(key: str) -> bool:
    """Return ``True`` when ``key`` matches the
    :data:`ADOPTION_MARKER_KEY_PREFIX` pattern.

    Used by the resume guard to detect adoption markers without parsing the
    full ``feature_id`` segment (e.g. when iterating over a feature's full
    artifact set).
    """

    return isinstance(key, str) and key.startswith(ADOPTION_MARKER_KEY_PREFIX)


# --- Artifact store protocol ----------------------------------------------


class AdoptionArtifactStore(Protocol):
    """The minimal artifact-store surface the adoption command + resume
    guard depend on.

    Matches the iriai-compose :class:`~iriai_compose.ArtifactStore` shape
    (the existing :class:`~iriai_build_v2.storage.artifacts.
    PostgresArtifactStore` implements these methods). Declared as a
    ``Protocol`` so test fakes don't need to inherit from the production
    ABC -- duck-typing on the two methods is enough.
    """

    async def get(self, key: str, *, feature: Any) -> Any | None:  # pragma: no cover - protocol
        ...

    async def put(self, key: str, value: Any, *, feature: Any) -> None:  # pragma: no cover - protocol
        ...


# --- The adoption command -------------------------------------------------


async def adopt_in_flight_feature(
    *,
    feature: Any,
    landing_gate_result: AtomicLandingGateResult,
    candidate_commit: str,
    deploy_artifact_id: str,
    artifact_store: AdoptionArtifactStore,
    legacy_root_dag_artifact_id: int,
    legacy_root_dag_sha256: str,
    completed_checkpoint_range: tuple[int, int],
    next_effective_group_idx: int,
    projection_digest: str,
    active_regroup_artifact_ids: list[int] | None = None,
    workspace_snapshot_ids: list[int] | None = None,
    rollback_disposition: RollbackDisposition = "legacy_resume_before_next_group",
    feature_state_at_adoption: str = "",
    adopted_by: str = "",
    landing_gate_result_id: str = "",
    pre_adoption_baseline: Mapping[str, Any] | None = None,
    notes: str = "",
    now: datetime | None = None,
) -> InFlightAdoptionRecord:
    """Adopt a legacy in-flight feature into the typed control plane.

    Per doc 12 Section "In-Flight Cutover Policy" + Section "Refactoring
    Steps" step 7: "Add the in-flight adoption command and resume guard.
    The command is allowed only after the complete-bundle go decision and
    only at checkpoint/quiesce boundaries."

    **Fail-closed paths (the 3 brief-mandated checks):**

    1. ``landing_gate_result.passed`` must be ``True`` AND
       ``operational_decision`` must be ``"go"``. A no-go landing record
       cannot be the basis for in-flight adoption.
    2. ``candidate_commit`` and ``deploy_artifact_id`` must match the
       landing record verbatim. A mismatch signals the operator tried to
       adopt against a different release than the one approved.
    3. **Idempotency.** A second call with the same
       ``(feature_id, candidate_commit, deploy_artifact_id)`` triple
       re-reads the existing marker and returns the persisted record. The
       marker is written EXACTLY ONCE per adoption.

    The constructed :class:`InFlightAdoptionRecord` is JSON-serialized via
    ``model_dump_json()`` and written under
    :func:`adoption_marker_artifact_key`. The artifact store assigns the
    Postgres row id; this command does NOT round-trip to fetch it (the row
    id is the store's responsibility and is not required for resume
    verification). The returned record has ``adoption_marker_artifact_id``
    set to ``None`` unless the caller pre-populates it (the
    :func:`read_adoption_marker` helper preserves whatever was written).

    Per :data:`_log_adoption_attempt`, every adoption attempt is logged at
    INFO level with the typed reason -- the operator audit trail is durable
    via the store; the logger is for live monitoring.

    Raises:
        ControlPlaneAdoptionError: when any of the 3 fail-closed checks
            above triggers. The error message names the failing check
            and the candidate values for operator audit.
    """

    when = (now if now is not None else datetime.now(timezone.utc))

    feature_id = _resolve_feature_id(feature)

    # Fail-closed path 1: landing gate result must be a "go".
    if not landing_gate_result.passed or landing_gate_result.operational_decision != "go":
        raise ControlPlaneAdoptionError(
            f"adopt_in_flight_feature refused for feature_id={feature_id!r}: "
            "landing-gate result is not 'go' "
            f"(passed={landing_gate_result.passed!r}, "
            f"operational_decision="
            f"{landing_gate_result.operational_decision!r}, "
            f"blockers={landing_gate_result.blockers!r}). Per doc 12 § 'In-"
            "Flight Cutover Policy': in-flight adoption is allowed only "
            "after the complete-bundle go decision."
        )

    # Fail-closed path 2: candidate_commit + deploy_artifact_id must match
    # the landing record verbatim. Operator cannot adopt against a different
    # release than the one approved.
    landing_candidate = landing_gate_result.candidate_commit
    landing_artifact = landing_gate_result.deploy_artifact_id
    if candidate_commit.strip() != landing_candidate.strip():
        raise ControlPlaneAdoptionError(
            f"adopt_in_flight_feature refused for feature_id={feature_id!r}: "
            f"candidate_commit={candidate_commit!r} does not match "
            f"landing-gate candidate_commit={landing_candidate!r}. Per doc "
            "12 § 'Operational Go/No-Go': adoption must be against the "
            "exact go-approved candidate."
        )
    if deploy_artifact_id.strip() != landing_artifact.strip():
        raise ControlPlaneAdoptionError(
            f"adopt_in_flight_feature refused for feature_id={feature_id!r}: "
            f"deploy_artifact_id={deploy_artifact_id!r} does not match "
            f"landing-gate deploy_artifact_id={landing_artifact!r}. Per doc "
            "12 § 'Operational Go/No-Go': adoption must be against the "
            "exact go-approved deploy artifact."
        )

    # Fail-closed path 3: idempotency. If a marker already exists for this
    # feature_id, validate it matches the triple and return it WITHOUT
    # writing a new marker (the marker is written EXACTLY ONCE per
    # adoption).
    existing = await read_adoption_marker(
        feature=feature, artifact_store=artifact_store
    )
    if existing is not None:
        if (
            existing.candidate_commit.strip() == candidate_commit.strip()
            and existing.deploy_artifact_id.strip() == deploy_artifact_id.strip()
        ):
            logger.info(
                "adopt_in_flight_feature is idempotent for feature_id=%s: "
                "marker already present (adopted_at=%s); returning existing "
                "record without re-writing",
                feature_id,
                existing.adopted_at.isoformat(),
            )
            return existing
        # Triple mismatch -- the existing marker was for a DIFFERENT
        # adoption attempt. Refuse: per doc 12 the marker is single-shot
        # per feature_id; re-adopting against a different candidate would
        # silently overwrite an already-adopted state.
        raise ControlPlaneAdoptionError(
            f"adopt_in_flight_feature refused for feature_id={feature_id!r}: "
            "an existing adoption marker is present for this feature with "
            f"candidate_commit={existing.candidate_commit!r} / "
            f"deploy_artifact_id={existing.deploy_artifact_id!r}, but the "
            f"caller supplied candidate_commit={candidate_commit!r} / "
            f"deploy_artifact_id={deploy_artifact_id!r}. Per doc 12 § 'In-"
            "Flight Cutover Policy': the adoption marker is single-shot per "
            "feature; re-adoption against a different candidate is not "
            "permitted (would silently overwrite the prior adoption)."
        )

    # Construct + persist the record.
    record = InFlightAdoptionRecord(
        feature_id=feature_id,
        candidate_commit=candidate_commit,
        deploy_artifact_id=deploy_artifact_id,
        legacy_root_dag_artifact_id=legacy_root_dag_artifact_id,
        legacy_root_dag_sha256=legacy_root_dag_sha256,
        completed_checkpoint_range=completed_checkpoint_range,
        next_effective_group_idx=next_effective_group_idx,
        active_regroup_artifact_ids=list(active_regroup_artifact_ids or []),
        workspace_snapshot_ids=list(workspace_snapshot_ids or []),
        projection_digest=projection_digest,
        adoption_marker_artifact_id=None,
        adopted_at=when,
        rollback_disposition=rollback_disposition,
        blockers=[],
        feature_state_at_adoption=feature_state_at_adoption,
        adopted_by=adopted_by,
        landing_gate_result_id=landing_gate_result_id,
        pre_adoption_baseline=dict(pre_adoption_baseline or {}),
        notes=notes,
    )

    marker_key = adoption_marker_artifact_key(feature_id)
    payload = record.model_dump_json()
    await artifact_store.put(marker_key, payload, feature=feature)
    logger.info(
        "adopt_in_flight_feature WROTE adoption marker for feature_id=%s "
        "(candidate_commit=%s, deploy_artifact_id=%s, adopted_by=%s, "
        "rollback_disposition=%s)",
        feature_id,
        candidate_commit,
        deploy_artifact_id,
        adopted_by or "<unspecified>",
        rollback_disposition,
    )
    return record


# --- Marker reader --------------------------------------------------------


async def read_adoption_marker(
    *,
    feature: Any,
    artifact_store: AdoptionArtifactStore,
) -> InFlightAdoptionRecord | None:
    """Read the adoption marker for ``feature`` and return the typed record.

    Returns ``None`` when the marker is absent (the typical "this feature
    has not been adopted yet" path). Returns the parsed
    :class:`InFlightAdoptionRecord` when present.

    Raises:
        AdoptionMarkerCorruptError: when the artifact body exists but
            cannot be parsed into an :class:`InFlightAdoptionRecord`. A
            corrupt marker is a DATA-integrity blocker -- callers must
            NOT silently treat it as "not adopted yet" (that would risk
            adopting a feature twice).
    """

    feature_id = _resolve_feature_id(feature)
    marker_key = adoption_marker_artifact_key(feature_id)
    try:
        raw = await artifact_store.get(marker_key, feature=feature)
    except Exception as exc:  # noqa: BLE001
        raise AdoptionMarkerCorruptError(
            f"adoption marker read failed for feature_id={feature_id!r} "
            f"under key {marker_key!r}: {exc!r}"
        ) from exc
    if raw is None or raw == "":
        return None

    body = _coerce_marker_body_to_text(raw)
    try:
        return InFlightAdoptionRecord.model_validate_json(body)
    except ValidationError as exc:
        raise AdoptionMarkerCorruptError(
            f"adoption marker for feature_id={feature_id!r} under key "
            f"{marker_key!r} has a body that does not validate against "
            f"InFlightAdoptionRecord: {exc!s}"
        ) from exc


def _coerce_marker_body_to_text(raw: Any) -> str:
    """Best-effort coercion of an artifact body to a JSON-string suitable
    for :meth:`InFlightAdoptionRecord.model_validate_json`.

    The :class:`~iriai_build_v2.storage.artifacts.PostgresArtifactStore.get`
    return shape varies (the store decodes JSON for some keys but stores
    raw strings for others). We accept:

    * ``str`` -- assumed to be the JSON-serialized record.
    * ``bytes`` -- decoded with ``surrogateescape`` to match the store's
      write path.
    * ``dict`` or ``list`` -- already parsed; re-serialize via
      ``json.dumps`` (the round-trip is cheap).

    Anything else is treated as corrupt and an
    :class:`AdoptionMarkerCorruptError` is raised by the caller.
    """

    if isinstance(raw, str):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw).decode("utf-8", "surrogateescape")
    if isinstance(raw, (dict, list)):
        return json.dumps(raw)
    raise AdoptionMarkerCorruptError(
        "adoption marker body has unexpected type "
        f"{type(raw).__name__!r}; expected str / bytes / dict"
    )


# --- The resume guard -----------------------------------------------------


async def assert_feature_adopted_or_legacy(
    *,
    feature: Any,
    artifact_store: AdoptionArtifactStore,
    env: Mapping[str, str] | None = None,
) -> InFlightAdoptionRecord | None:
    """Resume guard for the in-flight adoption boundary.

    Per doc 12 Section "In-Flight Cutover Policy" + Section "Refactoring
    Steps" step 7-8:

    * When ``IRIAI_EXEC_CONTROL_PLANE_ENABLED`` is :attr:`EnvFlagState.
      DISABLED` or :attr:`EnvFlagState.UNSET`: **pass through** (legacy
      mode; NO automatic migration per doc 12 line 76-77). The guard
      returns ``None`` -- the caller stays on the legacy executor.
    * When the flag is :attr:`EnvFlagState.ENABLED`: check the adoption
      marker. If absent, raise :class:`ControlPlaneAdoptionError` (NOT a
      silent migration). If present, return the parsed
      :class:`InFlightAdoptionRecord` so the caller can verify it
      against current Postgres + workspace state.

    The guard's return value carries the typed adoption record for the
    caller to consume (per doc 12 line 73-74: "Resume sees the adoption
    marker, verifies it against current Postgres and workspace state,
    and only then enters the control-plane resume path"). The verifies-
    against-current-state step is the caller's responsibility -- the
    guard only proves the marker exists + parses.

    Args:
        feature: the iriai-compose ``Feature`` object being resumed.
        artifact_store: the store the marker lives in.
        env: optional env mapping (default: ``os.environ``).

    Returns:
        ``None`` when the env flag is unset/disabled (legacy mode); the
        :class:`InFlightAdoptionRecord` when the flag is enabled and the
        marker is present.

    Raises:
        ControlPlaneAdoptionError: when the env flag is enabled but the
            adoption marker is absent.
        AdoptionMarkerCorruptError: when the marker exists but cannot be
            parsed (propagated from :func:`read_adoption_marker`).
    """

    flag_state = read_control_plane_env_flag(env=env)
    feature_id = _resolve_feature_id(feature)

    if not flag_state.is_enabled:
        # Legacy mode (UNSET or DISABLED). NO automatic migration per doc
        # 12 line 76-77. The caller stays on the legacy executor.
        logger.debug(
            "assert_feature_adopted_or_legacy passing through for "
            "feature_id=%s (env flag is %s; legacy mode)",
            feature_id,
            flag_state.value,
        )
        return None

    # Env flag is ENABLED -- check the adoption marker.
    record = await read_adoption_marker(
        feature=feature, artifact_store=artifact_store
    )
    if record is None:
        raise ControlPlaneAdoptionError(
            f"assert_feature_adopted_or_legacy refused for feature_id="
            f"{feature_id!r}: IRIAI_EXEC_CONTROL_PLANE_ENABLED is "
            f"{flag_state.value!r} but no adoption marker exists at "
            f"{adoption_marker_artifact_key(feature_id)!r}. Per doc 12 § "
            "'In-Flight Cutover Policy': existing in-flight legacy "
            "features are not migrated automatically -- run the adoption "
            "command (adopt_in_flight_feature) at a safe boundary before "
            "resuming under the typed control plane, or quiesce the "
            "feature on the legacy executor."
        )
    logger.info(
        "assert_feature_adopted_or_legacy PASS for feature_id=%s "
        "(adopted_at=%s; candidate_commit=%s; rollback_disposition=%s)",
        feature_id,
        record.adopted_at.isoformat(),
        record.candidate_commit,
        record.rollback_disposition,
    )
    return record


# --- Helpers --------------------------------------------------------------


def _resolve_feature_id(feature: Any) -> str:
    """Return the iriai-compose ``Feature.id`` for ``feature``.

    Accepts either a ``Feature`` object (the typical case) or a bare
    ``str`` ``feature_id`` (for callers that don't have a full ``Feature``
    in hand; the resume guard at restart time can have either). Fail-
    closed on missing/blank id.
    """

    if isinstance(feature, str):
        fid = feature.strip()
        if not fid:
            raise ValueError(
                "_resolve_feature_id received a blank string; a non-empty "
                "feature_id is required"
            )
        return fid
    fid = getattr(feature, "id", None)
    if not isinstance(fid, str) or not fid.strip():
        raise ValueError(
            "_resolve_feature_id requires a Feature with a non-empty "
            f"`id` attribute (or a bare feature_id string); got "
            f"{feature!r}"
        )
    return fid.strip()
