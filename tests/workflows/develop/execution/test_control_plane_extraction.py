"""Slice 12a-1 -- extraction proof for `execution/control_plane.py` CREATE.

Verifies the doc-11 § "How To Use This Map" four-question contract for the
PURE quiesce-propagation cluster extraction that establishes the
``execution/control_plane.py`` canonical home (the FOURTH CREATE pattern
in the Slice 11/12 refactor series, after Slice 11a ``execution/types.py``,
Slice 11l ``execution/post_dag_gates.py``, and Slice 11m
``execution/post_test_guard.py``).

1. What behavior moved: six pure quiesce-propagation primitives --
   ``DAG_QUIESCE_AFTER_GROUP_ENV`` (the env-var name string constant for
   the dispatch-quiesce override),
   ``DEFAULT_DAG_QUIESCE_AFTER_GROUP`` (the default group index after
   which DAG dispatch quiesces; the Slice 09 G45-G73 regroup window
   starts at group 45 so the default is 44 -- one group BEFORE the
   regroup window opens),
   ``_dag_quiesce_after_group() -> int | None`` (the env-driven
   getter that reads ``IRIAI_DAG_QUIESCE_AFTER_GROUP``; returns
   ``None`` for the off-string set; returns the parsed int otherwise;
   logs a warning and falls back to the default on parse error),
   ``_quiesce_marker_matches(payload, expected_identity) -> bool``
   (the pure dict-comparison primitive used by
   ``_maybe_quiesce_before_group_dispatch`` to test whether an
   existing quiesce marker matches the expected DAG /
   prior-checkpoint / task-id identity; every expected key must
   match exactly),
   ``_workflow_blocker_text(message) -> str`` (the CENTRAL quiesce-
   propagation primitive used 50+ times throughout
   ``implementation.py`` to prefix the ``SANDBOX_WORKFLOW_BLOCKER``
   marker onto a workflow-blocker message; idempotent on
   already-prefixed messages),
   ``_is_workflow_blocker_text(message) -> bool`` (the marker
   predicate that tests whether a message contains the
   ``SANDBOX_WORKFLOW_BLOCKER`` marker) -- moved byte-for-byte from
   ``workflows/develop/phases/implementation.py`` to the NEW
   canonical module ``workflows/develop/execution/control_plane.py``
   (CREATED by Slice 12a-1; mirrors the Slice-11a / Slice-11l /
   Slice-11m CREATE patterns -- no pre-existing surface to preserve).

2. Which legacy import names still work: every existing
   ``from iriai_build_v2.workflows.develop.phases.implementation import X``
   for one of the six moved names keeps resolving to the SAME object
   as the canonical definition in ``execution/control_plane.py`` (the
   shim is ``is``-equivalent, not a copy). ``monkeypatch.setattr(
   implementation_module, X, ...)`` continues to mutate the SAME
   function object (or rebind the SAME constant attribute) that any
   direct ``from execution.control_plane import X`` reader sees. The
   moved constants are externally consumed by 3 test sites in
   ``tests/workflows/test_dag_expanded_verify.py`` (via
   ``implementation_module.DAG_QUIESCE_AFTER_GROUP_ENV`` for
   ``monkeypatch.delenv``); the moved helpers are not externally
   monkeypatched but are heavily used within ``implementation.py``
   itself (the marker primitives 50+ times).

3. Which targeted tests prove the new facade and the compatibility
   shim: THIS file is the proof; it pins every moved name's shim
   equivalence, ``__module__`` rebinding (for the 4 callables -- the
   2 string/int constants are primitives and do not carry a
   ``__module__``), behavioral smoke against each of the six
   primitives (the env-var name constant value, the default after-
   group integer value, the env-driven getter with the unset / set /
   off / invalid paths, the dict matcher with the all-match /
   partial-match / mismatch paths, the marker prefixer with the
   plain / already-marked paths + idempotence, the marker predicate
   with both polarities), a cluster-ownership pin against the 13
   sibling execution modules, a shim-block completeness probe, a
   back-import guard against ``control_plane.py`` ever importing
   from ``implementation.py``, the ``__all__`` export probe, and a
   no-import-from-sibling-phases probe.

4. Why is the PR still refactor-only: nothing else moves. The six
   pure quiesce-propagation primitives moved byte-for-byte; no
   contract change, no behavior change. The orchestration-glue
   surface (``ImplementationPhase`` class + ``_implement_dag`` +
   ``_maybe_quiesce_before_group_dispatch`` +
   ``_resolve_active_regroup_before_group_dispatch`` + the post-DAG
   gate inner sequence inside ``ImplementationPhase.execute``) is
   PHASE-COUPLED (depends on ``runner``/``feature``/
   ``runner.artifacts``/``runner.services``/async impl.py-local
   helpers) and CORRECTLY stays in ``implementation.py`` in Slice
   12a-1 per the prompt hard rule against splitting non-pure
   helpers. The follow-on Slice 12a-2 + 12a-3 own the typed
   ``ExecutionControlPlane`` facade + the
   ``ImplementationPhase.execute`` shrink to phase adaptation +
   service assembly + quiesce propagation + post-DAG gate delegation
   + compatibility wrapper exports (per doc 11 § "PR 11.12").
"""

