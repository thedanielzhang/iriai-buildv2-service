"""Slice 10g-2 — real-Postgres startup/deployment guard tests.

doc 10 § "Tests" — Integration/regression: "Startup/deployment guard test
fails if typed mode is enabled without the dashboard route, MCP typed
snapshot path, classifier mapping, dedupe tables, public outbox projection,
or read-only policy enforcement."

The existing ``tests/test_execution_control_startup.py`` covers the static
checks + fake-pool schema checks (16 tests). This module is the real-Postgres
counterpart: exercises ``assert_control_plane_ready`` /
``control_plane_readiness_report`` end-to-end against a live Postgres pool
(the supervisor real-Postgres conftest's ``supervisor_pg_conn``) so the
doc-10 step-10 schema presence checks for ``supervisor_digest_state`` /
``supervisor_digest_audit`` / ``public_dashboard_outbox`` are exercised
against the actual loaded schema.

Each FAIL-CLOSED test asserts the error message names the missing component
so an operator can identify what to fix (doc 10 step 10 — fail closed,
NEVER silently degrade onto legacy artifact inference).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import asyncpg
import pytest

import dashboard
from iriai_build_v2.execution_control import startup
from iriai_build_v2.execution_control.startup import (
    ControlPlaneReadinessReport,
    ControlPlaneStartupError,
    assert_control_plane_ready,
    control_plane_readiness_report,
)
from iriai_build_v2.execution_control.store import ExecutionControlStore
from iriai_build_v2.public_dashboard import PublicDashboardOutbox
from iriai_build_v2.supervisor import classifier_mapping
from iriai_build_v2.supervisor.actions import ActionPolicy
from iriai_build_v2.supervisor.models import SupervisorMode
from iriai_build_v2.supervisor.read_only import ReadOnlyAuditArtifactSink


# ─────────────────────────────────────────────────────────────────────────────
# PASS path
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_assert_control_plane_ready_real_postgres_pass(
    supervisor_pg_conn: asyncpg.Connection,
) -> None:
    """The PASS path: all components present, all schema tables present.

    Constructs a real ``ExecutionControlStore`` + a real ``PublicDashboardOutbox``
    on the supervisor real-Postgres pool, then runs the doc-10 step-10 guard
    end-to-end. The readiness report reports ``ok`` and
    ``assert_control_plane_ready`` returns normally.

    Cites ``src/iriai_build_v2/execution_control/startup.py:350-422``: the
    guard inspects the six doc-10 components + the two STATUS.md additions +
    the schema-tables check when a live pool is supplied.
    """

    # Construct real ExecutionControlStore + PublicDashboardOutbox on the
    # live connection — proves the production wiring shape is satisfiable.
    outbox = PublicDashboardOutbox(
        pool=supervisor_pg_conn, outbox_enabled=True, display_jobs_enabled=False
    )
    store = ExecutionControlStore(
        supervisor_pg_conn, public_dashboard_outbox=outbox
    )
    # A correctly-wired read-only ActionPolicy (Slice 10c-1 landing-seam).
    policy = ActionPolicy(
        mode=SupervisorMode.READ_ONLY,
        artifact_sink=ReadOnlyAuditArtifactSink(artifact_store=object()),
    )

    report = await control_plane_readiness_report(
        pool=supervisor_pg_conn, action_policy=policy
    )

    assert isinstance(report, ControlPlaneReadinessReport)
    assert report.ready is True, report.missing
    assert report.missing == []
    assert report.schema_checked is True
    # The six doc-10 components + the two STATUS.md additions + the schema +
    # read-only landing seam checks are all in `checked`.
    for component in (
        "snapshot_store",
        "dashboard_route",
        "classifier_mapping",
        "mcp_snapshot_path",
        "slack_dedupe_store",
        "public_outbox_projection",
        "supervisor_feature_timeline_writer",
        "read_only_action_policy",
        "schema_tables",
    ):
        assert component in report.checked, f"missing {component} in {report.checked}"

    # `assert_control_plane_ready` returns normally (does not raise).
    same = await assert_control_plane_ready(
        pool=supervisor_pg_conn, action_policy=policy
    )
    assert same.ready is True

    # Sanity: the constructed store + outbox have the production wiring shape.
    # The store's optional outbox slot is configured (`store.py:2267-2283`).
    assert store._public_dashboard_outbox is outbox
    assert outbox.outbox_enabled is True


# ─────────────────────────────────────────────────────────────────────────────
# FAIL-CLOSED — schema (one test per Slack-dedupe / public-outbox table)
#
# Each test wraps the DROP TABLE in a transaction so it rolls back at the end —
# this keeps the per-function `supervisor_pg_conn` fixture compatible with
# subsequent tests (Postgres supports transactional DDL).
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "absent_table",
    [
        "supervisor_digest_state",
        "supervisor_digest_audit",
        "public_dashboard_outbox",
    ],
)
async def test_assert_control_plane_ready_real_postgres_fails_when_table_absent(
    supervisor_pg_conn: asyncpg.Connection, absent_table: str
) -> None:
    """Schema FAIL-CLOSED: each Slack-dedupe / public-outbox table is required.

    ``execution_control/startup.py:62-66`` names the three required typed-mode
    tables (``supervisor_digest_state``, ``supervisor_digest_audit``,
    ``public_dashboard_outbox``); ``_check_schema_tables`` (lines 321-347)
    fails closed when any one is absent (``to_regclass`` returns NULL).

    DROP TABLE is run inside an asyncpg transaction that is always rolled
    back so the schema is restored for subsequent tests.
    """

    tx = supervisor_pg_conn.transaction()
    await tx.start()
    try:
        # CASCADE handles the FK from execution_artifact_projections etc.
        await supervisor_pg_conn.execute(
            f"DROP TABLE IF EXISTS {absent_table} CASCADE"
        )

        # control_plane_readiness_report — non-raising verdict carries
        # `ready=False` + a name-bearing `missing` entry.
        report = await control_plane_readiness_report(pool=supervisor_pg_conn)
        assert report.ready is False
        assert any(absent_table in m for m in report.missing), report.missing

        # assert_control_plane_ready — raises ControlPlaneStartupError naming
        # the failed component so the operator can identify what to fix.
        with pytest.raises(ControlPlaneStartupError) as excinfo:
            await assert_control_plane_ready(pool=supervisor_pg_conn)
        message = str(excinfo.value)
        assert absent_table in message
    finally:
        # Roll back the DROP TABLE — restore the schema for sibling tests.
        await tx.rollback()


# ─────────────────────────────────────────────────────────────────────────────
# FAIL-CLOSED — a missing code component (one per doc-10 component)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_real_postgres_fails_closed_when_dashboard_route_missing(
    supervisor_pg_conn: asyncpg.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dashboard route FAIL-CLOSED: `dashboard.get_feature_control_plane` absent.

    Cites ``execution_control/startup.py:117-129``: the guard inspects
    ``dashboard.get_feature_control_plane`` (the typed /control-plane
    handler) and the bridge helper ``dashboard._typed_control_plane_snapshot``;
    a missing handler is a startup failure.
    """

    monkeypatch.delattr(dashboard, "get_feature_control_plane", raising=True)

    with pytest.raises(ControlPlaneStartupError) as excinfo:
        await assert_control_plane_ready(pool=supervisor_pg_conn)

    message = str(excinfo.value)
    assert "dashboard_route" in message
    assert "get_feature_control_plane" in message

    # control_plane_readiness_report is non-raising but reports `ready=False`.
    report = await control_plane_readiness_report(pool=supervisor_pg_conn)
    assert report.ready is False
    assert any("dashboard_route" in m for m in report.missing)


