"""Slice 19A-5 sentinel for the pre-activation task-execute boundary.

Slice 19 now provides a reusable display/advisory governance-context
builder only. Production task-execute consumption must be introduced by
a later accepted source-of-truth slice, so the current dispatcher,
runtime, and workflow-agent execution modules must not silently start
consuming the Slice 19 governance-context builder or its typed output.

This test is intentionally static and negative: it fails if the current
task-execute consumer modules gain governance-context imports, typed
symbols, or prompt-field names before that later wiring is accepted and
the sentinel is deliberately closed or flipped.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


TASK_EXECUTE_CONSUMER_FILES: tuple[tuple[str, Path], ...] = (
    (
        "dispatcher",
        REPO_ROOT
        / "src"
        / "iriai_build_v2"
        / "workflows"
        / "develop"
        / "execution"
        / "dispatcher.py",
    ),
    (
        "runtime_client",
        REPO_ROOT
        / "src"
        / "iriai_build_v2"
        / "workflows"
        / "develop"
        / "execution"
        / "runtime_client.py",
    ),
    (
        "implementation_phase",
        REPO_ROOT
        / "src"
        / "iriai_build_v2"
        / "workflows"
        / "develop"
        / "phases"
        / "implementation.py",
    ),
    (
        "codex_runtime",
        REPO_ROOT / "src" / "iriai_build_v2" / "runtimes" / "codex.py",
    ),
    (
        "claude_runtime",
        REPO_ROOT / "src" / "iriai_build_v2" / "runtimes" / "claude.py",
    ),
    (
        "claude_pool_runtime",
        REPO_ROOT
        / "src"
        / "iriai_build_v2"
        / "runtimes"
        / "claude_pool.py",
    ),
)


FORBIDDEN_GOVERNANCE_CONTEXT_TOKENS: tuple[str, ...] = (
    "governance_agent_context_builder",
    "GovernanceAgentContextBuilder",
    "AgentContextBuilderInputs",
    "AgentContextBuilderResult",
    "AgentContextBuilderGap",
    "AgentContextScope",
    "GovernanceAgentContext",
    "ContextLayerPackageSummary",
    "AGENT_CONTEXT_BUILDER_FAILURE_ID",
    "governance_context",
    "policy_guidance",
    "policy_guidance_authority",
    "governance_snapshot_api",
    "GovernanceSnapshot",
    "GovernanceEvidencePageRef",
)


FORBIDDEN_GOVERNANCE_CONTEXT_IMPORT_PREFIXES: tuple[str, ...] = (
    "iriai_build_v2.execution_control.governance_agent",
    "iriai_build_v2.execution_control.governance_agent_context_builder",
    "iriai_build_v2.execution_control.governance_snapshot_api",
    "iriai_build_v2.execution_control.finding_engine",
    "iriai_build_v2.execution_control.policy_recommendation",
    "iriai_build_v2.workflows.develop.governance.models",
)


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _import_modules(source: str) -> list[str]:
    tree = ast.parse(source)
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            modules.append(module)
            modules.extend(f"{module}.{alias.name}" for alias in node.names)
    return modules


@pytest.mark.parametrize(("label", "path"), TASK_EXECUTE_CONSUMER_FILES)
def test_task_execute_consumers_do_not_import_governance_context(
    label: str, path: Path
) -> None:
    """Task-execute consumers cannot pull in Slice 19 governance context.

    If this fails, production task-execute governance-context wiring is
    in flight and this sentinel must be closed or flipped in the same
    accepted slice that adds the authoritative consumer.
    """

    source = _source(path)
    imported = _import_modules(source)
    matches = [
        module
        for module in imported
        if module.startswith(FORBIDDEN_GOVERNANCE_CONTEXT_IMPORT_PREFIXES)
    ]
    assert matches == [], (
        f"{label} imports governance-context module(s) before production "
        f"task-execute wiring is accepted: {matches}"
    )


@pytest.mark.parametrize(("label", "path"), TASK_EXECUTE_CONSUMER_FILES)
def test_task_execute_consumers_do_not_reference_governance_context_symbols(
    label: str, path: Path
) -> None:
    """Catch silent prompt/schema consumption without relying on imports."""

    source = _source(path)
    matches = [
        token
        for token in FORBIDDEN_GOVERNANCE_CONTEXT_TOKENS
        if token in source
    ]
    assert matches == [], (
        f"{label} references governance-context token(s) before production "
        f"task-execute wiring is accepted: {matches}"
    )


def test_task_execute_boundary_roster_is_the_expected_pre_slice21_surface() -> None:
    """Make the sentinel's consumer roster deliberate, not accidental."""

    labels = [label for label, _ in TASK_EXECUTE_CONSUMER_FILES]
    assert labels == [
        "dispatcher",
        "runtime_client",
        "implementation_phase",
        "codex_runtime",
        "claude_runtime",
        "claude_pool_runtime",
    ]
    missing = [
        str(path)
        for _, path in TASK_EXECUTE_CONSUMER_FILES
        if not path.exists()
    ]
    assert missing == []
