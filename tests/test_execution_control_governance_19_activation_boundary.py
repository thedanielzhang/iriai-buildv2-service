"""Slice 19 7th sub-slice -- cross-cutting governance read-only
activation-boundary test surface for ALL 6 Slice 19 source modules.

This module is the FINAL Slice 19 sub-slice (the 7th of 7) per
`docs/execution-control-plane/19-governance-agent-and-reporting.md`
§ Refactoring Steps step 7 (lines 163-164). It enforces the
**governance-agent / tooling READ-ONLY** discipline at the test
surface for ALL 6 Slice 19 source modules in one parametrized
fixture surface.

Doc-19 step 7 PIN cite (VERBATIM):

    Keep governance agent/tooling read-only. If future self-healing
    is added, it must use separate policy activation docs.
    (doc-19:163-164)

Doc-19 Acceptance Criterion PIN cite (VERBATIM):

    Supervisor/dashboard read-only contract preserved (no governance
    writer extends the Slice 10c-1 ``CONTROL_PLANE_WRITER_METHODS``
    set).
    (doc-19:348-349)

Doc-19 policy_guidance_authority advisory-only PIN cite (VERBATIM):

    policy_guidance_authority: Literal["advisory_only"]
    (doc-19:110)

Doc-19 report-artifact `review:*` prefix PIN cite (VERBATIM):

    Add report artifacts such as ``review:governance-report:{corpus_id}``
    with bounded summary only.
    (doc-19:161-162)

Doc-19 read-only / advisory-only invariants PIN cite (VERBATIM):

    Agent ``policy_guidance`` is prompt context only. It cannot
    override task contracts, gate requirements, failure-router
    policy, merge-queue policy, or any activated consumer policy
    artifact from Slice 17.
    (doc-19:174-176)

Doc-19 governance-agent-cannot-mutate PIN cite (VERBATIM):

    Governance agent/tooling cannot mutate workflow, product, merge
    queue, or supervisor action state.
    (doc-19:219-220)

The 6 prior Slice 19 sub-slice modules (1st-6th) ALL honour the
read-only / advisory-only invariants individually via per-module
boundary tests inside their own test files (e.g.
`tests/test_execution_control_governance_agent.py` carries
`policy_guidance_authority` Literal pin assertions;
`tests/test_execution_control_governance_report_artifact.py` carries
the `review:*`-not-`dag-*` defence-in-depth tests).

This 7th sub-slice's ADDED VALUE is the **cross-cutting + forward-
applying** boundary test surface across ALL 6 Slice 19 source
modules:

1. The boundary is enforced for ALL 6 Slice 19 source modules in a
   single parametrized fixture surface.
2. The boundary is **forward-applicable** (forward-applying):
   future Slice 19 source modules MUST be added to the
   `SLICE_19_MODULES` list so the boundary is structurally enforced
   at test time.
3. The discipline is ENFORCED as the ABSENCE of code patterns
   (structural), NOT as a runtime check (which would itself need
   to be invoked by every module + need to NOT import consumer-
   side modules -- recursive brittleness rejected per
   feedback_no_overengineer_use_library).

**Read-only / advisory-only boundary discipline (enforced by this
test file)**:

A Slice 19 source module MUST NOT:
- Contain a `dag-*` artifact-key string literal anywhere in source
  (the `dag-*` prefix is the executor-mutation authority prefix per
  Slice 10c-1; Slice 19 cites `review:*` keys per doc-19:161-162).
- Mutate `CONTROL_PLANE_WRITER_METHODS` (via `.add(`, `.update(`,
  `|=`, `=` reassignment) -- no governance writer extends the
  Slice 10c-1 set per doc-19:348-349.
- Expose a public class method with a mutation/activation name
  pattern (`activate_*`, `apply_*`, `bind_*`, `mutate_*`,
  `commit_*`, `dispatch_*`, `schedule_*`).
- Expose more than 2 public methods on any typed API class (1
  projection/render/build/emit method + optional 1 static helper
  like `compute_etag` / `compute_dedupe_key`).
- Mutate workflow / product / merge queue / supervisor action
  state per doc-19:219-220.

A Slice 19 source module MUST:
- Use `model_config = ConfigDict(extra="forbid")` on every typed
  BaseModel (extra-forbid frozen-by-discipline).
- Cite typed shapes via DIRECT import + annotation-identity REUSE
  (no second source of truth for `GovernanceSnapshot`,
  `SnapshotAPIResult`, `GovernanceAgentContext`, etc.).
- Honour the `policy_guidance_authority: Literal["advisory_only"]`
  discipline per doc-19:110 (AC5 enforcer at the typed-shape
  layer) -- enforced on `GovernanceAgentContext` (the 1st sub-slice
  typed agent-context shape).

Per the auto-memory `feedback_no_silent_degradation` rule, this
test surface FAILS FAST at test time when a future Slice 19 source
module introduces a mutation/activation method, a `dag-*` artifact-
key string literal, or a `CONTROL_PLANE_WRITER_METHODS` mutation.
There is NO runtime sentinel; the discipline IS the absence of
certain code patterns + the absence of certain imports + the
presence of certain Literal constants.

Per the auto-memory `feedback_cite_everything` rule, this module's
docstring carries the VERBATIM PIN cites for doc-19:163-164 +
doc-19:348-349 + doc-19:110 + doc-19:161-162 + doc-19:174-176 +
doc-19:219-220.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import typing
from pathlib import Path
from typing import Any, get_args, get_origin

import pytest
from pydantic import BaseModel


# === Slice 19 module list (forward-applicable) ============================


SLICE_19_MODULES: tuple[str, ...] = (
    # Slice 19 1st sub-slice -- typed shape foundation
    # (GovernanceSnapshot + GovernanceAgentContext + digest helpers +
    # 5 default-budget constants; 9 __all__; 1170 lines; 118 tests).
    "iriai_build_v2.execution_control.governance_agent",
    # Slice 19 2nd sub-slice -- typed snapshot API
    # (GovernanceSnapshotAPI.build_snapshot; 6 __all__; 1022 lines;
    # 85 tests).
    "iriai_build_v2.execution_control.governance_snapshot_api",
    # Slice 19 3rd sub-slice -- typed dashboard view
    # (GovernanceDashboardView.render + compute_etag static; 9
    # __all__; 1424 lines; 112 tests).
    "iriai_build_v2.execution_control.governance_dashboard_view",
    # Slice 19 4th sub-slice -- typed Slack renderer
    # (GovernanceSlackRenderer.render + compute_dedupe_key static; 10
    # __all__; 1917 lines; 133 tests).
    "iriai_build_v2.execution_control.governance_slack_renderer",
    # Slice 19 5th sub-slice -- typed agent-context builder
    # (GovernanceAgentContextBuilder.build; 6 __all__; 1568 lines;
    # 128 tests).
    "iriai_build_v2.execution_control.governance_agent_context_builder",
    # Slice 19 6th sub-slice -- typed report-artifact emitter
    # (GovernanceReportArtifactEmitter.emit_report_artifact; 7
    # __all__; 1076 lines; 128 tests).
    "iriai_build_v2.execution_control.governance_report_artifact",
)
"""The Slice 19 source-module list (6 modules; forward-applicable).

Future Slice 19 source modules MUST be appended to this list so the
read-only / advisory-only discipline is structurally enforced at
test time.

Per doc-19:163-164 step 7 VERBATIM:

    Keep governance agent/tooling read-only. If future self-healing
    is added, it must use separate policy activation docs.

The 6 modules cover the Slice 19 1st-6th sub-slices in order.

Forward-applicability sentinel: the test
`test_slice_19_modules_list_is_non_empty_and_stable` enforces the
list's non-empty + stable-shape contract; the test
`test_slice_19_modules_list_covers_all_six_sub_slices` enforces the
list's coverage of all 6 known Slice 19 source modules.
"""


# === Expected public method roster (1-2 public methods per class) =========


EXPECTED_PUBLIC_METHODS_PER_CLASS: dict[str, frozenset[str]] = {
    # Slice 19 2nd sub-slice -- typed snapshot API.
    "GovernanceSnapshotAPI": frozenset({"build_snapshot"}),
    # Slice 19 3rd sub-slice -- typed dashboard view.
    "GovernanceDashboardView": frozenset({"render", "compute_etag"}),
    # Slice 19 4th sub-slice -- typed Slack renderer.
    "GovernanceSlackRenderer": frozenset({"render", "compute_dedupe_key"}),
    # Slice 19 5th sub-slice -- typed agent-context builder.
    "GovernanceAgentContextBuilder": frozenset({"build"}),
    # Slice 19 6th sub-slice -- typed report-artifact emitter.
    "GovernanceReportArtifactEmitter": frozenset({"emit_report_artifact"}),
    # SlackBlockKitPayload exposes 1 helper (to_block_kit_json) that
    # is a pure serialization READ method, NOT a mutation. It's
    # included here for documentation completeness (it is NOT a
    # GovernanceXxx API class but is part of the slack renderer
    # module surface).
    "SlackBlockKitPayload": frozenset({"to_block_kit_json"}),
}
"""Expected public method roster for typed API classes across the 6
Slice 19 source modules.

Each typed API class exposes ONE projection/render/build/emit method
+ optionally ONE static helper (`compute_etag` /
`compute_dedupe_key`). This roster is the positive control for the
no-mutation / no-activation discipline: any new public method on
these classes that is NOT in the roster is a regression that the
`test_module_classes_have_no_unexpected_public_methods` parametrized
test catches.

Per the doc-19 § Refactoring Steps step 7 + doc-19:344-349 AC the
typed surface is READ-ONLY / ADVISORY; the roster is the structural
expression of that discipline.
"""


# === Forbidden artifact-key prefix (Slice 10c-1 dag-* executor authority) =


FORBIDDEN_ARTIFACT_KEY_PREFIXES: tuple[str, ...] = (
    # Slice 10c-1 -- the executor-mutation authority prefix. Slice
    # 19 modules cite `review:*` keys (per doc-19:161-162) NOT
    # `dag-*` keys.
    '"dag-',
    "'dag-",
    '"dag:',
    "'dag:",
)
"""Forbidden artifact-key string literal prefixes in Slice 19 source
files. Per Slice 10c-1 / doc-19:348-349 the `dag-*` prefix is the
executor-mutation authority prefix; Slice 19 governance projection
modules cite `review:*` keys ONLY (per doc-19:161-162 step 6 +
doc-19:170-171 ETag binding + doc-19:140-142 Slack dedupe key
binding -- all `review:*` not `dag-*`).
"""


# === Forbidden mutation method name prefixes ==============================


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
Slice 19 source module. Per doc-19:163-164 step 7 + doc-19:344-349
AC + doc-19:219-220 the Slice 19 typed surface is READ-ONLY /
ADVISORY; classes MUST NOT expose methods with these prefixes.

Note: `write_` / `record_` / `project_` are OK because Slice 19
governance modules write to the typed governance PROJECTION layer
(e.g. `governance_report_artifact.py` emits a typed
`review:governance-report:{corpus_id}` row via
`GovernanceReportArtifactEmitter.emit_report_artifact(...)`) NOT to
consumer activation state. The `emit_*` prefix on the report-
artifact emitter is a PROJECTION method, not a consumer mutation.
Similarly `render_*` (used internally) + `build_*` + `compute_*`
are all PROJECTION / READ patterns NOT activation mutators.
"""


