"""Structural tests pinning the Slice 11m ``execution/post_test_guard.py``
canonical-home reservation.

Slice 11m extracts the ``execution/post_test_guard.py`` boundary per
``docs/execution-control-plane/11-refactor-map.md`` § "Boundary-level
API contracts" row for ``execution/post_test_guard.py``:
"``PostTestReadinessGuard.assert_ready(feature_id)``. Effective-DAG
completion checks, post-DAG gate completion checks, and no-active-
control-plane-work checks before ``PostTestObservationPhase``. Must
not own: Collecting post-test observations or dispatching product
fixes."

**Slice 11m is a legitimate near-no-op sub-slice.** The exhaustive
search of ``workflows/develop/phases/implementation.py`` (32489 lines
at end of Slice 11l, 489 function defs) found **zero** pure
post-test-guard primitives:

- No ``_post_test_*`` / ``_test_quiesce_*`` / ``PostTestReadiness*``
  / ``assert_ready`` function definitions or constants.
- The post-test logic lives entirely in
  ``workflows/develop/phases/post_test_observation.py`` (26 defs +
  the ``PostTestObservationPhase`` class), which is a SIBLING PHASE
  and OUT OF SCOPE for Slice 11m per the prompt edge-case clause.
- The semantically-adjacent ``_post_dag_gate_*`` family was already
  moved by Slice 11l to ``execution/post_dag_gates.py``.

Slice 11m therefore CREATES the canonical-home module with an empty
``__all__`` to reserve the boundary surface for future
``PostTestReadinessGuard.assert_ready()`` work, and pins the
structural contract via this test file. No shim block is added to
``implementation.py`` (there are no names to re-export; an empty
shim would be a falsifiable artifact per the prompt hard rule
against fabricating coverage). When future slices land the
``PostTestReadinessGuard.assert_ready()`` implementation, the
structural tests below will be extended with identity /
``__module__`` / behavioral smoke tests mirroring the
Slice-11a / Slice-11l / Slice-11b through Slice-11k pattern.

This file deliberately MIRRORS the structural-tests subset of
``test_post_dag_gates_extraction.py`` (the cluster-ownership pin,
the back-import guard, the ``__all__`` export probe) and ADDS the
near-no-op-specific pin (the no-shim-block contract +
no-PostTestReadiness-name-in-siblings contract).
"""

from __future__ import annotations

from pathlib import Path


CANONICAL_MODULE_PATH = (
    "iriai_build_v2.workflows.develop.execution.post_test_guard"
)


# --- Module-existence + canonical-home contract ---------------------------


def test_post_test_guard_module_imports_cleanly() -> None:
    """The Slice-11m canonical-home module
    ``execution/post_test_guard.py`` imports without error. Pins the
    module-creation contract (the third CREATE pattern in Slice 11,
    after Slice 11a ``types.py`` and Slice 11l ``post_dag_gates.py``).
    """

    import iriai_build_v2.workflows.develop.execution.post_test_guard as guard_mod

    assert guard_mod.__name__ == CANONICAL_MODULE_PATH


def test_post_test_guard_module_lives_in_execution_package() -> None:
    """The canonical-home module file lives under the
    ``workflows/develop/execution/`` package. Pins the
    boundary-contract directory layout per doc 11 § "Proposed Module
    Boundaries".
    """

    import iriai_build_v2.workflows.develop.execution.post_test_guard as guard_mod

    source_path = Path(guard_mod.__file__)
    assert source_path.name == "post_test_guard.py"
    assert source_path.parent.name == "execution"
    assert source_path.parent.parent.name == "develop"
    assert source_path.parent.parent.parent.name == "workflows"


# --- Empty-__all__ + near-no-op contract ----------------------------------


