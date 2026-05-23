"""Slice 11h -- extraction proof for `execution/repair.py` extension.

Verifies the doc-11 § "How To Use This Map" four-question contract for
the pure repair-domain primitive extraction (the eighth in Slice 11):

1. What behavior moved: 24 pure helpers + constants -- 7 route constants
   (`_COMMIT_HYGIENE_ROUTE`, `_MANIFEST_FORBIDDEN_CLEANUP_ROUTE`,
   `_REPO_HYGIENE_ROUTE`, `_NORMAL_VERIFY_ROUTE`,
   `_MANIFEST_FORBIDDEN_MARKER`, `_OPERATOR_REQUIRED_MARKER`,
   `_DAG_CONTRADICTION_MIXED_REPAIR_KIND`), 8 direct-route classifiers
   (`_classify_dag_direct_repair_route`, `_commit_failure_issue_kind`,
   `_direct_route_failure_pair`, `_direct_route_issue_operator_required`,
   `_direct_route_target`, `_direct_route_target_paths`,
   `_is_deterministic_dag_preflight_issue`,
   `_normalize_direct_route_signature`), 5 pure artifact-key classifiers
   (`_is_dag_artifact_repair_key`, `_is_dag_artifact_repair_path`,
   `_is_dag_task_artifact_key`, `_is_derived_dag_artifact_key`,
   `_normalize_dag_artifact_repair_ref`), and 4 repair-misc primitives
   (`_dag_contradiction_fix_guidance`,
   `_dag_contradiction_needs_artifact_repair`,
   `_dag_product_cleanup_ready_for_artifact_repair`,
   `_post_dag_repair_group_idx`) -- all moved from
   `workflows/develop/phases/implementation.py` to
   `workflows/develop/execution/repair.py`. The pre-11h Slice-08 surface
   in `repair.py` (the `RouteExecutor`, `RepairRequest`/`RepairOutcome`
   Pydantic models, `RetryRequest`/`RetryOutcome` Pydantic models,
   `RouteExecutorError`, 8 deterministic scope/budget/idempotency-key
   builders, `_authorized_direct_source_verdict` +
   `_authorized_product_repair_source_verdict`, and `stable_digest`) is
   UNTOUCHED -- Slice 11h EXTENDS, never modifies.

2. Which legacy import names still work: every existing
   `from iriai_build_v2.workflows.develop.phases.implementation import X`
   for one of the 24 moved names keeps resolving to the SAME object as
   the canonical definition in `execution/repair.py` (the shim is
   `is`-equivalent, not a copy). `monkeypatch.setattr(implementation_
   module, X, ...)` continues to mutate the SAME binding any direct
   `from execution.repair import X` reader sees.

3. Which targeted tests prove the new facade and the compatibility shim:
   THIS file is one of them; it pins every moved name's shim equivalence
   and behaviorally smoke-tests each moved helper.

4. Why is the PR still refactor-only: nothing else moves. The 24 pure
   primitives moved byte-for-byte. The phase-level repair PORT surface
   (the async runner+feature/store-coupled `_record_dag_direct_repair_
   route` / `_attempt_parallel_dag_repair` / `_attempt_dag_authority_
   gate_repair` / `_run_dag_artifact_repair_lane` / `_apply_dag_
   artifact_repair_updates` / `_bind_repair_sandbox`; the env-coupled
   `_dag_parallel_repair_enabled` / `_dag_preflight_repair_enabled`; the
   impl.py-local `_dedupe_preserving_order`-coupled text-scanner cluster
   `_dag_artifact_repair_refs_from_text` /
   `_dag_artifact_repair_refs_from_planned` /
   `_dag_artifact_repair_target_refs`; the impl.py-local
   `_safe_context_stem` / `_workflow_blocker_text`-coupled factories
   `_dag_artifact_repair_synthetic_result` /
   `_dag_repair_task_failed_result`; the impl.py-local path classifier
   `_classify_dag_repair_path` / `_sanitize_dag_repair_result`; the
   `_repair_task_for_rca` / `_normalize_repair_contract_path` /
   `_repair_contract_for_paths` repair-task factories; the
   `_repair_sandbox_required` runner-services-coupled predicate; and the
   `_contract_guard_results_for_repair` results-rewriter) is genuinely
   PHASE-LEVEL and CORRECTLY stays in `implementation.py` per the prompt
   hard rule against splitting non-pure helpers.
"""

from __future__ import annotations

from pathlib import Path

import pytest


# Each entry is a name moved from `implementation.py` to
# `execution/repair.py` in Slice 11h. The order matches the import-line
# order in the shim block in `implementation.py` (the Slice-11h block).
MOVED_NAMES = [
    # 7 route constants
    "_COMMIT_HYGIENE_ROUTE",
    "_DAG_CONTRADICTION_MIXED_REPAIR_KIND",
    "_MANIFEST_FORBIDDEN_CLEANUP_ROUTE",
    "_MANIFEST_FORBIDDEN_MARKER",
    "_NORMAL_VERIFY_ROUTE",
    "_OPERATOR_REQUIRED_MARKER",
    "_REPO_HYGIENE_ROUTE",
    # 8 direct-route classifiers
    "_classify_dag_direct_repair_route",
    "_commit_failure_issue_kind",
    "_dag_contradiction_fix_guidance",
    "_dag_contradiction_needs_artifact_repair",
    "_dag_product_cleanup_ready_for_artifact_repair",
    "_direct_route_failure_pair",
    "_direct_route_issue_operator_required",
    "_direct_route_target",
    "_direct_route_target_paths",
    "_is_dag_artifact_repair_key",
    "_is_dag_artifact_repair_path",
    "_is_dag_task_artifact_key",
    "_is_derived_dag_artifact_key",
    "_is_deterministic_dag_preflight_issue",
    "_normalize_dag_artifact_repair_ref",
    "_normalize_direct_route_signature",
    "_post_dag_repair_group_idx",
]


