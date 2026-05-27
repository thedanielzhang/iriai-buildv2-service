"""Canonical Slice 13A lossless context global-gate shard."""

from __future__ import annotations

from tests._canonical_governance_shards import export_canonical_shard_tests


export_canonical_shard_tests(
    globals(),
    (
        "test_governance_13a_acceptance_artifact",
        "test_governance_13a_step9_reconciliation",
        "test_governance_completeness_scanner",
        "test_execution_control_completeness",
        "test_execution_control_p3_13a_6_3_binding_closure",
    ),
)
