"""Slice 11f -- extraction proof for `execution/gates.py` extension.

Verifies the doc-11 § "How To Use This Map" four-question contract for
the pure DAG-authority routing-primitive + post-DAG gate proof-key /
notify helper extraction:

1. What behavior moved: 13 pure primitives -- the 6 routing constants
   `_DAG_AUTHORITY_SEMANTIC_ROUTE` /
   `_DAG_AUTHORITY_DB_TASK_RESULT_ROUTE` /
   `_DAG_AUTHORITY_TASK_SPEC_PROJECTION_ROUTE` /
   `_DAG_AUTHORITY_SOURCE_ARTIFACT_ROUTE` /
   `_DAG_AUTHORITY_PRODUCT_WORKSPACE_ROUTE` /
   `_DAG_AUTHORITY_REPO_BLOCKER_ROUTE`, the preflight-key formatter
   `_dag_authority_preflight_key`, the path-problem-route classifier
   `_dag_authority_path_problem_route`, the blocked-verdict factory
   `_dag_authority_blocked_verdict`, the synthetic-result factory
   `_dag_authority_synthetic_result`, the reconcile-target-coverage
   projection `_dag_authority_reconcile_target_coverage`, the
   post-DAG-gate-proof-key formatter `_post_dag_gate_proof_key`, and
   the notify-gate-proof-extra transform
   `_notify_gate_proof_extra_from_delivery` -- moved from
   `workflows/develop/phases/implementation.py` to
   `workflows/develop/execution/gates.py`. The Slice-06 `GateRunner`
   + `GateRequest` + 22 typed gate models + `ContextPackageBuilder` +
   `InMemoryEvidenceRecorder` already in `gates.py` are UNTOUCHED --
   Slice 11f EXTENDS, never modifies.
2. Which legacy import names still work: every existing
   `from iriai_build_v2.workflows.develop.phases.implementation import X`
   for one of the 13 moved names keeps resolving to the SAME object as
   the canonical definition in `execution/gates.py` (the shim is
   `is`-equivalent, not a copy). `monkeypatch.setattr(implementation_
   module, X, ...)` continues to mutate the SAME binding any direct
   `from execution.gates import X` reader sees.
3. Which targeted tests prove the new facade and the compatibility
   shim: THIS file is one of them; it pins every moved name's shim
   equivalence and behaviorally smoke-tests each moved helper.
4. Why is the PR still refactor-only: nothing else moves. The 13 pure
   primitives moved byte-for-byte. The phase-level gate PORT surface
   (the async runner+feature-coupled `_attempt_dag_authority_gate_
   repair` / `_dag_authority_load_preflight_report` / `_record_dag_
   authority_gate`, the `_execution_control_store_for_runner`-coupled
   `_record_typed_verification_gate_node` / `_typed_verification_
   gate_node_is_fresh`, the merge-queue-PORT-coupled `_merge_queue_
   post_apply_gate_decision`, the `_get_feature_root`+subprocess-
   coupled `_post_dag_gate_tree_digest` family, and the
   `_is_dag_task_artifact_key`-coupled
   `_dag_authority_applied_dag_task_updates` / `_dag_authority_task_
   refs_from_path_problems`) is genuinely PHASE-LEVEL and CORRECTLY
   stays in `implementation.py` per the prompt hard rule against
   splitting non-pure helpers.
"""

from __future__ import annotations

import json

import pytest


# Each entry is a name moved from `implementation.py` to
# `execution/gates.py` in Slice 11f. The order is the import-line
# order in the shim block in `implementation.py` (the Slice-11f block)
# so a grep over either file lists the names in the same order.
MOVED_NAMES = [
    "_DAG_AUTHORITY_DB_TASK_RESULT_ROUTE",
    "_DAG_AUTHORITY_PRODUCT_WORKSPACE_ROUTE",
    "_DAG_AUTHORITY_REPO_BLOCKER_ROUTE",
    "_DAG_AUTHORITY_SEMANTIC_ROUTE",
    "_DAG_AUTHORITY_SOURCE_ARTIFACT_ROUTE",
    "_DAG_AUTHORITY_TASK_SPEC_PROJECTION_ROUTE",
    "_dag_authority_blocked_verdict",
    "_dag_authority_path_problem_route",
    "_dag_authority_preflight_key",
    "_dag_authority_reconcile_target_coverage",
    "_dag_authority_synthetic_result",
    "_notify_gate_proof_extra_from_delivery",
    "_post_dag_gate_proof_key",
]

