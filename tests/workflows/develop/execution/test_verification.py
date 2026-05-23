from __future__ import annotations

import pytest

from iriai_build_v2.models.outputs import Issue, Verdict
from iriai_build_v2.workflows.develop.execution.verification import (
    EvidenceNode,
    GraphApprovalError,
    ReadBudgetReport,
    VerifierCompatibilityLinks,
    VerificationGraphAttempt,
    build_aggregate_verdict,
    build_graph_approval_proof,
    map_verifier_failure,
    normalize_raw_verifier_result,
)


FEATURE_ID = "feature-slice-06"
DAG_SHA = "dag-sha-06"


def _node(
    node_id: int,
    kind: str,
    name: str,
    *,
    status: str = "approved",
    failure_id: int | None = None,
    verdict_id: int | None = None,
) -> EvidenceNode:
    return EvidenceNode(
        id=node_id,
        feature_id=FEATURE_ID,
        group_idx=2,
        stage="verify",
        kind=kind,
        name=name,
        idempotency_key=f"node:{node_id}:{name}",
        status=status,
        deterministic=kind not in {"raw_verifier", "expanded_lens"},
        input_hash=f"input:{node_id}",
        failure_id=failure_id,
        verdict_id=verdict_id,
    )


def _verdict(
    *,
    approved: bool = True,
    summary: str = "ok",
    concerns: list[Issue] | None = None,
) -> Verdict:
    return Verdict(
        approved=approved,
        summary=summary,
        concerns=concerns or [],
    )


def _attempt() -> VerificationGraphAttempt:
    return VerificationGraphAttempt(
        feature_id=FEATURE_ID,
        dag_sha256=DAG_SHA,
        group_idx=2,
        stage="verify",
        attempt=1,
    )


def _upcoming_node_id(attempt: VerificationGraphAttempt) -> int:
    return max((node.id for node in attempt.nodes), default=0) + 1


def _compatibility(node_id: int, context_node_id: int) -> VerifierCompatibilityLinks:
    return VerifierCompatibilityLinks(
        raw_output_verifier_node_id=node_id,
        parsed_verdict_verifier_node_id=node_id,
        projection_verifier_node_id=node_id,
        context_package_node_id=context_node_id,
        context_hash_matches=True,
    )


def test_lens_nodes_sort_by_slug_for_deterministic_aggregation() -> None:
    attempt = _attempt()
    gate = attempt.upsert_node(
        kind="deterministic_gate",
        name="workspace_snapshot_freshness",
        input_payload={"snapshot_ids": [1]},
        status="approved",
        deterministic=True,
    )
    context = attempt.upsert_node(
        kind="context_package",
        name="bounded_context_package",
        input_payload={"refs": [1]},
        status="approved",
        deterministic=True,
    )
    raw = attempt.record_raw_verifier(
        context_node=context,
        verdict=_verdict(summary="raw approved"),
        verdict_id=100,
        read_budget=ReadBudgetReport(),
        compatibility=_compatibility(_upcoming_node_id(attempt), context.id),
    )
    lens_z = attempt.record_lens_verifier(
        lens_slug="z-runtime",
        context_node=context,
        raw_outcome=raw,
        verdict=_verdict(
            summary="runtime approved",
            concerns=[Issue(severity="minor", description="z finding", file="app.py")],
        ),
        verdict_id=102,
        read_budget=ReadBudgetReport(),
        compatibility=_compatibility(_upcoming_node_id(attempt), context.id),
    )
    lens_a = attempt.record_lens_verifier(
        lens_slug="a-acceptance",
        context_node=context,
        raw_outcome=raw,
        verdict=_verdict(
            summary="acceptance approved",
            concerns=[Issue(severity="minor", description="a finding", file="app.py")],
        ),
        verdict_id=101,
        read_budget=ReadBudgetReport(),
        compatibility=_compatibility(_upcoming_node_id(attempt), context.id),
    )

    aggregate = attempt.aggregate(
        required_gate_nodes=[gate],
        raw_outcome=raw,
        lens_outcomes=[lens_z, lens_a],
        required_lens_slugs=["z-runtime", "a-acceptance"],
        merged_verdict_id=200,
        projection_keys=["dag-verify:g2:verify"],
    )

    assert aggregate.aggregate.approved is True
    assert aggregate.aggregate.required_lens_node_ids == [lens_a.node.id, lens_z.node.id]
    assert [c.sources for c in aggregate.merged_concerns] == [
        ["lens:a-acceptance"],
        ["lens:z-runtime"],
    ]


