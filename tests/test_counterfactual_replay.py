"""Canonical Slice 18 counterfactual replay global-gate shard."""

from __future__ import annotations

from tests._canonical_governance_shards import export_canonical_shard_tests


export_canonical_shard_tests(
    globals(),
    (
        "test_execution_control_counterfactual_replay",
        "test_execution_control_counterfactual_replay_loader",
        "test_execution_control_counterfactual_summary_replay",
        "test_execution_control_counterfactual_event_replay",
        "test_execution_control_counterfactual_metrics_comparator",
        "test_execution_control_counterfactual_result_writer",
        "test_execution_control_recommendation_citation_hook",
    ),
)
