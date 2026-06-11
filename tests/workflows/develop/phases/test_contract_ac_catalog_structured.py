"""M-3: the contract AC catalog prefers ``test-plan-structured:{slug}``.

The markdown ``test-plan:{slug}`` heading format drops ``verification_method``
and ``pass_condition`` — contract-time verifiers silently lose the binding
pass conditions. The catalog loader
(``implementation._load_external_acceptance_criteria_catalog``) must read the
structured artifact first and only fall back to markdown with a loud WARN
listing exactly which AC ids arrived without method / pass condition.

Conventions mirror ``test_contract_ac_waivers.py`` (FakeRunner/FakeArtifacts).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from iriai_compose import Feature

# Aliased so pytest does not try to collect the Test*-named models.
from iriai_build_v2.models.outputs import (
    Subfeature,
    SubfeatureDecomposition,
    TestAcceptanceCriterion as ACModel,
    TestPlan as TestPlanModel,
)
from iriai_build_v2.workflows.develop.phases import implementation as impl

WAIVER_FLAG = "IRIAI_DEVELOP_CONTRACT_AC_WAIVERS"


def _feature() -> Feature:
    return Feature(
        id="feat-1", name="f", slug="f", workflow_name="full-develop",
        workspace_id="main",
    )


class FakeArtifacts:
    def __init__(self, rows: dict[str, str] | None = None) -> None:
        self.rows = dict(rows or {})

    async def get(self, key: str, feature: Any = None) -> str | None:
        return self.rows.get(key)


class FakeRunner:
    def __init__(self, artifacts: FakeArtifacts) -> None:
        self.artifacts = artifacts
        self.services: dict[str, Any] = {}


def _decomposition_json() -> str:
    return SubfeatureDecomposition(subfeatures=[
        Subfeature(id="SF-1", slug="alpha", name="Alpha", description="d"),
    ]).model_dump_json()


MARKDOWN_PLAN_ALPHA = (
    "## Acceptance Criteria\n"
    "- AC-A-1 — alpha does the first thing\n"
    "- AC-A-2 — alpha does the second thing\n"
)


def _structured_plan_json() -> str:
    return TestPlanModel(
        acceptance_criteria=[
            ACModel(
                id="AC-A-1",
                description="alpha does the first thing",
                verification_method="unit",
                pass_condition="pytest test_alpha_first passes",
            ),
            ACModel(
                id="AC-A-2",
                description="alpha does the second thing",
                verification_method="e2e",
                pass_condition="playwright smoke covers the second thing",
            ),
        ],
    ).model_dump_json()


def _load_catalog(rows: dict[str, str]) -> list[dict[str, Any]]:
    runner = FakeRunner(FakeArtifacts(rows))
    return asyncio.run(
        impl._load_external_acceptance_criteria_catalog(runner, _feature())
    )


def test_structured_artifact_preferred_over_markdown(monkeypatch):
    """Both artifacts present: the structured one wins and carries method+pass."""
    monkeypatch.delenv(WAIVER_FLAG, raising=False)
    catalog = _load_catalog({
        "decomposition": _decomposition_json(),
        "test-plan-structured:alpha": _structured_plan_json(),
        "test-plan:alpha": MARKDOWN_PLAN_ALPHA,
    })
    by_id = {e["id"]: e for e in catalog}
    assert set(by_id) == {"AC-A-1", "AC-A-2"}
    assert by_id["AC-A-1"]["verification_method"] == "unit"
    assert by_id["AC-A-1"]["pass_condition"] == "pytest test_alpha_first passes"
    assert by_id["AC-A-2"]["verification_method"] == "e2e"
    assert all(e["source"] == "test-plan-structured:alpha" for e in catalog)


def test_markdown_fallback_when_structured_absent_warns(monkeypatch, caplog):
    """No structured artifact: markdown fallback still works, with a loud WARN."""
    monkeypatch.delenv(WAIVER_FLAG, raising=False)
    with caplog.at_level(logging.WARNING):
        catalog = _load_catalog({
            "decomposition": _decomposition_json(),
            "test-plan:alpha": MARKDOWN_PLAN_ALPHA,
        })
    by_id = {e["id"]: e for e in catalog}
    assert set(by_id) == {"AC-A-1", "AC-A-2"}
    assert all(e["source"] == "test-plan:alpha" for e in catalog)
    warning = "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.WARNING
    )
    assert "fell back to markdown test-plan:alpha" in warning
    assert "no test-plan-structured:alpha artifact exists" in warning
    # The WARN lists exactly which AC ids lost method / pass condition.
    assert "AC-A-1" in warning and "AC-A-2" in warning


def test_markdown_fallback_when_structured_unparseable_warns(monkeypatch, caplog):
    """A corrupt structured artifact falls back to markdown with a loud WARN."""
    monkeypatch.delenv(WAIVER_FLAG, raising=False)
    with caplog.at_level(logging.WARNING):
        catalog = _load_catalog({
            "decomposition": _decomposition_json(),
            "test-plan-structured:alpha": "{not json",
            "test-plan:alpha": MARKDOWN_PLAN_ALPHA,
        })
    by_id = {e["id"]: e for e in catalog}
    assert set(by_id) == {"AC-A-1", "AC-A-2"}
    warning = "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.WARNING
    )
    assert "test-plan-structured:alpha exists but could not be parsed" in warning


def test_no_artifacts_yields_empty_catalog(monkeypatch):
    monkeypatch.delenv(WAIVER_FLAG, raising=False)
    assert _load_catalog({"decomposition": _decomposition_json()}) == []


def test_waivers_still_apply_to_structured_entries(monkeypatch):
    """Item-5 waiver marking is unchanged when entries come from structured."""
    monkeypatch.setenv(WAIVER_FLAG, "1")
    catalog = _load_catalog({
        "decomposition": _decomposition_json(),
        "test-plan-structured:alpha": _structured_plan_json(),
        "dag-contract:alpha": '{"slug": "alpha", "waived_ac_ids": ["AC-A-1"]}',
    })
    by_id = {e["id"]: e for e in catalog}
    assert by_id["AC-A-1"].get("waived") is True
    assert by_id["AC-A-1"]["waiver_source"] == "dag-contract:alpha"
    assert "waived" not in by_id["AC-A-2"]
    assert by_id["AC-A-1"]["verification_method"] == "unit"