def test_aggregate_merges_duplicate_concerns_by_normalized_key() -> None:
    attempt = _attempt()
    gate = attempt.upsert_node(
        kind="deterministic_gate",
        name="contract_closure",
        input_payload={"contract_ids": [1]},
        status="approved",
        deterministic=True,
    )
    context = attempt.upsert_node(
        kind="context_package",
        name="bounded_context_package",
        input_payload={"refs": [1]},
        status="approved",
        deterministic=True,
    )
    raw = attempt.record_raw_verifier(
        context_node=context,
        verdict=_verdict(
            approved=False,
            summary="raw rejected",
            concerns=[
                Issue(
                    severity="minor",
                    description="  Missing generated summary file ",
                    file="reports/out.json",
                )
            ],
        ),
        verdict_id=100,
        read_budget=ReadBudgetReport(),
        compatibility=_compatibility(_upcoming_node_id(attempt), context.id),
    )
    lens = attempt.record_lens_verifier(
        lens_slug="acceptance",
        context_node=context,
        raw_outcome=raw,
        verdict=_verdict(
            approved=False,
            summary="lens rejected",
            concerns=[
                Issue(
                    severity="blocker",
                    description="Missing generated   summary file",
                    file="reports/out.json",
                )
            ],
        ),
        verdict_id=101,
        read_budget=ReadBudgetReport(),
        compatibility=_compatibility(_upcoming_node_id(attempt), context.id),
    )

    aggregate = attempt.aggregate(
        required_gate_nodes=[gate],
        raw_outcome=raw,
        lens_outcomes=[lens],
        required_lens_slugs=["acceptance"],
        merged_verdict_id=200,
    )

    assert aggregate.aggregate.approved is False
    assert len(aggregate.merged_concerns) == 1
    merged = aggregate.merged_concerns[0]
    assert merged.concern.severity == "blocker"
    assert merged.sources == ["lens:acceptance", "raw"]
    assert merged.node_ids == [raw.node.id, lens.node.id]


def test_aggregate_approves_only_when_required_gate_raw_and_lenses_approve() -> None:
    attempt = _attempt()
    gate = attempt.upsert_node(
        kind="deterministic_gate",
        name="patch_integrity",
        input_payload={"patch_ids": [10]},
        status="approved",
        deterministic=True,
    )
    context = attempt.upsert_node(
        kind="context_package",
        name="bounded_context_package",
        input_payload={"refs": [1]},
        status="approved",
        deterministic=True,
    )
    raw = attempt.record_raw_verifier(
        context_node=context,
        verdict=_verdict(summary="raw approved"),
        verdict_id=100,
        read_budget=ReadBudgetReport(),
        compatibility=_compatibility(_upcoming_node_id(attempt), context.id),
    )
    lens = attempt.record_lens_verifier(
        lens_slug="security",
        context_node=context,
        raw_outcome=raw,
        verdict=_verdict(summary="security approved"),
        verdict_id=101,
        read_budget=ReadBudgetReport(),
        compatibility=_compatibility(_upcoming_node_id(attempt), context.id),
    )

    approved = attempt.aggregate(
        required_gate_nodes=[gate],
        raw_outcome=raw,
        lens_outcomes=[lens],
        required_lens_slugs=["security"],
        merged_verdict_id=200,
    )

    assert approved.aggregate.approved is True

    failed_gate = gate.model_copy(update={"status": "rejected", "failure_id": 55})
    rejected = build_aggregate_verdict(
        aggregate_node=_node(99, "aggregate_verdict", "aggregate_verdict"),
        required_gate_nodes=[failed_gate],
        raw_outcome=raw,
        lens_outcomes=[lens],
        required_lens_slugs=["security"],
        merged_verdict_id=201,
    )

    assert rejected.aggregate.approved is False
    assert rejected.aggregate.blocking_failure_class == "deterministic_gate"
    assert rejected.aggregate.failure_ids == [55]


