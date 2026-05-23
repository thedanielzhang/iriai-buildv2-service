"""Slice 10f (doc 10 step 10) — typed control-plane startup/deployment guard.

doc 10 § "Refactoring Steps" step 10:

    Add deployment/startup assertions that the snapshot store, dashboard route,
    classifier mapping, MCP snapshot path, Slack dedupe store, and public outbox
    projection are all present when typed control-plane mode is enabled.

doc 10 § "Rollout/Rollback Notes": "production typed mode requires the startup
assertions above to pass before serving dashboard, MCP, supervisor, or Slack
traffic." This module is that guard — :func:`assert_control_plane_ready`. It is
the MECHANISM; Slice 12c wires it at the real process entrypoint behind
``IRIAI_EXEC_CONTROL_PLANE_ENABLED`` via :func:`read_control_plane_env_flag` +
:func:`assert_control_plane_ready_for_workflow_launch` (see the "Slice 12c —
env-flag + workflow-launch guard" section below).

FAIL CLOSED: every check raises :class:`ControlPlaneStartupError` with a clear,
named message when a dependency is missing. doc 10's step-10 intent is a hard
startup failure — NEVER a silent degrade onto legacy artifact inference. A
caller that wants the readiness verdict without raising can use
:func:`control_plane_readiness_report`.

The guard checks the six doc-10 components plus the two STATUS.md
"Next safe action" additions:

1. the typed snapshot store (``ExecutionControlStore.get_control_plane_snapshot``
   / ``get_control_plane_snapshot_version``);
2. the dashboard ``/api/feature/{feature_id}/control-plane`` route;
3. the typed supervisor classifier mapping (``MAPPING_ROWS`` +
   ``classify_typed_snapshot``; the doc-10 coverage rule still holds);
4. the MCP typed-snapshot path (``SupervisorEvidenceMcpService.get_current_snapshot``);
5. the Slack dedupe store + its two supervisor-owned tables;
6. the public-dashboard ``control_plane.snapshot_changed`` outbox projection;
7. (STATUS.md) the production read-only wiring hands ``ReadOnlyAuditArtifactSink``
   to ``ActionPolicy`` — resolves the carried 10c-1 landing-seam check;
8. (STATUS.md) no new feature-timeline writer caller appears under ``supervisor/``.

Components 1-4, 6, 7 and 8 are static (import + symbol + source) checks. The
schema-presence checks for the Slack dedupe tables and the public outbox table
run only when a live ``pool`` is supplied — a startup caller that has a pool
gets the full guarantee; a static check still proves the code path exists.

Slice 12c — env-flag + workflow-launch guard
---------------------------------------------

Per doc 12 § "Atomic Landing Contract" + § "Internal Build Controls And Hard
Gates", ``IRIAI_EXEC_CONTROL_PLANE_ENABLED`` is the **single
product-authoritative switch** for the typed control plane. Slice 12c adds
three additions that wire this module at the real CLI/Slack-bridge entrypoint:

* :class:`EnvFlagState` — typed verdict for the env-flag read
  (``enabled`` / ``disabled`` / ``unset``). Malformed values raise
  :class:`ControlPlaneEnvFlagError` (fail closed; never silently default).
* :func:`read_control_plane_env_flag` — the env-flag helper. Default behavior
  on unset is ``disabled`` (opt-in for safety per the prompt hard rule).
* :func:`assert_control_plane_ready_for_workflow_launch` — the workflow-launch
  guard that fires **before** any workflow runs when the env flag is enabled.
  It refuses control-plane starts when:

  1. the deploy artifact does not match the go-approved candidate commit,
  2. required migrations are missing,
  3. the global switch (env flag) is disabled while a control-plane launch is
     attempted explicitly (``require_enabled=True``),
  4. any forbidden partial control (per Slice-12b's
     ``FORBIDDEN_PARTIAL_CONTROLS``) is enabled for production authority.

The Slice-12b :class:`~iriai_build_v2.execution_control.atomic_landing.AtomicLandingGateResult`
typed contract supplies the optional ``candidate_commit`` / ``deploy_artifact_id``
/ ``forbidden_partial_controls_enabled`` inputs; the PASS-path
:class:`ControlPlaneReadinessReport` is the typed output the Slice-12d adoption
record and Slice-12e final-landing chain consume.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping


class ControlPlaneStartupError(RuntimeError):
    """Raised when a typed-control-plane dependency is missing at startup.

    doc 10 step 10 is a fail-closed guard: a missing dependency is a hard
    startup failure, never a silent fallback onto legacy artifact inference.
    """


# The two supervisor-owned Slack dedupe tables (doc 10 § "Slack Dedupe And
# Suppression") and the public-dashboard outbox table (doc 10 § "Current Code
# Citations") the guard verifies when a live pool is supplied.
_SLACK_DEDUPE_TABLES: tuple[str, ...] = (
    "supervisor_digest_state",
    "supervisor_digest_audit",
)
_PUBLIC_OUTBOX_TABLE = "public_dashboard_outbox"


@dataclass
class ControlPlaneReadinessReport:
    """A structured readiness verdict for the typed control plane.

    ``ready`` is ``True`` only when ``missing`` is empty. ``checked`` lists
    every component the guard inspected (so a caller can see schema checks were
    skipped when no pool was supplied).
    """

    ready: bool
    checked: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    schema_checked: bool = False

    def raise_if_not_ready(self) -> None:
        if not self.ready:
            raise ControlPlaneStartupError(
                "typed control-plane mode is enabled but required dependencies "
                "are missing — refusing to serve dashboard/MCP/supervisor/Slack "
                "traffic (doc 10 step 10, fail closed). Missing: "
                + "; ".join(self.missing)
            )


def _missing_symbol(module: Any, name: str) -> bool:
    """Return ``True`` when ``module`` lacks a usable ``name`` attribute."""

    return not hasattr(module, name) or getattr(module, name) is None


def _check_snapshot_store(missing: list[str]) -> None:
    """doc 10 step 10 — the typed snapshot store is present."""

    try:
        from .store import ExecutionControlStore
    except Exception as exc:  # noqa: BLE001 — import failure IS the missing dep
        missing.append(f"snapshot_store: import failed ({exc!r})")
        return
    for method in (
        "get_control_plane_snapshot",
        "get_control_plane_snapshot_version",
    ):
        if not callable(getattr(ExecutionControlStore, method, None)):
            missing.append(
                f"snapshot_store: ExecutionControlStore.{method} is absent"
            )


def _check_dashboard_route(missing: list[str]) -> None:
    """doc 10 step 10 — the dashboard /control-plane route is present."""

    try:
        import dashboard  # the top-level FastAPI dashboard module
    except Exception as exc:  # noqa: BLE001
        missing.append(f"dashboard_route: import failed ({exc!r})")
        return
    # The /api/feature/{feature_id}/control-plane handler + the typed snapshot
    # bridge helper that backs both it and the embedded `control_plane` object.
    for name in ("get_feature_control_plane", "_typed_control_plane_snapshot"):
        if _missing_symbol(dashboard, name):
            missing.append(f"dashboard_route: dashboard.{name} is absent")


def _check_classifier_mapping(missing: list[str]) -> None:
    """doc 10 step 10 — the typed classifier mapping is present + complete."""

    try:
        from ..supervisor import classifier_mapping
    except Exception as exc:  # noqa: BLE001
        missing.append(f"classifier_mapping: import failed ({exc!r})")
        return
    for name in ("MAPPING_ROWS", "CLASSIFIER_PRIORITY", "classify_typed_snapshot"):
        if _missing_symbol(classifier_mapping, name):
            missing.append(f"classifier_mapping: {name} is absent")
            return
    if not classifier_mapping.MAPPING_ROWS:
        missing.append("classifier_mapping: MAPPING_ROWS is empty")
        return
    # doc 10 § "Supervisor Classifier Mapping" coverage rule: every canonical
    # FailureClass must map to exactly one row. A miss here means the typed
    # mapping is incomplete — fail closed rather than serve a classifier that
    # would fall through to a legacy label for an unmapped/double-mapped class.
    report = getattr(classifier_mapping, "coverage_report", None)
    if callable(report):
        try:
            coverage = report()
        except Exception as exc:  # noqa: BLE001
            missing.append(f"classifier_mapping: coverage_report raised ({exc!r})")
            return
        if not getattr(coverage, "ok", False):
            missing.append(
                "classifier_mapping: FailureClass coverage is incomplete "
                f"(unmapped={getattr(coverage, 'unmapped_classes', None)!r}, "
                f"double_mapped={getattr(coverage, 'double_mapped', None)!r})"
            )


def _check_mcp_snapshot_path(missing: list[str]) -> None:
    """doc 10 step 10 — the MCP typed-snapshot path is present."""

    try:
        from ..supervisor.mcp_server import SupervisorEvidenceMcpService
    except Exception as exc:  # noqa: BLE001
        missing.append(f"mcp_snapshot_path: import failed ({exc!r})")
        return
    if not callable(getattr(SupervisorEvidenceMcpService, "get_current_snapshot", None)):
        missing.append(
            "mcp_snapshot_path: SupervisorEvidenceMcpService.get_current_snapshot "
            "is absent"
        )


def _check_slack_dedupe_store(missing: list[str]) -> None:
    """doc 10 step 10 — the Slack dedupe store is present."""

    try:
        from ..supervisor.digest_dedupe import SupervisorDigestDedupeStore
    except Exception as exc:  # noqa: BLE001
        missing.append(f"slack_dedupe_store: import failed ({exc!r})")
        return
    for method in ("decide", "record_sent", "record_suppressed"):
        if not callable(getattr(SupervisorDigestDedupeStore, method, None)):
            missing.append(
                f"slack_dedupe_store: SupervisorDigestDedupeStore.{method} is absent"
            )


def _check_public_outbox_projection(missing: list[str]) -> None:
    """doc 10 step 10 — the public outbox projection is present."""

    try:
        from ..public_dashboard import (
            PublicDashboardOutbox,
            project_control_plane_snapshot_if_changed,
        )
    except Exception as exc:  # noqa: BLE001
        missing.append(f"public_outbox_projection: import failed ({exc!r})")
        return
    if not callable(
        getattr(PublicDashboardOutbox, "project_control_plane_snapshot_changed", None)
    ):
        missing.append(
            "public_outbox_projection: "
            "PublicDashboardOutbox.project_control_plane_snapshot_changed is absent"
        )
    if not callable(project_control_plane_snapshot_if_changed):
        missing.append(
            "public_outbox_projection: project_control_plane_snapshot_if_changed "
            "is absent"
        )


def _check_read_only_action_policy(
    action_policy: Any,
    missing: list[str],
) -> None:
    """STATUS.md 10c-1 landing-seam check — the production read-only wiring
    hands ``ReadOnlyAuditArtifactSink`` to ``ActionPolicy``.

    Only runs when a constructed ``ActionPolicy`` is supplied. doc 10 §
    "Read-Only And Audit Exception Policy": a read-only ``ActionPolicy`` holds
    NO execution-authority writer and its artifact write surface is the
    audit-scoped ``ReadOnlyAuditArtifactSink``.
    """

    if action_policy is None:
        return
    try:
        from ..supervisor.read_only import ReadOnlyAuditArtifactSink
    except Exception as exc:  # noqa: BLE001
        missing.append(f"read_only_action_policy: import failed ({exc!r})")
        return
    # An execution-authority writer must never be held (ActionPolicy.__init__
    # already asserts this; re-confirm at startup as a defence-in-depth seam).
    if getattr(action_policy, "execution_authority", None) is not None:
        missing.append(
            "read_only_action_policy: ActionPolicy holds a non-None "
            "execution_authority writer (must be absent in read-only mode)"
        )
    # When the policy has an artifact write surface it MUST be the audit-scoped
    # ReadOnlyAuditArtifactSink — an unrestricted artifact store is a denied
    # write surface for a read-only supervisor.
    sink = getattr(action_policy, "artifact_sink", None)
    if sink is not None and not isinstance(sink, ReadOnlyAuditArtifactSink):
        missing.append(
            "read_only_action_policy: ActionPolicy.artifact_sink is "
            f"{type(sink).__name__}, not ReadOnlyAuditArtifactSink — the "
            "production read-only wiring must hand the audit-scoped sink to "
            "ActionPolicy (doc 10 § 'Read-Only And Audit Exception Policy')"
        )


# The FeatureStore feature-timeline / phase-authority mutators a read-only
# supervisor must NEVER call. These are the UNAMBIGUOUS subset of
# `read_only.FEATURE_TIMELINE_WRITER_METHODS`: a `.transition_phase(` /
# `.update_metadata(` / `.log_event(` token under `supervisor/` is a genuine
# feature-timeline write — there is no sanctioned supervisor use.
#
# The ArtifactStore writers in `FEATURE_TIMELINE_WRITER_METHODS` — `put` /
# `write_artifact_bytes` / `delete` — are deliberately EXCLUDED: `put` is the
# dual-use audit write surface (doc 10's "Allowed writes" — supervisor-owned
# audit/dedupe/outbox records), call-site-enforced through
# `ReadOnlyAuditArtifactSink`, so a `.put(` token alone cannot distinguish a
# sanctioned audit write from a forbidden one. `create` is excluded as too
# generic a token (it collides with unrelated `*.create(` factory calls). The
# `read_only_action_policy` check above covers the audit-sink wiring instead.
_SUPERVISOR_FORBIDDEN_FEATURE_TIMELINE_WRITERS: tuple[str, ...] = (
    "transition_phase",
    "update_metadata",
    "log_event",
)


def _check_no_supervisor_feature_timeline_writer(missing: list[str]) -> None:
    """STATUS.md re-confirm — no new feature-timeline writer caller appears
    under ``supervisor/``.

    doc 10 § "Read-Only And Audit Exception Policy": a read-only supervisor may
    not perform feature-timeline writes (no attempt-state transitions, no phase
    authority). This statically scans every ``supervisor/*.py`` source file for
    a call to an unambiguous FeatureStore timeline mutator
    (:data:`_SUPERVISOR_FORBIDDEN_FEATURE_TIMELINE_WRITERS`). It is a
    conservative textual guard — the supervisor source tree is small and
    known-clean today; this assertion makes a future regression a startup
    failure.
    """

    supervisor_dir = Path(__file__).resolve().parent.parent / "supervisor"
    if not supervisor_dir.is_dir():
        missing.append(
            "supervisor_feature_timeline_writer: supervisor/ package directory "
            f"not found at {supervisor_dir}"
        )
        return
    offenders: list[str] = []
    for source in sorted(supervisor_dir.glob("*.py")):
        try:
            text = source.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001 — unreadable file is its own signal
            offenders.append(f"{source.name} (unreadable)")
            continue
        for method in _SUPERVISOR_FORBIDDEN_FEATURE_TIMELINE_WRITERS:
            if f".{method}(" in text:
                offenders.append(f"{source.name}:{method}")
    if offenders:
        missing.append(
            "supervisor_feature_timeline_writer: a feature-timeline writer call "
            "appears under supervisor/ (doc 10 forbids supervisor feature-"
            f"timeline writes): {sorted(offenders)!r}"
        )


async def _check_schema_tables(pool: Any, missing: list[str]) -> None:
    """Verify the Slack dedupe tables + the public outbox table exist.

    ``to_regclass('public.<table>')`` returns ``NULL`` when the table is
    absent (the established repo pattern, see ``dag_regroup.py``). doc 10 step
    10 names the Slack dedupe tables and the public outbox projection as
    required when typed mode is enabled.
    """

    fetchval = getattr(pool, "fetchval", None)
    if not callable(fetchval):
        missing.append(
            "schema: supplied pool has no fetchval — cannot verify typed "
            "control-plane tables"
        )
        return
    for table in (*_SLACK_DEDUPE_TABLES, _PUBLIC_OUTBOX_TABLE):
        try:
            present = await fetchval(f"SELECT to_regclass('public.{table}')")
        except Exception as exc:  # noqa: BLE001
            missing.append(f"schema: table check for {table!r} failed ({exc!r})")
            continue
        if present is None:
            missing.append(
                f"schema: required table {table!r} is absent (run ensure_schema "
                "before enabling typed control-plane mode)"
            )


async def control_plane_readiness_report(
    *,
    pool: Any | None = None,
    action_policy: Any | None = None,
) -> ControlPlaneReadinessReport:
    """Return a structured readiness verdict without raising.

    Use this when a caller wants to inspect the verdict (e.g. a health
    endpoint). For a fail-closed startup guard use
    :func:`assert_control_plane_ready`.

    ``pool`` enables the schema-presence checks for the Slack dedupe tables and
    the public outbox table. ``action_policy``, when supplied, enables the
    10c-1 landing-seam read-only-wiring check.
    """

    missing: list[str] = []
    checked: list[str] = []

    static_checks = (
        ("snapshot_store", _check_snapshot_store),
        ("dashboard_route", _check_dashboard_route),
        ("classifier_mapping", _check_classifier_mapping),
        ("mcp_snapshot_path", _check_mcp_snapshot_path),
        ("slack_dedupe_store", _check_slack_dedupe_store),
        ("public_outbox_projection", _check_public_outbox_projection),
        ("supervisor_feature_timeline_writer",
         _check_no_supervisor_feature_timeline_writer),
    )
    for name, check in static_checks:
        check(missing)
        checked.append(name)

    if action_policy is not None:
        _check_read_only_action_policy(action_policy, missing)
        checked.append("read_only_action_policy")

    schema_checked = False
    if pool is not None:
        await _check_schema_tables(pool, missing)
        checked.append("schema_tables")
        schema_checked = True

    return ControlPlaneReadinessReport(
        ready=not missing,
        checked=checked,
        missing=missing,
        schema_checked=schema_checked,
    )


async def assert_control_plane_ready(
    *,
    pool: Any | None = None,
    action_policy: Any | None = None,
) -> ControlPlaneReadinessReport:
    """Fail closed unless every typed-control-plane dependency is present.

    doc 10 step 10. Call this at the process entrypoint when typed
    control-plane mode is enabled, BEFORE serving dashboard/MCP/supervisor/Slack
    traffic. Raises :class:`ControlPlaneStartupError` (a hard startup failure)
    naming every missing dependency; returns the report on success.

    ``pool`` enables the DB schema-presence checks; ``action_policy`` enables
    the 10c-1 landing-seam read-only-wiring check. Both are optional so a
    purely-static deployment check still runs.
    """

    report = await control_plane_readiness_report(
        pool=pool, action_policy=action_policy
    )
    report.raise_if_not_ready()
    return report


# ─── Slice 12c — env-flag + workflow-launch guard ─────────────────────────────
#
# Per doc 12 § "Atomic Landing Contract": ``IRIAI_EXEC_CONTROL_PLANE_ENABLED`` is
# the single product-authoritative switch for the typed execution control plane.
# The env-flag helper below is the OUTERMOST gate at the real CLI/Slack-bridge
# entrypoint; the workflow-launch guard wraps the Slice-10f ``assert_control_
# plane_ready`` + adds the 4 fail-closed conditions per doc 12 § "Operational
# Go/No-Go":
#
#  1. deploy artifact must match the go-approved candidate commit;
#  2. required migrations are present;
#  3. global switch is enabled when a control-plane launch is requested;
#  4. no forbidden partial control is enabled for production authority.


IRIAI_EXEC_CONTROL_PLANE_ENABLED_ENV: str = "IRIAI_EXEC_CONTROL_PLANE_ENABLED"
"""The single product-authoritative env-flag name per doc 12 § "Atomic Landing
Contract". Slice 12c owns this constant; Slice 12d's adoption record + Slice
12e's final-landing wiring read this same flag through
:func:`read_control_plane_env_flag`."""


_ENV_FLAG_TRUE_VALUES: frozenset[str] = frozenset(
    {"1", "true", "yes", "on", "enabled"}
)
_ENV_FLAG_FALSE_VALUES: frozenset[str] = frozenset(
    {"", "0", "false", "no", "off", "disabled"}
)


class EnvFlagState(str, Enum):
    """Typed verdict for the ``IRIAI_EXEC_CONTROL_PLANE_ENABLED`` env-flag read.

    Per doc 12 § "Atomic Landing Contract" the env flag defaults to ``unset``
    (i.e. opt-in for safety). Any unrecognized value MUST FAIL CLOSED via
    :class:`ControlPlaneEnvFlagError` — never silently default to enabled or
    disabled.
    """

    ENABLED = "enabled"
    DISABLED = "disabled"
    UNSET = "unset"

    @property
    def is_enabled(self) -> bool:
        """Return ``True`` only when the flag is explicitly enabled.

        Both ``DISABLED`` and ``UNSET`` are NOT enabled (opt-in default).
        """

        return self is EnvFlagState.ENABLED


class ControlPlaneEnvFlagError(RuntimeError):
    """Raised when ``IRIAI_EXEC_CONTROL_PLANE_ENABLED`` carries a malformed value.

    Per the prompt hard rule: malformed env values MUST FAIL CLOSED with a
    typed error — never silently default to enabled or disabled. The caller
    propagates the error to the CLI/Slack-bridge with a clear message; the
    legacy path is NOT a silent fallback when the env flag is malformed.
    """


def read_control_plane_env_flag(
    env: Mapping[str, str] | None = None,
) -> EnvFlagState:
    """Read ``IRIAI_EXEC_CONTROL_PLANE_ENABLED`` and return a typed verdict.

    Per the prompt hard rule + doc 12 § "Atomic Landing Contract":

    - Recognized true values (case-insensitive after ``.strip().lower()``):
      ``"1"``, ``"true"``, ``"yes"``, ``"on"``, ``"enabled"`` →
      :attr:`EnvFlagState.ENABLED`.
    - Recognized false values (case-insensitive after ``.strip().lower()``):
      ``"0"``, ``"false"``, ``"no"``, ``"off"``, ``"disabled"`` →
      :attr:`EnvFlagState.DISABLED`.
    - Empty / missing (``"" or unset``) → :attr:`EnvFlagState.UNSET` (the
      opt-in default).
    - Any other value (e.g. ``"maybe"``, ``"2"``) → raises
      :class:`ControlPlaneEnvFlagError` (fail closed; never silently default).

    ``env`` defaults to :data:`os.environ`. The mapping is read once; concurrent
    mutation between calls is the caller's responsibility.
    """

    source = os.environ if env is None else env
    raw = source.get(IRIAI_EXEC_CONTROL_PLANE_ENABLED_ENV, "")
    normalized = raw.strip().lower() if isinstance(raw, str) else ""

    if normalized == "":
        # Distinguish "explicitly disabled" from "never set" so callers can
        # surface a clear error when the flag is required.
        if raw == "":
            return EnvFlagState.UNSET
        return EnvFlagState.UNSET

    if normalized in _ENV_FLAG_TRUE_VALUES:
        return EnvFlagState.ENABLED

    if normalized in _ENV_FLAG_FALSE_VALUES:
        return EnvFlagState.DISABLED

    raise ControlPlaneEnvFlagError(
        f"{IRIAI_EXEC_CONTROL_PLANE_ENABLED_ENV} carries a malformed value "
        f"{raw!r}; recognized true values are "
        f"{sorted(_ENV_FLAG_TRUE_VALUES)!r}, recognized false values are "
        f"{sorted(_ENV_FLAG_FALSE_VALUES - {''})!r}. Refusing to default "
        "silently (doc 12 § 'Atomic Landing Contract', fail closed)."
    )


@dataclass
class WorkflowLaunchReadinessReport:
    """Structured verdict from :func:`assert_control_plane_ready_for_workflow_launch`.

    Extends the Slice-10f :class:`ControlPlaneReadinessReport` with the four
    Slice-12c workflow-launch conditions per doc 12 § "Operational Go/No-Go".
    ``ready`` is ``True`` only when ``env_flag_state == ENABLED``, the
    base readiness report is ready, and the four landing-context conditions
    pass.

    When ``env_flag_state`` is :attr:`EnvFlagState.UNSET` or
    :attr:`EnvFlagState.DISABLED` the caller MUST treat the result as
    "legacy path requested" — NOT as a fail-closed error. The four
    landing-context conditions are evaluated only when the env flag is
    enabled (per the brief: "If the env flag is unset/disabled, the CLI
    continues with the LEGACY pre-control-plane behavior").
    """

    env_flag_state: EnvFlagState
    base_report: ControlPlaneReadinessReport | None = None
    deploy_artifact_id: str | None = None
    candidate_commit: str | None = None
    landing_blockers: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        if not self.env_flag_state.is_enabled:
            return False
        if self.base_report is None or not self.base_report.ready:
            return False
        return not self.landing_blockers

    def raise_if_not_ready(self) -> None:
        if self.ready:
            return
        if not self.env_flag_state.is_enabled:
            raise ControlPlaneStartupError(
                "control-plane workflow launch requested but "
                f"{IRIAI_EXEC_CONTROL_PLANE_ENABLED_ENV} is "
                f"{self.env_flag_state.value!r} — refuse to start the typed "
                "control plane (doc 12 § 'Atomic Landing Contract', fail "
                "closed). Unset/disable the flag to keep the legacy "
                "executor; set it to 'true' to enable the control plane "
                "(opt-in default)."
            )
        # Env flag is ENABLED but base readiness or landing checks failed.
        details: list[str] = []
        if self.base_report is not None and not self.base_report.ready:
            details.extend(self.base_report.missing)
        details.extend(self.landing_blockers)
        raise ControlPlaneStartupError(
            "control-plane workflow launch refused (doc 12 § 'Operational "
            "Go/No-Go', fail closed). Blockers: " + "; ".join(details)
        )


def _check_deploy_artifact_matches_candidate(
    *,
    expected_candidate_commit: str | None,
    deploy_artifact_commit: str | None,
    blockers: list[str],
) -> None:
    """Doc 12 § "Operational Go/No-Go": the deploy artifact MUST match the
    go-approved candidate commit. A mismatch is a hard no-go.

    Both arguments are optional so a purely-static check (no deploy-artifact
    metadata available) still runs; the check fires only when BOTH are
    supplied. A caller that supplies a candidate commit without a deploy
    artifact commit gets a blocker (the landing record requires both).
    """

    if expected_candidate_commit is None and deploy_artifact_commit is None:
        return
    if expected_candidate_commit is None:
        blockers.append(
            "deploy_artifact: candidate_commit not supplied — cannot verify "
            f"deploy_artifact_commit={deploy_artifact_commit!r}"
        )
        return
    if deploy_artifact_commit is None:
        blockers.append(
            "deploy_artifact: deploy_artifact_commit not supplied — cannot "
            f"verify against candidate_commit={expected_candidate_commit!r}"
        )
        return
    if expected_candidate_commit.strip() != deploy_artifact_commit.strip():
        blockers.append(
            "deploy_artifact: deploy_artifact_commit "
            f"{deploy_artifact_commit!r} does not match go-approved "
            f"candidate_commit={expected_candidate_commit!r} (doc 12 § "
            "'Operational Go/No-Go' — deploy artifact MUST match candidate)"
        )


async def _check_required_migrations(
    *,
    pool: Any | None,
    required_migrations: tuple[str, ...] | None,
    blockers: list[str],
) -> None:
    """Doc 12 § "Operational Go/No-Go": required migrations MUST be present.

    ``required_migrations`` is a tuple of table names that must exist on the
    candidate-commit deploy artifact. When ``pool`` is ``None`` we cannot
    verify migrations live; instead we record a blocker so a caller that
    asked for migration verification but supplied no pool is told why the
    check could not complete.
    """

    if required_migrations is None or len(required_migrations) == 0:
        return
    if pool is None:
        blockers.append(
            "required_migrations: pool not supplied — cannot verify "
            f"{sorted(required_migrations)!r} are present"
        )
        return
    fetchval = getattr(pool, "fetchval", None)
    if not callable(fetchval):
        blockers.append(
            "required_migrations: supplied pool has no fetchval — cannot "
            f"verify {sorted(required_migrations)!r}"
        )
        return
    for table in required_migrations:
        try:
            present = await fetchval(f"SELECT to_regclass('public.{table}')")
        except Exception as exc:  # noqa: BLE001
            blockers.append(
                f"required_migrations: probe for {table!r} raised ({exc!r})"
            )
            continue
        if present is None:
            blockers.append(
                f"required_migrations: required table {table!r} is absent "
                "(run ensure_schema before enabling typed control-plane mode)"
            )


def _check_no_forbidden_partial_controls_enabled(
    *,
    forbidden_partial_controls_enabled: tuple[str, ...] | None,
    blockers: list[str],
) -> None:
    """Doc 12 § "Atomic Landing Contract": "the landing gate must assert that
    no per-slice control is being used as production authority."

    ``forbidden_partial_controls_enabled`` is the caller-supplied list of
    per-slice controls that are currently enabled for production authority
    (the 4-name doc-12 set per Slice-12b's ``FORBIDDEN_PARTIAL_CONTROLS``).
    Any non-empty list is a hard no-go.
    """

    if forbidden_partial_controls_enabled is None:
        return
    for name in forbidden_partial_controls_enabled:
        blockers.append(
            "forbidden_partial_control: "
            f"{name!r} is enabled for production authority — doc 12 forbids "
            "per-slice controls as the production authority (use the single "
            f"{IRIAI_EXEC_CONTROL_PLANE_ENABLED_ENV} switch instead)"
        )


async def assert_control_plane_ready_for_workflow_launch(
    *,
    pool: Any | None = None,
    action_policy: Any | None = None,
    env: Mapping[str, str] | None = None,
    expected_candidate_commit: str | None = None,
    deploy_artifact_commit: str | None = None,
    required_migrations: tuple[str, ...] | None = None,
    forbidden_partial_controls_enabled: tuple[str, ...] | None = None,
    require_enabled: bool = True,
) -> WorkflowLaunchReadinessReport:
    """Slice 12c — the workflow-launch guard.

    Wraps the Slice-10f :func:`assert_control_plane_ready` with the four
    Slice-12c workflow-launch conditions per doc 12 § "Operational Go/No-Go":

    1. The deploy artifact MUST match the go-approved candidate commit.
    2. Required migrations MUST be present.
    3. ``IRIAI_EXEC_CONTROL_PLANE_ENABLED`` MUST be enabled when
       ``require_enabled`` is ``True`` (the workflow-launch caller asserts
       it wants the control plane).
    4. No forbidden partial control may be enabled for production authority.

    Fail-closed semantics:

    - A malformed env flag raises :class:`ControlPlaneEnvFlagError`
      (re-raised from :func:`read_control_plane_env_flag`); the caller MUST
      NOT silently fall back to legacy.
    - When ``require_enabled`` is ``True`` and the env flag is
      ``unset``/``disabled``, the returned report is not ``ready`` and
      :meth:`WorkflowLaunchReadinessReport.raise_if_not_ready` raises a
      clear :class:`ControlPlaneStartupError`.
    - When ``require_enabled`` is ``False`` (the default CLI path that
      tolerates a legacy execution start) and the env flag is
      ``unset``/``disabled``, the report records the env-flag state and
      ``ready`` is ``False`` — the caller decides whether to raise or
      continue on legacy.
    - When the env flag is ``enabled``, the four conditions above are
      checked and any blocker is recorded as a landing blocker.

    The PASS path produces a :class:`WorkflowLaunchReadinessReport` whose
    ``base_report`` is the Slice-10f :class:`ControlPlaneReadinessReport`;
    Slice-12d's adoption record + Slice-12e's final-landing chain consume
    that base report.
    """

    flag_state = read_control_plane_env_flag(env=env)

    if not flag_state.is_enabled:
        report = WorkflowLaunchReadinessReport(env_flag_state=flag_state)
        if require_enabled:
            report.raise_if_not_ready()
        return report

    # Env flag is ENABLED — run the four Slice-12c conditions plus the
    # Slice-10f base readiness check.
    base = await control_plane_readiness_report(
        pool=pool, action_policy=action_policy
    )
    landing_blockers: list[str] = []
    _check_deploy_artifact_matches_candidate(
        expected_candidate_commit=expected_candidate_commit,
        deploy_artifact_commit=deploy_artifact_commit,
        blockers=landing_blockers,
    )
    await _check_required_migrations(
        pool=pool,
        required_migrations=required_migrations,
        blockers=landing_blockers,
    )
    _check_no_forbidden_partial_controls_enabled(
        forbidden_partial_controls_enabled=forbidden_partial_controls_enabled,
        blockers=landing_blockers,
    )

    report = WorkflowLaunchReadinessReport(
        env_flag_state=flag_state,
        base_report=base,
        deploy_artifact_id=deploy_artifact_commit,
        candidate_commit=expected_candidate_commit,
        landing_blockers=landing_blockers,
    )
    report.raise_if_not_ready()
    return report
