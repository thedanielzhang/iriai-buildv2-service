"""Slice 11d -- extraction proof for `execution/sandbox.py` extension.

Verifies the doc-11 § "How To Use This Map" four-question contract for
the pure sandbox-lifecycle helper extraction:

1. What behavior moved: six pure sandbox-lifecycle helpers
   (`_sandbox_blocker`, `_is_terminal_sandbox_attempt_blocker`,
   `_sandbox_manifest_for_binding`, `_repair_repo_id_for_sandbox`,
   `_sandbox_prompt_context_dir`,
   `_exclude_sandbox_prompt_context_from_capture`) moved from
   `workflows/develop/phases/implementation.py` to
   `workflows/develop/execution/sandbox.py`. The Slice-04
   `SandboxRunner` + `SandboxSpec` + `SandboxLease` +
   `RuntimeWorkspaceBinding` + `PatchCaptureResult` + `SandboxRepoPatch` +
   six `Sandbox*Error` classes already in `sandbox.py` are UNTOUCHED --
   Slice 11d EXTENDS, never modifies.
2. Which legacy import names still work: every existing
   `from iriai_build_v2.workflows.develop.phases.implementation import X`
   for one of the six moved names keeps resolving to the SAME object as
   the canonical definition in `execution/sandbox.py` (the shim is
   `is`-equivalent, not a copy). `monkeypatch.setattr(implementation_
   module, X, ...)` continues to mutate the SAME binding any direct
   `from execution.sandbox import X` reader sees.
3. Which targeted tests prove the new facade and the compatibility
   shim: THIS file is one of them; it pins every moved name's shim
   equivalence and behaviorally smoke-tests each moved helper.
4. Why is the PR still refactor-only: nothing else moves. The pure
   sandbox-lifecycle helpers moved byte-for-byte. The phase-level
   sandbox port (the `_ImplementationSandboxPort` adapter class + the
   six async orchestrators `_bind_repair_sandbox`,
   `_promote_sandbox_capture_to_feature_worktree`,
   `_validate_sandbox_capture_against_contract`,
   `_capture_and_promote_sandbox_patch`, `_bind_task_sandbox`,
   `_bind_post_dag_product_repair_sandbox`) is genuinely PHASE-LEVEL
   (each takes `runner`+`feature` and depends on the typed
   `ExecutionControlStore` + `ContractCompiler.validate_patch` + the
   `_contract_workspace_snapshot` + projection / verdict-recording
   family) and CORRECTLY stays in `implementation.py` per the prompt
   hard rule against splitting non-pure helpers. `_write_sandbox_
   settings` is deferred (logs through the implementation.py-
   namespaced module logger and is feature-scaffold not sandbox-
   lifecycle). `_sandbox_runtime_name` + `_repair_sandbox_required`
   are deferred (depend on `runner` attribute access).
   `_sandbox_git_bytes` is deferred (belongs in `git_service.py`,
   Slice 11b's owner, not `sandbox.py`).
   `_build_task_prompt_with_optional_sandbox_context` is deferred
   (depends on `_build_task_prompt` which is `_model_json_dict`-
   coupled per the 11c deferral chain).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest


# Each entry is a name moved from `implementation.py` to
# `execution/sandbox.py` in Slice 11d. The order is the import-line
# order in the shim block in `implementation.py` (the Slice-11d block)
# so a grep over either file lists the names in the same order.
MOVED_NAMES = [
    "_exclude_sandbox_prompt_context_from_capture",
    "_is_terminal_sandbox_attempt_blocker",
    "_repair_repo_id_for_sandbox",
    "_sandbox_blocker",
    "_sandbox_manifest_for_binding",
    "_sandbox_prompt_context_dir",
]


@pytest.mark.parametrize("name", MOVED_NAMES)
def test_shim_re_export_is_same_object_as_new_canonical(name: str) -> None:
    """Every moved helper imported via the OLD path is the SAME object
    as the import via the NEW canonical path. Proves the shim is a re-
    export, not a copy. Locks the monkeypatch target equivalence --
    `monkeypatch.setattr(implementation_module, name, ...)` will mutate
    the SAME function object that any direct
    `from execution.sandbox import name` reader sees.
    """

    from iriai_build_v2.workflows.develop import execution as execution_pkg
    from iriai_build_v2.workflows.develop.execution import (
        sandbox as sandbox_mod,
    )
    from iriai_build_v2.workflows.develop.phases import implementation as impl_mod

    legacy = getattr(impl_mod, name)
    canonical = getattr(sandbox_mod, name)
    assert legacy is canonical, (
        f"shim drift: implementation.{name} is not the same object as "
        f"execution.sandbox.{name}"
    )
    # `execution_pkg` is imported only to ensure the package import chain
    # works end-to-end (no side-effect import errors).
    _ = execution_pkg


@pytest.mark.parametrize("name", MOVED_NAMES)
def test_canonical_module_is_sandbox(name: str) -> None:
    """The moved function objects' `__module__` is the new canonical
    `iriai_build_v2.workflows.develop.execution.sandbox` -- not the
    legacy `...phases.implementation`. Proves the definition genuinely
    moved rather than being re-aliased from the old module.
    """

    from iriai_build_v2.workflows.develop.execution import (
        sandbox as sandbox_mod,
    )

    canonical = getattr(sandbox_mod, name)
    assert canonical.__module__ == (
        "iriai_build_v2.workflows.develop.execution.sandbox"
    ), (
        f"{name}.__module__ = {canonical.__module__!r}; expected the new "
        "sandbox-module path"
    )


def test_sandbox_blocker_constructs_typed_workflow_blocker() -> None:
    """`_sandbox_blocker` is the canonical factory wrapping
    `SandboxWorkflowBlocker(message, task_id=task_id)`. Every other
    moved helper raises through this factory; the orchestrator family
    in `implementation.py` (`_bind_task_sandbox`,
    `_capture_and_promote_sandbox_patch`, etc.) calls it ~30+ times to
    convert sandbox lifecycle failures into the typed workflow blocker
    that the failure router routes off of. Pinned by the call-pattern
    `raise _sandbox_blocker(f"...", task_id=...)`.
    """

    from iriai_build_v2.workflows.develop.execution.sandbox import (
        _sandbox_blocker,
    )
    from iriai_build_v2.workflows.develop.execution.types import (
        SandboxWorkflowBlocker,
        _SANDBOX_WORKFLOW_BLOCKER_MARKER,
    )

    # No task_id (the default).
    blocker = _sandbox_blocker("manifest read failed")
    assert isinstance(blocker, SandboxWorkflowBlocker)
    assert isinstance(blocker, RuntimeError)
    # The marker is prepended by SandboxWorkflowBlocker so the failure
    # router can recognize the typed blocker by substring.
    assert _SANDBOX_WORKFLOW_BLOCKER_MARKER in str(blocker)
    assert "manifest read failed" in str(blocker)
    assert getattr(blocker, "task_id", None) is None

    # With task_id (the orchestrator-style call pattern).
    blocker_with_task = _sandbox_blocker(
        "sandbox binding failed for task T-1",
        task_id="T-1",
    )
    assert isinstance(blocker_with_task, SandboxWorkflowBlocker)
    assert getattr(blocker_with_task, "task_id", None) == "T-1"
    assert "sandbox binding failed for task T-1" in str(blocker_with_task)


def test_is_terminal_sandbox_attempt_blocker_matches_canonical_markers() -> None:
    """`_is_terminal_sandbox_attempt_blocker` recognizes the canonical
    "terminal sandbox lease" / "retained sandbox evidence" markers raised
    by the Slice-04 `SandboxRunner.allocate` path. Pinned by
    `implementation.py` callsites at the dispatch retry-budget gate
    (`:17252, :18271`) + the dispatch outcome classifier (`:8449`).
    A terminal blocker means the lease state is poisoned/retained AND
    cannot be reused -- the dispatcher must allocate a new attempt.
    """

    from iriai_build_v2.workflows.develop.execution.sandbox import (
        _is_terminal_sandbox_attempt_blocker,
    )

    # The three canonical terminal-attempt markers raised by
    # SandboxRunner.allocate (sandbox.py:305-318, :693-715) and the
    # _existing_lease_for_key path.
    assert _is_terminal_sandbox_attempt_blocker("terminal sandbox lease")
    assert _is_terminal_sandbox_attempt_blocker(
        "retained sandbox evidence requires a new attempt"
    )
    assert _is_terminal_sandbox_attempt_blocker(
        "retained sandbox evidence cannot be reused"
    )

    # Case-insensitive substring matching.
    assert _is_terminal_sandbox_attempt_blocker("TERMINAL SANDBOX LEASE!")
    assert _is_terminal_sandbox_attempt_blocker(
        "Sandbox failure: Retained Sandbox Evidence Requires A New Attempt"
    )
    # Embedded in a larger blocker message.
    assert _is_terminal_sandbox_attempt_blocker(
        "Sandbox binding failed for task T-7: terminal sandbox lease"
    )

    # Non-terminal sandbox blockers (re-attemptable) DO NOT match.
    assert not _is_terminal_sandbox_attempt_blocker("Sandbox binding failed")
    assert not _is_terminal_sandbox_attempt_blocker(
        "Sandbox manifest could not be read for sandbox-abc"
    )
    assert not _is_terminal_sandbox_attempt_blocker("sandbox patch dirty")
    assert not _is_terminal_sandbox_attempt_blocker("")
    # None-shaped input is coerced via `str(message or "").lower()`.
    assert not _is_terminal_sandbox_attempt_blocker(None)  # type: ignore[arg-type]


def test_sandbox_manifest_for_binding_reads_lease_root_json(tmp_path: Path) -> None:
    """`_sandbox_manifest_for_binding` reads
    `{binding.lease.root}/sandbox-manifest.json` via `Path(...).read_text`
    + `json.loads`. Returns the manifest as a dict. Raises
    `_sandbox_blocker` (`SandboxWorkflowBlocker`) wrapping the
    underlying exception when the file is unreadable. Pinned by
    `_promote_sandbox_capture_to_feature_worktree` at
    `implementation.py:6903` -- the only callsite. Round-trip-proof.
    """

    from iriai_build_v2.workflows.develop.execution.sandbox import (
        _sandbox_manifest_for_binding,
    )
    from iriai_build_v2.workflows.develop.execution.types import (
        SandboxWorkflowBlocker,
    )

    # Successful read.
    manifest_payload = {
        "manifest_version": "sandbox-runner-v1",
        "sandbox_id": "sandbox-feat1-g0-attempt-0-abcd1234",
        "root": str(tmp_path),
        "repo_roots": {"repo-main": str(tmp_path / "repos" / "repo-main")},
        "feature_id": "feat1",
    }
    (tmp_path / "sandbox-manifest.json").write_text(
        json.dumps(manifest_payload, indent=2), encoding="utf-8"
    )
    binding = SimpleNamespace(
        lease=SimpleNamespace(
            root=str(tmp_path),
            sandbox_id="sandbox-feat1-g0-attempt-0-abcd1234",
        ),
    )
    out = _sandbox_manifest_for_binding(binding)
    assert out == manifest_payload

    # Missing manifest -> SandboxWorkflowBlocker via _sandbox_blocker.
    missing_root = tmp_path / "missing"
    missing_root.mkdir()
    binding_missing = SimpleNamespace(
        lease=SimpleNamespace(
            root=str(missing_root),
            sandbox_id="sandbox-missing",
        ),
    )
    with pytest.raises(SandboxWorkflowBlocker) as excinfo:
        _sandbox_manifest_for_binding(binding_missing)
    assert "Sandbox manifest could not be read for sandbox-missing" in str(
        excinfo.value
    )

    # Invalid JSON -> SandboxWorkflowBlocker.
    bad_root = tmp_path / "bad"
    bad_root.mkdir()
    (bad_root / "sandbox-manifest.json").write_text(
        "{not-json}", encoding="utf-8"
    )
    binding_bad = SimpleNamespace(
        lease=SimpleNamespace(
            root=str(bad_root),
            sandbox_id="sandbox-bad",
        ),
    )
    with pytest.raises(SandboxWorkflowBlocker) as excinfo:
        _sandbox_manifest_for_binding(binding_bad)
    assert "Sandbox manifest could not be read for sandbox-bad" in str(
        excinfo.value
    )


def test_repair_repo_id_for_sandbox_resolves_from_contract_then_path() -> None:
    """`_repair_repo_id_for_sandbox` derives the repair-sandbox repo_id
    by precedence: (1) the contract.repo_id for any group task with a
    contract, then (2) the task.repo_path for any group task, then (3)
    the relative path of ws_path under feature_root, finally falling
    back to `"repo"`. Pinned by callsites at `implementation.py:6799,
    :32353` (the repair binding + post-DAG repair binding).
    """

    from iriai_build_v2.workflows.develop.execution.sandbox import (
        _repair_repo_id_for_sandbox,
    )
    from iriai_build_v2.models.outputs import ImplementationTask

    task_a = ImplementationTask(
        id="T-1",
        name="Task one",
        description="...",
        repo_path="",
    )
    task_b = ImplementationTask(
        id="T-2",
        name="Task two",
        description="...",
        repo_path="repo-secondary",
    )

    # (1) contract.repo_id wins for any task with a contract.
    contract = SimpleNamespace(repo_id="repo-main")
    assert (
        _repair_repo_id_for_sandbox(
            [task_a, task_b],
            {task_a.id: contract},
            feature_root=None,
            ws_path=None,
        )
        == "repo-main"
    )

    # (2) task.repo_path fallback when no contracts available.
    assert (
        _repair_repo_id_for_sandbox(
            [task_a, task_b],
            None,
            feature_root=None,
            ws_path=None,
        )
        == "repo-secondary"
    )

    # (3) ws_path.relative_to(feature_root) when no task has a
    # repo_path and feature_root + ws_path are provided.
    task_empty = ImplementationTask(
        id="T-0",
        name="empty",
        description="",
        repo_path="",
    )
    assert (
        _repair_repo_id_for_sandbox(
            [task_empty],
            {},
            feature_root=Path("/feature-root"),
            ws_path="/feature-root/repos/repo-x",
        )
        == "repos/repo-x"
    )

    # (4) fallback `"repo"` when neither contract nor task.repo_path
    # nor ws_path resolution yields a value.
    assert (
        _repair_repo_id_for_sandbox(
            [task_empty],
            None,
            feature_root=None,
            ws_path=None,
        )
        == "repo"
    )
    # ws_path == feature_root yields the fallback (resolved_ws ==
    # resolved_feature, no relative path).
    assert (
        _repair_repo_id_for_sandbox(
            [task_empty],
            None,
            feature_root=Path("/feature-root"),
            ws_path="/feature-root",
        )
        == "repo"
    )


def test_sandbox_prompt_context_dir_refuses_escapes_and_symlinks(
    tmp_path: Path,
) -> None:
    """`_sandbox_prompt_context_dir` returns
    `context_base / .iriai-context / context_segment`. Raises
    `_sandbox_blocker` when:
      - the `.iriai-context` root is a symlink (poisoned setup)
      - the per-segment context dir is a symlink
      - the resolved path escapes the context root (defense in depth
        even though `context_segment` is always derived from a task id)
    Pinned by `_build_task_prompt_with_optional_sandbox_context` at
    `implementation.py:12060` -- the only callsite.
    """

    from iriai_build_v2.workflows.develop.execution.sandbox import (
        _sandbox_prompt_context_dir,
    )
    from iriai_build_v2.workflows.develop.execution.types import (
        SandboxWorkflowBlocker,
    )

    # Successful resolution into a fresh tree.
    context_dir = _sandbox_prompt_context_dir(
        tmp_path,
        task_id="T-7",
        context_segment="t7-abc123",
    )
    assert context_dir == tmp_path / ".iriai-context" / "t7-abc123"
    # The function does NOT create the directory; only validates.
    assert not context_dir.exists()

    # Symlinked context_root is rejected.
    poisoned_root = tmp_path / "poisoned"
    poisoned_root.mkdir()
    (poisoned_root / ".iriai-context").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(SandboxWorkflowBlocker) as excinfo:
        _sandbox_prompt_context_dir(
            poisoned_root,
            task_id="T-1",
            context_segment="seg",
        )
    assert "Prompt context root is symlinked for task T-1" in str(excinfo.value)
    assert getattr(excinfo.value, "task_id", None) == "T-1"

    # Symlinked per-segment context dir is rejected.
    base = tmp_path / "ok"
    base.mkdir()
    context_root = base / ".iriai-context"
    context_root.mkdir()
    (context_root / "seg-1").symlink_to(tmp_path / "elsewhere2")
    with pytest.raises(SandboxWorkflowBlocker) as excinfo2:
        _sandbox_prompt_context_dir(
            base,
            task_id="T-2",
            context_segment="seg-1",
        )
    assert "Prompt context directory is symlinked for task T-2" in str(
        excinfo2.value
    )
    assert getattr(excinfo2.value, "task_id", None) == "T-2"


def test_exclude_sandbox_prompt_context_from_capture_appends_once(
    tmp_path: Path,
) -> None:
    """`_exclude_sandbox_prompt_context_from_capture` appends
    `/.iriai-context/{context_segment}/` to `.git/info/exclude` so the
    prompt-context side files don't leak into sandbox patch capture.
    Idempotent (no duplicate append). No-op when `.git` is not a
    directory. Pinned by
    `_build_task_prompt_with_optional_sandbox_context` at
    `implementation.py:12073`.
    """

    from iriai_build_v2.workflows.develop.execution.sandbox import (
        _exclude_sandbox_prompt_context_from_capture,
    )

    # No `.git` directory -> no-op (does not error).
    no_git_root = tmp_path / "no-git"
    no_git_root.mkdir()
    _exclude_sandbox_prompt_context_from_capture(
        no_git_root, context_segment="seg-1"
    )
    # No `.git` was created.
    assert not (no_git_root / ".git").exists()

    # With `.git` -> appends pattern to `.git/info/exclude`.
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    git_dir = repo_root / ".git"
    git_dir.mkdir()
    _exclude_sandbox_prompt_context_from_capture(
        repo_root, context_segment="t1-aaaa"
    )
    exclude_path = git_dir / "info" / "exclude"
    assert exclude_path.exists()
    contents = exclude_path.read_text(encoding="utf-8")
    assert "/.iriai-context/t1-aaaa/" in contents

    # Idempotent: a second call does NOT duplicate the pattern.
    _exclude_sandbox_prompt_context_from_capture(
        repo_root, context_segment="t1-aaaa"
    )
    contents_after_repeat = exclude_path.read_text(encoding="utf-8")
    assert contents_after_repeat == contents

    # A different segment appends a NEW line (does not collide).
    _exclude_sandbox_prompt_context_from_capture(
        repo_root, context_segment="t2-bbbb"
    )
    contents_two = exclude_path.read_text(encoding="utf-8")
    assert "/.iriai-context/t1-aaaa/" in contents_two
    assert "/.iriai-context/t2-bbbb/" in contents_two
    # Each pattern appears on its own line.
    lines = [line.strip() for line in contents_two.splitlines() if line.strip()]
    assert "/.iriai-context/t1-aaaa/" in lines
    assert "/.iriai-context/t2-bbbb/" in lines


def test_cluster_ownership_pin_sandbox_module() -> None:
    """All six moved names land in the canonical
    `execution/sandbox.py` module (not in any other `execution/`
    sibling like `types.py`, `git_service.py`, or
    `task_contracts.py`). Belt-and-braces guard against a future
    refactor accidentally relocating one of the helpers to the wrong
    canonical module while leaving the shim intact.
    """

    from iriai_build_v2.workflows.develop.execution import (
        sandbox as sandbox_mod,
    )

    expected = "iriai_build_v2.workflows.develop.execution.sandbox"
    for name in MOVED_NAMES:
        obj = getattr(sandbox_mod, name)
        assert obj.__module__ == expected, (
            f"{name}.__module__ = {obj.__module__!r}; expected {expected!r}"
        )

    # Cross-check that the names are NOT served by any of the sibling
    # execution modules (a deliberate "did anyone else accidentally
    # define a copy?" probe).
    from iriai_build_v2.workflows.develop.execution import (
        git_service as git_service_mod,
        task_contracts as task_contracts_mod,
        types as types_mod,
    )
    for name in MOVED_NAMES:
        for sibling, sibling_name in (
            (git_service_mod, "git_service"),
            (task_contracts_mod, "task_contracts"),
            (types_mod, "types"),
        ):
            assert not hasattr(sibling, name), (
                f"sibling drift: {sibling_name}.{name} unexpectedly exists; "
                "cluster ownership pin failed"
            )