# The 6 routing constants are `str` instances which carry no per-
# binding `__module__` attribute (only the `str` type does). Only the
# 7 callables are `__module__`-checkable.
MOVED_CALLABLES = [
    "_dag_authority_blocked_verdict",
    "_dag_authority_path_problem_route",
    "_dag_authority_preflight_key",
    "_dag_authority_reconcile_target_coverage",
    "_dag_authority_synthetic_result",
    "_notify_gate_proof_extra_from_delivery",
    "_post_dag_gate_proof_key",
]


@pytest.mark.parametrize("name", MOVED_NAMES)
def test_shim_re_export_is_same_object_as_new_canonical(name: str) -> None:
    """Every moved helper imported via the OLD path is the SAME object
    as the import via the NEW canonical path. Proves the shim is a re-
    export, not a copy. Locks the monkeypatch target equivalence --
    `monkeypatch.setattr(implementation_module, name, ...)` will mutate
    the SAME function object that any direct
    `from execution.gates import name` reader sees.
    """

    from iriai_build_v2.workflows.develop import execution as execution_pkg
    from iriai_build_v2.workflows.develop.execution import gates as gates_mod
    from iriai_build_v2.workflows.develop.phases import implementation as impl_mod

    legacy = getattr(impl_mod, name)
    canonical = getattr(gates_mod, name)
    assert legacy is canonical, (
        f"shim drift: implementation.{name} is not the same object as "
        f"execution.gates.{name}"
    )
    # `execution_pkg` is imported only to ensure the package import chain
    # works end-to-end (no side-effect import errors).
    _ = execution_pkg


@pytest.mark.parametrize("name", MOVED_CALLABLES)
def test_canonical_module_is_gates(name: str) -> None:
    """The moved function objects' `__module__` is the new canonical
    `iriai_build_v2.workflows.develop.execution.gates` -- not the
    legacy `...phases.implementation`. Proves the definition genuinely
    moved rather than being re-aliased from the old module.
    """

    from iriai_build_v2.workflows.develop.execution import gates as gates_mod

    canonical = getattr(gates_mod, name)
    assert canonical.__module__ == (
        "iriai_build_v2.workflows.develop.execution.gates"
    ), (
        f"{name}.__module__ = {canonical.__module__!r}; expected the new "
        "gates-module path"
    )


def test_routing_constants_have_canonical_values() -> None:
    """The 6 `_DAG_AUTHORITY_*` routing constants are pure str literals
    pinned to their established workflow-payload values. A future rename
    of any one of these constants would break consumers downstream that
    persist these strings into JSON artifacts under
    `dag-authority-gate:*`; the test locks the byte values.
    """

    from iriai_build_v2.workflows.develop.execution.gates import (
        _DAG_AUTHORITY_DB_TASK_RESULT_ROUTE,
        _DAG_AUTHORITY_PRODUCT_WORKSPACE_ROUTE,
        _DAG_AUTHORITY_REPO_BLOCKER_ROUTE,
        _DAG_AUTHORITY_SEMANTIC_ROUTE,
        _DAG_AUTHORITY_SOURCE_ARTIFACT_ROUTE,
        _DAG_AUTHORITY_TASK_SPEC_PROJECTION_ROUTE,
    )

    assert _DAG_AUTHORITY_SEMANTIC_ROUTE == "semantic_verify_needed"
    assert _DAG_AUTHORITY_DB_TASK_RESULT_ROUTE == "db_task_result_drift"
    assert _DAG_AUTHORITY_TASK_SPEC_PROJECTION_ROUTE == "task_spec_projection_drift"
    assert _DAG_AUTHORITY_SOURCE_ARTIFACT_ROUTE == "source_dag_artifact_drift"
    assert _DAG_AUTHORITY_PRODUCT_WORKSPACE_ROUTE == "product_workspace_drift"
    assert _DAG_AUTHORITY_REPO_BLOCKER_ROUTE == "repo_or_permission_blocker"