@pytest.mark.parametrize("gate_status", ["pending", "running"])
def test_required_pending_or_running_gate_rejects_aggregate(gate_status: str) -> None:
    raw_node = _node(20, "raw_verifier", "raw_verifier")
    raw = normalize_raw_verifier_result(
        raw_node,
        verdict=_verdict(summary="raw approved"),
        verdict_id=100,
        prompt_context_node_id=10,
        read_budget=ReadBudgetReport(),
        compatibility=_compatibility(raw_node.id, 10),
    )

    aggregate = build_aggregate_verdict(
        aggregate_node=_node(30, "aggregate_verdict", "aggregate_verdict"),
        required_gate_nodes=[
            _node(1, "deterministic_gate", "required_gate", status=gate_status)
        ],
        raw_outcome=raw,
        lens_outcomes=[],
        required_lens_slugs=[],
        merged_verdict_id=200,
    )

    assert aggregate.aggregate.approved is False
    assert aggregate.node.status == "rejected"
    assert aggregate.aggregate.blocking_failure_class == "deterministic_gate"
    assert aggregate.aggregate.failure_ids == []


def test_all_required_gate_nodes_approved_allows_aggregate_approval() -> None:
    raw_node = _node(20, "raw_verifier", "raw_verifier")
    raw = normalize_raw_verifier_result(
        raw_node,
        verdict=_verdict(summary="raw approved"),
        verdict_id=100,
        prompt_context_node_id=10,
        read_budget=ReadBudgetReport(),
        compatibility=_compatibility(raw_node.id, 10),
    )

    aggregate = build_aggregate_verdict(
        aggregate_node=_node(30, "aggregate_verdict", "aggregate_verdict"),
        required_gate_nodes=[
            _node(1, "deterministic_gate", "workspace_snapshot_freshness"),
            _node(2, "deterministic_gate", "contract_closure"),
        ],
        raw_outcome=raw,
        lens_outcomes=[],
        required_lens_slugs=[],
        merged_verdict_id=200,
    )

    assert aggregate.aggregate.approved is True
    assert aggregate.node.status == "approved"
    assert aggregate.aggregate.blocking_failure_class is None
    assert aggregate.aggregate.required_gate_node_ids == [1, 2]


def test_raw_verifier_approval_without_required_lenses_is_rejected() -> None:
    attempt = _attempt()
    gate = attempt.upsert_node(
        kind="deterministic_gate",
        name="artifact_freshness",
        input_payload={"artifact_ids": [1]},
        status="approved",
        deterministic=True,
    )
    context = attempt.upsert_node(
        kind="context_package",
        name="bounded_context_package",
        input_payload={"refs": [1]},
        status="approved",
        deterministic=True,
    )
    raw = attempt.record_raw_verifier(
        context_node=context,
        verdict=_verdict(summary="raw approved"),
        verdict_id=100,
        read_budget=ReadBudgetReport(),
        compatibility=_compatibility(_upcoming_node_id(attempt), context.id),
    )

    aggregate = attempt.aggregate(
        required_gate_nodes=[gate],
        raw_outcome=raw,
        lens_outcomes=[],
        required_lens_slugs=["acceptance"],
        merged_verdict_id=200,
    )

    assert aggregate.aggregate.approved is False
    assert aggregate.aggregate.blocking_failure_class == "verifier_context"
    assert aggregate.aggregate.raw_verdict_node_id == raw.node.id
    assert aggregate.aggregate.required_lens_node_ids == []


