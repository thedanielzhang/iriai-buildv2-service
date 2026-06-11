"""Item-5: develop honors planning-contract waived_ac_ids (fefd8f8 pattern).

Flag OFF (IRIAI_DEVELOP_CONTRACT_AC_WAIVERS unset) must be today's behavior
exactly: the catalog loader never reads planning contracts, no entry carries a
waived marker, every external AC compiles must_pass=True with byte-identical
digests, and every cited verification gate stays blocking.

Flag ON: waived ACs compile must_pass=False (WARN-logged with source), gates
citing them become non-blocking "(waived)" gates, the contract prompt block
marks them [WAIVED], and group verification receives a do-not-enforce section.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

from iriai_compose import Feature

from iriai_build_v2.models.outputs import Subfeature, SubfeatureDecomposition
from iriai_build_v2.workflows.develop.execution import task_contracts as tc
from iriai_build_v2.workflows.develop.phases import implementation as impl

FLAG = "IRIAI_DEVELOP_CONTRACT_AC_WAIVERS"


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
        Subfeature(id="SF-2", slug="beta", name="Beta", description="d"),
    ]).model_dump_json()


TEST_PLAN_ALPHA = (
    "## Acceptance Criteria\n"
    "- AC-A-1 — alpha does the first thing\n"
    "- AC-A-2 — alpha does the second thing\n"
)


def _contract_json(waived: list[str]) -> str:
    return json.dumps({"slug": "alpha", "waived_ac_ids": waived})


# ── catalog loader ──────────────────────────────────────────────────────────


def _load_catalog(rows: dict[str, str]) -> list[dict[str, Any]]:
    runner = FakeRunner(FakeArtifacts(rows))
    return asyncio.run(
        impl._load_external_acceptance_criteria_catalog(runner, _feature())
    )


def test_off_loader_never_marks_waived(monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    catalog = _load_catalog({
        "decomposition": _decomposition_json(),
        "test-plan:alpha": TEST_PLAN_ALPHA,
        "dag-contract:alpha": _contract_json(["AC-A-1"]),
    })
    assert len(catalog) == 2
    assert all("waived" not in entry for entry in catalog)


def test_on_loader_marks_waived_with_source(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    catalog = _load_catalog({
        "decomposition": _decomposition_json(),
        "test-plan:alpha": TEST_PLAN_ALPHA,
        "dag-contract:alpha": _contract_json(["AC-A-1"]),
    })
    by_id = {e["id"]: e for e in catalog}
    assert by_id["AC-A-1"].get("waived") is True
    assert by_id["AC-A-1"]["waiver_source"] == "dag-contract:alpha"
    assert "waived" not in by_id["AC-A-2"]


def test_on_loader_no_contract_no_marks(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    catalog = _load_catalog({
        "decomposition": _decomposition_json(),
        "test-plan:alpha": TEST_PLAN_ALPHA,
    })
    assert all("waived" not in entry for entry in catalog)


def test_on_loader_corrupt_contract_skipped(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    catalog = _load_catalog({
        "decomposition": _decomposition_json(),
        "test-plan:alpha": TEST_PLAN_ALPHA,
        "dag-contract:alpha": "{not json",
    })
    assert len(catalog) == 2
    assert all("waived" not in entry for entry in catalog)


def test_load_all_waivers(monkeypatch):
    runner = FakeRunner(FakeArtifacts({
        "decomposition": _decomposition_json(),
        "dag-contract:alpha": _contract_json(["AC-A-1", "AC-A-9"]),
    }))
    waivers = asyncio.run(
        impl._load_all_planning_contract_waivers(runner, _feature())
    )
    assert waivers == {"alpha": ["AC-A-1", "AC-A-9"]}


# ── contract compiler: must_pass + digest ───────────────────────────────────


def _entry(ac_id: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": ac_id,
        "description": f"{ac_id} does a thing",
        "source": "test-plan:alpha",
        "source_ordinal": 100_000,
        **extra,
    }


def test_unmarked_entry_compiles_must_pass_true_digest_unchanged():
    plain = tc._external_acceptance_criteria_by_id([_entry("AC-A-1")])
    spec = plain[tc._slug_id("AC-A-1")]
    assert spec.must_pass is True
    # Parity proof: digest is exactly today's hardcoded must_pass=True digest.
    expected = tc.stable_digest({
        "id": spec.id,
        "source_model": "TestAcceptanceCriterion",
        "source_field": spec.source_field,
        "source_ordinal": spec.source_ordinal,
        "text": spec.text,
        "must_pass": True,
    })
    assert spec.digest == expected


def test_waived_entry_compiles_must_pass_false():
    waived = tc._external_acceptance_criteria_by_id([
        _entry("AC-A-1", waived=True, waiver_source="dag-contract:alpha"),
    ])
    spec = waived[tc._slug_id("AC-A-1")]
    assert spec.must_pass is False
    plain = tc._external_acceptance_criteria_by_id([_entry("AC-A-1")])
    assert spec.digest != plain[tc._slug_id("AC-A-1")].digest


# ── gate compiler: waived gates non-blocking ────────────────────────────────


def _compile_gates(criteria: dict[str, Any]) -> list[Any]:
    request = SimpleNamespace(
        verification_gates=[],
        task=SimpleNamespace(id="T-1", verification_gates=["AC-A-1"]),
    )
    return tc._compile_verification_gates(
        request,
        list(criteria.values()),
        [],
        repo_ids={"repo-1"},
    )


def test_gate_for_waived_criterion_is_non_blocking():
    criteria = tc._external_acceptance_criteria_by_id([
        _entry("AC-A-1", waived=True, waiver_source="dag-contract:alpha"),
    ])
    gates = _compile_gates(criteria)
    assert len(gates) == 1
    gate = gates[0]
    assert gate.gate_kind == "model_verifier"
    assert gate.blocks_merge is False
    assert gate.blocks_checkpoint is False
    assert "(waived)" in gate.name


def test_gate_for_normal_criterion_stays_blocking():
    criteria = tc._external_acceptance_criteria_by_id([_entry("AC-A-1")])
    gates = _compile_gates(criteria)
    assert len(gates) == 1
    gate = gates[0]
    assert gate.blocks_merge is True
    assert gate.blocks_checkpoint is True
    assert "(waived)" not in gate.name


def test_unknown_criterion_still_fails_closed():
    import pytest

    request = SimpleNamespace(
        verification_gates=[],
        task=SimpleNamespace(id="T-1", verification_gates=["AC-UNKNOWN"]),
    )
    with pytest.raises(Exception, match="unknown criterion"):
        tc._compile_verification_gates(request, [], [], repo_ids={"repo-1"})


# ── prompt rendering ────────────────────────────────────────────────────────


def test_prompt_block_marks_waived_criteria():
    contract = {
        "id": "c-1",
        "repo_id": "repo-1",
        "repo_path": "repo-1",
        "acceptance_criteria": [
            {"id": "ac-a-1", "text": "waived one", "must_pass": False},
            {"id": "ac-a-2", "text": "binding one", "must_pass": True},
        ],
    }
    block = impl._task_contract_prompt_block(contract)
    waived_line = next(line for line in block.splitlines() if "ac-a-1" in line)
    binding_line = next(line for line in block.splitlines() if "ac-a-2" in line)
    assert "[WAIVED" in waived_line
    assert "[WAIVED" not in binding_line
