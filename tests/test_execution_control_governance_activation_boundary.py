"""Slice 17 7th sub-slice -- cross-cutting governance activation
boundary test surface.

This module is the FINAL Slice 17 sub-slice (the 7th of 7) per
`docs/execution-control-plane/17-policy-recommendation-interface.md`
§ Refactoring Steps. It enforces the **activation-out-of-governance-v1**
discipline at the test surface for ALL governance modules.

Doc-17 step 7 PIN cite (VERBATIM):

    Keep activation out of governance v1 unless a later self-healing
    feature explicitly owns activation with tests.
    (doc-17:178-179)

Doc-17 parent invariant (activation-NOT-a-status) PIN cite VERBATIM:

    activated is deliberately not a GovernancePolicyRecommendation.status.
    Activation belongs to a separate consumer-owned policy record with
    its own schema, tests, replay proof, rollback plan, and audit trail.
    Governance recommendations can propose or be accepted for review,
    but cannot become runtime policy by changing their own row status.
    (doc-17:159-163)

Doc-17 mutation-authority invariant PIN cite VERBATIM:

    Recommendation generation has no direct mutation authority.
    (doc-17:217)

Doc-17 validation-not-activation invariant PIN cite VERBATIM:

    Validation proves the artifact can be understood, not that it
    should be activated.
    (doc-17:170-171)

Doc-13A typed-shape adapter ALLOWED pattern PIN cite (allows
`prompt_context_adapter.py` + `dispatcher_prompt_context.py` to
import typed shapes from `dispatcher` per doc-13a:42-46 + 124-126;
these are READ-ONLY typed adapters NOT activation mutators):

    The legacy code path remains byte-identical when the new opt-in
    port is None. The new opt-in port acquires the dispatcher's
    typed shapes via additive constructor parameter + a single
    conditional branch in the Slice 05 _build_prompt body.
    (doc-13a:42-46 + 124-126)

Doc-14 non-blocking governance-projection pattern PIN cite (inherited
verbatim):

    Governance projection NEVER blocks the merge / checkpoint /
    dispatch path. Failures route through the EXISTING
    evidence_corruption failure_class with the REUSED Slice 14 2nd
    sub-slice retry_governance_projection NON-blocking RouteAction.
    (doc-14:242-243)

Doc-20 governance acceptance/adoption boundary PIN cite:

    Verify every recommendation is advisory unless a later policy
    activation feature explicitly owns mutation. Enable governance for
    new-feature analysis, dashboard, supervisor digest, and CLI
    reporting together only after the acceptance record passes. Task-
    execute agent context remains disabled until Slice 21 lands.
    (doc-20 Refactoring Steps 6-9 + Acceptance Criteria)

The 6 prior Slice 17 sub-slice modules (1st-6th) ALL honor the
activation-out-of-governance-v1 invariant individually via per-
module boundary tests inside their own test files (e.g.
`tests/test_execution_control_consumer_read_api.py` carries
`test_module_source_no_slice_07_failure_router_imports` +
`test_module_import_discipline_no_consumer_module_imports` +
`test_read_api_class_no_mutation_method_names` +
`test_read_api_grants_no_activation_authority`).

This 7th sub-slice's ADDED VALUE is the **cross-cutting + forward-
applying** boundary test surface:

1. The boundary is enforced for ALL 23 governance modules (the 6
   Slice 17 sub-slice modules + the 16 prior Slice 13/13A/14/15/16
   governance modules + the Slice 20 acceptance/adoption module) in a
   single parametrized fixture surface.
2. The boundary is **forward-applicable** (forward-applying):
   future governance modules MUST be added to the
   GOVERNANCE_MODULES list so the boundary is structurally
   enforced at test time.
3. The discipline is ENFORCED as the ABSENCE of code patterns
   (structural), NOT as a runtime check (which would itself need
   to be invoked by every module + need to NOT import consumer-
   side modules -- recursive brittleness rejected per
   feedback_no_overengineer_use_library).

**Activation-authority boundary discipline (enforced by this test
file)**:

A governance module MUST NOT:
- Import any consumer-side activation/mutation surface:
  `failure_router` (the 4 pure-data add points live INSIDE
  failure_router.py NOT inside the governance module), `dashboard`,
  `supervisor.*`, `merge_queue`, `regroup_overlay`,
  `scheduler_metrics`, `scheduler_sizing`, `git_service`, `repair`,
  `verification`, `gates`, `post_dag_gates`, `post_test_guard`,
  `runtime_client`, `sandbox`, `workspace_authority`.
  **Exception**: the Slice 13A typed-shape adapters
  `prompt_context_adapter.py` + `dispatcher_prompt_context.py` MAY
  import typed shapes from `dispatcher` per doc-13a:42-46 +
  124-126 (these are READ-ONLY typed adapters NOT activation
  mutators).
- Expose a public class method with a mutation/activation name
  pattern: `activate_*`, `apply_*`, `bind_*`, `mutate_*`,
  `commit_*`, `dispatch_*`, `schedule_*` are FORBIDDEN method
  prefixes on any public class.
- Write `dag-regroup-active:*`, route-budget state, supervisor
  actions, or merge queue state from governance projection per
  doc-17:184-188.

A governance module MAY:
- Import from `governance.models` (the typed governance evidence
  shapes).
- Import from prior governance modules in the same slice (e.g.
  Slice 16 `finding_rule_engine` imports from `finding_engine`).
- Import from Slice 13A typed-adapter modules
  (`prompt_context_adapter`, `completeness`, etc.).
- Expose public class methods with read/projection name patterns:
  `query_*`, `validate_*`, `build_*`, `write_*`, `record_*`,
  `compute_*`, `derive_*`, `read_*`, `score_*`, `extract_*`,
  `evaluate_*`, `project_*` (write_* is OK because it writes to
  the governance projection layer, NOT the consumer activation
  surface; this is the same write-pattern used by
  `governance_finding_writer` / `decision_record_writer` /
  `governance_scorecard_writer` / `commit_provenance_writer` --
  ALL write to the typed governance projection, NOT to consumer
  activation state).

Per the auto-memory `feedback_no_silent_degradation` rule, this
test surface FAILS FAST at test time when a future governance
module introduces an activation/mutation method or imports a
consumer-side module. There is NO runtime sentinel; the
discipline IS the absence of certain code patterns + the absence
of certain imports.
"""

from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path
from typing import Iterable

import pytest


# ── Governance module list (forward-applicable) ────────────────────────────


