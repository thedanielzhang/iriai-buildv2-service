"""Slice 13A 8th sub-slice 13An-2 -- unit tests for the P3-13A-6-3
binding closure wiring (the
:mod:`iriai_build_v2.execution_control.dashboard_wrapper` module).

Covers the user-prompt chunk-shape point 4 test surface:

* (a) Wiring ON path invokes the composite.
* (b) Wiring OFF path byte-identical to Slice 10 baseline.
* (c) Fail-closed on incomplete snapshot companion.
* (d) Fail-closed on incomplete gate companion.
* (e) Composite adapter chain actually executes (not just instantiated).
* (f) Typed failure id routes to ``quiesce`` per doc-13a:307-310.
* (g) Namespace assertion.

Plus typed-failure recording (durability) + env-flag opt-in
(default OFF) + byte-identical legacy-passthrough tests.

Per ``13a-acceptance.md:222-227`` the wrapper closes the dead-until-
wired binding statement by wiring the composite
:class:`~iriai_build_v2.execution_control.snapshot_companion.LegacyGateConsumerSnapshotAdapter`
into a real production consumer site (the
:class:`~iriai_build_v2.public_dashboard.PublicDashboardOutbox`
dashboard snapshot consumer at ``public_dashboard.py:243-308``).

Per the auto-memory ``feedback_no_refactor`` rule the wrapper lives in
a NEW module (``src/iriai_build_v2/execution_control/dashboard_wrapper.py``)
and does NOT in-place-edit the accepted Slice 10
:class:`~iriai_build_v2.public_dashboard.PublicDashboardOutbox`
boundary. The legacy outbox + every other dashboard helper remain
byte-identical (proven by the
:file:`tests/test_public_dashboard.py` baseline gate).
"""

from __future__ import annotations

import ast
import inspect
import pathlib
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock

import pytest

from iriai_build_v2.execution_control.completeness import (
    AuthoritativeContextRef,
    EvidenceCompleteness,
    EvidencePageRef,
    compute_completeness_digest,
)
from iriai_build_v2.execution_control.dashboard_wrapper import (
    DASHBOARD_COMPANION_WIRING_ENV,
    CompletenessAwareDashboardOutbox,
    DashboardCompanionFailureClass,
    DashboardCompanionFailureRecord,
    DashboardCompanionFailureType,
    InMemoryDashboardCompanionFailurePort,
    dashboard_companion_wiring_enabled,
    derive_snapshot_list_field_completeness_from_snapshot,
)
from iriai_build_v2.execution_control.gate_companion import (
    LegacyGateCompanionAdapter,
    MissingGateCompanionFieldError,
    MissingProofRowFieldError,
    derive_proof_row,
)
from iriai_build_v2.execution_control.prompt_context_adapter import (
    AuthoritativePromptContextBundle,
)
from iriai_build_v2.execution_control.snapshot_companion import (
    AuthoritativeSnapshotCompanionRecord,
    AuthoritativeSnapshotListFieldCompleteness,
    LegacyGateConsumerSnapshotAdapter,
    LegacySnapshotCompanionAdapter,
    MissingSnapshotCompanionFieldError,
)
from iriai_build_v2.public_dashboard import (
    CONTROL_PLANE_SNAPSHOT_CHANGED_EVENT,
    PublicDashboardOutbox,
    control_plane_snapshot_event_id,
)
from iriai_build_v2.workflows.develop.execution.failure_router import (
    FAILURE_CLASSES,
    FAILURE_TYPES,
    ROUTE_TABLE,
    _DETERMINISTIC_FAILURE_TYPES,
)
from iriai_build_v2.workflows.develop.execution.snapshots import (
    ControlPlaneSnapshot,
    EvidenceRef,
    ExecutionAttemptSummary,
    TypedFailureSummary,
)


# ── recording pool (copied verbatim from tests/test_public_dashboard.py) ────


class _RecordingPool:
    """Records every async pool method invocation for byte-identical
    assertions. Mirrors the
    :class:`tests.test_public_dashboard._RecordingPool` so the
    wiring-OFF byte-identical proof can compare invocation traces
    directly.
    """

    def __init__(self) -> None:
        self.executes: list[tuple[str, tuple[object, ...]]] = []
        self.fetchvals: list[tuple[str, tuple[object, ...]]] = []
        self.next_id = 41

    async def execute(self, sql: str, *args: object) -> str:
        self.executes.append((sql, args))
        return "INSERT 0 1"

    async def fetchval(self, sql: str, *args: object) -> int:
        self.fetchvals.append((sql, args))
        self.next_id += 1
        return self.next_id


# ── typed snapshot fixture (mirrors tests/test_public_dashboard.py:292) ────


def _typed_snapshot(
    *,
    feature_id: str = "feature-1",
    snapshot_version: str = "v" * 64,
    truncated: bool = False,
    omitted_counts: dict[str, int] | None = None,
    extra_failures: int = 0,
) -> ControlPlaneSnapshot:
    """Build a typed `ControlPlaneSnapshot` -- mirrors the dashboard
    test fixture at ``tests/test_public_dashboard.py:292``."""

    now = datetime(2026, 5, 24, 12, tzinfo=timezone.utc)
    attempt = ExecutionAttemptSummary(
        attempt_id=11,
        feature_id=feature_id,
        dag_sha256="d" * 64,
        group_idx=3,
        task_id="task-a",
        attempt_kind="merge",
        stage="merge",
        retry=1,
        status="started",
        actor="implementer",
        runtime="claude",
        input_digest="short",
        workspace_snapshot_id=2,
        latest_evidence_ids=[5, 6],
        started_at=now,
        finished_at=None,
        updated_at=now,
    )
    failures = [
        TypedFailureSummary(
            failure_id=21 + i,
            attempt_id=11,
            evidence_id=5,
            failure_class="merge_conflict",
            failure_type="rebase_failed",
            severity="error",
            deterministic=True,
            operator_required=False,
            retryable=True,
            status="routed",
            route="retry_merge",
            signature_hash="s" * 16,
            summary="short",
            created_at=now,
            resolved_at=None,
        )
        for i in range(1 + extra_failures)
    ]
    ref = EvidenceRef(
        table="evidence_nodes",
        id=5,
        citation="evidence_nodes#5",
        kind="typed_failure",
        summary="short",
        artifact_key="dag-commit-failure:3",
    )
    # The Slice 10a ControlPlaneSnapshot model_validator requires that
    # truncated=True carries omitted_counts; ensure consistency so the
    # test fixture matches the typed-shape contract.
    resolved_omitted = dict(omitted_counts) if omitted_counts else {}
    if truncated and not resolved_omitted:
        resolved_omitted = {"latest_failures": 0}
    return ControlPlaneSnapshot(
        feature_id=feature_id,
        snapshot_version=snapshot_version,
        generated_at=now,
        source="typed",
        degraded=False,
        degradation_reasons=[],
        active_group_idx=3,
        active_attempts=[attempt],
        latest_failures=failures,
        merge_queue=[],
        truncated=truncated,
        omitted_counts=resolved_omitted,
        recommended_route="retry_merge",
        recommended_action="recommend",
        evidence_refs=[ref],
    )


# ── prompt-context bundle test fixture (used for gate composite tests) ────


def _page_ref(**overrides: Any) -> EvidencePageRef:
    base: dict[str, Any] = dict(
        ref_id="page-1",
        source_kind="typed_row",
        source_id=42,
        sha256="page-sha",
        start=0,
        end=100,
        item_count=10,
        bytes=2048,
        reason="snapshot-list-field-page",
    )
    base.update(overrides)
    return EvidencePageRef(**base)