def test_post_test_guard_all_is_empty_list() -> None:
    """Slice 11m did not move any pure post-test-guard primitives
    (the exhaustive search of ``implementation.py`` found zero
    candidates). The ``__all__`` list MUST be empty to reflect this
    legitimate near-no-op finding. A non-empty ``__all__`` would be
    a fabricated-coverage smell per the prompt hard rule against
    falsifiable artifacts.
    """

    import iriai_build_v2.workflows.develop.execution.post_test_guard as guard_mod

    assert hasattr(guard_mod, "__all__"), (
        "post_test_guard.py is missing the __all__ declaration -- the "
        "canonical-home contract requires an explicit (even if empty) "
        "public surface."
    )
    assert guard_mod.__all__ == [], (
        f"post_test_guard.__all__ = {guard_mod.__all__!r}; Slice 11m "
        "is a near-no-op (zero pure post-test-guard primitives found "
        "in implementation.py) so __all__ MUST be empty until a future "
        "slice lands PostTestReadinessGuard.assert_ready()."
    )


def test_post_test_guard_does_not_define_any_public_names() -> None:
    """Belt-and-braces guard against a future refactor accidentally
    adding a name to ``post_test_guard.py`` without also adding it to
    ``__all__`` (which would create a public-surface drift the
    ``__all__`` test alone could miss).
    """

    import iriai_build_v2.workflows.develop.execution.post_test_guard as guard_mod

    public_names = [name for name in dir(guard_mod) if not name.startswith("_")]
    # ``__future__.annotations`` and stdlib module-attribute names like
    # ``__name__``, ``__doc__``, ``__file__``, ``__loader__``, etc. are
    # dunder-prefixed and excluded by the startswith("_") filter.
    # ``annotations`` from ``__future__`` is the only non-dunder name
    # that might leak through; we explicitly tolerate it.
    tolerated = {"annotations"}
    leaked = [name for name in public_names if name not in tolerated]
    assert leaked == [], (
        f"post_test_guard.py defines unexpected public names: {leaked!r}. "
        "Slice 11m is a near-no-op; the module must not introduce any "
        "public symbol beyond the (empty) __all__ contract."
    )


# --- Back-import guard ----------------------------------------------------


def test_post_test_guard_module_does_not_import_implementation() -> None:
    """The compatibility-arrow direction (per doc 11 § "How To Use This
    Map" Q4) is: ``execution/post_test_guard.py`` MUST NOT import from
    ``workflows.develop.phases.implementation``. This test reads the
    on-disk source of ``post_test_guard.py`` and asserts the import
    line is absent. Belt-and-braces guard against a future refactor
    accidentally introducing a back-import (the same guard locked in
    by the Slice-11l ``post_dag_gates.py`` and Slice-11h ``repair.py``
    test files).
    """

    import iriai_build_v2.workflows.develop.execution.post_test_guard as guard_mod

    source_path = Path(guard_mod.__file__)
    text = source_path.read_text(encoding="utf-8")
    assert (
        "from iriai_build_v2.workflows.develop.phases.implementation"
        not in text
    ), (
        "execution/post_test_guard.py imports from phases/implementation "
        "-- violates the doc-11 compatibility-arrow direction"
    )
    assert "from ..phases.implementation" not in text, (
        "execution/post_test_guard.py uses a relative back-import to "
        "phases/implementation -- violates the doc-11 compatibility-arrow "
        "direction"
    )
    assert "from .implementation" not in text, (
        "execution/post_test_guard.py uses a same-package back-import to "
        "implementation -- violates the doc-11 compatibility-arrow "
        "direction"
    )


def test_post_test_guard_module_does_not_import_post_test_observation() -> None:
    """Per the Slice 11m prompt edge-case clause,
    ``post_test_observation.py`` is a SIBLING PHASE (not a target
    for extraction) and the post-test-guard PRIMITIVE surface is
    NOT the observation phase. The canonical-home module
    ``execution/post_test_guard.py`` MUST NOT import from
    ``workflows.develop.phases.post_test_observation`` either -- the
    compatibility-arrow direction is from phases INTO the execution
    package, not the other way round.
    """

    import iriai_build_v2.workflows.develop.execution.post_test_guard as guard_mod

    source_path = Path(guard_mod.__file__)
    text = source_path.read_text(encoding="utf-8")
    assert (
        "from iriai_build_v2.workflows.develop.phases.post_test_observation"
        not in text
    ), (
        "execution/post_test_guard.py imports from "
        "phases/post_test_observation -- violates the doc-11 "
        "compatibility-arrow direction (execution modules must not "
        "import from sibling phases)"
    )
    assert "from ..phases.post_test_observation" not in text, (
        "execution/post_test_guard.py uses a relative back-import to "
        "phases/post_test_observation -- violates the doc-11 "
        "compatibility-arrow direction"
    )