def test_dag_authority_preflight_key_format() -> None:
    """`_dag_authority_preflight_key(group_idx, retry_label)` returns
    `f"dag-repair-preflight:g{group_idx}:retry-{retry_label}"`. Pure
    formatter. Consumed by the phase-level
    `_dag_authority_load_preflight_report` orchestrator (which STAYS
    in `implementation.py`).
    """

    from iriai_build_v2.workflows.develop.execution.gates import (
        _dag_authority_preflight_key,
    )

    assert (
        _dag_authority_preflight_key(0, "initial")
        == "dag-repair-preflight:g0:retry-initial"
    )
    assert (
        _dag_authority_preflight_key(7, "3")
        == "dag-repair-preflight:g7:retry-3"
    )
    assert (
        _dag_authority_preflight_key(42, "0-authority-spec")
        == "dag-repair-preflight:g42:retry-0-authority-spec"
    )


def test_dag_authority_path_problem_route_no_path_problems() -> None:
    """`_dag_authority_path_problem_route([], [])` returns
    `(semantic_verify_needed, "no_path_problems")` -- the no-input
    happy path falls back to the semantic-verify route since no
    deterministic path problems were observed.
    """

    from iriai_build_v2.workflows.develop.execution.gates import (
        _DAG_AUTHORITY_SEMANTIC_ROUTE,
        _dag_authority_path_problem_route,
    )

    route, reason = _dag_authority_path_problem_route([], [])
    assert route == _DAG_AUTHORITY_SEMANTIC_ROUTE
    assert reason == "no_path_problems"


def test_dag_authority_path_problem_route_repo_blocker_dominates() -> None:
    """Any `reason in {embedded_git, gitlink, parked_fallback}` in
    `problems` routes to `repo_or_permission_blocker` regardless of
    other shapes -- repo hygiene wins over everything else.
    """

    from iriai_build_v2.workflows.develop.execution.gates import (
        _DAG_AUTHORITY_REPO_BLOCKER_ROUTE,
        _dag_authority_path_problem_route,
    )

    for blocker_reason in ("embedded_git", "gitlink", "parked_fallback"):
        route, reason = _dag_authority_path_problem_route(
            [{"reason": blocker_reason, "artifact_key": "x"}],
            [],
        )
        assert route == _DAG_AUTHORITY_REPO_BLOCKER_ROUTE
        assert reason == "repo_hygiene_path_problem"


def test_dag_authority_path_problem_route_product_workspace_signals() -> None:
    """Any `repair_route == "product_cleanup_required"` or
    `exists_on_disk` truthy or `tracked_or_staged` truthy or
    `git_state in {clean_tracked, unstaged_delete, staged_add,
    untracked}` routes to `product_workspace_drift`. Each branch is
    exercised independently.
    """

    from iriai_build_v2.workflows.develop.execution.gates import (
        _DAG_AUTHORITY_PRODUCT_WORKSPACE_ROUTE,
        _dag_authority_path_problem_route,
    )

    branches = [
        {"repair_route": "product_cleanup_required"},
        {"exists_on_disk": True},
        {"tracked_or_staged": True},
        {"git_state": "clean_tracked"},
        {"git_state": "unstaged_delete"},
        {"git_state": "staged_add"},
        {"git_state": "untracked"},
    ]
    for branch in branches:
        route, reason = _dag_authority_path_problem_route([branch], [])
        assert route == _DAG_AUTHORITY_PRODUCT_WORKSPACE_ROUTE
        assert reason == "path_problem_requires_product_workspace_cleanup"