def test_legacy_dag_verify_projection_without_aggregate_cannot_build_proof() -> None:
    with pytest.raises(GraphApprovalError, match="raw compatibility projection alone"):
        build_graph_approval_proof(
            None,
            required_node_statuses={},
            raw_compat_projection_key="dag-verify:g2:verify",
        )


def test_provider_and_parse_failures_map_to_blocking_verifier_classes() -> None:
    raw_timeout = map_verifier_failure("raw", "provider", reason="timeout", failure_id=7)
    lens_parse = map_verifier_failure("lens", "parse", failure_id=8)
    context_failure = map_verifier_failure("lens", "context", failure_id=9)

    assert raw_timeout.failure_class == "verifier_provider"
    assert raw_timeout.failure_type == "verifier_provider_timeout"
    assert raw_timeout.blocking_failure_class == "verifier_provider"
    assert lens_parse.failure_class == "verifier_provider"
    assert lens_parse.failure_type == "verifier_parse_failed"
    assert context_failure.failure_class == "verifier_context"
    assert context_failure.failure_type == "context_materialization_failed"


def test_raw_verifier_projection_must_cite_same_node() -> None:
    raw_node = _node(20, "raw_verifier", "raw_verifier")
    raw = normalize_raw_verifier_result(
        raw_node,
        verdict=_verdict(summary="raw approved"),
        verdict_id=100,
        prompt_context_node_id=10,
        read_budget=ReadBudgetReport(),
        compatibility=VerifierCompatibilityLinks(
            raw_output_verifier_node_id=20,
            parsed_verdict_verifier_node_id=20,
            projection_verifier_node_id=999,
            context_package_node_id=10,
            context_hash_matches=True,
        ),
    )

    aggregate = build_aggregate_verdict(
        aggregate_node=_node(30, "aggregate_verdict", "aggregate_verdict"),
        required_gate_nodes=[_node(1, "deterministic_gate", "gate")],
        raw_outcome=raw,
        lens_outcomes=[],
        required_lens_slugs=[],
        merged_verdict_id=200,
    )

    assert raw.result.approved is False
    assert raw.compatibility_conflicts == ["projection_verifier_node_id"]
    assert aggregate.aggregate.approved is False
    assert aggregate.aggregate.blocking_failure_class == "aggregate.conflict"


def test_raw_verifier_missing_compatibility_links_do_not_default_to_current_node() -> None:
    raw_node = _node(20, "raw_verifier", "raw_verifier")
    raw = normalize_raw_verifier_result(
        raw_node,
        verdict=_verdict(summary="raw approved"),
        verdict_id=100,
        prompt_context_node_id=10,
        read_budget=ReadBudgetReport(),
    )

    assert raw.result.approved is False
    assert raw.compatibility_conflicts == [
        "raw_output_verifier_node_id",
        "parsed_verdict_verifier_node_id",
        "projection_verifier_node_id",
        "context_package_node_id",
    ]
    assert raw.node.metadata["verifier_compatibility_links"] == {
        "raw_output_verifier_node_id": None,
        "parsed_verdict_verifier_node_id": None,
        "projection_verifier_node_id": None,
        "context_package_node_id": None,
        "context_hash_matches": True,
    }