# The 7 route constants are `str` instances which carry no per-binding
# `__module__` attribute (only the `str` type does). Only the 17
# callables are `__module__`-checkable.
MOVED_CALLABLES = [
    "_classify_dag_direct_repair_route",
    "_commit_failure_issue_kind",
    "_dag_contradiction_fix_guidance",
    "_dag_contradiction_needs_artifact_repair",
    "_dag_product_cleanup_ready_for_artifact_repair",
    "_direct_route_failure_pair",
    "_direct_route_issue_operator_required",
    "_direct_route_target",
    "_direct_route_target_paths",
    "_is_dag_artifact_repair_key",
    "_is_dag_artifact_repair_path",
    "_is_dag_task_artifact_key",
    "_is_derived_dag_artifact_key",
    "_is_deterministic_dag_preflight_issue",
    "_normalize_dag_artifact_repair_ref",
    "_normalize_direct_route_signature",
    "_post_dag_repair_group_idx",
]


@pytest.mark.parametrize("name", MOVED_NAMES)
def test_shim_re_export_is_same_object_as_new_canonical(name: str) -> None:
    """Every moved helper imported via the OLD path is the SAME object
    as the import via the NEW canonical path. Proves the shim is a re-
    export, not a copy. Locks the monkeypatch target equivalence --
    `monkeypatch.setattr(implementation_module, name, ...)` will mutate
    the SAME function object that any direct
    `from execution.repair import name` reader sees.
    """

    from iriai_build_v2.workflows.develop import execution as execution_pkg
    from iriai_build_v2.workflows.develop.execution import repair as repair_mod
    from iriai_build_v2.workflows.develop.phases import implementation as impl_mod

    legacy = getattr(impl_mod, name)
    canonical = getattr(repair_mod, name)
    assert legacy is canonical, (
        f"shim drift: implementation.{name} is not the same object as "
        f"execution.repair.{name}"
    )
    _ = execution_pkg


@pytest.mark.parametrize("name", MOVED_CALLABLES)
def test_canonical_module_is_repair(name: str) -> None:
    """The moved function objects' `__module__` is the new canonical
    `iriai_build_v2.workflows.develop.execution.repair` -- not the
    legacy `...phases.implementation`. Proves the definition genuinely
    moved rather than being re-aliased from the old module.
    """

    from iriai_build_v2.workflows.develop.execution import repair as repair_mod

    canonical = getattr(repair_mod, name)
    assert canonical.__module__ == (
        "iriai_build_v2.workflows.develop.execution.repair"
    ), (
        f"{name}.__module__ = {canonical.__module__!r}; expected the new "
        "repair-module path"
    )


def test_route_constants_have_canonical_values() -> None:
    """The 7 route constants are pure str literals pinned to their
    established workflow-payload values. Two of them
    (`_NORMAL_VERIFY_ROUTE`, `_MANIFEST_FORBIDDEN_CLEANUP_ROUTE`) share
    their literal values with the pre-11h Slice-08
    `_DIRECT_PRODUCT_REPAIR_ROUTES` /
    `_DIRECT_CONTRACT_PRODUCT_REPAIR_ROUTES` frozensets in the same
    module -- the test locks both the value and the cross-frozenset
    consistency.
    """

    from iriai_build_v2.workflows.develop.execution.repair import (
        _COMMIT_HYGIENE_ROUTE,
        _DAG_CONTRADICTION_MIXED_REPAIR_KIND,
        _DIRECT_CONTRACT_PRODUCT_REPAIR_ROUTES,
        _DIRECT_PRODUCT_REPAIR_ROUTES,
        _MANIFEST_FORBIDDEN_CLEANUP_ROUTE,
        _MANIFEST_FORBIDDEN_MARKER,
        _NORMAL_VERIFY_ROUTE,
        _OPERATOR_REQUIRED_MARKER,
        _REPO_HYGIENE_ROUTE,
    )

    assert _COMMIT_HYGIENE_ROUTE == "commit_hygiene_focused"
    assert _MANIFEST_FORBIDDEN_CLEANUP_ROUTE == "manifest_forbidden_product_cleanup"
    assert _REPO_HYGIENE_ROUTE == "repo_hygiene_operator"
    assert _NORMAL_VERIFY_ROUTE == "normal_verify_repair"
    assert _MANIFEST_FORBIDDEN_MARKER == "manifest-forbidden product cleanup"
    assert _OPERATOR_REQUIRED_MARKER == "operator_required=true"
    assert _DAG_CONTRADICTION_MIXED_REPAIR_KIND == "mixed_repair"

    # Cross-frozenset consistency: the pre-11h Slice-08 frozensets
    # reference the SAME literal values.
    assert _NORMAL_VERIFY_ROUTE in _DIRECT_PRODUCT_REPAIR_ROUTES
    assert _MANIFEST_FORBIDDEN_CLEANUP_ROUTE in _DIRECT_CONTRACT_PRODUCT_REPAIR_ROUTES


