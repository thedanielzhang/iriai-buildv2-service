"""Typed supervisor classifier mapping (Slice 10c-2).

doc 10 ("Supervisor And Dashboard Integration") § "Supervisor Classifier
Mapping" — step 5 of the "Refactoring Steps" — moves the supervisor's
failure-routing decision from artifact-body inference to the typed
control-plane snapshot. This module is the **typed-PRIMARY** path: it maps a
typed :class:`~iriai_build_v2.workflows.develop.execution.snapshots.ControlPlaneSnapshot`
(its ``latest_failures`` typed-failure/route rows + the ``retry_budgets``
budget rows + the ``merge_queue`` state) directly onto a supervisor
classification, with NO heuristic reconstruction from artifact bodies or
untyped event text.

It is a NEW additive leaf module — it does NOT refactor the 2433-line
``classifier.py``. ``SupervisorClassifier.classify`` calls
:func:`classify_typed_snapshot` FIRST when ``evidence_mode == "typed"``; the
pre-existing artifact classifiers stay verbatim as the FALLBACK for
``evidence_mode != "typed"`` (legacy features with no typed rows, or a typed
query that degraded). Typed-primary, legacy-fallback.

The mapping table below is the doc-10 § "Supervisor Classifier Mapping" table
transcribed VERBATIM (:data:`MAPPING_ROWS`) — the supervisor class, action,
and notes are taken exactly from the doc; nothing is invented. The doc-10
classifier-priority list (its 8 numbered levels) is :data:`CLASSIFIER_PRIORITY`.

Two non-negotiable correctness rules this module enforces (doc 10):

1. **Coverage.** Every canonical Slice-07 ``FailureClass`` (the 27-class
   ``failure_router.FAILURE_CLASSES`` taxonomy) maps to EXACTLY ONE typed
   mapping row for any concrete ``(failure_class, route, budget)`` it can
   emit. A class with multiple routes (e.g. ``worktree_alias`` →
   ``run_canonicalization_repair`` vs ``quiesce``) has one row per route
   bucket; the route action + severity choose the row.
   :func:`coverage_report` / the static classifier-coverage test fail if any
   class is unmapped or a ``(class, route)`` double-maps.

2. **Deterministic executor-owned classes never escalate to the operator**
   while their typed route is retry/repair and budget remains.
   ``operator_required`` is valid ONLY when the typed router emits
   ``failure_class='operator_required'`` or ``operator_required=true`` AND no
   deterministic repair route with remaining budget exists. A false operator
   escalation for a deterministic class with budget is a correctness defect.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .models import ActionLevel, ClassificationResult, FailureClass

# The canonical Slice-07 failure taxonomy. Imported from the failure router so
# the coverage test keys against the ONE authoritative 27-class tuple
# (`workflows/develop/execution/failure_router.py:132` `FAILURE_CLASSES`).
from ..workflows.develop.execution.failure_router import (
    FAILURE_CLASSES,
    ROUTE_TABLE,
)

__all__ = [
    "FAILURE_CLASSES",
    "MAPPING_ROWS",
    "CLASSIFIER_PRIORITY",
    "DETERMINISTIC_EXECUTOR_OWNED_CLASSES",
    "TypedMappingRow",
    "TypedClassification",
    "coverage_report",
    "resolve_mapping_row",
    "classify_typed_snapshot",
]


# ── Canonical route-action buckets (failure_router.RouteAction) ─────────────
#
# The doc-10 table conditions on the canonical Slice-07 `RouteAction` values
# (`failure_router.py:103` `RouteAction`). A route is "retry/repair" (the
# executor-owned non-terminal bucket) iff it is one of these; `quiesce` and
# `operator_required` are terminal.

_RETRY_REPAIR_ROUTES: frozenset[str] = frozenset(
    {
        "retry_dispatch",
        "run_product_repair",
        "run_contract_repair",
        "run_canonicalization_repair",
        "run_workspace_repair",
        "run_commit_hygiene_repair",
        "retry_verifier",
        "retry_merge",
        "retry_sandbox_capture",
        "run_sandbox_cleanup",
    }
)

# Deterministic executor-owned failure classes (doc 10 § "Supervisor Classifier
# Mapping": "worktree aliasing, ACL workability, stale projection, commit
# hygiene, contract compile repair, verifier context rebuild, sandbox
# allocation/capture/cleanup, runtime structured output, merge retry, and
# bounded diagnostic dispatch"). These NEVER escalate to the operator while
# their typed route is retry/repair and budget remains.
DETERMINISTIC_EXECUTOR_OWNED_CLASSES: frozenset[str] = frozenset(
    {
        "worktree_alias",
        "acl_workability",
        "stale_projection",
        "commit_hygiene",
        "contract_compile",
        "verifier_context",
        "sandbox_allocation",
        "sandbox_capture",
        "sandbox_cleanup",
        "runtime_context",
        "runtime_structured_output",
        "merge_conflict",
        # bounded diagnostic dispatch — `unknown` with one retry_dispatch.
        "unknown",
    }
)


def _is_retry_repair(route: str) -> bool:
    """True iff ``route`` is a canonical executor-owned retry/repair route."""

    return route.strip().lower() in _RETRY_REPAIR_ROUTES


# ── The typed mapping row ───────────────────────────────────────────────────


@dataclass(frozen=True)
class TypedMappingRow:
    """One row of the doc-10 § "Supervisor Classifier Mapping" table.

    A row matches a typed failure when its ``failure_class`` is in
    :attr:`failure_classes` AND the typed route is in :attr:`routes` (an empty
    :attr:`routes` matches any route) AND :attr:`requires_budget` is satisfied.
    """

    row_id: str
    failure_classes: frozenset[str]
    routes: frozenset[str]
    classification: FailureClass
    action: ActionLevel
    notes: str
    # When True the row only matches if route-budget remains (doc-10 "and
    # budget remains" / "active ... retry budget" clauses).
    requires_budget: bool = False
    # When True the row only matches when NO route-budget remains — used to
    # disambiguate the budget-exhausted `quiesce` rows from the active-budget
    # retry/repair rows for the same class.
    requires_no_budget: bool = False
    # Priority bucket (doc-10's 8-level classifier priority list). Lower wins.
    priority: int = 99


# ── The doc-10 § "Supervisor Classifier Mapping" table (VERBATIM) ───────────
#
# Each row below is one row of the doc-10 table. The `notes` strings are the
# doc-10 "Notes" column verbatim. `priority` follows the doc-10 "Classifier
# priority after this slice" 8-level list:
#   1. Typed checkpoint contradiction.
#   2. Typed operator-required with no repair route.
#   3. Typed deterministic unblock with route budget remaining.
#   4. Safe bridge restart candidate. (bridge evidence — not a typed-failure row)
#   5. Stale Codex invocation.        (process evidence — not a typed-failure row)
#   6. Typed product repair.
#   7. Healthy typed progress.
#   8. Legacy fallback classifiers.
# pipeline_bug rows that are NOT the checkpoint-contradiction row sit at
# priority 2 alongside operator-required (both are safety stops that must
# outrank a deterministic-unblock retry on a *different*, lower-priority
# failure row — doc-10: "Highest priority. Blocks restart/product repair").

MAPPING_ROWS: tuple[TypedMappingRow, ...] = (
    # Row 1 — checkpoint contradiction. Highest priority.
    TypedMappingRow(
        row_id="checkpoint_contradiction",
        failure_classes=frozenset({"checkpoint_contradiction"}),
        routes=frozenset(),
        classification=FailureClass.PIPELINE_BUG_SUSPECTED,
        action=ActionLevel.STOP_ESCALATE,
        notes=(
            "Highest priority. Blocks restart/product repair recommendations."
        ),
        priority=1,
    ),
    # Row 2 — fatal control-plane safety classes.
    TypedMappingRow(
        row_id="fatal_control_plane_safety",
        failure_classes=frozenset(
            {"dispatcher_internal", "sandbox_isolation", "evidence_corruption"}
        ),
        routes=frozenset(),
        classification=FailureClass.PIPELINE_BUG_SUSPECTED,
        action=ActionLevel.STOP_ESCALATE,
        notes=(
            "Fatal control-plane safety issues. Supervisor reports executor "
            "quiesce and cited evidence, not restart/product repair. "
            "Checkpoint contradictions are matched only by the row above."
        ),
        priority=2,
    ),
    # Row 3 — regroup/contract scheduler-correction quiesce.
    TypedMappingRow(
        row_id="regroup_contract_quiesce",
        failure_classes=frozenset({"regroup_invalid", "contract_compile"}),
        routes=frozenset({"quiesce"}),
        classification=FailureClass.PIPELINE_BUG_SUSPECTED,
        action=ActionLevel.STOP_ESCALATE,
        notes=(
            "Scheduler/contract correction is required before dispatch can "
            "continue. If the router returns run_contract_repair, classify as "
            "deterministic unblock instead."
        ),
        priority=2,
    ),
    # Row 4 — workspace deterministic-unblock retry/repair (budget remains).
    TypedMappingRow(
        row_id="workspace_deterministic_unblock",
        failure_classes=frozenset(
            {"worktree_alias", "acl_workability", "stale_projection",
             "commit_hygiene"}
        ),
        routes=frozenset(
            {"run_canonicalization_repair", "run_workspace_repair",
             "run_commit_hygiene_repair", "retry_verifier"}
        ),
        classification=FailureClass.DETERMINISTIC_UNBLOCK,
        action=ActionLevel.RECOMMEND,
        notes=(
            "Never escalates to operator while retryable and budget remains. "
            "Stale generated-output projections use "
            "stale_projection/verifier_context_stale."
        ),
        requires_budget=True,
        priority=3,
    ),
    # Row 5 — workspace deterministic class, budget exhausted -> quiesce.
    TypedMappingRow(
        row_id="workspace_quiesce",
        failure_classes=frozenset(
            {"worktree_alias", "acl_workability", "stale_projection",
             "commit_hygiene"}
        ),
        routes=frozenset({"quiesce"}),
        classification=FailureClass.PIPELINE_BUG_SUSPECTED,
        action=ActionLevel.STOP_ESCALATE,
        notes=(
            "Deterministic unblock budget is exhausted or no longer safe. "
            "Supervisor reports the blocked route and scheduler/workflow "
            "correction need, not manual file copying or broad product repair."
        ),
        priority=2,
    ),
    # Row 6 — contract compile repair (budget remains).
    TypedMappingRow(
        row_id="contract_compile_repair",
        failure_classes=frozenset({"contract_compile"}),
        routes=frozenset({"run_contract_repair"}),
        classification=FailureClass.DETERMINISTIC_UNBLOCK,
        action=ActionLevel.RECOMMEND,
        notes=(
            "Contract repair is executor-owned only when the router selected "
            "an explicit repair route. contract_compile/quiesce is mapped "
            "above as a safety stop."
        ),
        requires_budget=True,
        priority=3,
    ),
    # Row 7 — sandbox/runtime deterministic retry/repair (budget remains).
    TypedMappingRow(
        row_id="sandbox_runtime_deterministic_unblock",
        failure_classes=frozenset(
            {"sandbox_allocation", "sandbox_capture", "sandbox_cleanup",
             "runtime_context", "runtime_structured_output"}
        ),
        routes=frozenset(
            {"retry_dispatch", "retry_sandbox_capture", "run_sandbox_cleanup"}
        ),
        classification=FailureClass.DETERMINISTIC_UNBLOCK,
        action=ActionLevel.RECOMMEND,
        notes=(
            "Deterministic retry/repair stays executor-owned while budget "
            "remains."
        ),
        requires_budget=True,
        priority=3,
    ),
    # Row 8 — sandbox/runtime deterministic class, quiesce -> pipeline bug.
    TypedMappingRow(
        row_id="sandbox_runtime_quiesce",
        failure_classes=frozenset(
            {"sandbox_allocation", "sandbox_capture", "sandbox_cleanup",
             "runtime_context", "runtime_structured_output"}
        ),
        routes=frozenset({"quiesce"}),
        classification=FailureClass.PIPELINE_BUG_SUSPECTED,
        action=ActionLevel.STOP_ESCALATE,
        notes=(
            "The router has stopped automatic retry for this deterministic "
            "class."
        ),
        priority=2,
    ),
    # Row 9 — resource exhausted retry (budget remains) -> watch only.
    TypedMappingRow(
        row_id="resource_exhausted_retry",
        failure_classes=frozenset({"resource_exhausted"}),
        routes=frozenset(
            {"retry_dispatch", "retry_sandbox_capture", "run_sandbox_cleanup"}
        ),
        classification=FailureClass.WATCH_ONLY,
        action=ActionLevel.OBSERVE,
        notes="Resource governor/backoff is the next executor-owned action.",
        requires_budget=True,
        priority=6,
    ),
    # Row 10 — resource exhausted quiesce -> pipeline bug.
    TypedMappingRow(
        row_id="resource_exhausted_quiesce",
        failure_classes=frozenset({"resource_exhausted"}),
        routes=frozenset({"quiesce"}),
        classification=FailureClass.PIPELINE_BUG_SUSPECTED,
        action=ActionLevel.STOP_ESCALATE,
        notes="Resource pressure is no longer safely retryable.",
        priority=2,
    ),
    # Row 11 — sandbox_binding / runtime_cancelled quiesce -> watch only.
    TypedMappingRow(
        row_id="sandbox_binding_runtime_cancelled_quiesce",
        failure_classes=frozenset({"sandbox_binding", "runtime_cancelled"}),
        routes=frozenset({"quiesce"}),
        classification=FailureClass.WATCH_ONLY,
        action=ActionLevel.OBSERVE,
        notes=(
            "The workflow is intentionally paused or prevented from unsafe "
            "runtime start. Do not recommend product repair."
        ),
        priority=6,
    ),
    # Row 12 — verifier_context rebuild -> deterministic unblock.
    TypedMappingRow(
        row_id="verifier_context_retry",
        failure_classes=frozenset({"verifier_context"}),
        routes=frozenset({"retry_verifier"}),
        classification=FailureClass.DETERMINISTIC_UNBLOCK,
        action=ActionLevel.RECOMMEND,
        notes=(
            "Verifier-context rebuild is executor-owned. The display must not "
            "call it product repair or safe-restart unless the bridge is "
            "separately dead with no active lease."
        ),
        requires_budget=True,
        priority=3,
    ),
    # Row 12b — verifier_context budget exhausted -> quiesce -> pipeline bug.
    #
    # `verifier_context` is a deterministic executor-owned class whose ONLY
    # `ROUTE_TABLE` route is `retry_verifier` (a budget-bearing retry route,
    # budget 1). When that budget is exhausted `failure_router.decide`
    # (`failure_router.py:1551-1554`) rewrites the action to `quiesce` with
    # `budget_exhausted=True`, so a typed `verifier_context` failure with
    # `route="quiesce"` is genuinely producible and reaches this classifier.
    # The doc-10 § "Supervisor Classifier Mapping" table omits an explicit
    # `verifier_context/quiesce` row, but doc-10's stated rule — "If one of
    # these [deterministic executor-owned] classes reaches `route="quiesce"`,
    # the supervisor reports the stopped route ... it still must not ask the
    # operator to manually edit files" — and the verbatim treatment of every
    # other deterministic-class budget-exhausted `quiesce` row (the workspace
    # `quiesce` row and the sandbox/runtime `quiesce` row both ->
    # `pipeline_bug_suspected`/`stop/escalate`) fix the verdict: a
    # budget-exhausted `verifier_context` is a deterministic safety stop, NOT
    # a `watch_only` legacy fallthrough. Verdict mirrors `workspace_quiesce` /
    # `sandbox_runtime_quiesce` exactly.
    TypedMappingRow(
        row_id="verifier_context_quiesce",
        failure_classes=frozenset({"verifier_context"}),
        routes=frozenset({"quiesce"}),
        classification=FailureClass.PIPELINE_BUG_SUSPECTED,
        action=ActionLevel.STOP_ESCALATE,
        notes=(
            "The router has stopped automatic verifier-context rebuild for "
            "this deterministic class. Supervisor reports the blocked route "
            "and scheduler/workflow correction need, not manual file copying "
            "or broad product repair."
        ),
        priority=2,
    ),
    # Row 13 — verifier_provider retry (budget remains) -> watch only.
    TypedMappingRow(
        row_id="verifier_provider_retry",
        failure_classes=frozenset({"verifier_provider"}),
        routes=frozenset({"retry_verifier"}),
        classification=FailureClass.WATCH_ONLY,
        action=ActionLevel.OBSERVE,
        notes=(
            "Verifier provider failures do not mark product unhealthy until a "
            "verifier returns a product verdict."
        ),
        requires_budget=True,
        priority=6,
    ),
    # Row 14 — verifier_provider quiesce -> watch only.
    TypedMappingRow(
        row_id="verifier_provider_quiesce",
        failure_classes=frozenset({"verifier_provider"}),
        routes=frozenset({"quiesce"}),
        classification=FailureClass.WATCH_ONLY,
        action=ActionLevel.OBSERVE,
        notes=(
            "Provider retry budget is exhausted or unsafe; supervisor reports "
            "provider blockage without classifying product unhealthy."
        ),
        priority=6,
    ),
    # Row 15 — merge_conflict retry (queue budget) -> deterministic unblock.
    TypedMappingRow(
        row_id="merge_conflict_retry",
        failure_classes=frozenset({"merge_conflict"}),
        routes=frozenset({"retry_merge"}),
        classification=FailureClass.DETERMINISTIC_UNBLOCK,
        action=ActionLevel.RECOMMEND,
        notes=(
            "Queue recovery is executor-owned; supervisor reports route/budget "
            "and does not ask for manual rebase while retry remains."
        ),
        requires_budget=True,
        priority=3,
    ),
    # Row 16 — merge_conflict quiesce -> pipeline bug.
    TypedMappingRow(
        row_id="merge_conflict_quiesce",
        failure_classes=frozenset({"merge_conflict"}),
        routes=frozenset({"quiesce"}),
        classification=FailureClass.PIPELINE_BUG_SUSPECTED,
        action=ActionLevel.STOP_ESCALATE,
        notes=(
            "Merge retry is exhausted or unsafe; scheduler feedback/regroup is "
            "required before continuing."
        ),
        priority=2,
    ),
    # Row 17 — operator_required. doc-10: "failure_class = 'operator_required'
    # or operator_required = true and no deterministic repair route remains".
    # The canonical Slice-07 router expresses this as the `operator_required`
    # ROUTE on the failing class (the `operator_required` class itself, and
    # `runtime_context/context_permission_denied`). This row therefore matches
    # the typed `operator_required` ROUTE for any class — the route IS the
    # router's explicit operator escalation. (A deterministic class that ALSO
    # carries a stale `operator_required=true` flag but a newer retry/repair
    # route with budget is handled in `_classify_one_failure`, which prefers
    # the deterministic route — that class never reaches this row.)
    TypedMappingRow(
        row_id="operator_required",
        failure_classes=frozenset(FAILURE_CLASSES),
        routes=frozenset({"operator_required"}),
        classification=FailureClass.OPERATOR_REQUIRED,
        action=ActionLevel.STOP_ESCALATE,
        notes=(
            "Requires explicit typed route; worktree samples alone are "
            "insufficient if the router can repair."
        ),
        priority=2,
    ),
    # Row 18 — product_defect run_product_repair -> normal product repair.
    TypedMappingRow(
        row_id="product_defect_repair",
        failure_classes=frozenset({"product_defect"}),
        routes=frozenset({"run_product_repair"}),
        classification=FailureClass.NORMAL_PRODUCT_REPAIR,
        action=ActionLevel.RECOMMEND,
        notes=(
            "Product failures stay product repair even if historical pipeline "
            "noise exists."
        ),
        priority=6,
    ),
    # Row 19 — product_defect quiesce -> pipeline bug.
    TypedMappingRow(
        row_id="product_defect_quiesce",
        failure_classes=frozenset({"product_defect"}),
        routes=frozenset({"quiesce"}),
        classification=FailureClass.PIPELINE_BUG_SUSPECTED,
        action=ActionLevel.STOP_ESCALATE,
        notes=(
            "Product repair budget is exhausted; scheduler feedback/regroup or "
            "contract review is required before continuing."
        ),
        priority=2,
    ),
    # Row 20 — contract_violation product/contract repair -> normal repair.
    TypedMappingRow(
        row_id="contract_violation_repair",
        failure_classes=frozenset({"contract_violation"}),
        routes=frozenset({"run_product_repair", "run_contract_repair"}),
        classification=FailureClass.NORMAL_PRODUCT_REPAIR,
        action=ActionLevel.RECOMMEND,
        notes=(
            "Contract failures use the canonical typed class; the dashboard "
            "may display legacy labels like contract, but never persists them "
            "as failure_class."
        ),
        priority=6,
    ),
    # Row 21 — contract_violation quiesce -> pipeline bug.
    TypedMappingRow(
        row_id="contract_violation_quiesce",
        failure_classes=frozenset({"contract_violation"}),
        routes=frozenset({"quiesce"}),
        classification=FailureClass.PIPELINE_BUG_SUSPECTED,
        action=ActionLevel.STOP_ESCALATE,
        notes=(
            "Contract id/scope contradictions at merge or checkpoint are "
            "safety stops, not product repair or healthy progress."
        ),
        priority=2,
    ),
    # Row 22 — runtime_provider / runtime_timeout retry budget -> watch only.
    TypedMappingRow(
        row_id="runtime_provider_timeout_retry",
        failure_classes=frozenset({"runtime_provider", "runtime_timeout"}),
        routes=frozenset({"retry_dispatch"}),
        classification=FailureClass.WATCH_ONLY,
        action=ActionLevel.OBSERVE,
        notes="Runtime/provider noise does not mark product unhealthy.",
        requires_budget=True,
        priority=6,
    ),
    # Row 23 — runtime_provider / runtime_timeout quiesce -> watch only.
    TypedMappingRow(
        row_id="runtime_provider_timeout_quiesce",
        failure_classes=frozenset({"runtime_provider", "runtime_timeout"}),
        routes=frozenset({"quiesce"}),
        classification=FailureClass.WATCH_ONLY,
        action=ActionLevel.OBSERVE,
        notes=(
            "Runtime/provider retry budget is exhausted or unsafe; supervisor "
            "reports runtime blockage without product repair or restart "
            "guidance."
        ),
        priority=6,
    ),
    # Row 24 — unknown quiesce -> pipeline bug.
    TypedMappingRow(
        row_id="unknown_quiesce",
        failure_classes=frozenset({"unknown"}),
        routes=frozenset({"quiesce"}),
        classification=FailureClass.PIPELINE_BUG_SUSPECTED,
        action=ActionLevel.STOP_ESCALATE,
        notes="Unknown stopped routes are safety stops.",
        priority=2,
    ),
    # Row 25 — unknown retry_dispatch with diagnostic budget -> watch only.
    TypedMappingRow(
        row_id="unknown_retry_dispatch",
        failure_classes=frozenset({"unknown"}),
        routes=frozenset({"retry_dispatch"}),
        classification=FailureClass.WATCH_ONLY,
        action=ActionLevel.OBSERVE,
        notes=(
            "One bounded diagnostic retry may run without product mutation. "
            "Unknown never becomes healthy progress."
        ),
        requires_budget=True,
        priority=6,
    ),
)


# doc-10 § "Supervisor Classifier Mapping" — "Classifier priority after this
# slice" (the 8 numbered levels), kept as data for the journal/audit trail.
CLASSIFIER_PRIORITY: tuple[str, ...] = (
    "1. Typed checkpoint contradiction.",
    "2. Typed operator-required with no repair route.",
    "3. Typed deterministic unblock with route budget remaining.",
    "4. Safe bridge restart candidate, only when bridge evidence shows "
    "dead/stopped/crashed/unreachable and no active invocation, sandbox "
    "lease, or merge queue lease exists.",
    "5. Stale Codex invocation, only when no typed deterministic route, "
    "active queue lease, or active dispatcher attempt is present.",
    "6. Typed product repair.",
    "7. Healthy typed progress.",
    "8. Legacy fallback classifiers.",
)


# ── Coverage (the static classifier-coverage rule) ─────────────────────────


@dataclass
class _CoverageResult:
    """Internal coverage scan result; surfaced via :func:`coverage_report`."""

    unmapped_classes: list[str] = field(default_factory=list)
    double_mapped: list[str] = field(default_factory=list)
    deterministic_escalations: list[str] = field(default_factory=list)
    ok: bool = True


def _router_emittable_routes_by_class() -> dict[str, set[str]]:
    """The concrete ``(class, route)`` universe the Slice-07 router can emit.

    This is NOT just the ``ROUTE_TABLE`` *fresh* routes. ``ROUTE_TABLE`` is the
    failure-class/type -> *initial* route mapping, but ``failure_router.decide``
    (`failure_router.py:1540-1554`) rewrites a route before it is emitted:

    * a ``run_product_repair`` route that fails ``_allows_product_repair`` is
      rewritten to ``quiesce``;
    * **any** non-terminal retry/repair route is rewritten to ``quiesce`` with
      ``budget_exhausted=True`` once its retry budget is exhausted
      (``budget_remaining <= 0``).

    So for EVERY canonical class whose ``ROUTE_TABLE`` route is a budget-bearing
    retry/repair route, the router can additionally emit that class with
    ``route="quiesce"`` (the budget-exhausted rewrite). The coverage scan MUST
    enumerate those ``(class, "quiesce")`` rewrite routes — otherwise a missing
    or double-mapped budget-exhausted ``quiesce`` row escapes the coverage test
    (this gap let ``verifier_context/quiesce`` ship unmapped: ``verifier_context``
    has only a ``retry_verifier`` ``ROUTE_TABLE`` route, so its budget-exhausted
    ``quiesce`` route was never visited).
    """

    routes_by_class: dict[str, set[str]] = {}
    for (failure_class, _failure_type), route in ROUTE_TABLE.items():
        emit = routes_by_class.setdefault(failure_class, set())
        emit.add(route.action)
        # The budget-exhausted (and the product-repair-denied) rewrite: any
        # retry/repair route can be rewritten to `quiesce` by `decide`.
        if _is_retry_repair(route.action):
            emit.add("quiesce")
    return routes_by_class


def coverage_report() -> _CoverageResult:
    """Scan :data:`MAPPING_ROWS` for the two doc-10 static coverage rules.

    Rule 1 (coverage): every canonical :data:`FAILURE_CLASSES` member maps to
    exactly one row for every concrete ``(failure_class, route)`` the Slice-07
    router can emit — no class unmapped, no ``(class, route, budget-bucket)``
    double-mapped to two supervisor classes. The router-emittable universe
    includes BOTH the ``ROUTE_TABLE`` fresh routes AND the budget-exhausted
    ``(class, "quiesce")`` rewrite routes ``failure_router.decide`` produces
    (see :func:`_router_emittable_routes_by_class`).

    Rule 2 (deterministic-never-escalates): no row classifies a deterministic
    executor-owned class as ``operator_required`` while its route is
    retry/repair. (A deterministic class only reaches ``operator_required`` via
    the typed router emitting that route/flag — never via this table.)

    The static classifier-coverage test asserts ``coverage_report().ok``.
    """

    result = _CoverageResult()

    # Build the concrete (class, route) universe the Slice-07 router can emit —
    # ROUTE_TABLE fresh routes PLUS the budget-exhausted `quiesce` rewrites.
    routes_by_class = _router_emittable_routes_by_class()

    for failure_class in FAILURE_CLASSES:
        emit_routes = routes_by_class.get(failure_class, set())
        if not emit_routes:
            # A class with no ROUTE_TABLE entry still needs a mapping row that
            # matches it under SOME route (defence-in-depth — today every
            # class has a route entry).
            emit_routes = {""}
        for route in sorted(emit_routes):
            # The Slice-07 router (`failure_router.decide`) NEVER emits a
            # retry/repair route with zero budget — when `budget_remaining
            # <= 0` it rewrites the action to `quiesce`. So the concrete
            # router-emittable budget buckets for a route are:
            #   * a retry/repair route -> only the budget-remaining bucket
            #   * a `quiesce`/`operator_required` route -> budget-irrelevant
            #     (modelled here as both buckets so a row that gates on
            #     budget for a terminal route is still flagged).
            if _is_retry_repair(route):
                budget_buckets = (True,)
            else:
                budget_buckets = (True, False)
            for has_budget in budget_buckets:
                matches = [
                    row
                    for row in MAPPING_ROWS
                    if _row_matches(row, failure_class, route, has_budget)
                ]
                distinct = {
                    (row.classification, row.action) for row in matches
                }
                if not matches:
                    result.unmapped_classes.append(
                        f"{failure_class}/{route}"
                        f"/{'budget' if has_budget else 'no_budget'}"
                    )
                    result.ok = False
                elif len(distinct) > 1:
                    result.double_mapped.append(
                        f"{failure_class}/{route}"
                        f"/{'budget' if has_budget else 'no_budget'} -> "
                        + ", ".join(
                            sorted(
                                f"{c.value}:{a.value}" for c, a in distinct
                            )
                        )
                    )
                    result.ok = False

    # Rule 2 — no row escalates a deterministic class on a retry/repair route.
    for row in MAPPING_ROWS:
        if row.classification is not FailureClass.OPERATOR_REQUIRED:
            continue
        for failure_class in row.failure_classes:
            if failure_class not in DETERMINISTIC_EXECUTOR_OWNED_CLASSES:
                continue
            row_routes = row.routes or _RETRY_REPAIR_ROUTES
            if any(_is_retry_repair(route) for route in row_routes):
                result.deterministic_escalations.append(
                    f"{row.row_id}:{failure_class}"
                )
                result.ok = False

    return result


def _row_matches(
    row: TypedMappingRow,
    failure_class: str,
    route: str,
    has_budget: bool,
) -> bool:
    """True iff ``row`` matches a typed failure of this class/route/budget."""

    if failure_class not in row.failure_classes:
        return False
    if row.routes and route not in row.routes:
        return False
    if row.requires_budget and not has_budget:
        return False
    if row.requires_no_budget and has_budget:
        return False
    return True


def resolve_mapping_row(
    failure_class: str,
    route: str,
    *,
    has_budget: bool,
) -> TypedMappingRow | None:
    """Return the single doc-10 mapping row for one typed failure, or None.

    A typed failure with no recognised mapping row returns ``None`` — the
    caller treats that as "no typed verdict for this row" and moves on to the
    next typed failure / the fallback (never a silent default).
    """

    failure_class = (failure_class or "").strip().lower()
    route = (route or "").strip().lower()
    matches = [
        row
        for row in MAPPING_ROWS
        if _row_matches(row, failure_class, route, has_budget)
    ]
    if not matches:
        return None
    # Coverage guarantees a single (classification, action); if several rows
    # tie, pick the lowest-priority-number (highest-priority) row.
    return min(matches, key=lambda row: row.priority)


# ── Typed classification result ─────────────────────────────────────────────


@dataclass(frozen=True)
class TypedClassification:
    """A typed-snapshot classification verdict for one supervisor failure row.

    Carries the chosen :class:`TypedMappingRow`, the typed failure it was
    derived from, and the bounded facts/citations the classifier surfaces.
    """

    classification: FailureClass
    action: ActionLevel
    row: TypedMappingRow
    confidence: float
    facts: dict[str, Any]
    inference: str
    citations: list[str]
    false_positive_checks: list[str]


# ── Typed snapshot helpers (attribute-access — no typed import edge) ────────


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    """Read ``name`` off a Pydantic model or a dict snapshot uniformly."""

    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _failure_route(failure: Any) -> str:
    return str(_attr(failure, "route", "") or "").strip().lower()


def _failure_class_of(failure: Any) -> str:
    return str(_attr(failure, "failure_class", "") or "").strip().lower()


def _failure_id(failure: Any) -> int:
    try:
        return int(_attr(failure, "failure_id", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _failure_created_ordinal(failure: Any) -> float:
    """Return a sortable recency ordinal for ``failure.created_at``.

    :class:`TypedFailureSummary.created_at` is the authoritative recency
    signal. A failure with no usable ``created_at`` sorts as the OLDEST (``-inf``)
    so a row with a real timestamp always wins the recency tiebreak; the
    ``failure_id`` secondary key (always present, monotonic) then orders any
    rows that genuinely share — or both lack — a ``created_at``.
    """

    created = _attr(failure, "created_at", None)
    if created is None:
        return float("-inf")
    timestamp = getattr(created, "timestamp", None)
    if callable(timestamp):
        try:
            return float(timestamp())
        except (TypeError, ValueError, OverflowError, OSError):
            return float("-inf")
    return float("-inf")


def _budget_remaining_for(snapshot: Any, failure: Any) -> int:
    """Return the typed route budget remaining for one typed failure.

    The REAL typed budget source (Slice 10c-2, resolving P3-10a-1):
    :class:`RetryBudgetSummary` rows are keyed by ``(route,
    failure_signature_hash)`` and built from the router's ``route_decision``
    budget payload by ``store._typed_retry_budgets``. Match the failure's
    ``(route, signature_hash)`` to its budget row; the budget row's
    ``budget_remaining`` is the genuine "budget remains" signal the doc-10
    ``requires_budget`` mapping rows gate on.

    Fail-safe: when no budget row matches (a legacy / pre-router failure with
    no typed budget), return ``0`` — a conservative "no budget" so a
    deterministic retry/repair row does NOT match and the budget-exhausted
    safety row applies instead. Never an over-optimistic positive.
    """

    route = _failure_route(failure)
    signature = str(_attr(failure, "signature_hash", "") or "")
    budgets = _attr(snapshot, "retry_budgets", []) or []
    # Prefer an exact (route, signature) match; fall back to a route-only
    # match (the signature may be unset on an older typed failure row).
    route_only: int | None = None
    for budget in budgets:
        if str(_attr(budget, "route", "") or "").strip().lower() != route:
            continue
        budget_signature = _attr(budget, "failure_signature_hash", None)
        remaining = _attr(budget, "budget_remaining", 0)
        try:
            remaining_int = int(remaining or 0)
        except (TypeError, ValueError):
            remaining_int = 0
        if signature and budget_signature and str(budget_signature) == signature:
            return max(0, remaining_int)
        if route_only is None:
            route_only = max(0, remaining_int)
    return route_only if route_only is not None else 0


def _failure_citation(failure: Any) -> str:
    """Return a bounded citation for one typed failure row.

    Prefers the typed ``evidence_id`` (the ``evidence_nodes`` row id) so the
    citation points at the typed control-plane row, not an artifact body.
    """

    evidence_id = _attr(failure, "evidence_id", None)
    if evidence_id is not None:
        return f"control-plane:typed-failure:evidence_node={evidence_id}"
    failure_id = _failure_id(failure)
    if failure_id:
        return f"control-plane:typed-failure:failure_id={failure_id}"
    return ""


def _is_open_failure(failure: Any) -> bool:
    """True iff the typed failure is still open/active (not resolved)."""

    status = str(_attr(failure, "status", "") or "").strip().lower()
    return status not in {"resolved", "suppressed"}


def _failure_sort_key(failure: Any) -> tuple[float, int]:
    """Sort key for "newest typed failure first".

    Primary key is :class:`TypedFailureSummary.created_at` (the authoritative
    recency signal — doc-10's "Latest failures use open/routed/retrying rows
    first"); the monotonic ``failure_id`` is the deterministic secondary key,
    breaking ties when two rows share — or both lack — a ``created_at``. Both
    keys sort ascending, so the caller reverses for newest-first.
    """

    return (_failure_created_ordinal(failure), _failure_id(failure))


# ── The typed-primary classifier ────────────────────────────────────────────


def classify_typed_snapshot(snapshot: Any) -> TypedClassification | None:
    """Classify a typed :class:`ControlPlaneSnapshot` per the doc-10 mapping.

    Returns the highest-priority :class:`TypedClassification` across all OPEN
    typed failures in ``snapshot.latest_failures``, or ``None`` when the
    snapshot carries no typed failure that maps (the caller then either uses
    the typed ``merge_queue`` healthy-progress signal or falls back).

    doc-10 § "Supervisor Classifier Mapping" — the operator-escalation rule:
    ``operator_required`` is honoured ONLY when the typed router emits
    ``failure_class='operator_required'`` or ``operator_required=true`` AND no
    deterministic repair route with remaining budget exists for that same
    failure. A deterministic executor-owned class whose typed route is
    retry/repair with budget remaining is NEVER escalated — the doc-10
    edge-case "Deterministic class with operator_required=true but an active
    newer repair route: prefer the newer deterministic route".
    """

    if snapshot is None:
        return None
    failures = list(_attr(snapshot, "latest_failures", []) or [])
    if not failures:
        return None

    # doc-10 edge case: "Latest failures use open/routed/retrying rows first";
    # a resolved row only explains a current route and is not a live verdict.
    open_failures = [f for f in failures if _is_open_failure(f)]
    candidate_failures = open_failures or failures
    # Newest-first (by created_at, then failure_id) so the live router verdict
    # wins.
    candidate_failures = sorted(
        candidate_failures, key=_failure_sort_key, reverse=True
    )

    verdicts: list[TypedClassification] = []
    for failure in candidate_failures:
        verdict = _classify_one_failure(snapshot, failure)
        if verdict is not None:
            verdicts.append(verdict)

    if not verdicts:
        return None

    # doc-10 classifier-priority list — lowest priority-number wins. Ties are
    # broken by the newest failure (verdicts is already newest-first).
    return min(verdicts, key=lambda v: v.row.priority)


def _classify_one_failure(
    snapshot: Any,
    failure: Any,
) -> TypedClassification | None:
    """Map one typed failure row to its doc-10 supervisor classification."""

    failure_class = _failure_class_of(failure)
    route = _failure_route(failure)
    operator_required = bool(_attr(failure, "operator_required", False))
    retryable = bool(_attr(failure, "retryable", False))
    failure_id = _failure_id(failure)
    budget_remaining = _budget_remaining_for(snapshot, failure)
    has_budget = budget_remaining > 0

    citation = _failure_citation(failure)
    base_facts: dict[str, Any] = {
        "typed_failure_id": failure_id,
        "failure_class": failure_class,
        "failure_type": str(_attr(failure, "failure_type", "") or ""),
        "route": route,
        "operator_required": operator_required,
        "retryable": retryable,
        "deterministic": bool(_attr(failure, "deterministic", False)),
        "budget_remaining": budget_remaining,
        "snapshot_version": str(_attr(snapshot, "snapshot_version", "") or ""),
        "snapshot_source": str(_attr(snapshot, "source", "") or ""),
    }

    # ── The operator-escalation rule (doc-10) ──────────────────────────────
    #
    # `operator_required` is valid ONLY when the typed router emits
    # `failure_class='operator_required'` OR `operator_required=true` AND no
    # deterministic repair route with remaining budget exists.
    operator_signalled = (
        failure_class == "operator_required" or operator_required
    )
    deterministic_repair_available = (
        failure_class in DETERMINISTIC_EXECUTOR_OWNED_CLASSES
        and _is_retry_repair(route)
        and has_budget
    )
    if operator_signalled and not deterministic_repair_available:
        # A genuine operator escalation — but it must still be expressed
        # through a mapping row. The `operator_required` class always has the
        # row; a non-operator class with `operator_required=true` and a
        # quiesce/terminal route is escalated via its own quiesce safety row,
        # which already classifies pipeline_bug_suspected -> stop/escalate.
        if failure_class == "operator_required":
            row = resolve_mapping_row(
                "operator_required", route, has_budget=has_budget
            )
            if row is not None:
                return TypedClassification(
                    classification=row.classification,
                    action=row.action,
                    row=row,
                    confidence=0.95,
                    facts=base_facts | {"mapping_row": row.row_id},
                    inference=(
                        "Typed failure router emitted an operator-required "
                        "route with no deterministic repair route remaining; "
                        f"{row.notes}"
                    ),
                    citations=[citation] if citation else [],
                    false_positive_checks=[
                        "operator_required is honoured only with an explicit "
                        "typed operator_required route/flag and no "
                        "deterministic repair route with budget remaining.",
                        "Worktree samples alone never escalate to operator.",
                    ],
                )
        # else: fall through — a non-operator class is classified by its own
        # route row below (its quiesce row is already a stop/escalate).
    elif operator_signalled and deterministic_repair_available:
        # doc-10 edge case: a deterministic class carries a stale
        # operator_required=true but the router has a newer deterministic
        # repair route with budget — prefer the deterministic route, mark the
        # operator signal superseded, and do NOT escalate.
        base_facts["superseded_operator_signal"] = True

    # ── The doc-10 mapping table ───────────────────────────────────────────
    row = resolve_mapping_row(failure_class, route, has_budget=has_budget)
    if row is None:
        # An unrecognised (class, route) — coverage guarantees this cannot
        # happen for a canonical typed failure, but if a typed row carries a
        # non-canonical class/route, surface no typed verdict (the caller
        # falls back rather than inventing a classification).
        return None

    confidence = _row_confidence(row, base_facts)
    inference = _row_inference(row, base_facts)
    return TypedClassification(
        classification=row.classification,
        action=row.action,
        row=row,
        confidence=confidence,
        facts=base_facts | {"mapping_row": row.row_id},
        inference=inference,
        citations=[citation] if citation else [],
        false_positive_checks=_row_false_positive_checks(row),
    )


def _row_confidence(row: TypedMappingRow, facts: dict[str, Any]) -> float:
    """Confidence for a typed verdict — typed rows are high-confidence."""

    if row.classification is FailureClass.PIPELINE_BUG_SUSPECTED:
        return 0.96
    if row.classification is FailureClass.OPERATOR_REQUIRED:
        return 0.95
    if row.classification is FailureClass.DETERMINISTIC_UNBLOCK:
        return 0.9
    if row.classification is FailureClass.NORMAL_PRODUCT_REPAIR:
        return 0.85
    return 0.8


def _row_inference(row: TypedMappingRow, facts: dict[str, Any]) -> str:
    """A bounded inference string for a typed verdict."""

    return (
        "Typed control-plane failure router decision is primary: "
        f"failure_class={facts['failure_class']} route={facts['route']} "
        f"budget_remaining={facts['budget_remaining']}. {row.notes}"
    )


def _row_false_positive_checks(row: TypedMappingRow) -> list[str]:
    """The standing false-positive checks for a typed verdict."""

    checks = [
        "Typed failure/route decision is primary; legacy artifact "
        "classifiers run only as fallback when evidence_mode != typed.",
        "The doc-10 mapping row is chosen by the typed failure_class + route "
        "+ budget; legacy artifact labels never fill a typed class.",
    ]
    if row.classification is FailureClass.DETERMINISTIC_UNBLOCK:
        checks.append(
            "A deterministic executor-owned class with a retry/repair route "
            "and budget remaining never escalates to the operator."
        )
    return checks