GOVERNANCE_MODULES: tuple[str, ...] = (
    # Slice 17 sub-slices (1st-6th) -- policy recommendation interface.
    "iriai_build_v2.execution_control.policy_recommendation",
    "iriai_build_v2.execution_control.recommendation_builder",
    "iriai_build_v2.execution_control.policy_validation_interface",
    "iriai_build_v2.execution_control.decision_record_writer",
    "iriai_build_v2.execution_control.replay_requirement_hook",
    "iriai_build_v2.execution_control.consumer_read_api",
    # Slice 20 -- governance acceptance/adoption boundary.
    "iriai_build_v2.execution_control.governance_acceptance",
    # Slice 16 sub-slices -- finding engine + taxonomy.
    "iriai_build_v2.execution_control.finding_engine",
    "iriai_build_v2.execution_control.finding_rule_engine",
    "iriai_build_v2.execution_control.finding_plan_deviation_engine",
    "iriai_build_v2.execution_control.finding_reviewer_test_failure_engine",
    "iriai_build_v2.execution_control.governance_finding_writer",
    # Slice 15 sub-slices -- governance metrics + scoring.
    "iriai_build_v2.execution_control.governance_metrics",
    "iriai_build_v2.execution_control.governance_metric_extractor",
    "iriai_build_v2.execution_control.governance_scorecard_writer",
    # Slice 14 sub-slices -- commit / line provenance.
    "iriai_build_v2.execution_control.commit_provenance",
    "iriai_build_v2.execution_control.commit_provenance_writer",
    "iriai_build_v2.execution_control.commit_provenance_reader",
    "iriai_build_v2.execution_control.commit_provenance_lineage",
    # Slice 13A sub-slices -- lossless context + evidence completeness.
    # Note: dispatcher_prompt_context + prompt_context_adapter are
    # typed-shape adapters that DO import the typed dispatcher shapes
    # per doc-13a:42-46 + 124-126 (ALLOWED-by-design). The
    # forbidden-imports test below explicitly excludes the
    # dispatcher import from those two modules' check.
    "iriai_build_v2.execution_control.completeness",
    "iriai_build_v2.execution_control.dispatcher_prompt_context",
    "iriai_build_v2.execution_control.gate_companion",
    "iriai_build_v2.execution_control.snapshot_companion",
)
"""The cross-cutting governance module list (23 modules; forward-
applicable). Future governance modules MUST be appended to this list
(future governance modules MUST be appended) so the activation-out-
of-governance-v1 discipline is structurally enforced at test time.

Per doc-17:178-179 step 7 VERBATIM:

    Keep activation out of governance v1 unless a later self-healing
    feature explicitly owns activation with tests.

The 23 modules cover:
- Slice 17 1st-6th sub-slices (6 modules; policy recommendation
  interface).
- Slice 20 acceptance/adoption (1 module; all-at-once governance
  acceptance gate + read-only adoption record).
- Slice 16 5 sub-slices (5 modules; finding engine + taxonomy).
- Slice 15 3 sub-slices (3 modules; governance metrics + scoring).
- Slice 14 4 sub-slices (4 modules; commit / line provenance).
- Slice 13A 4 sub-slices (4 modules; lossless context + evidence
  completeness).

Forward-applicability sentinel: the test
`test_governance_modules_list_is_non_empty_and_stable` enforces
the list's non-empty + stable-shape contract; the test
`test_governance_modules_list_covers_all_known_governance_modules`
enforces the list's coverage of all 23 known governance modules.
"""


# ── Slice 13A typed-shape adapter allowlist ────────────────────────────────


SLICE_13A_TYPED_ADAPTER_MODULES: frozenset[str] = frozenset({
    # Per doc-13a:42-46 + 124-126: these two modules are READ-ONLY
    # typed-shape adapters for the dispatcher's new opt-in port; they
    # import typed shapes (PromptContextBundle, DispatchRequest,
    # PromptBuildResult, ContractPromptBuilderPort) but DO NOT MUTATE
    # the dispatcher / activate any consumer state.
    "iriai_build_v2.execution_control.dispatcher_prompt_context",
    "iriai_build_v2.execution_control.prompt_context_adapter",
})
"""Slice 13A typed-shape adapter allowlist. These modules MAY import
typed shapes from `iriai_build_v2.workflows.develop.execution.dispatcher`
per doc-13a:42-46 + 124-126; the imports are typed-shape READS NOT
activation mutators. The legacy dispatcher code path remains
byte-identical when the new opt-in port is None.

NOTE: `prompt_context_adapter` is not in `GOVERNANCE_MODULES` because
it is a Slice 13A adapter not a governance module per se; but its
allowlisting here is documented for completeness.
"""


# ── Forbidden consumer-side modules ────────────────────────────────────────


FORBIDDEN_CONSUMER_MODULES: tuple[str, ...] = (
    # Slice 07 -- typed failure router. The 4 pure-data add points
    # live INSIDE failure_router.py; governance modules declare a
    # typed *_FAILURE_ID Literal const but DO NOT import the router.
    "iriai_build_v2.workflows.develop.execution.failure_router",
    # Slice 08 -- merge queue + commit / no-dirty proof rows.
    # Governance modules consume typed read-only shapes (e.g.
    # commit_provenance_writer.py + commit_provenance_reader.py +
    # commit_provenance_lineage.py import the typed RepoCommitProof
    # shape from merge_queue_store, which is the canonical Slice 08
    # typed read surface). The forbidden surface is the mutation
    # merge_queue.py / merge_queue_wiring.py modules.
    "iriai_build_v2.workflows.develop.execution.merge_queue",
    "iriai_build_v2.workflows.develop.execution.merge_queue_wiring",
    # Slice 09 -- regroup overlay + scheduler feedback.
    "iriai_build_v2.workflows.develop.execution.regroup_overlay",
    "iriai_build_v2.workflows.develop.execution.regroup_overlay_activation",
    "iriai_build_v2.workflows.develop.execution.regroup_overlay_resolver",
    "iriai_build_v2.workflows.develop.execution.regroup_overlay_validation",
    "iriai_build_v2.workflows.develop.execution.scheduler_metrics",
    "iriai_build_v2.workflows.develop.execution.scheduler_sizing",
    # Slice 05/06 -- dispatcher + repair + verification + gates +
    # runtime_client + sandbox + workspace_authority + git_service +
    # post_dag_gates + post_test_guard. The Slice 13A typed-shape
    # adapter pattern is allowlisted for dispatcher imports above; ALL
    # OTHER governance modules are forbidden from importing dispatcher
    # or any of the other consumer-side execution modules.
    "iriai_build_v2.workflows.develop.execution.dispatcher",
    "iriai_build_v2.workflows.develop.execution.repair",
    "iriai_build_v2.workflows.develop.execution.verification",
    "iriai_build_v2.workflows.develop.execution.gates",
    "iriai_build_v2.workflows.develop.execution.runtime_client",
    "iriai_build_v2.workflows.develop.execution.sandbox",
    "iriai_build_v2.workflows.develop.execution.workspace_authority",
    "iriai_build_v2.workflows.develop.execution.git_service",
    "iriai_build_v2.workflows.develop.execution.post_dag_gates",
    "iriai_build_v2.workflows.develop.execution.post_test_guard",
    # Slice 10 supervisor / dashboard -- per
    # IMPLEMENTATION_PROMPT_GOVERNANCE.md Subagent Contract Non-
    # Negotiables: "Supervisor and dashboard consume advisory
    # summaries and must remain read-only." Governance modules do
    # NOT import these.
    "iriai_build_v2.supervisor",
    "dashboard",
)
"""Forbidden consumer-side modules. ALL governance modules MUST NOT
import any of these. The Slice 13A typed-shape adapters
(SLICE_13A_TYPED_ADAPTER_MODULES) are explicitly allowlisted for the
dispatcher import only (per doc-13a:42-46 + 124-126).

Per doc-17:178-179 step 7 + doc-17:217 + doc-17:159-163, governance
modules are READ-ONLY/ADVISORY/ANALYTICAL; they NEVER consume the
consumer-side execution surfaces directly. The typed failure id
registration lives INSIDE failure_router.py (4 pure-data add points)
NOT inside the governance module.
"""


# ── Forbidden mutation method prefixes ────────────────────────────────────