from __future__ import annotations

from pathlib import Path

import pytest


# Each entry is a name moved from ``implementation.py`` to
# ``execution/control_plane.py`` in Slice 12a-1. The order is the import-
# line order in the Slice-12a-1 shim block in ``implementation.py`` so a
# grep over either file lists the names in the same order.
MOVED_NAMES = [
    "DAG_QUIESCE_AFTER_GROUP_ENV",
    "DEFAULT_DAG_QUIESCE_AFTER_GROUP",
    "_dag_quiesce_after_group",
    "_is_workflow_blocker_text",
    "_quiesce_marker_matches",
    "_workflow_blocker_text",
]

# The 4 moved callables (excluding the 2 string/int constants which are
# primitives and do not carry a ``__module__`` attribute). Used for the
# ``__module__`` rebinding pin.
MOVED_CALLABLES = [
    "_dag_quiesce_after_group",
    "_is_workflow_blocker_text",
    "_quiesce_marker_matches",
    "_workflow_blocker_text",
]


# --- Identity + module-rebind ----------------------------------------------


@pytest.mark.parametrize("name", MOVED_NAMES)
def test_shim_re_export_is_same_object_as_new_canonical(name: str) -> None:
    """Every moved helper imported via the OLD path is the SAME object as
    the import via the NEW canonical path. Proves the shim is a re-export,
    not a copy. Locks the monkeypatch target equivalence --
    ``monkeypatch.setattr(implementation_module, name, ...)`` will mutate
    the SAME function object (or rebind the SAME constant attribute) that
    any direct ``from execution.control_plane import name`` reader sees.

    For the two string/int constant names, ``is``-identity holds because
    CPython interns short strings and small integers; the test asserts
    object identity anyway because the constants are defined in exactly
    ONE place (``execution/control_plane.py``) and re-imported by
    ``implementation.py`` via the Slice-12a-1 shim block.
    """

    from iriai_build_v2.workflows.develop import execution as execution_pkg
    from iriai_build_v2.workflows.develop.execution import (
        control_plane as control_plane_mod,
    )
    from iriai_build_v2.workflows.develop.phases import (
        implementation as impl_mod,
    )

    legacy = getattr(impl_mod, name)
    canonical = getattr(control_plane_mod, name)
    assert legacy is canonical, (
        f"shim drift: implementation.{name} is not the same object as "
        f"execution.control_plane.{name}"
    )
    # ``execution_pkg`` is imported only to ensure the package import chain
    # works end-to-end (no side-effect import errors).
    _ = execution_pkg


@pytest.mark.parametrize("name", MOVED_CALLABLES)
def test_canonical_module_is_control_plane(name: str) -> None:
    """The moved function objects' ``__module__`` is the new canonical
    ``iriai_build_v2.workflows.develop.execution.control_plane`` -- not
    the legacy ``...phases.implementation``. Proves the definition
    genuinely moved rather than being re-aliased from the old module.
    """

    from iriai_build_v2.workflows.develop.execution import (
        control_plane as control_plane_mod,
    )

    canonical = getattr(control_plane_mod, name)
    assert canonical.__module__ == (
        "iriai_build_v2.workflows.develop.execution.control_plane"
    ), (
        f"{name}.__module__ = {canonical.__module__!r}; expected the new "
        "control_plane-module path"
    )


# --- Behavioral smoke: constants -------------------------------------------


def test_dag_quiesce_after_group_env_is_iriai_namespaced_string() -> None:
    """``DAG_QUIESCE_AFTER_GROUP_ENV`` is the env-var name string used by
    ``_dag_quiesce_after_group`` to look up the override. The value must
    stay byte-for-byte identical to the legacy ``implementation.py``
    constant so external tooling (env-file generators, deploy artifacts)
    keeps reading the same name. The 3 sites in
    ``tests/workflows/test_dag_expanded_verify.py`` consume this constant
    via ``monkeypatch.delenv(implementation_module.DAG_QUIESCE_AFTER_GROUP_ENV)``.
    """

    from iriai_build_v2.workflows.develop.execution.control_plane import (
        DAG_QUIESCE_AFTER_GROUP_ENV,
    )

    assert DAG_QUIESCE_AFTER_GROUP_ENV == "IRIAI_DAG_QUIESCE_AFTER_GROUP"
    assert isinstance(DAG_QUIESCE_AFTER_GROUP_ENV, str)