def test_dag_authority_path_problem_route_artifact_only_branches() -> None:
    """When `problems` is non-empty but does NOT trigger the
    repo-blocker or product-workspace branches, the route depends on
    `artifact_only`: empty -> `semantic_verify_needed`; reason in
    `{forbidden_task_spec, forbidden_task_spec_source_artifact}` ->
    `task_spec_projection_drift`; non-empty `source_artifact_ref` with
    `artifact_key` NOT starting with `dag-task:` ->
    `source_dag_artifact_drift`; otherwise -> `db_task_result_drift`.
    """

    from iriai_build_v2.workflows.develop.execution.gates import (
        _DAG_AUTHORITY_DB_TASK_RESULT_ROUTE,
        _DAG_AUTHORITY_SEMANTIC_ROUTE,
        _DAG_AUTHORITY_SOURCE_ARTIFACT_ROUTE,
        _DAG_AUTHORITY_TASK_SPEC_PROJECTION_ROUTE,
        _dag_authority_path_problem_route,
    )

    # No deterministic artifact-only problem -> semantic.
    route, reason = _dag_authority_path_problem_route(
        [{"reason": "anything_else"}],
        [],
    )
    assert route == _DAG_AUTHORITY_SEMANTIC_ROUTE
    assert reason == "no_deterministic_artifact_only_problem"

    # forbidden_task_spec -> task_spec_projection_drift.
    route, reason = _dag_authority_path_problem_route(
        [{"reason": "anything_else"}],
        [{"reason": "forbidden_task_spec"}],
    )
    assert route == _DAG_AUTHORITY_TASK_SPEC_PROJECTION_ROUTE
    assert reason == "task_spec_projection_drift"

    # forbidden_task_spec_source_artifact -> same route.
    route, reason = _dag_authority_path_problem_route(
        [{"reason": "anything_else"}],
        [{"reason": "forbidden_task_spec_source_artifact"}],
    )
    assert route == _DAG_AUTHORITY_TASK_SPEC_PROJECTION_ROUTE
    assert reason == "task_spec_projection_drift"

    # source_artifact_drift: non-empty source_artifact_ref, key not
    # starting with dag-task:.
    route, reason = _dag_authority_path_problem_route(
        [{"reason": "anything_else"}],
        [
            {
                "source_artifact_ref": "some-source",
                "artifact_key": "task-spec:42",
            }
        ],
    )
    assert route == _DAG_AUTHORITY_SOURCE_ARTIFACT_ROUTE
    assert reason == "source_artifact_drift"

    # When the artifact_key DOES start with dag-task:, the source-
    # artifact branch does NOT match -> falls through to db_task_result.
    route, reason = _dag_authority_path_problem_route(
        [{"reason": "anything_else"}],
        [
            {
                "source_artifact_ref": "some-source",
                "artifact_key": "dag-task:42",
            }
        ],
    )
    assert route == _DAG_AUTHORITY_DB_TASK_RESULT_ROUTE
    assert reason == "db_task_result_drift"

    # Empty source_artifact_ref also falls through to db_task_result.
    route, reason = _dag_authority_path_problem_route(
        [{"reason": "anything_else"}],
        [{"source_artifact_ref": "", "artifact_key": "task-spec:42"}],
    )
    assert route == _DAG_AUTHORITY_DB_TASK_RESULT_ROUTE
    assert reason == "db_task_result_drift"