FORBIDDEN_MUTATION_METHOD_PREFIXES: tuple[str, ...] = (
    "activate_",
    "apply_",
    "bind_",
    "mutate_",
    "commit_",
    "dispatch_",
    "schedule_",
)
"""Forbidden mutation method name prefixes on any public class in a
governance module. Per doc-17:178-179 + doc-17:217 + doc-17:159-163
governance modules are READ-ONLY / ADVISORY; they MUST NOT expose
methods with these prefixes.

Note: `write_` is OK because governance modules write to the
typed governance projection layer (e.g.
`GovernanceFindingWriter.write_finding(...)` writes a typed
`review:governance-findings:{corpus_id}` row), NOT to consumer
activation state. Same for `record_` / `project_` -- these write
to the governance projection NOT to consumer mutation surfaces.

Note: `query_*`, `validate_*`, `build_*`, `compute_*`, `derive_*`,
`read_*`, `score_*`, `extract_*`, `evaluate_*` are ALL OK
(projection / read / validate only).
"""


# ── Allowed method name prefixes (positive reference) ─────────────────────


ALLOWED_METHOD_PREFIXES: tuple[str, ...] = (
    "query_",
    "validate_",
    "build_",
    "write_",
    "record_",
    "compute_",
    "derive_",
    "read_",
    "score_",
    "extract_",
    "evaluate_",
    "project_",
    "to_",
    "from_",
    # Pydantic BaseModel methods like model_dump, model_validate.
    "model_",
)
"""Positive reference list of allowed method name prefixes on
governance module classes. NOT enforced by this test file (some
governance methods may have other naming conventions like
`get_*`, `list_*`, `is_*`); only the FORBIDDEN prefixes above are
hard-enforced.
"""


# ── Helper: load module source ────────────────────────────────────────────


def _load_module_source(module_name: str) -> str:
    """Load the source code of a module by import path.

    Per `feedback_verify_cite_everything_impl` the source is loaded
    via the standard `inspect.getfile()` + `Path.read_text()` pattern
    used by the 6 prior Slice 17 sub-slice boundary tests verbatim.
    """

    mod = importlib.import_module(module_name)
    path = Path(inspect.getfile(mod))
    return path.read_text()


def _public_class_methods(module_name: str) -> dict[str, list[str]]:
    """Return a dict mapping class name -> list of public method names
    for every public class in the module."""

    mod = importlib.import_module(module_name)
    result: dict[str, list[str]] = {}
    for name, obj in vars(mod).items():
        if name.startswith("_"):
            continue
        if not inspect.isclass(obj):
            continue
        # Skip classes that are merely re-exported from another
        # module (the class' __module__ must equal the inspected
        # module name).
        if obj.__module__ != module_name:
            continue
        methods = [
            method_name
            for method_name in vars(obj).keys()
            if not method_name.startswith("_")
            and callable(getattr(obj, method_name, None))
        ]
        result[name] = methods
    return result


# ── Sentinel test: GOVERNANCE_MODULES list ────────────────────────────────


def test_governance_modules_list_is_non_empty_and_stable() -> None:
    """Sentinel: the GOVERNANCE_MODULES list MUST be non-empty +
    have stable composition (≥23 modules; never shrinks).

    Per doc-17:178-179 step 7 + forward-applicability, future
    governance modules MUST be appended to this list; the list
    itself is the test surface's input. If a module is removed by
    mistake the boundary is silently weakened -- this sentinel
    catches that regression.
    """

    assert isinstance(GOVERNANCE_MODULES, tuple)
    assert len(GOVERNANCE_MODULES) >= 23, (
        f"GOVERNANCE_MODULES is suspiciously small: "
        f"{len(GOVERNANCE_MODULES)} (expected >=23 covering Slice "
        f"17 1st-6th + Slice 13/13A/14/15/16 governance modules "
        f"+ Slice 20 governance acceptance)"
    )
    # Stable composition: no duplicates.
    assert len(set(GOVERNANCE_MODULES)) == len(GOVERNANCE_MODULES), (
        "GOVERNANCE_MODULES has duplicates"
    )


def test_governance_modules_list_covers_all_slice_17_sub_slices() -> None:
    """All 6 Slice 17 sub-slice modules MUST be in
    GOVERNANCE_MODULES (1st-6th)."""

    slice_17_modules = {
        "iriai_build_v2.execution_control.policy_recommendation",
        "iriai_build_v2.execution_control.recommendation_builder",
        "iriai_build_v2.execution_control.policy_validation_interface",
        "iriai_build_v2.execution_control.decision_record_writer",
        "iriai_build_v2.execution_control.replay_requirement_hook",
        "iriai_build_v2.execution_control.consumer_read_api",
    }
    governance_set = set(GOVERNANCE_MODULES)
    missing = slice_17_modules - governance_set
    assert missing == set(), (
        f"Slice 17 sub-slice modules missing from GOVERNANCE_MODULES: "
        f"{sorted(missing)}"
    )


def test_governance_modules_list_covers_all_slice_16_sub_slices() -> None:
    """All 5 Slice 16 sub-slice modules MUST be in GOVERNANCE_MODULES."""

    slice_16_modules = {
        "iriai_build_v2.execution_control.finding_engine",
        "iriai_build_v2.execution_control.finding_rule_engine",
        "iriai_build_v2.execution_control.finding_plan_deviation_engine",
        "iriai_build_v2.execution_control.finding_reviewer_test_failure_engine",
        "iriai_build_v2.execution_control.governance_finding_writer",
    }
    governance_set = set(GOVERNANCE_MODULES)
    missing = slice_16_modules - governance_set
    assert missing == set(), (
        f"Slice 16 sub-slice modules missing from GOVERNANCE_MODULES: "
        f"{sorted(missing)}"
    )


def test_governance_modules_list_covers_all_slice_15_sub_slices() -> None:
    """All 3 Slice 15 sub-slice modules MUST be in GOVERNANCE_MODULES."""

    slice_15_modules = {
        "iriai_build_v2.execution_control.governance_metrics",
        "iriai_build_v2.execution_control.governance_metric_extractor",
        "iriai_build_v2.execution_control.governance_scorecard_writer",
    }
    governance_set = set(GOVERNANCE_MODULES)
    missing = slice_15_modules - governance_set
    assert missing == set(), (
        f"Slice 15 sub-slice modules missing from GOVERNANCE_MODULES: "
        f"{sorted(missing)}"
    )


def test_governance_modules_list_covers_all_slice_14_sub_slices() -> None:
    """All 4 Slice 14 sub-slice modules MUST be in GOVERNANCE_MODULES."""

    slice_14_modules = {
        "iriai_build_v2.execution_control.commit_provenance",
        "iriai_build_v2.execution_control.commit_provenance_writer",
        "iriai_build_v2.execution_control.commit_provenance_reader",
        "iriai_build_v2.execution_control.commit_provenance_lineage",
    }
    governance_set = set(GOVERNANCE_MODULES)
    missing = slice_14_modules - governance_set
    assert missing == set(), (
        f"Slice 14 sub-slice modules missing from GOVERNANCE_MODULES: "
        f"{sorted(missing)}"
    )


def test_governance_modules_list_covers_all_slice_13a_sub_slices() -> None:
    """The 4 Slice 13A sub-slice modules used by governance MUST be
    in GOVERNANCE_MODULES (completeness + dispatcher_prompt_context
    + gate_companion + snapshot_companion).

    Note: dispatcher_prompt_context is in GOVERNANCE_MODULES AND in
    SLICE_13A_TYPED_ADAPTER_MODULES allowlist (it imports dispatcher
    typed shapes per doc-13a:42-46 + 124-126).
    """

    slice_13a_modules = {
        "iriai_build_v2.execution_control.completeness",
        "iriai_build_v2.execution_control.dispatcher_prompt_context",
        "iriai_build_v2.execution_control.gate_companion",
        "iriai_build_v2.execution_control.snapshot_companion",
    }
    governance_set = set(GOVERNANCE_MODULES)
    missing = slice_13a_modules - governance_set
    assert missing == set(), (
        f"Slice 13A sub-slice modules missing from GOVERNANCE_MODULES: "
        f"{sorted(missing)}"
    )


