"""Slice 21 positive sentinel for governance-agent context-package wiring.

The former Slice 19 sentinel intentionally asserted that
``ContextLayerPackageSummary`` was absent until Slice 21 landed. Slice 21
now wires the typed package summary into the governance-agent reporting
surface, so this sentinel is flipped to assert the presence of the
advisory/read-only wiring and the typed tests that protect it.
"""

from __future__ import annotations

import pathlib


def test_governance_agent_exports_context_layer_package_summary() -> None:
    from iriai_build_v2.execution_control.governance_agent import (
        ContextLayerPackageSummary,
    )
    from iriai_build_v2.execution_control import governance_agent as mod

    assert mod.ContextLayerPackageSummary is ContextLayerPackageSummary
    assert "ContextLayerPackageSummary" in mod.__all__


def test_governance_agent_context_carries_context_package_field() -> None:
    from iriai_build_v2.execution_control.governance_agent import (
        ContextLayerPackageSummary,
        GovernanceAgentContext,
    )

    fields = GovernanceAgentContext.model_fields
    assert "context_package" in fields
    assert ContextLayerPackageSummary in fields["context_package"].annotation.__args__
    assert type(None) in fields["context_package"].annotation.__args__


def test_builder_inputs_accept_context_package_field() -> None:
    from iriai_build_v2.execution_control.governance_agent import (
        ContextLayerPackageSummary,
    )
    from iriai_build_v2.execution_control.governance_agent_context_builder import (
        AgentContextBuilderInputs,
    )

    fields = AgentContextBuilderInputs.model_fields
    assert "context_package" in fields
    assert ContextLayerPackageSummary in fields["context_package"].annotation.__args__
    assert type(None) in fields["context_package"].annotation.__args__


def test_builder_docs_mark_slice_21_wired_not_deferred() -> None:
    from iriai_build_v2.execution_control import (
        governance_agent_context_builder as mod,
    )
    from iriai_build_v2.execution_control.governance_agent_context_builder import (
        GovernanceAgentContextBuilder,
    )

    assert mod.__doc__ is not None
    assert GovernanceAgentContextBuilder.__doc__ is not None
    combined = f"{mod.__doc__}\n{GovernanceAgentContextBuilder.__doc__}"
    assert "Slice 21" in combined
    assert "ContextLayerPackageSummary" in combined
    assert "WIRED" in combined
    assert "DEFERRED" not in combined


def test_typed_context_package_carry_through_tests_exist() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    test_path = (
        repo_root
        / "tests"
        / "test_execution_control_governance_agent_context_builder.py"
    )
    text = test_path.read_text(encoding="utf-8")

    expected_tests = (
        "test_context_carries_context_package_summary_when_provided",
        "test_context_projects_context_layer_package_to_summary",
        "test_line_provenance_requires_context_package_summary",
        "test_line_scope_without_matching_provenance_requires_context_package_summary",
        "test_context_package_defaults_to_none_without_slice_21_input",
        "test_context_package_completeness_and_refs_are_preserved_not_promoted",
    )
    for test_name in expected_tests:
        assert f"def {test_name}(" in text