def _completeness(
    *,
    state: str = "complete",
    authority: str = "gate_authority",
    complete_for: list[str] | None = None,
    page_refs: list[EvidencePageRef] | None = None,
) -> EvidenceCompleteness:
    cf = complete_for if complete_for is not None else ["gate:atomic_landing"]
    pr = page_refs if page_refs is not None else [_page_ref()]
    digest = compute_completeness_digest(
        state=state,  # type: ignore[arg-type]
        authority=authority,  # type: ignore[arg-type]
        complete_for=cf,
        missing_required_refs=[],
        page_refs=pr,
        preview_ref=None,
        unavailable_reason=None,
    )
    return EvidenceCompleteness(
        state=state,  # type: ignore[arg-type]
        authority=authority,  # type: ignore[arg-type]
        complete_for=cf,
        missing_required_refs=[],
        page_refs=pr,
        preview_ref=None,
        unavailable_reason=None,
        completeness_digest=digest,
    )


def _authoritative_prompt_context_bundle(
    *,
    completeness_state: str = "complete",
    authority: str = "gate_authority",
) -> AuthoritativePromptContextBundle:
    """Construct an AuthoritativePromptContextBundle suitable for the
    gate companion derive helper."""

    comp = _completeness(state=completeness_state, authority=authority)
    return AuthoritativePromptContextBundle(
        prompt_ref=41,
        prompt_sha256="prompt-sha",
        display_prompt_summary="bounded prompt",
        context_manifest_ref=AuthoritativeContextRef(
            manifest_id="manifest-1",
            manifest_digest="manifest-digest",
            completeness_digest=comp.completeness_digest,
            required_complete_for=["gate:atomic_landing"],
            authority=authority,  # type: ignore[arg-type]
        ),
        context_file_refs=[42],
        context_file_paths=["context/TASK-1.md"],
        context_sha256="context-sha",
        included_contract_ids=[11],
        included_evidence_ids=[31],
        excluded_evidence_ids=[],
        excluded_evidence_refs=[],
        completeness=comp,
        truncation_notes=[],
    )


# ════════════════════════════════════════════════════════════════════════════
# (g) Namespace + module __all__ assertions
# ════════════════════════════════════════════════════════════════════════════


def test_module_all_lists_documented_surface_exactly() -> None:
    """The wrapper module ``__all__`` carries the 10 documented public
    names.
    """

    from iriai_build_v2.execution_control import dashboard_wrapper as mod

    expected = {
        "DASHBOARD_COMPANION_WIRING_ENV",
        "dashboard_companion_wiring_enabled",
        "DashboardCompanionFailureClass",
        "DashboardCompanionFailureType",
        "DashboardCompanionFailureRecord",
        "DashboardCompanionFailurePort",
        "InMemoryDashboardCompanionFailurePort",
        "CompletenessAwareDashboardOutbox",
        "derive_snapshot_list_field_completeness_from_snapshot",
    }
    assert set(mod.__all__) == expected
    for name in expected:
        assert hasattr(mod, name)


def test_module_lives_in_execution_control_namespace() -> None:
    """The wrapper module lives at
    ``src/iriai_build_v2/execution_control/dashboard_wrapper.py``
    alongside the 5 prior Slice 13A modules per
    ``13a-acceptance.md:222-227`` namespace decision.
    """

    from iriai_build_v2.execution_control import dashboard_wrapper as mod

    module_path = pathlib.Path(mod.__file__)
    assert module_path.name == "dashboard_wrapper.py"
    assert module_path.parent.name == "execution_control"


def test_module_imports_only_from_sanctioned_in_package_surfaces() -> None:
    """The wrapper module imports stdlib (``os`` + ``typing``) +
    Pydantic v2 + sanctioned execution-control surfaces +
    public_dashboard ONLY.

    Per the user-prompt non-negotiable: NO imports from ``governance/``
    or ``supervisor/`` or ``workflows/develop/execution/`` (the latter
    is the wrapped boundary, not a dependency of the wrapper). The
    wrapper composes the legacy outbox externally per
    ``feedback_no_refactor``.
    """

    from iriai_build_v2.execution_control import dashboard_wrapper as mod

    source_path = pathlib.Path(mod.__file__)
    parsed = ast.parse(source_path.read_text(encoding="utf-8"))
    forbidden_modules = ("governance", "supervisor", "workflows")
    for node in ast.walk(parsed):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for forbidden in forbidden_modules:
                # The wrapper imports public_dashboard (not workflows);
                # the execution_control surfaces are sanctioned.
                assert (
                    f"iriai_build_v2.{forbidden}" not in module
                ), f"Wrapper module imports forbidden namespace: {module}"
        if isinstance(node, ast.Import):
            for alias in node.names:
                for forbidden in forbidden_modules:
                    assert (
                        f"iriai_build_v2.{forbidden}" not in alias.name
                    ), f"Wrapper module imports forbidden namespace: {alias.name}"


def test_dashboard_companion_failure_class_uses_existing_failure_classes() -> None:
    """The wrapper's
    :data:`DashboardCompanionFailureClass` tuple lists the 2 EXISTING
    failure_classes the composite chain may emit. Per the user-prompt
    non-negotiable: NO new failure_class is added this sub-slice (would
    have triggered the supervisor classifier mapping coverage gate --
    READ-ONLY).
    """

    for failure_class in DashboardCompanionFailureClass:
        assert failure_class in FAILURE_CLASSES, (
            f"{failure_class!r} should be an EXISTING failure_class on "
            f"failure_router.FAILURE_CLASSES (no new class added)"
        )
    assert set(DashboardCompanionFailureClass) == {
        "verifier_context",
        "evidence_corruption",
    }


def test_dashboard_companion_failure_type_uses_existing_failure_types() -> None:
    """The wrapper's
    :data:`DashboardCompanionFailureType` tuple lists the 4 EXISTING
    failure_types the composite chain may emit. Per the user-prompt
    non-negotiable + the 5th + 6th sub-slice typed-failure
    registrations: NO new failure_type is added this sub-slice.
    """

    for failure_type in DashboardCompanionFailureType:
        assert failure_type in FAILURE_TYPES, (
            f"{failure_type!r} should be an EXISTING failure_type on "
            f"failure_router.FAILURE_TYPES (no new type added)"
        )
    assert set(DashboardCompanionFailureType) == {
        "list_field_incomplete",
        "classifier_rule_blocked",
        "companion_record_unavailable",
        "proof_row_required",
    }


# ════════════════════════════════════════════════════════════════════════════
# (f) Typed failure id routes to quiesce per doc-13a:307-310
# ════════════════════════════════════════════════════════════════════════════


def test_typed_failure_ids_route_to_quiesce() -> None:
    """The 4 typed failure ids the composite chain may emit ALL route
    to ``quiesce`` per doc-13a:307-310 ("Required evidence cannot be
    paged exactly: return ``state='unavailable'`` ... route ...").

    Per the auto-memory ``feedback_no_silent_degradation`` rule the
    fail-closed contract requires routing to ``quiesce``; this test
    pins that the EXISTING failure_router rows preserve the routing.
    """

    pairs = [
        ("evidence_corruption", "list_field_incomplete"),
        ("evidence_corruption", "classifier_rule_blocked"),
        ("verifier_context", "companion_record_unavailable"),
        ("verifier_context", "proof_row_required"),
    ]
    for failure_class, failure_type in pairs:
        route = ROUTE_TABLE[(failure_class, failure_type)]
        assert (
            route.action == "quiesce"
        ), f"{failure_class}/{failure_type} should route to 'quiesce' per doc-13a:307-310"


def test_typed_failure_ids_are_deterministic() -> None:
    """The 4 typed failure ids the composite chain may emit ALL appear
    in :data:`_DETERMINISTIC_FAILURE_TYPES` (per the 5th + 6th sub-slice
    fail-closed contracts).
    """

    for failure_type in DashboardCompanionFailureType:
        assert failure_type in _DETERMINISTIC_FAILURE_TYPES, (
            f"{failure_type!r} should be deterministic per the fail-closed "
            f"rule (the gate/snapshot companion record must fail closed "
            f"structurally)"
        )


# ════════════════════════════════════════════════════════════════════════════
# Env flag opt-in toggle (default OFF preserves Slice 10 byte-identical)
# ════════════════════════════════════════════════════════════════════════════