def test_post_dag_repair_group_idx_determinism() -> None:
    """`_post_dag_repair_group_idx(source, attempt_number)` returns a
    deterministic int in `[100_000, 200_000)` based on a sha256 digest
    of `f"{source}:{attempt_number}"`. Same input -> same output;
    different inputs typically produce different outputs.
    """

    from iriai_build_v2.workflows.develop.execution.repair import (
        _post_dag_repair_group_idx,
    )

    idx = _post_dag_repair_group_idx("post-dag", 1)
    assert isinstance(idx, int)
    assert 100_000 <= idx < 200_000
    # Same input -> same output (deterministic).
    assert _post_dag_repair_group_idx("post-dag", 1) == idx
    # Different attempt -> typically different.
    assert _post_dag_repair_group_idx("post-dag", 2) != idx
    # Different source -> typically different.
    assert _post_dag_repair_group_idx("another-source", 1) != idx


def test_direct_route_target_paths_normalizes() -> None:
    """`_direct_route_target_paths(route)` strips `:lineno` suffixes,
    normalizes path separators to `/`, dedupes via `set`, and returns
    a sorted list. Empty / whitespace-only entries are skipped.
    """

    from iriai_build_v2.workflows.develop.execution.repair import (
        _NORMAL_VERIFY_ROUTE,
        _direct_route_target_paths,
    )
    from iriai_build_v2.workflows.develop.execution.types import (
        DagDirectRepairRoute,
    )

    route = DagDirectRepairRoute(
        route=_NORMAL_VERIFY_ROUTE,
        reason="test",
        signature="",
        target_files=[
            "src/foo.py:42",
            "src\\bar.py",
            "src/foo.py:42",  # duplicate
            "  ",  # whitespace -> skipped
            "src/baz.py",
        ],
    )
    paths = _direct_route_target_paths(route)
    assert paths == ["src/bar.py", "src/baz.py", "src/foo.py"]


def test_direct_route_failure_pair_routes() -> None:
    """`_direct_route_failure_pair(route)` maps each route name to a
    `(failure_class, failure_type, source)` triple. Operator-required
    dominates regardless of route; explicit route mappings hit each
    branch.
    """

    from iriai_build_v2.workflows.develop.execution.repair import (
        _COMMIT_HYGIENE_ROUTE,
        _MANIFEST_FORBIDDEN_CLEANUP_ROUTE,
        _NORMAL_VERIFY_ROUTE,
        _REPO_HYGIENE_ROUTE,
        _direct_route_failure_pair,
    )
    from iriai_build_v2.workflows.develop.execution.types import (
        DagDirectRepairRoute,
    )

    # Operator-required dominates.
    op_route = DagDirectRepairRoute(
        route=_NORMAL_VERIFY_ROUTE,
        reason="t",
        signature="",
        operator_required=True,
    )
    assert _direct_route_failure_pair(op_route) == (
        "operator_required",
        "operator_clearance_required",
        "workspace_authority",
    )

    # Repo hygiene route -> operator_required pair (route check too).
    repo_route = DagDirectRepairRoute(
        route=_REPO_HYGIENE_ROUTE,
        reason="t",
        signature="",
    )
    assert _direct_route_failure_pair(repo_route) == (
        "operator_required",
        "operator_clearance_required",
        "workspace_authority",
    )

    # Commit hygiene.
    ch_route = DagDirectRepairRoute(
        route=_COMMIT_HYGIENE_ROUTE,
        reason="t",
        signature="",
    )
    assert _direct_route_failure_pair(ch_route) == (
        "commit_hygiene",
        "commit_hook_failed",
        "merge_queue",
    )

    # Manifest forbidden cleanup.
    mf_route = DagDirectRepairRoute(
        route=_MANIFEST_FORBIDDEN_CLEANUP_ROUTE,
        reason="t",
        signature="",
    )
    assert _direct_route_failure_pair(mf_route) == (
        "contract_violation",
        "forbidden_path_touched",
        "contract",
    )

    # Normal verify.
    nv_route = DagDirectRepairRoute(
        route=_NORMAL_VERIFY_ROUTE,
        reason="t",
        signature="",
    )
    assert _direct_route_failure_pair(nv_route) == (
        "product_defect",
        "semantic_verifier_rejected",
        "verification_graph",
    )

    # Unknown route -> default.
    unknown = DagDirectRepairRoute(
        route="something_else",
        reason="t",
        signature="",
    )
    assert _direct_route_failure_pair(unknown) == (
        "unknown",
        "unclassified",
        "journal",
    )


def test_commit_failure_issue_kind_branches() -> None:
    """`_commit_failure_issue_kind(issue)` lower-cases `issue.description
    \n issue.file` and returns one of four route names: manifest-
    forbidden cleanup, repo hygiene, commit hygiene, or normal verify
    (fallback).
    """

    from iriai_build_v2.models.outputs import Issue
    from iriai_build_v2.workflows.develop.execution.repair import (
        _COMMIT_HYGIENE_ROUTE,
        _MANIFEST_FORBIDDEN_CLEANUP_ROUTE,
        _NORMAL_VERIFY_ROUTE,
        _REPO_HYGIENE_ROUTE,
        _commit_failure_issue_kind,
    )

    mf_issue = Issue(
        severity="blocker",
        description="manifest-forbidden product cleanup required",
        file="dag.md",
    )
    assert _commit_failure_issue_kind(mf_issue) == _MANIFEST_FORBIDDEN_CLEANUP_ROUTE

    repo_issue = Issue(
        severity="blocker",
        description="workflow repo hygiene blocker detected",
        file="repo.md",
    )
    assert _commit_failure_issue_kind(repo_issue) == _REPO_HYGIENE_ROUTE

    embedded_git_issue = Issue(
        severity="blocker",
        description="Encountered embedded .git directory",
        file="r.md",
    )
    assert _commit_failure_issue_kind(embedded_git_issue) == _REPO_HYGIENE_ROUTE

    ch_issue = Issue(
        severity="blocker",
        description="pre-commit/husky failed during commit",
        file="hooks.md",
    )
    assert _commit_failure_issue_kind(ch_issue) == _COMMIT_HYGIENE_ROUTE

    # Fallback: any unrelated description -> normal verify.
    normal_issue = Issue(
        severity="major",
        description="semantic verifier rejected the patch",
        file="src/foo.py",
    )
    assert _commit_failure_issue_kind(normal_issue) == _NORMAL_VERIFY_ROUTE