def test_default_dag_quiesce_after_group_is_44() -> None:
    """``DEFAULT_DAG_QUIESCE_AFTER_GROUP`` is the default group index
    after which DAG dispatch quiesces. The Slice 09 G45-G73 regroup
    window starts at group 45, so the default is 44 -- ONE GROUP BEFORE
    the regroup window opens. Any drift in this default (without a
    matching update to the regroup window constants
    ``DAG_REGROUP_FROM_GROUP`` / ``DAG_REGROUP_TO_GROUP``) would
    silently change where dispatch quiesces.
    """

    from iriai_build_v2.workflows.develop.execution.control_plane import (
        DEFAULT_DAG_QUIESCE_AFTER_GROUP,
    )

    assert DEFAULT_DAG_QUIESCE_AFTER_GROUP == 44
    assert isinstance(DEFAULT_DAG_QUIESCE_AFTER_GROUP, int)


# --- Behavioral smoke: _dag_quiesce_after_group ----------------------------


def test_dag_quiesce_after_group_returns_default_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the env var is unset, ``_dag_quiesce_after_group()`` returns
    ``DEFAULT_DAG_QUIESCE_AFTER_GROUP``. Pin the default-case branch.
    """

    from iriai_build_v2.workflows.develop.execution.control_plane import (
        DAG_QUIESCE_AFTER_GROUP_ENV,
        DEFAULT_DAG_QUIESCE_AFTER_GROUP,
        _dag_quiesce_after_group,
    )

    monkeypatch.delenv(DAG_QUIESCE_AFTER_GROUP_ENV, raising=False)
    assert _dag_quiesce_after_group() == DEFAULT_DAG_QUIESCE_AFTER_GROUP


def test_dag_quiesce_after_group_returns_default_when_env_blank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the env var is set to a whitespace-only string,
    ``_dag_quiesce_after_group()`` falls back to the default (the
    ``raw.strip()`` empty-string branch).
    """

    from iriai_build_v2.workflows.develop.execution.control_plane import (
        DAG_QUIESCE_AFTER_GROUP_ENV,
        DEFAULT_DAG_QUIESCE_AFTER_GROUP,
        _dag_quiesce_after_group,
    )

    monkeypatch.setenv(DAG_QUIESCE_AFTER_GROUP_ENV, "   ")
    assert _dag_quiesce_after_group() == DEFAULT_DAG_QUIESCE_AFTER_GROUP


def test_dag_quiesce_after_group_returns_int_for_numeric_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A numeric env value parses to int. Pin the happy-path branch.
    """

    from iriai_build_v2.workflows.develop.execution.control_plane import (
        DAG_QUIESCE_AFTER_GROUP_ENV,
        _dag_quiesce_after_group,
    )

    monkeypatch.setenv(DAG_QUIESCE_AFTER_GROUP_ENV, "7")
    assert _dag_quiesce_after_group() == 7

    monkeypatch.setenv(DAG_QUIESCE_AFTER_GROUP_ENV, "0")
    # "0" is in the off-string set -- returns None, NOT 0. Locked here.
    assert _dag_quiesce_after_group() is None


@pytest.mark.parametrize(
    "off_value", ["0", "false", "no", "off", "disabled", "FALSE", "Off", "DiSaBlEd"],
)
def test_dag_quiesce_after_group_returns_none_for_off_strings(
    monkeypatch: pytest.MonkeyPatch, off_value: str,
) -> None:
    """Every variant of the off-string set returns ``None`` -- the
    "quiesce disabled" sentinel. The case-insensitive match is part of
    the public contract.
    """

    from iriai_build_v2.workflows.develop.execution.control_plane import (
        DAG_QUIESCE_AFTER_GROUP_ENV,
        _dag_quiesce_after_group,
    )

    monkeypatch.setenv(DAG_QUIESCE_AFTER_GROUP_ENV, off_value)
    assert _dag_quiesce_after_group() is None


def test_dag_quiesce_after_group_falls_back_to_default_on_invalid(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An unparseable env value (e.g. ``"abc"``) falls back to the
    default + emits a warning via the module logger. Pin the
    error-path branch.
    """

    import logging

    from iriai_build_v2.workflows.develop.execution.control_plane import (
        DAG_QUIESCE_AFTER_GROUP_ENV,
        DEFAULT_DAG_QUIESCE_AFTER_GROUP,
        _dag_quiesce_after_group,
    )

    monkeypatch.setenv(DAG_QUIESCE_AFTER_GROUP_ENV, "not-a-number")
    with caplog.at_level(
        logging.WARNING,
        logger="iriai_build_v2.workflows.develop.execution.control_plane",
    ):
        result = _dag_quiesce_after_group()
    assert result == DEFAULT_DAG_QUIESCE_AFTER_GROUP
    # The warning records both the env var name and the invalid raw value.
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "expected a WARNING log on parse-error fallback"
    assert any("Invalid" in r.getMessage() for r in warnings)