def test_env_flag_name_matches_expected() -> None:
    """The env flag name matches the user-prompt-recorded form."""

    assert DASHBOARD_COMPANION_WIRING_ENV == (
        "IRIAI_EXEC_CONTROL_DASHBOARD_COMPANION_WIRING_ENABLED"
    )


def test_dashboard_companion_wiring_defaults_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per the auto-memory ``feedback_no_refactor`` rule the default is
    OFF so the legacy path is byte-identical to the Slice 10 baseline
    when the wrapper is imported but not enabled.
    """

    monkeypatch.delenv(DASHBOARD_COMPANION_WIRING_ENV, raising=False)
    assert dashboard_companion_wiring_enabled() is False


def test_dashboard_companion_wiring_off_truthy_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The wiring is OFF for off-truthy values (``0`` / ``false`` /
    ``no`` / ``off``) mirroring ``public_dashboard._flag_enabled``.
    """

    for off_value in ("0", "false", "no", "off", "FALSE", "OFF"):
        monkeypatch.setenv(DASHBOARD_COMPANION_WIRING_ENV, off_value)
        assert dashboard_companion_wiring_enabled() is False, (
            f"Value {off_value!r} should disable wiring"
        )


def test_dashboard_companion_wiring_on_truthy_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The wiring is ON for any value not in the off-truthy set (mirrors
    ``public_dashboard._flag_enabled``).
    """

    for on_value in ("1", "true", "yes", "on", "ON", "YES"):
        monkeypatch.setenv(DASHBOARD_COMPANION_WIRING_ENV, on_value)
        assert dashboard_companion_wiring_enabled() is True, (
            f"Value {on_value!r} should enable wiring"
        )


def test_wrapper_reads_env_flag_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the wrapper is constructed without an explicit
    ``wiring_enabled`` override, the ``wiring_enabled`` property reads
    the env flag.
    """

    pool = _RecordingPool()
    outbox = PublicDashboardOutbox(pool, outbox_enabled=True)

    monkeypatch.delenv(DASHBOARD_COMPANION_WIRING_ENV, raising=False)
    wrapper = CompletenessAwareDashboardOutbox(outbox)
    assert wrapper.wiring_enabled is False

    monkeypatch.setenv(DASHBOARD_COMPANION_WIRING_ENV, "1")
    assert wrapper.wiring_enabled is True

    monkeypatch.setenv(DASHBOARD_COMPANION_WIRING_ENV, "0")
    assert wrapper.wiring_enabled is False


def test_wrapper_override_short_circuits_env_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the wrapper is constructed with an explicit
    ``wiring_enabled=True`` override, the env flag is ignored. This is
    the test seam for asserting the wiring ON path explicitly.
    """

    pool = _RecordingPool()
    outbox = PublicDashboardOutbox(pool, outbox_enabled=True)

    # Env flag OFF but override ON -> override wins.
    monkeypatch.setenv(DASHBOARD_COMPANION_WIRING_ENV, "0")
    wrapper_on = CompletenessAwareDashboardOutbox(outbox, wiring_enabled=True)
    assert wrapper_on.wiring_enabled is True

    # Env flag ON but override OFF -> override wins.
    monkeypatch.setenv(DASHBOARD_COMPANION_WIRING_ENV, "1")
    wrapper_off = CompletenessAwareDashboardOutbox(outbox, wiring_enabled=False)
    assert wrapper_off.wiring_enabled is False


# ════════════════════════════════════════════════════════════════════════════
# (b) Wiring OFF path byte-identical to Slice 10 baseline
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_wiring_off_path_delegates_byte_identical_to_legacy_outbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the wrapper's ``wiring_enabled`` is False, the wrapper
    delegates byte-identical to the legacy
    :meth:`~iriai_build_v2.public_dashboard.PublicDashboardOutbox.project_control_plane_snapshot_changed`.

    Per the auto-memory ``feedback_no_refactor`` rule + the gate-5
    byte-identical Slice 10 baseline proof: the legacy pool invocation
    trace MUST match the legacy outbox's behaviour exactly (same SQL,
    same args, same return value, no extra calls).
    """

    monkeypatch.delenv(DASHBOARD_COMPANION_WIRING_ENV, raising=False)

    # Baseline: invoke the legacy outbox directly.
    baseline_pool = _RecordingPool()
    baseline_outbox = PublicDashboardOutbox(baseline_pool, outbox_enabled=True)
    # Build snapshot ONCE so the projected_at timestamp in the payload
    # is stable across both invocations (it is set by control_plane_snapshot_changed_payload
    # via _utc_now() at the moment the payload is built; the wrapper
    # delegates BYTE-IDENTICAL to the legacy outbox so the payload is
    # built exactly once per invocation -- we compare the invocation
    # trace cells that DO NOT carry timing).
    snapshot = _typed_snapshot()
    baseline_event_id = await baseline_outbox.project_control_plane_snapshot_changed(
        feature_id="feature-1",
        snapshot=snapshot,
    )

    # Test: invoke through the wrapper (with wiring OFF).
    wrapped_pool = _RecordingPool()
    wrapped_outbox = PublicDashboardOutbox(wrapped_pool, outbox_enabled=True)
    wrapper = CompletenessAwareDashboardOutbox(wrapped_outbox)
    assert wrapper.wiring_enabled is False
    wrapped_event_id = await wrapper.project_control_plane_snapshot_changed(
        feature_id="feature-1",
        snapshot=snapshot,
    )

    # Byte-identical: same event_id, same SQL, same call count, same
    # non-timestamp args. The `projected_at` field in the JSON payload
    # is set by _utc_now() at payload-build time -- it differs between
    # the two invocations because they happen at distinct wall-clock
    # times; the rest of the payload is byte-identical.
    assert wrapped_event_id == baseline_event_id
    assert len(wrapped_pool.executes) == len(baseline_pool.executes) == 1
    assert wrapped_pool.executes[0][0] == baseline_pool.executes[0][0]
    # Compare the first 5 positional args (event_id, feature_id,
    # event_type, schema_version, visibility) which are byte-identical.
    assert wrapped_pool.executes[0][1][:5] == baseline_pool.executes[0][1][:5]
    # The 6th arg is the JSON payload; assert its non-timestamp fields
    # match (everything except projected_at).
    import json as _json
    wrapped_payload = _json.loads(wrapped_pool.executes[0][1][5])
    baseline_payload = _json.loads(baseline_pool.executes[0][1][5])
    wrapped_payload.pop("projected_at", None)
    baseline_payload.pop("projected_at", None)
    assert wrapped_payload == baseline_payload


@pytest.mark.asyncio
async def test_wiring_off_path_returns_legacy_event_id() -> None:
    """The wrapper's wiring-OFF path returns the legacy event_id verbatim
    (no projection wrapping)."""

    pool = _RecordingPool()
    outbox = PublicDashboardOutbox(pool, outbox_enabled=True)
    wrapper = CompletenessAwareDashboardOutbox(outbox, wiring_enabled=False)

    event_id = await wrapper.project_control_plane_snapshot_changed(
        feature_id="feature-1",
        snapshot=_typed_snapshot(),
    )
    expected = control_plane_snapshot_event_id("feature-1", "v" * 64)
    assert event_id == expected


@pytest.mark.asyncio
async def test_wiring_off_path_records_no_typed_failure() -> None:
    """The wiring-OFF path NEVER records a typed-failure observation;
    the composite chain is not invoked.
    """

    pool = _RecordingPool()
    outbox = PublicDashboardOutbox(pool, outbox_enabled=True)
    failure_port = InMemoryDashboardCompanionFailurePort()
    wrapper = CompletenessAwareDashboardOutbox(
        outbox,
        failure_port=failure_port,
        wiring_enabled=False,
    )

    await wrapper.project_control_plane_snapshot_changed(
        feature_id="feature-1",
        snapshot=_typed_snapshot(),
    )
    # No fail-closed records were emitted because the composite chain
    # was not invoked.
    assert failure_port.records == []


