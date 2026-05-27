"""Canonical Slice 17 policy recommendation global-gate shard."""

from __future__ import annotations

from tests._canonical_governance_shards import export_canonical_shard_tests


export_canonical_shard_tests(
    globals(),
    (
        "test_execution_control_policy_recommendation",
        "test_execution_control_recommendation_builder",
        "test_execution_control_policy_validation_interface",
        "test_execution_control_decision_record_writer",
        "test_execution_control_replay_requirement_hook",
        "test_execution_control_consumer_read_api",
    ),
)
