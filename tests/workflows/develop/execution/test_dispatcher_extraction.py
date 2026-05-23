"""Slice 11e -- extraction proof for `execution/dispatcher.py` extension.

Verifies the doc-11 § "How To Use This Map" four-question contract for
the pure runtime-selection / parallel-actor primitive extraction:

1. What behavior moved: six pure dispatcher-domain primitives -- the
   static role->runtime map `DAG_REPAIR_ROLE_RUNTIMES`, the role->runtime
   lookup `_dag_repair_runtime_for`, the per-group runtime-pair selector
   `_dag_group_runtime_pair`, the per-stage post-DAG selector
   `_post_dag_runtime_pair`, the diagnostic-runtime selector
   `_diagnostic_runtime_for_policy`, and the parallel-safe actor factory
   `_make_parallel_actor` -- moved from
   `workflows/develop/phases/implementation.py` to
   `workflows/develop/execution/dispatcher.py`. The Slice-05
   `RuntimeDispatcher` + ports + typed `Dispatch*` request/response
   models already in `dispatcher.py` are UNTOUCHED -- Slice 11e
   EXTENDS, never modifies.
2. Which legacy import names still work: every existing
   `from iriai_build_v2.workflows.develop.phases.implementation import X`
   for one of the six moved names keeps resolving to the SAME object as
   the canonical definition in `execution/dispatcher.py` (the shim is
   `is`-equivalent, not a copy). `monkeypatch.setattr(implementation_
   module, X, ...)` continues to mutate the SAME binding any direct
   `from execution.dispatcher import X` reader sees. The pre-existing
   external importers (`tests/workflows/test_runtime_policy.py:7-11`
   for `_dag_group_runtime_pair` / `_post_dag_runtime_pair` /
   `_diagnostic_runtime_for_policy`; `tests/workflows/test_dag_
   expanded_verify.py:47-55` for `implementation_module._dag_repair_
   runtime_for(...)`; `tests/workflows/test_dag_expanded_verify.py:
   15913` for `implementation_module._make_parallel_actor(...)`)
   continue resolving via the shim.
3. Which targeted tests prove the new facade and the compatibility
   shim: THIS file is one of them; it pins every moved name's shim
   equivalence and behaviorally smoke-tests each moved helper.
4. Why is the PR still refactor-only: nothing else moves. The six pure
   dispatcher-domain primitives moved byte-for-byte. The phase-level
   dispatcher PORT surface (the
   `_dispatch_task_attempt_via_runtime_dispatcher` orchestrator + the
   `_ImplementationSandboxPort` / `_ImplementationRuntimeClient` /
   `_ImplementationPromptBuilder` / `_ImplementationOutputNormalizer` /
   `_ExecutionControlDispatchJournalPort` / `_ArtifactDispatchJournalPort`
   adapter classes + the `_dispatcher_request_for_task` /
   `_dispatcher_actor_metadata` builders + `_dispatch_journal_port_for_
   runner` factory + `_runner_runtime_policy` runner-policy reader +
   `_runtime_instance_name_for_hint` / `_sandbox_runtime_name`) is
   genuinely PHASE-LEVEL (each one takes `runner: WorkflowRunner` +
   `feature: Feature` or depends on `_execution_control_store_for_
   runner(runner)` + the implementation.py-namespaced module `logger`
   + the impl.py-local `_model_json_dict` / `_task_contract_id` /
   `_snapshot_id_for_repo` / `_git_text` / `_sha256_text` /
   `_contract_repo_id` / `_sandbox_blocker` helpers) and CORRECTLY
   stays in `implementation.py` per the prompt hard rule against
   splitting non-pure helpers. `_runner_runtime_policy` is deferred
   because it reads `runner.services` AND logs through the
   implementation.py-namespaced module logger (same criterion that
   kept `_write_sandbox_settings` phase-level in Slice 11d). The
   `_dispatch_attempt_duplicate_replay_recovery_evidence` helper +
   the `DISPATCH_ATTEMPT_RECOVERY_EVIDENCE_FIELDS` constant are
   deferred because the helper depends on the impl.py-local
   `_model_json_dict` utility (same criterion that kept the
   `_model_json_dict`-coupled task-contract cluster in Slice 11c).
"""