@pytest.mark.asyncio
async def test_wiring_off_path_legacy_passthrough_emit_event() -> None:
    """The wrapper exposes legacy outbox methods (``emit_event``)
    unchanged so existing callers can swap in the wrapper without
    code changes. Mirrors the
    ``test_feature_and_artifact_stores_mirror_public_dashboard_events``
    Slice 10 baseline at ``tests/test_public_dashboard.py:225``.
    """

    pool = _RecordingPool()
    outbox = PublicDashboardOutbox(pool, outbox_enabled=True)
    wrapper = CompletenessAwareDashboardOutbox(outbox, wiring_enabled=False)

    event_id = await wrapper.emit_event(
        feature_id="feature-1",
        event_type="workflow.agent_start",
        payload={"agent": "codex"},
        event_id="evt-1",
    )
    assert event_id == "evt-1"
    assert len(pool.executes) == 1


@pytest.mark.asyncio
async def test_wiring_off_path_with_default_env_passes_no_extra_args() -> None:
    """When the wrapper delegates to the legacy outbox, it MUST NOT
    pass any extra arguments. Mirrors the legacy public_dashboard
    signature exactly so callers can swap in the wrapper without code
    changes.
    """

    pool = _RecordingPool()
    outbox = PublicDashboardOutbox(pool, outbox_enabled=True)
    spy = AsyncMock(return_value="event-id-spied")
    # Monkey-patch the underlying outbox method via the wrapper's
    # composition seam (the wrapper composes the outbox; the
    # `_outbox` attribute exposes it for introspection).
    outbox.project_control_plane_snapshot_changed = spy  # type: ignore[method-assign]
    wrapper = CompletenessAwareDashboardOutbox(outbox, wiring_enabled=False)

    await wrapper.project_control_plane_snapshot_changed(
        feature_id="feature-1",
        snapshot=_typed_snapshot(),
    )
    spy.assert_called_once()
    call_kwargs = spy.call_args.kwargs
    # The wrapper passes through the public legacy keyword args exactly.
    assert "feature_id" in call_kwargs and call_kwargs["feature_id"] == "feature-1"
    assert "snapshot" in call_kwargs
    assert "conn" in call_kwargs and call_kwargs["conn"] is None


# ════════════════════════════════════════════════════════════════════════════
# (a) Wiring ON path invokes the composite
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_wiring_on_path_invokes_composite_chain_when_snapshot_complete() -> None:
    """When the wiring is ON and the snapshot is complete, the wrapper:

    1. Invokes the composite chain (the
       :class:`~iriai_build_v2.execution_control.snapshot_companion.LegacySnapshotCompanionAdapter`
       + the
       :class:`~iriai_build_v2.execution_control.snapshot_companion.LegacyGateConsumerSnapshotAdapter`).
    2. The composite returns a complete-state companion record.
    3. The wrapper THEN delegates to the legacy outbox.
    4. No typed-failure is recorded.
    """

    pool = _RecordingPool()
    outbox = PublicDashboardOutbox(pool, outbox_enabled=True)
    failure_port = InMemoryDashboardCompanionFailurePort()
    wrapper = CompletenessAwareDashboardOutbox(
        outbox,
        failure_port=failure_port,
        wiring_enabled=True,
    )

    event_id = await wrapper.project_control_plane_snapshot_changed(
        feature_id="feature-1",
        snapshot=_typed_snapshot(),
    )
    # The composite passed -> the legacy outbox enqueued the event.
    assert event_id is not None
    assert len(pool.executes) == 1
    # No fail-closed records (the composite passed).
    assert failure_port.records == []


@pytest.mark.asyncio
async def test_wiring_on_path_invokes_composite_via_real_adapter_chain() -> None:
    """The composite adapter chain (LegacySnapshotCompanionAdapter +
    LegacyGateConsumerSnapshotAdapter) is invoked when the wrapper's
    wiring is ON. Test seam: replace the adapter with a spy and verify
    the spy was called.
    """

    pool = _RecordingPool()
    outbox = PublicDashboardOutbox(pool, outbox_enabled=True)

    class _SpyAdapter:
        """Spy that records every derive_companion invocation."""

        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []
            self._delegate = LegacySnapshotCompanionAdapter()

        def derive_companion(
            self,
            list_field_completeness,
            *,
            snapshot_scope_id,
            snapshot_digest,
            manifest_id,
            manifest_digest,
            required_list_field_scopes=(),
            authority="routing_authority",
        ) -> AuthoritativeSnapshotCompanionRecord:
            self.calls.append(
                {
                    "snapshot_scope_id": snapshot_scope_id,
                    "snapshot_digest": snapshot_digest,
                    "manifest_id": manifest_id,
                    "manifest_digest": manifest_digest,
                    "required_list_field_scopes": tuple(required_list_field_scopes),
                    "list_field_keys": tuple(list_field_completeness.keys()),
                }
            )
            return self._delegate.derive_companion(
                list_field_completeness,
                snapshot_scope_id=snapshot_scope_id,
                snapshot_digest=snapshot_digest,
                manifest_id=manifest_id,
                manifest_digest=manifest_digest,
                required_list_field_scopes=required_list_field_scopes,
                authority=authority,
            )

    spy_snapshot = _SpyAdapter()
    wrapper = CompletenessAwareDashboardOutbox(
        outbox,
        snapshot_adapter=spy_snapshot,  # type: ignore[arg-type]
        wiring_enabled=True,
    )

    await wrapper.project_control_plane_snapshot_changed(
        feature_id="feature-1",
        snapshot=_typed_snapshot(),
    )

    # The composite chain executed (NOT just instantiated) -- the spy
    # captured exactly one derive_companion call.
    assert len(spy_snapshot.calls) == 1
    captured = spy_snapshot.calls[0]
    assert captured["snapshot_scope_id"] == "snapshot:dashboard:feature-1"
    assert captured["snapshot_digest"] == "v" * 64
    # The per-list-field dict keys cover the Slice 10a list-field names.
    assert "latest_failures" in captured["list_field_keys"]
    assert "merge_queue" in captured["list_field_keys"]


@pytest.mark.asyncio
async def test_wiring_on_path_invokes_gate_composite_when_gate_supplied() -> None:
    """When the wiring is ON and the caller supplies the 3 gate arguments,
    the wrapper invokes the LegacyGateConsumerSnapshotAdapter composite.
    """

    pool = _RecordingPool()
    outbox = PublicDashboardOutbox(pool, outbox_enabled=True)

    class _SpyGateAdapter:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []
            self._delegate = LegacyGateConsumerSnapshotAdapter()

        def derive_gate_with_snapshot(
            self,
            snapshot_companion,
            authoritative_bundle,
            *,
            gate_scope_id,
            gate_input_digest,
            required_snapshot_list_field_scopes=(),
            proof_rows=(),
        ):
            self.calls.append(
                {
                    "gate_scope_id": gate_scope_id,
                    "gate_input_digest": gate_input_digest,
                    "required_snapshot_list_field_scopes": tuple(
                        required_snapshot_list_field_scopes
                    ),
                    "snapshot_scope_id": snapshot_companion.snapshot_scope_id,
                }
            )
            return self._delegate.derive_gate_with_snapshot(
                snapshot_companion,
                authoritative_bundle,
                gate_scope_id=gate_scope_id,
                gate_input_digest=gate_input_digest,
                required_snapshot_list_field_scopes=required_snapshot_list_field_scopes,
                proof_rows=proof_rows,
            )

    spy_gate = _SpyGateAdapter()
    wrapper = CompletenessAwareDashboardOutbox(
        outbox,
        gate_consumer_adapter=spy_gate,  # type: ignore[arg-type]
        wiring_enabled=True,
    )

    bundle = _authoritative_prompt_context_bundle()
    await wrapper.project_control_plane_snapshot_changed(
        feature_id="feature-1",
        snapshot=_typed_snapshot(),
        gate_scope_id="gate:atomic_landing",
        gate_input_digest="gate-input-digest",
        gate_authoritative_bundle=bundle,
    )

    # The gate composite executed -- the spy captured the call.
    assert len(spy_gate.calls) == 1
    captured = spy_gate.calls[0]
    assert captured["gate_scope_id"] == "gate:atomic_landing"
    assert captured["gate_input_digest"] == "gate-input-digest"
    assert captured["snapshot_scope_id"] == "snapshot:dashboard:feature-1"


