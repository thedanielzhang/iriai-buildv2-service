"""Canonical Slice 13 governance evidence global-gate shard."""

from __future__ import annotations

from tests._canonical_governance_shards import export_canonical_shard_tests


export_canonical_shard_tests(
    globals(),
    (
        "test_governance_evidence_models",
        "test_governance_evidence_store",
        "test_governance_postgres_evidence_store",
        "test_governance_evidence_ingestor",
        "test_governance_evidence_set_digester",
        "test_governance_journal_parser",
        "test_governance_decision_log_parser",
        "test_governance_acceptance_criteria",
    ),
)
