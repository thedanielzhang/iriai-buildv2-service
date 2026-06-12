from __future__ import annotations

"""Pinning tests for the operator gate-override mechanism (W-OG).

Formalizes the 2026-06-11 hand-INSERT precedent (live artifacts row 2248683:
an operator fiat-completed gate task recorded as a ``dag-task:*`` row with an
OPERATOR-OVERRIDE summary) as a supported, audited two-step path:

1. ``iriai-build-v2 override-task`` (CLI, ``_override_task_core``) writes the
   durable ``dag-task-operator-override:{task_id}`` marker via the store layer
   after validating the rails (known-to-active-DAG, not already terminal,
   non-empty reason, idempotent re-run).
2. The implementation dispatch loop's per-task resume block
   (``_consume_operator_task_override``) consumes a valid marker BEFORE
   dispatch: it persists the terminal ``dag-task:{task_id}`` row engine-style
   with override provenance and skips execution. Consumption is single-shot —
   once the terminal row exists, later boots see ``"already_terminal"`` and
   the completed-marker short-circuit recognizes the row via
   ``result_is_operator_override`` (no contract-lineage revalidation).

Driving the full ``_implement_dag_dispatch_loop`` is blocked in unit tests by
the strict-resume adoption-marker requirement (same reason documented in
``test_integrated_lane_pending_marker.py``), so these tests exercise the two
new seams directly with store fakes mirroring ``PostgresArtifactStore``
semantics (append-only ``put``; ``get`` returns the newest row).
"""

import datetime as _dt
from types import SimpleNamespace

import click
import pytest

from iriai_build_v2.models.outputs import (
    ImplementationDAG,
    ImplementationResult,
    ImplementationTask,
)
from iriai_build_v2.workflows.develop.execution.operator_override import (
    OPERATOR_OVERRIDE_RESULT_NOTE,
    OperatorTaskOverride,
    build_override_result,
    new_operator_override,
    operator_override_marker_key,
    overrides_equivalent,
    parse_operator_override,
    result_is_operator_override,
)
from iriai_build_v2.workflows.develop.phases.implementation import (
    _consume_operator_task_override,
    _result_requires_durable_merge_queue,
)
from iriai_build_v2.interfaces.cli.app import _override_task_core


# ── Fakes mirroring the PostgresArtifactStore surface the seams touch ────────


class _FakeArtifacts:
    """Append-only rows; ``get`` returns the NEWEST value for a key — the
    exact semantics of ``PostgresArtifactStore.get``/``put``
    (``storage/artifacts.py:97-106`` / ``386-404``)."""

    def __init__(self) -> None:
        self.rows: list[tuple[int, str, str]] = []  # (id, key, value)
        self._next_id = 1

    async def get(self, key: str, *, feature) -> str | None:
        for _id, k, v in reversed(self.rows):
            if k == key:
                return v
        return None

    async def put(self, key: str, value, *, feature) -> None:
        self.rows.append((self._next_id, key, str(value)))
        self._next_id += 1

    async def get_record(self, key: str, *, feature):
        for _id, k, v in reversed(self.rows):
            if k == key:
                return {
                    "id": _id,
                    "created_at": _dt.datetime(2026, 6, 11, tzinfo=_dt.timezone.utc),
                    "value": v,
                }
        return None

    def rows_for(self, key: str) -> list[str]:
        return [v for _id, k, v in self.rows if k == key]


class _FakeFeatureStore:
    def __init__(self, features: dict[str, object]) -> None:
        self._features = features

    async def get_feature(self, feature_id: str):
        return self._features.get(feature_id)


def _feature(feature_id: str = "5b280bb4"):
    return SimpleNamespace(id=feature_id)


def _runner(artifacts: _FakeArtifacts):
    # `_log_feature_event` degrades to a no-op when the runner has no
    # feature_store.log_event (implementation.py:1643) — fine for unit tests.
    return SimpleNamespace(artifacts=artifacts, feature_store=None)


def _dag_json(task_ids: list[str]) -> str:
    return ImplementationDAG(
        tasks=[
            ImplementationTask(id=tid, name=f"task {tid}", description="d")
            for tid in task_ids
        ],
        execution_order=[task_ids],
    ).model_dump_json()