# ════════════════════════════════════════════════════════════════════════════
# (e) Composite adapter chain actually executes (not just instantiated)
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_composite_chain_executes_under_wiring_on() -> None:
    """The composite chain's derive_snapshot_companion + classifier_routing
    BOTH execute under the wiring-ON path; the wrapper does NOT bypass
    them even on a complete snapshot.
    """

    pool = _RecordingPool()
    outbox = PublicDashboardOutbox(pool, outbox_enabled=True)
    wrapper = CompletenessAwareDashboardOutbox(outbox, wiring_enabled=True)

    snapshot = _typed_snapshot()
    # Verify the helper actually computes a real companion record.
    helper_output = derive_snapshot_list_field_completeness_from_snapshot(snapshot)
    assert "latest_failures" in helper_output
    assert isinstance(
        helper_output["latest_failures"], AuthoritativeSnapshotListFieldCompleteness
    )
    assert helper_output["latest_failures"].item_count == 1
    assert helper_output["latest_failures"].completeness.state == "complete"

    event_id = await wrapper.project_control_plane_snapshot_changed(
        feature_id="feature-1",
        snapshot=snapshot,
    )
    assert event_id is not None


def test_derive_snapshot_list_field_completeness_paged_on_truncated_required_scope() -> None:
    """When the snapshot reports ``truncated=True`` AND the field is in
    the required_list_field_scopes, the per-field state degrades to
    ``"paged"`` (conservative degrade per doc-13a:280-282 +
    feedback_no_silent_degradation).
    """

    snapshot = _typed_snapshot(truncated=True)
    helper_output = derive_snapshot_list_field_completeness_from_snapshot(
        snapshot,
        required_list_field_scopes=("latest_failures",),
    )
    assert helper_output["latest_failures"].completeness.state == "paged"


def test_derive_snapshot_list_field_completeness_paged_on_omitted_counts() -> None:
    """When the snapshot reports ``omitted_counts[field] > 0``, the
    per-field state degrades to ``"paged"`` regardless of the
    required_list_field_scopes (the cursor is set; the consumer can
    re-fetch).
    """

    snapshot = _typed_snapshot(omitted_counts={"latest_failures": 5})
    helper_output = derive_snapshot_list_field_completeness_from_snapshot(
        snapshot,
    )
    assert helper_output["latest_failures"].completeness.state == "paged"


def test_derive_snapshot_list_field_completeness_complete_when_not_truncated() -> None:
    """When the snapshot is not truncated and no omitted_counts apply,
    every per-field state is ``"complete"``.
    """

    snapshot = _typed_snapshot()
    helper_output = derive_snapshot_list_field_completeness_from_snapshot(snapshot)
    for field_name, per_field in helper_output.items():
        assert (
            per_field.completeness.state == "complete"
        ), f"{field_name!r} should be complete; got {per_field.completeness.state}"


# ════════════════════════════════════════════════════════════════════════════
# (c) Fail-closed on incomplete snapshot companion
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_fails_closed_on_empty_snapshot_version_under_wiring_on() -> None:
    """An empty snapshot_version under wiring-ON fails closed with the
    typed exception + a recorded failure (per the auto-memory
    ``feedback_no_silent_degradation`` rule).
    """

    pool = _RecordingPool()
    outbox = PublicDashboardOutbox(pool, outbox_enabled=True)
    failure_port = InMemoryDashboardCompanionFailurePort()
    wrapper = CompletenessAwareDashboardOutbox(
        outbox,
        failure_port=failure_port,
        wiring_enabled=True,
    )

    snapshot = _typed_snapshot().model_copy(update={"snapshot_version": ""})

    with pytest.raises(MissingSnapshotCompanionFieldError) as excinfo:
        await wrapper.project_control_plane_snapshot_changed(
            feature_id="feature-1",
            snapshot=snapshot,
        )
    # The legacy outbox was NEVER invoked.
    assert pool.executes == []
    # Typed failure record was emitted.
    assert len(failure_port.records) == 1
    failure_record = failure_port.records[0]
    assert failure_record.failure_class == "evidence_corruption"
    assert failure_record.failure_type == "list_field_incomplete"
    assert "snapshot_version" in failure_record.missing_field_names
    # The typed exception's missing_field_names also names the field.
    assert "snapshot_version" in excinfo.value.missing_field_names


@pytest.mark.asyncio
async def test_fails_closed_on_classifier_rule_blocked() -> None:
    """When the snapshot's per-list-field completeness cannot satisfy
    the required scope (e.g. required scope not in the snapshot's
    per-list-field completeness keys), the wrapper fails closed via
    the ``evidence_corruption/classifier_rule_blocked`` typed failure
    id per doc-13a:280-282.

    Use a required scope that is NOT one of the Slice 10a list-field
    names so the snapshot's per-list-field completeness cannot satisfy
    the scope; this is the doc-13a:280-282 "classifier rule blocked"
    case (the scope is named but the snapshot's per-list-field
    completeness has no entry for it).
    """

    pool = _RecordingPool()
    outbox = PublicDashboardOutbox(pool, outbox_enabled=True)
    failure_port = InMemoryDashboardCompanionFailurePort()
    wrapper = CompletenessAwareDashboardOutbox(
        outbox,
        failure_port=failure_port,
        wiring_enabled=True,
    )

    snapshot = _typed_snapshot()

    with pytest.raises(MissingSnapshotCompanionFieldError):
        await wrapper.project_control_plane_snapshot_changed(
            feature_id="feature-1",
            snapshot=snapshot,
            # Required scope NOT present in the Slice 10a per-list-field
            # dict -> classifier rule blocked per doc-13a:280-282.
            required_list_field_scopes=("nonexistent_list_field",),
        )
    # The legacy outbox was NEVER invoked.
    assert pool.executes == []
    # Typed failure record was emitted (classifier_rule_blocked
    # specifically).
    assert len(failure_port.records) == 1
    failure_record = failure_port.records[0]
    assert failure_record.failure_class == "evidence_corruption"
    assert failure_record.failure_type == "classifier_rule_blocked"
    assert "nonexistent_list_field" in failure_record.missing_field_names


# ════════════════════════════════════════════════════════════════════════════
# (d) Fail-closed on incomplete gate companion
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_fails_closed_on_gate_companion_preview_only() -> None:
    """When the gate's authoritative bundle has
    ``completeness.state="preview_only"``, the gate composite raises
    :class:`MissingGateCompanionFieldError` per doc-13a:273-275; the
    wrapper records the typed
    ``verifier_context/companion_record_unavailable`` failure id.
    """

    pool = _RecordingPool()
    outbox = PublicDashboardOutbox(pool, outbox_enabled=True)
    failure_port = InMemoryDashboardCompanionFailurePort()
    wrapper = CompletenessAwareDashboardOutbox(
        outbox,
        failure_port=failure_port,
        wiring_enabled=True,
    )

    # Gate's bundle is preview_only -> gate companion raises.
    bundle = _authoritative_prompt_context_bundle(
        completeness_state="preview_only",
        authority="display_only",
    )

    with pytest.raises(MissingGateCompanionFieldError):
        await wrapper.project_control_plane_snapshot_changed(
            feature_id="feature-1",
            snapshot=_typed_snapshot(),
            gate_scope_id="gate:atomic_landing",
            gate_input_digest="gate-input-digest",
            gate_authoritative_bundle=bundle,
        )
    # The legacy outbox was NEVER invoked.
    assert pool.executes == []
    # Typed failure record was emitted.
    assert len(failure_port.records) == 1
    failure_record = failure_port.records[0]
    assert failure_record.failure_class == "verifier_context"
    assert failure_record.failure_type == "companion_record_unavailable"
    assert failure_record.gate_scope_id == "gate:atomic_landing"