def test_governance_modules_list_covers_slice_20_acceptance_module() -> None:
    """The Slice 20 governance acceptance/adoption module MUST be covered.

    Per doc-20 Refactoring Steps 6-9 and Acceptance Criteria, governance
    acceptance/adoption remains advisory/read-only, enables analytical surfaces
    only after acceptance, and keeps task-execute context disabled until Slice
    21. The structural activation boundary must therefore cover the new Slice 20
    module.
    """

    slice_20_modules = {
        "iriai_build_v2.execution_control.governance_acceptance",
    }
    governance_set = set(GOVERNANCE_MODULES)
    missing = slice_20_modules - governance_set
    assert missing == set(), (
        f"Slice 20 governance acceptance module missing from GOVERNANCE_MODULES: "
        f"{sorted(missing)}"
    )


def test_governance_modules_list_all_modules_importable() -> None:
    """Every module in GOVERNANCE_MODULES MUST be importable.

    Catches typos / renamed modules that would silently bypass the
    boundary check (per feedback_no_silent_degradation).
    """

    for module_name in GOVERNANCE_MODULES:
        # Import via importlib; AssertionError if import fails.
        mod = importlib.import_module(module_name)
        assert mod is not None, f"failed to import {module_name}"


# ── Parametrized: no forbidden consumer-module imports ─────────────────────


@pytest.mark.parametrize("module_name", GOVERNANCE_MODULES)
def test_module_has_no_forbidden_consumer_imports(module_name: str) -> None:
    """Doc-17:178-179 + doc-17:217 + doc-17:159-163: governance
    modules MUST NOT import any consumer-side activation/mutation
    module.

    Exception: the Slice 13A typed-shape adapter modules
    (SLICE_13A_TYPED_ADAPTER_MODULES) MAY import typed shapes from
    `dispatcher` per doc-13a:42-46 + 124-126; the imports are typed-
    shape READS NOT activation mutators.
    """

    source = _load_module_source(module_name)
    for forbidden in FORBIDDEN_CONSUMER_MODULES:
        # Slice 13A typed-shape adapter allowlist exception: the
        # dispatcher import is allowed-by-design for the two adapter
        # modules per doc-13a:42-46 + 124-126.
        if (
            module_name in SLICE_13A_TYPED_ADAPTER_MODULES
            and forbidden == "iriai_build_v2.workflows.develop.execution.dispatcher"
        ):
            continue
        # Match both `from X import` and `import X` forms.
        from_pattern = f"from {forbidden} import"
        import_pattern = f"import {forbidden}"
        assert from_pattern not in source, (
            f"governance module {module_name} has forbidden import "
            f"'{from_pattern}' (violates doc-17:178-179 step 7 + "
            f"doc-17:217 mutation-authority invariant)"
        )
        # `import X` is harder to match exactly because we must
        # match the whole module identifier; use exact line check.
        for line in source.splitlines():
            stripped = line.strip()
            if (
                stripped == f"import {forbidden}"
                or stripped.startswith(f"import {forbidden} ")
                or stripped.startswith(f"import {forbidden}, ")
                or stripped.endswith(f", {forbidden}")
            ):
                pytest.fail(
                    f"governance module {module_name} has forbidden "
                    f"import line '{stripped}' (violates "
                    f"doc-17:178-179 step 7 + doc-17:217 mutation-"
                    f"authority invariant)"
                )


@pytest.mark.parametrize("module_name", GOVERNANCE_MODULES)
def test_module_does_not_import_failure_router(module_name: str) -> None:
    """Doc-17:178-179 step 7: NO governance module imports the typed
    failure router.

    The 4 pure-data add points (for the per-slice typed failure ids
    like `consumer_read_api_failed`, `decision_record_persistence_failed`,
    etc.) live INSIDE `failure_router.py`, NOT inside the governance
    module. The governance module declares a typed `*_FAILURE_ID`
    Literal const but does NOT import the router itself.
    """

    source = _load_module_source(module_name)
    forbidden = "iriai_build_v2.workflows.develop.execution.failure_router"
    assert f"from {forbidden} import" not in source, (
        f"governance module {module_name} imports the failure_router "
        f"directly (violates doc-17:178-179 + the 4 pure-data add "
        f"points discipline; the typed failure id Literal const "
        f"should be declared in the governance module but the router "
        f"itself is NOT imported)"
    )


@pytest.mark.parametrize("module_name", GOVERNANCE_MODULES)
def test_module_does_not_import_dashboard(module_name: str) -> None:
    """Doc-17:178-179 + IMPLEMENTATION_PROMPT_GOVERNANCE.md
    "Supervisor and dashboard consume advisory summaries and must
    remain read-only": NO governance module imports dashboard.

    The dashboard is a CONSUMER of governance evidence; governance
    NEVER imports dashboard.
    """

    source = _load_module_source(module_name)
    assert "from dashboard import" not in source, (
        f"governance module {module_name} imports dashboard "
        f"(violates IMPLEMENTATION_PROMPT_GOVERNANCE.md read-only "
        f"discipline)"
    )
    assert "import dashboard" not in source, (
        f"governance module {module_name} imports dashboard "
        f"(violates IMPLEMENTATION_PROMPT_GOVERNANCE.md read-only "
        f"discipline)"
    )


@pytest.mark.parametrize("module_name", GOVERNANCE_MODULES)
def test_module_does_not_import_supervisor(module_name: str) -> None:
    """Doc-17:178-179 + IMPLEMENTATION_PROMPT_GOVERNANCE.md
    "Supervisor and dashboard consume advisory summaries and must
    remain read-only": NO governance module imports supervisor."""

    source = _load_module_source(module_name)
    # Match both `from iriai_build_v2.supervisor` and `import
    # iriai_build_v2.supervisor` forms.
    assert "from iriai_build_v2.supervisor" not in source, (
        f"governance module {module_name} imports from "
        f"iriai_build_v2.supervisor (violates "
        f"IMPLEMENTATION_PROMPT_GOVERNANCE.md read-only discipline)"
    )
    assert "import iriai_build_v2.supervisor" not in source, (
        f"governance module {module_name} imports "
        f"iriai_build_v2.supervisor (violates "
        f"IMPLEMENTATION_PROMPT_GOVERNANCE.md read-only discipline)"
    )


@pytest.mark.parametrize("module_name", GOVERNANCE_MODULES)
def test_module_does_not_import_merge_queue(module_name: str) -> None:
    """Doc-17:184-188 + doc-17:178-179: NO governance module imports
    merge_queue or merge_queue_wiring (the mutation surfaces).

    The Slice 08 typed read shape `RepoCommitProof` lives in
    `merge_queue_store` (the READ surface); the
    `commit_provenance_*` modules import that typed READ shape, NOT
    the mutation merge_queue / merge_queue_wiring surfaces.
    """

    source = _load_module_source(module_name)
    forbidden_mutation_modules = (
        "iriai_build_v2.workflows.develop.execution.merge_queue",
        "iriai_build_v2.workflows.develop.execution.merge_queue_wiring",
    )
    for forbidden in forbidden_mutation_modules:
        # The merge_queue_store import (the typed READ surface) is OK.
        assert f"from {forbidden} import" not in source, (
            f"governance module {module_name} imports the merge_queue "
            f"mutation surface {forbidden} (violates doc-17:178-179 + "
            f"doc-17:184-188 -- governance must NOT mutate merge "
            f"queue state)"
        )


