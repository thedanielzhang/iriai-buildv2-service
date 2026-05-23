from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from iriai_build_v2.workflows.develop.execution.gates import (
    CandidateManifest,
    ContextBudget,
    ContextPackageBuilder,
    ContextReadRef,
    GateRequest,
    GateRunner,
    IdempotencyConflict,
    InMemoryEvidenceRecorder,
)


FEATURE_ID = "feature-slice-06"
DAG_SHA = "dag-sha-06"


class FakeGateGateway:
    def __init__(
        self,
        *,
        snapshots: list[dict] | None = None,
        contracts: list[dict] | None = None,
        attempts: list[dict] | None = None,
        patches: list[dict] | None = None,
    ) -> None:
        self.snapshots = {item["id"]: item for item in snapshots or []}
        self.contracts = {item["id"]: item for item in contracts or []}
        self.attempts = {item["id"]: item for item in attempts or []}
        self.patches = {item["id"]: item for item in patches or []}

    def get_workspace_snapshots_by_ids(self, ids):
        return [self.snapshots[item] for item in ids if item in self.snapshots]

    def get_contracts_by_ids(self, ids):
        return [self.contracts[item] for item in ids if item in self.contracts]

    def get_task_attempts_by_ids(self, ids):
        return [self.attempts[item] for item in ids if item in self.attempts]

    def get_patch_summaries_by_ids(self, ids):
        return [self.patches[item] for item in ids if item in self.patches]

    def get_events(self, *args, **kwargs):  # pragma: no cover - sentinel
        raise AssertionError("broad event reads are forbidden")

    def scan_artifacts(self, *args, **kwargs):  # pragma: no cover - sentinel
        raise AssertionError("broad artifact scans are forbidden")


class FakeContextGateway:
    def __init__(
        self,
        *,
        artifacts: dict[int, str] | None = None,
        events: dict[int, str] | None = None,
        feature_events: dict[str, list[dict]] | None = None,
        files: dict[str, str] | None = None,
    ) -> None:
        self.artifacts = artifacts or {}
        self.events = events or {}
        self.feature_events = feature_events or {}
        self.files = files or {}
        self.event_id_calls: list[list[int | str]] = []
        self.feature_event_calls: list[tuple[int | str, tuple[str, ...], int, int]] = []

    def get_artifacts_by_ids(self, ids):
        return [
            SimpleNamespace(id=item, text=self.artifacts[item])
            for item in ids
            if item in self.artifacts
        ]

    def get_artifact_by_exact_key(self, key):
        return None

    def get_events_by_ids(self, ids):
        self.event_id_calls.append(list(ids))
        return [
            SimpleNamespace(id=item, text=self.events[item])
            for item in ids
            if item in self.events
        ]

    def get_events_by_feature(self, feature_id, *, event_types, after_id, limit):
        self.feature_event_calls.append((
            feature_id,
            tuple(event_types),
            after_id,
            limit,
        ))
        rows = [
            SimpleNamespace(**item)
            for item in self.feature_events.get(str(feature_id), [])
            if int(item.get("id", 0)) > after_id
            and str(item.get("event_type") or "") in set(event_types)
        ]
        return rows[:limit]

    def get_file_slice(self, path, *, start_line, end_line, max_bytes):
        text = "\n".join(self.files[path].splitlines()[start_line - 1 : end_line])
        return SimpleNamespace(id=path, path=path, text=text, start_line=start_line, end_line=end_line)

    def get_events(self, *args, **kwargs):  # pragma: no cover - sentinel
        raise AssertionError("broad event reads are forbidden")

    def scan_artifacts(self, *args, **kwargs):  # pragma: no cover - sentinel
        raise AssertionError("broad artifact scans are forbidden")


def _request(**overrides) -> GateRequest:
    data = {
        "feature_id": FEATURE_ID,
        "dag_sha256": DAG_SHA,
        "group_idx": 6,
        "stage": "verify",
        "attempt": 1,
        "contract_ids": [1],
        "verification_gate_ids": ["gate-1"],
        "workspace_snapshot_ids": [10],
        "patch_summary_ids": [20],
        "task_attempt_ids": [30],
        "candidate_manifest_id": 40,
        "idempotency_key": "gate-request:slice-06",
    }
    data.update(overrides)
    return GateRequest(**data)