# --- Behavioral smoke: _quiesce_marker_matches -----------------------------


def test_quiesce_marker_matches_returns_true_for_full_subset() -> None:
    """Every key in ``expected_identity`` finds an equal value in
    ``payload`` -> returns ``True``. Extra keys in ``payload`` are
    ignored. Pin the happy-path branch + the "extra payload fields
    are irrelevant" contract.
    """

    from iriai_build_v2.workflows.develop.execution.control_plane import (
        _quiesce_marker_matches,
    )

    payload = {"dag_sha256": "abc", "prior_checkpoint_sha256": "def", "extra": "ok"}
    expected = {"dag_sha256": "abc", "prior_checkpoint_sha256": "def"}
    assert _quiesce_marker_matches(payload, expected) is True


def test_quiesce_marker_matches_returns_false_on_value_mismatch() -> None:
    """Any expected key whose value differs from the payload returns
    ``False`` immediately. Pin the early-return-on-mismatch contract.
    """

    from iriai_build_v2.workflows.develop.execution.control_plane import (
        _quiesce_marker_matches,
    )

    payload = {"dag_sha256": "abc", "prior_checkpoint_sha256": "def"}
    # Mismatched value for ``dag_sha256``.
    expected = {"dag_sha256": "OTHER", "prior_checkpoint_sha256": "def"}
    assert _quiesce_marker_matches(payload, expected) is False


def test_quiesce_marker_matches_returns_false_on_missing_key() -> None:
    """An expected key absent from the payload returns ``False`` (because
    ``payload.get(key)`` returns ``None`` which mismatches the expected
    value). Pin the missing-key branch.
    """

    from iriai_build_v2.workflows.develop.execution.control_plane import (
        _quiesce_marker_matches,
    )

    payload: dict = {"dag_sha256": "abc"}
    expected = {"dag_sha256": "abc", "prior_checkpoint_sha256": "def"}
    assert _quiesce_marker_matches(payload, expected) is False


def test_quiesce_marker_matches_empty_expected_returns_true() -> None:
    """An empty ``expected_identity`` (no keys to check) trivially
    returns ``True``. Edge-case probe.
    """

    from iriai_build_v2.workflows.develop.execution.control_plane import (
        _quiesce_marker_matches,
    )

    # Any payload (including empty) matches an empty expected.
    assert _quiesce_marker_matches({}, {}) is True
    assert _quiesce_marker_matches({"a": 1}, {}) is True


def test_quiesce_marker_matches_compares_list_values_by_equality() -> None:
    """List-valued expected identities are compared by equality (the
    ``payload.get(key) != expected`` branch). Pin that lists with the
    same elements in the same order match; reordered lists do not.
    """

    from iriai_build_v2.workflows.develop.execution.control_plane import (
        _quiesce_marker_matches,
    )

    payload = {"task_ids": ["t1", "t2", "t3"]}
    # Same order matches.
    assert _quiesce_marker_matches(payload, {"task_ids": ["t1", "t2", "t3"]}) is True
    # Reordered lists do NOT match (list equality is positional).
    assert _quiesce_marker_matches(payload, {"task_ids": ["t3", "t2", "t1"]}) is False


# --- Behavioral smoke: _workflow_blocker_text ------------------------------


def test_workflow_blocker_text_prefixes_marker_on_plain_message() -> None:
    """A plain message gets the ``SANDBOX_WORKFLOW_BLOCKER:`` prefix.
    Pin the happy-path branch.
    """

    from iriai_build_v2.workflows.develop.execution.control_plane import (
        _workflow_blocker_text,
    )
    from iriai_build_v2.workflows.develop.execution.types import (
        _SANDBOX_WORKFLOW_BLOCKER_MARKER,
    )

    result = _workflow_blocker_text("something broke")
    assert result.startswith(f"{_SANDBOX_WORKFLOW_BLOCKER_MARKER}:")
    assert "something broke" in result