# --- Cluster-ownership pin against sibling execution modules --------------


def test_post_test_guard_is_distinct_sibling_in_execution_package() -> None:
    """The new module is its OWN sibling in the
    ``workflows/develop/execution/`` package, alongside the 12
    existing canonical-home modules (``types.py`` from Slice 11a,
    ``git_service.py`` from Slice 08b / 11b, ``task_contracts.py``
    from Slice 03 / 11c, ``sandbox.py`` from Slice 04 / 11d,
    ``dispatcher.py`` from Slice 05 / 11e, ``gates.py`` from Slice
    06 / 11f, ``verification.py`` from Slice 11g, ``repair.py`` from
    Slice 08 / 11h, ``failure_router.py`` from Slice 07 / 11i,
    ``merge_queue.py`` from Slice 08 / 11j, ``regroup_overlay.py``
    from Slice 09a / 11k, ``post_dag_gates.py`` from Slice 11l).
    Pins the boundary-contract sibling layout.
    """

    from iriai_build_v2.workflows.develop.execution import (
        dispatcher as dispatcher_mod,
        failure_router as failure_router_mod,
        gates as gates_mod,
        git_service as git_service_mod,
        merge_queue as merge_queue_mod,
        post_dag_gates as post_dag_gates_mod,
        post_test_guard as post_test_guard_mod,
        regroup_overlay as regroup_overlay_mod,
        repair as repair_mod,
        sandbox as sandbox_mod,
        task_contracts as task_contracts_mod,
        types as types_mod,
        verification as verification_mod,
    )

    # Every sibling module has a distinct __name__.
    siblings = [
        dispatcher_mod,
        failure_router_mod,
        gates_mod,
        git_service_mod,
        merge_queue_mod,
        post_dag_gates_mod,
        post_test_guard_mod,
        regroup_overlay_mod,
        repair_mod,
        sandbox_mod,
        task_contracts_mod,
        types_mod,
        verification_mod,
    ]
    names = [sibling.__name__ for sibling in siblings]
    assert len(set(names)) == len(names), (
        f"sibling-name collision: {names!r}"
    )
    assert post_test_guard_mod.__name__ == CANONICAL_MODULE_PATH


