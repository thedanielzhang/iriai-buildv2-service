"""Item-1 (P0-1): flag-gated born-adopted resume record + bundled P2 context load.

Covers:
- flag default OFF and flag-OFF no-op (today's behavior exactly);
- flag-ON synthesized record passes the UNMODIFIED ``InFlightAdoptionRecord``
  model validators, the strict resume reader, and the boundary validator;
- ceremony-record preservation + monotonic no-regress upsert semantics;
- the ``_project_dag_group_checkpoint`` seal chokepoint integration;
- the bundled pre-boundary context loader (audit P2).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from iriai_compose import Workspace

from iriai_build_v2.execution_control.atomic_landing import InFlightAdoptionRecord
from iriai_build_v2.workflows._runner import TrackedWorkflowRunner
from iriai_build_v2.workflows.develop.phases import born_adopted as born_adopted_module
from iriai_build_v2.workflows.develop.phases import implementation as implementation_module
from iriai_build_v2.workflows.develop.phases.born_adopted import (
    BORN_ADOPTED_RESUME_ENV,
    adoption_marker_key,
    born_adopted_resume_enabled,
    load_pre_boundary_checkpoint_results,
    upsert_born_adopted_record_at_seal,
)


class _FeatureStore:
    async def transition_phase(self, feature_id: str, new_phase: str) -> None:
        del feature_id, new_phase

    async def log_event(self, *args, **kwargs) -> None:
        del args, kwargs


class _ContextProvider:
    async def resolve(self, *_args, **_kwargs) -> str:
        return ""


class _Artifacts:
    def __init__(self, initial: dict[str, str] | None = None) -> None:
        self.store = dict(initial or {})
        self.put_keys: list[str] = []

    async def get(self, key: str, *, feature) -> str:
        del feature
        return self.store.get(key, "")

    async def put(self, key: str, value: str, *, feature) -> None:
        del feature
        self.put_keys.append(key)
        self.store[key] = value


class _Runtime:
    name = "fake"


def _feature(feature_id: str = "feat-born") -> SimpleNamespace:
    return SimpleNamespace(
        id=feature_id, workspace_id="main", name="Feature", metadata={},
    )


def _runner(artifacts: _Artifacts) -> TrackedWorkflowRunner:
    return TrackedWorkflowRunner(
        feature_store=_FeatureStore(),
        agent_runtime=_Runtime(),
        secondary_runtime=None,
        interaction_runtimes={"terminal": object()},
        artifacts=artifacts,
        sessions=object(),
        context_provider=_ContextProvider(),
        workspaces={"main": Workspace(id="main", path=Path("/tmp"))},
    )


def _ceremony_record(feature_id: str) -> InFlightAdoptionRecord:
    return InFlightAdoptionRecord(
        feature_id=feature_id,
        candidate_commit="ceremony-commit",
        deploy_artifact_id="deploy-123",
        legacy_root_dag_artifact_id=7,
        legacy_root_dag_sha256="legacy-sha",
        completed_checkpoint_range=(0, 1),
        next_effective_group_idx=2,
        projection_digest="ceremony-digest",
        adopted_at="2026-06-01T00:00:00+00:00",
        adopted_by="operator",
        notes="real migration ceremony",
    )


# ── flag semantics ──────────────────────────────────────────────────


def test_flag_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(BORN_ADOPTED_RESUME_ENV, raising=False)
    assert born_adopted_resume_enabled() is False
    monkeypatch.setenv(BORN_ADOPTED_RESUME_ENV, "0")
    assert born_adopted_resume_enabled() is False
    monkeypatch.setenv(BORN_ADOPTED_RESUME_ENV, "false")
    assert born_adopted_resume_enabled() is False
    monkeypatch.setenv(BORN_ADOPTED_RESUME_ENV, "1")
    assert born_adopted_resume_enabled() is True
    monkeypatch.setenv(BORN_ADOPTED_RESUME_ENV, "true")
    assert born_adopted_resume_enabled() is True


@pytest.mark.asyncio
async def test_upsert_is_noop_when_flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(BORN_ADOPTED_RESUME_ENV, raising=False)
    artifacts = _Artifacts()
    error = await upsert_born_adopted_record_at_seal(
        _runner(artifacts),
        _feature(),
        group_idx=0,
        dag_sha256="sha",
        checkpoint_body="{}",
    )
    assert error == ""
    assert artifacts.put_keys == []
    assert artifacts.store == {}


# ── flag ON: synthesized record passes every unmodified validator ───


@pytest.mark.asyncio
async def test_upsert_synthesizes_record_accepted_by_strict_resume_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(BORN_ADOPTED_RESUME_ENV, "1")
    feature = _feature()
    artifacts = _Artifacts()
    runner = _runner(artifacts)

    error = await upsert_born_adopted_record_at_seal(
        runner,
        feature,
        group_idx=0,
        dag_sha256="dag-sha-256",
        checkpoint_body='{"group_idx": 0}',
        commit_hash="abc123",
    )
    assert error == ""
    marker_key = adoption_marker_key(str(feature.id))
    assert artifacts.put_keys == [marker_key]

    # Round-trips the UNMODIFIED model (atomic_landing untouched).
    record = InFlightAdoptionRecord.model_validate_json(artifacts.store[marker_key])
    assert record.status == "adopted"
    assert record.feature_id == str(feature.id)
    assert record.completed_checkpoint_range == (0, 0)
    assert record.next_effective_group_idx == 1
    assert record.candidate_commit == "abc123"
    assert record.legacy_root_dag_sha256 == "dag-sha-256"

    # Accepted by the strict-resume marker reader...
    read_record, read_error = await (
        implementation_module._execution_control_adoption_record_for_resume(
            runner, feature,
        )
    )
    assert read_error == ""
    assert read_record is not None

    # ...and passes the boundary validator for any group_count >= 1.
    assert implementation_module._validate_adoption_resume_boundary(
        read_record, group_count=1,
    ) == ""
    assert implementation_module._validate_adoption_resume_boundary(
        read_record, group_count=5,
    ) == ""


@pytest.mark.asyncio
async def test_upsert_advances_range_at_each_seal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(BORN_ADOPTED_RESUME_ENV, "1")
    feature = _feature()
    artifacts = _Artifacts()
    runner = _runner(artifacts)
    marker_key = adoption_marker_key(str(feature.id))

    for sealed_idx in (0, 1, 2, 3):
        error = await upsert_born_adopted_record_at_seal(
            runner,
            feature,
            group_idx=sealed_idx,
            dag_sha256="dag-sha",
            checkpoint_body="{}",
        )
        assert error == ""
        record = InFlightAdoptionRecord.model_validate_json(
            artifacts.store[marker_key]
        )
        assert record.completed_checkpoint_range == (0, sealed_idx)
        assert record.next_effective_group_idx == sealed_idx + 1
        assert implementation_module._validate_adoption_resume_boundary(
            record, group_count=4,
        ) == ""


@pytest.mark.asyncio
async def test_upsert_preserves_ceremony_record_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(BORN_ADOPTED_RESUME_ENV, "1")
    feature = _feature("feat-ceremony")
    ceremony = _ceremony_record(str(feature.id))
    marker_key = adoption_marker_key(str(feature.id))
    artifacts = _Artifacts({marker_key: ceremony.model_dump_json()})
    runner = _runner(artifacts)

    error = await upsert_born_adopted_record_at_seal(
        runner,
        feature,
        group_idx=4,
        dag_sha256="new-sha",
        checkpoint_body="{}",
    )
    assert error == ""
    record = InFlightAdoptionRecord.model_validate_json(artifacts.store[marker_key])
    # Boundary advanced...
    assert record.completed_checkpoint_range == (0, 4)
    assert record.next_effective_group_idx == 5
    # ...ceremony provenance preserved (NOT clobbered by synthesized values).
    assert record.candidate_commit == "ceremony-commit"
    assert record.deploy_artifact_id == "deploy-123"
    assert record.legacy_root_dag_artifact_id == 7
    assert record.legacy_root_dag_sha256 == "legacy-sha"
    assert record.projection_digest == "ceremony-digest"
    assert record.adopted_by == "operator"
    assert record.notes == "real migration ceremony"


@pytest.mark.asyncio
async def test_upsert_is_monotonic_never_regresses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(BORN_ADOPTED_RESUME_ENV, "1")
    feature = _feature("feat-mono")
    ceremony = _ceremony_record(str(feature.id))  # next_effective_group_idx=2
    marker_key = adoption_marker_key(str(feature.id))
    artifacts = _Artifacts({marker_key: ceremony.model_dump_json()})
    runner = _runner(artifacts)

    # Re-projection of an OLDER group (e.g. during resume) must not regress.
    error = await upsert_born_adopted_record_at_seal(
        runner,
        feature,
        group_idx=0,
        dag_sha256="sha",
        checkpoint_body="{}",
    )
    assert error == ""
    assert artifacts.put_keys == []  # no-op write
    record = InFlightAdoptionRecord.model_validate_json(artifacts.store[marker_key])
    assert record.completed_checkpoint_range == (0, 1)
    assert record.next_effective_group_idx == 2


@pytest.mark.asyncio
async def test_upsert_synthesizes_fresh_record_over_corrupt_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(BORN_ADOPTED_RESUME_ENV, "1")
    feature = _feature("feat-corrupt")
    marker_key = adoption_marker_key(str(feature.id))
    artifacts = _Artifacts({marker_key: "not json at all"})
    runner = _runner(artifacts)

    error = await upsert_born_adopted_record_at_seal(
        runner,
        feature,
        group_idx=2,
        dag_sha256="sha",
        checkpoint_body="{}",
    )
    assert error == ""
    record = InFlightAdoptionRecord.model_validate_json(artifacts.store[marker_key])
    assert record.completed_checkpoint_range == (0, 2)
    assert record.next_effective_group_idx == 3


# ── flag-OFF regression: the strict resume gate is unchanged ────────


@pytest.mark.asyncio
async def test_gate_still_blocks_without_marker_when_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(BORN_ADOPTED_RESUME_ENV, raising=False)
    feature = _feature("feat-blocked")
    runner = _runner(_Artifacts())

    record, error = await (
        implementation_module._execution_control_adoption_record_for_resume(
            runner, feature,
        )
    )
    assert record is None
    assert "missing required adoption marker" in error
    blocker = implementation_module._execution_control_adoption_resume_blocker(error)
    assert "in-flight adoption migration playbook" in blocker


# ── seal chokepoint integration (_project_dag_group_checkpoint) ─────


def _stub_projection_store(monkeypatch: pytest.MonkeyPatch) -> list:
    projected: list = []

    class _Store:
        test_mirror_group_checkpoint_to_artifacts = False

        async def project_group_checkpoint(self, projection) -> None:
            projected.append(projection)

    monkeypatch.setattr(
        implementation_module,
        "_execution_control_store_for_runner",
        lambda runner: _Store(),
    )
    monkeypatch.setattr(
        implementation_module,
        "StoredGroupCheckpointProjection",
        lambda **kwargs: SimpleNamespace(**kwargs),
    )
    return projected


@pytest.mark.asyncio
async def test_seal_chokepoint_writes_marker_only_when_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoint = {
        "group_idx": 0,
        "task_ids": ["TASK-0"],
        "results": [],
        "verdict": "approved",
        "commit_hash": "c0ffee",
        "dag_sha256": "dag-sha",
    }
    gate_proof = {"checkpoint_gate": "{}", "proof_digest": "digest"}

    # Flag OFF (default): seal succeeds, NO adoption marker is written.
    monkeypatch.delenv(BORN_ADOPTED_RESUME_ENV, raising=False)
    projected = _stub_projection_store(monkeypatch)
    feature = _feature("feat-seal-off")
    artifacts = _Artifacts()
    ok, error = await implementation_module._project_dag_group_checkpoint(
        _runner(artifacts),
        feature,
        0,
        checkpoint,
        dag_sha256="dag-sha",
        checkpoint_gate_proof=gate_proof,
    )
    assert (ok, error) == (True, "")
    assert len(projected) == 1
    assert artifacts.put_keys == []

    # Flag ON: same seal also upserts a gate-valid adoption marker.
    monkeypatch.setenv(BORN_ADOPTED_RESUME_ENV, "1")
    feature_on = _feature("feat-seal-on")
    artifacts_on = _Artifacts()
    runner_on = _runner(artifacts_on)
    ok, error = await implementation_module._project_dag_group_checkpoint(
        runner_on,
        feature_on,
        0,
        checkpoint,
        dag_sha256="dag-sha",
        checkpoint_gate_proof=gate_proof,
    )
    assert (ok, error) == (True, "")
    marker_key = adoption_marker_key(str(feature_on.id))
    assert artifacts_on.put_keys == [marker_key]
    record, read_error = await (
        implementation_module._execution_control_adoption_record_for_resume(
            runner_on, feature_on,
        )
    )
    assert read_error == ""
    assert implementation_module._validate_adoption_resume_boundary(
        record, group_count=3,
    ) == ""
    assert record.candidate_commit == "c0ffee"


@pytest.mark.asyncio
async def test_seal_chokepoint_fails_loud_when_flag_on_and_upsert_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(BORN_ADOPTED_RESUME_ENV, "1")
    _stub_projection_store(monkeypatch)

    class _BrokenArtifacts(_Artifacts):
        async def put(self, key: str, value: str, *, feature) -> None:
            raise RuntimeError("artifact store down")

    ok, error = await implementation_module._project_dag_group_checkpoint(
        _runner(_BrokenArtifacts()),
        _feature("feat-loud"),
        1,
        {"group_idx": 1, "commit_hash": "h", "results": []},
        dag_sha256="dag-sha",
        checkpoint_gate_proof={"checkpoint_gate": "{}"},
    )
    assert ok is False
    assert "born-adopted adoption-record upsert failed" in error
    assert "artifact store down" in error


# ── bundled P2 fix: pre-boundary context loader ─────────────────────


@pytest.mark.asyncio
async def test_pre_boundary_loader_collects_results_in_group_order() -> None:
    artifacts = _Artifacts({
        "dag-group:0": json.dumps({
            "group_idx": 0,
            "results": [{"task_id": "TASK-0", "summary": "g0"}],
        }),
        "dag-group:1": json.dumps({
            "group_idx": 1,
            "results": [
                {"task_id": "TASK-1", "summary": "g1a"},
                {"task_id": "TASK-2", "summary": "g1b"},
            ],
        }),
        # group 2 is AT the boundary (start_group=2) — must NOT load.
        "dag-group:2": json.dumps({
            "group_idx": 2,
            "results": [{"task_id": "TASK-3", "summary": "g2"}],
        }),
    })
    results = await load_pre_boundary_checkpoint_results(
        _runner(artifacts), _feature(), start_group=2,
    )
    assert [r["task_id"] for r in results] == ["TASK-0", "TASK-1", "TASK-2"]


@pytest.mark.asyncio
async def test_pre_boundary_loader_tolerates_missing_and_corrupt_checkpoints(
    caplog: pytest.LogCaptureFixture,
) -> None:
    artifacts = _Artifacts({
        # dag-group:0 missing entirely
        "dag-group:1": "{corrupt",
        "dag-group:2": json.dumps({
            "group_idx": 2,
            "results": [{"task_id": "TASK-OK", "summary": "ok"}],
        }),
    })
    with caplog.at_level("WARNING", logger=born_adopted_module.__name__):
        results = await load_pre_boundary_checkpoint_results(
            _runner(artifacts), _feature(), start_group=3,
        )
    assert [r["task_id"] for r in results] == ["TASK-OK"]
    warnings = [r.message for r in caplog.records]
    assert any("dag-group:0" in m and "missing" in m for m in warnings)
    assert any("dag-group:1" in m and "not valid JSON" in m for m in warnings)


@pytest.mark.asyncio
async def test_pre_boundary_loader_zero_start_group_is_empty() -> None:
    results = await load_pre_boundary_checkpoint_results(
        _runner(_Artifacts()), _feature(), start_group=0,
    )
    assert results == []
