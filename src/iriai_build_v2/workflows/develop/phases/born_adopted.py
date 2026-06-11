"""Item-1 (P0-1): flag-gated "born-adopted" execution-control resume record.

Problem (develop-phase audit P0-1): the strict execution-control resume gate
in :mod:`.implementation` demands a valid ``execution-control-adoption:
{feature_id}`` marker whenever ANY ``dag-group:{idx}`` checkpoint exists, but
the only production writer of that marker is the operator-invoked legacy
in-flight adoption ceremony (``adopt_in_flight_feature``). A feature born
under the control plane therefore BLOCKS on its first post-seal resume.

Fix shape (research-corrected — the audit's ``completed_checkpoint_range=
(0, -1)`` proposal is unconstructible because
``InFlightAdoptionRecord._completed_range_well_formed`` rejects ``end < 0``):
a flag-gated UPSERT AT EACH GROUP-CHECKPOINT SEAL with
``completed_checkpoint_range=(0, sealed_idx)`` and
``next_effective_group_idx=sealed_idx + 1``. That shape passes the
``InFlightAdoptionRecord`` model validators AND
``_validate_adoption_resume_boundary`` (start == 0; next == end + 1;
next <= group_count) with NO model changes — atomic_landing.py is untouched.

Everything here is gated on ``IRIAI_BORN_ADOPTED_RESUME`` (default OFF).
Flag OFF preserves today's behavior exactly: no marker writes, no
pre-boundary context loads.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

try:
    from ....execution_control.atomic_landing import InFlightAdoptionRecord
except Exception:  # pragma: no cover - mirrors implementation.py's guard
    InFlightAdoptionRecord = None  # type: ignore[assignment]

BORN_ADOPTED_RESUME_ENV = "IRIAI_BORN_ADOPTED_RESUME"

BORN_ADOPTED_ADOPTED_BY = "born-adopted-seal-writer"


def born_adopted_resume_enabled() -> bool:
    """Item-1 flag (IRIAI_BORN_ADOPTED_RESUME, default OFF).

    OFF preserves today's behavior exactly: no born-adopted marker writes at
    group-checkpoint seal, no pre-boundary context load on adoption resume.
    """
    return os.environ.get(BORN_ADOPTED_RESUME_ENV, "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def adoption_marker_key(feature_id: str) -> str:
    return f"execution-control-adoption:{feature_id}"


def _existing_record(raw: Any, feature_id: str) -> Any | None:
    """Parse an existing marker body; None when absent/corrupt/foreign."""
    if raw is None or raw == "" or InFlightAdoptionRecord is None:
        return None
    body = raw
    if isinstance(body, (bytes, bytearray)):
        body = bytes(body).decode("utf-8", "surrogateescape")
    if isinstance(body, (dict, list)):
        body = json.dumps(body)
    try:
        record = InFlightAdoptionRecord.model_validate_json(str(body))
    except Exception:  # noqa: BLE001 - corrupt marker: synthesize fresh
        return None
    if str(getattr(record, "feature_id", "") or "") != feature_id:
        return None
    return record


async def upsert_born_adopted_record_at_seal(
    runner: Any,
    feature: Any,
    *,
    group_idx: int,
    dag_sha256: str,
    checkpoint_body: str,
    commit_hash: str = "",
) -> str:
    """Upsert the born-adopted adoption record after group ``group_idx`` seals.

    Returns "" on success (or no-op), an error string on failure so the
    caller can fail loud (no silent degradation while the flag is ON).

    Flag OFF: unconditional no-op ("").
    """
    if not born_adopted_resume_enabled():
        return ""
    if InFlightAdoptionRecord is None:
        return "InFlightAdoptionRecord model is unavailable"
    feature_id = str(getattr(feature, "id", "") or "")
    if not feature_id:
        return "feature id is missing; cannot write born-adopted record"
    if group_idx < 0:
        return f"invalid sealed group index {group_idx}"

    marker_key = adoption_marker_key(feature_id)
    try:
        raw = await runner.artifacts.get(marker_key, feature=feature)
    except Exception as exc:  # noqa: BLE001
        return f"failed to read {marker_key}: {type(exc).__name__}: {exc}"

    existing = _existing_record(raw, feature_id)
    next_idx = group_idx + 1
    if existing is not None:
        # MONOTONIC: never regress an already-advanced boundary (e.g. when an
        # older group's checkpoint re-projects during resume).
        if int(getattr(existing, "next_effective_group_idx", 0)) >= next_idx:
            return ""
        # Preserve ceremony/operator-context fields; only advance the
        # boundary. Re-validate through the model (model_copy would bypass
        # the validators).
        payload = existing.model_dump(mode="json")
        payload["completed_checkpoint_range"] = (0, group_idx)
        payload["next_effective_group_idx"] = next_idx
    else:
        projection_digest = hashlib.sha256(
            (checkpoint_body or "").encode("utf-8")
        ).hexdigest()
        payload = {
            "status": "adopted",
            "feature_id": feature_id,
            "candidate_commit": (
                str(commit_hash or "").strip() or f"born-adopted:g{group_idx}"
            ),
            "deploy_artifact_id": f"born-adopted:{feature_id}",
            "legacy_root_dag_artifact_id": 0,
            "legacy_root_dag_sha256": (
                str(dag_sha256 or "").strip() or "born-adopted"
            ),
            "completed_checkpoint_range": (0, group_idx),
            "next_effective_group_idx": next_idx,
            "projection_digest": projection_digest,
            "adopted_at": datetime.now(timezone.utc).isoformat(),
            "adopted_by": BORN_ADOPTED_ADOPTED_BY,
            "feature_state_at_adoption": "born-adopted",
            "notes": (
                "Synthesized by the flag-gated born-adopted seal writer "
                f"({BORN_ADOPTED_RESUME_ENV}); feature was created under the "
                "execution control plane — no legacy migration ceremony ran."
            ),
        }

    try:
        record = InFlightAdoptionRecord.model_validate(payload)
    except Exception as exc:  # noqa: BLE001
        return (
            f"born-adopted record failed model validation: "
            f"{type(exc).__name__}: {exc}"
        )
    try:
        await runner.artifacts.put(
            marker_key, record.model_dump_json(), feature=feature,
        )
    except Exception as exc:  # noqa: BLE001
        return f"failed to write {marker_key}: {type(exc).__name__}: {exc}"
    logger.info(
        "Born-adopted resume record upserted for feature %s at sealed group "
        "%d (next_effective_group_idx=%d)",
        feature_id, group_idx, next_idx,
    )
    return ""


async def load_pre_boundary_checkpoint_results(
    runner: Any,
    feature: Any,
    *,
    start_group: int,
) -> list[dict[str, Any]]:
    """Bundled P2 fix: collect raw result payloads for groups 0..start_group-1.

    The strict adoption resume sets ``start_group = next_effective_group_idx``
    and the checkpoint-reload loop starts there, so pre-boundary groups'
    results never reach ``all_results``/handover — dropping prior context on
    every resume. This loader reads the sealed ``dag-group:{idx}`` checkpoint
    bodies for the pre-boundary range and returns their result dicts in group
    order; the caller validates each into ``ImplementationResult`` and records
    them. Missing/corrupt checkpoints are skipped with a warning (the adoption
    record already attests the range completed; context load is best-effort
    enrichment, never a new blocker).
    """
    collected: list[dict[str, Any]] = []
    for g_idx in range(max(0, int(start_group))):
        key = f"dag-group:{g_idx}"
        try:
            raw = await runner.artifacts.get(key, feature=feature)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Pre-boundary context load: failed to read %s: %s", key, exc,
            )
            continue
        if not raw:
            logger.warning(
                "Pre-boundary context load: checkpoint %s is missing — "
                "its results will be absent from resume handover context",
                key,
            )
            continue
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            logger.warning(
                "Pre-boundary context load: checkpoint %s is not valid JSON — "
                "skipping",
                key,
            )
            continue
        results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, list):
            continue
        collected.extend(r for r in results if isinstance(r, dict))
    return collected