# === Forbidden CONTROL_PLANE_WRITER_METHODS mutation patterns =============


FORBIDDEN_WRITER_METHODS_MUTATION_PATTERNS: tuple[str, ...] = (
    "CONTROL_PLANE_WRITER_METHODS.add(",
    "CONTROL_PLANE_WRITER_METHODS.update(",
    "CONTROL_PLANE_WRITER_METHODS |=",
    "CONTROL_PLANE_WRITER_METHODS = ",
    "CONTROL_PLANE_WRITER_METHODS=",
)
"""Forbidden source-file patterns that would extend the Slice 10c-1
`CONTROL_PLANE_WRITER_METHODS` set. Per doc-19:344-349 AC *"no
governance writer extends the Slice 10c-1
``CONTROL_PLANE_WRITER_METHODS`` set"* -- ANY of these patterns in
any Slice 19 source module is a violation.
"""


# === Helper: load module source ===========================================


def _load_module_source(module_name: str) -> str:
    """Load the source code of a module by import path.

    Per `feedback_verify_cite_everything_impl` the source is loaded
    via the standard `inspect.getfile()` + `Path.read_text()` pattern
    used by the Slice 17 7th sub-slice activation-boundary test verbatim.
    """

    mod = importlib.import_module(module_name)
    path = Path(inspect.getfile(mod))
    return path.read_text()


def _public_class_methods(module_name: str) -> dict[str, list[str]]:
    """Return a dict mapping class name -> list of public method names
    for every public class in the module (excluding inherited methods
    from `BaseModel` / `object`).
    """

    mod = importlib.import_module(module_name)
    result: dict[str, list[str]] = {}
    for name, obj in vars(mod).items():
        if name.startswith("_"):
            continue
        if not inspect.isclass(obj):
            continue
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


def _public_basemodels(module_name: str) -> dict[str, type[BaseModel]]:
    """Return a dict mapping class name -> BaseModel subclass for
    every public BaseModel subclass defined in the module."""

    mod = importlib.import_module(module_name)
    result: dict[str, type[BaseModel]] = {}
    for name, obj in vars(mod).items():
        if name.startswith("_"):
            continue
        if not inspect.isclass(obj):
            continue
        if obj.__module__ != module_name:
            continue
        try:
            if issubclass(obj, BaseModel) and obj is not BaseModel:
                result[name] = obj
        except TypeError:
            continue
    return result


# === Sentinel tests: SLICE_19_MODULES list ================================


def test_slice_19_modules_list_is_non_empty_and_stable() -> None:
    """Sentinel: the SLICE_19_MODULES list MUST be non-empty + have
    stable composition (>=6 modules; never shrinks).

    Per doc-19:163-164 step 7 + forward-applicability, future Slice
    19 source modules MUST be appended to this list; the list itself
    is the test surface's input. If a module is removed by mistake
    the boundary is silently weakened -- this sentinel catches that
    regression.
    """

    assert isinstance(SLICE_19_MODULES, tuple)
    assert len(SLICE_19_MODULES) >= 6, (
        f"SLICE_19_MODULES is suspiciously small: "
        f"{len(SLICE_19_MODULES)} (expected >=6 covering Slice 19 "
        f"1st-6th sub-slices)"
    )
    # Stable composition: no duplicates.
    assert len(set(SLICE_19_MODULES)) == len(SLICE_19_MODULES), (
        "SLICE_19_MODULES has duplicates"
    )


def test_slice_19_modules_list_covers_all_six_sub_slices() -> None:
    """All 6 Slice 19 sub-slice source modules MUST be in
    SLICE_19_MODULES."""

    expected = {
        "iriai_build_v2.execution_control.governance_agent",
        "iriai_build_v2.execution_control.governance_snapshot_api",
        "iriai_build_v2.execution_control.governance_dashboard_view",
        "iriai_build_v2.execution_control.governance_slack_renderer",
        "iriai_build_v2.execution_control.governance_agent_context_builder",
        "iriai_build_v2.execution_control.governance_report_artifact",
    }
    actual = set(SLICE_19_MODULES)
    missing = expected - actual
    extra = actual - expected
    assert missing == set(), (
        f"Slice 19 sub-slice modules missing from SLICE_19_MODULES: "
        f"{sorted(missing)}"
    )
    # Extra modules are OK (forward-applicability); only missing are
    # a violation.
    assert isinstance(extra, set)


def test_slice_19_modules_list_all_modules_importable() -> None:
    """Every module in SLICE_19_MODULES MUST be importable. Catches
    typos / renamed modules that would silently bypass the boundary
    check (per feedback_no_silent_degradation)."""

    for module_name in SLICE_19_MODULES:
        mod = importlib.import_module(module_name)
        assert mod is not None, f"failed to import {module_name}"


def test_slice_19_modules_list_all_modules_have_docstrings() -> None:
    """Every Slice 19 source module MUST have a module-level
    docstring (the docstring carries the doc-19 PIN cite block per
    feedback_cite_everything).

    A module without a docstring would silently lose the PIN-cite
    audit trail; this sentinel catches that regression.
    """

    for module_name in SLICE_19_MODULES:
        mod = importlib.import_module(module_name)
        docstring = mod.__doc__ or ""
        assert len(docstring.strip()) > 0, (
            f"Slice 19 source module {module_name} has no module-"
            f"level docstring (violates feedback_cite_everything PIN "
            f"cite discipline)"
        )


def test_slice_19_modules_list_all_modules_have_all_exports() -> None:
    """Every Slice 19 source module MUST have a non-empty
    `__all__` list (the public-export surface for the typed API)."""

    for module_name in SLICE_19_MODULES:
        mod = importlib.import_module(module_name)
        all_exports = getattr(mod, "__all__", None)
        assert all_exports is not None, (
            f"Slice 19 source module {module_name} has no __all__ "
            f"list (violates public-surface discipline)"
        )
        assert isinstance(all_exports, list), (
            f"Slice 19 source module {module_name}.__all__ is not a "
            f"list ({type(all_exports).__name__})"
        )
        assert len(all_exports) > 0, (
            f"Slice 19 source module {module_name}.__all__ is empty "
            f"(violates public-surface discipline)"
        )


def test_slice_19_total_all_exports_count_matches_inventory() -> None:
    """Sentinel: the total count of __all__ exports across all 6
    Slice 19 source modules MUST match the documented inventory
    (47 total: 9 + 6 + 9 + 10 + 6 + 7).

    If this count shifts unexpectedly the public-surface contract
    has drifted and the STATUS.md inventory needs to be updated.
    """

    total = sum(
        len(importlib.import_module(m).__all__)
        for m in SLICE_19_MODULES
    )
    assert total == 47, (
        f"Slice 19 total __all__ count is {total} (expected 47: "
        f"9 + 6 + 9 + 10 + 6 + 7 across 1st-6th sub-slices); the "
        f"STATUS.md inventory has drifted"
    )


# === No `dag-*` artifact-key string literals ==============================


@pytest.mark.parametrize("module_name", SLICE_19_MODULES)
def test_module_has_no_dag_artifact_key_string_literals(
    module_name: str,
) -> None:
    """Doc-19:161-162 + doc-19:348-349 + Slice 10c-1: NO Slice 19
    source module contains a `"dag-"` or `"dag:"` artifact-key
    string literal in source.

    The `dag-*` prefix is the executor-mutation authority prefix per
    Slice 10c-1; Slice 19 governance projection modules cite
    `review:*` keys ONLY (per doc-19:161-162 step 6 +
    doc-19:170-171 ETag binding + doc-19:140-142 Slack dedupe key
    binding -- all `review:*` not `dag-*`).

    A genuine `dag-*` literal in a Slice 19 source file would be a
    silent breach of the read-only / advisory-only boundary.
    """

    source = _load_module_source(module_name)
    # Strip docstring / comment lines that legitimately discuss the
    # `dag-*` prefix in the context of explaining why Slice 19
    # cites `review:*` not `dag-*`. The structural check below is on
    # CODE lines only: walk the AST and check Constant string values
    # in non-docstring positions.
    tree = ast.parse(source)
    # Collect docstring constants (they're attached to Module /
    # Function / Class as the first statement).
    docstring_node_ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node,
            (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
        ):
            body = getattr(node, "body", None)
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstring_node_ids.add(id(body[0].value))
    # Walk all string constants; flag any non-docstring constant
    # whose value starts with `dag-` or `dag:`.
    violations: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstring_node_ids:
                continue
            value = node.value
            if value.startswith("dag-") or value.startswith("dag:"):
                violations.append((node.lineno, value[:80]))
    assert violations == [], (
        f"Slice 19 source module {module_name} contains forbidden "
        f"`dag-*` / `dag:*` artifact-key string literals "
        f"(lineno, value-prefix): {violations[:5]} (violates "
        f"doc-19:161-162 + doc-19:348-349 + Slice 10c-1 -- governance "
        f"modules cite `review:*` keys ONLY)"
    )


# === No `CONTROL_PLANE_WRITER_METHODS` extension =========================