@pytest.mark.asyncio
async def test_real_postgres_fails_closed_when_classifier_mapping_incomplete(
    supervisor_pg_conn: asyncpg.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Classifier coverage FAIL-CLOSED: incomplete typed mapping.

    Cites ``execution_control/startup.py:132-164``: the guard calls
    ``classifier_mapping.coverage_report()`` and fails closed when
    ``coverage.ok`` is ``False`` (an unmapped or double-mapped FailureClass
    means the supervisor would fall through to legacy labels).

    Simulates incompleteness by clearing ``MAPPING_ROWS``, which makes both
    ``classify_typed_snapshot`` ineffective AND ``coverage_report().ok``
    False — the same approach the static
    ``tests/test_execution_control_startup.py`` test uses (16 tests above).
    """

    monkeypatch.setattr(classifier_mapping, "MAPPING_ROWS", (), raising=True)

    with pytest.raises(ControlPlaneStartupError) as excinfo:
        await assert_control_plane_ready(pool=supervisor_pg_conn)

    message = str(excinfo.value)
    assert "classifier_mapping" in message
    assert "MAPPING_ROWS is empty" in message

    report = await control_plane_readiness_report(pool=supervisor_pg_conn)
    assert report.ready is False
    assert any("classifier_mapping" in m for m in report.missing)


@pytest.mark.asyncio
async def test_real_postgres_fails_closed_when_mcp_snapshot_path_missing(
    supervisor_pg_conn: asyncpg.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MCP snapshot path FAIL-CLOSED: `get_current_snapshot` absent.

    Cites ``execution_control/startup.py:166-178``: the guard inspects
    ``SupervisorEvidenceMcpService.get_current_snapshot``; a missing method
    means the typed-mode MCP path is broken and supervisor would degrade
    onto legacy reads.
    """

    from iriai_build_v2.supervisor.mcp_server import SupervisorEvidenceMcpService

    monkeypatch.delattr(
        SupervisorEvidenceMcpService, "get_current_snapshot", raising=True
    )

    with pytest.raises(ControlPlaneStartupError) as excinfo:
        await assert_control_plane_ready(pool=supervisor_pg_conn)

    message = str(excinfo.value)
    assert "mcp_snapshot_path" in message
    assert "get_current_snapshot" in message


@pytest.mark.asyncio
async def test_real_postgres_fails_closed_when_slack_dedupe_store_broken(
    supervisor_pg_conn: asyncpg.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Slack dedupe store FAIL-CLOSED: a required method is absent.

    Cites ``execution_control/startup.py:181-193``: the guard inspects
    ``SupervisorDigestDedupeStore.decide`` / ``record_sent`` / ``record_suppressed``;
    a missing method means the typed-mode Slack dedupe routing is broken.
    """

    from iriai_build_v2.supervisor.digest_dedupe import SupervisorDigestDedupeStore

    monkeypatch.delattr(SupervisorDigestDedupeStore, "decide", raising=True)

    with pytest.raises(ControlPlaneStartupError) as excinfo:
        await assert_control_plane_ready(pool=supervisor_pg_conn)

    message = str(excinfo.value)
    assert "slack_dedupe_store" in message
    assert "decide" in message


@pytest.mark.asyncio
async def test_real_postgres_fails_closed_when_public_outbox_projection_missing(
    supervisor_pg_conn: asyncpg.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Public outbox projection FAIL-CLOSED: the projection method is absent.

    Cites ``execution_control/startup.py:196-218``: the guard inspects
    ``PublicDashboardOutbox.project_control_plane_snapshot_changed`` AND
    ``project_control_plane_snapshot_if_changed`` (the helper that fires it).
    A missing method means the typed-mode public-outbox projection is
    broken — fail closed.
    """

    import iriai_build_v2.public_dashboard as public_dashboard

    monkeypatch.delattr(
        public_dashboard.PublicDashboardOutbox,
        "project_control_plane_snapshot_changed",
        raising=True,
    )

    with pytest.raises(ControlPlaneStartupError) as excinfo:
        await assert_control_plane_ready(pool=supervisor_pg_conn)

    message = str(excinfo.value)
    assert "public_outbox_projection" in message
    assert "project_control_plane_snapshot_changed" in message


# ─────────────────────────────────────────────────────────────────────────────
# FAIL-CLOSED — the 10c-1 read-only-wiring landing-seam check
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_real_postgres_fails_closed_for_non_audit_scoped_artifact_sink(
    supervisor_pg_conn: asyncpg.Connection,
) -> None:
    """The 10c-1 landing-seam FAIL-CLOSED: a read-only ActionPolicy must use
    the ``ReadOnlyAuditArtifactSink``.

    Cites ``execution_control/startup.py:221-258``: the guard re-confirms the
    Slice-10c-1 contract — an ``ActionPolicy`` whose ``artifact_sink`` is NOT
    a ``ReadOnlyAuditArtifactSink`` is a denied write surface (the production
    read-only wiring must hand the audit-scoped sink to ``ActionPolicy``).
    """

    class _UnrestrictedStore:
        async def put(self, *_a: object, **_k: object) -> None:
            return None

    policy = ActionPolicy(
        mode=SupervisorMode.READ_ONLY,
        artifact_sink=_UnrestrictedStore(),  # NOT a ReadOnlyAuditArtifactSink
    )

    with pytest.raises(ControlPlaneStartupError) as excinfo:
        await assert_control_plane_ready(
            pool=supervisor_pg_conn, action_policy=policy
        )

    message = str(excinfo.value)
    assert "read_only_action_policy" in message
    assert "ReadOnlyAuditArtifactSink" in message


# ─────────────────────────────────────────────────────────────────────────────
# FAIL-CLOSED — the no-supervisor-feature-timeline-writer scan
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_real_postgres_fails_closed_when_supervisor_module_writes_feature_timeline(
    supervisor_pg_conn: asyncpg.Connection,
    tmp_path: Path,
) -> None:
    """Feature-timeline-writer scan FAIL-CLOSED: a regression file added under
    ``src/iriai_build_v2/supervisor/`` containing ``.transition_phase(`` text.

    Cites ``execution_control/startup.py:275-318``: the guard scans every
    ``supervisor/*.py`` for an unambiguous FeatureStore timeline mutator
    (``.transition_phase(`` / ``.update_metadata(`` / ``.log_event(``). doc 10
    § "Read-Only And Audit Exception Policy" forbids supervisor
    feature-timeline writes.

    Simulates the regression by writing a fake file under the live
    ``supervisor/`` directory and cleaning it up at teardown — this exercises
    the real scanner against a real path (rather than monkeypatching the
    forbidden token set as the static unit-tests do).
    """

    import iriai_build_v2.supervisor as supervisor_pkg

    supervisor_dir = Path(supervisor_pkg.__file__).resolve().parent
    fake_file = supervisor_dir / f"_test_regression_{id(tmp_path)}.py"
    # A REGRESSION caller — a fake module that imports a feature store and
    # calls `feature_store.transition_phase(...)`. The scan is textual; this
    # text alone is enough to trigger the guard.
    fake_file.write_text(
        "# slice 10g-2 test regression simulation — clean up at teardown.\n"
        "from typing import Any\n"
        "\n"
        "\n"
        "async def _regression_caller(feature_store: Any) -> None:\n"
        "    # A forbidden FeatureStore timeline mutator under supervisor/.\n"
        "    await feature_store.transition_phase('feat-x', 'pm')\n",
        encoding="utf-8",
    )
    try:
        report = await control_plane_readiness_report(pool=supervisor_pg_conn)
        assert report.ready is False
        offender_lines = [
            m
            for m in report.missing
            if "supervisor_feature_timeline_writer" in m
        ]
        assert offender_lines, report.missing
        # The offender is named by file:method so the operator can fix it.
        assert "transition_phase" in offender_lines[0]
        assert fake_file.name in offender_lines[0]

        # assert_control_plane_ready raises and names the failed component.
        with pytest.raises(ControlPlaneStartupError) as excinfo:
            await assert_control_plane_ready(pool=supervisor_pg_conn)
        message = str(excinfo.value)
        assert "supervisor_feature_timeline_writer" in message
        assert "transition_phase" in message
    finally:
        fake_file.unlink(missing_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# Sanity: the supervisor_pg_conn fixture restores cleanly after schema drops.
# (The transactional-DDL rollback in the schema fail-closed tests above relies
# on this — the merge-queue-style fixture truncates between tests but does
# not reload the schema, so transactional rollback IS the safety net.)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_real_postgres_schema_check_passes_after_schema_drop_rollback(
    supervisor_pg_conn: asyncpg.Connection,
) -> None:
    """A separate test confirms the schema-drop tests roll back cleanly.

    If the transactional-DDL rollback in
    `test_assert_control_plane_ready_real_postgres_fails_when_table_absent`
    leaked a missing table across tests, this test (run after the parametrized
    schema-drop tests by `-p no:randomly`) would fail. Its passing is the
    proof that the parametrized test is hygienic — defence in depth so a
    future maintainer accidentally landing a non-transactional schema drop
    gets a CI signal instead of a silent regression.
    """

    report = await control_plane_readiness_report(pool=supervisor_pg_conn)
    assert report.ready is True, report.missing
    assert report.schema_checked is True
