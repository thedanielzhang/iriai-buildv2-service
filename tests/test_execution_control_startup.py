"""Slice 10f + Slice 12c — typed control-plane startup-guard tests.

Slice 10f (doc 10 step 10) covers :func:`assert_control_plane_ready` /
:func:`control_plane_readiness_report`: the guard PASSES when every dependency
is present, and FAILS CLOSED with a clear named error when one is missing
(doc 10 § "Tests" — the startup/deployment guard test). No real Postgres is
needed — the schema check is driven by fake pools.

Slice 12c (doc 12 § "Atomic Landing Contract") covers
:func:`read_control_plane_env_flag` + :func:`assert_control_plane_ready_for_workflow_launch`:
the env-flag helper enforces the opt-in default (unset → not enabled) and
fails closed on malformed values; the workflow-launch guard wraps the
Slice-10f assertion with the four doc-12 § "Operational Go/No-Go"
conditions (deploy-artifact / candidate-commit match, required migrations
present, global switch enabled when explicitly required, no forbidden
partial control enabled).
"""

from __future__ import annotations

import pytest

import dashboard
from iriai_build_v2.execution_control import startup
from iriai_build_v2.execution_control.startup import (
    ControlPlaneEnvFlagError,
    ControlPlaneReadinessReport,
    ControlPlaneStartupError,
    EnvFlagState,
    IRIAI_EXEC_CONTROL_PLANE_ENABLED_ENV,
    WorkflowLaunchReadinessReport,
    assert_control_plane_ready,
    assert_control_plane_ready_for_workflow_launch,
    control_plane_readiness_report,
    read_control_plane_env_flag,
)
from iriai_build_v2.supervisor import classifier_mapping
from iriai_build_v2.supervisor.actions import ActionPolicy
from iriai_build_v2.supervisor.models import SupervisorMode
from iriai_build_v2.supervisor.read_only import ReadOnlyAuditArtifactSink


class _AllTablesPresentPool:
    """Fake pool whose ``to_regclass`` reports every table present."""

    def __init__(self) -> None:
        self.queries: list[str] = []

    async def fetchval(self, sql: str, *_args: object) -> str:
        self.queries.append(sql)
        # to_regclass returns the regclass oid name when the table exists.
        return sql.split("'public.")[1].rstrip("')")


class _MissingTablePool:
    """Fake pool whose ``to_regclass`` reports one named table absent."""

    def __init__(self, absent_table: str) -> None:
        self._absent = absent_table
        self.queries: list[str] = []

    async def fetchval(self, sql: str, *_args: object) -> str | None:
        self.queries.append(sql)
        if f"'public.{self._absent}'" in sql:
            return None  # to_regclass NULL == table absent
        return self._absent


# ── happy path ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_assert_control_plane_ready_passes_when_all_dependencies_present() -> None:
    report = await assert_control_plane_ready()

    assert isinstance(report, ControlPlaneReadinessReport)
    assert report.ready is True
    assert report.missing == []
    # The six doc-10 components + the feature-timeline-writer re-confirm.
    for component in (
        "snapshot_store",
        "dashboard_route",
        "classifier_mapping",
        "mcp_snapshot_path",
        "slack_dedupe_store",
        "public_outbox_projection",
        "supervisor_feature_timeline_writer",
    ):
        assert component in report.checked


@pytest.mark.asyncio
async def test_readiness_report_passes_with_live_pool_and_read_only_policy() -> None:
    pool = _AllTablesPresentPool()
    # A correctly-wired read-only ActionPolicy: no execution-authority writer,
    # the audit-scoped ReadOnlyAuditArtifactSink as the artifact write surface.
    policy = ActionPolicy(
        mode=SupervisorMode.READ_ONLY,
        artifact_sink=ReadOnlyAuditArtifactSink(artifact_store=object()),
    )

    report = await control_plane_readiness_report(pool=pool, action_policy=policy)

    assert report.ready is True
    assert report.missing == []
    assert report.schema_checked is True
    assert "schema_tables" in report.checked
    assert "read_only_action_policy" in report.checked
    # to_regclass was probed for both dedupe tables + the public outbox table.
    assert len(pool.queries) == 3


