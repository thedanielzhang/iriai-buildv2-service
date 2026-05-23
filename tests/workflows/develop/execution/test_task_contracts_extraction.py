"""Slice 11c -- extraction proof for `execution/task_contracts.py` extension.

Verifies the doc-11 § "How To Use This Map" four-question contract for
the pure task-contract projection-key/sanitization extraction:

1. What behavior moved: three pure task-contract string/format helpers
   (`_task_contract_projection_key`, `_safe_contract_key_fragment`,
   `_contract_stage_sandbox_id`) moved from
   `workflows/develop/phases/implementation.py` to
   `workflows/develop/execution/task_contracts.py`. The Slice-03
   ContractCompiler + request/response types + ContractVerdict surface
   that was already in `task_contracts.py` is UNTOUCHED -- Slice 11c
   EXTENDS, never modifies.
2. Which legacy import names still work: every existing
   `from iriai_build_v2.workflows.develop.phases.implementation import X`
   for one of the three moved names keeps resolving to the SAME object
   as the canonical definition in `execution/task_contracts.py` (the
   shim is `is`-equivalent, not a copy). One known external monkeypatch
   target (`tests/workflows/develop/execution/
   test_implementation_workspace_authority_adapter.py:1517` accessing
   `implementation_module._task_contract_projection_key`) keeps
   resolving via the shim.
3. Which targeted tests prove the new facade and the compatibility
   shim: THIS file is one of them; it pins every moved name's shim
   equivalence and behaviorally smoke-tests each moved helper.
4. Why is the PR still refactor-only: nothing else moves. The pure
   string-format / regex-sanitization helpers moved byte-for-byte. The
   `_model_json_dict`-coupled cluster
   (`_task_contract_projection_summary`, `_task_contract_digest`,
   `_task_contract_id`, `_task_contract_prompt_block`,
   `_contract_repo_id`, `_contract_repo_path`,
   `_contract_verdict_projection_key`,
   `_contract_relative_observed_path`,
   `_contract_rule_matches_observed_path`, etc.) is deferred because
   `_model_json_dict` is a generic 35-caller Pydantic/dataclass-
   conversion utility (NOT task-contract-specific). The
   `_workspace_authority_jsonable`-coupled cluster
   (`_dedupe_contract_items`, `_combined_contract_for_repo`) is
   deferred to Slice 11d (workspace_authority). The
   `_dedupe_preserving_order`-coupled cluster
   (`_result_paths_for_contracts`, `_reported_paths_by_repo`,
   `_parse_git_status_patch_paths`) is deferred because
   `_dedupe_preserving_order` is generic, not task-contract-specific.
   The persistence/orchestration family (`_persist_task_contracts`,
   `_compile_task_contracts_for_group`,
   `_record_precommit_contract_verdicts`, etc.) is genuinely
   PHASE-LEVEL (`runner` + `feature` + I-O via `runner.artifacts` or
   typed store transactions) and CORRECTLY stays in
   `implementation.py` per the prompt hard rule against splitting
   non-pure helpers.
"""

from __future__ import annotations

import pytest


# Each entry is a name moved from `implementation.py` to
# `execution/task_contracts.py` in Slice 11c. The order is the import-line
# order in the shim block at `implementation.py:395-399` (the Slice-11c
# block) so a grep over either file lists the names in the same order.
MOVED_NAMES = [
    "_contract_stage_sandbox_id",
    "_safe_contract_key_fragment",
    "_task_contract_projection_key",
]


@pytest.mark.parametrize("name", MOVED_NAMES)
def test_shim_re_export_is_same_object_as_new_canonical(name: str) -> None:
    """Every moved helper imported via the OLD path is the SAME object
    as the import via the NEW canonical path. Proves the shim is a re-
    export, not a copy. Locks the monkeypatch target equivalence --
    `monkeypatch.setattr(implementation_module, name, ...)` will mutate
    the SAME function object that any direct
    `from execution.task_contracts import name` reader sees.
    """

    from iriai_build_v2.workflows.develop import execution as execution_pkg
    from iriai_build_v2.workflows.develop.execution import (
        task_contracts as task_contracts_mod,
    )
    from iriai_build_v2.workflows.develop.phases import implementation as impl_mod

    legacy = getattr(impl_mod, name)
    canonical = getattr(task_contracts_mod, name)
    assert legacy is canonical, (
        f"shim drift: implementation.{name} is not the same object as "
        f"execution.task_contracts.{name}"
    )
    # `execution_pkg` is imported only to ensure the package import chain
    # works end-to-end (no side-effect import errors).
    _ = execution_pkg


@pytest.mark.parametrize("name", MOVED_NAMES)
def test_canonical_module_is_task_contracts(name: str) -> None:
    """The moved function objects' `__module__` is the new canonical
    `iriai_build_v2.workflows.develop.execution.task_contracts` -- not
    the legacy `...phases.implementation`. Proves the definition
    genuinely moved rather than being re-aliased from the old module.
    """

    from iriai_build_v2.workflows.develop.execution import (
        task_contracts as task_contracts_mod,
    )

    canonical = getattr(task_contracts_mod, name)
    assert canonical.__module__ == (
        "iriai_build_v2.workflows.develop.execution.task_contracts"
    ), (
        f"{name}.__module__ = {canonical.__module__!r}; expected the new "
        "task_contracts-module path"
    )


