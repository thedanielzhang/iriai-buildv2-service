"""Canonical Slice 16 finding engine global-gate shard."""

from __future__ import annotations

from tests._canonical_governance_shards import export_canonical_shard_tests


export_canonical_shard_tests(
    globals(),
    (
        "test_execution_control_governance_finding_writer",
        "test_execution_control_finding_reviewer_test_failure_engine",
        "test_execution_control_finding_plan_deviation_engine",
        "test_execution_control_finding_rule_engine",
        "test_execution_control_finding_engine",
    ),
)
