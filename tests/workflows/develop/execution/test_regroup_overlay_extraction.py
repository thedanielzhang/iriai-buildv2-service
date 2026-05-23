"""Slice 11k -- extraction proof for `execution/regroup_overlay.py` extension.

Verifies the doc-11 § "How To Use This Map" four-question contract for the
pure ``DerivedDAGArtifact``-validator cluster extraction:

1. What behavior moved: seven pure ``DerivedDAGArtifact``-validator helpers --
   ``_validate_derived_dag_artifact_update(artifact_key, content, *, base_dag,
   base_dag_artifact_id, base_dag_sha256, boundary_checkpoint_exists,
   require_regroup_context)`` (the synchronous parser + validator entry-point),
   ``_validate_regroup_against_base_dag(parsed, *, base_dag,
   base_dag_artifact_id, base_dag_sha256, boundary_checkpoint_exists)`` (the
   pure regroup-vs-base contract validator),
   ``_regroup_task_definition_for_compare(task, *, remaining_task_ids)``
   (``ImplementationTask`` ``model_dump`` projection minus ``dependencies``),
   ``_regroup_hard_barrier_by_task(parsed, *, task_definitions_by_id)`` (the
   barrier-by-task index builder; carries a function-body lazy
   ``from ..dag_regroup import _barrier_for_task, semantic_lane_for_task``
   that is preserved byte-for-byte across the move because the relative
   import points "across" within the same ``workflows/develop/`` package
   from the destination ``workflows/develop/execution/regroup_overlay.py``),
   ``_derived_dag_write_set_conflicts(parsed, *, task_write_sets)`` (the
   pairwise overlap builder),
   ``_derived_dag_task_write_sets(parsed, *, task_definitions_by_id)`` (the
   write-set aggregator), and ``_regroup_task_declared_write_paths(task)``
   (the declared-write-paths reader) -- moved byte-for-byte from
   ``workflows/develop/phases/implementation.py`` to
   ``workflows/develop/execution/regroup_overlay.py``. The Slice-09a typed
   overlay schema + models + deterministic identifier derivations
   (``OverlayStatus``, ``RegroupActiveMarkerStatus``, ``OverlayBarrierSource``,
   ``SchedulerGroupMetricState``, ``SchedulerFeedbackDataQuality``,
   ``SchedulerFeedbackConfidence``, ``OverlayCompatibilityKeys``,
   ``OverlayBarrier``, ``OverlayTaskSpeedMetadata``,
   ``RegroupActivationContract``, ``RegroupRollbackPlan``,
   ``RegroupActiveMarker``, ``RegroupOverlay``, ``SchedulerGroupMetric``,
   ``SchedulerFeedback``, ``derive_overlay_id``, ``derive_overlay_slug``,
   ``derive_metric_id``) already in ``regroup_overlay.py`` is UNTOUCHED --
   Slice 11k EXTENDS, never modifies.

2. Which legacy import names still work: every existing
   ``from iriai_build_v2.workflows.develop.phases.implementation import X``
   for one of the seven moved names keeps resolving to the SAME object as
   the canonical definition in ``execution/regroup_overlay.py`` (the shim
   is ``is``-equivalent, not a copy). ``monkeypatch.setattr(
   implementation_module, X, ...)`` continues to mutate the SAME function
   object that any direct ``from execution.regroup_overlay import X``
   reader sees. The validator entry-point ``_validate_derived_dag_artifact_
   update`` is externally imported by ``workflows/develop/dag_regroup.py``
   (late import at ``:558``) and by 10+ ``tests/workflows/test_dag_*``
   test files via ``implementation_module._validate_derived_dag_artifact_
   update(...)``; each one continues working through the shim.

3. Which targeted tests prove the new facade and the compatibility shim:
   THIS file is the proof; it pins every moved name's shim equivalence,
   ``__module__`` rebinding, behavioral smoke against the validator entry-
   point + the write-set aggregator + the barrier index builder + the
   declared-write-paths reader + the regroup-vs-base contract validator,
   the cluster-ownership pin AND a back-import guard against
   ``regroup_overlay.py`` ever importing from ``implementation.py``.

4. Why is the PR still refactor-only: nothing else moves. The seven pure
   ``DerivedDAGArtifact``-validator helpers moved byte-for-byte; no
   contract change, no behavior change. The phase-level regroup-overlay
   PORT surface (the async runner+feature/store-coupled
   ``_resolve_active_regroup_before_group_dispatch`` at
   ``implementation.py:15561``,
   ``_resolve_active_regroup_typed_overlay`` at ``:15620``,
   ``_write_typed_regroup_observation`` at ``:15730``,
   ``_typed_regroup_active_overlay_probe`` at ``:15777``,
   ``_typed_regroup_active_overlay_offset`` at ``:15807``,
   ``_resolve_active_regroup_legacy_marker`` at ``:15858``, and
   ``_load_regroup_validation_context`` at ``:22176`` -- each one takes
   ``runner: WorkflowRunner`` + ``feature: Feature`` and/or uses
   ``_execution_control_store_for_runner`` + ``_merge_queue_connection`` +
   ``RegroupOverlayStore``/``RegroupOverlayResolver`` +
   ``_log_feature_event`` + ``_dag_artifact_record_for_key`` +
   ``runner.artifacts``) is genuinely PHASE-LEVEL and CORRECTLY stays in
   ``implementation.py`` per the prompt hard rule against splitting
   non-pure helpers. The ``DAG_REGROUP_*`` constants at
   ``implementation.py:723-727`` (``DAG_REGROUP_FROM_GROUP``,
   ``DAG_REGROUP_TO_GROUP``, ``DAG_REGROUP_CANONICAL_KEY``,
   ``DAG_REGROUP_ACTIVE_KEY``, ``DAG_REGROUP_OBSERVATION_KEY``) STAY
   because they are imported by ``phases/post_test_observation.py:38-39``
   via the legacy ``from .implementation import …`` path and live near
   the resolver orchestrators they support.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from iriai_build_v2.models.outputs import (
    DerivedDAGArtifact,
    ImplementationDAG,
    ImplementationTask,
    TaskFileScope,
)


# Each entry is a name moved from ``implementation.py`` to
# ``execution/regroup_overlay.py`` in Slice 11k. The order is the import-
# line order in the Slice-11k shim block in ``implementation.py`` so a grep
# over either file lists the names in the same order.
MOVED_NAMES = [
    "_derived_dag_task_write_sets",
    "_derived_dag_write_set_conflicts",
    "_regroup_hard_barrier_by_task",
    "_regroup_task_declared_write_paths",
    "_regroup_task_definition_for_compare",
    "_validate_derived_dag_artifact_update",
    "_validate_regroup_against_base_dag",
]

# All seven moved names are module-level functions; each has a
# ``__module__``.
MOVED_CALLABLES = list(MOVED_NAMES)


# -- Identity + module-rebind --------------------------------------------------


@pytest.mark.parametrize("name", MOVED_NAMES)
def test_shim_re_export_is_same_object_as_new_canonical(name: str) -> None:
    """Every moved helper imported via the OLD path is the SAME object as
    the import via the NEW canonical path. Proves the shim is a re-export,
    not a copy. Locks the monkeypatch target equivalence --
    ``monkeypatch.setattr(implementation_module, name, ...)`` will mutate
    the SAME function object that any direct
    ``from execution.regroup_overlay import name`` reader sees.
    """

    from iriai_build_v2.workflows.develop import execution as execution_pkg
    from iriai_build_v2.workflows.develop.execution import (
        regroup_overlay as regroup_overlay_mod,
    )
    from iriai_build_v2.workflows.develop.phases import (
        implementation as impl_mod,
    )

    legacy = getattr(impl_mod, name)
    canonical = getattr(regroup_overlay_mod, name)
    assert legacy is canonical, (
        f"shim drift: implementation.{name} is not the same object as "
        f"execution.regroup_overlay.{name}"
    )
    # ``execution_pkg`` is imported only to ensure the package import chain
    # works end-to-end (no side-effect import errors).
    _ = execution_pkg


@pytest.mark.parametrize("name", MOVED_CALLABLES)
def test_canonical_module_is_regroup_overlay(name: str) -> None:
    """The moved function objects' ``__module__`` is the new canonical
    ``iriai_build_v2.workflows.develop.execution.regroup_overlay`` -- not
    the legacy ``...phases.implementation``. Proves the definition
    genuinely moved rather than being re-aliased from the old module.
    """

    from iriai_build_v2.workflows.develop.execution import (
        regroup_overlay as regroup_overlay_mod,
    )

    canonical = getattr(regroup_overlay_mod, name)
    assert canonical.__module__ == (
        "iriai_build_v2.workflows.develop.execution.regroup_overlay"
    ), (
        f"{name}.__module__ = {canonical.__module__!r}; expected the new "
        "regroup_overlay-module path"
    )


# -- Behavioral smoke ----------------------------------------------------------


def _build_simple_task(
    task_id: str,
    *,
    files: list[str] | None = None,
    file_scope: list[dict] | None = None,
    repo_path: str = "",
    dependencies: list[str] | None = None,
) -> ImplementationTask:
    """Build a minimal ``ImplementationTask`` for smoke tests."""

    return ImplementationTask(
        id=task_id,
        name=f"Task {task_id}",
        description=f"Test task {task_id}",
        repo_path=repo_path,
        files=files or [],
        file_scope=[TaskFileScope(**scope) for scope in (file_scope or [])],
        dependencies=dependencies or [],
        verification_gates=[],
    )


def _build_simple_dag(tasks: list[ImplementationTask]) -> ImplementationDAG:
    """Build a minimal ``ImplementationDAG`` over the given tasks with each
    task in its own group in id order.
    """

    return ImplementationDAG(
        tasks=tasks,
        execution_order=[[task.id] for task in tasks],
    )


def test_validate_derived_dag_artifact_update_rejects_non_derived_key() -> None:
    """The entry-point fail-closes on any artifact_key that does not match
    ``_is_derived_dag_artifact_key`` (the Slice-11h predicate at
    ``execution/repair.py:1242``). Returns ``"not_derived_dag_artifact"``
    with no parsed payload and an empty details list.
    """

    from iriai_build_v2.workflows.develop.execution.regroup_overlay import (
        _validate_derived_dag_artifact_update,
    )

    parsed, reason, details = _validate_derived_dag_artifact_update(
        "dag",  # NOT a derived dag artifact key (no ``dag-regroup:`` prefix etc.)
        "{}",
    )
    assert parsed is None
    assert reason == "not_derived_dag_artifact"
    assert details == []


def test_validate_derived_dag_artifact_update_rejects_invalid_json() -> None:
    """A malformed JSON body returns
    ``"invalid_derived_dag_artifact_json"`` with the parser error string
    in the details list (fail-soft: no exception escapes).
    """

    from iriai_build_v2.workflows.develop.execution.regroup_overlay import (
        _validate_derived_dag_artifact_update,
    )

    parsed, reason, details = _validate_derived_dag_artifact_update(
        "dag-regroup:g45-g73",
        "{not_json",
    )
    assert parsed is None
    assert reason == "invalid_derived_dag_artifact_json"
    assert details
    assert "error" in details[0]


def test_validate_derived_dag_artifact_update_returns_parsed_on_minimal_valid_input() -> None:
    """On a minimal valid ``DerivedDAGArtifact`` JSON body the validator
    returns the parsed Pydantic model + an empty reason + a details list
    carrying task_count / group_count / source_dag_key /
    checkpointed_group / group_idx_offset / activation_plan_count. Uses
    a ``derived-dag:`` (non-regroup) artifact_key prefix to exercise the
    "general derived DAG" path without the regroup-only requirements
    (checkpointed_group, group_idx_offset, original_to_new_group_mapping,
    rollback_plan, activation_contract); those are exercised by the
    dedicated ``_validate_regroup_against_base_dag`` tests below.
    """

    from iriai_build_v2.workflows.develop.execution.regroup_overlay import (
        _validate_derived_dag_artifact_update,
    )

    task_a = _build_simple_task("a", files=["a.py"])
    task_b = _build_simple_task("b", files=["b.py"])
    dag = _build_simple_dag([task_a, task_b])
    derived = DerivedDAGArtifact(
        schema_version=1,
        # ``derived-dag:`` prefix accepted by ``_is_derived_dag_artifact_key``
        # (at ``execution/repair.py:1242``); the ``dag-regroup:`` prefix-only
        # requirements (checkpointed_group, group_idx_offset, etc.) are NOT
        # enforced on non-regroup derived DAG artifacts.
        artifact_key="derived-dag:smoke-test",
        source_dag_key="dag",
        base_dag_artifact_id=None,
        base_dag_sha256="",
        checkpointed_group=None,
        group_idx_offset=None,
        original_execution_order=[],
        original_to_new_group_mapping={},
        rollback_plan=[],
        activation_contract=[],
        activation_plan=[],
        barriers=[],
        write_sets={},
        speed_index={},
        dag=dag,
    )

    parsed, reason, details = _validate_derived_dag_artifact_update(
        "derived-dag:smoke-test",
        derived.model_dump_json(),
    )
    assert parsed is not None
    assert reason == ""
    assert details and details[0]["task_count"] == 2
    assert details[0]["group_count"] == 2
    assert details[0]["source_dag_key"] == "dag"


def test_validate_derived_dag_artifact_update_artifact_key_mismatch() -> None:
    """A ``DerivedDAGArtifact.artifact_key`` that does not match the
    caller-supplied key returns ``"derived_dag_artifact_key_mismatch"``
    with both keys in the details payload. Both keys must be recognized
    by ``_is_derived_dag_artifact_key`` -- otherwise the early
    ``"not_derived_dag_artifact"`` exit short-circuits before the
    key-mismatch check.
    """

    from iriai_build_v2.workflows.develop.execution.regroup_overlay import (
        _validate_derived_dag_artifact_update,
    )

    task = _build_simple_task("a", files=["a.py"])
    dag = _build_simple_dag([task])
    derived = DerivedDAGArtifact(
        schema_version=1,
        artifact_key="dag-regroup:g45-g73",  # body says g45-g73
        source_dag_key="dag",
        base_dag_artifact_id=None,
        base_dag_sha256="",
        checkpointed_group=None,
        group_idx_offset=None,
        original_execution_order=[],
        original_to_new_group_mapping={},
        rollback_plan=[],
        activation_contract=[],
        activation_plan=[],
        barriers=[],
        write_sets={},
        speed_index={},
        dag=dag,
    )

    parsed, reason, details = _validate_derived_dag_artifact_update(
        # Caller key is ALSO a recognized derived-dag key but disagrees
        # with the parsed body's artifact_key; the key-mismatch check
        # therefore fires.
        "dag-regroup:other-slug",
        derived.model_dump_json(),
    )
    assert parsed is None
    assert reason == "derived_dag_artifact_key_mismatch"
    assert details
    assert details[0]["expected_artifact_key"] == "dag-regroup:other-slug"
    assert details[0]["actual_artifact_key"] == "dag-regroup:g45-g73"


def test_regroup_task_definition_for_compare_drops_dependencies() -> None:
    """The pure ``model_dump`` projection minus the ``dependencies``
    field. Two tasks with identical content but different dependencies
    produce identical projections (the projection is dependency-
    insensitive by design so the regroup contract validator can compare
    task definitions across the rewrite without false mismatches from
    intentional dependency restructuring).
    """

    from iriai_build_v2.workflows.develop.execution.regroup_overlay import (
        _regroup_task_definition_for_compare,
    )

    task_with_deps = _build_simple_task("a", files=["a.py"], dependencies=["root"])
    task_no_deps = _build_simple_task("a", files=["a.py"], dependencies=[])

    proj_with = _regroup_task_definition_for_compare(
        task_with_deps, remaining_task_ids={"root", "a"}
    )
    proj_without = _regroup_task_definition_for_compare(
        task_no_deps, remaining_task_ids={"root", "a"}
    )

    # Both projections drop ``dependencies`` so they are equal regardless
    # of the input dependencies field.
    assert "dependencies" not in proj_with
    assert "dependencies" not in proj_without
    assert proj_with == proj_without


def test_regroup_task_declared_write_paths_includes_repo_prefixed_and_bare() -> None:
    """The declared-write-paths reader returns both the bare path AND the
    ``{repo_path}/{path}`` variant when a repo_path is set, so the
    write-set aggregator can match either spelling. ``read_only`` scopes
    are excluded.
    """

    from iriai_build_v2.workflows.develop.execution.regroup_overlay import (
        _regroup_task_declared_write_paths,
    )

    task = _build_simple_task(
        "a",
        repo_path="backend",
        files=["src/foo.py"],
        file_scope=[
            {"path": "src/bar.py", "action": "write"},
            # ``read_only`` scope must be excluded.
            {"path": "src/baz.py", "action": "read_only"},
        ],
    )
    paths = _regroup_task_declared_write_paths(task)
    # Both bare and repo-prefixed spellings of each writable path appear.
    assert "src/foo.py" in paths
    assert "backend/src/foo.py" in paths
    assert "src/bar.py" in paths
    assert "backend/src/bar.py" in paths
    # ``read_only`` scope is excluded.
    assert "src/baz.py" not in paths
    assert "backend/src/baz.py" not in paths


def test_derived_dag_task_write_sets_merges_task_and_artifact_write_sets() -> None:
    """The write-set aggregator merges both (a) the declared
    ``ImplementationTask`` write paths (via
    ``_regroup_task_declared_write_paths``) and (b) the explicit
    ``DerivedDAGArtifact.write_sets`` mapping from the artifact body.
    Either source contributes; the union is what the conflict detector
    sees.
    """

    from iriai_build_v2.workflows.develop.execution.regroup_overlay import (
        _derived_dag_task_write_sets,
    )

    task_a = _build_simple_task("a", files=["a.py"])
    task_b = _build_simple_task("b", files=["b.py"])
    derived = DerivedDAGArtifact(
        schema_version=1,
        artifact_key="review:dag-regroup-draft:custom",
        source_dag_key="dag",
        base_dag_artifact_id=None,
        base_dag_sha256="",
        checkpointed_group=None,
        group_idx_offset=None,
        original_execution_order=[],
        original_to_new_group_mapping={},
        rollback_plan=[],
        activation_contract=[],
        activation_plan=[],
        barriers=[],
        # Explicit artifact-level write_sets for task "a" -- merged with the
        # task's own declared write paths.
        write_sets={"a": ["override-a.py"]},
        speed_index={},
        dag=_build_simple_dag([task_a, task_b]),
    )

    write_sets = _derived_dag_task_write_sets(derived)
    assert "a.py" in write_sets["a"]
    assert "override-a.py" in write_sets["a"]
    assert "b.py" in write_sets["b"]


def test_derived_dag_write_set_conflicts_flags_same_group_overlap() -> None:
    """The pairwise overlap detector flags any pair of tasks in the SAME
    execution-order group that touch overlapping write paths. Tasks in
    different groups never conflict (the regroup contract allows
    parallel groups to refactor disjoint scopes).
    """

    from iriai_build_v2.workflows.develop.execution.regroup_overlay import (
        _derived_dag_write_set_conflicts,
    )

    task_a = _build_simple_task("a", files=["shared.py"])
    task_b = _build_simple_task("b", files=["shared.py", "b.py"])
    task_c = _build_simple_task("c", files=["c.py"])
    # Place a + b in the same group, c on its own; a+b conflict on
    # shared.py.
    dag = ImplementationDAG(
        tasks=[task_a, task_b, task_c],
        execution_order=[["a", "b"], ["c"]],
    )
    derived = DerivedDAGArtifact(
        schema_version=1,
        artifact_key="review:dag-regroup-draft:custom",
        source_dag_key="dag",
        base_dag_artifact_id=None,
        base_dag_sha256="",
        checkpointed_group=None,
        group_idx_offset=None,
        original_execution_order=[],
        original_to_new_group_mapping={},
        rollback_plan=[],
        activation_contract=[],
        activation_plan=[],
        barriers=[],
        write_sets={},
        speed_index={},
        dag=dag,
    )

    conflicts = _derived_dag_write_set_conflicts(derived)
    assert len(conflicts) == 1
    only = conflicts[0]
    assert only["group_idx"] == 0
    # The conflict is on the same group between a + b; sort order of
    # ``owners`` is preserved.
    assert {only["left"], only["right"]} == {"a", "b"}
    assert "shared.py" in only["overlap"]


def test_regroup_hard_barrier_by_task_with_dag_regroup_module_lazy_import() -> None:
    """The barrier index builder carries a function-body lazy import:
    ``from ..dag_regroup import _barrier_for_task, semantic_lane_for_task``.
    The relative import is preserved byte-for-byte across the Slice 11k
    move and resolves correctly from the new canonical
    ``execution/regroup_overlay.py`` location because the destination has
    a sibling ``..dag_regroup`` under ``workflows/develop/``.

    When ``task_definitions_by_id`` is non-empty, the helper short-
    circuits through the lazy ``..dag_regroup`` import and returns a
    barrier-by-task mapping derived from each task's semantic lane.
    """

    from iriai_build_v2.workflows.develop.execution.regroup_overlay import (
        _regroup_hard_barrier_by_task,
    )

    task_a = _build_simple_task(
        "a",
        files=["src/foo.py"],
    )
    task_b = _build_simple_task(
        "b",
        files=["src/bar.py"],
    )
    derived = DerivedDAGArtifact(
        schema_version=1,
        artifact_key="review:dag-regroup-draft:custom",
        source_dag_key="dag",
        base_dag_artifact_id=None,
        base_dag_sha256="",
        checkpointed_group=None,
        group_idx_offset=None,
        original_execution_order=[],
        original_to_new_group_mapping={},
        rollback_plan=[],
        activation_contract=[],
        activation_plan=[],
        barriers=[],
        write_sets={},
        speed_index={},
        dag=_build_simple_dag([task_a, task_b]),
    )

    # Lazy ``..dag_regroup`` import path: ``task_definitions_by_id`` is
    # provided so the early-return branch runs.
    barriers = _regroup_hard_barrier_by_task(
        derived,
        task_definitions_by_id={"a": task_a, "b": task_b},
    )
    # Each task gets SOME barrier id (the specific string depends on the
    # task's semantic lane; ``misc`` is the default lane for the empty
    # task text). The lazy import must resolve.
    assert set(barriers.keys()) == {"a", "b"}
    for task_id in ("a", "b"):
        assert isinstance(barriers[task_id], str)
        assert barriers[task_id]


def test_regroup_hard_barrier_by_task_from_artifact_barriers_field_when_no_task_defs() -> None:
    """When ``task_definitions_by_id`` is ``None`` (or empty), the helper
    falls back to building the index from the artifact's own
    ``barriers`` list. Each barrier dict supplies an id (via
    ``id``/``barrier_id``/``name``/``kind``) and a ``task_ids`` list of
    members. ``hard=False`` barriers are skipped.
    """

    from iriai_build_v2.workflows.develop.execution.regroup_overlay import (
        _regroup_hard_barrier_by_task,
    )

    task_a = _build_simple_task("a", files=["a.py"])
    task_b = _build_simple_task("b", files=["b.py"])
    derived = DerivedDAGArtifact(
        schema_version=1,
        artifact_key="review:dag-regroup-draft:custom",
        source_dag_key="dag",
        base_dag_artifact_id=None,
        base_dag_sha256="",
        checkpointed_group=None,
        group_idx_offset=None,
        original_execution_order=[],
        original_to_new_group_mapping={},
        rollback_plan=[],
        activation_contract=[],
        activation_plan=[],
        barriers=[
            {"id": "hard-1", "task_ids": ["a"], "hard": True},
            # Soft barriers are skipped.
            {"id": "soft-1", "task_ids": ["b"], "hard": False},
        ],
        write_sets={},
        speed_index={},
        dag=_build_simple_dag([task_a, task_b]),
    )

    barriers = _regroup_hard_barrier_by_task(derived)
    assert barriers.get("a") == "hard-1"
    assert "b" not in barriers


def test_validate_regroup_against_base_dag_passes_on_identity_rewrite() -> None:
    """A ``DerivedDAGArtifact`` whose derived order is byte-identical to
    the base DAG's tail (an identity rewrite) and whose
    ``original_to_new_group_mapping`` is the identity passes the regroup
    contract validator with an empty reason.
    """

    from iriai_build_v2.workflows.develop.execution.regroup_overlay import (
        _validate_regroup_against_base_dag,
    )

    task_a = _build_simple_task("a", files=["a.py"])
    task_b = _build_simple_task("b", files=["b.py"])
    base_dag = _build_simple_dag([task_a, task_b])
    derived = DerivedDAGArtifact(
        schema_version=1,
        artifact_key="dag-regroup:g0-g1",
        source_dag_key="dag",
        base_dag_artifact_id=42,
        base_dag_sha256="abc",
        checkpointed_group=-1,  # boundary before group 0
        group_idx_offset=0,
        original_execution_order=[["a"], ["b"]],  # same as base tail
        original_to_new_group_mapping={"0": [0], "1": [1]},  # identity
        rollback_plan=[],
        activation_contract=[],
        activation_plan=[],
        barriers=[],
        write_sets={},
        speed_index={},
        dag=_build_simple_dag([task_a, task_b]),
    )

    reason, details = _validate_regroup_against_base_dag(
        derived,
        base_dag=base_dag,
        base_dag_artifact_id=42,
        base_dag_sha256="abc",
        boundary_checkpoint_exists=False,
    )
    assert reason == ""
    assert details == []


def test_validate_regroup_against_base_dag_flags_base_dag_artifact_id_mismatch() -> None:
    """The contract validator fails closed when the caller's
    ``base_dag_artifact_id`` disagrees with the artifact's own
    ``base_dag_artifact_id``. Returns
    ``"dag_regroup_base_dag_artifact_mismatch"`` with both ids in the
    details payload.
    """

    from iriai_build_v2.workflows.develop.execution.regroup_overlay import (
        _validate_regroup_against_base_dag,
    )

    task_a = _build_simple_task("a", files=["a.py"])
    base_dag = _build_simple_dag([task_a])
    derived = DerivedDAGArtifact(
        schema_version=1,
        artifact_key="dag-regroup:g0-g0",
        source_dag_key="dag",
        base_dag_artifact_id=42,  # body says 42
        base_dag_sha256="abc",
        checkpointed_group=-1,
        group_idx_offset=0,
        original_execution_order=[["a"]],
        original_to_new_group_mapping={"0": [0]},
        rollback_plan=[],
        activation_contract=[],
        activation_plan=[],
        barriers=[],
        write_sets={},
        speed_index={},
        dag=_build_simple_dag([task_a]),
    )

    reason, details = _validate_regroup_against_base_dag(
        derived,
        base_dag=base_dag,
        base_dag_artifact_id=999,  # caller disagrees
        base_dag_sha256="abc",
        boundary_checkpoint_exists=False,
    )
    assert reason == "dag_regroup_base_dag_artifact_mismatch"
    assert details
    assert details[0]["expected_base_dag_artifact_id"] == 999
    assert details[0]["actual_base_dag_artifact_id"] == 42


def test_validate_regroup_against_base_dag_fails_when_boundary_checkpoint_exists() -> None:
    """When ``boundary_checkpoint_exists`` is True (the
    ``dag-group:{group_idx_offset}`` checkpoint is already committed),
    the regroup is fail-closed because activating a regroup AFTER its
    boundary checkpoint has been committed would re-do work; the doc 09
    "forbidden checkpoint" check enforces this. Returns
    ``"dag_regroup_boundary_checkpoint_exists"`` with the forbidden
    checkpoint key.
    """

    from iriai_build_v2.workflows.develop.execution.regroup_overlay import (
        _validate_regroup_against_base_dag,
    )

    task_a = _build_simple_task("a", files=["a.py"])
    base_dag = _build_simple_dag([task_a])
    derived = DerivedDAGArtifact(
        schema_version=1,
        artifact_key="dag-regroup:g0-g0",
        source_dag_key="dag",
        base_dag_artifact_id=None,
        base_dag_sha256="",
        checkpointed_group=-1,
        group_idx_offset=0,
        original_execution_order=[["a"]],
        original_to_new_group_mapping={"0": [0]},
        rollback_plan=[],
        activation_contract=[],
        activation_plan=[],
        barriers=[],
        write_sets={},
        speed_index={},
        dag=_build_simple_dag([task_a]),
    )

    reason, details = _validate_regroup_against_base_dag(
        derived,
        base_dag=base_dag,
        base_dag_artifact_id=None,
        base_dag_sha256=None,
        boundary_checkpoint_exists=True,  # post-boundary; forbidden
    )
    assert reason == "dag_regroup_boundary_checkpoint_exists"
    assert details
    assert details[0]["checkpoint_key"] == "dag-group:0"


# -- Structural ----------------------------------------------------------------


def test_cluster_ownership_pin_regroup_overlay_module() -> None:
    """All seven moved names land in the canonical
    ``execution/regroup_overlay.py`` module (not in any other
    ``execution/`` sibling like ``types.py``, ``git_service.py``,
    ``task_contracts.py``, ``sandbox.py``, ``dispatcher.py``,
    ``gates.py``, ``verification.py``, ``repair.py``,
    ``failure_router.py``, or ``merge_queue.py``). Belt-and-braces
    guard against a future refactor accidentally relocating one of the
    helpers to the wrong canonical module while leaving the shim
    intact.
    """

    from iriai_build_v2.workflows.develop.execution import (
        regroup_overlay as regroup_overlay_mod,
    )

    expected = "iriai_build_v2.workflows.develop.execution.regroup_overlay"
    for name in MOVED_CALLABLES:
        obj = getattr(regroup_overlay_mod, name)
        assert obj.__module__ == expected, (
            f"{name}.__module__ = {obj.__module__!r}; expected {expected!r}"
        )

    # Cross-check that the names are NOT served by any of the sibling
    # execution modules (a deliberate "did anyone else accidentally define
    # a copy?" probe). The sibling ``regroup_overlay_validation.py``
    # carries the Slice-09b doc-09 13-step ``validate_overlay`` for typed
    # overlays + the related cluster mirrors at ``:723, :1091`` (which
    # parallel the moved helpers' algorithms by docstring); it MUST NOT
    # redefine the moved names. The canonical home is the
    # ``regroup_overlay.py`` module Slice 11k extends.
    from iriai_build_v2.workflows.develop.execution import (
        dispatcher as dispatcher_mod,
        failure_router as failure_router_mod,
        gates as gates_mod,
        git_service as git_service_mod,
        merge_queue as merge_queue_mod,
        regroup_overlay_validation as regroup_overlay_validation_mod,
        repair as repair_mod,
        sandbox as sandbox_mod,
        task_contracts as task_contracts_mod,
        types as types_mod,
        verification as verification_mod,
    )
    for name in MOVED_NAMES:
        for sibling, sibling_name in (
            (dispatcher_mod, "dispatcher"),
            (failure_router_mod, "failure_router"),
            (gates_mod, "gates"),
            (git_service_mod, "git_service"),
            (merge_queue_mod, "merge_queue"),
            (regroup_overlay_validation_mod, "regroup_overlay_validation"),
            (repair_mod, "repair"),
            (sandbox_mod, "sandbox"),
            (task_contracts_mod, "task_contracts"),
            (types_mod, "types"),
            (verification_mod, "verification"),
        ):
            assert not hasattr(sibling, name), (
                f"sibling drift: {sibling_name}.{name} unexpectedly exists; "
                "cluster ownership pin failed"
            )


def test_shim_block_exports_all_seven_names() -> None:
    """The Slice-11k shim block in ``implementation.py`` re-exports
    exactly the seven moved names from ``..execution.regroup_overlay``.
    This test asserts the shim block actually carries all seven (a
    deliberate "did the shim block lose a name?" probe).
    """

    from iriai_build_v2.workflows.develop.execution.regroup_overlay import (
        _derived_dag_task_write_sets,
        _derived_dag_write_set_conflicts,
        _regroup_hard_barrier_by_task,
        _regroup_task_declared_write_paths,
        _regroup_task_definition_for_compare,
        _validate_derived_dag_artifact_update,
        _validate_regroup_against_base_dag,
    )
    from iriai_build_v2.workflows.develop.phases import (
        implementation as impl_mod,
    )

    # All seven moved names accessible via the impl module.
    for name in MOVED_NAMES:
        assert hasattr(impl_mod, name), (
            f"implementation.{name} missing -- the Slice-11k shim block "
            "dropped a re-export"
        )

    # All seven shim entries point to the SAME canonical objects.
    assert (
        impl_mod._derived_dag_task_write_sets is _derived_dag_task_write_sets
    )
    assert (
        impl_mod._derived_dag_write_set_conflicts
        is _derived_dag_write_set_conflicts
    )
    assert (
        impl_mod._regroup_hard_barrier_by_task is _regroup_hard_barrier_by_task
    )
    assert (
        impl_mod._regroup_task_declared_write_paths
        is _regroup_task_declared_write_paths
    )
    assert (
        impl_mod._regroup_task_definition_for_compare
        is _regroup_task_definition_for_compare
    )
    assert (
        impl_mod._validate_derived_dag_artifact_update
        is _validate_derived_dag_artifact_update
    )
    assert (
        impl_mod._validate_regroup_against_base_dag
        is _validate_regroup_against_base_dag
    )

    # The pre-existing Slice-09a typed overlay schema + models +
    # deterministic identifier derivations
    # (``OverlayStatus`` / ``RegroupActiveMarkerStatus`` /
    # ``OverlayBarrierSource`` / ``SchedulerGroupMetricState`` /
    # ``SchedulerFeedbackDataQuality`` / ``SchedulerFeedbackConfidence`` /
    # ``OverlayCompatibilityKeys`` / ``OverlayBarrier`` /
    # ``OverlayTaskSpeedMetadata`` / ``RegroupActivationContract`` /
    # ``RegroupRollbackPlan`` / ``RegroupActiveMarker`` /
    # ``RegroupOverlay`` / ``SchedulerGroupMetric`` /
    # ``SchedulerFeedback`` / ``derive_overlay_id`` /
    # ``derive_overlay_slug`` / ``derive_metric_id``) are STILL present
    # in the canonical module and STILL point to the canonical Slice-09a
    # types (the Slice-09a surface is untouched by Slice 11k).
    from iriai_build_v2.workflows.develop.execution.regroup_overlay import (
        derive_metric_id,
        derive_overlay_id,
        derive_overlay_slug,
        OverlayBarrier,
        OverlayBarrierSource,
        OverlayCompatibilityKeys,
        OverlayStatus,
        OverlayTaskSpeedMetadata,
        RegroupActivationContract,
        RegroupActiveMarker,
        RegroupActiveMarkerStatus,
        RegroupOverlay,
        RegroupRollbackPlan,
        SchedulerFeedback,
        SchedulerFeedbackConfidence,
        SchedulerFeedbackDataQuality,
        SchedulerGroupMetric,
        SchedulerGroupMetricState,
    )
    # All Slice-09a names continue to exist + be importable from the
    # canonical module location.
    for name in (
        "OverlayStatus",
        "RegroupActiveMarkerStatus",
        "OverlayBarrierSource",
        "SchedulerGroupMetricState",
        "SchedulerFeedbackDataQuality",
        "SchedulerFeedbackConfidence",
        "OverlayCompatibilityKeys",
        "OverlayBarrier",
        "OverlayTaskSpeedMetadata",
        "RegroupActivationContract",
        "RegroupRollbackPlan",
        "RegroupActiveMarker",
        "RegroupOverlay",
        "SchedulerGroupMetric",
        "SchedulerFeedback",
        "derive_overlay_id",
        "derive_overlay_slug",
        "derive_metric_id",
    ):
        from iriai_build_v2.workflows.develop.execution import (
            regroup_overlay as regroup_overlay_mod,
        )
        assert hasattr(regroup_overlay_mod, name), (
            f"Slice-09a {name} lost from execution/regroup_overlay.py "
            "post-11k"
        )


def test_regroup_overlay_module_does_not_import_implementation() -> None:
    """The compatibility-arrow direction (per doc 11 § "How To Use This
    Map" Q4) is: ``execution/regroup_overlay.py`` MUST NOT import from
    ``workflows.develop.phases.implementation``. This test reads the
    on-disk source of ``regroup_overlay.py`` and asserts the import
    line is absent. Belt-and-braces guard against a future refactor
    accidentally introducing a back-import.
    """

    import iriai_build_v2.workflows.develop.execution.regroup_overlay as regroup_overlay_mod

    source_path = Path(regroup_overlay_mod.__file__)
    text = source_path.read_text(encoding="utf-8")
    assert (
        "from iriai_build_v2.workflows.develop.phases.implementation"
        not in text
    ), (
        "execution/regroup_overlay.py imports from phases/implementation -- "
        "violates the doc-11 compatibility-arrow direction"
    )
    assert "from ..phases.implementation" not in text, (
        "execution/regroup_overlay.py uses a relative back-import to "
        "phases/implementation -- violates the doc-11 compatibility-arrow "
        "direction"
    )


def test_all_export_includes_seven_moved_names() -> None:
    """``regroup_overlay.py.__all__`` includes all seven moved names.
    Belt-and-braces probe against a refactor that forgets to add the new
    public symbols to the module's public surface (which would cause
    ``from execution.regroup_overlay import *`` to silently lose them).
    """

    from iriai_build_v2.workflows.develop.execution import (
        regroup_overlay as regroup_overlay_mod,
    )

    for name in MOVED_NAMES:
        assert name in regroup_overlay_mod.__all__, (
            f"{name} missing from execution/regroup_overlay.py __all__"
        )