async def _seeded_cli_env(task_ids=("TASK-A", "TASK-B")):
    artifacts = _FakeArtifacts()
    feature = _feature()
    await artifacts.put("dag", _dag_json(list(task_ids)), feature=feature)
    feature_store = _FakeFeatureStore({feature.id: feature})
    return artifacts, feature_store, feature


def _cli_kwargs(**overrides):
    kwargs = dict(
        feature_id="5b280bb4",
        task_id="TASK-A",
        target_status="completed",
        reason="operator fiat: upstream gate satisfied out-of-band",
        authorized_by="operator",
        echo=lambda *_a, **_k: None,
    )
    kwargs.update(overrides)
    return kwargs


# ── Schema ────────────────────────────────────────────────────────────────────


def test_marker_schema_round_trips_and_key_shape() -> None:
    override = new_operator_override(
        task_id="TASK-A",
        reason="documented operator authorization",
        authorized_by="operator:daniel",
        feature_id="5b280bb4",
    )
    assert operator_override_marker_key("TASK-A") == (
        "dag-task-operator-override:TASK-A"
    )
    parsed = parse_operator_override(override.model_dump_json())
    assert parsed.task_id == "TASK-A"
    assert parsed.target_status == "completed"
    assert parsed.authorized_by == "operator:daniel"
    assert parsed.created_at  # writer-side timestamp provenance recorded
    assert overrides_equivalent(parsed, override)


def test_marker_schema_refuses_empty_reason_and_task() -> None:
    with pytest.raises(ValueError):
        OperatorTaskOverride(task_id="T", reason="   ")
    with pytest.raises(ValueError):
        OperatorTaskOverride(task_id="  ", reason="r")
    with pytest.raises(ValueError):
        new_operator_override(
            task_id="T", reason="r", authorized_by="op", feature_id="f",
            target_status="blocked",
        )


def test_override_result_shape_matches_precedent_row() -> None:
    """Mirrors row 2248683: completed, empty file lists/commit, OPERATOR-
    OVERRIDE summary; never trips the durable-merge-queue resume blocker."""
    override = new_operator_override(
        task_id="TASK-A", reason="fiat", authorized_by="op", feature_id="f",
    )
    result = build_override_result(override)
    assert result.status == "completed"
    assert result.summary.startswith("OPERATOR-OVERRIDE")
    assert result.files_created == [] and result.files_modified == []
    assert result.commit_hash == ""
    assert result_is_operator_override(result)
    assert not _result_requires_durable_merge_queue(result)
    # A normally-dispatched result is NOT recognized as an override.
    plain = ImplementationResult(task_id="TASK-A", summary="s", status="completed")
    assert not result_is_operator_override(plain)


# ── CLI write path (`_override_task_core`) ───────────────────────────────────


@pytest.mark.asyncio
async def test_cli_writes_marker_via_store_layer() -> None:
    artifacts, feature_store, _feature_obj = await _seeded_cli_env()
    summary = await _override_task_core(
        artifacts=artifacts, feature_store=feature_store, **_cli_kwargs()
    )
    assert summary["written"] is True
    key = operator_override_marker_key("TASK-A")
    stored = artifacts.rows_for(key)
    assert len(stored) == 1
    parsed = parse_operator_override(stored[0])
    assert parsed.task_id == "TASK-A"
    assert parsed.feature_id == "5b280bb4"
    assert parsed.reason.startswith("operator fiat")
    # The CLI never touches the engine-owned dag-task:* namespace.
    assert artifacts.rows_for("dag-task:TASK-A") == []


@pytest.mark.asyncio
async def test_cli_refuses_unknown_task() -> None:
    artifacts, feature_store, _f = await _seeded_cli_env()
    with pytest.raises(click.ClickException, match="unknown to the active DAG"):
        await _override_task_core(
            artifacts=artifacts,
            feature_store=feature_store,
            **_cli_kwargs(task_id="TASK-NOPE"),
        )
    assert artifacts.rows_for(operator_override_marker_key("TASK-NOPE")) == []


@pytest.mark.asyncio
async def test_cli_refuses_missing_dag_and_missing_feature() -> None:
    artifacts = _FakeArtifacts()
    feature = _feature()
    feature_store = _FakeFeatureStore({feature.id: feature})
    with pytest.raises(click.ClickException, match="no active implementation DAG"):
        await _override_task_core(
            artifacts=artifacts, feature_store=feature_store, **_cli_kwargs()
        )
    with pytest.raises(click.ClickException, match="not found"):
        await _override_task_core(
            artifacts=artifacts,
            feature_store=_FakeFeatureStore({}),
            **_cli_kwargs(),
        )