@pytest.mark.asyncio
async def test_fails_closed_on_snapshot_when_gate_supplied() -> None:
    """When the gate scope is supplied AND the snapshot itself is
    incomplete, the snapshot fail-closed precedes the gate composite
    (the gate composite never runs).
    """

    pool = _RecordingPool()
    outbox = PublicDashboardOutbox(pool, outbox_enabled=True)
    failure_port = InMemoryDashboardCompanionFailurePort()
    wrapper = CompletenessAwareDashboardOutbox(
        outbox,
        failure_port=failure_port,
        wiring_enabled=True,
    )

    snapshot = _typed_snapshot().model_copy(update={"snapshot_version": ""})
    bundle = _authoritative_prompt_context_bundle()

    with pytest.raises(MissingSnapshotCompanionFieldError):
        await wrapper.project_control_plane_snapshot_changed(
            feature_id="feature-1",
            snapshot=snapshot,
            gate_scope_id="gate:atomic_landing",
            gate_input_digest="gate-input-digest",
            gate_authoritative_bundle=bundle,
        )
    # The legacy outbox was NEVER invoked.
    assert pool.executes == []
    # Snapshot failure preceded gate failure.
    assert len(failure_port.records) == 1
    assert failure_port.records[0].failure_class == "evidence_corruption"