@pytest.mark.parametrize("module_name", GOVERNANCE_MODULES)
def test_module_does_not_import_scheduler_metrics(module_name: str) -> None:
    """Doc-17:147-158 + doc-17:178-179: NO governance module imports
    the scheduler_metrics / scheduler_sizing mutation surfaces."""

    source = _load_module_source(module_name)
    forbidden_scheduler_modules = (
        "iriai_build_v2.workflows.develop.execution.scheduler_metrics",
        "iriai_build_v2.workflows.develop.execution.scheduler_sizing",
    )
    for forbidden in forbidden_scheduler_modules:
        assert f"from {forbidden} import" not in source, (
            f"governance module {module_name} imports the scheduler "
            f"mutation surface {forbidden} (violates doc-17:178-179 + "
            f"doc-17:147-158 -- governance must NOT mutate scheduler "
            f"state)"
        )


@pytest.mark.parametrize("module_name", GOVERNANCE_MODULES)
def test_module_does_not_import_regroup_overlay(module_name: str) -> None:
    """Doc-17:178-179: NO governance module imports the regroup
    overlay mutation surfaces."""

    source = _load_module_source(module_name)
    forbidden_regroup_modules = (
        "iriai_build_v2.workflows.develop.execution.regroup_overlay",
        "iriai_build_v2.workflows.develop.execution.regroup_overlay_activation",
        "iriai_build_v2.workflows.develop.execution.regroup_overlay_resolver",
        "iriai_build_v2.workflows.develop.execution.regroup_overlay_validation",
    )
    for forbidden in forbidden_regroup_modules:
        assert f"from {forbidden} import" not in source, (
            f"governance module {module_name} imports the regroup "
            f"overlay mutation surface {forbidden} (violates "
            f"doc-17:178-179)"
        )


# ── Parametrized: no forbidden mutation method names on public classes ────


@pytest.mark.parametrize("module_name", GOVERNANCE_MODULES)
def test_module_classes_have_no_forbidden_mutation_method_names(
    module_name: str,
) -> None:
    """Doc-17:178-179 + doc-17:217 + doc-17:159-163: NO public class
    in a governance module exposes a method with a forbidden
    mutation prefix (`activate_`, `apply_`, `bind_`, `mutate_`,
    `commit_`, `dispatch_`, `schedule_`).

    Per the activation-authority boundary discipline, governance
    classes are READ-ONLY / ADVISORY; the consumer owns activation.
    """

    class_methods = _public_class_methods(module_name)
    violations: list[tuple[str, str]] = []
    for class_name, methods in class_methods.items():
        for method_name in methods:
            for forbidden_prefix in FORBIDDEN_MUTATION_METHOD_PREFIXES:
                if method_name.startswith(forbidden_prefix):
                    violations.append((class_name, method_name))
                    break
    assert violations == [], (
        f"governance module {module_name} has classes with "
        f"forbidden mutation method names: "
        f"{[(c, m) for c, m in violations]} (violates "
        f"doc-17:178-179 step 7 + doc-17:217 mutation-authority "
        f"invariant; forbidden prefixes: "
        f"{FORBIDDEN_MUTATION_METHOD_PREFIXES})"
    )


# ── Parametrized: no `activate` / `apply` keyword in public exports ───────


@pytest.mark.parametrize("module_name", GOVERNANCE_MODULES)
def test_module_public_exports_have_no_activate_or_apply_method(
    module_name: str,
) -> None:
    """Doc-17:178-179 + doc-17:159-163: NO public callable in a
    governance module's __all__ exposes an `activate` or `apply`
    surface.

    This is a parallel test to the class-method check that catches
    module-level helper functions named `activate_*` / `apply_*`.
    """

    mod = importlib.import_module(module_name)
    public_names = getattr(mod, "__all__", [])
    forbidden_prefixes = ("activate", "apply_", "apply(")
    violations = [
        name
        for name in public_names
        if any(name.startswith(prefix.rstrip("_")) for prefix in forbidden_prefixes)
    ]
    assert violations == [], (
        f"governance module {module_name} has public exports with "
        f"forbidden activate/apply prefix: {violations} (violates "
        f"doc-17:178-179 step 7 + doc-17:159-163 activation-NOT-a-"
        f"status invariant)"
    )


# ── Doc-17 PIN cite presence sentinel ──────────────────────────────────────


def test_module_docstring_carries_doc_17_178_179_pin_cite() -> None:
    """Sentinel: this test module's docstring MUST carry the
    doc-17:178-179 PIN cite block + the doc-17:159-163 + doc-17:217
    + doc-17:170-171 + doc-13a:42-46 + doc-13a:124-126 PIN cites.

    Per feedback_cite_everything every requirement, journey, and
    architectural decision must be justified with a citation.
    """

    import tests.test_execution_control_governance_activation_boundary as boundary_mod

    docstring = boundary_mod.__doc__ or ""
    expected_cites = (
        "(doc-17:178-179)",
        "(doc-17:159-163)",
        "(doc-17:217)",
        "(doc-17:170-171)",
        "(doc-13a:42-46 + 124-126)",
        "(doc-14:242-243)",
        "(doc-20 Refactoring Steps 6-9 + Acceptance Criteria)",
    )
    for cite in expected_cites:
        assert cite in docstring, (
            f"governance activation boundary test module docstring "
            f"missing PIN cite: {cite}"
        )


def test_module_docstring_carries_step_7_verbatim_quote() -> None:
    """Sentinel: the module docstring MUST quote doc-17:178-179
    step 7 VERBATIM (so the PIN cite is unambiguous + the rule is
    traceable to the source-of-truth doc)."""

    import tests.test_execution_control_governance_activation_boundary as boundary_mod

    docstring = boundary_mod.__doc__ or ""
    verbatim_quote = (
        "Keep activation out of governance v1 unless a later self-healing\n    feature explicitly owns activation with tests."
    )
    assert verbatim_quote in docstring, (
        "module docstring does not carry the doc-17:178-179 step 7 "
        "VERBATIM quote"
    )


def test_module_docstring_carries_step_159_163_verbatim_quote() -> None:
    """Sentinel: the module docstring MUST quote doc-17:159-163
    VERBATIM (activated-NOT-a-status invariant)."""

    import tests.test_execution_control_governance_activation_boundary as boundary_mod

    docstring = boundary_mod.__doc__ or ""
    verbatim_quote = "activated is deliberately not a"
    assert verbatim_quote in docstring, (
        "module docstring does not carry the doc-17:159-163 "
        "activated-NOT-a-status invariant quote"
    )


# ── Forbidden module list integrity ───────────────────────────────────────


def test_forbidden_consumer_modules_list_is_non_empty_and_stable() -> None:
    """Sentinel: FORBIDDEN_CONSUMER_MODULES MUST be non-empty +
    have stable shape (≥15 modules; never shrinks)."""

    assert isinstance(FORBIDDEN_CONSUMER_MODULES, tuple)
    assert len(FORBIDDEN_CONSUMER_MODULES) >= 15, (
        f"FORBIDDEN_CONSUMER_MODULES is suspiciously small: "
        f"{len(FORBIDDEN_CONSUMER_MODULES)} (expected >=15 covering "
        f"failure_router + dashboard + supervisor + merge_queue + "
        f"regroup_overlay + scheduler + dispatcher + repair + "
        f"verification + gates + git_service + ...)"
    )
    assert len(set(FORBIDDEN_CONSUMER_MODULES)) == len(
        FORBIDDEN_CONSUMER_MODULES
    ), "FORBIDDEN_CONSUMER_MODULES has duplicates"