def test_is_deterministic_dag_preflight_issue_branches() -> None:
    """`_is_deterministic_dag_preflight_issue(issue)` returns True iff
    any of the deterministic-preflight marker substrings hit on the
    lower-cased description+file text.
    """

    from iriai_build_v2.models.outputs import Issue
    from iriai_build_v2.workflows.develop.execution.repair import (
        _is_deterministic_dag_preflight_issue,
    )

    assert _is_deterministic_dag_preflight_issue(
        Issue(severity="blocker", description="manifest-forbidden product cleanup", file="")
    )
    assert _is_deterministic_dag_preflight_issue(
        Issue(severity="blocker", description="manifest-forbidden/stale path observed", file="")
    )
    assert _is_deterministic_dag_preflight_issue(
        Issue(
            severity="blocker",
            description="The synthesizer reports changed file that is missing from the feature workspace; manifest violation",
            file="",
        )
    )
    assert _is_deterministic_dag_preflight_issue(
        Issue(severity="blocker", description="repair stale task metadata", file="")
    )
    assert _is_deterministic_dag_preflight_issue(
        Issue(severity="blocker", description="programmatic dag preflight failure", file="")
    )

    # Unrelated description -> False.
    assert not _is_deterministic_dag_preflight_issue(
        Issue(severity="blocker", description="something unrelated", file="src/foo.py")
    )


def test_direct_route_target_extracts_path() -> None:
    """`_direct_route_target(issue)` returns `issue.file` (or
    `f"{file}:{line}"` if `issue.line` is truthy); empty file -> `""`.
    """

    from iriai_build_v2.models.outputs import Issue
    from iriai_build_v2.workflows.develop.execution.repair import (
        _direct_route_target,
    )

    assert _direct_route_target(Issue(severity="major", description="", file="")) == ""
    assert _direct_route_target(
        Issue(severity="major", description="", file="src/foo.py")
    ) == "src/foo.py"
    assert _direct_route_target(
        Issue(severity="major", description="", file="src/foo.py", line=42)
    ) == "src/foo.py:42"


def test_direct_route_issue_operator_required_marker() -> None:
    """`_direct_route_issue_operator_required(issue)` returns True iff
    the `_OPERATOR_REQUIRED_MARKER` substring (`"operator_required=true"`)
    appears in `issue.description`.
    """

    from iriai_build_v2.models.outputs import Issue
    from iriai_build_v2.workflows.develop.execution.repair import (
        _direct_route_issue_operator_required,
    )

    assert _direct_route_issue_operator_required(
        Issue(
            severity="blocker",
            description="cleanup required; operator_required=true; reason=foo",
            file="",
        )
    )
    assert not _direct_route_issue_operator_required(
        Issue(severity="blocker", description="no marker here", file="")
    )


def test_normalize_direct_route_signature_stable() -> None:
    """`_normalize_direct_route_signature(verdict, route)` returns a
    sha256 hex digest over the sorted+normalized concern descriptions.
    Same verdict -> same digest; order of concerns must not affect it
    (sort happens inside).
    """

    from iriai_build_v2.models.outputs import Issue, Verdict
    from iriai_build_v2.workflows.develop.execution.repair import (
        _NORMAL_VERIFY_ROUTE,
        _normalize_direct_route_signature,
    )

    v1 = Verdict(
        approved=False,
        summary="s",
        concerns=[
            Issue(severity="blocker", description="failure A retry-3", file="a.py", line=1),
            Issue(severity="blocker", description="failure B attempt 7", file="b.py", line=2),
        ],
    )
    v2 = Verdict(
        approved=False,
        summary="s",
        concerns=[
            # Reversed order; should produce identical digest after sort.
            Issue(severity="blocker", description="failure B attempt 7", file="b.py", line=2),
            Issue(severity="blocker", description="failure A retry-3", file="a.py", line=1),
        ],
    )
    sig1 = _normalize_direct_route_signature(v1, _NORMAL_VERIFY_ROUTE)
    sig2 = _normalize_direct_route_signature(v2, _NORMAL_VERIFY_ROUTE)
    assert sig1 == sig2
    assert len(sig1) == 64  # sha256 hex digest length

    # Different concern -> different digest.
    v3 = Verdict(
        approved=False,
        summary="s",
        concerns=[
            Issue(severity="blocker", description="different concern", file="c.py", line=3),
        ],
    )
    sig3 = _normalize_direct_route_signature(v3, _NORMAL_VERIFY_ROUTE)
    assert sig3 != sig1

    # retry-N normalization: `retry-1` and `retry-99` produce same digest.
    v_r1 = Verdict(
        approved=False,
        summary="s",
        concerns=[Issue(severity="blocker", description="retry-1 failure", file="x.py", line=1)],
    )
    v_r99 = Verdict(
        approved=False,
        summary="s",
        concerns=[Issue(severity="blocker", description="retry-99 failure", file="x.py", line=1)],
    )
    assert _normalize_direct_route_signature(v_r1, "r") == _normalize_direct_route_signature(
        v_r99, "r"
    )