def test_product_looking_raw_rejection_with_stale_context_routes_to_conflict() -> None:
    attempt = _attempt()
    gate = attempt.upsert_node(
        kind="deterministic_gate",
        name="workspace_snapshot_freshness",
        input_payload={"snapshot_ids": [1]},
        status="approved",
        deterministic=True,
    )
    context = attempt.upsert_node(
        kind="context_package",
        name="bounded_context_package",
        input_payload={"refs": [1]},
        status="approved",
        deterministic=True,
    )
    raw_node_id = _upcoming_node_id(attempt)
    raw = attempt.record_raw_verifier(
        context_node=context,
        verdict=_verdict(
            approved=False,
            summary="raw rejected",
            concerns=[
                Issue(
                    severity="major",
                    description="Product behavior still fails acceptance",
                    file="app.py",
                )
            ],
        ),
        verdict_id=100,
        read_budget=ReadBudgetReport(),
        compatibility=VerifierCompatibilityLinks(
            raw_output_verifier_node_id=raw_node_id,
            parsed_verdict_verifier_node_id=raw_node_id,
            projection_verifier_node_id=raw_node_id,
            context_package_node_id=context.id + 999,
            context_hash_matches=False,
        ),
    )
    aggregate = attempt.aggregate(
        required_gate_nodes=[gate],
        raw_outcome=raw,
        lens_outcomes=[],
        required_lens_slugs=[],
        merged_verdict_id=200,
    )

    assert raw.result.approved is False
    assert raw.compatibility_conflicts == ["context_package_node_id", "context_hash"]
    assert raw.typed_failure is not None
    assert raw.typed_failure.failure_class == "evidence_corruption"
    assert raw.typed_failure.failure_type == "projection_body_conflict"
    assert raw.typed_failure.route == "quiesce"
    assert raw.node.metadata["blocking_failure_class"] == "aggregate.conflict"
    assert raw.node.metadata["failure_class"] == "evidence_corruption"
    assert raw.node.metadata["route"] == "quiesce"
    assert aggregate.aggregate.approved is False
    assert aggregate.aggregate.blocking_failure_class == "aggregate.conflict"


def test_product_looking_raw_rejection_with_unbound_context_avoids_product_repair() -> None:
    attempt = _attempt()
    gate = attempt.upsert_node(
        kind="deterministic_gate",
        name="workspace_snapshot_freshness",
        input_payload={"snapshot_ids": [1]},
        status="approved",
        deterministic=True,
    )
    context = attempt.upsert_node(
        kind="context_package",
        name="bounded_context_package",
        input_payload={"refs": [1]},
        status="approved",
        deterministic=True,
    )
    raw = attempt.record_raw_verifier(
        context_node=context,
        verdict=_verdict(
            approved=False,
            summary="raw rejected",
            concerns=[
                Issue(
                    severity="blocker",
                    description="Verifier says the product is still broken",
                    file="app.py",
                )
            ],
        ),
        verdict_id=100,
        read_budget=ReadBudgetReport(),
    )
    aggregate = attempt.aggregate(
        required_gate_nodes=[gate],
        raw_outcome=raw,
        lens_outcomes=[],
        required_lens_slugs=[],
        merged_verdict_id=200,
    )

    assert raw.result.approved is False
    assert raw.compatibility_conflicts == [
        "raw_output_verifier_node_id",
        "parsed_verdict_verifier_node_id",
        "projection_verifier_node_id",
        "context_package_node_id",
    ]
    assert raw.typed_failure is not None
    assert raw.typed_failure.failure_class == "evidence_corruption"
    assert raw.typed_failure.failure_type == "projection_body_conflict"
    assert raw.typed_failure.route == "quiesce"
    assert raw.node.metadata["failure_class"] != "product_defect"
    assert raw.node.metadata["route"] != "run_product_repair"
    assert aggregate.aggregate.approved is False
    assert aggregate.aggregate.blocking_failure_class == "aggregate.conflict"