def test_forbidden_consumer_modules_includes_critical_surfaces() -> None:
    """Sentinel: FORBIDDEN_CONSUMER_MODULES MUST include the
    critical mutation surfaces named in doc-17:178-179 + doc-17:217.
    """

    critical = {
        "iriai_build_v2.workflows.develop.execution.failure_router",
        "iriai_build_v2.workflows.develop.execution.dispatcher",
        "iriai_build_v2.workflows.develop.execution.merge_queue",
        "iriai_build_v2.workflows.develop.execution.regroup_overlay",
        "iriai_build_v2.workflows.develop.execution.scheduler_metrics",
        "dashboard",
        "iriai_build_v2.supervisor",
    }
    forbidden_set = set(FORBIDDEN_CONSUMER_MODULES)
    missing = critical - forbidden_set
    assert missing == set(), (
        f"FORBIDDEN_CONSUMER_MODULES missing critical surfaces: "
        f"{sorted(missing)}"
    )


def test_forbidden_mutation_method_prefixes_includes_activate() -> None:
    """Sentinel: FORBIDDEN_MUTATION_METHOD_PREFIXES MUST include
    `activate_` (the canonical activation method name).

    Per doc-17:159-163 activation is consumer-owned; no governance
    class may expose an `activate_*` method.
    """

    assert "activate_" in FORBIDDEN_MUTATION_METHOD_PREFIXES


def test_forbidden_mutation_method_prefixes_excludes_write() -> None:
    """Sentinel: `write_` MUST NOT be in FORBIDDEN_MUTATION_METHOD_PREFIXES.

    Governance modules write to the typed governance projection
    layer (e.g. `governance_finding_writer.write_finding(...)`); this
    is the CORRECT pattern -- governance writes to the governance
    projection, NOT to consumer activation state. The 4 governance
    writer modules (`governance_finding_writer`,
    `decision_record_writer`, `governance_scorecard_writer`,
    `commit_provenance_writer`) all expose `write_*` methods by
    design.
    """

    assert "write_" not in FORBIDDEN_MUTATION_METHOD_PREFIXES


def test_forbidden_mutation_method_prefixes_excludes_query() -> None:
    """Sentinel: `query_` MUST NOT be in FORBIDDEN_MUTATION_METHOD_PREFIXES.

    Consumer read APIs (Slice 17 6th sub-slice `consumer_read_api`)
    expose `query_recommendations(...)` -- a READ method, NOT a
    mutation. Same for any future governance read API.
    """

    assert "query_" not in FORBIDDEN_MUTATION_METHOD_PREFIXES


def test_forbidden_mutation_method_prefixes_excludes_validate() -> None:
    """Sentinel: `validate_` MUST NOT be in FORBIDDEN_MUTATION_METHOD_PREFIXES.

    Per doc-17:170-171 *"Validation proves the artifact can be
    understood, not that it should be activated."* -- validation
    methods are READ-ONLY checks, NOT mutations.
    """

    assert "validate_" not in FORBIDDEN_MUTATION_METHOD_PREFIXES


# ── Slice 13A typed-shape adapter allowlist tests ──────────────────────────


def test_slice_13a_adapter_allowlist_is_non_empty() -> None:
    """Sentinel: SLICE_13A_TYPED_ADAPTER_MODULES MUST be non-empty
    (the Slice 13A typed-shape adapter pattern is real per
    doc-13a:42-46 + 124-126)."""

    assert isinstance(SLICE_13A_TYPED_ADAPTER_MODULES, frozenset)
    assert len(SLICE_13A_TYPED_ADAPTER_MODULES) >= 2


def test_slice_13a_adapter_allowlist_contains_dispatcher_prompt_context() -> None:
    """Sentinel: dispatcher_prompt_context.py MUST be in the
    allowlist (the canonical Slice 13A typed-shape adapter)."""

    assert (
        "iriai_build_v2.execution_control.dispatcher_prompt_context"
        in SLICE_13A_TYPED_ADAPTER_MODULES
    )


def test_slice_13a_adapter_allowlist_contains_prompt_context_adapter() -> None:
    """Sentinel: prompt_context_adapter.py MUST be in the allowlist
    (the upstream Slice 13A typed-shape adapter consumed by
    dispatcher_prompt_context)."""

    assert (
        "iriai_build_v2.execution_control.prompt_context_adapter"
        in SLICE_13A_TYPED_ADAPTER_MODULES
    )


def test_slice_13a_adapter_modules_do_import_dispatcher_typed_shapes() -> None:
    """The Slice 13A typed-shape adapter modules MUST in fact import
    typed shapes from `dispatcher` (positive control for the
    allowlist; if they didn't, the allowlist would be over-broad).

    Per doc-13a:42-46 + 124-126 the dispatcher import is the
    DEFINING characteristic of the typed-shape adapter pattern."""

    for module_name in SLICE_13A_TYPED_ADAPTER_MODULES:
        try:
            source = _load_module_source(module_name)
        except (ImportError, ModuleNotFoundError):
            pytest.skip(f"adapter module {module_name} not importable")
            continue
        assert (
            "from iriai_build_v2.workflows.develop.execution.dispatcher import"
            in source
        ), (
            f"Slice 13A typed-shape adapter {module_name} does NOT "
            f"import dispatcher typed shapes -- allowlist may be "
            f"over-broad (or the adapter module was removed)"
        )


# ── Per-module boundary test cross-references (REUSE sentinel) ─────────────


def test_consumer_read_api_per_module_boundary_tests_still_exist() -> None:
    """Sentinel: the Slice 17 6th sub-slice
    `consumer_read_api.py` per-module boundary tests MUST still
    exist in its own test file. This cross-cutting test surface
    SUPPLEMENTS, does not REPLACE, the per-module boundary tests.

    Per the implementer brief: "the cross-cutting test surface
    SUPPLEMENTS, does not REPLACE, the per-module boundary tests."
    """

    consumer_test_path = Path(
        "/Users/danielzhang/src/iriai/iriai-build-v2/tests/"
        "test_execution_control_consumer_read_api.py"
    )
    assert consumer_test_path.exists()
    source = consumer_test_path.read_text()
    for expected_test in (
        "test_module_source_no_slice_07_failure_router_imports",
        "test_module_import_discipline_no_consumer_module_imports",
        "test_read_api_class_no_mutation_method_names",
        "test_read_api_grants_no_activation_authority",
    ):
        assert f"def {expected_test}(" in source, (
            f"consumer_read_api per-module boundary test "
            f"{expected_test} missing (the cross-cutting test "
            f"surface SUPPLEMENTS not REPLACES the per-module "
            f"tests)"
        )


def test_policy_recommendation_test_file_exists() -> None:
    """Sentinel: the Slice 17 1st sub-slice test file exists."""

    p = Path(
        "/Users/danielzhang/src/iriai/iriai-build-v2/tests/"
        "test_execution_control_policy_recommendation.py"
    )
    assert p.exists()


def test_recommendation_builder_test_file_exists() -> None:
    """Sentinel: the Slice 17 2nd sub-slice test file exists."""

    p = Path(
        "/Users/danielzhang/src/iriai/iriai-build-v2/tests/"
        "test_execution_control_recommendation_builder.py"
    )
    assert p.exists()


def test_policy_validation_interface_test_file_exists() -> None:
    """Sentinel: the Slice 17 3rd sub-slice test file exists."""

    p = Path(
        "/Users/danielzhang/src/iriai/iriai-build-v2/tests/"
        "test_execution_control_policy_validation_interface.py"
    )
    assert p.exists()