# ── fail-closed: schema ─────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "absent_table",
    ["supervisor_digest_state", "supervisor_digest_audit", "public_dashboard_outbox"],
)
async def test_assert_control_plane_ready_fails_closed_when_a_table_is_absent(
    absent_table: str,
) -> None:
    pool = _MissingTablePool(absent_table)

    with pytest.raises(ControlPlaneStartupError) as excinfo:
        await assert_control_plane_ready(pool=pool)

    message = str(excinfo.value)
    assert "fail closed" in message
    assert absent_table in message
    assert "is absent" in message


@pytest.mark.asyncio
async def test_readiness_report_flags_missing_table_without_raising() -> None:
    pool = _MissingTablePool("supervisor_digest_audit")

    report = await control_plane_readiness_report(pool=pool)

    assert report.ready is False
    assert any("supervisor_digest_audit" in m for m in report.missing)


@pytest.mark.asyncio
async def test_schema_check_skipped_when_no_pool_supplied() -> None:
    report = await control_plane_readiness_report()

    assert report.schema_checked is False
    assert "schema_tables" not in report.checked
    # A purely-static check still passes on a healthy tree.
    assert report.ready is True


# ── fail-closed: a missing code component ───────────────────────────────────


@pytest.mark.asyncio
async def test_assert_control_plane_ready_fails_closed_when_dashboard_route_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Simulate a deployment where the dashboard /control-plane route handler is
    # not present — the guard must refuse startup, not silently degrade.
    monkeypatch.delattr(dashboard, "get_feature_control_plane", raising=True)

    with pytest.raises(ControlPlaneStartupError) as excinfo:
        await assert_control_plane_ready()

    message = str(excinfo.value)
    assert "dashboard_route" in message
    assert "get_feature_control_plane" in message


@pytest.mark.asyncio
async def test_assert_control_plane_ready_fails_closed_when_classifier_mapping_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An empty typed classifier mapping means the supervisor would fall through
    # to legacy labels for every class — a fail-closed startup error.
    monkeypatch.setattr(classifier_mapping, "MAPPING_ROWS", (), raising=True)

    with pytest.raises(ControlPlaneStartupError) as excinfo:
        await assert_control_plane_ready()

    assert "classifier_mapping" in str(excinfo.value)
    assert "MAPPING_ROWS is empty" in str(excinfo.value)


@pytest.mark.asyncio
async def test_assert_control_plane_ready_fails_closed_when_public_projection_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import iriai_build_v2.public_dashboard as public_dashboard

    monkeypatch.delattr(
        public_dashboard.PublicDashboardOutbox,
        "project_control_plane_snapshot_changed",
        raising=True,
    )

    with pytest.raises(ControlPlaneStartupError) as excinfo:
        await assert_control_plane_ready()

    message = str(excinfo.value)
    assert "public_outbox_projection" in message
    assert "project_control_plane_snapshot_changed" in message


# ── fail-closed: the 10c-1 read-only-wiring landing-seam check ──────────────


@pytest.mark.asyncio
async def test_read_only_check_fails_closed_for_unrestricted_artifact_sink() -> None:
    """STATUS.md 10c-1 landing-seam: a read-only ActionPolicy must hand the
    audit-scoped ReadOnlyAuditArtifactSink — an unrestricted artifact store is
    a denied write surface."""

    class _UnrestrictedStore:
        async def put(self, *_a: object, **_k: object) -> None: ...

    policy = ActionPolicy(
        mode=SupervisorMode.READ_ONLY,
        artifact_sink=_UnrestrictedStore(),  # NOT a ReadOnlyAuditArtifactSink
    )

    with pytest.raises(ControlPlaneStartupError) as excinfo:
        await assert_control_plane_ready(action_policy=policy)

    message = str(excinfo.value)
    assert "read_only_action_policy" in message
    assert "ReadOnlyAuditArtifactSink" in message


@pytest.mark.asyncio
async def test_read_only_check_passes_for_audit_scoped_sink() -> None:
    policy = ActionPolicy(
        mode=SupervisorMode.READ_ONLY,
        artifact_sink=ReadOnlyAuditArtifactSink(artifact_store=object()),
    )

    report = await assert_control_plane_ready(action_policy=policy)

    assert report.ready is True
    assert "read_only_action_policy" in report.checked