def test_workflow_blocker_text_is_idempotent_on_already_marked_message() -> None:
    """Calling the helper on an already-marked message returns the
    message unchanged (no double-prefix). This idempotence is what lets
    50+ call sites compose the marker without worrying about whether the
    caller already added it.
    """

    from iriai_build_v2.workflows.develop.execution.control_plane import (
        _workflow_blocker_text,
    )
    from iriai_build_v2.workflows.develop.execution.types import (
        _SANDBOX_WORKFLOW_BLOCKER_MARKER,
    )

    already_marked = f"{_SANDBOX_WORKFLOW_BLOCKER_MARKER}: previous"
    result = _workflow_blocker_text(already_marked)
    assert result == already_marked
    # No double-marker.
    assert result.count(_SANDBOX_WORKFLOW_BLOCKER_MARKER) == 1


def test_workflow_blocker_text_coerces_non_string_input_to_str() -> None:
    """The check is ``_SANDBOX_WORKFLOW_BLOCKER_MARKER in str(message)``,
    so non-string inputs are tolerated via ``str(...)``. Edge probe
    against future regressions that drop the ``str(...)`` coercion.
    """

    from iriai_build_v2.workflows.develop.execution.control_plane import (
        _workflow_blocker_text,
    )
    from iriai_build_v2.workflows.develop.execution.types import (
        _SANDBOX_WORKFLOW_BLOCKER_MARKER,
    )

    # Non-string message coerces via str() in the marker check; the
    # helper returns the ORIGINAL message verbatim when the marker is
    # already in str(message). For a non-string input without the
    # marker, the helper still returns the f-string-formatted prefix.
    result = _workflow_blocker_text(123)  # type: ignore[arg-type]
    assert _SANDBOX_WORKFLOW_BLOCKER_MARKER in result
    assert "123" in result


# --- Behavioral smoke: _is_workflow_blocker_text ---------------------------


def test_is_workflow_blocker_text_true_on_marker_present() -> None:
    """The marker predicate returns ``True`` when the marker string is in
    the message. Pin the happy-path branch.
    """

    from iriai_build_v2.workflows.develop.execution.control_plane import (
        _is_workflow_blocker_text,
    )
    from iriai_build_v2.workflows.develop.execution.types import (
        _SANDBOX_WORKFLOW_BLOCKER_MARKER,
    )

    assert _is_workflow_blocker_text(
        f"{_SANDBOX_WORKFLOW_BLOCKER_MARKER}: blocker reason"
    ) is True
    # Mid-string marker is also detected.
    assert _is_workflow_blocker_text(
        f"prefix {_SANDBOX_WORKFLOW_BLOCKER_MARKER} suffix"
    ) is True


def test_is_workflow_blocker_text_false_on_marker_absent() -> None:
    """The predicate returns ``False`` for plain messages. Pin the
    no-marker branch.
    """

    from iriai_build_v2.workflows.develop.execution.control_plane import (
        _is_workflow_blocker_text,
    )

    assert _is_workflow_blocker_text("plain message") is False
    assert _is_workflow_blocker_text("") is False


def test_is_workflow_blocker_text_handles_non_string_via_str() -> None:
    """The predicate uses ``str(message)`` so non-string inputs are
    tolerated. Edge probe.
    """

    from iriai_build_v2.workflows.develop.execution.control_plane import (
        _is_workflow_blocker_text,
    )

    assert _is_workflow_blocker_text(123) is False  # type: ignore[arg-type]
    assert _is_workflow_blocker_text(None) is False  # type: ignore[arg-type]


# --- Round-trip + integration ---------------------------------------------


def test_workflow_blocker_text_and_predicate_round_trip() -> None:
    """Composing the prefixer + predicate is the canonical workflow-
    blocker propagation idiom used 50+ times across
    ``implementation.py``: ``_workflow_blocker_text`` produces a
    message that ``_is_workflow_blocker_text`` returns ``True`` for.
    Pin the producer-consumer contract.
    """

    from iriai_build_v2.workflows.develop.execution.control_plane import (
        _is_workflow_blocker_text,
        _workflow_blocker_text,
    )

    for message in ["a", "ä", "with: colons", ""]:
        marked = _workflow_blocker_text(message)
        assert _is_workflow_blocker_text(marked) is True


# --- Cluster-ownership pin against sibling execution modules --------------


