"""Readiness item-2 (P0-2/P-9): default CHK-boundary ``dag_quiesce_hook``.

Flag-gated (``IRIAI_DAG_QUIESCE_OPERATOR_GATE``, default OFF = today's
behavior: no hook registered, a listed boundary WARNs loudly and continues).
When ON, both runner-build sites (``interfaces/_bootstrap.py`` and
``interfaces/slack/orchestrator.py``) register :func:`default_dag_quiesce_hook`
via :func:`register_default_dag_quiesce_hook`.

DRIVER SELF-CLEAR exit flow (binding operator directive 2026-06-10, runbook
§17 — CHK migration application is DELEGATED TO THE DRIVER). On each listed
CHK boundary (``IRIAI_QUIESCE_GROUP_INDEXES``) the hook:

1. derives GENERIC batch hints — the groups this boundary covers (since the
   previous listed boundary), their task ids and the ``*migration*`` paths
   among the checkpointed changed files (= the batched migration FILE LIST).
   Project-specific CHK enumeration is NOT baked in: the kaya CHK->group-index
   mapping plugs in POST-DAG by setting the list env (operator rider);
2. loads the boundary's verification PROBE LIST from the optional
   ``<workspace>/.iriai/chk-probes.json`` (``{"<after_group_idx>"|"g<A>":
   ["cmd", ...]}``) — when absent it states the path checked and points at
   the plan's CHK section (kaya probes arrive post-DAG);
3. writes a first-visit-only OPERATOR-ACTIONS ``DONE-by-driver`` RECORD (the
   item-4 writer, reused) carrying the migration file list, the probe list
   and the EXACT marker-write psql, so the driver's apply step is mechanical;
4. returns ``{"status": "paused", "done_by": "driver", "clear_method":
   "marker-write", "migration_files": [...], "probes": [...],
   "clear_command": "<exact psql>"}`` — the quiesce primitive's existing
   pause branch embeds it in the fail-loud paused marker and dispatch halts
   via the existing ``terminal_state="quiesced"`` channel.

The boundary does NOT wait on an operator query: NO ``.query.json`` is filed,
NO ``.answer.json`` is polled, no new interactive surfaces. The DRIVER (after
snapshot -> applying the authored migrations to the LOCAL compose dev DB ONLY,
in plan order -> running the probes) self-clears mechanically: it inserts a
new artifacts row for the marker key copying the latest paused payload with
``status:"complete"`` + ``cleared_by:"driver"`` + ``probe_evidence`` and
restarts — the primitive's EXISTING re-entry identity guard (status==complete
+ identity match) then skips the boundary without re-invoking this hook.

The hook itself NEVER executes migrations — it has no migration-executing
code path; it only quiesces and files the durable record (implementation-agent
prohibition unchanged).

Driver compatibility: ``dag-quiesce:*`` markers with ``status != complete``
are surface-don't-alarm in guard greps; the clear path is a marker write +
restart, both existing channels.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from .control_plane import _dag_quiesce_group_indexes

logger = logging.getLogger(__name__)

DAG_QUIESCE_OPERATOR_GATE_ENV = "IRIAI_DAG_QUIESCE_OPERATOR_GATE"
"""Flag (default OFF) gating registration of the default CHK-boundary hook."""

CHK_PROBES_FILENAME = "chk-probes.json"
"""Optional per-boundary probe lists at ``<workspace>/.iriai/chk-probes.json``:
``{"<after_group_idx>" | "g<after_group_idx>": ["probe cmd", ...]}``."""


def dag_quiesce_operator_gate_enabled() -> bool:
    """Item-2 flag (default OFF = no default hook = today's behavior)."""
    return os.environ.get(DAG_QUIESCE_OPERATOR_GATE_ENV, "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def register_default_dag_quiesce_hook(services: dict[str, Any]) -> None:
    """Flag-gated registration into a runner ``services`` dict (both sites).

    No-op when the flag is OFF (default). Never clobbers an explicitly wired
    ``dag_quiesce_hook``/``quiesce_hook``.
    """
    if not dag_quiesce_operator_gate_enabled():
        return
    if services.get("dag_quiesce_hook") or services.get("quiesce_hook"):
        return
    services["dag_quiesce_hook"] = default_dag_quiesce_hook


def _workspace_root(runner: Any) -> Path | None:
    workspace_mgr = (getattr(runner, "services", {}) or {}).get("workspace_manager")
    base = getattr(workspace_mgr, "_base", "") if workspace_mgr else ""
    return Path(base) if base else None


async def _derive_batch_hints(
    runner: Any,
    feature: Any,
    payload: dict[str, Any],
    *,
    after_group_idx: int,
    before_group_idx: int,
) -> dict[str, Any]:
    """Generic batch hints for the boundary's DONE-by-driver record + marker.

    Covers the groups sealed since the PREVIOUS listed boundary: their task
    ids and any checkpointed changed paths containing ``migration`` (the
    generic derivable slice = the batched migration file list — exact CHK
    content is project knowledge that arrives via the list env post-DAG).
    """
    previous_boundary = max(
        (idx for idx in _dag_quiesce_group_indexes() if idx < after_group_idx),
        default=-1,
    )
    groups_covered = list(range(previous_boundary + 1, after_group_idx + 1))
    covered_task_ids: list[str] = []
    changed_paths: list[str] = []
    get_artifact = getattr(getattr(runner, "artifacts", None), "get", None)
    if callable(get_artifact):
        for group_idx in groups_covered:
            try:
                raw = await get_artifact(f"dag-group:{group_idx}", feature=feature)
                if not raw:
                    continue
                data = json.loads(raw)
            except Exception as exc:  # noqa: BLE001 — hints are best-effort
                logger.warning(
                    "Quiesce batch hints: could not read dag-group:%d (%s)",
                    group_idx, exc,
                )
                continue
            covered_task_ids.extend(str(t) for t in (data.get("task_ids") or []))
            for result in data.get("results") or []:
                if not isinstance(result, dict):
                    continue
                for key in ("files_created", "files_modified"):
                    changed_paths.extend(
                        str(p) for p in (result.get(key) or [])
                    )
    migration_paths = sorted(
        {p for p in changed_paths if "migration" in p.lower()}
    )
    return {
        "boundary": f"g{after_group_idx}->g{before_group_idx}",
        "groups_covered": groups_covered,
        "covered_task_ids": sorted(set(covered_task_ids)),
        "migration_paths": migration_paths,
        "changed_path_count": len(changed_paths),
        "next_group_task_ids": [
            str(t) for t in (payload.get("next_group_task_ids") or [])
        ],
    }


def _load_chk_probes(
    workspace_root: Path | None, after_group_idx: int,
) -> tuple[list[str], str]:
    """Boundary probe list from ``.iriai/chk-probes.json`` (+ a note when not).

    Returns ``(probes, note)``; ``note`` is non-empty when no probes could be
    embedded and states the path checked + where the probes live instead.
    """
    if workspace_root is None:
        return [], (
            "no workspace root available to read .iriai/chk-probes.json — "
            "see the plan's CHK section for this boundary's probes"
        )
    path = workspace_root / ".iriai" / CHK_PROBES_FILENAME
    if not path.is_file():
        return [], (
            f"no probe file at {path} — see the plan's CHK section for this "
            "boundary's verification probes (kaya probes arrive post-DAG)"
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [], f"unreadable probe file {path}: {exc}"
    probes = (
        data.get(str(after_group_idx))
        or data.get(f"g{after_group_idx}")
        or []
    )
    if not probes:
        return [], (
            f"{path} has no entry for boundary g{after_group_idx} — see the "
            "plan's CHK section for this boundary's verification probes"
        )
    return [str(p) for p in probes], ""


def _marker_key(after_group_idx: int, before_group_idx: int) -> str:
    return f"dag-quiesce:g{after_group_idx}-before-g{before_group_idx}"


def _clear_command(feature: Any, after_group_idx: int, before_group_idx: int) -> str:
    """The EXACT psql the driver runs to self-clear (real key + feature id).

    Inserts a NEW artifacts row copying the latest paused payload with
    ``status:"complete"`` + ``cleared_by:"driver"`` + ``probe_evidence`` so
    the quiesce primitive's existing complete+identity re-entry guard skips
    the boundary on restart.
    """
    fid = getattr(feature, "id", "<FID>")
    key = _marker_key(after_group_idx, before_group_idx)
    return (
        "INSERT INTO artifacts (feature_id, key, value) "
        "SELECT feature_id, key, "
        "(value::jsonb || '{\"status\":\"complete\",\"cleared_by\":\"driver\","
        "\"probe_evidence\":\"<EVIDENCE>\"}'::jsonb)::text "
        f"FROM artifacts WHERE feature_id='{fid}' AND key='{key}' "
        "ORDER BY id DESC LIMIT 1;"
    )


async def default_dag_quiesce_hook(
    *,
    runner: Any,
    feature: Any,
    payload: dict[str, Any],
    after_group_idx: int,
    before_group_idx: int,
) -> dict[str, Any]:
    """The default CHK-boundary driver-self-clear gate (see module docstring).

    NEVER executes migrations and files NO operator query — it only derives
    the boundary's batch (migrations + probes + exact clear psql), writes the
    first-visit DONE-by-driver OPERATOR-ACTIONS record, and pauses.
    """
    hints = await _derive_batch_hints(
        runner,
        feature,
        payload,
        after_group_idx=after_group_idx,
        before_group_idx=before_group_idx,
    )
    workspace_root = _workspace_root(runner)
    probes, probe_note = _load_chk_probes(workspace_root, after_group_idx)
    clear_command = _clear_command(feature, after_group_idx, before_group_idx)
    marker_key = _marker_key(after_group_idx, before_group_idx)

    migrations_text = (
        "\n".join(f"  - {p}" for p in hints["migration_paths"])
        or "  (no '*migration*' paths derivable from the sealed checkpoints — "
        "consult the plan's CHK section for this boundary)"
    )
    probes_text = (
        "\n".join(f"  - {p}" for p in probes)
        or f"  ({probe_note})"
    )

    entry_state = _append_done_by_driver_record(
        runner,
        workspace_root=workspace_root,
        after_group_idx=after_group_idx,
        before_group_idx=before_group_idx,
        hints=hints,
        migrations_text=migrations_text,
        probes_text=probes_text,
        clear_command=clear_command,
    )

    logger.warning(
        "DAG quiesce boundary g%d->g%d paused for DRIVER-applied batched "
        "migrations (%d file(s), %d probe(s); clear = marker-write + restart; "
        "marker %s)",
        after_group_idx,
        before_group_idx,
        len(hints["migration_paths"]),
        len(probes),
        marker_key,
    )
    result: dict[str, Any] = {
        "approved": False,
        "status": "paused",
        "done_by": "driver",
        "clear_method": "marker-write",
        "migration_files": list(hints["migration_paths"]),
        "probes": probes,
        "clear_command": clear_command,
        "reason": (
            f"CHK boundary g{after_group_idx}->g{before_group_idx} quiesced "
            "for DRIVER-applied batched migrations (runbook §17 self-clear): "
            "snapshot, apply the listed migrations to the LOCAL compose dev "
            "DB only, run the listed probes, then self-clear via the exact "
            "marker-write psql in hook_result.clear_command and restart/resume"
        ),
        "batch_hints": hints,
        "operator_actions_entry": entry_state,
    }
    if probe_note:
        result["probe_note"] = probe_note
    return result


def _append_done_by_driver_record(
    runner: Any,
    *,
    workspace_root: Path | None,
    after_group_idx: int,
    before_group_idx: int,
    hints: dict[str, Any],
    migrations_text: str,
    probes_text: str,
    clear_command: str,
) -> str:
    """First-visit-only DONE-by-driver OPERATOR-ACTIONS record (item-4 writer).

    Returns a small state string recorded in the hook result:
    ``written`` | ``already-present`` | ``unavailable: ...`` (best-effort by
    design — the paused marker is the primary fail-loud artifact and carries
    the same migration/probe/clear payload).
    """
    title = (
        f"CHK boundary g{after_group_idx}->g{before_group_idx} — DRIVER "
        "applies batched migrations (DONE-by-driver)"
    )
    if workspace_root is None:
        logger.error(
            "Quiesce gate g%d->g%d: no workspace_manager service — cannot "
            "write the OPERATOR-ACTIONS record (the paused marker still "
            "carries the full self-clear payload)",
            after_group_idx,
            before_group_idx,
        )
        return "unavailable: no workspace_manager service"
    actions_path = workspace_root / ".iriai" / "OPERATOR-ACTIONS.md"
    boundary_token = f"CHK boundary g{after_group_idx}->g{before_group_idx}"
    try:
        if actions_path.is_file() and boundary_token in actions_path.read_text(
            encoding="utf-8"
        ):
            return "already-present"  # first-visit-only across re-entry
    except OSError as exc:  # noqa: PERF203 — single read
        logger.warning("Could not read %s: %s", actions_path, exc)

    try:
        from ..phases.implementation import _append_operator_actions_entry
    except Exception as exc:  # noqa: BLE001 — record is best-effort by design
        logger.warning("OPERATOR-ACTIONS writer unavailable: %s", exc)
        return f"unavailable: {exc}"
    _append_operator_actions_entry(
        runner,
        title=title,
        why=(
            f"DAG dispatch quiesced at the listed CHK boundary covering groups "
            f"{hints['groups_covered']} (tasks: "
            f"{', '.join(hints['covered_task_ids']) or 'n/a'}). Batched "
            f"migration files (authored in these groups):\n{migrations_text}"
        ),
        commands=(
            "DRIVER (NOT the implementation agents): 1) snapshot the compose "
            "data volumes; 2) apply the listed migrations to the LOCAL kaya "
            "compose dev DB ONLY, in plan order; 3) run the verification "
            f"probes:\n{probes_text}\n4) append probe evidence to this entry "
            "and mark it DONE-by-driver; 5) self-clear the quiesce via the "
            f"exact marker write:\n  {clear_command}\n6) restart/resume the "
            "workflow."
        ),
        verify=(
            f"dag-quiesce:g{after_group_idx}-before-g{before_group_idx} marker "
            "reaches status=complete (cleared_by=driver) and dispatch enters "
            f"group {before_group_idx} without re-quiescing"
        ),
    )
    return "written"