from __future__ import annotations

import pytest

from iriai_build_v2.runtime_policy import (
    DEFAULT_RUNTIME_POLICY,
    PRIMARY_IMPL_SECONDARY_REVIEW_POLICY,
)


# Each entry is a name moved from `implementation.py` to
# `execution/dispatcher.py` in Slice 11e. The order is the import-line
# order in the shim block in `implementation.py` (the Slice-11e block)
# so a grep over either file lists the names in the same order.
MOVED_NAMES = [
    "DAG_REPAIR_ROLE_RUNTIMES",
    "_dag_group_runtime_pair",
    "_dag_repair_runtime_for",
    "_diagnostic_runtime_for_policy",
    "_make_parallel_actor",
    "_post_dag_runtime_pair",
]

# `DAG_REPAIR_ROLE_RUNTIMES` is a module-level dict constant; it has no
# `__module__` attribute. The five callables do.
MOVED_CALLABLES = [
    "_dag_group_runtime_pair",
    "_dag_repair_runtime_for",
    "_diagnostic_runtime_for_policy",
    "_make_parallel_actor",
    "_post_dag_runtime_pair",
]


@pytest.mark.parametrize("name", MOVED_NAMES)
def test_shim_re_export_is_same_object_as_new_canonical(name: str) -> None:
    """Every moved helper imported via the OLD path is the SAME object
    as the import via the NEW canonical path. Proves the shim is a re-
    export, not a copy. Locks the monkeypatch target equivalence --
    `monkeypatch.setattr(implementation_module, name, ...)` will mutate
    the SAME function object that any direct
    `from execution.dispatcher import name` reader sees.
    """

    from iriai_build_v2.workflows.develop import execution as execution_pkg
    from iriai_build_v2.workflows.develop.execution import (
        dispatcher as dispatcher_mod,
    )
    from iriai_build_v2.workflows.develop.phases import implementation as impl_mod

    legacy = getattr(impl_mod, name)
    canonical = getattr(dispatcher_mod, name)
    assert legacy is canonical, (
        f"shim drift: implementation.{name} is not the same object as "
        f"execution.dispatcher.{name}"
    )
    # `execution_pkg` is imported only to ensure the package import chain
    # works end-to-end (no side-effect import errors).
    _ = execution_pkg


@pytest.mark.parametrize("name", MOVED_CALLABLES)
def test_canonical_module_is_dispatcher(name: str) -> None:
    """The moved function objects' `__module__` is the new canonical
    `iriai_build_v2.workflows.develop.execution.dispatcher` -- not the
    legacy `...phases.implementation`. Proves the definition genuinely
    moved rather than being re-aliased from the old module.
    """

    from iriai_build_v2.workflows.develop.execution import (
        dispatcher as dispatcher_mod,
    )

    canonical = getattr(dispatcher_mod, name)
    assert canonical.__module__ == (
        "iriai_build_v2.workflows.develop.execution.dispatcher"
    ), (
        f"{name}.__module__ = {canonical.__module__!r}; expected the new "
        "dispatcher-module path"
    )


