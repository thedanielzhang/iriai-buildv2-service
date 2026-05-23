"""Slice 12b -- readiness gates infrastructure for the atomic landing record.

This module owns the doc-12 *release-control* contracts and supporting
surfaces: the :class:`AtomicLandingGateResult` operational go/no-go record,
the :class:`WorkflowImprovementMetrics` metrics snapshot, the 10 readiness
gate evidence Pydantic contracts (doc 12 Section "Readiness Gates"), the 11
CI/test matrix-row contracts (doc 12 Section "CI/Test Matrix"), and the
metrics collector that compares typed state + legacy artifact/event evidence
+ the ``8ac124d6`` baseline.

doc 12 Section "Proposed Interfaces/Types": "This slice defines release-
control interfaces, not executor runtime interfaces." That bright line is
why this module is a NEW SIBLING of :mod:`iriai_build_v2.execution_control.
startup` (Slice 10f, which owns runtime/startup readiness for the typed
control plane) rather than an extension of :mod:`iriai_build_v2.workflows.
develop.execution.control_plane` (Slice 12a-1, which owns runtime quiesce
primitives). The release-control surface and the runtime control surface are
distinct concerns; doc 12 calls this out explicitly.

**Fail-closed defaults (the prompt hard rule).** Every contract here is
fail-closed by default. ``AtomicLandingGateResult.passed`` defaults to
``False``; ``operational_decision`` defaults to ``"no_go"``; any
missing/stale required gate, blocker, forbidden partial control,
ci_matrix_run_id, metrics_snapshot_id, decision authority, or rollback
runbook id forces ``passed = False``. The
:meth:`AtomicLandingGateResult.evaluate_passed` classmethod-derived
verdict is the single source of truth -- callers cannot set
``passed = True`` while leaving any required signal absent.

**Reuse of existing primitives (the prompt hard rule).** The metrics
collector reuses the Slice-10a typed :class:`~iriai_build_v2.workflows.
develop.execution.snapshots.ControlPlaneSnapshot` baseline; it does NOT
introduce a new persistence layer. The readiness gate evidence surface
re-uses :class:`~iriai_build_v2.execution_control.startup.ControlPlaneReadinessReport`
for the "Atomic enablement" gate evidence.

**No back-imports.** This module MUST NOT import from
``workflows.develop.phases.implementation`` (the compatibility arrow points
IN, never OUT -- locked by a back-import guard test).

**Slice 12b inventory finding.** ``grep -rn "AtomicLandingGateResult |
WorkflowImprovementMetrics | InFlightAdoptionRecord" src/ tests/`` returned
0 results at the start of Slice 12b; these are NEW Pydantic models, not
refactors.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


__all__ = [
    # Enum literals
    "ReadinessGateName",
    "GateResultStatus",
    "OperationalDecision",
    "TestGroupName",
    "TestGroupVerdict",
    "TestFreshnessVerdict",
    "InternalBuildControlName",
    "FailureClassForDrag",
    "RollbackDisposition",
    # Readiness gate evidence
    "ReadinessGateEvidence",
    "ReadinessGateEvidenceSurface",
    # CI/test matrix
    "CiTestMatrixRow",
    "CiTestMatrixResult",
    # The three doc-12 release-control contracts
    "AtomicLandingGateResult",
    "WorkflowImprovementMetrics",
    "InFlightAdoptionRecord",
    # Metrics collector + supporting types
    "MetricsCollectionInputs",
    "TypedStateMetricsSource",
    "LegacyArtifactMetricsSource",
    "BaselineMetricsSource",
    "MetricsCollector",
    # Helpers
    "REQUIRED_READINESS_GATES",
    "REQUIRED_CI_TEST_GROUPS",
    "FORBIDDEN_PARTIAL_CONTROLS",
    "WORKFLOW_DRAG_FAILURE_CLASSES",
    "compute_task_complexity_weight",
    "evaluate_metrics_success",
]


# --- Enum literals (doc 12) ------------------------------------------------


ReadinessGateName = Literal[
    "atomic_enablement",
    "schema_and_journal",
    "workspace_and_contracts",
    "sandbox_and_dispatcher",
    "verification_and_routing",
    "merge_and_checkpoint",
    "post_dag_business_gates",
    "consumers",
    "resource_safety",
    "operations",
]
"""The 10 readiness gates per doc 12 Section "Readiness Gates" table."""


GateResultStatus = Literal["passed", "failed", "missing", "stale"]
"""Per doc 12 Section "Proposed Interfaces/Types" --
``AtomicLandingGateResult.required_gate_results`` enum."""


OperationalDecision = Literal["go", "no_go"]
"""Per doc 12 Section "Proposed Interfaces/Types" --
``AtomicLandingGateResult.operational_decision`` enum."""


TestGroupName = Literal[
    "static_import_and_syntax",
    "expanded_verification_and_regroup",
    "quiesce_and_resume_safety",
    "workspace_authority",
    "contracts_and_sandbox",
    "dispatcher_gates_and_router",
    "merge_queue_and_checkpoint_proof",
    "post_dag_and_post_test_compatibility",
    "supervisor_and_dashboard_read_models",
    "planning_compatibility",
    "full_regression",
]
"""The 11 CI/test matrix groups per doc 12 Section "CI/Test Matrix" table."""


TestGroupVerdict = Literal["passed", "failed", "missing", "skipped"]
"""Per doc 12 Section "CI/Test Matrix": "A skipped gate is a failed gate" and
"Not implemented yet is a no-go" -- ``missing`` and ``skipped`` are both
non-passing verdicts that fail the gate."""


TestFreshnessVerdict = Literal["fresh", "stale", "unknown"]
"""Per doc 12 Section "CI/Test Matrix": evidence is accepted only when the
freshness verdict is ``fresh`` (matching the candidate commit)."""


InternalBuildControlName = Literal[
    "IRIAI_EXEC_JOURNAL_SHADOW",
    "IRIAI_WORKSPACE_AUTHORITY_BLOCKING",
    "IRIAI_TASK_CONTRACTS_BLOCKING",
    "IRIAI_SANDBOX_CAPTURE_SHADOW",
    "IRIAI_RUNTIME_DISPATCHER_V2",
    "IRIAI_VERIFICATION_GRAPH_V2",
    "IRIAI_FAILURE_ROUTER_V2",
    "IRIAI_MERGE_QUEUE_V2",
    "IRIAI_REGROUP_OVERLAY_V2",
    "IRIAI_SUPERVISOR_TYPED_SNAPSHOT",
]
"""The 10 per-slice internal-build controls per doc 12 Section "Internal
Build Controls And Hard Gates" table. These exist only for CI / local
validation / fixture replay; ``IRIAI_EXEC_CONTROL_PLANE_ENABLED`` (Slice 12c)
is the ONLY product-authoritative production switch."""


FailureClassForDrag = Literal[
    "worktree_alias",
    "acl_workability",
    "stale_projection",
    "commit_hygiene",
    "runtime_provider",
    "merge_conflict",
    "checkpoint_contradiction",
]
"""The 7 failure classes that contribute to ``workflow_drag_hours`` per
doc 12 Section "Success Metrics"."""


RollbackDisposition = Literal[
    "legacy_resume_before_next_group",
    "control_plane_only_after_next_attempt",
]
"""Per doc 12 Section "Proposed Interfaces/Types" line 139 --
``InFlightAdoptionRecord.rollback_disposition`` enum. The two doc-12
documented rollback dispositions for a feature mid-adoption.