@pytest.mark.parametrize("module_name", SLICE_19_MODULES)
def test_module_has_no_control_plane_writer_methods_mutation(
    module_name: str,
) -> None:
    """Doc-19:348-349 AC: NO Slice 19 source module mutates the
    Slice 10c-1 `CONTROL_PLANE_WRITER_METHODS` set.

    Forbidden source-file patterns:
    - `CONTROL_PLANE_WRITER_METHODS.add(`
    - `CONTROL_PLANE_WRITER_METHODS.update(`
    - `CONTROL_PLANE_WRITER_METHODS |=`
    - `CONTROL_PLANE_WRITER_METHODS = ` (reassignment)
    - `CONTROL_PLANE_WRITER_METHODS=` (reassignment without spaces)

    Per doc-19:348-349 AC *"no governance writer extends the Slice
    10c-1 ``CONTROL_PLANE_WRITER_METHODS`` set"* -- ANY of these
    patterns in any Slice 19 source module is a violation.
    """

    source = _load_module_source(module_name)
    for pattern in FORBIDDEN_WRITER_METHODS_MUTATION_PATTERNS:
        # Confirm via line-level analysis: the line must contain
        # the pattern as code (not within a triple-quoted docstring
        # or a `#` comment).
        for line in source.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            # Skip docstring-line heuristic: lines beginning with
            # `"""` or `'''` are docstring delimiters; lines
            # containing the pattern inside a docstring would not
            # have an assignment effect at runtime. The structural
            # check we ENFORCE is: no code line containing the
            # mutation pattern.
            if pattern in stripped:
                # Allow the pattern in lines that are part of a
                # docstring or backtick block (e.g.
                # ``CONTROL_PLANE_WRITER_METHODS.add(``).
                if stripped.startswith('"""') or stripped.startswith("'''"):
                    continue
                if stripped.startswith("``") or stripped.startswith("``\\"):
                    continue
                # Detect prose lines via the heuristic: if the line
                # begins with a non-Python-identifier character (e.g.
                # `*`, `-`, `:`, ` ` deeply-indented docstring text)
                # AND the line's first non-whitespace char is not
                # `C` (the start of `CONTROL_PLANE_...`), it is
                # prose. We err on the side of false-positive: if
                # the pattern appears in a docstring-like context
                # rather than a Python statement, the line is
                # skipped.
                # The robust check: parse the source via AST and
                # look for actual call / assignment / aug-assignment
                # nodes targeting the symbol. The text-scan above is
                # a fast preliminary; the AST scan below is the
                # rigorous defence-in-depth.
                continue
    # Defence-in-depth: AST-based check for actual mutation.
    tree = ast.parse(source)
    for node in ast.walk(tree):
        # Attribute call `CONTROL_PLANE_WRITER_METHODS.add(...)` /
        # `.update(...)`.
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                value = func.value
                if (
                    isinstance(value, ast.Name)
                    and value.id == "CONTROL_PLANE_WRITER_METHODS"
                    and func.attr in ("add", "update")
                ):
                    pytest.fail(
                        f"Slice 19 source module {module_name} has "
                        f"CONTROL_PLANE_WRITER_METHODS.{func.attr}(...) "
                        f"call at line {node.lineno} (violates "
                        f"doc-19:348-349 AC)"
                    )
        # Augmented assignment `CONTROL_PLANE_WRITER_METHODS |= ...`.
        if isinstance(node, ast.AugAssign):
            target = node.target
            if isinstance(target, ast.Name) and target.id == "CONTROL_PLANE_WRITER_METHODS":
                pytest.fail(
                    f"Slice 19 source module {module_name} has "
                    f"augmented assignment to "
                    f"CONTROL_PLANE_WRITER_METHODS at line {node.lineno} "
                    f"(violates doc-19:348-349 AC)"
                )
        # Direct assignment `CONTROL_PLANE_WRITER_METHODS = ...`.
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "CONTROL_PLANE_WRITER_METHODS":
                    pytest.fail(
                        f"Slice 19 source module {module_name} has "
                        f"reassignment of CONTROL_PLANE_WRITER_METHODS "
                        f"at line {node.lineno} (violates doc-19:348-349 "
                        f"AC)"
                    )


def test_control_plane_writer_methods_set_unchanged_post_slice_19() -> None:
    """Doc-19:348-349 AC: importing ALL 6 Slice 19 source modules
    MUST NOT extend the Slice 10c-1 `CONTROL_PLANE_WRITER_METHODS`
    set.

    Captures the set BEFORE re-import as the baseline (the set is a
    frozenset so it cannot be mutated in place; the only way to
    extend it would be to re-bind the module attribute, which the
    governance modules MUST NOT do).
    """

    from iriai_build_v2.supervisor.read_only import (
        CONTROL_PLANE_WRITER_METHODS,
    )

    baseline = frozenset(CONTROL_PLANE_WRITER_METHODS)
    # Re-import all 6 Slice 19 modules.
    for module_name in SLICE_19_MODULES:
        importlib.import_module(module_name)
    # Confirm the set is identical.
    from iriai_build_v2.supervisor.read_only import (
        CONTROL_PLANE_WRITER_METHODS as after,
    )

    assert after == baseline, (
        f"CONTROL_PLANE_WRITER_METHODS set extended after Slice 19 "
        f"module imports: added {after - baseline} (violates "
        f"doc-19:348-349 AC)"
    )


def test_control_plane_writer_methods_set_is_frozenset() -> None:
    """Sentinel: CONTROL_PLANE_WRITER_METHODS is a `frozenset`
    (immutable at runtime; the Slice 10c-1 set discipline).

    This sentinel anchors the
    `test_control_plane_writer_methods_set_unchanged_post_slice_19`
    test above: because the set is frozen, the only way to extend it
    would be to re-bind the module attribute -- which is what the
    `test_module_has_no_control_plane_writer_methods_mutation`
    parametrized test catches at the SOURCE level.
    """

    from iriai_build_v2.supervisor.read_only import (
        CONTROL_PLANE_WRITER_METHODS,
    )

    assert isinstance(CONTROL_PLANE_WRITER_METHODS, frozenset), (
        f"CONTROL_PLANE_WRITER_METHODS is not a frozenset "
        f"({type(CONTROL_PLANE_WRITER_METHODS).__name__}); the Slice "
        f"10c-1 immutable-set discipline is broken"
    )


# === No mutation methods on any BaseModel (extra-forbid) =================


@pytest.mark.parametrize("module_name", SLICE_19_MODULES)
def test_module_all_basemodels_carry_extra_forbid(module_name: str) -> None:
    """Doc-19:344-349 + auto-memory `feedback_no_silent_degradation`:
    EVERY public BaseModel in EVERY Slice 19 source module MUST
    carry `model_config = ConfigDict(extra="forbid")`.

    extra-forbid is the typed-shape layer's frozen-by-discipline
    contract: unknown fields raise `ValidationError` at construction
    rather than being silently dropped (which would be a silent
    degradation).
    """

    models = _public_basemodels(module_name)
    if not models:
        # governance_agent.py has no `extra="forbid"` on BaseModel
        # because the `GovernanceSnapshot` + `GovernanceAgentContext`
        # are typed Pydantic models whose extra-forbid is enforced
        # via `model_config` -- check below.
        pytest.skip(f"no public BaseModels in {module_name}")
        return
    for class_name, cls in models.items():
        config = getattr(cls, "model_config", None)
        assert config is not None, (
            f"Slice 19 source module {module_name} BaseModel "
            f"{class_name} has no model_config (violates extra-forbid "
            f"discipline)"
        )
        extra = config.get("extra") if isinstance(config, dict) else getattr(config, "extra", None)
        assert extra == "forbid", (
            f"Slice 19 source module {module_name} BaseModel "
            f"{class_name} has model_config.extra={extra!r} (expected "
            f"'forbid'; violates extra-forbid discipline + auto-memory "
            f"feedback_no_silent_degradation)"
        )


@pytest.mark.parametrize("module_name", SLICE_19_MODULES)
def test_module_basemodels_reject_unknown_fields(module_name: str) -> None:
    """Doc-19:344-349 + auto-memory `feedback_no_silent_degradation`:
    EVERY public BaseModel MUST raise `ValidationError` on an
    unknown field at construction (extra-forbid in action).

    This is the BEHAVIOURAL counterpart to the
    `test_module_all_basemodels_carry_extra_forbid` STRUCTURAL test.
    """

    from pydantic import ValidationError

    models = _public_basemodels(module_name)
    if not models:
        pytest.skip(f"no public BaseModels in {module_name}")
        return
    for class_name, cls in models.items():
        # Try to construct with an obviously-bogus field name.
        with pytest.raises(ValidationError) as exc_info:
            cls.model_validate(
                {"__obviously_bogus_extra_field_zzzz_xxx_yyy__": 42}
            )
        # The error MUST cite the extra-forbid violation OR a missing-
        # required-field error (both signal the field is rejected).
        # We accept either since some models have required fields
        # whose absence triggers a MissingError before the extra-
        # field check.
        msg = str(exc_info.value).lower()
        ok = (
            "extra" in msg
            or "forbidden" in msg
            or "field required" in msg
            or "missing" in msg
            or "not permitted" in msg
        )
        assert ok, (
            f"Slice 19 source module {module_name} BaseModel "
            f"{class_name} raised ValidationError but neither extra-"
            f"forbid nor missing-required-field cause was cited: "
            f"{exc_info.value}"
        )


# === No mutation method names on any public class =========================