def test_dag_repair_role_runtimes_is_canonical_map() -> None:
    """`DAG_REPAIR_ROLE_RUNTIMES` is the static role->runtime selection
    table consumed by `_dag_repair_runtime_for`. Under the bridge-claude-
    pool-codex-review configuration (primary=Claude pool, secondary=Codex),
    `dag-normal-verify` / `dag-final-verify` / `dag-contradiction-resolve`
    + the acceptance-coverage / contract-protocol lenses target
    `secondary`; the `dag-triage` / `dag-rca` / `dag-fix` / `dag-focused-
    reverify` family + the build-dependency / runtime-composition /
    security-boundary / regression-downstream lenses target `primary`.
    Locks the doc-11 Slice 11e canonical key set so a future role rebal-
    ance is caught by the test.
    """

    from iriai_build_v2.workflows.develop.execution.dispatcher import (
        DAG_REPAIR_ROLE_RUNTIMES,
    )

    # The expected canonical 13-entry mapping (matches the byte-for-byte
    # extraction from implementation.py).
    expected = {
        "dag-normal-verify": "secondary",
        "dag-final-verify": "secondary",
        "dag-triage": "primary",
        "dag-rca": "primary",
        "dag-fix": "primary",
        "dag-focused-reverify": "primary",
        "dag-contradiction-resolve": "secondary",
        "lens:acceptance-coverage": "secondary",
        "lens:contract-protocol": "secondary",
        "lens:build-dependency": "primary",
        "lens:runtime-composition": "primary",
        "lens:security-boundary": "primary",
        "lens:regression-downstream": "primary",
    }
    assert DAG_REPAIR_ROLE_RUNTIMES == expected
    assert len(DAG_REPAIR_ROLE_RUNTIMES) == 13


def test_dag_repair_runtime_for_resolves_table_with_fallback() -> None:
    """`_dag_repair_runtime_for(role_or_lens, fallback=None)` returns the
    table value if the key exists; otherwise returns the `fallback`.
    Pure dict-get wrapper. Pinned by `tests/workflows/test_dag_expanded_
    verify.py:47-55` which calls `implementation_module._dag_repair_
    runtime_for(...)` on each canonical key + one fallback case.
    """

    from iriai_build_v2.workflows.develop.execution.dispatcher import (
        _dag_repair_runtime_for,
    )

    # Each canonical key resolves to its table value.
    assert _dag_repair_runtime_for("dag-normal-verify") == "secondary"
    assert _dag_repair_runtime_for("dag-fix") == "primary"
    assert _dag_repair_runtime_for("lens:acceptance-coverage") == "secondary"
    assert _dag_repair_runtime_for("lens:build-dependency") == "primary"

    # Unknown key with no fallback -> None.
    assert _dag_repair_runtime_for("nonexistent-role") is None

    # Unknown key with fallback -> fallback.
    assert _dag_repair_runtime_for("nonexistent-role", "primary") == "primary"
    assert _dag_repair_runtime_for("unknown", "fallback") == "fallback"

    # Fallback is NEVER consulted for a known key.
    assert (
        _dag_repair_runtime_for("dag-fix", "secondary-override") == "primary"
    )


def test_dag_group_runtime_pair_alternates_under_default_policy() -> None:
    """`_dag_group_runtime_pair(group_idx, runtime_policy)` returns a
    `(implementation_runtime, review_runtime)` 2-tuple. Under DEFAULT
    policy (`alternating`), even groups get `(primary, secondary)` and
    odd groups get `(secondary, primary)` -- adversarial review
    alternates each group. Under `primary-impl-secondary-review`,
    EVERY group is `(primary, secondary)` (the impl runtime is pinned).
    Pinned by `tests/workflows/test_runtime_policy.py:14-22, :36-43`.
    """

    from iriai_build_v2.workflows.develop.execution.dispatcher import (
        _dag_group_runtime_pair,
    )

    # DEFAULT (alternating) policy.
    assert _dag_group_runtime_pair(0, DEFAULT_RUNTIME_POLICY) == (
        "primary",
        "secondary",
    )
    assert _dag_group_runtime_pair(1, DEFAULT_RUNTIME_POLICY) == (
        "secondary",
        "primary",
    )
    assert _dag_group_runtime_pair(2, DEFAULT_RUNTIME_POLICY) == (
        "primary",
        "secondary",
    )
    assert _dag_group_runtime_pair(3, DEFAULT_RUNTIME_POLICY) == (
        "secondary",
        "primary",
    )

    # PRIMARY_IMPL_SECONDARY_REVIEW policy: impl is pinned to primary,
    # review is pinned to secondary, regardless of group parity.
    assert _dag_group_runtime_pair(0, PRIMARY_IMPL_SECONDARY_REVIEW_POLICY) == (
        "primary",
        "secondary",
    )
    assert _dag_group_runtime_pair(1, PRIMARY_IMPL_SECONDARY_REVIEW_POLICY) == (
        "primary",
        "secondary",
    )
    assert _dag_group_runtime_pair(99, PRIMARY_IMPL_SECONDARY_REVIEW_POLICY) == (
        "primary",
        "secondary",
    )