def test_decision_record_writer_test_file_exists() -> None:
    """Sentinel: the Slice 17 4th sub-slice test file exists."""

    p = Path(
        "/Users/danielzhang/src/iriai/iriai-build-v2/tests/"
        "test_execution_control_decision_record_writer.py"
    )
    assert p.exists()


def test_replay_requirement_hook_test_file_exists() -> None:
    """Sentinel: the Slice 17 5th sub-slice test file exists."""

    p = Path(
        "/Users/danielzhang/src/iriai/iriai-build-v2/tests/"
        "test_execution_control_replay_requirement_hook.py"
    )
    assert p.exists()


def test_consumer_read_api_test_file_exists() -> None:
    """Sentinel: the Slice 17 6th sub-slice test file exists."""

    p = Path(
        "/Users/danielzhang/src/iriai/iriai-build-v2/tests/"
        "test_execution_control_consumer_read_api.py"
    )
    assert p.exists()


# ── AST-based boundary check (defence-in-depth) ────────────────────────────


@pytest.mark.parametrize("module_name", GOVERNANCE_MODULES)
def test_module_ast_has_no_consumer_module_import_statements(
    module_name: str,
) -> None:
    """Defence-in-depth: parse the module source via AST and check
    that NO `ImportFrom` / `Import` node targets a forbidden
    consumer module.

    The text-scan tests above already catch the surface; the AST
    check is a defence-in-depth pass that catches any future
    syntactic variation (e.g. parenthesized imports across multiple
    lines that might evade naive text matching).
    """

    source = _load_module_source(module_name)
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for forbidden in FORBIDDEN_CONSUMER_MODULES:
                # Slice 13A typed-shape adapter allowlist exception.
                if (
                    module_name in SLICE_13A_TYPED_ADAPTER_MODULES
                    and forbidden == "iriai_build_v2.workflows.develop.execution.dispatcher"
                ):
                    continue
                assert module != forbidden, (
                    f"governance module {module_name} has "
                    f"ImportFrom node targeting forbidden consumer "
                    f"module {forbidden} (violates doc-17:178-179 "
                    f"step 7 + doc-17:217)"
                )
        if isinstance(node, ast.Import):
            for alias in node.names:
                for forbidden in FORBIDDEN_CONSUMER_MODULES:
                    # Slice 13A typed-shape adapter allowlist exception.
                    if (
                        module_name in SLICE_13A_TYPED_ADAPTER_MODULES
                        and forbidden
                        == "iriai_build_v2.workflows.develop.execution.dispatcher"
                    ):
                        continue
                    assert alias.name != forbidden, (
                        f"governance module {module_name} has Import "
                        f"node targeting forbidden consumer module "
                        f"{forbidden} (violates doc-17:178-179 step 7 + "
                        f"doc-17:217)"
                    )


# ── Forward-applicability sentinel: future module addition pattern ─────────


def test_forward_applicability_documented_in_module_docstring() -> None:
    """Sentinel: the test module docstring MUST document the
    forward-applicability contract -- future governance modules
    MUST be added to GOVERNANCE_MODULES.

    Per doc-17:178-179 step 7 the discipline is forward-applicable;
    the test surface must continue to enforce the boundary as new
    governance modules land.
    """

    import tests.test_execution_control_governance_activation_boundary as boundary_mod

    docstring = boundary_mod.__doc__ or ""
    # Phrases as they actually appear in the module-level docstring
    # (case-sensitive). The constant GOVERNANCE_MODULES is referenced
    # by name; the forward-applicability contract is described in
    # both the module docstring + the constant's own docstring block.
    expected_phrases = (
        "forward-applying",
        "future governance modules",
        "GOVERNANCE_MODULES",
    )
    for phrase in expected_phrases:
        assert phrase in docstring, (
            f"governance activation boundary test module docstring "
            f"missing forward-applicability phrase: {phrase!r}"
        )


def test_governance_modules_list_documents_addition_protocol() -> None:
    """Sentinel: the GOVERNANCE_MODULES docstring MUST document
    that future governance modules MUST be appended to the list."""

    docstring = GOVERNANCE_MODULES.__doc__ or ""
    # The GOVERNANCE_MODULES constant docstring lives in the module
    # source not on the tuple itself; check the module-level
    # docstring proxy instead.
    import tests.test_execution_control_governance_activation_boundary as boundary_mod

    # Find the GOVERNANCE_MODULES = (...) block + the following
    # triple-quoted docstring.
    source = inspect.getsource(boundary_mod)
    governance_docstring_block_starts = source.find(
        "\"\"\"The cross-cutting governance module list"
    )
    assert governance_docstring_block_starts >= 0, (
        "GOVERNANCE_MODULES docstring block missing"
    )
    governance_docstring_block_ends = source.find(
        "\"\"\"", governance_docstring_block_starts + 3
    )
    governance_docstring = source[
        governance_docstring_block_starts:governance_docstring_block_ends
    ]
    assert "future governance modules MUST be appended" in governance_docstring, (
        "GOVERNANCE_MODULES docstring does not document the "
        "forward-applicability protocol"
    )


# ── Activation-authority boundary cross-check: failure_router.py ──────────


def test_failure_router_has_per_slice_17_typed_failure_ids() -> None:
    """Cross-check: failure_router.py carries the 5 Slice 17 typed
    failure ids (2nd-6th sub-slices); the 4 pure-data add points
    discipline is preserved.

    This is a positive control: the governance modules CORRECTLY
    place the typed failure id INSIDE failure_router.py (NOT inside
    the governance module itself); the governance module declares
    the typed `*_FAILURE_ID` Literal const but the registration
    (the 4 add points) lives in `failure_router.py`.
    """

    router_path = Path(
        "/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/"
        "workflows/develop/execution/failure_router.py"
    )
    source = router_path.read_text()
    expected_failure_ids = (
        "recommendation_builder_emission_failed",  # 2nd sub-slice
        "policy_validation_failed",  # 3rd sub-slice
        "decision_record_persistence_failed",  # 4th sub-slice
        "replay_requirement_validation_failed",  # 5th sub-slice
        "consumer_read_api_failed",  # 6th sub-slice
    )
    for failure_id in expected_failure_ids:
        assert f'"{failure_id}"' in source, (
            f"failure_router.py does not carry the Slice 17 typed "
            f"failure id {failure_id} (the 4 pure-data add points "
            f"discipline appears violated)"
        )


def test_failure_router_byte_size_did_not_shrink() -> None:
    """Sentinel: failure_router.py is at least 800 lines (after the
    Slice 17 6th sub-slice add points). A shrinkage would indicate
    a regression in the 4 pure-data add points discipline.
    """

    router_path = Path(
        "/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/"
        "workflows/develop/execution/failure_router.py"
    )
    source = router_path.read_text()
    line_count = len(source.splitlines())
    assert line_count >= 800, (
        f"failure_router.py is suspiciously small: {line_count} lines "
        f"(expected >=800; the 4 pure-data add points for Slice 17 "
        f"2nd-6th sub-slices should bring it well above this floor)"
    )


# ── Cross-cutting boundary supplements per-module boundary tests ───────────