def test_classify_dag_direct_repair_route_non_verdict_or_approved() -> None:
    """`_classify_dag_direct_repair_route(non_verdict_or_approved)`
    returns the `_NORMAL_VERIFY_ROUTE` with `reason="not_a_failed_verdict"`
    -- the early-out for inputs that are not actionable.
    """

    from iriai_build_v2.models.outputs import Verdict
    from iriai_build_v2.workflows.develop.execution.repair import (
        _NORMAL_VERIFY_ROUTE,
        _classify_dag_direct_repair_route,
    )

    # Not a Verdict.
    route = _classify_dag_direct_repair_route({"approved": False})
    assert route.route == _NORMAL_VERIFY_ROUTE
    assert route.reason == "not_a_failed_verdict"

    # Approved Verdict -> same not-actionable route.
    approved = Verdict(approved=True, summary="s")
    route = _classify_dag_direct_repair_route(approved)
    assert route.route == _NORMAL_VERIFY_ROUTE
    assert route.reason == "not_a_failed_verdict"


def test_classify_dag_direct_repair_route_no_concerns_branches() -> None:
    """Failed Verdicts with gaps / failed checks / no concerns each
    produce the `_NORMAL_VERIFY_ROUTE` with the matching reason
    sentinel.
    """

    from iriai_build_v2.models.outputs import Check, Gap, Verdict
    from iriai_build_v2.workflows.develop.execution.repair import (
        _NORMAL_VERIFY_ROUTE,
        _classify_dag_direct_repair_route,
    )

    # Gaps present.
    with_gaps = Verdict(
        approved=False,
        summary="s",
        gaps=[Gap(severity="blocker", category="missing", description="g")],
    )
    route = _classify_dag_direct_repair_route(with_gaps)
    assert route.route == _NORMAL_VERIFY_ROUTE
    assert route.reason == "verdict_has_gaps"

    # Failed checks.
    with_fail = Verdict(
        approved=False,
        summary="s",
        checks=[Check(criterion="c", result="FAIL", detail="e")],
    )
    route = _classify_dag_direct_repair_route(with_fail)
    assert route.route == _NORMAL_VERIFY_ROUTE
    assert route.reason == "verdict_has_failed_checks"

    # No concerns at all (and no failed checks / gaps).
    empty = Verdict(approved=False, summary="s")
    route = _classify_dag_direct_repair_route(empty)
    assert route.route == _NORMAL_VERIFY_ROUTE
    assert route.reason == "verdict_has_no_concerns"


def test_classify_dag_direct_repair_route_repo_hygiene_only() -> None:
    """A Verdict whose every concern is a repo-hygiene blocker -> the
    `_REPO_HYGIENE_ROUTE` with operator_required=True. Targets are
    sorted+deduped via the helper.
    """

    from iriai_build_v2.models.outputs import Issue, Verdict
    from iriai_build_v2.workflows.develop.execution.repair import (
        _REPO_HYGIENE_ROUTE,
        _classify_dag_direct_repair_route,
    )

    v = Verdict(
        approved=False,
        summary="s",
        concerns=[
            Issue(severity="blocker", description="workflow repo hygiene blocker", file="r1.md", line=2),
            Issue(severity="blocker", description="embedded .git encountered", file="r2.md"),
        ],
    )
    route = _classify_dag_direct_repair_route(v)
    assert route.route == _REPO_HYGIENE_ROUTE
    assert route.reason == "repo_hygiene_only_verdict"
    assert route.operator_required is True
    assert route.skip_expanded_verify is True
    assert route.skip_parallel_repair is True
    assert route.skip_rca is True
    # Targets sorted; `r1.md:2` because of line, `r2.md` plain.
    assert "r1.md:2" in route.target_files
    assert "r2.md" in route.target_files


def test_classify_dag_direct_repair_route_commit_hygiene_only() -> None:
    """A Verdict whose every concern is a pre-commit/husky failure
    routes to `_COMMIT_HYGIENE_ROUTE` with the deterministic signature
    populated and skip_* flags on.
    """

    from iriai_build_v2.models.outputs import Issue, Verdict
    from iriai_build_v2.workflows.develop.execution.repair import (
        _COMMIT_HYGIENE_ROUTE,
        _classify_dag_direct_repair_route,
    )

    v = Verdict(
        approved=False,
        summary="s",
        concerns=[
            Issue(severity="blocker", description="pre-commit/husky failed", file="h.md", line=1),
        ],
    )
    route = _classify_dag_direct_repair_route(v)
    assert route.route == _COMMIT_HYGIENE_ROUTE
    assert route.reason == "commit_hygiene_only_verdict"
    assert route.signature  # non-empty sha256
    assert route.skip_expanded_verify is True
    assert route.skip_parallel_repair is True
    assert route.skip_rca is True