def _snapshot(**overrides) -> dict:
    data = {
        "id": 10,
        "feature_id": FEATURE_ID,
        "dag_sha256": DAG_SHA,
        "group_idx": 6,
        "root": "/workspace/canonical",
        "base_commit": "base-1",
        "snapshot_hash": "snapshot-hash",
        "present_paths": ["src/app.py"],
        "retired_aliases": [],
    }
    data.update(overrides)
    return data


def _contract(**overrides) -> dict:
    data = {
        "id": 1,
        "feature_id": FEATURE_ID,
        "dag_sha256": DAG_SHA,
        "group_idx": 6,
        "task_id": "TASK-1",
        "status": "active",
        "contract_digest": "contract-hash",
        "dependency_task_ids": [],
        "known_task_ids": [],
        "acceptance_criteria": [{"id": "ac-1"}],
        "verification_gates": [
            {
                "id": "gate-1",
                "source": "task_acceptance",
                "criterion_ids": ["ac-1"],
            }
        ],
        "allowed_paths": [{"path": "src/app.py"}],
        "required_paths": [{"path": "src/app.py"}],
        "generated_outputs": [],
        "forbidden_paths": [],
    }
    data.update(overrides)
    return data


def _attempt(**overrides) -> dict:
    data = {
        "id": 30,
        "feature_id": FEATURE_ID,
        "dag_sha256": DAG_SHA,
        "group_idx": 6,
        "attempt": 1,
        "task_id": "TASK-1",
    }
    data.update(overrides)
    return data


def _patch(**overrides) -> dict:
    data = {
        "id": 20,
        "feature_id": FEATURE_ID,
        "dag_sha256": DAG_SHA,
        "group_idx": 6,
        "attempt": 1,
        "repo_id": "repo-app",
        "workspace_snapshot_id": 10,
        "summary_sha256": "patch-hash",
        "actual_summary_sha256": "patch-hash",
        "touched_paths": ["src/app.py"],
        "modified_paths": ["src/app.py"],
    }
    data.update(overrides)
    return data


def _gateway(**overrides) -> FakeGateGateway:
    return FakeGateGateway(
        snapshots=overrides.pop("snapshots", [_snapshot()]),
        contracts=overrides.pop("contracts", [_contract()]),
        attempts=overrides.pop("attempts", [_attempt()]),
        patches=overrides.pop("patches", [_patch()]),
    )


def test_gate_request_requires_unique_input_ids() -> None:
    with pytest.raises(ValidationError, match="input ids must be unique"):
        _request(contract_ids=[1, 1])
    with pytest.raises(ValidationError, match="input ids must be unique"):
        _request(patch_summary_ids=[20, 20])
    with pytest.raises(ValidationError, match="input ids must be unique"):
        _request(workspace_snapshot_ids=[10, 10])


def test_workspace_snapshot_freshness_rejects_cross_feature_snapshot() -> None:
    result = GateRunner(
        _gateway(snapshots=[_snapshot(feature_id="different-feature")])
    ).run_preflight(_request())

    assert not result.approved
    assert not result.should_dispatch_verifier
    assert result.failure is not None
    assert result.failure.local_code == "workspace_snapshot.stale"
    assert result.failure.failure_class == "stale_projection"
    assert result.failure.failure_type == "workspace_snapshot_stale"
    assert result.nodes[-1].name == "workspace_snapshot_freshness"


def test_contract_closure_rejects_unknown_and_same_wave_dependencies() -> None:
    contracts = [
        _contract(
            id=1,
            task_id="TASK-1",
            dependency_task_ids=["TASK-2", "MISSING"],
            verification_gates=[{"id": "gate-1", "source": "task_acceptance", "criterion_ids": ["ac-1"]}],
        ),
        _contract(
            id=2,
            task_id="TASK-2",
            verification_gates=[{"id": "gate-2", "source": "task_acceptance", "criterion_ids": ["ac-1"]}],
        ),
    ]

    result = GateRunner(_gateway(contracts=contracts)).run_preflight(
        _request(contract_ids=[1, 2], verification_gate_ids=["gate-1", "gate-2"])
    )

    assert not result.approved
    assert result.failure is not None
    assert result.failure.local_code == "contract_closure.invalid"
    assert result.failure.failure_class == "contract_compile"
    assert result.failure.failure_type == "contract_same_wave_dependency"
    assert result.failure.details["unknown_dependencies"] == {"TASK-1": ["MISSING"]}
    assert result.failure.details["same_wave_dependencies"] == {"TASK-1": ["TASK-2"]}