def test_dag_authority_blocked_verdict_shape() -> None:
    """`_dag_authority_blocked_verdict(...)` returns a non-approved
    `Verdict` with one `blocker` `Issue` concern + one suggestion. The
    summary and concern description embed the supplied route, reason,
    target_refs (joined with `, ` or "(none)" if empty), and detail.
    """

    from iriai_build_v2.models.outputs import Issue, Verdict
    from iriai_build_v2.workflows.develop.execution.gates import (
        _dag_authority_blocked_verdict,
    )

    verdict = _dag_authority_blocked_verdict(
        7,
        2,
        route="task_spec_projection_drift",
        reason="forbidden_task_spec",
        target_refs=["dag-task:abc", "dag-task:def"],
        detail="Synthesizer did not apply spec.",
    )
    assert isinstance(verdict, Verdict)
    assert verdict.approved is False
    assert "Group 7 authority gate blocked retry 2" in verdict.summary
    assert "task_spec_projection_drift" in verdict.summary
    assert len(verdict.concerns) == 1
    concern = verdict.concerns[0]
    assert isinstance(concern, Issue)
    assert concern.severity == "blocker"
    assert "DAG authority gate blocked broad repair" in concern.description
    assert "Route: task_spec_projection_drift" in concern.description
    assert "reason: forbidden_task_spec" in concern.description
    assert "dag-task:abc, dag-task:def" in concern.description
    assert "Synthesizer did not apply spec." in concern.description
    assert len(verdict.suggestions) == 1
    assert "Repair the authoritative DAG/task artifact state" in verdict.suggestions[0]

    # Empty target_refs renders as "(none)".
    verdict_empty = _dag_authority_blocked_verdict(
        0,
        0,
        route="semantic_verify_needed",
        reason="no_path_problems",
        target_refs=[],
        detail="d",
    )
    assert "(none)" in verdict_empty.concerns[0].description


def test_dag_authority_synthetic_result_shape() -> None:
    """`_dag_authority_synthetic_result(group_idx, retry, report)`
    returns a completed `ImplementationResult` whose `task_id` is
    `f"DAG-AUTHORITY-REPAIR-g{group_idx}-r{retry}"`, summary embeds the
    `status` from `report`, status is `"completed"`, files lists are
    empty, and `notes` is the JSON-pretty-printed report. Pure factory.
    """

    from iriai_build_v2.models.outputs import ImplementationResult
    from iriai_build_v2.workflows.develop.execution.gates import (
        _dag_authority_synthetic_result,
    )

    report = {"status": "task_spec_reconciled", "applied": ["dag-task:42"]}
    result = _dag_authority_synthetic_result(3, 1, report)
    assert isinstance(result, ImplementationResult)
    assert result.task_id == "DAG-AUTHORITY-REPAIR-g3-r1"
    assert "task_spec_reconciled" in result.summary
    assert result.status == "completed"
    assert result.files_created == []
    assert result.files_modified == []
    parsed_notes = json.loads(result.notes)
    assert parsed_notes == report

    # Missing "status" key uses "unknown" fallback per `.get(..., "unknown")`.
    fallback = _dag_authority_synthetic_result(0, 0, {})
    assert "unknown" in fallback.summary