* ``legacy_resume_before_next_group`` -- the more conservative option: if the
  adoption record is later found stale/inconsistent before the next group
  dispatch, the feature falls back to the legacy executor at the most recent
  safe boundary. This is the default per the prompt fail-closed rule.
* ``control_plane_only_after_next_attempt`` -- after the next dispatch attempt
  on the control plane, the feature is irrevocably committed to the typed
  executor; rollback after that point is whole-feature only (per doc 12 §
  "Rollout/Rollback Notes")."""


# --- Module-level constants -------------------------------------------------


REQUIRED_READINESS_GATES: tuple[ReadinessGateName, ...] = (
    "atomic_enablement",
    "schema_and_journal",
    "workspace_and_contracts",
    "sandbox_and_dispatcher",
    "verification_and_routing",
    "merge_and_checkpoint",
    "post_dag_business_gates",
    "consumers",
    "resource_safety",
    "operations",
)
"""Doc 12 Section "Readiness Gates" -- every gate is REQUIRED for go.
A skipped gate is a failed gate."""


REQUIRED_CI_TEST_GROUPS: tuple[TestGroupName, ...] = (
    "static_import_and_syntax",
    "expanded_verification_and_regroup",
    "quiesce_and_resume_safety",
    "workspace_authority",
    "contracts_and_sandbox",
    "dispatcher_gates_and_router",
    "merge_queue_and_checkpoint_proof",
    "post_dag_and_post_test_compatibility",
    "supervisor_and_dashboard_read_models",
    "planning_compatibility",
    "full_regression",
)
"""Doc 12 Section "CI/Test Matrix" -- every matrix row is required for atomic
landing."""


FORBIDDEN_PARTIAL_CONTROLS: frozenset[str] = frozenset(InternalBuildControlName.__args__)
"""The 10 per-slice controls per doc 12 Section "Internal Build Controls And
Hard Gates" table. Doc 12: "the landing gate must assert that no per-slice
control is being used as production authority." When any of these names
appears in ``forbidden_partial_controls_enabled``, the landing gate must
fail closed."""


WORKFLOW_DRAG_FAILURE_CLASSES: tuple[FailureClassForDrag, ...] = (
    "worktree_alias",
    "acl_workability",
    "stale_projection",
    "commit_hygiene",
    "runtime_provider",
    "merge_conflict",
    "checkpoint_contradiction",
)
"""Per doc 12 Section "Success Metrics" -- the 7 failure classes summed into
``workflow_drag_hours``."""


# --- Readiness gate evidence ------------------------------------------------


class ReadinessGateEvidence(BaseModel):
    """A bounded evidence record for one of the 10 doc-12 readiness gates.

    Per doc 12 Section "Readiness Gates": "A gate can be marked green only
    when its proof is generated from the same candidate commit as the deploy
    artifact, includes a freshness timestamp, and is linked from the
    ``AtomicLandingGateResult``. A skipped gate is a failed gate."

    ``status`` is the doc-12 gate verdict. ``candidate_commit`` and
    ``recorded_at`` together prove the proof is fresh; the
    :meth:`raise_if_stale_for_candidate` classmethod is the freshness check
    a caller invokes when assembling the landing record.

    ``evidence_refs`` cites the underlying evidence (artifact ids / typed
    row ids / test command output). ``no_go_reasons`` is non-empty for any
    status that is not ``passed`` -- doc 12 fail-closed: "Skipped gate is a
    failed gate" so a skipped gate must record WHY it was skipped.
    """

    gate: ReadinessGateName
    status: GateResultStatus
    candidate_commit: str
    recorded_at: datetime
    summary: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    no_go_reasons: list[str] = Field(default_factory=list)
    proof_owner: str = ""

    @field_validator("candidate_commit")
    @classmethod
    def _candidate_commit_non_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("readiness gate evidence requires a candidate_commit")
        return value

    @model_validator(mode="after")
    def _non_passed_requires_reasons(self) -> "ReadinessGateEvidence":
        # Fail closed: any non-passed status must say WHY (mirrors the
        # Slice-10a ``ControlPlaneSnapshot._degraded_implies_reasons`` rule).
        if self.status != "passed" and not self.no_go_reasons:
            raise ValueError(
                f"readiness gate evidence for {self.gate!r} status "
                f"{self.status!r} must carry no_go_reasons"
            )
        if self.status == "passed" and self.no_go_reasons:
            # A passed gate carrying no-go reasons is contradictory.
            raise ValueError(
                f"readiness gate evidence for {self.gate!r} status 'passed' "
                f"must not carry no_go_reasons: {self.no_go_reasons!r}"
            )
        return self


class ReadinessGateEvidenceSurface(BaseModel):
    """Aggregate evidence surface across the 10 doc-12 readiness gates.

    This is the typed evidence projection a landing-record builder consumes
    to populate :attr:`AtomicLandingGateResult.required_gate_results`.

    Per doc 12 Section "Readiness Gates" -- the surface is fail-closed on
    coverage: every required gate name in :data:`REQUIRED_READINESS_GATES`
    must appear in ``gates`` (an unmapped gate is treated as ``missing``).
    """

    candidate_commit: str
    deploy_artifact_id: str
    generated_at: datetime
    gates: list[ReadinessGateEvidence] = Field(default_factory=list)

    @field_validator("candidate_commit", "deploy_artifact_id")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError(
                "ReadinessGateEvidenceSurface candidate_commit and "
                "deploy_artifact_id must be non-empty"
            )
        return value

    @model_validator(mode="after")
    def _gates_share_candidate(self) -> "ReadinessGateEvidenceSurface":
        # Doc 12: "proof is generated from the same candidate commit as the
        # deploy artifact." Any gate evidence whose candidate_commit does not
        # match the surface candidate is rejected.
        for entry in self.gates:
            if entry.candidate_commit != self.candidate_commit:
                raise ValueError(
                    f"gate {entry.gate!r} candidate_commit "
                    f"{entry.candidate_commit!r} does not match surface "
                    f"candidate_commit {self.candidate_commit!r}"
                )
        return self

    def gate_results_map(self) -> dict[str, GateResultStatus]:
        """Return the doc-12 required-gate-results mapping for the landing
        record.

        Every name in :data:`REQUIRED_READINESS_GATES` appears in the result.
        A gate not present in :attr:`gates` is reported as ``"missing"``
        (fail-closed default).
        """

        recorded: dict[str, GateResultStatus] = {
            entry.gate: entry.status for entry in self.gates
        }
        return {
            name: recorded.get(name, "missing")
            for name in REQUIRED_READINESS_GATES
        }


# --- CI/test matrix -------------------------------------------------------


class CiTestMatrixRow(BaseModel):
    """One row of the doc-12 CI/test matrix.

    Per doc 12 Section "CI/Test Matrix": "A row is accepted only when it
    records the exact command or coverage source, candidate commit, run id,
    pass/fail state, and freshness verdict."

    ``failure_summary`` is non-empty when ``verdict`` is not ``passed`` -- a
    failing/missing/skipped row must record WHY (doc 12 fail-closed).
    """

    test_group: TestGroupName
    command: str
    candidate_commit: str
    run_id: str
    verdict: TestGroupVerdict
    freshness: TestFreshnessVerdict
    started_at: datetime
    finished_at: datetime | None = None
    pass_count: int = 0
    fail_count: int = 0
    skipped_count: int = 0
    failure_summary: str = ""

    @field_validator("command", "candidate_commit", "run_id")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError(
                "CiTestMatrixRow command, candidate_commit, and run_id must "
                "be non-empty"
            )
        return value

    @field_validator("pass_count", "fail_count", "skipped_count")
    @classmethod
    def _counts_non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("CiTestMatrixRow counts must be >= 0")
        return value

    @model_validator(mode="after")
    def _non_passing_requires_summary(self) -> "CiTestMatrixRow":
        if self.verdict != "passed" and not self.failure_summary:
            raise ValueError(
                f"CiTestMatrixRow {self.test_group!r} verdict "
                f"{self.verdict!r} must carry failure_summary"
            )
        # A stale verdict can never be 'passed' -- doc 12 freshness rule.
        if self.verdict == "passed" and self.freshness != "fresh":
            raise ValueError(
                f"CiTestMatrixRow {self.test_group!r} verdict 'passed' "
                f"requires freshness='fresh', got {self.freshness!r}"
            )
        # finished_at must be >= started_at when present.
        if self.finished_at is not None and self.finished_at < self.started_at:
            raise ValueError(
                f"CiTestMatrixRow {self.test_group!r} finished_at "
                "must be >= started_at"
            )
        return self


class CiTestMatrixResult(BaseModel):
    """Aggregate CI/test matrix result keyed by candidate commit + run id.

    Per doc 12 Section "CI/Test Matrix" -- every matrix row is required for
    atomic landing. :meth:`all_required_groups_passed` is the fail-closed
    check the landing record consumes.
    """

    candidate_commit: str
    matrix_run_id: str
    generated_at: datetime
    rows: list[CiTestMatrixRow] = Field(default_factory=list)

    @field_validator("candidate_commit", "matrix_run_id")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError(
                "CiTestMatrixResult candidate_commit and matrix_run_id "
                "must be non-empty"
            )
        return value

    @model_validator(mode="after")
    def _rows_share_candidate(self) -> "CiTestMatrixResult":
        for row in self.rows:
            if row.candidate_commit != self.candidate_commit:
                raise ValueError(
                    f"row {row.test_group!r} candidate_commit "
                    f"{row.candidate_commit!r} does not match matrix "
                    f"candidate_commit {self.candidate_commit!r}"
                )
        return self

    def all_required_groups_passed(self) -> bool:
        """Return ``True`` only when every required test group has a fresh
        passing row.

        Fail-closed: a missing required group, a stale row, a failed row, a
        skipped row, or an unknown freshness verdict all return ``False``.
        """

        by_group: dict[str, CiTestMatrixRow] = {row.test_group: row for row in self.rows}
        for required in REQUIRED_CI_TEST_GROUPS:
            row = by_group.get(required)
            if row is None:
                return False
            if row.verdict != "passed":
                return False
            if row.freshness != "fresh":
                return False
        return True

    def missing_or_failing_groups(self) -> list[str]:
        """Return the sorted list of required-group names that are missing,
        non-passing, or stale.

        Used as the ``blockers`` reason source when the landing record is
        being assembled.
        """

        by_group: dict[str, CiTestMatrixRow] = {row.test_group: row for row in self.rows}
        offenders: list[str] = []
        for required in REQUIRED_CI_TEST_GROUPS:
            row = by_group.get(required)
            if row is None:
                offenders.append(f"{required}:missing")
                continue
            if row.verdict != "passed":
                offenders.append(f"{required}:verdict={row.verdict}")
                continue
            if row.freshness != "fresh":
                offenders.append(f"{required}:freshness={row.freshness}")
                continue
        return sorted(offenders)


# --- The doc-12 release-control Pydantic contracts -------------------------


class WorkflowImprovementMetrics(BaseModel):
    """The doc-12 metrics snapshot per Section "Success Metrics".

    Compares the candidate against the ``8ac124d6`` baseline + the current
    legacy baseline. doc 12: "The metrics snapshot is accepted only when it
    names the validation corpus, candidate commit, baseline source,
    collection time range, and query version. Metrics are evaluated as a
    bundle. Throughput or latency gains cannot offset a checkpoint safety
    regression, stale projection recurrence, or unresolved workspace/commit
    workflow class."

    Field types and names mirror the doc-12 Section "Proposed Interfaces/
    Types" Pydantic schema verbatim.
    """

    feature_id: str
    candidate_id: str
    validation_corpus_id: str
    retry_cycles_per_task: float
    commit_failures_per_task: float
    stale_projection_count: int
    alias_or_acl_failures: int
    checkpoint_safety_regressions: int
    workflow_drag_hours: float
    tasks_per_hour: float
    operator_required_escalations: int
    db_rss_regression_pct: float
    postgres_bytes_growth_pct: float
    complexity_adjusted_tasks_per_hour: float
    baseline_retry_cycles_per_task: float
    baseline_commit_failures_per_task: float
    baseline_stale_projection_count: int
    baseline_workflow_drag_hours: float
    baseline_complexity_adjusted_tasks_per_hour: float

    @field_validator("feature_id", "candidate_id", "validation_corpus_id")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError(
                "WorkflowImprovementMetrics feature_id, candidate_id, and "
                "validation_corpus_id must be non-empty"
            )
        return value

    @field_validator(
        "retry_cycles_per_task",
        "commit_failures_per_task",
        "stale_projection_count",
        "alias_or_acl_failures",
        "checkpoint_safety_regressions",
        "workflow_drag_hours",
        "tasks_per_hour",
        "operator_required_escalations",
        "complexity_adjusted_tasks_per_hour",
        "baseline_retry_cycles_per_task",
        "baseline_commit_failures_per_task",
        "baseline_stale_projection_count",
        "baseline_workflow_drag_hours",
        "baseline_complexity_adjusted_tasks_per_hour",
    )
    @classmethod
    def _non_negative(cls, value: float | int) -> float | int:
        if value < 0:
            raise ValueError(
                "WorkflowImprovementMetrics counts/rates/durations must "
                "be >= 0"
            )
        return value


class AtomicLandingGateResult(BaseModel):
    """The doc-12 atomic landing gate result per Section "Proposed
    Interfaces/Types".

    The operational go/no-go record for the complete-bundle landing. Field
    types and names mirror the doc-12 spec verbatim.

    **Fail-closed defaults.** ``passed`` defaults to ``False``;
    ``operational_decision`` defaults to ``"no_go"``;
    ``forbidden_partial_controls_enabled`` defaults to ``[]``;
    ``blockers`` defaults to ``[]``. The :meth:`evaluate_go_requires`
    classmethod is the single source of truth that derives whether a
    ``passed = True`` / ``operational_decision = "go"`` result is consistent
    with doc 12 Section "Operational Go/No-Go" go-requires bullets. A caller
    cannot construct a ``passed=True`` record that lacks any required
    signal.
    """

    candidate_id: str
    candidate_commit: str
    deploy_artifact_id: str
    passed: bool = False
    required_tests: list[str] = Field(default_factory=list)
    required_gate_results: dict[str, GateResultStatus] = Field(default_factory=dict)
    ci_matrix_run_id: str | None = None
    metrics_snapshot_id: int | None = None
    operational_decision: OperationalDecision = "no_go"
    decided_by: str | None = None
    decided_at: datetime | None = None
    rollback_runbook_id: str | None = None
    forbidden_partial_controls_enabled: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)

    @field_validator("candidate_id", "candidate_commit", "deploy_artifact_id")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError(
                "AtomicLandingGateResult candidate_id, candidate_commit, "
                "and deploy_artifact_id must be non-empty"
            )
        return value

    @model_validator(mode="after")
    def _passed_requires_go_signals(self) -> "AtomicLandingGateResult":
        # Per doc 12 Section "Operational Go/No-Go" -- a ``passed=True`` and
        # ``operational_decision='go'`` result requires every signal listed
        # in :meth:`evaluate_go_requires` to be present. Fail-closed: if a
        # caller asserts ``passed=True`` but the signals do not justify it,
        # raise.
        derived_pass, derived_blockers = self.evaluate_go_requires()
        if self.passed and not derived_pass:
            raise ValueError(
                "AtomicLandingGateResult.passed=True is inconsistent with "
                f"go-requires evaluation; blockers={sorted(derived_blockers)!r}"
            )
        if self.operational_decision == "go" and not derived_pass:
            raise ValueError(
                "AtomicLandingGateResult.operational_decision='go' is "
                f"inconsistent with go-requires evaluation; "
                f"blockers={sorted(derived_blockers)!r}"
            )
        if self.passed and self.operational_decision != "go":
            raise ValueError(
                "AtomicLandingGateResult.passed=True requires "
                f"operational_decision='go', got "
                f"{self.operational_decision!r}"
            )
        return self

    def evaluate_go_requires(self) -> tuple[bool, list[str]]:
        """Return ``(passed, blockers)`` per doc 12 Section "Operational
        Go/No-Go" go-requires bullets.

        ``passed`` is ``True`` only when every doc-12 go-requires bullet
        evaluates true. ``blockers`` lists the failing requirement names
        when ``passed`` is ``False``.

        This is the FAIL-CLOSED check -- if any required signal is missing,
        ``passed`` is ``False`` and the missing signal name is in
        ``blockers``.
        """

        blockers: list[str] = []

        # Doc 12 Section "Operational Go/No-Go": "Every readiness gate is
        # green on the candidate commit."
        for name in REQUIRED_READINESS_GATES:
            status = self.required_gate_results.get(name)
            if status is None:
                blockers.append(f"required_gate:{name}:missing")
            elif status != "passed":
                blockers.append(f"required_gate:{name}:{status}")

        # Doc 12 Section "Operational Go/No-Go": "CI/test matrix results are
        # present, passing, and not stale."
        if self.ci_matrix_run_id is None:
            blockers.append("ci_matrix_run_id:missing")

        # Doc 12 Section "Operational Go/No-Go": "Metrics snapshot compares
        # the candidate against the 8ac124d6 baseline and the current legacy
        # baseline."
        if self.metrics_snapshot_id is None:
            blockers.append("metrics_snapshot_id:missing")

        # Doc 12 Section "Operational Go/No-Go": "The global control-plane
        # enablement owner, alert owner, rollback command path, queue-drain
        # procedure, and active-feature disposition are documented."
        if self.decided_by is None or not str(self.decided_by).strip():
            blockers.append("decided_by:missing")
        if self.decided_at is None:
            blockers.append("decided_at:missing")
        if self.rollback_runbook_id is None or not str(
            self.rollback_runbook_id
        ).strip():
            blockers.append("rollback_runbook_id:missing")

        # Doc 12 Section "Atomic Landing Contract": "the landing gate must
        # assert that no per-slice control is being used as production
        # authority."
        for name in self.forbidden_partial_controls_enabled:
            blockers.append(f"forbidden_partial_control:{name}")

        # Any pre-existing blocker (e.g. workspace cleanliness, queue-drain
        # state) recorded by the caller.
        for blocker in self.blockers:
            blockers.append(f"caller_blocker:{blocker}")

        return (not blockers, blockers)

    @classmethod
    def no_go(
        cls,
        *,
        candidate_id: str,
        candidate_commit: str,
        deploy_artifact_id: str,
        blockers: list[str] | None = None,
    ) -> "AtomicLandingGateResult":
        """Construct a fail-closed no-go landing record.

        Convenience for the doc-12 explicit no-go path: every absent signal
        results in a no-go without raising.
        """

        return cls(
            candidate_id=candidate_id,
            candidate_commit=candidate_commit,
            deploy_artifact_id=deploy_artifact_id,
            passed=False,
            operational_decision="no_go",
            blockers=list(blockers or []),
        )


# --- Slice 12d: in-flight adoption record ---------------------------------


class InFlightAdoptionRecord(BaseModel):
    """The doc-12 per-feature in-flight adoption record per Section "Proposed
    Interfaces/Types" lines 126-141.

    Documents that a legacy in-flight feature has been adopted into the typed
    control plane at a first-safe boundary (a checkpoint or quiesce boundary
    per doc 12 § "In-Flight Cutover Policy"). The record is the artifact body
    of the adoption marker ``execution-control-adoption:{feature_id}`` (per
    doc 12 lines 68-72) and the typed input the resume guard reads.

    Field types and names mirror the doc-12 spec verbatim (lines 126-141) PLUS
    the Slice-12d operator-context fields the brief enumerates
    (``feature_state_at_adoption``, ``adopted_by``, ``landing_gate_result_id``,
    ``pre_adoption_baseline``, ``notes``). The brief's optional fields are
    fail-closed-friendly defaults: empty string / empty dict / default
    rollback disposition.

    **Fail-closed defaults.** ``rollback_disposition`` defaults to
    ``"legacy_resume_before_next_group"`` (the more conservative option per
    doc 12 § "Rollout/Rollback Notes"); ``blockers`` defaults to ``[]``;
    ``notes`` defaults to ``""``; ``adoption_marker_artifact_id`` is
    ``None`` until the artifact write returns the row id.

    **Single source of truth for the marker body.** The adoption command in
    :mod:`iriai_build_v2.execution_control.adoption` constructs this record,
    JSON-serializes it via ``model_dump_json()``, and writes the result under
    the marker key (see :func:`iriai_build_v2.execution_control.adoption.
    adoption_marker_artifact_key`). The resume guard reads the artifact body
    back into this contract via ``model_validate_json``.
    """

    feature_id: str
    candidate_commit: str
    deploy_artifact_id: str
    legacy_root_dag_artifact_id: int
    legacy_root_dag_sha256: str
    completed_checkpoint_range: tuple[int, int]
    next_effective_group_idx: int
    active_regroup_artifact_ids: list[int] = Field(default_factory=list)
    workspace_snapshot_ids: list[int] = Field(default_factory=list)
    projection_digest: str
    adoption_marker_artifact_id: int | None = None
    adopted_at: datetime
    rollback_disposition: RollbackDisposition = "legacy_resume_before_next_group"
    blockers: list[str] = Field(default_factory=list)

    # Slice-12d operator-context fields per the brief (additive on top of the
    # doc-12 verbatim shape). These describe WHO adopted WHEN and the
    # connecting evidence (the Slice-12b landing-gate result id + the pre-
    # adoption typed-state baseline).
    feature_state_at_adoption: str = ""
    adopted_by: str = ""
    landing_gate_result_id: str = ""
    pre_adoption_baseline: dict[str, Any] = Field(default_factory=dict)
    notes: str = ""

    @field_validator(
        "feature_id",
        "candidate_commit",
        "deploy_artifact_id",
        "legacy_root_dag_sha256",
        "projection_digest",
    )
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError(
                "InFlightAdoptionRecord feature_id, candidate_commit, "
                "deploy_artifact_id, legacy_root_dag_sha256, and "
                "projection_digest must be non-empty"
            )
        return value

    @field_validator(
        "legacy_root_dag_artifact_id",
        "next_effective_group_idx",
    )
    @classmethod
    def _non_negative_int(cls, value: int) -> int:
        if value < 0:
            raise ValueError(
                "InFlightAdoptionRecord legacy_root_dag_artifact_id and "
                "next_effective_group_idx must be >= 0"
            )
        return value

    @model_validator(mode="after")
    def _completed_range_well_formed(self) -> "InFlightAdoptionRecord":
        # Fail-closed: the completed checkpoint range must be a 2-tuple of
        # non-negative ints with start <= end (the conventional doc-12 closed
        # range semantics). A range whose end < start signals a malformed
        # adoption attempt -- refuse to construct.
        start, end = self.completed_checkpoint_range
        if start < 0 or end < 0:
            raise ValueError(
                "InFlightAdoptionRecord.completed_checkpoint_range entries "
                f"must be >= 0, got ({start}, {end})"
            )
        if end < start:
            raise ValueError(
                "InFlightAdoptionRecord.completed_checkpoint_range end "
                f"({end}) must be >= start ({start})"
            )
        return self

    @model_validator(mode="after")
    def _adoption_marker_id_non_negative(self) -> "InFlightAdoptionRecord":
        # Fail-closed: when the marker id IS set, it must be a non-negative
        # int (artifact ids are bigserial in Postgres; a negative value
        # signals corruption).
        if (
            self.adoption_marker_artifact_id is not None
            and self.adoption_marker_artifact_id < 0
        ):
            raise ValueError(
                "InFlightAdoptionRecord.adoption_marker_artifact_id must be "
                f">= 0, got {self.adoption_marker_artifact_id}"
            )
        return self


# --- Metrics collector ----------------------------------------------------


class TypedStateMetricsSource(BaseModel):
    """Counts derived from typed state (the Slice-10a
    :class:`ControlPlaneSnapshot` + the typed merge queue + the typed
    failure router).

    The metrics collector reads this SUMMARY from the existing typed
    snapshot rather than re-querying the database -- the snapshot already
    has bounded reads / fresh cursors / typed failure summaries. This
    upholds the prompt rule "reuse the typed snapshot baseline from Slice
    10a, NOT introduce new persistence."
    """

    completed_task_count: int
    typed_retry_count: int
    commit_failure_count: int
    stale_projection_count: int
    alias_or_acl_failures: int
    checkpoint_safety_regressions: int
    operator_required_escalations: int
    workflow_drag_seconds_by_class: dict[str, float] = Field(default_factory=dict)
    wall_clock_hours: float
    task_complexity_weight_sum: float
    db_rss_median_bytes: int
    postgres_bytes: int

    @field_validator(
        "completed_task_count",
        "typed_retry_count",
        "commit_failure_count",
        "stale_projection_count",
        "alias_or_acl_failures",
        "checkpoint_safety_regressions",
        "operator_required_escalations",
        "wall_clock_hours",
        "task_complexity_weight_sum",
        "db_rss_median_bytes",
        "postgres_bytes",
    )
    @classmethod
    def _non_negative(cls, value: float | int) -> float | int:
        if value < 0:
            raise ValueError("TypedStateMetricsSource counters must be >= 0")
        return value


class LegacyArtifactMetricsSource(BaseModel):
    """Counts derived from legacy artifact / event evidence.

    Per doc 12 Section "Persistence And Artifact Compatibility": "Existing
    artifacts and events remain the source for legacy feature metrics until
    typed state is available." This source feeds the legacy baseline
    portion of the comparison.
    """

    completed_task_count: int
    retry_event_count: int
    commit_failure_artifact_count: int
    stale_projection_artifact_count: int
    workflow_drag_seconds_by_class: dict[str, float] = Field(default_factory=dict)
    wall_clock_hours: float
    task_complexity_weight_sum: float

    @field_validator(
        "completed_task_count",
        "retry_event_count",
        "commit_failure_artifact_count",
        "stale_projection_artifact_count",
        "wall_clock_hours",
        "task_complexity_weight_sum",
    )
    @classmethod
    def _non_negative(cls, value: float | int) -> float | int:
        if value < 0:
            raise ValueError("LegacyArtifactMetricsSource counters must be >= 0")
        return value


class BaselineMetricsSource(BaseModel):
    """The ``8ac124d6`` baseline metrics (from
    ``docs/execution-control-plane/00-evidence-and-current-state.md``
    evidence corpus).

    Per doc 12 Section "Cross-Slice Dependencies": "Slice 00 supplies the
    baseline and validation corpus for success metrics." The collector
    reads these baseline counts but never derives them from current state.
    """

    baseline_label: str  # e.g. "8ac124d6" or "current_legacy"
    completed_task_count: int
    retry_count: int
    commit_failure_count: int
    stale_projection_count: int
    workflow_drag_hours: float
    wall_clock_hours: float
    task_complexity_weight_sum: float
    db_rss_median_bytes: int
    postgres_bytes: int

    @field_validator("baseline_label")
    @classmethod
    def _label_non_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("BaselineMetricsSource baseline_label must be non-empty")
        return value


class MetricsCollectionInputs(BaseModel):
    """The bundled inputs for :class:`MetricsCollector`.

    Combines the three doc-12 metric sources (typed state + legacy
    artifact/event evidence + the ``8ac124d6`` baseline) so the collector
    is a pure function of its inputs (testable; deterministic).
    """

    feature_id: str
    candidate_id: str
    validation_corpus_id: str
    typed_state: TypedStateMetricsSource
    legacy_artifacts: LegacyArtifactMetricsSource
    baseline_8ac124d6: BaselineMetricsSource

    @field_validator("feature_id", "candidate_id", "validation_corpus_id")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError(
                "MetricsCollectionInputs feature_id, candidate_id, and "
                "validation_corpus_id must be non-empty"
            )
        return value

    @model_validator(mode="after")
    def _baseline_label_8ac124d6(self) -> "MetricsCollectionInputs":
        # Fail-closed: the baseline must be labeled '8ac124d6' (the doc-00
        # baseline label). The metrics collector compares against this
        # specific baseline; mismatched labels are rejected.
        if self.baseline_8ac124d6.baseline_label != "8ac124d6":
            raise ValueError(
                "MetricsCollectionInputs.baseline_8ac124d6 must have "
                f"baseline_label='8ac124d6', got "
                f"{self.baseline_8ac124d6.baseline_label!r}"
            )
        return self


def compute_task_complexity_weight(
    *,
    backend_repo_count: int = 0,
    cross_repo_flag: bool = False,
    generated_output_flag: bool = False,
    unknown_write_set_flag: bool = False,
    verification_gate_count: int = 0,
) -> float:
    """Compute the doc-12 task complexity weight per Section "Success
    Metrics".

    Formula from doc 12:
        ``task_complexity_weight = 1 + 0.25 * backend_repo_count + 0.25 *
        cross_repo_flag + 0.25 * generated_output_flag + 0.25 *
        unknown_write_set_flag + 0.1 * verification_gate_count``, capped at
        ``2.5``.

    Inputs that exceed reasonable bounds (negative counts) are rejected.
    """

    if backend_repo_count < 0:
        raise ValueError("backend_repo_count must be >= 0")
    if verification_gate_count < 0:
        raise ValueError("verification_gate_count must be >= 0")
    raw = (
        1.0
        + 0.25 * backend_repo_count
        + 0.25 * (1 if cross_repo_flag else 0)
        + 0.25 * (1 if generated_output_flag else 0)
        + 0.25 * (1 if unknown_write_set_flag else 0)
        + 0.10 * verification_gate_count
    )
    return min(raw, 2.5)


class MetricsCollector(BaseModel):
    """The doc-12 metrics collector per Section "Refactoring Steps" step 3:
    "Add a command or test helper that gathers required metrics from typed
    state, legacy summaries, and the ``8ac124d6`` evidence baseline."

    The collector is a PURE function of :class:`MetricsCollectionInputs`:
    it derives every doc-12 :class:`WorkflowImprovementMetrics` field from
    the typed-state + legacy-artifact + baseline sources without performing
    any I/O. Persistence is the caller's responsibility -- per the prompt
    rule "the metrics collector should reuse the typed snapshot baseline
    from Slice 10a, NOT introduce new persistence."
    """

    inputs: MetricsCollectionInputs

    def collect(self) -> WorkflowImprovementMetrics:
        """Return the doc-12 metrics snapshot derived from the inputs.

        Implements the doc-12 Section "Success Metrics" formulas:

        - ``retry_cycles_per_task = typed_retry_count / completed_task_count``
        - ``commit_failures_per_task = commit_failure_count /
          completed_task_count``
        - ``workflow_drag_hours = sum(seconds_by_class.values()) / 3600``
        - ``complexity_adjusted_tasks_per_hour = completed_task_count /
          sum(task_complexity_weight * wall_clock_hours)``

        Division by zero is handled fail-closed: when the denominator is 0,
        the rate is 0.0 (an empty validation corpus cannot show
        improvement; the caller must use a non-empty corpus to claim go).
        """

        typed = self.inputs.typed_state
        baseline = self.inputs.baseline_8ac124d6

        retry_cycles_per_task = _safe_ratio(
            typed.typed_retry_count, typed.completed_task_count
        )
        commit_failures_per_task = _safe_ratio(
            typed.commit_failure_count, typed.completed_task_count
        )
        baseline_retry_cycles_per_task = _safe_ratio(
            baseline.retry_count, baseline.completed_task_count
        )
        baseline_commit_failures_per_task = _safe_ratio(
            baseline.commit_failure_count, baseline.completed_task_count
        )

        workflow_drag_seconds = sum(
            float(seconds)
            for cls_name, seconds in typed.workflow_drag_seconds_by_class.items()
            if cls_name in WORKFLOW_DRAG_FAILURE_CLASSES
        )
        workflow_drag_hours = workflow_drag_seconds / 3600.0 if workflow_drag_seconds else 0.0

        tasks_per_hour = _safe_ratio(
            typed.completed_task_count, typed.wall_clock_hours
        )

        complexity_adjusted_tasks_per_hour = _safe_ratio(
            typed.completed_task_count,
            typed.task_complexity_weight_sum * typed.wall_clock_hours,
        )
        baseline_complexity_adjusted_tasks_per_hour = _safe_ratio(
            baseline.completed_task_count,
            baseline.task_complexity_weight_sum * baseline.wall_clock_hours,
        )

        db_rss_regression_pct = _safe_regression_pct(
            typed.db_rss_median_bytes, baseline.db_rss_median_bytes
        )
        postgres_bytes_growth_pct = _safe_regression_pct(
            typed.postgres_bytes, baseline.postgres_bytes
        )

        return WorkflowImprovementMetrics(
            feature_id=self.inputs.feature_id,
            candidate_id=self.inputs.candidate_id,
            validation_corpus_id=self.inputs.validation_corpus_id,
            retry_cycles_per_task=retry_cycles_per_task,
            commit_failures_per_task=commit_failures_per_task,
            stale_projection_count=typed.stale_projection_count,
            alias_or_acl_failures=typed.alias_or_acl_failures,
            checkpoint_safety_regressions=typed.checkpoint_safety_regressions,
            workflow_drag_hours=workflow_drag_hours,
            tasks_per_hour=tasks_per_hour,
            operator_required_escalations=typed.operator_required_escalations,
            db_rss_regression_pct=db_rss_regression_pct,
            postgres_bytes_growth_pct=postgres_bytes_growth_pct,
            complexity_adjusted_tasks_per_hour=complexity_adjusted_tasks_per_hour,
            baseline_retry_cycles_per_task=baseline_retry_cycles_per_task,
            baseline_commit_failures_per_task=baseline_commit_failures_per_task,
            baseline_stale_projection_count=baseline.stale_projection_count,
            baseline_workflow_drag_hours=baseline.workflow_drag_hours,
            baseline_complexity_adjusted_tasks_per_hour=(
                baseline_complexity_adjusted_tasks_per_hour
            ),
        )


def _safe_ratio(numerator: float | int, denominator: float | int) -> float:
    """Return ``numerator / denominator`` or ``0.0`` for zero denominator.

    Per doc 12 Section "Success Metrics": "``<= 0.25`` when the baseline is
    too small for a stable ratio" -- the rate is well-defined as 0.0 for an
    empty corpus, and the caller must use the size guard to decide whether
    the ratio is meaningful.
    """

    if denominator == 0:
        return 0.0
    return float(numerator) / float(denominator)


def _safe_regression_pct(current: float | int, baseline: float | int) -> float:
    """Return percentage change ``100 * (current - baseline) / baseline`` or
    ``0.0`` when ``baseline == 0``.

    A zero baseline means there is no comparable prior measurement; the
    regression percentage is undefined -- doc 12: "must stay under 10%
    unless explicitly approved in the go/no-go record." Treat as 0% when
    the baseline is unknown (caller must explicitly approve in the go/no-go
    record).
    """

    if baseline == 0:
        return 0.0
    return 100.0 * (float(current) - float(baseline)) / float(baseline)


def evaluate_metrics_success(
    metrics: WorkflowImprovementMetrics,
) -> tuple[bool, list[str]]:
    """Return ``(passed, reasons)`` for the doc-12 Section "Success
    Metrics" success conditions.

    Doc 12 success bullets:
    - ``retry_cycles_per_task <= 0.8 * baseline_retry_cycles_per_task`` over
      the validation corpus, or ``<= 0.25`` when the baseline is too small.
    - ``workflow_drag_hours <= 0.7 * baseline_workflow_drag_hours``.
    - ``commit_failures_per_task <= 0.75 *
      baseline_commit_failures_per_task``, or ``<= 0.05`` when the baseline
      is near zero.
    - ``stale_projection_count <= 0.5 * baseline_stale_projection_count``,
      or ``<= 1`` in fixtures with fewer than three baseline stale
      projections.
    - ``complexity_adjusted_tasks_per_hour >= 0.95 *
      baseline_complexity_adjusted_tasks_per_hour``.
    - ``checkpoint_safety_regressions == 0``.
    - ``operator_required_escalations == 0``.
    - ``alias_or_acl_failures == 0``.
    - ``db_rss_regression_pct < 10.0``.
    - ``postgres_bytes_growth_pct < 10.0``.

    Fail-closed: any failing bullet adds a reason; ``passed`` is ``True``
    only when ``reasons`` is empty.
    """

    reasons: list[str] = []

    # retry_cycles_per_task
    retry_threshold = 0.8 * metrics.baseline_retry_cycles_per_task
    if metrics.baseline_retry_cycles_per_task < 0.3125:  # 0.25 / 0.8
        retry_threshold = 0.25
    if metrics.retry_cycles_per_task > retry_threshold:
        reasons.append(
            f"retry_cycles_per_task={metrics.retry_cycles_per_task:.4f} "
            f"exceeds threshold {retry_threshold:.4f}"
        )

    # workflow_drag_hours
    drag_threshold = 0.7 * metrics.baseline_workflow_drag_hours
    if metrics.workflow_drag_hours > drag_threshold:
        reasons.append(
            f"workflow_drag_hours={metrics.workflow_drag_hours:.4f} "
            f"exceeds threshold {drag_threshold:.4f}"
        )

    # commit_failures_per_task
    commit_threshold = 0.75 * metrics.baseline_commit_failures_per_task
    if metrics.baseline_commit_failures_per_task < 0.0667:  # 0.05 / 0.75
        commit_threshold = 0.05
    if metrics.commit_failures_per_task > commit_threshold:
        reasons.append(
            f"commit_failures_per_task={metrics.commit_failures_per_task:.4f} "
            f"exceeds threshold {commit_threshold:.4f}"
        )

    # stale_projection_count
    stale_threshold = 0.5 * metrics.baseline_stale_projection_count
    if metrics.baseline_stale_projection_count < 3:
        stale_threshold = 1.0
    if metrics.stale_projection_count > stale_threshold:
        reasons.append(
            f"stale_projection_count={metrics.stale_projection_count} "
            f"exceeds threshold {stale_threshold:.2f}"
        )

    # complexity_adjusted_tasks_per_hour
    catph_threshold = 0.95 * metrics.baseline_complexity_adjusted_tasks_per_hour
    if metrics.complexity_adjusted_tasks_per_hour < catph_threshold:
        reasons.append(
            f"complexity_adjusted_tasks_per_hour="
            f"{metrics.complexity_adjusted_tasks_per_hour:.4f} below "
            f"threshold {catph_threshold:.4f}"
        )

    # checkpoint_safety_regressions
    if metrics.checkpoint_safety_regressions != 0:
        reasons.append(
            f"checkpoint_safety_regressions="
            f"{metrics.checkpoint_safety_regressions} (must be 0)"
        )

    # operator_required_escalations
    if metrics.operator_required_escalations != 0:
        reasons.append(
            f"operator_required_escalations="
            f"{metrics.operator_required_escalations} (must be 0)"
        )

    # alias_or_acl_failures
    if metrics.alias_or_acl_failures != 0:
        reasons.append(
            f"alias_or_acl_failures={metrics.alias_or_acl_failures} "
            "(must be 0)"
        )

    # db_rss_regression_pct
    if metrics.db_rss_regression_pct >= 10.0:
        reasons.append(
            f"db_rss_regression_pct={metrics.db_rss_regression_pct:.2f} "
            "exceeds 10.0 (without explicit operational approval)"
        )

    # postgres_bytes_growth_pct
    if metrics.postgres_bytes_growth_pct >= 10.0:
        reasons.append(
            f"postgres_bytes_growth_pct={metrics.postgres_bytes_growth_pct:.2f} "
            "exceeds 10.0 (without explicit operational approval)"
        )

    return (not reasons, reasons)