def test_artifact_freshness_rejects_stale_dag_hash() -> None:
    result = GateRunner(_gateway(attempts=[_attempt(dag_sha256="old-dag")])).run_preflight(_request())

    assert not result.approved
    assert result.failure is not None
    assert result.failure.local_code == "artifact_freshness.stale"
    assert result.failure.failure_type == "verifier_context_stale"


def test_path_scope_rejects_retired_alias_and_missing_changed_file() -> None:
    result = GateRunner(
        _gateway(
            snapshots=[_snapshot(present_paths=[], retired_aliases=["legacy/app.py"])],
            patches=[_patch(touched_paths=["legacy/app.py", "src/app.py"], modified_paths=["legacy/app.py", "src/app.py"])],
        )
    ).run_preflight(_request())

    assert not result.approved
    assert result.failure is not None
    assert result.failure.local_code == "path_scope.invalid"
    assert result.failure.failure_class == "worktree_alias"
    invalid = result.failure.details["invalid_paths"]
    assert {"path": "legacy/app.py", "reason": "retired_alias_or_noncanonical"} in invalid


def test_path_scope_rejects_absolute_patch_paths_before_normalizing() -> None:
    result = GateRunner(
        _gateway(
            patches=[
                _patch(
                    touched_paths=["/src/app.py"],
                    modified_paths=["/src/app.py"],
                )
            ]
        )
    ).run_preflight(_request())

    assert not result.approved
    assert result.failure is not None
    assert result.failure.local_code == "path_scope.invalid"
    assert result.failure.failure_class == "worktree_alias"
    invalid = result.failure.details["invalid_paths"]
    assert {"path": "/src/app.py", "reason": "retired_alias_or_noncanonical"} in invalid


def test_patch_integrity_requires_summary_hash_match() -> None:
    result = GateRunner(
        _gateway(patches=[_patch(actual_summary_sha256="different-hash")])
    ).run_preflight(_request())

    assert not result.approved
    assert result.failure is not None
    assert result.failure.local_code == "patch_integrity.invalid"
    assert result.failure.failure_class == "evidence_corruption"
    assert result.failure.failure_type == "payload_digest_mismatch"


def test_patch_integrity_requires_workspace_snapshot_reference() -> None:
    result = GateRunner(
        _gateway(patches=[_patch(workspace_snapshot_id=None)])
    ).run_preflight(_request())

    assert not result.approved
    assert result.failure is not None
    assert result.failure.local_code == "patch_integrity.invalid"
    invalid = result.failure.details["invalid_patch_summaries"]
    assert {"patch_summary_id": 20, "reason": "missing_workspace_snapshot"} in invalid


def test_context_package_uses_only_explicit_refs() -> None:
    context_gateway = FakeContextGateway(
        artifacts={1: "artifact body"},
        events={2: "event body"},
        files={"src/app.py": "line 1\nline 2\nline 3"},
    )
    runner = GateRunner(
        _gateway(),
        context_builder=ContextPackageBuilder(context_gateway),
    )

    result = runner.run_preflight(
        _request(),
        context_refs=[
            ContextReadRef(source="artifact", id=1),
            ContextReadRef(source="event", id=2),
            ContextReadRef(source="file", path="src/app.py", start_line=1, end_line=2),
        ],
    )

    assert result.approved
    assert result.context_package is not None
    assert result.context_package.read_budget.blocked_unbounded_read_count == 0
    assert [query.source for query in result.context_package.read_budget.bounded_queries] == [
        "artifact",
        "event",
        "file",
    ]


def test_context_package_budget_records_omitted_optional_refs() -> None:
    builder = ContextPackageBuilder(
        FakeContextGateway(artifacts={1: "ok", 2: "this body is too large"}),
        budget=ContextBudget(max_aggregate_bytes=4),
    )

    package = builder.build(
        [
            ContextReadRef(source="artifact", id=1, required=True),
            ContextReadRef(source="artifact", id=2, required=False),
        ]
    )

    assert package.approved
    assert package.read_budget.aggregate_bytes == 2
    assert [ref.id for ref in package.read_budget.omitted_optional_refs] == [2]
    assert package.read_budget.omitted_required_refs == []


