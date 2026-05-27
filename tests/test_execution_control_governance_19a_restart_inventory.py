from __future__ import annotations

import re
from pathlib import Path

from iriai_build_v2.workflows.develop.execution.failure_router import ROUTE_TABLE


REPO_ROOT = Path(__file__).resolve().parents[1]
DOC_19A = REPO_ROOT / "docs" / "execution-control-plane" / (
    "19a-governance-implementation-reassessment.md"
)
DOC_13A_ACCEPTANCE = REPO_ROOT / "docs" / "execution-control-plane" / (
    "13a-acceptance.md"
)


EXPECTED_GOVERNANCE_PROJECTION_IDS_BY_SLICE: dict[str, tuple[str, ...]] = {
    "14": (
        "line_provenance_gap",
        "governance_evidence_conflict",
    ),
    "15": (
        "governance_metric_extraction_failed",
        "governance_scorecard_persistence_failed",
    ),
    "16": (
        "finding_rule_emission_failed",
        "finding_plan_deviation_parse_failed",
        "finding_reviewer_test_failure_parse_failed",
        "governance_finding_persistence_failed",
    ),
    "17": (
        "recommendation_builder_emission_failed",
        "policy_validation_failed",
        "decision_record_persistence_failed",
        "replay_requirement_validation_failed",
        "consumer_read_api_failed",
    ),
    "18": (
        "replay_corpus_or_scenario_load_failed",
        "summary_replay_failed",
        "event_replay_failed",
        "metrics_comparator_failed",
        "counterfactual_result_persistence_failed",
        "recommendation_citation_validation_failed",
    ),
    "19": (
        "governance_snapshot_api_failed",
        "governance_dashboard_view_failed",
        "governance_slack_renderer_failed",
        "governance_agent_context_builder_failed",
        "governance_report_artifact_emission_failed",
    ),
}


EXPECTED_13A_FAIL_CLOSED_ROUTES: tuple[tuple[str, str], ...] = (
    ("runtime_context", "context_incomplete"),
    ("verifier_context", "companion_record_unavailable"),
    ("verifier_context", "proof_row_required"),
    ("evidence_corruption", "list_field_incomplete"),
    ("evidence_corruption", "classifier_rule_blocked"),
)


EXPECTED_LEDGER_IDS: tuple[str, ...] = (
    "19A-P3-001",
    "19A-P3-002",
    "19A-P3-003",
    "19A-P3-004",
    "19A-P3-005",
    "19A-P3-006",
    "19A-P3-007",
    "P3-13e-3",
    "P3-13-V1-2",
    "P3-13g-R1",
    "P3-13c-2",
    "P3-13c-3",
    "P3-13d-2",
    "P3-13d-R1",
    "P3-13e-1",
    "P3-13e-2",
    "P3-13e-4",
    "P3-A5-coverage-gap",
    "P3-13f-2",
    "P3-13h-1",
    "P3-13h-2",
    "P3-13i-1",
    "P3-13j-1",
    "P3-13k-1",
    "P3-13k-2",
    "P3-12c-1",
    "Deferred-12a-2",
    "Deferred-12a-3",
    "Slice-09-maintenance-carries",
    "Slice-10-maintenance-carries",
    "Slice-11-maintenance-carries",
    "P3-13A-1",
    "P3-13A-V2-1",
    "P3-13A-V3-1",
    "P3-13A-V5-1",
    "P3-13A-V5-2",
    "P3-13A-5-1",
    "P3-13A-5-2",
    "P3-13A-5-4",
    "P3-13A-6-1",
    "P3-13A-6-2",
    "P3-13A-6-3",
    "P3-14-V1-1",
    "P3-14-V1-2",
    "P3-14-V3-2",
    "P3-14-V5-1",
    "P3-14-V6-1",
    "P3-14-1-1",
    "P3-14-2-1",
    "P3-14-3-1",
    "P3-14-3-2",
    "P3-14-3-3",
    "P3-14-3-R1",
    "P3-14-3-R2",
    "P3-14-3-R3",
    "P3-14-4-1",
    "P3-14-4-2",
    "P3-14-4-3",
    "P3-14-4-4",
    "P3-14-4-5",
    "P3-V3-15-1",
    "P3-15-1-1",
    "P3-15-2-2",
    "P3-15-3-R1",
    "P3-15-3-1",
    "P3-15-3-2",
    "P3-15-3-3",
    "P3-15-4-R1",
    "P3-15-4-1",
    "P3-15-4-2",
    "P3-15-4-3",
    "P3-15-5-1",
    "P3-15-5-2",
    "P3-15-5-3",
    "P3-15-REMED-1",
    "P3-V1-16-1",
    "P3-16-1-1",
    "P3-16-1-2",
    "P3-16-2-1",
    "P3-16-2-2",
    "P3-16-2-3",
    "P3-16-3A-1",
    "P3-16-3A-2",
    "P3-16-3A-3",
    "P3-16-3B-R1",
    "P3-16-3B-1",
    "P3-16-3B-2",
    "P3-16-3B-3",
    "P3-16-4-1",
    "P3-16-4-2",
    "P3-16-4-3",
    "P3-V3-17-1",
    "P3-17-1-1",
    "P3-17-1-2",
    "P3-17-2-1",
    "P3-17-2-2",
    "P3-17-2-3",
    "P3-17-3-1",
    "P3-17-4-1",
    "P3-17-4-2",
    "P3-17-4-3",
    "P3-17-5-1",
    "P3-17-5-2",
    "P3-17-6-1",
    "P3-17-7-1",
    "P3-18-1-1",
    "P3-18-1-2",
    "P3-18-2-1",
    "P3-18-2-2",
    "P3-18-3-1",
    "P3-18-3-2",
    "P3-18-4-1",
    "P3-18-4-2",
    "P3-18-5-1",
    "P3-18-5-2",
    "P3-18-6-1",
    "P3-18-6-2",
    "P3-18-7-1",
    "P3-18-7-2",
    "P3-19-2-1",
    "P3-V1-19-REMED-1",
    "P3-V3-19-CLI-1",
    "P3-V4-FINAL-1",
)