def test_raw_verifier_approval_requires_parseable_approved_verdict() -> None:
    attempt = _attempt()
    gate = attempt.upsert_node(
        kind="deterministic_gate",
        name="raw_gate_approval_requirements",
        input_payload={"requires": ["parseable_verdict"]},
        status="approved",
        deterministic=True,
    )
    context = attempt.upsert_node(
        kind="context_package",
        name="bounded_context_package",
        input_payload={"refs": [1]},
        status="approved",
        deterministic=True,
    )

    unparseable_raw = attempt.record_raw_verifier(
        context_node=context,
        verdict=None,
        verdict_id=100,
        read_budget=ReadBudgetReport(),
        failure_source="parse",
        runtime_failure_reason="parse_failed",
    )
    unparseable_aggregate = attempt.aggregate(
        required_gate_nodes=[gate],
        raw_outcome=unparseable_raw,
        lens_outcomes=[],
        required_lens_slugs=[],
        merged_verdict_id=200,
    )

    assert unparseable_raw.result.approved is False
    assert unparseable_raw.node.status == "failed"
    assert unparseable_raw.typed_failure is not None
    assert unparseable_raw.typed_failure.failure_type == "verifier_parse_failed"
    assert unparseable_aggregate.aggregate.approved is False
    assert unparseable_aggregate.aggregate.blocking_failure_class == "verifier_provider"

    retry = attempt.clone_for_replay()
    retry_gate = retry.upsert_node(
        kind="deterministic_gate",
        name="raw_gate_approval_requirements",
        input_payload={"requires": ["parseable_verdict"]},
        status="approved",
        deterministic=True,
    )
    retry_context = retry.upsert_node(
        kind="context_package",
        name="bounded_context_package",
        input_payload={"refs": [1]},
        status="approved",
        deterministic=True,
    )
    retry_raw_node_id = max(node.id for node in retry.nodes) + 1
    missing_parsed_verdict = retry.record_raw_verifier(
        context_node=retry_context,
        verdict=_verdict(summary="raw approved"),
        verdict_id=101,
        read_budget=ReadBudgetReport(),
        compatibility=VerifierCompatibilityLinks(
            raw_output_verifier_node_id=retry_raw_node_id,
            parsed_verdict_verifier_node_id=None,
            projection_verifier_node_id=retry_raw_node_id,
            context_package_node_id=retry_context.id,
            context_hash_matches=True,
        ),
    )
    missing_parsed_aggregate = retry.aggregate(
        required_gate_nodes=[retry_gate],
        raw_outcome=missing_parsed_verdict,
        lens_outcomes=[],
        required_lens_slugs=[],
        merged_verdict_id=201,
    )

    assert retry_gate.id == gate.id
    assert retry_context.id == context.id
    assert missing_parsed_verdict.result.approved is False
    assert missing_parsed_verdict.compatibility_conflicts == [
        "parsed_verdict_verifier_node_id"
    ]
    assert missing_parsed_aggregate.aggregate.approved is False
    assert missing_parsed_aggregate.aggregate.blocking_failure_class == "aggregate.conflict"