def test_context_package_budget_rejects_omitted_required_ref() -> None:
    builder = ContextPackageBuilder(
        FakeContextGateway(artifacts={1: "this body is too large"}),
        budget=ContextBudget(max_aggregate_bytes=4),
    )

    package = builder.build([ContextReadRef(source="artifact", id=1, required=True)])

    assert not package.approved
    assert package.failure is not None
    assert package.failure.local_code == "context_package.insufficient"
    assert [ref.id for ref in package.read_budget.omitted_required_refs] == [1]


def test_context_package_rejects_missing_required_explicit_ref() -> None:
    builder = ContextPackageBuilder(FakeContextGateway(artifacts={}))

    package = builder.build([ContextReadRef(source="artifact", id=404, required=True)])

    assert not package.approved
    assert [ref.id for ref in package.read_budget.omitted_required_refs] == [404]


def test_context_package_enforces_artifact_event_count_budget() -> None:
    builder = ContextPackageBuilder(
        FakeContextGateway(artifacts={1: "one"}, events={2: "two"}),
        budget=ContextBudget(max_artifacts_events=1),
    )

    package = builder.build(
        [
            ContextReadRef(source="artifact", id=1, required=True),
            ContextReadRef(source="event", id=2, required=False),
        ]
    )

    assert package.approved
    assert package.read_budget.artifact_count == 1
    assert package.read_budget.event_count == 0
    assert [ref.id for ref in package.read_budget.omitted_optional_refs] == [2]


def test_context_package_rejects_broad_read_attempt() -> None:
    builder = ContextPackageBuilder(FakeContextGateway(events={1: "event body"}))

    package = builder.build(
        [ContextReadRef(source="event", lookup_kind="bounded_feature", required=True)]
    )

    assert not package.approved
    assert package.read_budget.blocked_unbounded_read_count == 1
    assert package.failure is not None
    assert package.failure.failure_class == "verifier_context"


def test_context_package_reads_bounded_feature_events_without_id_lookup() -> None:
    gateway = FakeContextGateway(
        feature_events={
            FEATURE_ID: [
                {"id": 1, "event_type": "dag_verify_start", "text": "old"},
                {"id": 2, "event_type": "dag_verify_finish", "text": "selected"},
                {"id": 3, "event_type": "unrelated", "text": "ignored"},
                {"id": 4, "event_type": "dag_verify_finish", "text": "second"},
            ]
        }
    )
    builder = ContextPackageBuilder(gateway)

    package = builder.build([
        ContextReadRef(
            source="event",
            lookup_kind="bounded_feature",
            id=FEATURE_ID,
            event_types=["dag_verify_finish"],
            after_id=1,
            limit=1,
            required=True,
        )
    ])

    assert package.approved
    assert gateway.event_id_calls == []
    assert gateway.feature_event_calls == [
        (FEATURE_ID, ("dag_verify_finish",), 1, 1)
    ]
    assert package.read_budget.event_count == 1
    query = package.read_budget.bounded_queries[0]
    assert query.lookup_kind == "bounded_feature"
    assert query.ids == [FEATURE_ID]
    assert query.limit == 1
    assert query.after_id == 1
    assert package.payloads[0]["id"] == 2


def test_gate_nodes_are_idempotent_for_same_input_hash() -> None:
    recorder = InMemoryEvidenceRecorder()
    runner = GateRunner(_gateway(), recorder=recorder)

    first = runner.run_preflight(_request())
    second = runner.run_preflight(_request())

    assert first.approved
    assert second.approved
    assert [node.id for node in first.nodes] == [1, 2, 3, 4, 5, 6]
    assert second.nodes == []
    assert [node.id for node in recorder.nodes] == [1, 2, 3, 4, 5, 6]


def test_gate_nodes_reject_same_key_different_input_hash() -> None:
    recorder = InMemoryEvidenceRecorder()
    runner = GateRunner(
        _gateway(patches=[_patch(), _patch(id=21, summary_sha256="other", actual_summary_sha256="other")]),
        recorder=recorder,
    )

    assert runner.run_preflight(_request()).approved

    with pytest.raises(IdempotencyConflict):
        runner.run_preflight(_request(patch_summary_ids=[21]))


def test_candidate_manifest_digest_changes_with_explicit_refs() -> None:
    first = CandidateManifest.from_request(_request())
    second = CandidateManifest.from_request(_request(patch_summary_ids=[21]))

    assert first.manifest_digest != second.manifest_digest
    assert first.idempotency_key.startswith("candidate-manifest:")