def test_classify_dag_direct_repair_route_manifest_forbidden_cleanup() -> None:
    """A Verdict whose concerns include manifest-forbidden cleanup
    routes to `_MANIFEST_FORBIDDEN_CLEANUP_ROUTE`. When at least one
    concern carries the operator_required marker, the route's
    operator_required flag is True and the reason ends with
    `_operator_required`.
    """

    from iriai_build_v2.models.outputs import Issue, Verdict
    from iriai_build_v2.workflows.develop.execution.repair import (
        _MANIFEST_FORBIDDEN_CLEANUP_ROUTE,
        _classify_dag_direct_repair_route,
    )

    v = Verdict(
        approved=False,
        summary="s",
        concerns=[
            Issue(
                severity="blocker",
                description="manifest-forbidden product cleanup required; operator_required=true",
                file="dag.md",
                line=5,
            ),
        ],
    )
    route = _classify_dag_direct_repair_route(v)
    assert route.route == _MANIFEST_FORBIDDEN_CLEANUP_ROUTE
    assert route.operator_required is True
    # The single-concern marker text triggers the early "any MF cleanup
    # and all preflight" branch with reason `manifest_forbidden_cleanup_*`
    # (not the later `all kind == MF` branch).
    assert route.reason == "manifest_forbidden_cleanup_operator_required"
    assert route.skip_expanded_verify is True


def test_classify_dag_direct_repair_route_mixed_falls_back() -> None:
    """A Verdict mixing repo-hygiene + commit-hygiene routes (where no
    single kind dominates and the `all()` clauses fail) routes to
    `_NORMAL_VERIFY_ROUTE` with `reason="mixed_deterministic_routes"`.
    """

    from iriai_build_v2.models.outputs import Issue, Verdict
    from iriai_build_v2.workflows.develop.execution.repair import (
        _NORMAL_VERIFY_ROUTE,
        _classify_dag_direct_repair_route,
    )

    v = Verdict(
        approved=False,
        summary="s",
        concerns=[
            Issue(severity="blocker", description="workflow repo hygiene blocker", file="r.md"),
            Issue(severity="blocker", description="pre-commit/husky failed", file="h.md"),
        ],
    )
    route = _classify_dag_direct_repair_route(v)
    assert route.route == _NORMAL_VERIFY_ROUTE
    assert route.reason == "mixed_deterministic_routes"


def test_is_dag_artifact_repair_path_branches() -> None:
    """`_is_dag_artifact_repair_path(path)` returns True for empty
    paths, `dag.md`, parenthetical placeholders, paths containing the
    artifact-marker substrings, and paths starting with one of the
    artifact-prefix tuples.
    """

    from iriai_build_v2.workflows.develop.execution.repair import (
        _is_dag_artifact_repair_path,
    )

    # Empty -> True.
    assert _is_dag_artifact_repair_path("")
    assert _is_dag_artifact_repair_path("   ")
    # Top-level dag.md.
    assert _is_dag_artifact_repair_path("dag.md")
    # Parenthetical placeholder.
    assert _is_dag_artifact_repair_path("(unspecified)")
    # Marker substrings.
    assert _is_dag_artifact_repair_path("/some/path/.iriai/artifacts/features/F/dag.md")
    assert _is_dag_artifact_repair_path("workspace/.iriai-context/g0-expanded-verify.md")
    # Prefix matches.
    assert _is_dag_artifact_repair_path(".iriai/artifacts/features/abc/x.json")
    assert _is_dag_artifact_repair_path("dag/dag-001.md")
    assert _is_dag_artifact_repair_path("subfeatures/S/foo.md")
    # Plain product path -> False.
    assert not _is_dag_artifact_repair_path("src/iriai_build_v2/workflows/develop/phases/implementation.py")


def test_normalize_dag_artifact_repair_ref_strips_quotes_and_lines() -> None:
    """`_normalize_dag_artifact_repair_ref(ref)` strips backticks,
    quotes, brackets, trailing punctuation, and a trailing `:lineno`
    fragment. Backslashes become `/`.
    """

    from iriai_build_v2.workflows.develop.execution.repair import (
        _normalize_dag_artifact_repair_ref,
    )

    assert _normalize_dag_artifact_repair_ref("`src/foo.py`") == "src/foo.py"
    assert _normalize_dag_artifact_repair_ref("'src/foo.py'") == "src/foo.py"
    assert _normalize_dag_artifact_repair_ref('"src/foo.py"') == "src/foo.py"
    # Trailing punctuation is stripped AFTER the quote-strip; the
    # `.,;:` rstrip removes a stray `,` once the quote layer is gone.
    assert _normalize_dag_artifact_repair_ref("src/foo.py,") == "src/foo.py"
    assert _normalize_dag_artifact_repair_ref("[src/foo.py]") == "src/foo.py"
    assert _normalize_dag_artifact_repair_ref("src\\bar.py") == "src/bar.py"
    # Trailing :lineno fragment -- the helper requires an extension to detect.
    assert _normalize_dag_artifact_repair_ref("src/foo.py:42") == "src/foo.py"
    assert _normalize_dag_artifact_repair_ref("src/foo.py:42:7") == "src/foo.py"
    # Idempotent.
    assert _normalize_dag_artifact_repair_ref(
        _normalize_dag_artifact_repair_ref("`src/foo.py:42`")
    ) == "src/foo.py"


def test_is_dag_task_artifact_key_predicate() -> None:
    """`_is_dag_task_artifact_key(ref)` returns True only for refs
    that start with `dag-task:` and have a non-empty suffix, with no
    embedded `/` or `\\`.
    """

    from iriai_build_v2.workflows.develop.execution.repair import (
        _is_dag_task_artifact_key,
    )

    assert _is_dag_task_artifact_key("dag-task:T-001")
    assert _is_dag_task_artifact_key("dag-task:T-007-impl")
    assert not _is_dag_task_artifact_key("dag-task:")
    assert not _is_dag_task_artifact_key("dag-task: ")
    assert not _is_dag_task_artifact_key("plan:T-001")
    assert not _is_dag_task_artifact_key("dag-task:T/001")