def test_post_dag_runtime_pair_inverts_group_parity() -> None:
    """`_post_dag_runtime_pair(last_group_idx, runtime_policy)` returns
    `(gate_runtime, fix_runtime)`. Under DEFAULT (alternating) policy
    the pair is INVERTED relative to `_dag_group_runtime_pair` so the
    final group's impl runtime becomes the post-DAG fix runtime (and
    its review runtime becomes the gate runtime) -- ensuring the
    post-DAG verifier is a different model from the implementer of the
    last group. Under `primary-impl-secondary-review`, the gate runtime
    is pinned to `secondary` (the review runtime) and the fix runtime
    is pinned to `primary` (the impl runtime). Pinned by
    `tests/workflows/test_runtime_policy.py:25-33, :45-52`.
    """

    from iriai_build_v2.workflows.develop.execution.dispatcher import (
        _post_dag_runtime_pair,
    )

    # DEFAULT (alternating) policy: even-last-group gets (secondary,
    # primary); odd-last-group gets (primary, secondary). Inversion of
    # `_dag_group_runtime_pair`.
    assert _post_dag_runtime_pair(0, DEFAULT_RUNTIME_POLICY) == (
        "secondary",
        "primary",
    )
    assert _post_dag_runtime_pair(1, DEFAULT_RUNTIME_POLICY) == (
        "primary",
        "secondary",
    )

    # PRIMARY_IMPL_SECONDARY_REVIEW policy: gate=secondary, fix=primary,
    # regardless of group parity.
    assert _post_dag_runtime_pair(0, PRIMARY_IMPL_SECONDARY_REVIEW_POLICY) == (
        "secondary",
        "primary",
    )
    assert _post_dag_runtime_pair(1, PRIMARY_IMPL_SECONDARY_REVIEW_POLICY) == (
        "secondary",
        "primary",
    )
    assert _post_dag_runtime_pair(7, PRIMARY_IMPL_SECONDARY_REVIEW_POLICY) == (
        "secondary",
        "primary",
    )


def test_diagnostic_runtime_for_policy_returns_none_for_default() -> None:
    """`_diagnostic_runtime_for_policy(runtime_policy)` returns the
    runtime to use for RCA / triage / regression analysis under a
    given policy. Under DEFAULT (alternating), no diagnostic runtime
    is pinned -- returns `None` so callers fall back to per-task /
    per-group routing. Under `primary-impl-secondary-review`, the
    diagnostic runtime is pinned to `secondary`. Pinned by
    `tests/workflows/test_runtime_policy.py:55-60`.
    """

    from iriai_build_v2.workflows.develop.execution.dispatcher import (
        _diagnostic_runtime_for_policy,
    )

    assert _diagnostic_runtime_for_policy(DEFAULT_RUNTIME_POLICY) is None
    assert (
        _diagnostic_runtime_for_policy(PRIMARY_IMPL_SECONDARY_REVIEW_POLICY)
        == "secondary"
    )