def test_cluster_ownership_pin_control_plane_module() -> None:
    """All six moved names land in the canonical
    ``execution/control_plane.py`` module (not in any other
    ``execution/`` sibling). Belt-and-braces guard against a future
    refactor accidentally relocating one of the helpers to the wrong
    canonical module while leaving the shim intact.
    """

    from iriai_build_v2.workflows.develop.execution import (
        control_plane as control_plane_mod,
    )

    expected = "iriai_build_v2.workflows.develop.execution.control_plane"
    for name in MOVED_CALLABLES:
        obj = getattr(control_plane_mod, name)
        assert obj.__module__ == expected, (
            f"{name}.__module__ = {obj.__module__!r}; expected {expected!r}"
        )

    # Cross-check that the names are NOT served by any of the 13 sibling
    # execution modules (the 24 pre-12a-1 modules minus the new
    # control_plane module).
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
    for name in MOVED_NAMES:
        for sibling, sibling_name in (
            (dispatcher_mod, "dispatcher"),
            (failure_router_mod, "failure_router"),
            (gates_mod, "gates"),
            (git_service_mod, "git_service"),
            (merge_queue_mod, "merge_queue"),
            (post_dag_gates_mod, "post_dag_gates"),
            (post_test_guard_mod, "post_test_guard"),
            (regroup_overlay_mod, "regroup_overlay"),
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


# --- Shim-block completeness ----------------------------------------------


def test_shim_block_exports_all_six_names() -> None:
    """The Slice-12a-1 shim block in ``implementation.py`` re-exports
    exactly the six moved names from ``..execution.control_plane``.
    This test asserts the shim block actually carries all six (a
    deliberate "did the shim block lose a name?" probe).
    """

    from iriai_build_v2.workflows.develop.execution.control_plane import (
        DAG_QUIESCE_AFTER_GROUP_ENV,
        DEFAULT_DAG_QUIESCE_AFTER_GROUP,
        _dag_quiesce_after_group,
        _is_workflow_blocker_text,
        _quiesce_marker_matches,
        _workflow_blocker_text,
    )
    from iriai_build_v2.workflows.develop.phases import (
        implementation as impl_mod,
    )

    # All six moved names accessible via the impl module.
    for name in MOVED_NAMES:
        assert hasattr(impl_mod, name), (
            f"implementation.{name} missing -- the Slice-12a-1 shim block "
            "dropped a re-export"
        )

    # All six shim entries point to the SAME canonical objects.
    assert impl_mod.DAG_QUIESCE_AFTER_GROUP_ENV is DAG_QUIESCE_AFTER_GROUP_ENV
    assert (
        impl_mod.DEFAULT_DAG_QUIESCE_AFTER_GROUP
        is DEFAULT_DAG_QUIESCE_AFTER_GROUP
    )
    assert impl_mod._dag_quiesce_after_group is _dag_quiesce_after_group
    assert impl_mod._is_workflow_blocker_text is _is_workflow_blocker_text
    assert impl_mod._quiesce_marker_matches is _quiesce_marker_matches
    assert impl_mod._workflow_blocker_text is _workflow_blocker_text


# --- Module-existence + canonical-home contract --------------------------


def test_control_plane_module_imports_cleanly() -> None:
    """The Slice-12a-1 canonical-home module
    ``execution/control_plane.py`` imports without error. Pins the
    module-creation contract (the fourth CREATE pattern in the Slice
    11/12 refactor series).
    """

    import iriai_build_v2.workflows.develop.execution.control_plane as cp_mod

    assert cp_mod.__name__ == (
        "iriai_build_v2.workflows.develop.execution.control_plane"
    )


def test_control_plane_module_lives_in_execution_package() -> None:
    """The canonical-home module file lives under the
    ``workflows/develop/execution/`` package. Pins the
    boundary-contract directory layout per doc 11 § "Proposed Module
    Boundaries".
    """

    import iriai_build_v2.workflows.develop.execution.control_plane as cp_mod

    source_path = Path(cp_mod.__file__)
    assert source_path.name == "control_plane.py"
    assert source_path.parent.name == "execution"
    assert source_path.parent.parent.name == "develop"
    assert source_path.parent.parent.parent.name == "workflows"


# --- __all__ export probe -------------------------------------------------


def test_all_export_includes_all_six_moved_names() -> None:
    """``control_plane.py.__all__`` includes all six moved names.
    Belt-and-braces probe against a refactor that forgets to add the
    new public symbols to the module's public surface (which would
    cause ``from execution.control_plane import *`` to silently lose
    them).
    """

    from iriai_build_v2.workflows.develop.execution import (
        control_plane as cp_mod,
    )

    for name in MOVED_NAMES:
        assert name in cp_mod.__all__, (
            f"{name} missing from execution/control_plane.py __all__"
        )


# --- Back-import guard ----------------------------------------------------


def test_control_plane_module_does_not_import_implementation() -> None:
    """The compatibility-arrow direction (per doc 11 § "How To Use This
    Map" Q4) is: ``execution/control_plane.py`` MUST NOT import from
    ``workflows.develop.phases.implementation``. This test reads the
    on-disk source of ``control_plane.py`` and asserts the import
    line is absent. Belt-and-braces guard against a future refactor
    accidentally introducing a back-import (the same guard locked in
    by the Slice-11l ``post_dag_gates.py``, Slice-11m
    ``post_test_guard.py``, and Slice-11h ``repair.py`` test files).
    """

    import iriai_build_v2.workflows.develop.execution.control_plane as cp_mod

    source_path = Path(cp_mod.__file__)
    text = source_path.read_text(encoding="utf-8")
    assert (
        "from iriai_build_v2.workflows.develop.phases.implementation"
        not in text
    ), (
        "execution/control_plane.py imports from phases/implementation -- "
        "violates the doc-11 compatibility-arrow direction"
    )
    assert "from ..phases.implementation" not in text, (
        "execution/control_plane.py uses a relative back-import to "
        "phases/implementation -- violates the doc-11 compatibility-arrow "
        "direction"
    )
    assert "from .implementation" not in text, (
        "execution/control_plane.py uses a same-package back-import to "
        "implementation -- violates the doc-11 compatibility-arrow "
        "direction"
    )


def test_control_plane_module_does_not_import_sibling_phases() -> None:
    """Per doc 11 § "Cross-module dependency direction" line 153-155:
    "Lower modules may depend on ``execution/types.py`` and narrowly on
    ``journal.py``... they must not call back into ``control_plane.py``
    or ``implementation.py``." The dual constraint applies HERE too:
    ``control_plane.py`` is itself a lower module relative to phases
    AND must not import from sibling phases (``post_test_observation``,
    ``planning``, ``post_test_implementation`` etc). The
    compatibility-arrow direction is from phases INTO the execution
    package, not the other way round.
    """

    import iriai_build_v2.workflows.develop.execution.control_plane as cp_mod

    source_path = Path(cp_mod.__file__)
    text = source_path.read_text(encoding="utf-8")
    forbidden_sibling_phase_imports = (
        "from iriai_build_v2.workflows.develop.phases.post_test_observation",
        "from iriai_build_v2.workflows.develop.phases.planning",
        "from ..phases.post_test_observation",
        "from ..phases.planning",
    )
    for forbidden in forbidden_sibling_phase_imports:
        assert forbidden not in text, (
            f"execution/control_plane.py uses {forbidden!r} -- violates the "
            "doc-11 compatibility-arrow direction (execution modules must "
            "not import from sibling phases)"
        )


# --- Slice-12a-1 inventory pin: PHASE-LEVEL surface STAYS in impl.py -----


def test_implementation_phase_class_still_lives_in_implementation_module() -> None:
    """The ``ImplementationPhase`` class is the phase boundary and STAYS
    in ``implementation.py`` in Slice 12a-1. Slice 12a-3 will shrink
    ``ImplementationPhase.execute`` (the deferred-final shrink mandated
    by doc 11 § "PR 11.12") but does NOT move the class itself.
    """

    from iriai_build_v2.workflows.develop.phases import (
        implementation as impl_mod,
    )

    assert hasattr(impl_mod, "ImplementationPhase")
    cls = impl_mod.ImplementationPhase
    assert cls.__module__ == (
        "iriai_build_v2.workflows.develop.phases.implementation"
    ), (
        f"ImplementationPhase.__module__ = {cls.__module__!r}; "
        "Slice 12a-1 must leave the phase class in implementation.py"
    )


def test_implement_dag_async_function_still_lives_in_implementation_module() -> None:
    """``_implement_dag`` is heavily phase-coupled (runner / feature /
    runner.artifacts / runner.run async calls + impl.py-local helpers
    like ``_commit_repos`` and ``_diagnose_and_fix``) and STAYS in
    ``implementation.py`` in Slice 12a-1. Slice 12a-2 owns the move
    into the typed ``ExecutionControlPlane`` facade.
    """

    from iriai_build_v2.workflows.develop.phases import (
        implementation as impl_mod,
    )

    assert hasattr(impl_mod, "_implement_dag")
    fn = impl_mod._implement_dag
    assert fn.__module__ == (
        "iriai_build_v2.workflows.develop.phases.implementation"
    ), (
        f"_implement_dag.__module__ = {fn.__module__!r}; "
        "Slice 12a-1 must leave _implement_dag in implementation.py"
    )


def test_maybe_quiesce_before_group_dispatch_still_lives_in_implementation_module() -> None:
    """``_maybe_quiesce_before_group_dispatch`` is runner-coupled
    (runner.artifacts + runner.services + ``_log_feature_event``) and
    STAYS in ``implementation.py`` in Slice 12a-1. Pin the
    PHASE-LEVEL boundary for the orchestration-glue surface.
    """

    from iriai_build_v2.workflows.develop.phases import (
        implementation as impl_mod,
    )

    assert hasattr(impl_mod, "_maybe_quiesce_before_group_dispatch")
    fn = impl_mod._maybe_quiesce_before_group_dispatch
    assert fn.__module__ == (
        "iriai_build_v2.workflows.develop.phases.implementation"
    ), (
        f"_maybe_quiesce_before_group_dispatch.__module__ = "
        f"{fn.__module__!r}; Slice 12a-1 must leave runner-coupled "
        "async orchestrators in implementation.py"
    )


# --- Legacy import-path probe: monkeypatch contract ------------------------


def test_monkeypatch_implementation_module_attribute_still_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A legacy ``monkeypatch.setattr(implementation_module, '_workflow_
    blocker_text', sentinel)`` continues to work after the Slice-12a-1
    extraction: the sentinel becomes the value reachable via
    ``implementation._workflow_blocker_text``. This is the existing
    Slice 11a-11l monkeypatch contract -- shim entries are
    ``monkeypatch``-targetable just like the original definitions.

    The contract is one-way: the shim is the LEGACY-side handle, while
    the canonical-home module retains the original function object.
    Rebinding ``impl_mod._workflow_blocker_text`` does NOT mutate
    ``execution/control_plane.py._workflow_blocker_text`` (mirrors the
    Slice 11a-11l monkeypatch contract).
    """

    from iriai_build_v2.workflows.develop.execution import (
        control_plane as cp_mod,
    )
    from iriai_build_v2.workflows.develop.phases import (
        implementation as impl_mod,
    )

    original_legacy = impl_mod._workflow_blocker_text
    original_canonical = cp_mod._workflow_blocker_text
    assert original_legacy is original_canonical

    sentinel = lambda message: f"SENTINEL: {message}"  # noqa: E731
    monkeypatch.setattr(impl_mod, "_workflow_blocker_text", sentinel)

    # The legacy attribute is now the sentinel.
    assert impl_mod._workflow_blocker_text is sentinel
    # The canonical home is UNCHANGED (the shim is one-way).
    assert cp_mod._workflow_blocker_text is original_canonical


# --- Implementation.py source-side pin: removed bodies + shim block -------


def test_implementation_py_no_longer_defines_moved_helpers_locally() -> None:
    """The Slice-12a-1 extraction removed the LOCAL definition of each
    moved helper from ``implementation.py``. The names remain
    importable via the shim block, but the on-disk source of
    ``implementation.py`` no longer contains the original ``def``
    statements. Belt-and-braces probe against a partial extraction
    where the body was moved but the original body was left behind
    (which would create a definition-order race between the def and
    the shim re-import).
    """

    from iriai_build_v2.workflows.develop.phases import (
        implementation as impl_mod,
    )

    source_path = Path(impl_mod.__file__)
    text = source_path.read_text(encoding="utf-8")
    # The original ``def _workflow_blocker_text(message: str)`` body must
    # not appear in implementation.py anymore.
    assert "def _workflow_blocker_text(message: str)" not in text, (
        "implementation.py still contains the local def of "
        "_workflow_blocker_text -- the Slice-12a-1 extraction did not "
        "fully remove the body"
    )
    assert "def _is_workflow_blocker_text(message: str)" not in text, (
        "implementation.py still contains the local def of "
        "_is_workflow_blocker_text -- the Slice-12a-1 extraction did "
        "not fully remove the body"
    )
    assert "def _dag_quiesce_after_group()" not in text, (
        "implementation.py still contains the local def of "
        "_dag_quiesce_after_group -- the Slice-12a-1 extraction did "
        "not fully remove the body"
    )
    assert "def _quiesce_marker_matches(" not in text, (
        "implementation.py still contains the local def of "
        "_quiesce_marker_matches -- the Slice-12a-1 extraction did "
        "not fully remove the body"
    )


def test_implementation_py_shim_block_imports_from_control_plane() -> None:
    """The Slice-12a-1 shim block in ``implementation.py`` imports the
    six moved names from ``..execution.control_plane``. Pin the import
    line is present.
    """

    from iriai_build_v2.workflows.develop.phases import (
        implementation as impl_mod,
    )

    source_path = Path(impl_mod.__file__)
    text = source_path.read_text(encoding="utf-8")
    assert "from ..execution.control_plane import (" in text, (
        "implementation.py is missing the Slice-12a-1 shim block "
        "(no ``from ..execution.control_plane import (`` line found)"
    )
    for name in MOVED_NAMES:
        assert f"    {name}," in text, (
            f"implementation.py Slice-12a-1 shim block missing "
            f"re-export of {name}"
        )