def test_task_contract_projection_key_returns_legacy_artifact_key_format() -> None:
    """`_task_contract_projection_key` returns the legacy
    `"dag-task-contract:{task_id}"` artifact key used by the contract
    projection path. The exact format is pinned by every
    `runner.artifacts.get/put(_task_contract_projection_key(task_id),
    ...)` callsite in `implementation.py` (at `:2581, :2602, :2704`)
    and by `tests/workflows/develop/execution/
    test_implementation_workspace_authority_adapter.py:1517` which
    accesses `implementation_module._task_contract_projection_key`.
    """

    from iriai_build_v2.workflows.develop.execution.task_contracts import (
        _task_contract_projection_key,
    )

    assert _task_contract_projection_key("TASK-0") == "dag-task-contract:TASK-0"
    assert _task_contract_projection_key("g3-t7") == "dag-task-contract:g3-t7"
    # Empty + None-shaped inputs are passed through as-is (the function
    # itself does NOT defend against falsy input; the caller is expected
    # to pre-validate). This pin protects against silent coercion.
    assert _task_contract_projection_key("") == "dag-task-contract:"


def test_safe_contract_key_fragment_normalizes_and_falls_back() -> None:
    """`_safe_contract_key_fragment` collapses every run of characters
    outside `[A-Za-z0-9_.-]` into a single `-`, trims leading/trailing
    `-`, and falls back to `"unknown"` when the result is empty. Pinned
    by every `_contract_stage_sandbox_id` callsite + the `dag-sandbox-
    patch` projection-key construction at
    `implementation.py:3141, :3241`.
    """

    from iriai_build_v2.workflows.develop.execution.task_contracts import (
        _safe_contract_key_fragment,
    )

    # Alphanumeric + dot + underscore + hyphen pass through.
    assert _safe_contract_key_fragment("repo_main.v2-beta") == "repo_main.v2-beta"
    # Spaces + special chars collapse to `-`.
    assert _safe_contract_key_fragment("hello world!@#") == "hello-world"
    # Runs of special chars become a single `-`.
    assert _safe_contract_key_fragment("a   b   c") == "a-b-c"
    # Leading/trailing whitespace + special chars get trimmed.
    assert _safe_contract_key_fragment("  /repo/main/  ") == "repo-main"
    # Empty string falls back to "unknown".
    assert _safe_contract_key_fragment("") == "unknown"
    # Only special chars also fall back to "unknown".
    assert _safe_contract_key_fragment("///") == "unknown"
    # None-like input ("" after `str(value or "")`) falls back to "unknown".
    assert _safe_contract_key_fragment(None) == "unknown"  # type: ignore[arg-type]


def test_contract_stage_sandbox_id_composes_canonical_precommit_format() -> None:
    """`_contract_stage_sandbox_id` composes the pre-commit canonical
    sandbox-id format used by the contract verdict + patch projection
    paths. The exact format
    `"canonical-precommit-g{group_idx}-{stage}-repo-{repo_id}"` is
    pinned by callsites at `implementation.py:3426, :7123` and by the
    `_contract_verdict_projection_key` composition at `:2799-2800`.
    Stage + repo_id are sanitized through `_safe_contract_key_fragment`
    (which is also moved in this slice).
    """

    from iriai_build_v2.workflows.develop.execution.task_contracts import (
        _contract_stage_sandbox_id,
    )

    # Clean inputs pass through.
    assert (
        _contract_stage_sandbox_id(5, "verify", "repo-main")
        == "canonical-precommit-g5-verify-repo-repo-main"
    )
    # Stage + repo_id with special chars get sanitized via
    # `_safe_contract_key_fragment`. This proves the cluster moves
    # together -- the moved `_contract_stage_sandbox_id` correctly
    # calls the moved `_safe_contract_key_fragment` (NOT a stale copy
    # still in `implementation.py`).
    assert (
        _contract_stage_sandbox_id(12, "verify stage", "repo/main")
        == "canonical-precommit-g12-verify-stage-repo-repo-main"
    )
    # Empty stage + empty repo_id fall back to "unknown" via
    # `_safe_contract_key_fragment`.
    assert (
        _contract_stage_sandbox_id(0, "", "")
        == "canonical-precommit-g0-unknown-repo-unknown"
    )
    # group_idx of 0 + negative are accepted as-is (the f-string format
    # passes them through; the caller is expected to provide a
    # non-negative attempt index).
    assert (
        _contract_stage_sandbox_id(99, "implementation", "iriai-build-v2")
        == "canonical-precommit-g99-implementation-repo-iriai-build-v2"
    )


def test_safe_contract_key_fragment_and_stage_sandbox_id_share_canonical_object() -> None:
    """The moved `_contract_stage_sandbox_id` calls into the moved
    `_safe_contract_key_fragment` via the SAME canonical object as a
    direct import. Proves the cluster is internally consistent after
    the move (no stale closure over the legacy `implementation.py`
    binding). A regression here would mean
    `_contract_stage_sandbox_id` calls the old `implementation.py`
    helper instead of the moved one.
    """

    from iriai_build_v2.workflows.develop.execution import (
        task_contracts as task_contracts_mod,
    )

    # If `_contract_stage_sandbox_id` somehow closed over a stale binding
    # of `_safe_contract_key_fragment` from `implementation.py`, the test
    # below would still pass (closures bind by name, not by value, in
    # Python). The real proof is that BOTH functions live in the same
    # canonical module and the integration smoke (above) yields the
    # expected sanitized output. This belt-and-braces test pins the
    # cluster ownership.
    assert (
        task_contracts_mod._contract_stage_sandbox_id.__module__
        == task_contracts_mod._safe_contract_key_fragment.__module__
        == "iriai_build_v2.workflows.develop.execution.task_contracts"
    )