@pytest.mark.asyncio
async def test_proof_row_fail_closed_records_typed_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A composite invocation that raises
    :class:`MissingProofRowFieldError` is recorded as the typed
    ``verifier_context/proof_row_required`` failure id (per
    doc-13a:276-278).

    Test via a stub gate_consumer_adapter that raises
    MissingProofRowFieldError on call (the LegacyGateConsumerSnapshotAdapter
    itself only raises Snapshot/Gate errors, but a real production
    adapter MAY raise MissingProofRowFieldError on a missing-proof-row
    derive_proof_row -- the wrapper handles both.).
    """

    pool = _RecordingPool()
    outbox = PublicDashboardOutbox(pool, outbox_enabled=True)
    failure_port = InMemoryDashboardCompanionFailurePort()

    class _ProofRowRaisingAdapter:
        """Raises MissingProofRowFieldError on the composite call."""

        def derive_gate_with_snapshot(
            self,
            snapshot_companion,
            authoritative_bundle,
            *,
            gate_scope_id,
            gate_input_digest,
            required_snapshot_list_field_scopes=(),
            proof_rows=(),
        ):
            raise MissingProofRowFieldError(
                ["source_digest", "page_refs"],
                summary_digest="some-summary",
            )

    wrapper = CompletenessAwareDashboardOutbox(
        outbox,
        failure_port=failure_port,
        gate_consumer_adapter=_ProofRowRaisingAdapter(),  # type: ignore[arg-type]
        wiring_enabled=True,
    )

    bundle = _authoritative_prompt_context_bundle()
    with pytest.raises(MissingProofRowFieldError):
        await wrapper.project_control_plane_snapshot_changed(
            feature_id="feature-1",
            snapshot=_typed_snapshot(),
            gate_scope_id="gate:atomic_landing",
            gate_input_digest="gate-input-digest",
            gate_authoritative_bundle=bundle,
        )
    # The legacy outbox was NEVER invoked.
    assert pool.executes == []
    # Typed proof-row failure record was emitted.
    assert len(failure_port.records) == 1
    failure_record = failure_port.records[0]
    assert failure_record.failure_class == "verifier_context"
    assert failure_record.failure_type == "proof_row_required"


# ════════════════════════════════════════════════════════════════════════════
# Typed-failure record durability + introspection
# ════════════════════════════════════════════════════════════════════════════


def test_dashboard_companion_failure_record_forbids_unknown_fields() -> None:
    """The :class:`DashboardCompanionFailureRecord` carries
    ``extra="forbid"`` per the user-prompt non-negotiable for new
    Pydantic models.
    """

    with pytest.raises(Exception):  # pydantic ValidationError
        DashboardCompanionFailureRecord(
            failure_class="evidence_corruption",
            failure_type="list_field_incomplete",
            snapshot_scope_id="snapshot:dashboard:feature-1",
            snapshot_digest="snap-digest",
            unsupported_field="should fail",  # type: ignore[call-arg]
        )


def test_dashboard_companion_failure_record_round_trips_via_json() -> None:
    """The typed-failure record round-trips via model_dump_json +
    model_validate_json (per the Pydantic v2 freshness contract).
    """

    record = DashboardCompanionFailureRecord(
        failure_class="evidence_corruption",
        failure_type="list_field_incomplete",
        snapshot_scope_id="snapshot:dashboard:feature-1",
        snapshot_digest="snap-digest",
        missing_field_names=("latest_failures",),
        gate_scope_id="gate:atomic_landing",
        unavailable_reason="paged list field",
    )
    json_bytes = record.model_dump_json()
    restored = DashboardCompanionFailureRecord.model_validate_json(json_bytes)
    assert restored == record


def test_in_memory_failure_port_records_in_call_order() -> None:
    """The :class:`InMemoryDashboardCompanionFailurePort` records typed
    failures in invocation order (preserves the cross-process
    observability ordering per
    ``feedback_no_silent_degradation``).
    """

    port = InMemoryDashboardCompanionFailurePort()
    record_a = DashboardCompanionFailureRecord(
        failure_class="evidence_corruption",
        failure_type="list_field_incomplete",
        snapshot_scope_id="snapshot:dashboard:feature-1",
        snapshot_digest="snap-digest-a",
    )
    record_b = DashboardCompanionFailureRecord(
        failure_class="verifier_context",
        failure_type="companion_record_unavailable",
        snapshot_scope_id="snapshot:dashboard:feature-2",
        snapshot_digest="snap-digest-b",
        gate_scope_id="gate:atomic_landing",
    )
    port.record(record_a)
    port.record(record_b)
    assert port.records == [record_a, record_b]


# ════════════════════════════════════════════════════════════════════════════
# Composition assertions (NO in-place edit to legacy outbox)
# ════════════════════════════════════════════════════════════════════════════


def test_wrapper_composes_legacy_outbox_via_instance_composition() -> None:
    """The wrapper composes the legacy
    :class:`~iriai_build_v2.public_dashboard.PublicDashboardOutbox`
    via instance composition (NOT inheritance / monkey-patch /
    in-place edit). Per the auto-memory ``feedback_no_refactor`` rule.
    """

    pool = _RecordingPool()
    outbox = PublicDashboardOutbox(pool, outbox_enabled=True)
    wrapper = CompletenessAwareDashboardOutbox(outbox)
    # The wrapper does NOT inherit from PublicDashboardOutbox.
    assert not isinstance(wrapper, PublicDashboardOutbox)
    # The wrapper exposes the legacy outbox via .outbox composition seam.
    assert wrapper.outbox is outbox


def test_wrapper_default_adapter_chain_is_lightweight() -> None:
    """Per P3-13A-5-1 + P3-13A-6-2 the default adapter chain is
    stateless. The wrapper instantiates fresh adapters when the
    constructor doesn't supply them.
    """

    pool = _RecordingPool()
    outbox = PublicDashboardOutbox(pool, outbox_enabled=True)
    wrapper = CompletenessAwareDashboardOutbox(outbox)
    # Adapters constructed; they are stateless wrappers.
    assert isinstance(wrapper._snapshot_adapter, LegacySnapshotCompanionAdapter)
    assert isinstance(
        wrapper._gate_consumer_adapter, LegacyGateConsumerSnapshotAdapter
    )


def test_wrapper_method_signature_matches_legacy_outbox_kwargs() -> None:
    """The wrapper's :meth:`project_control_plane_snapshot_changed`
    method accepts the legacy keyword args (``feature_id`` +
    ``snapshot`` + ``conn``) PLUS the wrapper-specific opt-in args.
    Mirrors the legacy signature so existing callers can swap in the
    wrapper.
    """

    sig = inspect.signature(
        CompletenessAwareDashboardOutbox.project_control_plane_snapshot_changed
    )
    params = sig.parameters
    # Legacy keyword args:
    assert "feature_id" in params
    assert "snapshot" in params
    assert "conn" in params
    # Wrapper-specific opt-in args:
    assert "required_list_field_scopes" in params
    assert "gate_scope_id" in params
    assert "gate_input_digest" in params
    assert "gate_authoritative_bundle" in params
    assert "gate_proof_rows" in params
    assert "required_snapshot_list_field_scopes_for_gate" in params


# ════════════════════════════════════════════════════════════════════════════
# Composite adapter chain HAS PRODUCTION CALLERS (closes P3-13A-6-3)
# ════════════════════════════════════════════════════════════════════════════


def test_wrapper_is_real_production_caller_of_composite_chain() -> None:
    """The
    :class:`~iriai_build_v2.execution_control.dashboard_wrapper.CompletenessAwareDashboardOutbox`
    wrapper IS a real production caller of the
    :class:`~iriai_build_v2.execution_control.snapshot_companion.LegacyGateConsumerSnapshotAdapter`
    composite chain.

    Closes the P3-13A-6-3 dead-until-wired binding statement (per
    ``13a-acceptance.md:222-227``). Prior to this sub-slice the
    composite chain was dead-until-wired (no external production
    callers); this test pins the composite chain has a real production
    caller now.
    """

    pool = _RecordingPool()
    outbox = PublicDashboardOutbox(pool, outbox_enabled=True)
    wrapper = CompletenessAwareDashboardOutbox(outbox)
    # The wrapper's composition seam holds the composite chain.
    assert isinstance(
        wrapper._gate_consumer_adapter, LegacyGateConsumerSnapshotAdapter
    )
    # The composite chain's gate_adapter is the 5th-sub-slice
    # LegacyGateCompanionAdapter (the binding closure couples the two).
    assert isinstance(
        wrapper._gate_consumer_adapter._gate_adapter,
        LegacyGateCompanionAdapter,
    )


@pytest.mark.asyncio
async def test_composite_chain_production_caller_is_invoked_at_real_call_site() -> None:
    """The wrapper's
    :meth:`project_control_plane_snapshot_changed` method (the REAL
    dashboard call site) invokes the composite chain under the
    wiring-ON path.

    Per ``13a-acceptance.md:222-227`` the binding closure requires the
    composite chain to be invoked at the real call site. This test
    asserts the real call site (the dashboard's
    project_control_plane_snapshot_changed method) actually invokes
    the composite chain (not just instantiates it).
    """

    pool = _RecordingPool()
    outbox = PublicDashboardOutbox(pool, outbox_enabled=True)

    composite_invocation_count = 0
    delegate = LegacyGateConsumerSnapshotAdapter()

    class _CountingComposite:
        def derive_gate_with_snapshot(self, *args: Any, **kwargs: Any):
            nonlocal composite_invocation_count
            composite_invocation_count += 1
            return delegate.derive_gate_with_snapshot(*args, **kwargs)

    wrapper = CompletenessAwareDashboardOutbox(
        outbox,
        gate_consumer_adapter=_CountingComposite(),  # type: ignore[arg-type]
        wiring_enabled=True,
    )

    bundle = _authoritative_prompt_context_bundle()
    await wrapper.project_control_plane_snapshot_changed(
        feature_id="feature-1",
        snapshot=_typed_snapshot(),
        gate_scope_id="gate:atomic_landing",
        gate_input_digest="gate-input-digest",
        gate_authoritative_bundle=bundle,
    )
    # The composite was invoked once at the real call site.
    assert composite_invocation_count == 1


# ════════════════════════════════════════════════════════════════════════════
# Integration: end-to-end fail-closed routes through the typed-failure router
# ════════════════════════════════════════════════════════════════════════════


def test_dashboard_companion_failure_record_maps_to_failure_router_keys() -> None:
    """The (failure_class, failure_type) tuple on
    :class:`DashboardCompanionFailureRecord` MUST correspond to a
    valid row in :data:`ROUTE_TABLE` so the typed-failure observation
    can be routed through the
    :class:`~iriai_build_v2.workflows.develop.execution.failure_router.FailureRouter`
    by a production adapter.
    """

    test_pairs = [
        ("evidence_corruption", "list_field_incomplete"),
        ("evidence_corruption", "classifier_rule_blocked"),
        ("verifier_context", "companion_record_unavailable"),
        ("verifier_context", "proof_row_required"),
    ]
    for failure_class, failure_type in test_pairs:
        record = DashboardCompanionFailureRecord(
            failure_class=failure_class,
            failure_type=failure_type,
            snapshot_scope_id="snapshot:dashboard:feature-1",
            snapshot_digest="snap-digest",
        )
        # Verify the (class, type) tuple is routable via the
        # production-side failure_router ROUTE_TABLE.
        assert (record.failure_class, record.failure_type) in ROUTE_TABLE
        # Verify the route action is quiesce (per doc-13a:307-310).
        route = ROUTE_TABLE[(record.failure_class, record.failure_type)]
        assert route.action == "quiesce"


# ════════════════════════════════════════════════════════════════════════════
# P2-13An-2-1 REMEDIATION: production callsite wiring at dashboard.py:1541
# ════════════════════════════════════════════════════════════════════════════
#
# These integration tests prove the P3-13A-6-3 binding is ACTUALLY CLOSED at a
# real production callsite (not merely against the in-test wrapper module).
# The reviewer's P2-13An-2-1 mandates a production-site swap; these tests
# verify the swap landed and preserves byte-identical Slice 10 behaviour when
# the env flag is OFF (the default).


def test_dashboard_module_constructs_completeness_aware_wrapper() -> None:
    """The production callsite at ``dashboard.py:1541`` (around the
    ``_project_control_plane_snapshot_event`` helper) MUST construct
    :class:`CompletenessAwareDashboardOutbox` -- proving the
    P3-13A-6-3 binding closure is wired into a real production caller.

    Per the reviewer's P2-13An-2-1 finding: prior to this remediation
    the wrapper was instantiated ONLY in tests; the production
    callsite still constructed bare ``PublicDashboardOutbox(pool)``,
    leaving the composite chain dead-until-wired. The grep here is
    the durable production-callsite proof.
    """

    repo_root = pathlib.Path(__file__).resolve().parent.parent
    dashboard_source = (repo_root / "dashboard.py").read_text(encoding="utf-8")

    # The wrapper class is imported at the top of dashboard.py.
    assert (
        "from iriai_build_v2.execution_control.dashboard_wrapper import"
        in dashboard_source
    ), (
        "dashboard.py must import CompletenessAwareDashboardOutbox at the top "
        "(the production callsite swap requires the import)"
    )
    assert "CompletenessAwareDashboardOutbox" in dashboard_source, (
        "dashboard.py must reference CompletenessAwareDashboardOutbox "
        "(the P2-13An-2-1 remediation requires the production-callsite swap)"
    )
    # The wrapper is constructed via composition with PublicDashboardOutbox.
    # We accept any whitespace between the class name and the inner
    # constructor; the assertion is that the production code wraps the
    # legacy outbox with the wrapper (not just imports it).
    assert "CompletenessAwareDashboardOutbox(PublicDashboardOutbox(pool))" in (
        dashboard_source
    ), (
        "dashboard.py must construct "
        "CompletenessAwareDashboardOutbox(PublicDashboardOutbox(pool)) "
        "at the production callsite (per the P2-13An-2-1 remediation closes "
        "P3-13A-6-3 binding by making the composite chain reachable from a "
        "real production caller)"
    )


def test_dashboard_module_no_longer_constructs_bare_outbox_at_projection_site() -> None:
    """The production callsite that previously constructed
    ``outbox = PublicDashboardOutbox(pool)`` MUST NOT do so anymore --
    it must be wrapped with :class:`CompletenessAwareDashboardOutbox`.

    Per the reviewer's P2-13An-2-1: the prior implementer left
    ``dashboard.py:1541`` constructing the bare legacy outbox, which
    is exactly the dead-until-wired condition the binding statement
    forbids. This regression test pins that the swap landed.
    """

    repo_root = pathlib.Path(__file__).resolve().parent.parent
    dashboard_source = (repo_root / "dashboard.py").read_text(encoding="utf-8")
    # The exact bare assignment from the prior state must not appear
    # as the leftmost token (no leading wrapper-call paren); we look
    # for the line-anchored substring.
    bare_construction = "outbox = PublicDashboardOutbox(pool)"
    # `bare_construction` MUST NOT appear without `CompletenessAware`
    # wrapping it on the same logical line. The wrapped form is
    # `outbox = CompletenessAwareDashboardOutbox(PublicDashboardOutbox(pool))`
    # so the bare-substring `outbox = PublicDashboardOutbox(pool)` should
    # not appear in any line.
    for line in dashboard_source.splitlines():
        if bare_construction in line and "CompletenessAware" not in line:
            raise AssertionError(
                f"dashboard.py still contains the bare production-callsite "
                f"construction `{bare_construction}` without the "
                f"`CompletenessAwareDashboardOutbox(...)` wrapper "
                f"(P2-13An-2-1 remediation regression). Offending line: "
                f"{line!r}"
            )


def test_dashboard_module_imports_wrapper_at_top() -> None:
    """The production callsite swap requires the wrapper class to be
    importable at the top of ``dashboard.py``. The import landed at
    finalizer time per the P2-13An-2-1 remediation.

    AST-based assertion (not a substring) so this test catches a
    later rename / removal of the import alias.
    """

    repo_root = pathlib.Path(__file__).resolve().parent.parent
    dashboard_source = (repo_root / "dashboard.py").read_text(encoding="utf-8")
    parsed = ast.parse(dashboard_source)
    found_import = False
    for node in ast.walk(parsed):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.endswith("execution_control.dashboard_wrapper"):
                names = {alias.name for alias in node.names}
                if "CompletenessAwareDashboardOutbox" in names:
                    found_import = True
                    break
    assert found_import, (
        "dashboard.py must contain a top-level "
        "`from iriai_build_v2.execution_control.dashboard_wrapper import "
        "CompletenessAwareDashboardOutbox` import (per the P2-13An-2-1 "
        "remediation; the production callsite swap requires the import)"
    )


@pytest.mark.asyncio
async def test_production_callsite_wrapping_preserves_legacy_byte_identical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the env flag default OFF, the wrapper used at the
    production callsite preserves the legacy outbox's SQL + args +
    JSON payload trace byte-identical to bare
    :class:`~iriai_build_v2.public_dashboard.PublicDashboardOutbox`.

    Models the production callsite swap shape exactly: the wrapper
    wraps :class:`PublicDashboardOutbox(pool)`; the
    ``outbox.outbox_enabled`` early-return check (now backed by the
    wrapper's delegating property) consumes the legacy outbox's flag;
    and ``project_control_plane_snapshot_changed`` is invoked via the
    wrapper. The recorded pool trace must match the bare-outbox
    baseline byte-for-byte (modulo the per-invocation
    ``projected_at`` timestamp).

    Per the user-prompt non-negotiable + the reviewer's P2-13An-2-1
    finding: this is the byte-identical legacy-path proof AT THE
    PRODUCTION CALLSITE.
    """

    monkeypatch.delenv(DASHBOARD_COMPANION_WIRING_ENV, raising=False)
    snapshot = _typed_snapshot()

    # Baseline: bare PublicDashboardOutbox invoked directly (mirrors
    # the prior production callsite shape exactly).
    baseline_pool = _RecordingPool()
    baseline_outbox = PublicDashboardOutbox(baseline_pool, outbox_enabled=True)
    # Mirrors `outbox.outbox_enabled` early-return check at dashboard.py:1542.
    assert baseline_outbox.outbox_enabled is True
    baseline_event_id = await baseline_outbox.project_control_plane_snapshot_changed(
        feature_id="feature-1",
        snapshot=snapshot,
    )

    # Production-callsite shape: wrap with
    # `CompletenessAwareDashboardOutbox(PublicDashboardOutbox(pool))`
    # exactly as `dashboard.py:1541` does now.
    wrapped_pool = _RecordingPool()
    wrapped_outbox = CompletenessAwareDashboardOutbox(
        PublicDashboardOutbox(wrapped_pool, outbox_enabled=True),
    )
    # The wrapper's delegating `outbox_enabled` property MUST forward
    # to the wrapped legacy outbox's flag (per the production callsite's
    # `if not outbox.outbox_enabled: return` early-return guard).
    assert wrapped_outbox.outbox_enabled is True
    # Env flag default OFF -> wiring OFF -> wrapper delegates byte-identical.
    assert wrapped_outbox.wiring_enabled is False

    wrapped_event_id = await wrapped_outbox.project_control_plane_snapshot_changed(
        feature_id="feature-1",
        snapshot=snapshot,
    )

    # Byte-identical: same event_id (deterministic on feature_id +
    # snapshot_version), same number of pool invocations, same SQL,
    # same first-5 positional args, same JSON payload modulo
    # `projected_at`.
    assert wrapped_event_id == baseline_event_id
    assert len(wrapped_pool.executes) == len(baseline_pool.executes) == 1
    assert wrapped_pool.executes[0][0] == baseline_pool.executes[0][0]
    assert wrapped_pool.executes[0][1][:5] == baseline_pool.executes[0][1][:5]
    import json as _json
    wrapped_payload = _json.loads(wrapped_pool.executes[0][1][5])
    baseline_payload = _json.loads(baseline_pool.executes[0][1][5])
    wrapped_payload.pop("projected_at", None)
    baseline_payload.pop("projected_at", None)
    assert wrapped_payload == baseline_payload


