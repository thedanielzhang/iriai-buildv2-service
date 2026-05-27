"""Canonical Slice 19 governance agent global-gate shard."""

from __future__ import annotations

from tests._canonical_governance_shards import export_canonical_shard_tests


export_canonical_shard_tests(
    globals(),
    (
        "test_execution_control_governance_agent",
        "test_execution_control_governance_snapshot_api",
        "test_execution_control_governance_dashboard_view",
        "test_execution_control_governance_slack_renderer",
        "test_execution_control_governance_agent_context_builder",
        "test_execution_control_governance_report_artifact",
        "test_execution_control_governance_19_activation_boundary",
        "test_execution_control_governance_19_slice21_deferral_sentinel",
        "test_execution_control_governance_cli",
    ),
)