def test_make_parallel_actor_creates_renamed_copy_with_runtime_metadata() -> None:
    """`_make_parallel_actor(base, suffix, runtime=None, workspace_path=None,
    runtime_workspace_binding=None, sandbox_required=False) -> AgentActor`
    creates a parallel-safe copy of `base` with name `{base.name}-{suffix}`,
    a `model_copy`d role whose metadata is extended (not mutated) with
    `runtime` / `workspace_override` / `sandbox_required` /
    `runtime_workspace_binding` / `write_producing`. Pinned by
    `tests/workflows/test_dag_expanded_verify.py:15913` (the legacy
    monkeypatch surface) and the post-DAG gate construction at
    `implementation.py:10454, :10465` etc.
    """

    from iriai_compose import AgentActor
    from iriai_compose.actors import Role

    from iriai_build_v2.workflows.develop.execution.dispatcher import (
        _make_parallel_actor,
    )

    base_role = Role(
        name="implementer",
        prompt="impl prompt",
        metadata={"runtime": "primary", "preexisting_key": "preserved"},
    )
    base = AgentActor(
        name="implementer",
        role=base_role,
        context_keys=["k1", "k2"],
        persistent=True,
    )

    # No runtime / workspace / binding -> only the name + suffix change;
    # metadata copied (not aliased).
    copy = _make_parallel_actor(base, "g0")
    assert copy.name == "implementer-g0"
    assert copy.role is not base.role  # `.model_copy` returned a new role
    assert copy.role.metadata == {
        "runtime": "primary",
        "preexisting_key": "preserved",
    }
    assert list(copy.context_keys) == ["k1", "k2"]
    assert copy.persistent is True
    # Mutating the copy's metadata does NOT touch the base.
    copy.role.metadata["new_key"] = "ok"
    assert "new_key" not in base.role.metadata

    # `runtime` override goes into the rebuilt metadata; original base
    # metadata is preserved (a model_copy + dict-update, not a replace).
    with_runtime = _make_parallel_actor(base, "g1", runtime="secondary")
    assert with_runtime.name == "implementer-g1"
    assert with_runtime.role.metadata["runtime"] == "secondary"
    assert with_runtime.role.metadata["preexisting_key"] == "preserved"

    # `workspace_path` override sets workspace_override in metadata.
    with_workspace = _make_parallel_actor(
        base, "g2", runtime="primary", workspace_path="/feat/repos/repo-x"
    )
    assert with_workspace.role.metadata["workspace_override"] == (
        "/feat/repos/repo-x"
    )

    # `sandbox_required=True` without a binding sets sandbox_required.
    with_sandbox = _make_parallel_actor(
        base, "g3", sandbox_required=True
    )
    assert with_sandbox.role.metadata["sandbox_required"] is True
    # `write_producing` is only set when there's a binding.
    assert "write_producing" not in with_sandbox.role.metadata

    # With a typed-ish `runtime_workspace_binding` (an object with
    # `model_dump`), the binding payload is serialized + sandbox_required
    # forced True + write_producing True + workspace_override derived
    # from the binding's cwd.
    class _FakeBinding:
        def model_dump(self, mode: str | None = None) -> dict[str, object]:
            del mode
            return {
                "cwd": "/sandbox/repo-x",
                "workspace_override": "/sandbox/repo-x",
                "sandbox_id": "sb-abc",
            }

    with_binding = _make_parallel_actor(
        base,
        "g4",
        runtime="primary",
        runtime_workspace_binding=_FakeBinding(),
    )
    assert with_binding.role.metadata["sandbox_required"] is True
    assert with_binding.role.metadata["write_producing"] is True
    assert with_binding.role.metadata["workspace_override"] == "/sandbox/repo-x"
    binding_payload = with_binding.role.metadata["runtime_workspace_binding"]
    assert binding_payload["cwd"] == "/sandbox/repo-x"
    assert binding_payload["sandbox_id"] == "sb-abc"

    # A dict-shaped binding (no `model_dump`) is also accepted and
    # serialized via `dict(...)`.
    dict_binding = {
        "cwd": "/sandbox/repo-y",
        "workspace_override": "",
        "sandbox_id": "sb-def",
    }
    with_dict_binding = _make_parallel_actor(
        base, "g5", runtime_workspace_binding=dict_binding
    )
    binding_payload2 = with_dict_binding.role.metadata[
        "runtime_workspace_binding"
    ]
    assert binding_payload2["cwd"] == "/sandbox/repo-y"
    assert with_dict_binding.role.metadata["workspace_override"] == (
        "/sandbox/repo-y"
    )

    # Empty cwd in a dict-shape binding falls back to workspace_path,
    # else to "" -- the str(...) coercion guards against None.
    empty_binding = {
        "cwd": "",
        "workspace_override": "",
        "sandbox_id": "sb-ghi",
    }
    with_empty = _make_parallel_actor(
        base,
        "g6",
        workspace_path="/feat/fallback",
        runtime_workspace_binding=empty_binding,
    )
    assert with_empty.role.metadata["workspace_override"] == "/feat/fallback"