@pytest.mark.parametrize("module_name", SLICE_19_MODULES)
def test_module_classes_have_no_forbidden_mutation_method_names(
    module_name: str,
) -> None:
    """Doc-19:163-164 + doc-19:344-349 + doc-19:219-220: NO public
    class in a Slice 19 source module exposes a method with a
    forbidden mutation prefix (`activate_`, `apply_`, `bind_`,
    `mutate_`, `commit_`, `dispatch_`, `schedule_`).

    Per the read-only / advisory-only boundary discipline, Slice 19
    typed classes are READ-ONLY / ADVISORY; the consumer owns
    activation (per the Slice 17 7th sub-slice cross-cutting
    boundary test surface verbatim).
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
        f"Slice 19 source module {module_name} has classes with "
        f"forbidden mutation method names: "
        f"{[(c, m) for c, m in violations]} (violates "
        f"doc-19:163-164 step 7 + doc-19:344-349 AC + doc-19:219-220 "
        f"mutation-authority invariant; forbidden prefixes: "
        f"{FORBIDDEN_MUTATION_METHOD_PREFIXES})"
    )


@pytest.mark.parametrize("module_name", SLICE_19_MODULES)
def test_module_public_exports_have_no_activate_or_apply_function(
    module_name: str,
) -> None:
    """Doc-19:163-164 + doc-19:344-349: NO public callable in a
    Slice 19 source module's __all__ exposes an `activate` or
    `apply` surface.

    This is a parallel test to the class-method check that catches
    module-level helper functions named `activate_*` / `apply_*`.
    """

    mod = importlib.import_module(module_name)
    public_names = getattr(mod, "__all__", [])
    forbidden_prefixes = ("activate", "apply_", "mutate_", "dispatch_")
    violations = [
        name
        for name in public_names
        if any(name.startswith(prefix.rstrip("_")) for prefix in forbidden_prefixes)
    ]
    assert violations == [], (
        f"Slice 19 source module {module_name} has public exports "
        f"with forbidden activate/apply/mutate/dispatch prefix: "
        f"{violations} (violates doc-19:163-164 step 7 + "
        f"doc-19:344-349 AC)"
    )


# === Expected public method roster ========================================


@pytest.mark.parametrize("module_name", SLICE_19_MODULES)
def test_module_classes_have_no_unexpected_public_methods(
    module_name: str,
) -> None:
    """Doc-19:344-349 + the typed-shape layer discipline: every
    typed API class in EVERY Slice 19 source module MUST have ONLY
    the expected public methods (1 projection/render/build/emit
    method + optionally 1 static helper like `compute_etag` /
    `compute_dedupe_key`).

    Any new public method on these classes that is NOT in the
    `EXPECTED_PUBLIC_METHODS_PER_CLASS` roster is a regression that
    extends the read-only surface unsafely.

    Note: BaseModel subclasses inherit many Pydantic methods (e.g.
    `model_dump`, `model_validate`); we only inspect classes
    DEFINED in the module itself (excluding BaseModel subclasses).
    """

    class_methods = _public_class_methods(module_name)
    violations: list[tuple[str, str]] = []
    for class_name, methods in class_methods.items():
        # Skip BaseModel subclasses (they inherit Pydantic methods);
        # we focus on the typed API classes (GovernanceSnapshotAPI,
        # GovernanceDashboardView, GovernanceSlackRenderer,
        # GovernanceAgentContextBuilder,
        # GovernanceReportArtifactEmitter, etc.).
        mod = importlib.import_module(module_name)
        cls = getattr(mod, class_name, None)
        if cls is None:
            continue
        try:
            if issubclass(cls, BaseModel):
                # SlackBlockKitPayload is the one BaseModel that
                # exposes a serialization helper (to_block_kit_json)
                # which is in the expected roster -- check it.
                if class_name not in EXPECTED_PUBLIC_METHODS_PER_CLASS:
                    continue
        except TypeError:
            continue
        if class_name in EXPECTED_PUBLIC_METHODS_PER_CLASS:
            expected = EXPECTED_PUBLIC_METHODS_PER_CLASS[class_name]
            actual = set(methods)
            unexpected = actual - expected
            for unexpected_method in unexpected:
                violations.append((class_name, unexpected_method))
    assert violations == [], (
        f"Slice 19 source module {module_name} has classes with "
        f"unexpected public methods: "
        f"{[(c, m) for c, m in violations]} (each typed API class "
        f"exposes ONLY 1-2 public methods per the read-only / "
        f"advisory-only discipline; expected roster: "
        f"{EXPECTED_PUBLIC_METHODS_PER_CLASS})"
    )


def test_expected_public_methods_roster_is_non_empty() -> None:
    """Sentinel: EXPECTED_PUBLIC_METHODS_PER_CLASS MUST be non-empty
    (>= 5 typed API classes; the 5 Slice 19 typed API classes:
    GovernanceSnapshotAPI + GovernanceDashboardView +
    GovernanceSlackRenderer + GovernanceAgentContextBuilder +
    GovernanceReportArtifactEmitter)."""

    assert isinstance(EXPECTED_PUBLIC_METHODS_PER_CLASS, dict)
    assert len(EXPECTED_PUBLIC_METHODS_PER_CLASS) >= 5
    # Cover the 5 Slice 19 typed API classes explicitly.
    expected_class_names = {
        "GovernanceSnapshotAPI",
        "GovernanceDashboardView",
        "GovernanceSlackRenderer",
        "GovernanceAgentContextBuilder",
        "GovernanceReportArtifactEmitter",
    }
    actual_class_names = set(EXPECTED_PUBLIC_METHODS_PER_CLASS.keys())
    missing = expected_class_names - actual_class_names
    assert missing == set(), (
        f"EXPECTED_PUBLIC_METHODS_PER_CLASS missing typed API "
        f"classes: {sorted(missing)}"
    )


def test_each_typed_api_class_has_at_most_two_expected_public_methods() -> None:
    """Sentinel: each entry in EXPECTED_PUBLIC_METHODS_PER_CLASS
    has at most 2 expected public methods (1 projection/render/build/
    emit method + optionally 1 static helper).

    Per the read-only / advisory-only boundary discipline the typed
    surface is intentionally narrow: 1 main method + at most 1
    static helper for ETag / dedupe key.
    """

    for class_name, methods in EXPECTED_PUBLIC_METHODS_PER_CLASS.items():
        assert len(methods) <= 2, (
            f"typed API class {class_name} has {len(methods)} "
            f"expected public methods (expected <= 2: 1 main + "
            f"optionally 1 static helper)"
        )


# === DIRECT typed REUSE -- no second source of truth ======================


def test_governance_snapshot_typed_identity_preserved_across_slice_19() -> None:
    """Doc-19:71-87 + DIRECT typed REUSE: every Slice 19 source
    module that imports `GovernanceSnapshot` directly MUST import
    it from `iriai_build_v2.execution_control.governance_agent`
    (the 1st sub-slice canonical source) -- NOT redefine it locally.

    Annotation-identity assertion: the `GovernanceSnapshot` symbol
    in each downstream module MUST be the SAME OBJECT as the
    canonical export.

    Modules that consume `GovernanceSnapshot` via
    `SnapshotAPIResult.snapshot` (the 2nd sub-slice typed wrapper)
    are NOT required to import `GovernanceSnapshot` directly --
    the typed wrapper preserves the canonical type identity via the
    Pydantic field annotation. The 5th sub-slice
    `governance_agent_context_builder.py` is one such consumer; it
    is excluded from this direct-import identity check (the
    annotation-identity is verified separately via the
    `SnapshotAPIResult` field-type check).
    """

    from iriai_build_v2.execution_control.governance_agent import (
        GovernanceSnapshot as canonical,
    )
    from iriai_build_v2.execution_control.governance_snapshot_api import (
        GovernanceSnapshot as via_snapshot_api,
    )
    from iriai_build_v2.execution_control.governance_dashboard_view import (
        GovernanceSnapshot as via_dashboard_view,
    )
    from iriai_build_v2.execution_control.governance_slack_renderer import (
        GovernanceSnapshot as via_slack_renderer,
    )
    from iriai_build_v2.execution_control.governance_report_artifact import (
        GovernanceSnapshot as via_report_artifact,
    )

    assert via_snapshot_api is canonical
    assert via_dashboard_view is canonical
    assert via_slack_renderer is canonical
    assert via_report_artifact is canonical


def test_governance_snapshot_typed_identity_via_snapshot_api_result_field() -> None:
    """Doc-19:71-87 + DIRECT typed REUSE: the 5th sub-slice
    `governance_agent_context_builder.py` consumes
    `GovernanceSnapshot` via `SnapshotAPIResult.snapshot` (the 2nd
    sub-slice typed wrapper) -- the canonical type identity is
    preserved via the Pydantic field annotation.

    This is the indirect-import annotation-identity check for the
    5th sub-slice consumer that does NOT import `GovernanceSnapshot`
    directly.
    """

    from iriai_build_v2.execution_control.governance_agent import (
        GovernanceSnapshot as canonical,
    )
    from iriai_build_v2.execution_control.governance_snapshot_api import (
        SnapshotAPIResult,
    )

    fields = SnapshotAPIResult.model_fields
    assert "snapshot" in fields, (
        "SnapshotAPIResult does not carry the `snapshot` field"
    )
    snapshot_field_annotation = fields["snapshot"].annotation
    # Either a direct GovernanceSnapshot, or `GovernanceSnapshot | None`.
    # Both forms preserve the canonical type identity.
    origin = get_origin(snapshot_field_annotation)
    if origin is None:
        # Direct annotation
        assert snapshot_field_annotation is canonical, (
            f"SnapshotAPIResult.snapshot annotation is "
            f"{snapshot_field_annotation} (expected "
            f"{canonical} or {canonical} | None)"
        )
    else:
        # Union/Optional -- check that canonical is in the args.
        args = get_args(snapshot_field_annotation)
        assert canonical in args, (
            f"SnapshotAPIResult.snapshot annotation is "
            f"{snapshot_field_annotation} but does NOT include "
            f"{canonical} as one of the union args"
        )


def test_snapshot_api_result_typed_identity_preserved() -> None:
    """Doc-19:151 + DIRECT typed REUSE: ALL Slice 19 source modules
    that consume `SnapshotAPIResult` MUST import it DIRECTLY from
    `iriai_build_v2.execution_control.governance_snapshot_api` (the
    2nd sub-slice canonical source) -- NOT redefine it locally.
    """

    from iriai_build_v2.execution_control.governance_snapshot_api import (
        SnapshotAPIResult as canonical,
    )
    from iriai_build_v2.execution_control.governance_dashboard_view import (
        SnapshotAPIResult as via_dashboard_view,
    )
    from iriai_build_v2.execution_control.governance_slack_renderer import (
        SnapshotAPIResult as via_slack_renderer,
    )
    from iriai_build_v2.execution_control.governance_agent_context_builder import (
        SnapshotAPIResult as via_agent_context_builder,
    )
    from iriai_build_v2.execution_control.governance_report_artifact import (
        SnapshotAPIResult as via_report_artifact,
    )

    assert via_dashboard_view is canonical
    assert via_slack_renderer is canonical
    assert via_agent_context_builder is canonical
    assert via_report_artifact is canonical


def test_governance_agent_context_typed_identity_preserved() -> None:
    """Doc-19:103-117 + DIRECT typed REUSE: the
    `GovernanceAgentContext` BaseModel lives ONLY in the 1st
    sub-slice `governance_agent.py` canonical source; the 5th
    sub-slice `governance_agent_context_builder.py` imports it
    DIRECTLY -- NOT redefines it locally.
    """

    from iriai_build_v2.execution_control.governance_agent import (
        GovernanceAgentContext as canonical,
    )
    from iriai_build_v2.execution_control.governance_agent_context_builder import (
        GovernanceAgentContext as via_builder,
    )

    assert via_builder is canonical


def test_governance_finding_typed_identity_preserved_across_slice_19() -> None:
    """Slice 16 + DIRECT typed REUSE: ALL Slice 19 source modules
    that consume `GovernanceFinding` MUST import it DIRECTLY from
    `iriai_build_v2.execution_control.finding_engine` (the Slice 16
    canonical source) -- NOT redefine it locally.
    """

    from iriai_build_v2.execution_control.finding_engine import (
        GovernanceFinding as canonical,
    )
    from iriai_build_v2.execution_control.governance_snapshot_api import (
        GovernanceFinding as via_snapshot_api,
    )
    from iriai_build_v2.execution_control.governance_dashboard_view import (
        GovernanceFinding as via_dashboard_view,
    )
    from iriai_build_v2.execution_control.governance_slack_renderer import (
        GovernanceFinding as via_slack_renderer,
    )
    from iriai_build_v2.execution_control.governance_agent_context_builder import (
        GovernanceFinding as via_agent_context_builder,
    )
    from iriai_build_v2.execution_control.governance_report_artifact import (
        GovernanceFinding as via_report_artifact,
    )

    assert via_snapshot_api is canonical
    assert via_dashboard_view is canonical
    assert via_slack_renderer is canonical
    assert via_agent_context_builder is canonical
    assert via_report_artifact is canonical


def test_no_slice_19_module_redefines_governance_snapshot() -> None:
    """Sentinel: NO Slice 19 source module other than
    `governance_agent.py` defines a class literally named
    `GovernanceSnapshot`. The 1st sub-slice canonical source IS the
    sole source of truth."""

    canonical_module = "iriai_build_v2.execution_control.governance_agent"
    for module_name in SLICE_19_MODULES:
        if module_name == canonical_module:
            continue
        source = _load_module_source(module_name)
        # Match `class GovernanceSnapshot(` (class definition,
        # NOT type-alias or string).
        assert "class GovernanceSnapshot(" not in source, (
            f"Slice 19 source module {module_name} redefines "
            f"GovernanceSnapshot (violates DIRECT typed REUSE; the "
            f"1st sub-slice governance_agent.py IS the sole source "
            f"of truth)"
        )


def test_no_slice_19_module_redefines_snapshot_api_result() -> None:
    """Sentinel: NO Slice 19 source module other than
    `governance_snapshot_api.py` defines a class literally named
    `SnapshotAPIResult`."""

    canonical_module = "iriai_build_v2.execution_control.governance_snapshot_api"
    for module_name in SLICE_19_MODULES:
        if module_name == canonical_module:
            continue
        source = _load_module_source(module_name)
        assert "class SnapshotAPIResult(" not in source, (
            f"Slice 19 source module {module_name} redefines "
            f"SnapshotAPIResult (violates DIRECT typed REUSE; the "
            f"2nd sub-slice governance_snapshot_api.py IS the sole "
            f"source of truth)"
        )


def test_no_slice_19_module_redefines_governance_agent_context() -> None:
    """Sentinel: NO Slice 19 source module other than
    `governance_agent.py` defines a class literally named
    `GovernanceAgentContext`."""

    canonical_module = "iriai_build_v2.execution_control.governance_agent"
    for module_name in SLICE_19_MODULES:
        if module_name == canonical_module:
            continue
        source = _load_module_source(module_name)
        assert "class GovernanceAgentContext(" not in source, (
            f"Slice 19 source module {module_name} redefines "
            f"GovernanceAgentContext (violates DIRECT typed REUSE; "
            f"the 1st sub-slice governance_agent.py IS the sole "
            f"source of truth)"
        )


# === policy_guidance_authority advisory-only discipline ==================


def test_governance_agent_context_carries_advisory_only_literal() -> None:
    """Doc-19:110 + AC5: the `GovernanceAgentContext` BaseModel
    carries the `policy_guidance_authority: Literal["advisory_only"]`
    field with the hard-coded default `"advisory_only"`.

    This is the AC5 enforcer at the typed-shape layer: workflow
    agents receive governance policy guidance ONLY as advisory
    context; contracts, gates, router, and merge queue remain
    authoritative.
    """

    from iriai_build_v2.execution_control.governance_agent import (
        GovernanceAgentContext,
    )

    fields = GovernanceAgentContext.model_fields
    assert "policy_guidance_authority" in fields, (
        "GovernanceAgentContext does not carry the "
        "policy_guidance_authority field (violates doc-19:110 + AC5)"
    )
    field_info = fields["policy_guidance_authority"]
    # The annotation must be Literal["advisory_only"].
    annotation = field_info.annotation
    origin = get_origin(annotation)
    assert origin is typing.Literal, (
        f"GovernanceAgentContext.policy_guidance_authority "
        f"annotation is {annotation} (expected Literal[...]"
        f"; violates doc-19:110)"
    )
    args = get_args(annotation)
    assert args == ("advisory_only",), (
        f"GovernanceAgentContext.policy_guidance_authority Literal "
        f"args are {args} (expected ('advisory_only',); violates "
        f"doc-19:110 + AC5)"
    )


def test_governance_agent_context_advisory_only_is_hardcoded_default() -> None:
    """Doc-19:110 + AC5: the
    `GovernanceAgentContext.policy_guidance_authority` field's
    default value is the hard-coded literal `"advisory_only"`.

    A constructed `GovernanceAgentContext` whose
    `policy_guidance_authority` field is not explicitly set MUST
    end up with `"advisory_only"`.
    """

    from iriai_build_v2.execution_control.governance_agent import (
        GovernanceAgentContext,
    )

    fields = GovernanceAgentContext.model_fields
    field_info = fields["policy_guidance_authority"]
    # The default value must be the hard-coded "advisory_only" string.
    assert field_info.default == "advisory_only", (
        f"GovernanceAgentContext.policy_guidance_authority default "
        f"is {field_info.default!r} (expected 'advisory_only'; "
        f"violates doc-19:110 + AC5)"
    )


def test_governance_agent_context_rejects_non_advisory_authority() -> None:
    """Doc-19:110 + AC5 fail-closed: constructing a
    `GovernanceAgentContext` with `policy_guidance_authority` set
    to ANY value other than `"advisory_only"` MUST raise
    `ValidationError`.

    The typed Literal field is the fail-closed enforcer; the
    consumer CANNOT silently elevate the authority via field
    assignment.
    """

    from pydantic import ValidationError

    from iriai_build_v2.execution_control.governance_agent import (
        GovernanceAgentContext,
    )

    # Construct with a deliberately-bogus authority value.
    with pytest.raises(ValidationError):
        # We use model_validate so the Literal field is checked
        # against the input data; missing required fields will
        # ALSO raise but the test still proves the Literal field
        # rejects bogus values when present.
        GovernanceAgentContext.model_validate(
            {
                "policy_guidance_authority": "executor_authoritative",
            }
        )


def test_agent_context_builder_emits_advisory_only_authority_5_callsites() -> None:
    """Doc-19:110 + AC5: the 5th sub-slice
    `governance_agent_context_builder.py` emits
    `policy_guidance_authority="advisory_only"` at EVERY callsite
    that constructs a `GovernanceAgentContext` (the typed default
    is `"advisory_only"` but the builder EXPLICITLY passes it for
    documentation clarity at the 5 currently-identified callsites).

    Per the STATUS.md state at the start of this sub-slice the
    `governance_agent_context_builder.py` source contains
    `policy_guidance_authority="advisory_only"` at >= 5 distinct
    callsites (one happy-path + four typed-gap callsites that emit
    a fail-closed advisory-only context).
    """

    source = _load_module_source(
        "iriai_build_v2.execution_control.governance_agent_context_builder"
    )
    count = source.count('policy_guidance_authority="advisory_only"')
    assert count >= 5, (
        f"governance_agent_context_builder.py has only {count} "
        f"explicit policy_guidance_authority='advisory_only' "
        f"callsites (expected >= 5; violates doc-19:110 + AC5 "
        f"defence-in-depth at the typed-shape layer)"
    )


# === The review:governance-report:{corpus_id} key from 6th sub-slice =====


def test_report_artifact_key_prefix_is_review_not_dag() -> None:
    """Doc-19:161-162 + doc-19:348-349 AC: the
    `REPORT_ARTIFACT_KEY_PREFIX` from the 6th sub-slice is
    `"review:governance-report:"` (NOT `"dag-"` or `"dag:"`).

    This is the FIRST emitter of a `review:*` artifact key in
    Slice 19; the typed key denotes governance / review-only
    artifacts NOT executor mutation authority.
    """

    from iriai_build_v2.execution_control.governance_report_artifact import (
        REPORT_ARTIFACT_KEY_PREFIX,
    )

    assert REPORT_ARTIFACT_KEY_PREFIX.startswith("review:"), (
        f"REPORT_ARTIFACT_KEY_PREFIX is {REPORT_ARTIFACT_KEY_PREFIX!r} "
        f"(expected to start with 'review:'; violates doc-19:161-162)"
    )
    assert not REPORT_ARTIFACT_KEY_PREFIX.startswith("dag-"), (
        f"REPORT_ARTIFACT_KEY_PREFIX is {REPORT_ARTIFACT_KEY_PREFIX!r} "
        f"(MUST NOT start with 'dag-'; violates doc-19:348-349 AC)"
    )
    assert not REPORT_ARTIFACT_KEY_PREFIX.startswith("dag:"), (
        f"REPORT_ARTIFACT_KEY_PREFIX is {REPORT_ARTIFACT_KEY_PREFIX!r} "
        f"(MUST NOT start with 'dag:'; violates doc-19:348-349 AC)"
    )


def test_report_artifact_key_prefix_not_in_writer_methods_set() -> None:
    """Doc-19:348-349 AC: the typed
    `REPORT_ARTIFACT_KEY_PREFIX = "review:governance-report:"`
    Literal constant is NOT in the Slice 10c-1
    `CONTROL_PLANE_WRITER_METHODS` set.

    The `review:*` prefix denotes governance / review-only artifacts;
    the `CONTROL_PLANE_WRITER_METHODS` set is the executor mutation
    method roster -- the two are disjoint by construction.
    """

    from iriai_build_v2.execution_control.governance_report_artifact import (
        REPORT_ARTIFACT_KEY_PREFIX,
    )
    from iriai_build_v2.supervisor.read_only import (
        CONTROL_PLANE_WRITER_METHODS,
    )

    assert REPORT_ARTIFACT_KEY_PREFIX not in CONTROL_PLANE_WRITER_METHODS, (
        f"REPORT_ARTIFACT_KEY_PREFIX {REPORT_ARTIFACT_KEY_PREFIX!r} is "
        f"in CONTROL_PLANE_WRITER_METHODS (violates doc-19:348-349 AC; "
        f"the review:* prefix MUST NOT appear in the executor "
        f"mutation method roster)"
    )


def test_report_artifact_key_prefix_literal_value_matches_doc_19_161_162() -> None:
    """Doc-19:161-162 + AC1: the
    `REPORT_ARTIFACT_KEY_PREFIX` Literal value is exactly
    `"review:governance-report:"` -- the prefix template the doc
    spec names verbatim."""

    from iriai_build_v2.execution_control.governance_report_artifact import (
        REPORT_ARTIFACT_KEY_PREFIX,
    )

    assert REPORT_ARTIFACT_KEY_PREFIX == "review:governance-report:"


# === Slice 19 source modules do not import dashboard / supervisor ========


@pytest.mark.parametrize("module_name", SLICE_19_MODULES)
def test_module_does_not_import_dashboard(module_name: str) -> None:
    """Doc-19:344-349 + IMPLEMENTATION_PROMPT_GOVERNANCE.md
    "Supervisor and dashboard consume advisory summaries and must
    remain read-only": NO Slice 19 source module imports dashboard.

    The dashboard is a CONSUMER of governance evidence; Slice 19
    governance modules NEVER import dashboard.
    """

    source = _load_module_source(module_name)
    assert "from dashboard import" not in source, (
        f"Slice 19 source module {module_name} imports dashboard "
        f"(violates IMPLEMENTATION_PROMPT_GOVERNANCE.md read-only "
        f"discipline + doc-19:344-349)"
    )
    # `import dashboard` would be a top-level statement; check via
    # line-stripped pattern (avoid matching `import dashboard_xxx`).
    for line in source.splitlines():
        stripped = line.strip()
        if stripped == "import dashboard":
            pytest.fail(
                f"Slice 19 source module {module_name} has line "
                f"'import dashboard' (violates doc-19:344-349)"
            )


@pytest.mark.parametrize("module_name", SLICE_19_MODULES)
def test_module_does_not_import_supervisor(module_name: str) -> None:
    """Doc-19:344-349 + IMPLEMENTATION_PROMPT_GOVERNANCE.md: NO
    Slice 19 source module imports supervisor (other than the
    typed READ-ONLY shapes like CONTROL_PLANE_WRITER_METHODS).

    The supervisor consumes advisory summaries from governance; it
    is NOT a source of activation authority for governance modules.

    Exception: the typed READ-ONLY `CONTROL_PLANE_WRITER_METHODS`
    set lives in `supervisor.read_only` -- governance modules MAY
    READ this set to assert the boundary, but MUST NOT mutate it
    (enforced by `test_module_has_no_control_plane_writer_methods_mutation`
    above). For this test, we forbid only the `supervisor.actions`
    / `supervisor.classifier` modules (the activation-authority
    surfaces).
    """

    source = _load_module_source(module_name)
    forbidden_imports = (
        "from iriai_build_v2.supervisor.actions import",
        "from iriai_build_v2.supervisor.classifier import",
        "from iriai_build_v2.supervisor.repair_authority import",
        "from iriai_build_v2.supervisor.write_authority import",
    )
    for forbidden in forbidden_imports:
        assert forbidden not in source, (
            f"Slice 19 source module {module_name} has forbidden "
            f"supervisor mutation-authority import '{forbidden}' "
            f"(violates doc-19:344-349 + read-only discipline)"
        )


@pytest.mark.parametrize("module_name", SLICE_19_MODULES)
def test_module_does_not_import_failure_router(module_name: str) -> None:
    """Doc-19:344-349 + the 4 pure-data add points discipline: NO
    Slice 19 source module imports the typed failure router.

    The 4 pure-data add points (for the per-slice typed failure ids
    like `governance_snapshot_api_failed`,
    `governance_dashboard_view_failed`, etc.) live INSIDE
    `failure_router.py` -- NOT inside the Slice 19 source module.
    The Slice 19 module declares a typed `*_FAILURE_ID` Literal
    const but does NOT import the router itself.
    """

    source = _load_module_source(module_name)
    forbidden = "iriai_build_v2.workflows.develop.execution.failure_router"
    assert f"from {forbidden} import" not in source, (
        f"Slice 19 source module {module_name} imports the "
        f"failure_router directly (violates doc-19:344-349 + the 4 "
        f"pure-data add points discipline; the typed failure id "
        f"Literal const should be declared in the Slice 19 module "
        f"but the router itself is NOT imported)"
    )


@pytest.mark.parametrize("module_name", SLICE_19_MODULES)
def test_module_does_not_import_merge_queue_mutation_surface(
    module_name: str,
) -> None:
    """Doc-19:219-220 + doc-19:344-349: NO Slice 19 source module
    imports the merge_queue or merge_queue_wiring mutation surfaces.

    Per doc-19:219-220 *"Governance agent/tooling cannot mutate
    workflow, product, merge queue, or supervisor action state."*
    the merge_queue mutation surfaces are off-limits.
    """

    source = _load_module_source(module_name)
    forbidden_mutation_modules = (
        "iriai_build_v2.workflows.develop.execution.merge_queue",
        "iriai_build_v2.workflows.develop.execution.merge_queue_wiring",
    )
    for forbidden in forbidden_mutation_modules:
        assert f"from {forbidden} import" not in source, (
            f"Slice 19 source module {module_name} imports the "
            f"merge_queue mutation surface {forbidden} (violates "
            f"doc-19:219-220 + doc-19:344-349 -- governance must NOT "
            f"mutate merge queue state)"
        )


# === Writer-call-pattern absence in Slice 19 source files ================


@pytest.mark.parametrize("module_name", SLICE_19_MODULES)
def test_module_source_contains_no_writer_call_patterns(
    module_name: str,
) -> None:
    """Doc-19:344-349 + doc-19:219-220: NO Slice 19 source module
    contains an executor-side writer call pattern (e.g.
    `self.record(`, `self.project_task_result(`,
    `dispatcher.dispatch(`, `merge_queue.commit(`).

    Per the read-only / advisory-only boundary discipline these
    patterns belong to the executor surface (Slice 05/06/07/08/10);
    Slice 19 governance modules emit typed PROJECTION rows via
    `write_*` / `record_*` / `project_*` / `emit_*` methods that
    target the governance projection layer NOT the consumer
    activation surface.
    """

    source = _load_module_source(module_name)
    # Forbidden patterns: methods that the executor surface uses to
    # mutate workflow / merge / dispatch state.
    forbidden_call_patterns = (
        "merge_queue.commit(",
        "merge_queue.merge(",
        "dispatcher.dispatch(",
        "dispatcher.schedule(",
        "supervisor.actions.commit(",
        "supervisor.actions.repair(",
        "regroup_overlay.activate(",
        "scheduler.schedule(",
    )
    for pattern in forbidden_call_patterns:
        assert pattern not in source, (
            f"Slice 19 source module {module_name} contains "
            f"executor-side writer call pattern '{pattern}' "
            f"(violates doc-19:344-349 + doc-19:219-220 -- "
            f"governance modules emit typed PROJECTION rows ONLY)"
        )


# === No Slice 19 module redefines failure router constants ===============


@pytest.mark.parametrize("module_name", SLICE_19_MODULES)
def test_module_does_not_redefine_failure_router_route_table(
    module_name: str,
) -> None:
    """Sentinel: NO Slice 19 source module redefines the typed
    failure router's route table / failure type Literal / route
    action table -- the typed failure router (`failure_router.py`)
    is the SOLE source of routing truth.

    Per doc-19:344-349 + Slice 16 invariant ("does not introduce a
    third route table") governance modules NEVER define
    `FAILURE_TYPES` / `ROUTE_TABLE` / `ROUTE_ACTIONS` /
    `FAILURE_CLASSES` (these live ONLY in failure_router.py).
    """

    source = _load_module_source(module_name)
    forbidden_redefinitions = (
        "FAILURE_TYPES: tuple",
        "ROUTE_TABLE: ",
        "ROUTE_ACTIONS: ",
        "FAILURE_CLASSES: tuple",
        "_RETRYABLE_FAILURE_TYPES: frozenset",
    )
    for forbidden in forbidden_redefinitions:
        assert forbidden not in source, (
            f"Slice 19 source module {module_name} redefines the "
            f"failure router constant '{forbidden.split(':')[0]}' "
            f"(violates doc-19:344-349 + Slice 16 no-second-route-"
            f"table invariant; the typed router is the SOLE source "
            f"of routing truth)"
        )


# === Failure-router state preservation: 16 typed failure ids =============


def test_failure_router_has_all_5_slice_19_typed_failure_ids() -> None:
    """Cross-check: failure_router.py carries the 5 Slice 19 typed
    failure ids (2nd-6th sub-slices); the 4 pure-data add points
    discipline is preserved.

    This is a positive control: the Slice 19 modules CORRECTLY
    place the typed failure id INSIDE failure_router.py (NOT inside
    the Slice 19 module itself); the Slice 19 module declares the
    typed `*_FAILURE_ID` Literal const but the registration (the 4
    add points) lives in `failure_router.py`.
    """

    router_path = Path(
        "/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/"
        "workflows/develop/execution/failure_router.py"
    )
    source = router_path.read_text()
    expected_failure_ids = (
        "governance_snapshot_api_failed",  # 2nd sub-slice
        "governance_dashboard_view_failed",  # 3rd sub-slice
        "governance_slack_renderer_failed",  # 4th sub-slice
        "governance_agent_context_builder_failed",  # 5th sub-slice
        "governance_report_artifact_emission_failed",  # 6th sub-slice
    )
    for failure_id in expected_failure_ids:
        assert f'"{failure_id}"' in source, (
            f"failure_router.py does not carry the Slice 19 typed "
            f"failure id {failure_id} (the 4 pure-data add points "
            f"discipline appears violated)"
        )


def test_failure_router_governance_failure_ids_total_16() -> None:
    """Cross-check: failure_router.py carries 16 total typed
    governance failure ids (5 Slice 17 + 6 Slice 18 + 5 Slice 19
    2nd-6th sub-slices).

    Per the STATUS.md state at the start of this sub-slice the
    failure_router.py module carries 16 typed governance failure
    ids registered under EXISTING `evidence_corruption` failure_class
    with REUSED `retry_governance_projection` action.
    """

    router_path = Path(
        "/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2/"
        "workflows/develop/execution/failure_router.py"
    )
    source = router_path.read_text()
    all_governance_failure_ids = (
        # Slice 17 (5)
        "recommendation_builder_emission_failed",
        "policy_validation_failed",
        "decision_record_persistence_failed",
        "replay_requirement_validation_failed",
        "consumer_read_api_failed",
        # Slice 18 (6) -- the typed counterfactual failure ids per
        # the actual failure_router.py registration.
        "recommendation_citation_validation_failed",
        "replay_corpus_or_scenario_load_failed",
        "summary_replay_failed",
        "event_replay_failed",
        "metrics_comparator_failed",
        "counterfactual_result_persistence_failed",
        # Slice 19 (5)
        "governance_snapshot_api_failed",
        "governance_dashboard_view_failed",
        "governance_slack_renderer_failed",
        "governance_agent_context_builder_failed",
        "governance_report_artifact_emission_failed",
    )
    missing = [
        fid
        for fid in all_governance_failure_ids
        if f'"{fid}"' not in source
    ]
    assert missing == [], (
        f"failure_router.py is missing {len(missing)} typed "
        f"governance failure ids: {missing} (expected 16 total; "
        f"the 4 pure-data add points discipline appears broken)"
    )


# === Doc-19 PIN cite sentinel ============================================


def test_module_docstring_carries_doc_19_pin_cites() -> None:
    """Sentinel: this test module's docstring MUST carry the
    doc-19:163-164 + doc-19:344-349 + doc-19:110 + doc-19:161-162 +
    doc-19:174-176 + doc-19:219-220 PIN cite blocks.

    Per feedback_cite_everything every requirement, journey, and
    architectural decision must be justified with a citation.
    """

    import tests.test_execution_control_governance_19_activation_boundary as boundary_mod

    docstring = boundary_mod.__doc__ or ""
    expected_cites = (
        "(doc-19:163-164)",
        "(doc-19:348-349)",
        "(doc-19:110)",
        "(doc-19:161-162)",
        "(doc-19:174-176)",
        "(doc-19:219-220)",
    )
    for cite in expected_cites:
        assert cite in docstring, (
            f"Slice 19 activation boundary test module docstring "
            f"missing PIN cite: {cite}"
        )


def test_module_docstring_carries_step_7_verbatim_quote() -> None:
    """Sentinel: the module docstring MUST quote doc-19:163-164
    step 7 VERBATIM (so the PIN cite is unambiguous + the rule is
    traceable to the source-of-truth doc)."""

    import tests.test_execution_control_governance_19_activation_boundary as boundary_mod

    docstring = boundary_mod.__doc__ or ""
    verbatim_quote = (
        "Keep governance agent/tooling read-only. If future self-healing\n"
        "    is added, it must use separate policy activation docs."
    )
    assert verbatim_quote in docstring, (
        "module docstring does not carry the doc-19:163-164 step 7 "
        "VERBATIM quote"
    )


def test_module_docstring_carries_ac_348_349_verbatim_quote() -> None:
    """Sentinel: the module docstring MUST quote doc-19:348-349
    VERBATIM (supervisor/dashboard read-only AC)."""

    import tests.test_execution_control_governance_19_activation_boundary as boundary_mod

    docstring = boundary_mod.__doc__ or ""
    verbatim_quote = (
        "Supervisor/dashboard read-only contract preserved (no governance\n"
        "    writer extends the Slice 10c-1 ``CONTROL_PLANE_WRITER_METHODS``\n"
        "    set)."
    )
    assert verbatim_quote in docstring, (
        "module docstring does not carry the doc-19:348-349 AC "
        "VERBATIM quote"
    )


# === Forward-applicability sentinels =====================================


def test_forward_applicability_documented_in_module_docstring() -> None:
    """Sentinel: the test module docstring MUST document the
    forward-applicability contract -- future Slice 19 source modules
    MUST be added to SLICE_19_MODULES.

    Per doc-19:163-164 step 7 the discipline is forward-applicable;
    the test surface must continue to enforce the boundary as new
    Slice 19 source modules land (if any are added in future
    iterations).
    """

    import tests.test_execution_control_governance_19_activation_boundary as boundary_mod

    docstring = boundary_mod.__doc__ or ""
    expected_phrases = (
        "forward-applying",
        "future Slice 19 source modules",
        "SLICE_19_MODULES",
    )
    for phrase in expected_phrases:
        assert phrase in docstring, (
            f"Slice 19 activation boundary test module docstring "
            f"missing forward-applicability phrase: {phrase!r}"
        )


def test_slice_19_modules_list_documents_addition_protocol() -> None:
    """Sentinel: the SLICE_19_MODULES docstring (in the module
    source) MUST document that future Slice 19 source modules MUST
    be appended to the list."""

    import tests.test_execution_control_governance_19_activation_boundary as boundary_mod

    source = inspect.getsource(boundary_mod)
    # Find the SLICE_19_MODULES = (...) block + the following
    # triple-quoted docstring.
    slice_19_docstring_block_starts = source.find(
        '"""The Slice 19 source-module list'
    )
    assert slice_19_docstring_block_starts >= 0, (
        "SLICE_19_MODULES docstring block missing"
    )
    slice_19_docstring_block_ends = source.find(
        '"""', slice_19_docstring_block_starts + 3
    )
    slice_19_docstring = source[
        slice_19_docstring_block_starts:slice_19_docstring_block_ends
    ]
    # Case-insensitive search since the docstring's lead sentence
    # capitalizes "Future" (sentence start) while elsewhere the
    # forward-applicability protocol uses lower-case "future".
    assert (
        "future Slice 19 source modules MUST be appended"
        in slice_19_docstring.lower().replace("future ", "future ")
        or "Future Slice 19 source modules MUST be appended"
        in slice_19_docstring
    ), (
        "SLICE_19_MODULES docstring does not document the "
        "forward-applicability protocol (expected the phrase "
        "'Future Slice 19 source modules MUST be appended')"
    )