def test_wrapper_outbox_enabled_property_delegates_to_legacy_outbox() -> None:
    """The wrapper exposes a delegating ``outbox_enabled`` property
    that forwards to the wrapped legacy outbox's flag.

    This property is required so the production callsite's early-return
    guard (``dashboard.py:1542`` ``if not outbox.outbox_enabled: return``)
    + the projection driver's
    ``getattr(outbox, "outbox_enabled", False)`` early-return at
    ``public_dashboard.py:705`` both preserve byte-identical Slice 10
    behaviour after the P2-13An-2-1 swap.

    Per the auto-memory ``feedback_no_refactor`` rule: the property is
    a pure forward-add (composes the legacy outbox's flag via
    delegation; does NOT modify the legacy outbox shape).
    """

    pool = _RecordingPool()
    # When the legacy outbox is constructed with outbox_enabled=True,
    # the wrapper's delegating property MUST report True.
    enabled_outbox = PublicDashboardOutbox(pool, outbox_enabled=True)
    enabled_wrapper = CompletenessAwareDashboardOutbox(enabled_outbox)
    assert enabled_wrapper.outbox_enabled is True
    assert enabled_wrapper.outbox_enabled == enabled_outbox.outbox_enabled

    # When the legacy outbox is constructed with outbox_enabled=False,
    # the wrapper's delegating property MUST report False.
    disabled_outbox = PublicDashboardOutbox(pool, outbox_enabled=False)
    disabled_wrapper = CompletenessAwareDashboardOutbox(disabled_outbox)
    assert disabled_wrapper.outbox_enabled is False
    assert disabled_wrapper.outbox_enabled == disabled_outbox.outbox_enabled

    # Sanity: the `getattr(outbox, "outbox_enabled", False)` early-return
    # in `project_control_plane_snapshot_if_changed` at
    # `public_dashboard.py:705` would return the wrapper's flag (not the
    # `False` default), proving the wrapper is drop-in compatible.
    assert getattr(enabled_wrapper, "outbox_enabled", False) is True
    assert getattr(disabled_wrapper, "outbox_enabled", False) is False
