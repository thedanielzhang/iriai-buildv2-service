"""Deterministic tests for the e2e build isolation guard (no git, no I/O).

These run as part of ``pytest tests/`` and never inspect the live tree, so they
cannot false-flag the concurrent runner agent's legitimate workflow commits.
"""

from __future__ import annotations

from tests.isolation_guard import (
    allowlist_violations,
    hook_violations,
    is_e2e_commit,
    protected_violations,
)

ORCH = "src/iriai_build_v2/interfaces/slack/orchestrator.py"
EXEC = "src/iriai_build_v2/workflows/develop/execution/dispatcher.py"
SANDBOX = "src/iriai_build_v2/workflows/develop/execution/sandbox.py"
PHASE = "src/iriai_build_v2/workflows/develop/phases/implementation.py"
E2E = "src/iriai_build_v2/workflows/develop/e2e/checkpoint.py"
ROLE = "src/iriai_build_v2/roles/spec_author/__init__.py"
CLI = "src/iriai_build_v2/interfaces/cli/e2e_cmd.py"
REGISTRY = "src/iriai_build_v2/roles/__init__.py"
TEST = "tests/test_checkpoint.py"


def test_protected_paths_detected():
    assert protected_violations([ORCH]) == [ORCH]
    assert protected_violations([EXEC]) == [EXEC]
    assert protected_violations([SANDBOX]) == [SANDBOX]
    assert protected_violations([PHASE]) == [PHASE]


def test_e2e_owned_paths_are_not_protected():
    assert protected_violations([E2E, ROLE, CLI, REGISTRY, TEST]) == []


def test_allowlist_accepts_e2e_paths():
    assert allowlist_violations([E2E, ROLE, CLI, REGISTRY, TEST]) == []


def test_allowlist_rejects_outside_paths():
    stray = "src/iriai_build_v2/config.py"
    assert allowlist_violations([E2E, stray]) == [stray]


def test_is_e2e_commit():
    assert is_e2e_commit([E2E])
    assert is_e2e_commit([ROLE, TEST])
    assert not is_e2e_commit([EXEC, ORCH])
    # tests/ alone does not mark a commit as e2e-owned (could be runner's test).
    assert not is_e2e_commit([TEST])


def test_hook_rejects_mixed_commit():
    # The leak signature: a protected path staged alongside an e2e-owned path.
    assert hook_violations([E2E, ORCH]) == [ORCH]
    assert hook_violations([ROLE, EXEC]) == [EXEC]


def test_hook_allows_pure_runner_commit():
    # The concurrent runner stages only workflow files — must pass untouched.
    assert hook_violations([EXEC, PHASE, ORCH]) == []
    assert hook_violations([SANDBOX]) == []


def test_hook_allows_pure_e2e_commit():
    assert hook_violations([E2E, ROLE, CLI, TEST]) == []
