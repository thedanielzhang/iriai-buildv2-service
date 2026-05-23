"""Slice 10c-1 — the mechanical read-only supervisor contract.

doc 10 ("Supervisor And Dashboard Integration") § "Read-Only And Audit
Exception Policy" + § "Tests" is the SPEC. These tests prove the read-only
contract is MECHANICAL and FAIL-CLOSED:

* every execution-authority (control-plane) writer path is structurally
  absent / denied before runtime parameters are inspected;
* a denied write produces a blocked-action audit ROW — never a best-effort
  mutation;
* the supervisor-owned audit / dedupe / outbox writers ARE allowed;
* the supervisor evidence service construction fails closed if it is ever
  wired with an execution-authority store;
* a static coverage assertion keeps the doc-10 writer surface in sync.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from iriai_build_v2.supervisor.actions import ActionPolicy
from iriai_build_v2.supervisor.mcp_server import SupervisorEvidenceMcpService
from iriai_build_v2.supervisor.models import (
    ActionLevel,
    EvidencePacket,
    FailureClass,
    SupervisorActionStatus,
    SupervisorMode,
)
from iriai_build_v2.supervisor.read_only import (
    CONTROL_PLANE_WRITER_METHODS,
    EXECUTION_AUTHORITY_WRITER_METHODS,
    FEATURE_TIMELINE_WRITER_METHODS,
    SUPERVISOR_OWNED_AUDIT_KEY_PREFIXES,
    BlockedExecutionWrite,
    ReadOnlyAuditArtifactSink,
    ReadOnlySupervisorViolation,
    assert_no_control_plane_writer,
    assert_read_only_supervisor_handles,
    is_supervisor_owned_audit_key,
)


# ── test doubles ────────────────────────────────────────────────────────────


def _packet(*, cursor: int = 100, next_cursor: int = 200) -> EvidencePacket:
    """A minimal EvidencePacket carrying the cursor facts ActionPolicy reads."""

    return EvidencePacket(
        feature_id="8ac124d6",
        group_idx=3,
        retry=0,
        phase="implementation",
        classification=FailureClass.DETERMINISTIC_UNBLOCK,
        confidence=0.85,
        facts={"cursor": cursor, "next_cursor": next_cursor},
        inference="deterministic unblock",
        recommended_action=ActionLevel.RECOMMEND,
    )


class _RecordingSink:
    """A supervisor-owned audit sink: records every put, mutates nothing real."""

    def __init__(self) -> None:
        self.records: list[tuple[str, object, object]] = []

    async def put(self, key: str, value: object, *, feature: object) -> None:
        self.records.append((key, value, feature))


class _ReadOnlyArtifactStore:
    """The artifact READ surface a read-only supervisor legitimately holds.

    It exposes `put` (the dual-use writer) so the audit-scoped wrapper has
    something real to delegate to, plus the bounded read methods. It exposes
    NO control-plane writer.
    """

    def __init__(self) -> None:
        self.puts: list[tuple[str, object]] = []

    async def put(self, key: str, value: object, *, feature: object) -> None:
        self.puts.append((key, value))

    async def list_record_summaries(self, **_kwargs: object) -> list[object]:
        return []

    async def get_slice(self, **_kwargs: object) -> dict[str, object] | None:
        return None


class _ReadOnlyFeatureStore:
    """The feature READ surface a read-only supervisor legitimately holds."""

    async def get_feature(self, _feature_id: str) -> object:
        return SimpleNamespace(id="8ac124d6", metadata={})

    async def list_event_summaries(self, *_args: object, **_kwargs: object) -> list[object]:
        return []


class _FakeExecutionControlStore:
    """A stand-in for `ExecutionControlStore` — the execution-authority store.

    It exposes the control-plane writers doc 10 forbids a read-only supervisor
    from holding. Used to prove the construction guard fails closed.
    """

    async def record(self, *_a: object, **_k: object) -> None:  # pragma: no cover - never called
        raise AssertionError("read-only supervisor must never reach a control-plane writer")

    async def record_runtime_failure(self, *_a: object, **_k: object) -> None:  # pragma: no cover
        raise AssertionError("read-only supervisor must never reach a control-plane writer")

    async def project_group_checkpoint(self, *_a: object, **_k: object) -> None:  # pragma: no cover
        raise AssertionError("read-only supervisor must never reach a control-plane writer")

    async def record_workspace_snapshot(self, *_a: object, **_k: object) -> None:  # pragma: no cover
        raise AssertionError("read-only supervisor must never reach a control-plane writer")

    async def allocate_sandbox_lease(self, *_a: object, **_k: object) -> None:  # pragma: no cover
        raise AssertionError("read-only supervisor must never reach a control-plane writer")

    async def put_task_contract(self, *_a: object, **_k: object) -> None:  # pragma: no cover
        raise AssertionError("read-only supervisor must never reach a control-plane writer")

    # a read method, to prove the guard fires on the WRITERS, not all methods
    async def get_control_plane_snapshot(self, *_a: object, **_k: object) -> None:  # pragma: no cover
        return None


# ── the doc-10 writer surface is split + complete ───────────────────────────


def test_execution_authority_surface_is_the_union_of_the_two_subsets():
    """Static coverage: the doc-10 mutation surface = control-plane ∪ timeline.

    A new writer added to either subset is forced into the union, so the
    coverage test and the construction guard stay in sync. The two subsets are
    disjoint — a writer is either control-plane-exclusive or feature/artifact
    dual-use, never both.
    """

    assert (
        EXECUTION_AUTHORITY_WRITER_METHODS
        == CONTROL_PLANE_WRITER_METHODS | FEATURE_TIMELINE_WRITER_METHODS
    )
    assert not (CONTROL_PLANE_WRITER_METHODS & FEATURE_TIMELINE_WRITER_METHODS)
    # Every doc-10-enumerated execution-authority mutation class is represented.
    for required in (
        "record",  # typed journal rows
        "start_dispatch_attempt",  # attempt state transitions
        "finish_dispatch_attempt",
        "record_runtime_failure",  # typed failures
        "project_group_checkpoint",  # checkpoints
        "project_task_result",  # execution artifact projection writes
        "put_task_contract",  # task contracts
        "record_workspace_snapshot",  # workspace snapshots
        "allocate_sandbox_lease",  # sandbox / merge-queue authority
        "record_verification_graph_node",  # gate evidence
    ):
        assert required in CONTROL_PLANE_WRITER_METHODS


def test_real_execution_control_store_exposes_every_control_plane_writer():
    """The CONTROL_PLANE_WRITER_METHODS names are real ExecutionControlStore methods.

    This binds the contract to the actual store class — if a control-plane
    writer is renamed, this test fails and forces the contract name to follow.
    """

    from iriai_build_v2.execution_control.store import ExecutionControlStore

    for name in CONTROL_PLANE_WRITER_METHODS:
        attr = getattr(ExecutionControlStore, name, None)
        assert callable(attr), f"ExecutionControlStore.{name} is not a method"


def test_control_plane_writer_methods_set_matches_execution_control_store_public_surface():
    """The REVERSE assertion: every public mutation method on the real
    ``ExecutionControlStore`` IS in :data:`CONTROL_PLANE_WRITER_METHODS`.

    The sibling test above (forward direction) catches a RENAME of a known
    writer. This test catches the OTHER drift mode: a NEWLY ADDED writer that
    silently slipped onto the store class without being added to the
    `CONTROL_PLANE_WRITER_METHODS` frozenset (which is what the construction
    guard / `assert_no_control_plane_writer` consult).

    Implementation note: we deliberately do NOT derive the expected writer set
    from a hard-coded *prefix* list (`record_`, `project_`, `put_`, …). A
    prefix-derivation approach is fragile — a future writer adopting a new
    verb (e.g. `commit_…`, `seal_…`, `dispatch_…`) would silently bypass the
    coverage. Instead we use the inverse, conservative classifier: a public
    method is a READER iff it starts with one of the known read-only prefixes
    (`get_`/`list_`/`read_`); EVERYTHING else on the public surface is treated
    as a candidate writer and must already be in
    `CONTROL_PLANE_WRITER_METHODS`. The symmetric difference between the two
    sets must therefore be empty.

    If a future change adds a new READER prefix (e.g. `query_…`), this test
    will fail with a clear violation list — at which point the reader prefix
    set below should be extended in tandem with `CONTROL_PLANE_WRITER_METHODS`,
    so the contract evolves DELIBERATELY rather than by silent omission.
    """

    import inspect

    from iriai_build_v2.execution_control.store import ExecutionControlStore

    # Known read-only verbs on the store. Anything else public is a candidate
    # writer the read-only contract must cover.
    _READER_PREFIXES = ("get_", "list_", "read_")

    public_methods = {
        name
        for name, value in inspect.getmembers(ExecutionControlStore)
        if not name.startswith("_") and inspect.isfunction(value)
    }
    candidate_writers = {
        name
        for name in public_methods
        if not name.startswith(_READER_PREFIXES)
    }

    symmetric_difference = candidate_writers ^ CONTROL_PLANE_WRITER_METHODS
    assert symmetric_difference == set(), (
        "ExecutionControlStore public-mutation surface drifted from "
        "CONTROL_PLANE_WRITER_METHODS. "
        f"Only on the class (newly-added writer not in the frozenset, or a "
        f"new reader prefix not yet excluded): "
        f"{sorted(candidate_writers - CONTROL_PLANE_WRITER_METHODS)}. "
        f"Only in the frozenset (writer removed from the class): "
        f"{sorted(CONTROL_PLANE_WRITER_METHODS - candidate_writers)}."
    )


# ── the construction guard fails closed on a control-plane writer ───────────


def test_guard_fails_closed_when_handle_is_the_execution_control_store():
    """assert_no_control_plane_writer raises on the execution-authority store."""

    with pytest.raises(ReadOnlySupervisorViolation) as excinfo:
        assert_no_control_plane_writer(
            _FakeExecutionControlStore(), role="test_handle"
        )
    message = str(excinfo.value)
    assert "control-plane writer" in message
    # the violating method names are surfaced for the operator
    assert "record_runtime_failure" in message


def test_guard_passes_for_read_only_stores_and_for_none():
    """The permitted read surfaces + an absent handle pass the guard."""

    assert_no_control_plane_writer(_ReadOnlyFeatureStore(), role="feature_store")
    assert_no_control_plane_writer(_ReadOnlyArtifactStore(), role="artifact_store")
    # None — the safest state — always passes.
    assert_no_control_plane_writer(None, role="absent")


def test_assert_read_only_handles_rejects_an_execution_control_store():
    """assert_read_only_supervisor_handles fails closed on any control-plane handle."""

    with pytest.raises(ReadOnlySupervisorViolation):
        assert_read_only_supervisor_handles(
            feature_store=_ReadOnlyFeatureStore(),
            artifact_store=_ReadOnlyArtifactStore(),
            execution_control_store=_FakeExecutionControlStore(),
        )
    # also rejects a control-plane writer that sneaks into a feature/artifact slot
    with pytest.raises(ReadOnlySupervisorViolation):
        assert_read_only_supervisor_handles(
            feature_store=_FakeExecutionControlStore(),
        )
    with pytest.raises(ReadOnlySupervisorViolation):
        assert_read_only_supervisor_handles(
            extra_handles={"sneaky": _FakeExecutionControlStore()},
        )


def test_assert_read_only_handles_passes_for_read_surfaces_only():
    """The doc-10-correct wiring (read surfaces, no control-plane store) passes."""

    assert_read_only_supervisor_handles(
        feature_store=_ReadOnlyFeatureStore(),
        artifact_store=_ReadOnlyArtifactStore(),
        execution_control_store=None,
    )


# ── MCP service construction enforcement (doc 10 § "Refactoring Steps" 8) ───


def test_mcp_service_construction_is_read_only_for_supplied_stores():
    """A correctly-wired MCP evidence service constructs and asserts read-only."""

    service = SupervisorEvidenceMcpService(
        feature_store=_ReadOnlyFeatureStore(),
        artifact_store=_ReadOnlyArtifactStore(),
    )
    # assert_read_only is idempotent and stays green for the read surfaces.
    service.assert_read_only()


def test_mcp_service_construction_fails_closed_on_control_plane_store():
    """Wiring the MCP evidence service with the execution-authority store fails closed.

    doc 10 § "Tests": "Supervisor service construction in read-only mode
    exposes no write-capable execution store handles ... before they reach the
    store."
    """

    with pytest.raises(ReadOnlySupervisorViolation):
        SupervisorEvidenceMcpService(
            feature_store=_ReadOnlyFeatureStore(),
            artifact_store=_FakeExecutionControlStore(),
        )


def test_mcp_service_exposes_no_write_tool():
    """Every MCP tool the service exposes is a read/query tool, never a writer.

    The read-only contract for the MCP surface is enforced by the *absence* of
    a write tool — assert that absence mechanically.
    """

    service = SupervisorEvidenceMcpService(
        feature_store=_ReadOnlyFeatureStore(),
        artifact_store=_ReadOnlyArtifactStore(),
    )
    public_methods = {
        name
        for name in dir(service)
        if not name.startswith("_") and callable(getattr(service, name))
    }
    # No public service method is a control-plane writer.
    assert not (public_methods & CONTROL_PLANE_WRITER_METHODS)
    # The evidence surface is read/query verbs only.
    for name in public_methods:
        assert name.startswith(
            ("get_", "list_", "probe_", "readonly_", "database_", "assert_")
        ), f"unexpected non-read MCP service method: {name}"


# ── supervisor-owned audit key classification ───────────────────────────────


def test_supervisor_owned_audit_keys_are_recognized():
    """Every supervisor-owned audit/decision/digest key prefix is allowed."""

    for prefix in SUPERVISOR_OWNED_AUDIT_KEY_PREFIXES:
        assert is_supervisor_owned_audit_key(f"{prefix}8ac124d6:200:x")
    # the real action_key shape is supervisor-owned
    assert is_supervisor_owned_audit_key(
        "supervisor-action:8ac124d6:200:restart_bridge:blocked"
    )


def test_non_supervisor_keys_are_not_audit_keys():
    """Execution-authority / product artifact keys are NOT supervisor-owned."""

    for key in (
        "dag-group:3",  # checkpoint projection
        "dag-task:g3:t1",  # execution artifact projection
        "dag-commit-failure:g3",
        "dag-verify:g3:initial",
        "product/src/main.py",  # product file
        "",  # empty key is never owned
    ):
        assert not is_supervisor_owned_audit_key(key)


# ── ReadOnlyAuditArtifactSink — allowed audit puts, denied everything else ──


@pytest.mark.asyncio
async def test_audit_sink_allows_supervisor_owned_audit_put():
    """A supervisor-owned audit put passes through to the wrapped store."""

    store = _ReadOnlyArtifactStore()
    sink = ReadOnlyAuditArtifactSink(store)
    feature = object()
    await sink.put(
        "supervisor-action:8ac124d6:200:restart_bridge:blocked",
        '{"kind": "supervisor-action"}',
        feature=feature,
    )
    assert store.puts == [
        (
            "supervisor-action:8ac124d6:200:restart_bridge:blocked",
            '{"kind": "supervisor-action"}',
        )
    ]


@pytest.mark.asyncio
async def test_audit_sink_denies_execution_authority_put_and_does_not_mutate():
    """A put to a non-audit key fails closed — and the wrapped store is untouched.

    doc 10 § "Read-Only And Audit Exception Policy": "Denied writes fail closed
    ... rather than a best-effort mutation."
    """

    store = _ReadOnlyArtifactStore()
    sink = ReadOnlyAuditArtifactSink(store)
    with pytest.raises(BlockedExecutionWrite) as excinfo:
        await sink.put("dag-group:3", '{"checkpoint": true}', feature=object())
    assert excinfo.value.action == "artifact_put"
    # the deny is FAIL-CLOSED: the wrapped store performed NO write.
    assert store.puts == []


# ── ActionPolicy — control-plane writer is structurally absent ──────────────


def test_action_policy_construction_fails_closed_on_execution_authority_handle():
    """ActionPolicy fails closed if wired with a control-plane writer.

    doc 10: even guarded bridge actions "cannot mutate typed execution state or
    product files" — so a control-plane writer is NEVER valid on an
    ActionPolicy, in read-only OR guarded mode.
    """

    for mode in (SupervisorMode.READ_ONLY, SupervisorMode.GUARDED):
        with pytest.raises(ReadOnlySupervisorViolation):
            ActionPolicy(mode=mode, execution_authority=_FakeExecutionControlStore())


def test_action_policy_construction_without_writer_handle_succeeds():
    """The default ActionPolicy (no execution_authority) is structurally read-only."""

    policy = ActionPolicy(mode=SupervisorMode.READ_ONLY)
    # the writer slot is structurally absent.
    assert policy.execution_authority is None


@pytest.mark.asyncio
async def test_guard_execution_write_blocks_and_writes_blocked_audit_row():
    """A denied execution write produces a BLOCKED supervisor-action audit row.

    doc 10 § "Tests": "Read-only action policy blocks execution/control-plane
    mutation and writes a blocked audit record."
    """

    sink = _RecordingSink()
    feature = object()
    policy = ActionPolicy(
        mode=SupervisorMode.READ_ONLY,
        artifact_sink=sink,
        feature=feature,
    )
    record = await policy.guard_execution_write(
        _packet(),
        action="project_group_checkpoint",
        reason="supervisor attempted a checkpoint projection",
    )
    # the deny is recorded as a BLOCKED audit row — never a mutation.
    assert record.status == SupervisorActionStatus.BLOCKED
    assert record.action == "project_group_checkpoint"
    assert "fail-closed" in record.reason
    assert "no mutation performed" in record.reason
    # the blocked-action audit ROW was persisted to the supervisor-owned sink.
    assert len(sink.records) == 1
    audit_key, _value, audit_feature = sink.records[0]
    assert audit_key == (
        "supervisor-action:8ac124d6:200:project_group_checkpoint:blocked"
    )
    assert audit_feature is feature
    assert is_supervisor_owned_audit_key(audit_key)


@pytest.mark.asyncio
async def test_guard_execution_write_through_audit_sink_writes_blocked_row():
    """End-to-end: a denied write via the audit sink still records a blocked row.

    The ReadOnlyAuditArtifactSink is the mechanical deny; the ActionPolicy's
    guard is the fail-closed handler that records the blocked-action audit row.
    The audit row write itself targets a supervisor-owned key, so it is NOT
    denied by the same sink.
    """

    store = _ReadOnlyArtifactStore()
    sink = ReadOnlyAuditArtifactSink(store)
    policy = ActionPolicy(
        mode=SupervisorMode.READ_ONLY,
        artifact_sink=sink,
        feature=object(),
    )
    # simulate a denied execution write surfacing as BlockedExecutionWrite,
    # caught and routed to the fail-closed audit handler.
    try:
        await sink.put("dag-task:g3:t1", "{}", feature=object())
    except BlockedExecutionWrite as blocked:
        record = await policy.guard_execution_write(
            _packet(),
            action=blocked.action,
            reason=blocked.reason,
        )
    else:  # pragma: no cover - the put above must be denied
        raise AssertionError("audit sink failed to deny an execution write")

    assert record.status == SupervisorActionStatus.BLOCKED
    # the wrapped store recorded ONLY the supervisor-owned blocked-action row,
    # never the denied dag-task:* execution write.
    assert len(store.puts) == 1
    assert store.puts[0][0].startswith("supervisor-action:")


@pytest.mark.asyncio
async def test_guard_execution_write_with_no_sink_still_fails_closed():
    """Without an audit sink, the guard still denies (returns BLOCKED, no mutation)."""

    policy = ActionPolicy(mode=SupervisorMode.READ_ONLY)
    record = await policy.guard_execution_write(
        _packet(),
        action="record_runtime_failure",
        reason="supervisor attempted a typed-failure write",
    )
    assert record.status == SupervisorActionStatus.BLOCKED