def test_post_test_readiness_name_not_owned_by_any_sibling_execution_module() -> None:
    """The boundary contract name ``PostTestReadinessGuard`` (per doc
    11 § "Boundary-level API contracts" row for
    ``execution/post_test_guard.py``) is RESERVED for the
    ``post_test_guard.py`` canonical home. No sibling execution
    module currently defines or imports it (which would be a
    cluster-ownership violation). This pin protects the reservation
    against a future refactor accidentally landing the boundary
    surface in the wrong canonical module.
    """

    from iriai_build_v2.workflows.develop.execution import (
        dispatcher as dispatcher_mod,
        failure_router as failure_router_mod,
        gates as gates_mod,
        git_service as git_service_mod,
        merge_queue as merge_queue_mod,
        post_dag_gates as post_dag_gates_mod,
        post_test_guard as post_test_guard_mod,
        regroup_overlay as regroup_overlay_mod,
        repair as repair_mod,
        sandbox as sandbox_mod,
        task_contracts as task_contracts_mod,
        types as types_mod,
        verification as verification_mod,
    )

    reserved_names = (
        "PostTestReadinessGuard",
        "assert_post_test_ready",
    )
    # The canonical home does NOT yet define them either (Slice 11m
    # is a near-no-op reservation; the implementation lands in a
    # future slice). This block asserts NEITHER the canonical home
    # NOR any sibling pre-emptively claims the boundary name.
    for name in reserved_names:
        assert not hasattr(post_test_guard_mod, name), (
            f"post_test_guard.{name} unexpectedly exists; Slice 11m "
            "is a near-no-op reservation, the boundary surface is "
            "not yet implemented."
        )
        for sibling, sibling_name in (
            (dispatcher_mod, "dispatcher"),
            (failure_router_mod, "failure_router"),
            (gates_mod, "gates"),
            (git_service_mod, "git_service"),
            (merge_queue_mod, "merge_queue"),
            (post_dag_gates_mod, "post_dag_gates"),
            (regroup_overlay_mod, "regroup_overlay"),
            (repair_mod, "repair"),
            (sandbox_mod, "sandbox"),
            (task_contracts_mod, "task_contracts"),
            (types_mod, "types"),
            (verification_mod, "verification"),
        ):
            assert not hasattr(sibling, name), (
                f"cluster ownership violation: {sibling_name}.{name} "
                f"unexpectedly exists; the {name!r} boundary surface "
                "is RESERVED for execution/post_test_guard.py."
            )


# --- No-shim-block contract -----------------------------------------------


def test_implementation_py_has_no_post_test_guard_shim_block_yet() -> None:
    """Slice 11m is a near-no-op (zero pure post-test-guard
    primitives found in ``implementation.py``); no shim block is
    added to ``implementation.py`` for this sub-slice because there
    are no names to re-export. An empty shim block would be a
    fabricated-coverage smell per the prompt hard rule against
    falsifiable artifacts.

    This test reads the on-disk source of ``implementation.py`` and
    asserts the absence of any ``from ..execution.post_test_guard
    import`` line, the absence of any ``Slice-11m post_test_guard
    shim`` banner comment, and the absence of any
    ``post_test_guard`` re-export.

    Future slices that land the
    ``PostTestReadinessGuard.assert_ready()`` implementation will
    introduce the shim block AND update this test to assert the
    shim block exists. The current contract is "near-no-op, no
    shim".
    """

    import iriai_build_v2.workflows.develop.phases.implementation as impl_mod

    source_path = Path(impl_mod.__file__)
    text = source_path.read_text(encoding="utf-8")
    # No actual re-export from the new canonical module.
    assert "from ..execution.post_test_guard import" not in text, (
        "implementation.py contains a Slice-11m shim block re-exporting "
        "from ..execution.post_test_guard, but Slice 11m is a near-no-op "
        "(zero pure primitives found). The shim block must not exist "
        "until a future slice lands actual mover names."
    )
    assert "from .execution.post_test_guard" not in text
    assert "from iriai_build_v2.workflows.develop.execution.post_test_guard" not in text


def test_post_test_guard_not_currently_exposed_via_implementation_module() -> None:
    """Belt-and-braces guard: until a future slice lands a real
    shim block, the canonical-home module
    ``post_test_guard`` is NOT re-exported as a sub-attribute of
    ``implementation.py``. Pinning this contract now prevents a
    silent regression where the shim block sneaks in without
    updating the empty-__all__ test above.
    """

    import iriai_build_v2.workflows.develop.phases.implementation as impl_mod

    # No exposed `post_test_guard` attribute on the impl module
    # (the would-be shim block does not exist).
    assert not hasattr(impl_mod, "post_test_guard"), (
        "implementation.post_test_guard unexpectedly exists; the "
        "Slice-11m near-no-op contract forbids a shim block until a "
        "future slice lands actual mover names."
    )
    # The reserved boundary names are also not yet on impl.
    for name in ("PostTestReadinessGuard", "assert_post_test_ready"):
        assert not hasattr(impl_mod, name), (
            f"implementation.{name} unexpectedly exists; Slice 11m "
            "reserves the boundary name for the canonical-home module "
            "but does not yet land it on implementation.py."
        )