@pytest.mark.asyncio
async def test_cli_refuses_already_terminal_task() -> None:
    artifacts, feature_store, feature = await _seeded_cli_env()
    done = ImplementationResult(task_id="TASK-A", summary="s", status="completed")
    await artifacts.put("dag-task:TASK-A", done.model_dump_json(), feature=feature)
    with pytest.raises(click.ClickException, match="already has a terminal"):
        await _override_task_core(
            artifacts=artifacts, feature_store=feature_store, **_cli_kwargs()
        )
    assert artifacts.rows_for(operator_override_marker_key("TASK-A")) == []


@pytest.mark.asyncio
async def test_cli_allows_override_of_non_terminal_blocked_row() -> None:
    """The precedent: a blocked marker row (2248623) superseded by the
    operator's completed row. A non-terminal dag-task row must not refuse."""
    artifacts, feature_store, feature = await _seeded_cli_env()
    blocked = ImplementationResult(task_id="TASK-A", summary="s", status="blocked")
    await artifacts.put("dag-task:TASK-A", blocked.model_dump_json(), feature=feature)
    summary = await _override_task_core(
        artifacts=artifacts, feature_store=feature_store, **_cli_kwargs()
    )
    assert summary["written"] is True


@pytest.mark.asyncio
async def test_cli_refuses_empty_reason() -> None:
    artifacts, feature_store, _f = await _seeded_cli_env()
    with pytest.raises(click.ClickException, match="reason must be non-empty"):
        await _override_task_core(
            artifacts=artifacts,
            feature_store=feature_store,
            **_cli_kwargs(reason="   "),
        )
    assert artifacts.rows_for(operator_override_marker_key("TASK-A")) == []


@pytest.mark.asyncio
async def test_cli_rerun_same_args_is_idempotent_no_double_write() -> None:
    artifacts, feature_store, _f = await _seeded_cli_env()
    first = await _override_task_core(
        artifacts=artifacts, feature_store=feature_store, **_cli_kwargs()
    )
    second = await _override_task_core(
        artifacts=artifacts, feature_store=feature_store, **_cli_kwargs()
    )
    assert first["written"] is True
    assert second["written"] is False and second["idempotent"] is True
    assert len(artifacts.rows_for(operator_override_marker_key("TASK-A"))) == 1


@pytest.mark.asyncio
async def test_cli_rerun_with_different_reason_supersedes() -> None:
    artifacts, feature_store, _f = await _seeded_cli_env()
    await _override_task_core(
        artifacts=artifacts, feature_store=feature_store, **_cli_kwargs()
    )
    summary = await _override_task_core(
        artifacts=artifacts,
        feature_store=feature_store,
        **_cli_kwargs(reason="corrected justification"),
    )
    assert summary["written"] is True
    stored = artifacts.rows_for(operator_override_marker_key("TASK-A"))
    assert len(stored) == 2
    # The engine reads the NEWEST row (store `get` semantics).
    latest = parse_operator_override(stored[-1])
    assert latest.reason == "corrected justification"


# ── Dispatch-loop consumption (`_consume_operator_task_override`) ────────────


@pytest.mark.asyncio
async def test_consume_no_marker_is_none() -> None:
    artifacts = _FakeArtifacts()
    disposition, result, detail = await _consume_operator_task_override(
        _runner(artifacts), _feature(), "TASK-A", group_idx=2,
    )
    assert (disposition, result, detail) == ("none", None, "")
    assert artifacts.rows_for("dag-task:TASK-A") == []