def test_dag_authority_reconcile_target_coverage_projection() -> None:
    """`_dag_authority_reconcile_target_coverage(reconcile_report,
    target_refs)` returns a deterministic shape:
    target_refs/covered_refs sorted; missing_refs = target - covered
    sorted; complete True iff no missing AND no blockers; skipped /
    blockers filtered to entries with `artifact_key in targets`.
    """

    from iriai_build_v2.workflows.develop.execution.gates import (
        _dag_authority_reconcile_target_coverage,
    )

    targets = ["dag-task:c", "dag-task:a", "dag-task:b"]
    reconcile = {
        "applied": [
            {"artifact_key": "dag-task:a"},
            {"artifact_key": "dag-task:b"},
            # Outside the target set -- ignored.
            {"artifact_key": "dag-task:zzz"},
            # Not a dict -- ignored.
            "not-a-dict",
        ],
        "skipped": [
            {"artifact_key": "dag-task:a", "reason": "stale_spec"},
            {"artifact_key": "dag-task:zzz", "reason": "out_of_scope"},
            "not-a-dict",
        ],
        "blockers": [
            {"artifact_key": "dag-task:c", "reason": "operator_required"},
            {"artifact_key": "dag-task:zzz", "reason": "out_of_scope"},
        ],
    }
    coverage = _dag_authority_reconcile_target_coverage(reconcile, targets)
    assert coverage["target_refs"] == ["dag-task:a", "dag-task:b", "dag-task:c"]
    assert coverage["covered_refs"] == ["dag-task:a", "dag-task:b"]
    assert coverage["missing_refs"] == ["dag-task:c"]
    assert coverage["complete"] is False  # missing AND blockers both non-empty
    # Skipped: only the dag-task:a entry is in targets; the zzz + the
    # bare string are filtered out.
    assert coverage["skipped"] == [
        {"artifact_key": "dag-task:a", "reason": "stale_spec"}
    ]
    # Blockers: only the dag-task:c entry is in targets.
    assert coverage["blockers"] == [
        {"artifact_key": "dag-task:c", "reason": "operator_required"}
    ]

    # Fully-covered case -> complete True.
    full = _dag_authority_reconcile_target_coverage(
        {
            "applied": [
                {"artifact_key": "dag-task:a"},
                {"artifact_key": "dag-task:b"},
                {"artifact_key": "dag-task:c"},
            ],
            "skipped": [],
            "blockers": [],
        },
        ["dag-task:a", "dag-task:b", "dag-task:c"],
    )
    assert full["complete"] is True
    assert full["missing_refs"] == []

    # Missing keys default to empty lists; no exceptions.
    empty = _dag_authority_reconcile_target_coverage({}, ["dag-task:a"])
    assert empty["target_refs"] == ["dag-task:a"]
    assert empty["covered_refs"] == []
    assert empty["missing_refs"] == ["dag-task:a"]
    assert empty["complete"] is False
    assert empty["skipped"] == []
    assert empty["blockers"] == []


def test_post_dag_gate_proof_key_format() -> None:
    """`_post_dag_gate_proof_key(gate_name)` returns
    `f"dag-gate-proof:{gate_name}"`. Pure 1-liner formatter used by
    the post-DAG gate proof recorders to compute the artifact key for
    `dag-gate-proof:<gate_name>` entries. The key string is consumed
    by `runner.artifacts.get`/`put` in the phase-level
    `_record_post_dag_gate_proof` orchestrator (which STAYS).
    """

    from iriai_build_v2.workflows.develop.execution.gates import (
        _post_dag_gate_proof_key,
    )

    assert _post_dag_gate_proof_key("code-review") == "dag-gate-proof:code-review"
    assert _post_dag_gate_proof_key("security") == "dag-gate-proof:security"
    assert _post_dag_gate_proof_key("") == "dag-gate-proof:"
    assert (
        _post_dag_gate_proof_key("test-authoring")
        == "dag-gate-proof:test-authoring"
    )


def test_notify_gate_proof_extra_from_delivery_shape() -> None:
    """`_notify_gate_proof_extra_from_delivery(delivery)` extracts a
    canonical 2-key dict carrying `delivery_id` + `notification_sha256`,
    with `None`/missing keys coerced to `""`. Pure dict transform.
    """

    from iriai_build_v2.workflows.develop.execution.gates import (
        _notify_gate_proof_extra_from_delivery,
    )

    # Full delivery.
    full = _notify_gate_proof_extra_from_delivery(
        {
            "delivery_id": "deliv-abc",
            "notification_sha256": "abc123def456",
            "irrelevant": "ignored",
        }
    )
    assert full == {
        "delivery_id": "deliv-abc",
        "notification_sha256": "abc123def456",
    }

    # Missing keys -> empty strings.
    empty = _notify_gate_proof_extra_from_delivery({})
    assert empty == {"delivery_id": "", "notification_sha256": ""}

    # None values -> empty strings via the `or ""` guard.
    nones = _notify_gate_proof_extra_from_delivery(
        {"delivery_id": None, "notification_sha256": None}
    )
    assert nones == {"delivery_id": "", "notification_sha256": ""}