def _section(text: str, heading: str) -> str:
    pattern = rf"^### {re.escape(heading)}\n(?P<body>.*?)(?=^### |^## |\Z)"
    match = re.search(pattern, text, flags=re.MULTILINE | re.DOTALL)
    assert match is not None, f"missing section {heading!r}"
    return match.group("body")


def _table_rows(section: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if all(set(cell) <= {"-", ":"} for cell in cells):
            continue
        rows.append(cells)
    assert rows, "expected at least one markdown table row"
    return rows


def _documented_projection_inventory() -> dict[str, tuple[str, ...]]:
    section = _section(DOC_19A.read_text(encoding="utf-8"), "19A-6 governance failure-id inventory")
    rows = _table_rows(section)
    assert rows[0] == ["Slice", "Count", "Failure types"]
    inventory: dict[str, tuple[str, ...]] = {}
    for slice_id, count_s, failure_types in rows[1:]:
        ids = tuple(re.findall(r"`([^`]+)`", failure_types))
        assert len(ids) == int(count_s)
        inventory[slice_id] = ids
    return inventory


def test_19a_documented_governance_projection_inventory_matches_route_table() -> None:
    documented = _documented_projection_inventory()
    assert documented == EXPECTED_GOVERNANCE_PROJECTION_IDS_BY_SLICE

    expected_types = {
        failure_type
        for failure_types in EXPECTED_GOVERNANCE_PROJECTION_IDS_BY_SLICE.values()
        for failure_type in failure_types
    }
    route_rows = {
        (failure_class, failure_type)
        for (failure_class, failure_type), route in ROUTE_TABLE.items()
        if route.action == "retry_governance_projection"
    }

    assert len(route_rows) == 24
    assert route_rows == {
        ("evidence_corruption", failure_type)
        for failure_type in expected_types
    }


def test_19a_failure_router_safety_matrix_pins_13a_fail_closed_routes() -> None:
    section = _section(DOC_19A.read_text(encoding="utf-8"), "19A-6 failure-router safety matrix")
    rows = _table_rows(section)
    assert rows[0] == ["Scope", "Failure class", "Failure type", "Required action"]

    documented = {
        (failure_class.strip("`"), failure_type.strip("`")): action.strip("`")
        for _, failure_class, failure_type, action in rows[1:]
        if "all 24 inventory rows" not in failure_type
    }
    assert set(EXPECTED_13A_FAIL_CLOSED_ROUTES) <= set(documented)

    for key in EXPECTED_13A_FAIL_CLOSED_ROUTES:
        assert documented[key] == "quiesce"
        route = ROUTE_TABLE[key]
        assert route.action == "quiesce"
        assert route.allow_product_repair is False


def test_19a_carried_p3_ledger_is_explicit_owned_and_triggered() -> None:
    section = _section(DOC_19A.read_text(encoding="utf-8"), "19A-6 carried-P3 acceptance ledger")
    rows = _table_rows(section)
    assert rows[0] == ["ID", "Origin", "Disposition", "Rationale", "Owner", "Future trigger"]

    ledger_rows = rows[1:]
    assert len({tuple(row) for row in ledger_rows}) == len(ledger_rows)
    ids = [row[0] for row in ledger_rows]
    assert len(set(ids)) == len(ids)
    assert tuple(ids) == EXPECTED_LEDGER_IDS

    for row in ledger_rows:
        assert len(row) == 6
        item_id, origin, disposition, rationale, owner, trigger = row
        assert item_id
        assert origin
        assert disposition in {
            "CLOSED",
            "RETAIN",
            "RECLASSIFIED",
            "PARTIALLY CLOSED",
        }
        assert rationale
        assert owner
        assert trigger
        assert ".." not in item_id
        assert "/" not in item_id
        assert "+" not in item_id


def test_13a_acceptance_points_to_19a_cross_slice_p3_ledger() -> None:
    text = DOC_13A_ACCEPTANCE.read_text(encoding="utf-8")
    assert "19a-governance-implementation-reassessment.md" in text
    assert "19A-6 carried-P3 acceptance ledger" in text
    assert "STATUS.md` is the active restart" in text