def test_is_derived_dag_artifact_key_predicate() -> None:
    """`_is_derived_dag_artifact_key(ref)` returns True for refs
    starting with `derived-dag:`, `dag-derived:`, or `dag-regroup:`
    with a non-empty suffix.
    """

    from iriai_build_v2.workflows.develop.execution.repair import (
        _is_derived_dag_artifact_key,
    )

    assert _is_derived_dag_artifact_key("derived-dag:g42")
    assert _is_derived_dag_artifact_key("dag-derived:hash")
    assert _is_derived_dag_artifact_key("dag-regroup:abc")
    assert not _is_derived_dag_artifact_key("derived-dag:")
    assert not _is_derived_dag_artifact_key("dag-task:T-001")
    assert not _is_derived_dag_artifact_key("derived-dag:foo/bar")  # `/` in path.


def test_is_dag_artifact_repair_key_predicate_branches() -> None:
    """`_is_dag_artifact_repair_key(ref)` returns True for dag-task
    keys, the 13 hard-coded top-level keys, and namespace-prefixed
    keys with non-empty slug. Refs containing `/` or `\\` are False.
    """

    from iriai_build_v2.workflows.develop.execution.repair import (
        _is_dag_artifact_repair_key,
    )

    # dag-task delegates to _is_dag_task_artifact_key.
    assert _is_dag_artifact_repair_key("dag-task:T-007")
    # Top-level keys.
    assert _is_dag_artifact_repair_key("context")
    assert _is_dag_artifact_repair_key("plan")
    assert _is_dag_artifact_repair_key("prd")
    assert _is_dag_artifact_repair_key("decomposition")
    assert _is_dag_artifact_repair_key("design")
    assert _is_dag_artifact_repair_key("test-plan")
    assert _is_dag_artifact_repair_key("system-design")
    # Namespace prefixes.
    assert _is_dag_artifact_repair_key("plan-structured:abc")
    assert _is_dag_artifact_repair_key("design-structured:abc")
    assert _is_dag_artifact_repair_key("gate-review-ledger:abc")
    assert _is_dag_artifact_repair_key("dag-fragment-attempt:42")
    # Unknown namespace -> False.
    assert not _is_dag_artifact_repair_key("unknown-namespace:abc")
    # Empty slug -> False.
    assert not _is_dag_artifact_repair_key("plan:")
    # Slash / backslash -> False.
    assert not _is_dag_artifact_repair_key("plan:abc/def")
    assert not _is_dag_artifact_repair_key("path/with/slash")


def test_dag_contradiction_needs_artifact_repair_kinds() -> None:
    """`_dag_contradiction_needs_artifact_repair(resolution)` returns
    True iff `resolution.resolution_kind` is `artifact_repair` or the
    `_DAG_CONTRADICTION_MIXED_REPAIR_KIND` ("mixed_repair").
    """

    from iriai_build_v2.workflows.develop.execution.repair import (
        _DAG_CONTRADICTION_MIXED_REPAIR_KIND,
        _dag_contradiction_needs_artifact_repair,
    )
    from iriai_build_v2.workflows.develop.execution.types import (
        DagContradictionResolution,
    )

    artifact = DagContradictionResolution(
        resolution="r",
        resolution_kind="artifact_repair",
        authoritative_sources=["s"],
        implementation_direction="i",
        rationale="r",
    )
    assert _dag_contradiction_needs_artifact_repair(artifact)

    mixed = DagContradictionResolution(
        resolution="r",
        resolution_kind=_DAG_CONTRADICTION_MIXED_REPAIR_KIND,
        authoritative_sources=["s"],
        implementation_direction="i",
        rationale="r",
    )
    assert _dag_contradiction_needs_artifact_repair(mixed)

    other = DagContradictionResolution(
        resolution="r",
        resolution_kind="decision_only",
        authoritative_sources=["s"],
        implementation_direction="i",
        rationale="r",
    )
    assert not _dag_contradiction_needs_artifact_repair(other)


def test_dag_contradiction_fix_guidance_embeds_fields() -> None:
    """`_dag_contradiction_fix_guidance(resolution)` returns a string
    that embeds `implementation_direction` (or `resolution` if empty),
    the resolution text, kind, and superseded expectation (or "not
    specified" fallback).
    """

    from iriai_build_v2.workflows.develop.execution.repair import (
        _dag_contradiction_fix_guidance,
    )
    from iriai_build_v2.workflows.develop.execution.types import (
        DagContradictionResolution,
    )

    with_direction = DagContradictionResolution(
        resolution="canonical resolution text",
        resolution_kind="requires_code_change",
        authoritative_sources=["src"],
        implementation_direction="apply patch X",
        rationale="r",
        superseded_expectation="old expectation",
    )
    text = _dag_contradiction_fix_guidance(with_direction)
    assert "apply patch X" in text
    assert "canonical resolution text" in text
    assert "requires_code_change" in text
    assert "old expectation" in text

    # Empty direction falls back to resolution; empty superseded uses
    # the literal "not specified".
    no_direction = DagContradictionResolution(
        resolution="canonical fallback",
        resolution_kind="decision_only",
        authoritative_sources=["src"],
        implementation_direction="",
        rationale="r",
    )
    text = _dag_contradiction_fix_guidance(no_direction)
    assert "canonical fallback" in text
    assert "not specified" in text