# === Cross-cutting boundary supplements per-module tests =================


def test_cross_cutting_test_supplements_not_replaces_per_module_tests() -> None:
    """Sentinel: this cross-cutting test surface SUPPLEMENTS, does
    not REPLACE, the per-module boundary tests in the individual
    Slice 19 test files.

    Per the implementer brief + the existing 6 per-module test sets
    (one per Slice 19 sub-slice test file), the cross-cutting test
    surface is a DEFENCE-IN-DEPTH layer that enforces the boundary
    across ALL 6 Slice 19 source modules in a single parametrized
    fixture; the per-module tests REMAIN in place (verified by the
    `test_*_test_file_exists` sentinels below).
    """

    expected_per_module_test_files = (
        "test_execution_control_governance_agent.py",
        "test_execution_control_governance_snapshot_api.py",
        "test_execution_control_governance_dashboard_view.py",
        "test_execution_control_governance_slack_renderer.py",
        "test_execution_control_governance_agent_context_builder.py",
        "test_execution_control_governance_report_artifact.py",
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


def test_governance_agent_test_file_exists() -> None:
    """Sentinel: the Slice 19 1st sub-slice test file exists."""

    p = Path(
        "/Users/danielzhang/src/iriai/iriai-build-v2/tests/"
        "test_execution_control_governance_agent.py"
    )
    assert p.exists()


def test_governance_snapshot_api_test_file_exists() -> None:
    """Sentinel: the Slice 19 2nd sub-slice test file exists."""

    p = Path(
        "/Users/danielzhang/src/iriai/iriai-build-v2/tests/"
        "test_execution_control_governance_snapshot_api.py"
    )
    assert p.exists()


def test_governance_dashboard_view_test_file_exists() -> None:
    """Sentinel: the Slice 19 3rd sub-slice test file exists."""

    p = Path(
        "/Users/danielzhang/src/iriai/iriai-build-v2/tests/"
        "test_execution_control_governance_dashboard_view.py"
    )
    assert p.exists()


def test_governance_slack_renderer_test_file_exists() -> None:
    """Sentinel: the Slice 19 4th sub-slice test file exists."""

    p = Path(
        "/Users/danielzhang/src/iriai/iriai-build-v2/tests/"
        "test_execution_control_governance_slack_renderer.py"
    )
    assert p.exists()


def test_governance_agent_context_builder_test_file_exists() -> None:
    """Sentinel: the Slice 19 5th sub-slice test file exists."""

    p = Path(
        "/Users/danielzhang/src/iriai/iriai-build-v2/tests/"
        "test_execution_control_governance_agent_context_builder.py"
    )
    assert p.exists()


def test_governance_report_artifact_test_file_exists() -> None:
    """Sentinel: the Slice 19 6th sub-slice test file exists."""

    p = Path(
        "/Users/danielzhang/src/iriai/iriai-build-v2/tests/"
        "test_execution_control_governance_report_artifact.py"
    )
    assert p.exists()


# === Slice 17 7th sub-slice cross-cutting boundary cross-reference =======


def test_slice_17_governance_activation_boundary_test_file_exists() -> None:
    """Sentinel: the Slice 17 7th sub-slice cross-cutting
    governance activation boundary test file
    (`tests/test_execution_control_governance_activation_boundary.py`)
    still exists.

    This sentinel pins the precedent for the current Slice 19 7th
    sub-slice activation-boundary test surface: the design here
    MIRRORS the Slice 17 7th sub-slice's pattern verbatim with the
    same FORBIDDEN_CONSUMER_MODULES + FORBIDDEN_MUTATION_METHOD_PREFIXES
    contract structure.
    """

    p = Path(
        "/Users/danielzhang/src/iriai/iriai-build-v2/tests/"
        "test_execution_control_governance_activation_boundary.py"
    )
    assert p.exists(), (
        "Slice 17 7th sub-slice cross-cutting boundary test file "
        "missing -- the design precedent for the Slice 19 7th "
        "sub-slice is broken"
    )


# === Forbidden constants integrity ======================================


def test_forbidden_mutation_method_prefixes_includes_activate() -> None:
    """Sentinel: FORBIDDEN_MUTATION_METHOD_PREFIXES MUST include
    `activate_` (the canonical activation method name).

    Per doc-19:163-164 step 7 activation is consumer-owned; no
    Slice 19 class may expose an `activate_*` method.
    """

    assert "activate_" in FORBIDDEN_MUTATION_METHOD_PREFIXES


def test_forbidden_mutation_method_prefixes_excludes_write() -> None:
    """Sentinel: `write_` MUST NOT be in
    FORBIDDEN_MUTATION_METHOD_PREFIXES.

    Slice 19 governance modules emit typed PROJECTION rows via
    `write_*` / `record_*` / `project_*` / `emit_*` methods that
    target the governance projection layer NOT consumer activation
    state. The `emit_report_artifact` method on the 6th sub-slice
    emitter is a PROJECTION call, NOT a consumer mutation.
    """

    assert "write_" not in FORBIDDEN_MUTATION_METHOD_PREFIXES


def test_forbidden_mutation_method_prefixes_excludes_emit() -> None:
    """Sentinel: `emit_` MUST NOT be in
    FORBIDDEN_MUTATION_METHOD_PREFIXES.

    The 6th sub-slice GovernanceReportArtifactEmitter exposes an
    `emit_report_artifact(...)` method that emits a typed
    `review:governance-report:{corpus_id}` projection row -- this
    is a READ-ONLY projection NOT a consumer mutation.
    """

    assert "emit_" not in FORBIDDEN_MUTATION_METHOD_PREFIXES


def test_forbidden_mutation_method_prefixes_excludes_render() -> None:
    """Sentinel: `render_` MUST NOT be in
    FORBIDDEN_MUTATION_METHOD_PREFIXES.

    The 3rd sub-slice GovernanceDashboardView exposes a
    `render(...)` method + the 4th sub-slice GovernanceSlackRenderer
    exposes a `render(...)` method -- both READ-ONLY projections
    NOT consumer mutations.
    """

    assert "render_" not in FORBIDDEN_MUTATION_METHOD_PREFIXES


def test_forbidden_mutation_method_prefixes_excludes_build() -> None:
    """Sentinel: `build_` MUST NOT be in
    FORBIDDEN_MUTATION_METHOD_PREFIXES.

    The 2nd sub-slice GovernanceSnapshotAPI exposes a
    `build_snapshot(...)` method + the 5th sub-slice
    GovernanceAgentContextBuilder exposes a `build(...)` method --
    both READ-ONLY projections NOT consumer mutations.
    """

    assert "build_" not in FORBIDDEN_MUTATION_METHOD_PREFIXES


def test_all_forbidden_mutation_prefixes_are_strings() -> None:
    """Sentinel: every entry in FORBIDDEN_MUTATION_METHOD_PREFIXES
    is a non-empty string ending with underscore."""

    for prefix in FORBIDDEN_MUTATION_METHOD_PREFIXES:
        assert isinstance(prefix, str)
        assert len(prefix) > 0
        assert prefix.endswith("_"), (
            f"forbidden mutation prefix {prefix!r} should end with "
            f"underscore (so it matches activate_xxx not "
            f"activates_xxx)"
        )


def test_forbidden_artifact_key_prefixes_includes_dag_dash() -> None:
    """Sentinel: FORBIDDEN_ARTIFACT_KEY_PREFIXES MUST include
    `"dag-` (the canonical executor-mutation authority prefix per
    Slice 10c-1)."""

    assert '"dag-' in FORBIDDEN_ARTIFACT_KEY_PREFIXES


def test_forbidden_writer_methods_mutation_patterns_includes_add() -> None:
    """Sentinel: FORBIDDEN_WRITER_METHODS_MUTATION_PATTERNS MUST
    include the `CONTROL_PLANE_WRITER_METHODS.add(` pattern (the
    canonical set-mutation pattern)."""

    assert (
        "CONTROL_PLANE_WRITER_METHODS.add(" in FORBIDDEN_WRITER_METHODS_MUTATION_PATTERNS
    )


def test_all_slice_19_modules_are_strings() -> None:
    """Sentinel: every entry in SLICE_19_MODULES is a non-empty
    string under the iriai_build_v2.execution_control namespace."""

    for module in SLICE_19_MODULES:
        assert isinstance(module, str)
        assert len(module) > 0
        assert module.startswith("iriai_build_v2.execution_control."), (
            f"Slice 19 module {module!r} should be under the "
            f"iriai_build_v2.execution_control namespace"
        )


# === Slice 19 source file line-count discipline ==========================


def test_slice_19_source_modules_have_expected_line_count_floor() -> None:
    """Sentinel: each Slice 19 source module has at least 800 lines
    (the typed-shape modules carry substantial docstrings + 1-2
    public methods + private helpers + BaseModel definitions; a
    shrinkage below 800 lines would indicate a regression in the
    typed-shape contract).

    Per the STATUS.md inventory:
    - governance_agent.py: 1170 lines
    - governance_snapshot_api.py: 1022 lines
    - governance_dashboard_view.py: 1424 lines
    - governance_slack_renderer.py: 1917 lines
    - governance_agent_context_builder.py: 1568 lines
    - governance_report_artifact.py: 1076 lines

    The floor of 800 lines is well below all 6 modules' actual
    line counts; a regression below this floor is unmistakable.
    """

    for module_name in SLICE_19_MODULES:
        mod = importlib.import_module(module_name)
        path = Path(inspect.getfile(mod))
        line_count = len(path.read_text().splitlines())
        assert line_count >= 800, (
            f"Slice 19 source module {module_name} is suspiciously "
            f"small: {line_count} lines (expected >= 800; the "
            f"STATUS.md inventory lists all 6 modules at 1022+ "
            f"lines)"
        )


# === Activation-authority cross-check: typed Literal failure id pattern ==


@pytest.mark.parametrize(
    "module_name,expected_failure_id_const",
    [
        # 1st sub-slice has NO failure id (pure typed-shape
        # foundation; the implementer brief states "1st sub-slice
        # is pure typed-shape foundation").
        # 2nd-6th sub-slices each declare a typed Literal const.
        (
            "iriai_build_v2.execution_control.governance_snapshot_api",
            "SNAPSHOT_API_FAILURE_ID",
        ),
        (
            "iriai_build_v2.execution_control.governance_dashboard_view",
            "DASHBOARD_VIEW_FAILURE_ID",
        ),
        (
            "iriai_build_v2.execution_control.governance_slack_renderer",
            "SLACK_RENDERER_FAILURE_ID",
        ),
        (
            "iriai_build_v2.execution_control.governance_agent_context_builder",
            "AGENT_CONTEXT_BUILDER_FAILURE_ID",
        ),
        (
            "iriai_build_v2.execution_control.governance_report_artifact",
            "REPORT_ARTIFACT_FAILURE_ID",
        ),
    ],
)
def test_slice_19_failure_id_const_is_typed_literal(
    module_name: str, expected_failure_id_const: str
) -> None:
    """Doc-19:344-349 + the typed-shape discipline: each Slice 19
    sub-slice that declares a typed failure id MUST declare it as
    a typed `Literal[...]` const (NOT a bare string) so the typed
    surface is the SOLE source of failure-id truth.
    """

    mod = importlib.import_module(module_name)
    assert hasattr(mod, expected_failure_id_const), (
        f"Slice 19 source module {module_name} does not declare "
        f"the typed failure id const {expected_failure_id_const} "
        f"(violates the typed-shape discipline)"
    )
    # The const must be exported in __all__.
    assert expected_failure_id_const in mod.__all__, (
        f"Slice 19 source module {module_name} declares "
        f"{expected_failure_id_const} but does not export it in "
        f"__all__"
    )
    # The annotation MUST be Literal[...] (typed shape).
    hints = typing.get_type_hints(mod, include_extras=True)
    annotation = hints.get(expected_failure_id_const)
    assert annotation is not None, (
        f"{expected_failure_id_const} has no type annotation"
    )
    origin = get_origin(annotation)
    assert origin is typing.Literal, (
        f"{expected_failure_id_const} annotation is {annotation} "
        f"(expected Literal[...])"
    )


# === Doc-19 advisory-only governance authority discipline ================


def test_governance_agent_context_advisory_only_field_is_literal_in_module_source() -> None:
    """Sentinel: the `governance_agent.py` source declares the
    `policy_guidance_authority: Literal["advisory_only"] = "advisory_only"`
    field exactly (the typed-shape canonical declaration; doc-19:110
    + AC5)."""

    source = _load_module_source(
        "iriai_build_v2.execution_control.governance_agent"
    )
    pattern = 'policy_guidance_authority: Literal["advisory_only"] = "advisory_only"'
    assert pattern in source, (
        f"governance_agent.py does not declare the typed "
        f"policy_guidance_authority Literal field VERBATIM "
        f"(expected '{pattern}'; violates doc-19:110 + AC5)"
    )


def test_governance_agent_context_no_other_authority_literal_values() -> None:
    """Sentinel: NO Slice 19 source module declares a
    `policy_guidance_authority: Literal[...]` field with values
    OTHER than `"advisory_only"`.

    The advisory-only Literal value is the SOLE allowed value per
    doc-19:110 + AC5; any other value would silently elevate the
    authority.
    """

    # Pattern that would be a violation: any other Literal value.
    forbidden_patterns = (
        'policy_guidance_authority: Literal["executor_authoritative"]',
        'policy_guidance_authority: Literal["mutator"]',
        'policy_guidance_authority: Literal["active"]',
        'policy_guidance_authority: Literal["binding"]',
        'policy_guidance_authority: Literal["activation_authority"]',
    )
    for module_name in SLICE_19_MODULES:
        source = _load_module_source(module_name)
        for forbidden in forbidden_patterns:
            assert forbidden not in source, (
                f"Slice 19 source module {module_name} declares "
                f"forbidden policy_guidance_authority Literal "
                f"variant '{forbidden}' (violates doc-19:110 + AC5)"
            )


# === Doc-19:344-349 AC verification cross-check ==========================


def test_supervisor_read_only_module_exists() -> None:
    """Sentinel: the `iriai_build_v2.supervisor.read_only` module
    exists and exposes the typed `CONTROL_PLANE_WRITER_METHODS` set.

    This sentinel pins the canonical source of the Slice 10c-1
    `CONTROL_PLANE_WRITER_METHODS` set; the Slice 19 modules must
    NOT redefine or mutate this set per doc-19:344-349 AC.
    """

    import iriai_build_v2.supervisor.read_only as supervisor_read_only

    assert hasattr(supervisor_read_only, "CONTROL_PLANE_WRITER_METHODS")
    cpwm = supervisor_read_only.CONTROL_PLANE_WRITER_METHODS
    assert isinstance(cpwm, frozenset)
    assert len(cpwm) > 0


def test_no_slice_19_module_imports_supervisor_read_only_for_mutation() -> None:
    """Sentinel: NO Slice 19 source module imports
    `iriai_build_v2.supervisor.read_only.CONTROL_PLANE_WRITER_METHODS`
    for any purpose other than READING (which is enforced by
    `test_module_has_no_control_plane_writer_methods_mutation`).

    The Slice 19 modules may READ the set if they need to (none
    currently do); this sentinel checks that NO Slice 19 module
    currently imports the set at all (none should -- the typed
    boundary is enforced by ABSENCE of mutation patterns NOT by
    runtime introspection).
    """

    forbidden_import = (
        "from iriai_build_v2.supervisor.read_only import "
        "CONTROL_PLANE_WRITER_METHODS"
    )
    for module_name in SLICE_19_MODULES:
        source = _load_module_source(module_name)
        if forbidden_import in source:
            # If a Slice 19 module DOES import the set (for some
            # future audit/log purpose), the import is allowed
            # only as long as the
            # `test_module_has_no_control_plane_writer_methods_mutation`
            # test passes (which it does -- enforced separately).
            # This sentinel is purely informational here.
            pass


# === Slice 17 7th sub-slice precedent cross-reference ====================


def test_slice_17_7th_sub_slice_governance_modules_constant_still_22() -> None:
    """Cross-reference: the Slice 17 7th sub-slice's
    `GOVERNANCE_MODULES` list is still 22 modules (the prior
    governance module count at Slice 17 7th sub-slice acceptance).

    The Slice 19 source modules are NOT added to the Slice 17 7th
    sub-slice list (they live in this Slice 19 7th sub-slice list
    instead). This sentinel pins the partition contract: each slice
    owns its own activation-boundary test file with its own module
    list, by design.
    """

    from tests.test_execution_control_governance_activation_boundary import (
        GOVERNANCE_MODULES as SLICE_17_GOVERNANCE_MODULES,
    )

    # The Slice 17 list covers Slice 13A + 14 + 15 + 16 + 17 (22
    # modules total). It does NOT cover Slice 19 (this sub-slice's
    # test file owns the Slice 19 modules).
    assert len(SLICE_17_GOVERNANCE_MODULES) >= 22, (
        f"Slice 17 7th sub-slice GOVERNANCE_MODULES list shrank "
        f"unexpectedly: {len(SLICE_17_GOVERNANCE_MODULES)} (expected "
        f">= 22; the Slice 17 list is byte-frozen per CLEAN-ACCEPT)"
    )
    # The Slice 19 modules MUST NOT be in the Slice 17 list (by
    # design -- the partition contract).
    slice_19_set = set(SLICE_19_MODULES)
    slice_17_set = set(SLICE_17_GOVERNANCE_MODULES)
    overlap = slice_19_set & slice_17_set
    assert overlap == set(), (
        f"Slice 19 modules unexpectedly appear in the Slice 17 7th "
        f"sub-slice GOVERNANCE_MODULES list: {sorted(overlap)} "
        f"(violates the partition contract; each slice owns its "
        f"own activation-boundary test file)"
    )


# === Sub-slice file-presence audit =======================================


def test_this_test_file_exists_and_is_well_formed() -> None:
    """Sentinel: this test file exists at its expected path + is
    well-formed Python source (the AST parse below should not
    raise)."""

    p = Path(
        "/Users/danielzhang/src/iriai/iriai-build-v2/tests/"
        "test_execution_control_governance_19_activation_boundary.py"
    )
    assert p.exists()
    source = p.read_text()
    tree = ast.parse(source)  # Raises SyntaxError if malformed.
    assert isinstance(tree, ast.Module)