@pytest.mark.asyncio
async def test_consume_valid_marker_writes_terminal_row_engine_style() -> None:
    artifacts = _FakeArtifacts()
    feature = _feature()
    override = new_operator_override(
        task_id="TASK-A",
        reason="fiat-complete RCAN-style gate",
        authorized_by="operator:daniel",
        feature_id=feature.id,
    )
    await artifacts.put(
        operator_override_marker_key("TASK-A"),
        override.model_dump_json(),
        feature=feature,
    )
    disposition, result, _detail = await _consume_operator_task_override(
        _runner(artifacts), feature, "TASK-A", group_idx=2,
    )
    assert disposition == "consumed"
    assert result is not None and result.status == "completed"
    # Terminal row persisted the same way the engine persists a completion:
    # `put(f"dag-task:{tid}", result.model_dump_json(), feature=feature)`.
    stored = artifacts.rows_for("dag-task:TASK-A")
    assert len(stored) == 1
    persisted = ImplementationResult.model_validate_json(stored[0])
    assert persisted.status == "completed"
    assert "OPERATOR-OVERRIDE" in persisted.summary
    assert OPERATOR_OVERRIDE_RESULT_NOTE in persisted.notes
    assert "operator:daniel" in persisted.summary or (
        "operator:daniel" in persisted.notes
    )
    assert result_is_operator_override(persisted)
    assert not _result_requires_durable_merge_queue(persisted)


@pytest.mark.asyncio
async def test_consume_is_single_shot_second_boot_sees_terminal() -> None:
    artifacts = _FakeArtifacts()
    feature = _feature()
    override = new_operator_override(
        task_id="TASK-A", reason="fiat", authorized_by="op", feature_id=feature.id,
    )
    await artifacts.put(
        operator_override_marker_key("TASK-A"),
        override.model_dump_json(),
        feature=feature,
    )
    first, _r1, _ = await _consume_operator_task_override(
        _runner(artifacts), feature, "TASK-A", group_idx=2,
    )
    second, r2, _ = await _consume_operator_task_override(
        _runner(artifacts), feature, "TASK-A", group_idx=2,
    )
    assert first == "consumed"
    assert second == "already_terminal"
    assert r2 is not None and r2.status == "completed"
    # No second dag-task write: the marker was NOT re-consumed.
    assert len(artifacts.rows_for("dag-task:TASK-A")) == 1


@pytest.mark.asyncio
async def test_consume_supersedes_non_terminal_blocked_row() -> None:
    artifacts = _FakeArtifacts()
    feature = _feature()
    blocked = ImplementationResult(task_id="TASK-A", summary="s", status="blocked")
    await artifacts.put("dag-task:TASK-A", blocked.model_dump_json(), feature=feature)
    override = new_operator_override(
        task_id="TASK-A", reason="fiat", authorized_by="op", feature_id=feature.id,
    )
    await artifacts.put(
        operator_override_marker_key("TASK-A"),
        override.model_dump_json(),
        feature=feature,
    )
    disposition, result, _ = await _consume_operator_task_override(
        _runner(artifacts), feature, "TASK-A", group_idx=2,
    )
    assert disposition == "consumed"
    # The newest dag-task row is now the override-completed one (store
    # latest-wins `get` semantics).
    rows = artifacts.rows_for("dag-task:TASK-A")
    assert len(rows) == 2
    latest = ImplementationResult.model_validate_json(rows[-1])
    assert latest.status == "completed" and result_is_operator_override(latest)


@pytest.mark.asyncio
async def test_consume_malformed_marker_is_invalid_and_loud() -> None:
    artifacts = _FakeArtifacts()
    feature = _feature()
    await artifacts.put(
        operator_override_marker_key("TASK-A"), '{"not": "an override"}',
        feature=feature,
    )
    disposition, result, detail = await _consume_operator_task_override(
        _runner(artifacts), feature, "TASK-A", group_idx=2,
    )
    assert disposition == "invalid"
    assert result is None
    assert "dag-task-operator-override:TASK-A" in detail
    # Fail-fast: no terminal row is synthesized from an unusable directive.
    assert artifacts.rows_for("dag-task:TASK-A") == []


@pytest.mark.asyncio
async def test_consume_task_id_mismatch_is_invalid() -> None:
    artifacts = _FakeArtifacts()
    feature = _feature()
    override = new_operator_override(
        task_id="TASK-B", reason="fiat", authorized_by="op", feature_id=feature.id,
    )
    # Marker stored under TASK-A but naming TASK-B.
    await artifacts.put(
        operator_override_marker_key("TASK-A"),
        override.model_dump_json(),
        feature=feature,
    )
    disposition, _result, detail = await _consume_operator_task_override(
        _runner(artifacts), feature, "TASK-A", group_idx=2,
    )
    assert disposition == "invalid"
    assert "TASK-B" in detail
    assert artifacts.rows_for("dag-task:TASK-A") == []