def test_dag_product_cleanup_ready_for_artifact_repair_blocks_on_reasons() -> None:
    """`_dag_product_cleanup_ready_for_artifact_repair(report)` returns
    False iff any `report.skipped` entry carries one of the three
    blocking reason strings; True otherwise.
    """

    from iriai_build_v2.workflows.develop.execution.repair import (
        _dag_product_cleanup_ready_for_artifact_repair,
    )

    # Empty report -> ready.
    assert _dag_product_cleanup_ready_for_artifact_repair({})
    assert _dag_product_cleanup_ready_for_artifact_repair({"skipped": []})

    # Non-blocking reason in skipped -> still ready.
    assert _dag_product_cleanup_ready_for_artifact_repair(
        {"skipped": [{"reason": "harmless"}]}
    )

    # Each of the three blocking reasons -> not ready.
    for blocking in (
        "missing_feature_roots",
        "fix_result_status_not_completed_or_partial",
        "product_cleanup_still_required",
    ):
        assert not _dag_product_cleanup_ready_for_artifact_repair(
            {"skipped": [{"reason": blocking}]}
        )

    # Non-dict entry in skipped -> ignored (not blocking).
    assert _dag_product_cleanup_ready_for_artifact_repair(
        {"skipped": ["not-a-dict"]}
    )


def test_no_sibling_module_owns_moved_name() -> None:
    """Sibling execution modules (`types.py`, `git_service.py`,
    `task_contracts.py`, `sandbox.py`, `dispatcher.py`, `gates.py`,
    `verification.py`) MUST NOT define any of the 24 moved names. Locks
    cluster ownership: each name lives in EXACTLY ONE execution
    sibling module (`repair.py`).
    """

    from iriai_build_v2.workflows.develop.execution import (
        dispatcher as dispatcher_mod,
        gates as gates_mod,
        git_service as git_service_mod,
        sandbox as sandbox_mod,
        task_contracts as task_contracts_mod,
        types as types_mod,
        verification as verification_mod,
    )
    for name in MOVED_NAMES:
        for sibling, sibling_name in (
            (dispatcher_mod, "dispatcher"),
            (gates_mod, "gates"),
            (git_service_mod, "git_service"),
            (sandbox_mod, "sandbox"),
            (task_contracts_mod, "task_contracts"),
            (types_mod, "types"),
            (verification_mod, "verification"),
        ):
            assert not hasattr(sibling, name), (
                f"sibling drift: {sibling_name}.{name} unexpectedly exists; "
                "cluster ownership pin failed"
            )


def test_shim_block_exports_all_twenty_four_names() -> None:
    """The Slice-11h shim block in `implementation.py` re-exports
    exactly the 24 moved names from `..execution.repair`. This test
    asserts the shim block actually carries all 24 (a deliberate "did
    the shim block lose a name?" probe) and that the pre-existing
    Slice-11a + Slice-11b + Slice-11c + Slice-11d + Slice-11e +
    Slice-11f + Slice-11g shim blocks are unchanged (a representative
    sample from each is checked).
    """

    from iriai_build_v2.workflows.develop.phases import implementation as impl_mod

    # All 24 moved names are accessible via the impl module.
    for name in MOVED_NAMES:
        assert hasattr(impl_mod, name), (
            f"implementation.{name} missing -- the Slice-11h shim block "
            "dropped a re-export"
        )

    # The pre-existing Slice-11a through Slice-11g shim re-exports are
    # STILL present (representative samples).
    from iriai_build_v2.workflows.develop.execution.types import (
        DagAuthorityGateOutcome,
        DagDirectRepairRoute,
    )
    assert impl_mod.DagAuthorityGateOutcome is DagAuthorityGateOutcome
    assert impl_mod.DagDirectRepairRoute is DagDirectRepairRoute
    from iriai_build_v2.workflows.develop.execution.dispatcher import (
        _make_parallel_actor,
    )
    assert impl_mod._make_parallel_actor is _make_parallel_actor
    from iriai_build_v2.workflows.develop.execution.gates import (
        _dag_authority_blocked_verdict,
    )
    assert impl_mod._dag_authority_blocked_verdict is _dag_authority_blocked_verdict
    from iriai_build_v2.workflows.develop.execution.verification import (
        _pydantic_json,
    )
    assert impl_mod._pydantic_json is _pydantic_json


def test_repair_module_does_not_import_implementation() -> None:
    """The compatibility-arrow direction (per doc 11 § "How To Use
    This Map" Q4) is: `execution/repair.py` MUST NOT import from
    `workflows.develop.phases.implementation`. This test reads the
    on-disk source of `repair.py` and asserts the import line is
    absent. Belt-and-braces guard against a future refactor
    accidentally introducing a back-import.
    """

    import iriai_build_v2.workflows.develop.execution.repair as repair_mod

    source_path = Path(repair_mod.__file__)
    text = source_path.read_text(encoding="utf-8")
    assert "from iriai_build_v2.workflows.develop.phases.implementation" not in text, (
        "execution/repair.py imports from phases/implementation -- "
        "violates the doc-11 compatibility-arrow direction"
    )
    assert "from ..phases.implementation" not in text, (
        "execution/repair.py uses a relative back-import to phases/"
        "implementation -- violates the doc-11 compatibility-arrow direction"
    )