def test_crash_replay_reuses_existing_nodes_and_finishes_missing_lenses_once() -> None:
    attempt = _attempt()
    gate = attempt.upsert_node(
        kind="deterministic_gate",
        name="workspace_snapshot_freshness",
        input_payload={"snapshot_ids": [1]},
        status="approved",
        deterministic=True,
    )
    context = attempt.upsert_node(
        kind="context_package",
        name="bounded_context_package",
        input_payload={"refs": [1]},
        status="approved",
        deterministic=True,
    )
    raw = attempt.record_raw_verifier(
        context_node=context,
        verdict=_verdict(summary="raw approved"),
        verdict_id=100,
        read_budget=ReadBudgetReport(),
        compatibility=_compatibility(_upcoming_node_id(attempt), context.id),
    )

    replay = attempt.clone_for_replay()
    replay_gate = replay.upsert_node(
        kind="deterministic_gate",
        name="workspace_snapshot_freshness",
        input_payload={"snapshot_ids": [1]},
        status="approved",
        deterministic=True,
    )
    replay_context = replay.upsert_node(
        kind="context_package",
        name="bounded_context_package",
        input_payload={"refs": [1]},
        status="approved",
        deterministic=True,
    )
    replay_raw = replay.record_raw_verifier(
        context_node=replay_context,
        verdict=_verdict(summary="raw approved"),
        verdict_id=100,
        read_budget=ReadBudgetReport(),
        compatibility=_compatibility(raw.node.id, replay_context.id),
    )
    lens = replay.record_lens_verifier(
        lens_slug="acceptance",
        context_node=replay_context,
        raw_outcome=replay_raw,
        verdict=_verdict(summary="acceptance approved"),
        verdict_id=101,
        read_budget=ReadBudgetReport(),
        compatibility=_compatibility(_upcoming_node_id(replay), replay_context.id),
    )
    first_aggregate = replay.aggregate(
        required_gate_nodes=[replay_gate],
        raw_outcome=replay_raw,
        lens_outcomes=[lens],
        required_lens_slugs=["acceptance"],
        merged_verdict_id=200,
    )
    second_aggregate = replay.aggregate(
        required_gate_nodes=[replay_gate],
        raw_outcome=replay_raw,
        lens_outcomes=[lens],
        required_lens_slugs=["acceptance"],
        merged_verdict_id=200,
    )
    proof = build_graph_approval_proof(
        first_aggregate,
        required_node_statuses=replay.node_statuses(),
    )

    assert replay_gate.id == gate.id
    assert replay_context.id == context.id
    assert replay_raw.node.id == raw.node.id
    assert first_aggregate.node.id == second_aggregate.node.id
    assert [node.name for node in replay.nodes].count("raw_verifier") == 1
    assert [node.name for node in replay.nodes].count("expanded_lens:acceptance") == 1
    assert [node.name for node in replay.nodes].count("aggregate_verdict") == 1
    assert proof.aggregate_node_id == first_aggregate.node.id
    assert proof.raw_verifier_node_id == raw.node.id
    assert proof.feature_id == FEATURE_ID
    assert proof.dag_sha256 == DAG_SHA
    assert proof.group_idx == 2
    assert proof.stage == "verify"
    assert proof.verifier_compatibility_links[str(raw.node.id)]["projection_verifier_node_id"] == raw.node.id