def test_cross_cutting_test_supplements_not_replaces_per_module_tests() -> None:
    """Sentinel: this cross-cutting test surface SUPPLEMENTS, does
    not REPLACE, the per-module boundary tests in the individual
    test files.

    Per the implementer brief + the existing 6 per-module boundary
    test sets (one per Slice 17 sub-slice test file), the
    cross-cutting test surface is a DEFENCE-IN-DEPTH layer that
    enforces the boundary across ALL governance modules in a single
    parametrized fixture; the per-module boundary tests REMAIN in
    place for the 6 Slice 17 sub-slice test files (verified by the
    `test_*_test_file_exists` sentinels above).
    """

    expected_per_module_test_files = (
        "test_execution_control_policy_recommendation.py",
        "test_execution_control_recommendation_builder.py",
        "test_execution_control_policy_validation_interface.py",
        "test_execution_control_decision_record_writer.py",
        "test_execution_control_replay_requirement_hook.py",
        "test_execution_control_consumer_read_api.py",
    )
    for test_filename in expected_per_module_test_files:
        test_path = Path(
            f"/Users/danielzhang/src/iriai/iriai-build-v2/tests/{test_filename}"
        )
        assert test_path.exists(), (
            f"per-module test file {test_filename} missing -- the "
            f"cross-cutting test surface SUPPLEMENTS not REPLACES "
            f"per-module tests"
        )


def test_governance_modules_have_module_level_docstrings() -> None:
    """Sentinel: every governance module MUST have a module-level
    docstring (the docstring carries the doc-N PIN cite block per
    feedback_cite_everything).

    A module without a docstring would silently lose the PIN-cite
    audit trail; this sentinel catches that regression.
    """

    for module_name in GOVERNANCE_MODULES:
        mod = importlib.import_module(module_name)
        docstring = mod.__doc__ or ""
        assert len(docstring.strip()) > 0, (
            f"governance module {module_name} has no module-level "
            f"docstring (violates feedback_cite_everything PIN cite "
            f"discipline)"
        )


# ── Forbidden mutation pattern sentinels (positive coverage) ──────────────


def test_all_forbidden_mutation_prefixes_are_strings() -> None:
    """Sentinel: every entry in FORBIDDEN_MUTATION_METHOD_PREFIXES
    is a non-empty string."""

    for prefix in FORBIDDEN_MUTATION_METHOD_PREFIXES:
        assert isinstance(prefix, str)
        assert len(prefix) > 0
        # Mutation prefixes should end with underscore (so they
        # match `activate_xxx` not `activates_xxx`).
        assert prefix.endswith("_"), (
            f"forbidden mutation prefix {prefix!r} should end with "
            f"underscore (so it matches activate_xxx not "
            f"activates_xxx)"
        )


def test_all_forbidden_consumer_modules_are_strings() -> None:
    """Sentinel: every entry in FORBIDDEN_CONSUMER_MODULES is a
    non-empty string."""

    for module in FORBIDDEN_CONSUMER_MODULES:
        assert isinstance(module, str)
        assert len(module) > 0


def test_all_governance_modules_are_strings() -> None:
    """Sentinel: every entry in GOVERNANCE_MODULES is a non-empty
    string."""

    for module in GOVERNANCE_MODULES:
        assert isinstance(module, str)
        assert len(module) > 0
        assert module.startswith("iriai_build_v2."), (
            f"governance module {module!r} should be under the "
            f"iriai_build_v2 namespace"
        )


# ── No-second-source-of-activation-truth sentinel ─────────────────────────


def test_no_governance_module_redefines_failure_router_route_table() -> None:
    """Sentinel: NO governance module redefines a route table /
    failure type Literal / route action table -- the typed failure
    router is the SOLE source of routing truth.

    Per doc-17:178-179 step 7 + Slice 16 invariant
    ("does not introduce a third route table"), governance modules
    NEVER define `FAILURE_TYPES` / `ROUTE_TABLE` / `ROUTE_ACTIONS`
    / `FAILURE_CLASSES` (these live ONLY in failure_router.py).
    """

    forbidden_redefinitions = (
        "FAILURE_TYPES: tuple",
        "ROUTE_TABLE: ",
        "ROUTE_ACTIONS: ",
        "FAILURE_CLASSES: tuple",
        "_RETRYABLE_FAILURE_TYPES: frozenset",
    )
    for module_name in GOVERNANCE_MODULES:
        source = _load_module_source(module_name)
        for forbidden in forbidden_redefinitions:
            assert forbidden not in source, (
                f"governance module {module_name} redefines the "
                f"failure router constant '{forbidden.split(':')[0]}' "
                f"(violates doc-17:178-179 step 7 + Slice 16 "
                f"no-second-route-table invariant; the typed router "
                f"is the SOLE source of routing truth)"
            )


def test_no_governance_module_redefines_failure_type_literal() -> None:
    """Sentinel: NO governance module redefines `FailureType`
    Literal (the typed alias lives ONLY in failure_router.py).

    Per doc-17:178-179 step 7: the typed failure router is the SOLE
    source of failure-type truth.
    """

    for module_name in GOVERNANCE_MODULES:
        source = _load_module_source(module_name)
        # The FailureType TypeAlias declaration pattern.
        assert "FailureType: TypeAlias = Literal[" not in source, (
            f"governance module {module_name} redefines the "
            f"FailureType TypeAlias (violates doc-17:178-179 step 7 "
            f"+ the typed router is the SOLE source of failure-type "
            f"truth)"
        )


# ── No-second-source-of-activation-truth: status mutation ─────────────────


@pytest.mark.parametrize("module_name", GOVERNANCE_MODULES)
def test_module_does_not_mutate_status_to_activated(
    module_name: str,
) -> None:
    """Doc-17:159-163: `activated` is NOT a
    GovernancePolicyRecommendation.status; NO governance module
    sets `status = "activated"` on any record.

    Activation belongs to a separate consumer-owned policy record;
    governance recommendations CANNOT become runtime policy by
    changing their own row status.
    """

    source = _load_module_source(module_name)
    # Forbidden status-mutation patterns.
    forbidden_patterns = (
        '.status = "activated"',
        ".status = 'activated'",
        'status="activated"',
        "status='activated'",
    )
    for pattern in forbidden_patterns:
        # The patterns above might appear in a docstring or comment
        # legitimately discussing the invariant; we only forbid
        # them in code blocks. A simple heuristic: forbid the
        # pattern only when NOT preceded by `#`, `*`, or a quote.
        # For the structural check we use a stricter rule: the
        # pattern must not appear as an assignment statement at
        # all. Docstrings discussing the invariant should
        # paraphrase rather than mechanically embed the literal
        # mutation.
        if pattern in source:
            # Confirm via line-level analysis: the line must contain
            # the pattern as code (not within a triple-quoted
            # docstring or a `#` comment).
            for line in source.splitlines():
                stripped = line.lstrip()
                if stripped.startswith("#"):
                    continue
                if stripped.startswith("*"):
                    continue
                if pattern in line:
                    # Allow the pattern inside a docstring
                    # `"""..."""` block; the heuristic check is
                    # imprecise for multi-line docstrings, but a
                    # genuine status-mutation would be an
                    # assignment statement -- not a quoted string
                    # inside a docstring.
                    if line.strip().startswith('"""'):
                        continue
                    if line.strip().startswith("'''"):
                        continue
                    # If the line starts with the pattern itself
                    # (e.g. `rec.status = "activated"`) that is the
                    # forbidden mutation.
                    # A robust heuristic: if the line contains `=`
                    # in non-docstring/comment context with the
                    # forbidden pattern on the RHS or LHS.
                    # We err on the side of false-positive: any
                    # non-comment / non-docstring line containing
                    # the pattern is a violation.
                    if "=" in line:
                        pytest.fail(
                            f"governance module {module_name} has "
                            f"line that mutates status to "
                            f"'activated': {line.strip()!r} (violates "
                            f"doc-17:159-163 -- activated NOT a "
                            f"status; activation consumer-owned)"
                        )