def test_cluster_ownership_pin_dispatcher_module() -> None:
    """All six moved names land in the canonical
    `execution/dispatcher.py` module (not in any other `execution/`
    sibling like `types.py`, `git_service.py`, `task_contracts.py`, or
    `sandbox.py`). Belt-and-braces guard against a future refactor
    accidentally relocating one of the helpers to the wrong canonical
    module while leaving the shim intact.
    """

    from iriai_build_v2.workflows.develop.execution import (
        dispatcher as dispatcher_mod,
    )

    expected = "iriai_build_v2.workflows.develop.execution.dispatcher"
    for name in MOVED_CALLABLES:
        obj = getattr(dispatcher_mod, name)
        assert obj.__module__ == expected, (
            f"{name}.__module__ = {obj.__module__!r}; expected {expected!r}"
        )

    # Cross-check that the names are NOT served by any of the sibling
    # execution modules (a deliberate "did anyone else accidentally
    # define a copy?" probe).
    from iriai_build_v2.workflows.develop.execution import (
        git_service as git_service_mod,
        sandbox as sandbox_mod,
        task_contracts as task_contracts_mod,
        types as types_mod,
    )
    for name in MOVED_NAMES:
        for sibling, sibling_name in (
            (git_service_mod, "git_service"),
            (sandbox_mod, "sandbox"),
            (task_contracts_mod, "task_contracts"),
            (types_mod, "types"),
        ):
            assert not hasattr(sibling, name), (
                f"sibling drift: {sibling_name}.{name} unexpectedly exists; "
                "cluster ownership pin failed"
            )


def test_shim_block_exports_all_six_names() -> None:
    """The Slice-11e shim block in `implementation.py` re-exports
    exactly the six moved names from `..execution.dispatcher`. This
    test asserts the shim block actually carries all six (a deliberate
    "did the shim block lose a name?" probe) and that the existing
    `Dispatcher*` aliases for the Slice-05 SDK at `:75-113` are
    unchanged (the alias-import block is the consumer-facing surface
    for the Slice-05 SDK; this test only pins the Slice-11e block).
    """

    from iriai_build_v2.workflows.develop.phases import implementation as impl_mod

    # All six moved names are accessible via the impl module.
    for name in MOVED_NAMES:
        assert hasattr(impl_mod, name), (
            f"implementation.{name} missing -- the Slice-11e shim block "
            "dropped a re-export"
        )

    # The Slice-05 `Dispatcher*` aliases are STILL present and STILL
    # point to the existing canonical surface (the alias-import block
    # at implementation.py:75-113 is untouched by Slice 11e).
    from iriai_build_v2.workflows.develop.execution.dispatcher import (
        RuntimeDispatcher as CanonicalRuntimeDispatcher,
    )
    assert impl_mod.RuntimeDispatcher is CanonicalRuntimeDispatcher