@pytest.mark.asyncio
async def test_read_only_check_passes_when_no_artifact_sink_configured() -> None:
    # An ActionPolicy with no artifact write surface at all is trivially
    # read-only-safe — there is no write path to mis-wire.
    policy = ActionPolicy(mode=SupervisorMode.READ_ONLY)

    report = await assert_control_plane_ready(action_policy=policy)

    assert report.ready is True


@pytest.mark.asyncio
async def test_read_only_policy_check_skipped_when_no_policy_supplied() -> None:
    report = await control_plane_readiness_report()

    assert "read_only_action_policy" not in report.checked


# ── fail-closed: no supervisor feature-timeline writer ──────────────────────


@pytest.mark.asyncio
async def test_supervisor_feature_timeline_writer_scan_passes_on_clean_tree() -> None:
    """The current supervisor/ tree calls no FeatureStore timeline mutator —
    the re-confirm scan passes."""

    report = await control_plane_readiness_report()

    assert report.ready is True
    assert not any(
        "supervisor_feature_timeline_writer" in m for m in report.missing
    )


@pytest.mark.asyncio
async def test_supervisor_feature_timeline_writer_scan_fails_closed_on_regression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If a supervisor module ever calls a FeatureStore timeline mutator, the
    guard must flag it. Simulate the regression by widening the scanned token
    set to a method the supervisor source legitimately uses elsewhere."""

    # `classify` is called inside supervisor/ (e.g. SupervisorClassifier);
    # widening the forbidden set to it proves the scanner detects a token.
    monkeypatch.setattr(
        startup,
        "_SUPERVISOR_FORBIDDEN_FEATURE_TIMELINE_WRITERS",
        ("transition_phase", "classify"),
        raising=True,
    )

    report = await control_plane_readiness_report()

    assert report.ready is False
    offender_lines = [
        m for m in report.missing if "supervisor_feature_timeline_writer" in m
    ]
    assert offender_lines, report.missing
    assert "classify" in offender_lines[0]


# ─── Slice 12c — env-flag helper ────────────────────────────────────────────


def test_env_flag_constant_pinned() -> None:
    """The product-authoritative env-flag name is constant-pinned per doc 12
    § "Atomic Landing Contract"."""

    assert IRIAI_EXEC_CONTROL_PLANE_ENABLED_ENV == "IRIAI_EXEC_CONTROL_PLANE_ENABLED"


def test_env_flag_state_is_enabled_only_for_enabled() -> None:
    """Both ``DISABLED`` and ``UNSET`` are NOT enabled — the env flag defaults
    to opt-in per the doc 12 / prompt hard rule."""

    assert EnvFlagState.ENABLED.is_enabled is True
    assert EnvFlagState.DISABLED.is_enabled is False
    assert EnvFlagState.UNSET.is_enabled is False


@pytest.mark.parametrize(
    "raw_value",
    ["1", "true", "TRUE", "True", "yes", "Yes", "on", "ON", "enabled", "  true  "],
)
def test_read_env_flag_recognized_true_values(raw_value: str) -> None:
    """All five recognized true values plus whitespace + case variants resolve
    to ``ENABLED``."""

    state = read_control_plane_env_flag(
        env={IRIAI_EXEC_CONTROL_PLANE_ENABLED_ENV: raw_value}
    )
    assert state is EnvFlagState.ENABLED


@pytest.mark.parametrize(
    "raw_value",
    ["0", "false", "FALSE", "no", "off", "OFF", "disabled", "Disabled", "  false  "],
)
def test_read_env_flag_recognized_false_values(raw_value: str) -> None:
    """All five recognized false values plus whitespace + case variants resolve
    to ``DISABLED``."""

    state = read_control_plane_env_flag(
        env={IRIAI_EXEC_CONTROL_PLANE_ENABLED_ENV: raw_value}
    )
    assert state is EnvFlagState.DISABLED


def test_read_env_flag_unset_returns_unset_not_disabled() -> None:
    """A missing env-flag returns ``UNSET``, not ``DISABLED`` (so callers can
    distinguish "explicitly disabled" from "never set")."""

    state = read_control_plane_env_flag(env={})
    assert state is EnvFlagState.UNSET


def test_read_env_flag_empty_string_returns_unset() -> None:
    """An empty-string env-flag resolves to ``UNSET`` (the opt-in default)."""

    state = read_control_plane_env_flag(
        env={IRIAI_EXEC_CONTROL_PLANE_ENABLED_ENV: ""}
    )
    assert state is EnvFlagState.UNSET


@pytest.mark.parametrize(
    "malformed_value",
    ["2", "maybe", "on/off", "true,false", "ENABLED!", "trueish", "y", "n"],
)
def test_read_env_flag_malformed_value_raises_fail_closed(
    malformed_value: str,
) -> None:
    """Any unrecognized env-flag value MUST raise (NOT silently default to
    enabled or disabled). Per prompt hard rule + doc 12 § "Atomic Landing
    Contract", fail closed."""

    with pytest.raises(ControlPlaneEnvFlagError) as excinfo:
        read_control_plane_env_flag(
            env={IRIAI_EXEC_CONTROL_PLANE_ENABLED_ENV: malformed_value}
        )

    message = str(excinfo.value)
    assert "IRIAI_EXEC_CONTROL_PLANE_ENABLED" in message
    assert "fail closed" in message
    # Recognized values are surfaced in the error so the operator can fix it.
    assert "true" in message and "false" in message