def test_retry_after_provider_failure_reuses_deterministic_nodes() -> None:
    attempt = _attempt()
    gate = attempt.upsert_node(
        kind="deterministic_gate",
        name="workspace_snapshot_freshness",
        input_payload={"snapshot_ids": [1]},
        status="approved",
        deterministic=True,
    )
    context = attempt.upsert_node(
        kind="context_package",
        name="bounded_context_package",
        input_payload={"refs": [1]},
        status="approved",
        deterministic=True,
    )
    failed_raw = attempt.record_raw_verifier(
        context_node=context,
        verdict=None,
        verdict_id=100,
        read_budget=ReadBudgetReport(),
        failure_source="provider",
        runtime_failure_reason="crash",
    )
    failed_aggregate = attempt.aggregate(
        required_gate_nodes=[gate],
        raw_outcome=failed_raw,
        lens_outcomes=[],
        required_lens_slugs=[],
        merged_verdict_id=200,
    )

    retry = attempt.clone_for_replay()
    retry_gate = retry.upsert_node(
        kind="deterministic_gate",
        name="workspace_snapshot_freshness",
        input_payload={"snapshot_ids": [1]},
        status="approved",
        deterministic=True,
    )
    retry_context = retry.upsert_node(
        kind="context_package",
        name="bounded_context_package",
        input_payload={"refs": [1]},
        status="approved",
        deterministic=True,
    )
    retry_raw = retry.record_raw_verifier(
        context_node=retry_context,
        verdict=_verdict(summary="raw approved after retry"),
        verdict_id=101,
        read_budget=ReadBudgetReport(),
        compatibility=_compatibility(_upcoming_node_id(retry), retry_context.id),
    )
    retry_aggregate = retry.aggregate(
        required_gate_nodes=[retry_gate],
        raw_outcome=retry_raw,
        lens_outcomes=[],
        required_lens_slugs=[],
        merged_verdict_id=201,
    )
    replayed_raw = retry.record_raw_verifier(
        context_node=retry_context,
        verdict=_verdict(summary="raw approved after retry"),
        verdict_id=101,
        read_budget=ReadBudgetReport(),
        compatibility=_compatibility(retry_raw.node.id, retry_context.id),
    )
    replayed_aggregate = retry.aggregate(
        required_gate_nodes=[retry_gate],
        raw_outcome=retry_raw,
        lens_outcomes=[],
        required_lens_slugs=[],
        merged_verdict_id=201,
    )
    supersedes_edges = [
        edge for edge in retry.edges if edge.kind == "supersedes"
    ]

    assert failed_raw.node.status == "failed"
    assert failed_raw.typed_failure is not None
    assert failed_raw.typed_failure.failure_type == "verifier_provider_crash"
    assert failed_raw.node.metadata["route"] == "retry_verifier"
    assert failed_aggregate.node.status == "rejected"
    assert failed_aggregate.aggregate.blocking_failure_class == "verifier_provider"
    assert retry_gate.id == gate.id
    assert retry_context.id == context.id
    assert retry_raw.node.id != failed_raw.node.id
    assert retry_aggregate.node.id != failed_aggregate.node.id
    assert retry_raw.node.status == "approved"
    assert retry_aggregate.node.status == "approved"
    assert retry_raw.node.metadata["supersedes_node_id"] == failed_raw.node.id
    assert retry_aggregate.node.metadata["supersedes_node_id"] == failed_aggregate.node.id
    assert replayed_raw.node.id == retry_raw.node.id
    assert replayed_aggregate.node.id == retry_aggregate.node.id
    assert [node.name for node in retry.nodes].count("workspace_snapshot_freshness") == 1
    assert [node.name for node in retry.nodes].count("bounded_context_package") == 1
    assert [node.name for node in retry.nodes].count("raw_verifier") == 2
    assert [node.name for node in retry.nodes].count("aggregate_verdict") == 2
    assert {
        (edge.from_node_id, edge.to_node_id, edge.required)
        for edge in supersedes_edges
    } == {
        (failed_raw.node.id, retry_raw.node.id, False),
        (failed_aggregate.node.id, retry_aggregate.node.id, False),
    }


def test_graph_approval_proof_digest_is_bound_to_graph_identity() -> None:
    attempt = _attempt()
    gate = attempt.upsert_node(
        kind="deterministic_gate",
        name="workspace_snapshot_freshness",
        input_payload={"snapshot_ids": [1]},
        status="approved",
        deterministic=True,
    )
    context = attempt.upsert_node(
        kind="context_package",
        name="bounded_context_package",
        input_payload={"refs": [1]},
        status="approved",
        deterministic=True,
    )
    raw = attempt.record_raw_verifier(
        context_node=context,
        verdict=_verdict(summary="raw approved"),
        verdict_id=100,
        read_budget=ReadBudgetReport(),
        compatibility=_compatibility(_upcoming_node_id(attempt), context.id),
    )
    aggregate = attempt.aggregate(
        required_gate_nodes=[gate],
        raw_outcome=raw,
        lens_outcomes=[],
        required_lens_slugs=[],
        merged_verdict_id=200,
        projection_keys=["dag-verify:g2:verify"],
    )

    proof = build_graph_approval_proof(
        aggregate,
        required_node_statuses=attempt.node_statuses(),
    )
    transplanted = build_graph_approval_proof(
        aggregate,
        required_node_statuses=attempt.node_statuses(),
        feature_id="feature-other",
    )
    changed_payload = build_graph_approval_proof(
        aggregate,
        required_node_statuses=attempt.node_statuses(),
        graph_payload_digest="payload-digest",
    )

    assert transplanted.proof_digest != proof.proof_digest
    assert changed_payload.proof_digest != proof.proof_digest
