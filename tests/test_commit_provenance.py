"""Canonical Slice 14 commit provenance global-gate shard."""

from __future__ import annotations

from tests._canonical_governance_shards import export_canonical_shard_tests


export_canonical_shard_tests(
    globals(),
    (
        "test_execution_control_commit_provenance",
        "test_execution_control_commit_provenance_lineage",
        "test_execution_control_commit_provenance_reader",
        "test_execution_control_commit_provenance_writer",
    ),
)