def test_read_env_flag_defaults_to_os_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without an explicit ``env`` mapping, the helper reads ``os.environ``."""

    monkeypatch.setenv(IRIAI_EXEC_CONTROL_PLANE_ENABLED_ENV, "true")
    assert read_control_plane_env_flag() is EnvFlagState.ENABLED

    monkeypatch.setenv(IRIAI_EXEC_CONTROL_PLANE_ENABLED_ENV, "false")
    assert read_control_plane_env_flag() is EnvFlagState.DISABLED

    monkeypatch.delenv(IRIAI_EXEC_CONTROL_PLANE_ENABLED_ENV, raising=False)
    assert read_control_plane_env_flag() is EnvFlagState.UNSET


# ─── Slice 12c — workflow-launch guard fail-closed scenarios ────────────────


@pytest.mark.asyncio
async def test_workflow_launch_guard_pass_path_when_flag_enabled() -> None:
    """The PASS path: env flag enabled, all base readiness checks pass, no
    landing-context blockers — the guard returns a ready report."""

    pool = _AllTablesPresentPool()
    report = await assert_control_plane_ready_for_workflow_launch(
        pool=pool,
        env={IRIAI_EXEC_CONTROL_PLANE_ENABLED_ENV: "true"},
        require_enabled=True,
    )

    assert isinstance(report, WorkflowLaunchReadinessReport)
    assert report.env_flag_state is EnvFlagState.ENABLED
    assert report.base_report is not None
    assert report.base_report.ready is True
    assert report.landing_blockers == []
    assert report.ready is True


@pytest.mark.asyncio
async def test_workflow_launch_guard_refuses_when_flag_unset_and_required() -> None:
    """Fail-closed scenario 3: env flag UNSET + ``require_enabled=True`` →
    refuse (NOT silent fallback to legacy).

    Caller asked for the control plane; the env flag is the SINGLE
    product-authoritative switch and it is not enabled — refuse to start."""

    with pytest.raises(ControlPlaneStartupError) as excinfo:
        await assert_control_plane_ready_for_workflow_launch(
            env={},  # IRIAI_EXEC_CONTROL_PLANE_ENABLED not set
            require_enabled=True,
        )

    message = str(excinfo.value)
    assert "IRIAI_EXEC_CONTROL_PLANE_ENABLED" in message
    assert "fail closed" in message
    assert "unset" in message


@pytest.mark.asyncio
async def test_workflow_launch_guard_refuses_when_flag_disabled_and_required() -> None:
    """Fail-closed scenario 3 (variant): env flag explicitly DISABLED +
    ``require_enabled=True`` → refuse."""

    with pytest.raises(ControlPlaneStartupError) as excinfo:
        await assert_control_plane_ready_for_workflow_launch(
            env={IRIAI_EXEC_CONTROL_PLANE_ENABLED_ENV: "false"},
            require_enabled=True,
        )

    message = str(excinfo.value)
    assert "IRIAI_EXEC_CONTROL_PLANE_ENABLED" in message
    assert "disabled" in message


@pytest.mark.asyncio
async def test_workflow_launch_guard_legacy_path_when_flag_unset_and_not_required() -> None:
    """When ``require_enabled=False`` and the env flag is unset/disabled,
    the guard returns a not-ready report WITHOUT raising — the CLI uses this
    to continue on the legacy executor (backward-compat during rollout)."""

    report = await assert_control_plane_ready_for_workflow_launch(
        env={},
        require_enabled=False,
    )

    assert report.env_flag_state is EnvFlagState.UNSET
    assert report.ready is False
    # Base readiness was not evaluated (env flag is the outer gate).
    assert report.base_report is None


@pytest.mark.asyncio
async def test_workflow_launch_guard_propagates_malformed_env_flag_error() -> None:
    """A malformed env-flag value re-raises ``ControlPlaneEnvFlagError`` from
    the underlying helper — the workflow-launch guard does NOT swallow it."""

    with pytest.raises(ControlPlaneEnvFlagError):
        await assert_control_plane_ready_for_workflow_launch(
            env={IRIAI_EXEC_CONTROL_PLANE_ENABLED_ENV: "maybe"},
            require_enabled=True,
        )


@pytest.mark.asyncio
async def test_workflow_launch_guard_refuses_on_deploy_artifact_mismatch() -> None:
    """Fail-closed scenario 1: deploy artifact does NOT match the go-approved
    candidate commit — refuse with typed reason (doc 12 § "Operational
    Go/No-Go": "deploy artifact MUST match candidate")."""

    pool = _AllTablesPresentPool()
    with pytest.raises(ControlPlaneStartupError) as excinfo:
        await assert_control_plane_ready_for_workflow_launch(
            pool=pool,
            env={IRIAI_EXEC_CONTROL_PLANE_ENABLED_ENV: "true"},
            expected_candidate_commit="abc123deadbeef",
            deploy_artifact_commit="999wrongcommit",
            require_enabled=True,
        )

    message = str(excinfo.value)
    assert "deploy_artifact" in message
    assert "abc123deadbeef" in message
    assert "999wrongcommit" in message
    assert "does not match" in message


@pytest.mark.asyncio
async def test_workflow_launch_guard_accepts_deploy_artifact_match() -> None:
    """Fail-closed scenario 1 — CLEAR: deploy artifact commit matches the
    candidate commit → guard accepts and returns ready."""

    pool = _AllTablesPresentPool()
    report = await assert_control_plane_ready_for_workflow_launch(
        pool=pool,
        env={IRIAI_EXEC_CONTROL_PLANE_ENABLED_ENV: "true"},
        expected_candidate_commit="abc123deadbeef",
        deploy_artifact_commit="abc123deadbeef",
        require_enabled=True,
    )

    assert report.ready is True
    assert report.landing_blockers == []


@pytest.mark.asyncio
async def test_workflow_launch_guard_refuses_on_missing_required_migration() -> None:
    """Fail-closed scenario 2: a required migration table is absent — refuse
    with typed reason (doc 12 § "Operational Go/No-Go": "Required
    migrations are present")."""

    # The pool reports the doc-10 base tables present (so the base readiness
    # check passes) but the required migration table is absent.
    pool = _MissingTablePool("future_migration_table")
    with pytest.raises(ControlPlaneStartupError) as excinfo:
        await assert_control_plane_ready_for_workflow_launch(
            pool=pool,
            env={IRIAI_EXEC_CONTROL_PLANE_ENABLED_ENV: "true"},
            required_migrations=("future_migration_table",),
            require_enabled=True,
        )

    message = str(excinfo.value)
    assert "required_migrations" in message
    assert "future_migration_table" in message
    assert "is absent" in message


@pytest.mark.asyncio
async def test_workflow_launch_guard_accepts_when_required_migrations_present() -> None:
    """Fail-closed scenario 2 — CLEAR: every required migration table exists
    → guard accepts."""

    pool = _AllTablesPresentPool()
    report = await assert_control_plane_ready_for_workflow_launch(
        pool=pool,
        env={IRIAI_EXEC_CONTROL_PLANE_ENABLED_ENV: "true"},
        required_migrations=("public_dashboard_outbox", "supervisor_digest_state"),
        require_enabled=True,
    )

    assert report.ready is True
    assert report.landing_blockers == []


@pytest.mark.asyncio
async def test_workflow_launch_guard_refuses_on_forbidden_partial_control() -> None:
    """Fail-closed scenario 4: a Slice-12b ``FORBIDDEN_PARTIAL_CONTROLS`` name
    is enabled for production authority — refuse with typed reason (doc 12
    § "Atomic Landing Contract": "the landing gate must assert that no
    per-slice control is being used as production authority")."""

    pool = _AllTablesPresentPool()
    with pytest.raises(ControlPlaneStartupError) as excinfo:
        await assert_control_plane_ready_for_workflow_launch(
            pool=pool,
            env={IRIAI_EXEC_CONTROL_PLANE_ENABLED_ENV: "true"},
            forbidden_partial_controls_enabled=("IRIAI_MERGE_QUEUE_V2",),
            require_enabled=True,
        )

    message = str(excinfo.value)
    assert "forbidden_partial_control" in message
    assert "IRIAI_MERGE_QUEUE_V2" in message
    assert "doc 12 forbids" in message


@pytest.mark.asyncio
async def test_workflow_launch_guard_accepts_when_no_forbidden_partial_controls() -> None:
    """Fail-closed scenario 4 — CLEAR: empty forbidden-partial-controls list
    → guard accepts."""

    pool = _AllTablesPresentPool()
    report = await assert_control_plane_ready_for_workflow_launch(
        pool=pool,
        env={IRIAI_EXEC_CONTROL_PLANE_ENABLED_ENV: "true"},
        forbidden_partial_controls_enabled=(),
        require_enabled=True,
    )

    assert report.ready is True
    assert report.landing_blockers == []


@pytest.mark.asyncio
async def test_workflow_launch_guard_aggregates_multiple_blockers() -> None:
    """All four conditions can stack — the report records every blocker so
    the operator gets a single error with the full picture."""

    pool = _AllTablesPresentPool()
    with pytest.raises(ControlPlaneStartupError) as excinfo:
        await assert_control_plane_ready_for_workflow_launch(
            pool=pool,
            env={IRIAI_EXEC_CONTROL_PLANE_ENABLED_ENV: "true"},
            expected_candidate_commit="abc123",
            deploy_artifact_commit="def456",  # mismatch
            forbidden_partial_controls_enabled=("IRIAI_FAILURE_ROUTER_V2",),  # forbidden
            require_enabled=True,
        )

    message = str(excinfo.value)
    assert "deploy_artifact" in message
    assert "forbidden_partial_control" in message


@pytest.mark.asyncio
async def test_workflow_launch_guard_refuses_when_base_readiness_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the Slice-10f base readiness check fails (e.g. dashboard route
    missing), the workflow-launch guard surfaces that failure too —
    fail-closed semantics extend through the base report."""

    monkeypatch.delattr(dashboard, "get_feature_control_plane", raising=True)

    pool = _AllTablesPresentPool()
    with pytest.raises(ControlPlaneStartupError) as excinfo:
        await assert_control_plane_ready_for_workflow_launch(
            pool=pool,
            env={IRIAI_EXEC_CONTROL_PLANE_ENABLED_ENV: "true"},
            require_enabled=True,
        )

    message = str(excinfo.value)
    assert "dashboard_route" in message


@pytest.mark.asyncio
async def test_workflow_launch_guard_with_atomic_landing_gate_result_integration() -> None:
    """Slice-12b ``AtomicLandingGateResult.forbidden_partial_controls_enabled``
    is the typed input the guard reads — round-trip through the Slice-12c
    workflow-launch guard mirrors the Slice-12b types verbatim."""

    from iriai_build_v2.execution_control.atomic_landing import (
        FORBIDDEN_PARTIAL_CONTROLS,
    )

    # Pick the first sample forbidden control from the Slice-12b constant
    # and feed it to the Slice-12c guard. The guard must reject.
    sample_forbidden_name = sorted(FORBIDDEN_PARTIAL_CONTROLS)[0]
    pool = _AllTablesPresentPool()

    with pytest.raises(ControlPlaneStartupError) as excinfo:
        await assert_control_plane_ready_for_workflow_launch(
            pool=pool,
            env={IRIAI_EXEC_CONTROL_PLANE_ENABLED_ENV: "true"},
            forbidden_partial_controls_enabled=(sample_forbidden_name,),
            require_enabled=True,
        )

    assert sample_forbidden_name in str(excinfo.value)