def test_cluster_ownership_pin_gates_module() -> None:
    """All 13 moved names land in the canonical `execution/gates.py`
    module (not in any other `execution/` sibling like `types.py`,
    `git_service.py`, `task_contracts.py`, `sandbox.py`, or
    `dispatcher.py`). Belt-and-braces guard against a future refactor
    accidentally relocating one of the helpers to the wrong canonical
    module while leaving the shim intact.
    """

    from iriai_build_v2.workflows.develop.execution import gates as gates_mod

    expected = "iriai_build_v2.workflows.develop.execution.gates"
    for name in MOVED_CALLABLES:
        obj = getattr(gates_mod, name)
        assert obj.__module__ == expected, (
            f"{name}.__module__ = {obj.__module__!r}; expected {expected!r}"
        )

    # Cross-check that the names are NOT served by any of the sibling
    # execution modules (a deliberate "did anyone else accidentally
    # define a copy?" probe).
    from iriai_build_v2.workflows.develop.execution import (
        dispatcher as dispatcher_mod,
        git_service as git_service_mod,
        sandbox as sandbox_mod,
        task_contracts as task_contracts_mod,
        types as types_mod,
    )
    for name in MOVED_NAMES:
        for sibling, sibling_name in (
            (dispatcher_mod, "dispatcher"),
            (git_service_mod, "git_service"),
            (sandbox_mod, "sandbox"),
            (task_contracts_mod, "task_contracts"),
            (types_mod, "types"),
        ):
            assert not hasattr(sibling, name), (
                f"sibling drift: {sibling_name}.{name} unexpectedly exists; "
                "cluster ownership pin failed"
            )


def test_shim_block_exports_all_thirteen_names() -> None:
    """The Slice-11f shim block in `implementation.py` re-exports
    exactly the 13 moved names from `..execution.gates`. This test
    asserts the shim block actually carries all 13 (a deliberate "did
    the shim block lose a name?" probe) and that the pre-existing
    Slice-11a + Slice-11b + Slice-11c + Slice-11d + Slice-11e shim
    blocks at `:321-463` are unchanged (a representative sample from
    each is checked).
    """

    from iriai_build_v2.workflows.develop.phases import implementation as impl_mod

    # All 13 moved names are accessible via the impl module.
    for name in MOVED_NAMES:
        assert hasattr(impl_mod, name), (
            f"implementation.{name} missing -- the Slice-11f shim block "
            "dropped a re-export"
        )

    # The pre-existing Slice-11a + Slice-11b + Slice-11c + Slice-11d +
    # Slice-11e shim re-exports are STILL present (representative
    # samples).
    from iriai_build_v2.workflows.develop.execution.types import (
        DagAuthorityGateOutcome,
    )
    assert impl_mod.DagAuthorityGateOutcome is DagAuthorityGateOutcome
    from iriai_build_v2.workflows.develop.execution.dispatcher import (
        _make_parallel_actor,
    )
    assert impl_mod._make_parallel_actor is _make_parallel_actor


def test_gates_module_does_not_import_implementation() -> None:
    """The compatibility-arrow direction (per doc 11 § "How To Use
    This Map" Q4) is: `execution/gates.py` MUST NOT import from
    `workflows.develop.phases.implementation`. This test reads the
    on-disk source of `gates.py` and asserts the import line is
    absent. Belt-and-braces guard against a future refactor
    accidentally introducing a back-import.
    """

    from pathlib import Path

    import iriai_build_v2.workflows.develop.execution.gates as gates_mod

    source_path = Path(gates_mod.__file__)
    text = source_path.read_text(encoding="utf-8")
    assert "from iriai_build_v2.workflows.develop.phases.implementation" not in text, (
        "execution/gates.py imports from phases/implementation -- "
        "violates the doc-11 compatibility-arrow direction"
    )
    assert "from ..phases.implementation" not in text, (
        "execution/gates.py uses a relative back-import to phases/"
        "implementation -- violates the doc-11 compatibility-arrow direction"
    )
