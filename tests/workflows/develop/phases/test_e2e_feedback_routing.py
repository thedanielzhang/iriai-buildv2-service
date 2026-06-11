"""Item-10 (R3) develop-side e2e feedback routing unit tests.

Covers: the post-DAG end-of-run green gate (IRIAI_E2E_RUN_GATE), the tier-i
critical quiesce (IRIAI_E2E_CRITICAL_QUIESCE), the tier-ii boundary-wave
helpers (candidate selection / synthetic verdict / own retry budget), and the
_verify_and_fix_group parameter plumbing (max_retries + verify_key_stage_prefix
defaults preserve today's behavior byte-for-byte).

Every flag OFF (default/unset) must be a no-op — parity asserted explicitly.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from typing import Any

import pytest
from iriai_compose import Feature

from iriai_build_v2.models.outputs import EnhancementItem
from iriai_build_v2.workflows.develop.phases import implementation as impl

RUN_GATE_FLAG = "IRIAI_E2E_RUN_GATE"
QUIESCE_FLAG = "IRIAI_E2E_CRITICAL_QUIESCE"
REPAIR_FLAG = "IRIAI_E2E_BOUNDARY_REPAIR"
BUDGET_ENV = "IRIAI_E2E_REPAIR_RETRIES"


def _feature() -> Feature:
    return Feature(
        id="feat-1", name="f", slug="f", workflow_name="full-develop",
        workspace_id="main",
    )


class FakeArtifacts:
    def __init__(self, rows: dict[str, str] | None = None) -> None:
        self.rows = dict(rows or {})
        self.puts: list[tuple[str, str]] = []

    async def get(self, key: str, feature: Any = None) -> str | None:
        return self.rows.get(key)

    async def put(self, key: str, value: str, feature: Any = None) -> None:
        self.puts.append((key, value))
        self.rows[key] = value


class FakeWorkspaceManager:
    def __init__(self, base: str) -> None:
        self._base = base


class FakeRunner:
    def __init__(self, artifacts: FakeArtifacts, base: str | None = None) -> None:
        self.artifacts = artifacts
        self.services: dict[str, Any] = {}
        if base is not None:
            self.services["workspace_manager"] = FakeWorkspaceManager(base)


GREEN_STATUS = json.dumps({
    "latest_checkpoint": "group 3", "boot_smoke": "pass", "failed": 0,
    "errors": 0, "open_regressions": [],
})
GREEN_POINTER = json.dumps({"group_idx": 3, "result_commits": {"repo": "abc"}})


# ── (d) end-of-run green gate ───────────────────────────────────────────────


def test_run_gate_flag_off_is_noop(monkeypatch):
    monkeypatch.delenv(RUN_GATE_FLAG, raising=False)
    runner = FakeRunner(FakeArtifacts())  # no e2e-status anywhere
    asyncio.run(impl._enforce_e2e_run_gate(runner, _feature(), phase_name="implementation"))
    assert runner.artifacts.puts == []  # parity: nothing read mutates, nothing written


def test_run_gate_absent_status_fails_loud(monkeypatch, tmp_path):
    monkeypatch.setenv(RUN_GATE_FLAG, "1")
    (tmp_path / ".iriai").mkdir()
    runner = FakeRunner(FakeArtifacts(), base=str(tmp_path))
    with pytest.raises(impl.WorkflowQuiesced) as exc:
        asyncio.run(impl._enforce_e2e_run_gate(runner, _feature(), phase_name="implementation"))
    assert "NEVER RAN" in str(exc.value)
    assert exc.value.metadata.get("operator_required") is True
    keys = [k for k, _ in runner.artifacts.puts]
    assert "workflow-blocker:e2e-run-gate" in keys
    actions = (tmp_path / ".iriai" / "OPERATOR-ACTIONS.md").read_text()
    assert actions.startswith("## [PENDING]")
    assert "e2e" in actions


def test_run_gate_present_but_red_fails_loud(monkeypatch, tmp_path):
    monkeypatch.setenv(RUN_GATE_FLAG, "1")
    (tmp_path / ".iriai").mkdir()
    red = json.dumps({
        "latest_checkpoint": "group 4", "boot_smoke": "fail", "failed": 3,
        "errors": 1, "open_regressions": ["spec-a"],
    })
    # No green pointer at all:
    runner = FakeRunner(FakeArtifacts({"e2e-status": red}), base=str(tmp_path))
    with pytest.raises(impl.WorkflowQuiesced):
        asyncio.run(impl._enforce_e2e_run_gate(runner, _feature(), phase_name="implementation"))
    # Green pointer exists but newest checkpoint is red:
    runner2 = FakeRunner(
        FakeArtifacts({"e2e-status": red, "e2e-green-checkpoint": GREEN_POINTER}),
        base=str(tmp_path),
    )
    with pytest.raises(impl.WorkflowQuiesced) as exc:
        asyncio.run(impl._enforce_e2e_run_gate(runner2, _feature(), phase_name="implementation"))
    assert "boot_smoke" in str(exc.value)


def test_run_gate_green_passes_and_records(monkeypatch):
    monkeypatch.setenv(RUN_GATE_FLAG, "1")
    runner = FakeRunner(FakeArtifacts({
        "e2e-status": GREEN_STATUS, "e2e-green-checkpoint": GREEN_POINTER,
    }))
    asyncio.run(impl._enforce_e2e_run_gate(runner, _feature(), phase_name="implementation"))
    assert runner.artifacts.rows.get("dag-gate:e2e-run") == "approved"


def test_run_gate_waiver_bypasses(monkeypatch):
    monkeypatch.setenv(RUN_GATE_FLAG, "1")
    runner = FakeRunner(FakeArtifacts({
        "e2e-run-gate-waiver": "operator: browser lanes not built yet (STEP-13 pending)",
    }))  # no e2e-status — would otherwise quiesce
    asyncio.run(impl._enforce_e2e_run_gate(runner, _feature(), phase_name="implementation"))
    assert runner.artifacts.rows.get("dag-gate:e2e-run") == "waived"


# ── tier-i critical quiesce ─────────────────────────────────────────────────

BLOCKER_ROW = json.dumps({
    "checkpoint": "group 2",
    "blockers": [{"kind": "critical_regression", "spec_id": "spec-x", "summary": "s"}],
})


def test_tier_i_flag_off_is_noop(monkeypatch):
    monkeypatch.delenv(QUIESCE_FLAG, raising=False)
    runner = FakeRunner(FakeArtifacts({"e2e-blocker": BLOCKER_ROW}))
    out = asyncio.run(impl._maybe_quiesce_on_e2e_critical(runner, _feature(), group_idx=3))
    assert out == ""
    assert runner.artifacts.puts == []


def test_tier_i_new_blocker_quiesces_with_marker_and_actions(monkeypatch, tmp_path):
    monkeypatch.setenv(QUIESCE_FLAG, "1")
    (tmp_path / ".iriai").mkdir()
    runner = FakeRunner(FakeArtifacts({"e2e-blocker": BLOCKER_ROW}), base=str(tmp_path))
    out = asyncio.run(impl._maybe_quiesce_on_e2e_critical(runner, _feature(), group_idx=3))
    assert "CRITICAL" in out and "group 3" in out
    keys = [k for k, _ in runner.artifacts.puts]
    assert "workflow-blocker:e2e-critical" in keys
    assert "e2e-blocker-handled" in keys
    actions = (tmp_path / ".iriai" / "OPERATOR-ACTIONS.md").read_text()
    assert actions.startswith("## [PENDING]")
    assert "e2e" in actions


def test_tier_i_handled_digest_resumes_past_same_blocker(monkeypatch, tmp_path):
    monkeypatch.setenv(QUIESCE_FLAG, "1")
    (tmp_path / ".iriai").mkdir()
    runner = FakeRunner(FakeArtifacts({"e2e-blocker": BLOCKER_ROW}), base=str(tmp_path))
    first = asyncio.run(impl._maybe_quiesce_on_e2e_critical(runner, _feature(), group_idx=3))
    assert first  # quiesced once
    second = asyncio.run(impl._maybe_quiesce_on_e2e_critical(runner, _feature(), group_idx=3))
    assert second == ""  # restart resumes past THIS blocker (operator decided)
    # but a NEW blocker payload re-arms the quiesce
    runner.artifacts.rows["e2e-blocker"] = json.dumps({
        "checkpoint": "group 5",
        "blockers": [{"kind": "boot_smoke", "surface": "compose", "detail": "down"}],
    })
    third = asyncio.run(impl._maybe_quiesce_on_e2e_critical(runner, _feature(), group_idx=6))
    assert third


def test_tier_i_no_blocker_is_noop(monkeypatch):
    monkeypatch.setenv(QUIESCE_FLAG, "1")
    runner = FakeRunner(FakeArtifacts())
    assert asyncio.run(
        impl._maybe_quiesce_on_e2e_critical(runner, _feature(), group_idx=1)
    ) == ""


# ── tier-ii boundary wave helpers ───────────────────────────────────────────


def _item(**kw: Any) -> EnhancementItem:
    kw.setdefault("source", "e2e_regression")
    kw.setdefault("severity", "major")
    kw.setdefault("description", "d")
    return EnhancementItem(**kw)


def test_wave_candidates_select_only_unconsumed_e2e_majors():
    items = [
        _item(description="e2e major A"),
        _item(description="e2e major consumed"),
        _item(description="e2e minor", severity="minor"),
        _item(description="build major", source="e2e_preview_build"),
        _item(description="non-e2e major", source="code_reviewer"),
    ]
    out = impl._e2e_wave_candidates(items, {"e2e major consumed"})
    assert [it.description for it in out] == ["e2e major A", "build major"]


def test_wave_synthetic_verdict_shape():
    items = [
        _item(description="regression in login", file="src/login.py", line=12),
        _item(description="build break"),
    ]
    v = impl._build_e2e_wave_verdict(items)
    assert v.approved is False
    assert len(v.concerns) == 2
    assert v.concerns[0].severity == "major"
    assert v.concerns[0].file == "src/login.py"
    assert v.concerns[0].line == 12
    assert "regression in login" in v.concerns[0].description  # verbatim


def test_repair_budget_own_env_never_verify_retries(monkeypatch):
    monkeypatch.delenv(BUDGET_ENV, raising=False)
    assert impl._e2e_repair_retries() == 1  # sensible small default
    monkeypatch.setenv(BUDGET_ENV, "3")
    assert impl._e2e_repair_retries() == 3
    monkeypatch.setenv(BUDGET_ENV, "garbage")
    assert impl._e2e_repair_retries() == 1
    monkeypatch.setenv(BUDGET_ENV, "-2")
    assert impl._e2e_repair_retries() == 0
    # rider: the wave budget is structurally independent of VERIFY_RETRIES
    assert impl.VERIFY_RETRIES == 2
    monkeypatch.setenv(BUDGET_ENV, "5")
    assert impl._e2e_repair_retries() != impl.VERIFY_RETRIES


def test_wave_flag_off_is_noop(monkeypatch):
    monkeypatch.delenv(REPAIR_FLAG, raising=False)
    runner = FakeRunner(FakeArtifacts({
        "enhancement-backlog": json.dumps({"items": [
            {"source": "e2e_regression", "severity": "major", "description": "x"},
        ]}),
        "dag-group:0": json.dumps({"results": []}),
    }))

    class _Dag:
        execution_order = [["t1"], ["t2"]]
        tasks: list[Any] = []

    out = asyncio.run(impl._maybe_run_e2e_boundary_repair_wave(
        runner, _feature(), _Dag(), group_idx=1, tasks_by_id={},
        all_results=[], handover=impl.HandoverDoc(), dag_sha256="d",
    ))
    assert out == ""
    assert runner.artifacts.puts == []


def test_wave_flag_on_no_candidates_is_noop(monkeypatch):
    monkeypatch.setenv(REPAIR_FLAG, "1")
    runner = FakeRunner(FakeArtifacts({
        "enhancement-backlog": json.dumps({"items": [
            {"source": "verify", "severity": "major", "description": "not e2e"},
            {"source": "e2e_regression", "severity": "minor", "description": "tier-iii"},
        ]}),
    }))

    class _Dag:
        execution_order = [["t1"], ["t2"]]
        tasks: list[Any] = []

    out = asyncio.run(impl._maybe_run_e2e_boundary_repair_wave(
        runner, _feature(), _Dag(), group_idx=1, tasks_by_id={},
        all_results=[], handover=impl.HandoverDoc(), dag_sha256="d",
    ))
    assert out == ""
    assert runner.artifacts.puts == []


def test_wave_skips_before_group_zero_and_without_checkpoint(monkeypatch):
    monkeypatch.setenv(REPAIR_FLAG, "1")
    backlog = json.dumps({"items": [
        {"source": "e2e_regression", "severity": "major", "description": "x"},
    ]})

    class _Dag:
        execution_order = [["t1"], ["t2"]]
        tasks: list[Any] = []

    # group 0 boundary: no sealed group yet
    runner = FakeRunner(FakeArtifacts({"enhancement-backlog": backlog}))
    assert asyncio.run(impl._maybe_run_e2e_boundary_repair_wave(
        runner, _feature(), _Dag(), group_idx=0, tasks_by_id={},
        all_results=[], handover=impl.HandoverDoc(), dag_sha256="d",
    )) == ""
    # boundary 1 but the prior checkpoint row is absent: skip loudly, no writes
    runner2 = FakeRunner(FakeArtifacts({"enhancement-backlog": backlog}))
    assert asyncio.run(impl._maybe_run_e2e_boundary_repair_wave(
        runner2, _feature(), _Dag(), group_idx=1, tasks_by_id={},
        all_results=[], handover=impl.HandoverDoc(), dag_sha256="d",
    )) == ""
    assert runner2.artifacts.puts == []


def test_wave_runs_namespaced_with_own_budget_and_ledgers(monkeypatch):
    """The wave calls _verify_and_fix_group with the wave-namespaced key, the
    OWN budget, and the resolved sealed-group semantics; consumed items land in
    the durable wave ledger so the next boundary does not re-feed them."""
    monkeypatch.setenv(REPAIR_FLAG, "1")
    monkeypatch.setenv(BUDGET_ENV, "1")
    captured: dict[str, Any] = {}

    async def fake_verify(runner, feature, group_idx, group_tasks, results,
                          all_results, handover, feature_root, impl_runtime,
                          review_runtime, rca_runtime=None, **kwargs):
        captured.update(kwargs, group_idx=group_idx)
        return True, ""

    monkeypatch.setattr(impl, "_verify_and_fix_group", fake_verify)

    async def fake_event(*a, **k):
        return None

    monkeypatch.setattr(impl, "_log_feature_event", fake_event)
    runner = FakeRunner(FakeArtifacts({
        "enhancement-backlog": json.dumps({"items": [
            {"source": "e2e_regression", "severity": "major",
             "description": "e2e regression: spend tab 500s", "file": "api/spend.py"},
        ]}),
        "dag-group:1": json.dumps({"results": []}),
    }))

    class _Dag:
        execution_order = [["t1"], ["t2"], ["t3"]]
        tasks: list[Any] = []

    out = asyncio.run(impl._maybe_run_e2e_boundary_repair_wave(
        runner, _feature(), _Dag(), group_idx=2, tasks_by_id={},
        all_results=[], handover=impl.HandoverDoc(), dag_sha256="sha",
    ))
    assert out == ""
    assert captured["group_idx"] == 1  # last SEALED group, no DAG mutation
    assert captured["initial_verdict_key"] == "dag-verify:g1:e2e-wave-0"
    assert captured["verify_key_stage_prefix"] == "e2e-wave-0:"
    assert captured["max_retries"] == 1  # own budget, not VERIFY_RETRIES
    assert captured["initial_verdict"].approved is False
    assert "spend tab 500s" in captured["fix_context"]
    ledger = json.loads(runner.artifacts.rows["e2e-wave-ledger"])
    assert ledger["consumed"] == ["e2e regression: spend tab 500s"]
    assert ledger["waves"][0]["approved"] is True
    assert ledger["waves"][0]["verdict_key"] == "dag-verify:g1:e2e-wave-0"
    # second boundary: the same row is consumed — no second wave
    captured.clear()
    out2 = asyncio.run(impl._maybe_run_e2e_boundary_repair_wave(
        runner, _feature(), _Dag(), group_idx=2, tasks_by_id={},
        all_results=[], handover=impl.HandoverDoc(), dag_sha256="sha",
    ))
    assert out2 == "" and captured == {}


def test_wave_nonconverged_is_nonblocking_but_blocker_propagates(monkeypatch):
    monkeypatch.setenv(REPAIR_FLAG, "1")
    rows = {
        "enhancement-backlog": json.dumps({"items": [
            {"source": "e2e_preview_build", "severity": "major", "description": "boom"},
        ]}),
        "dag-group:0": json.dumps({"results": []}),
    }

    class _Dag:
        execution_order = [["t1"], ["t2"]]
        tasks: list[Any] = []

    async def fake_event(*a, **k):
        return None

    monkeypatch.setattr(impl, "_log_feature_event", fake_event)

    async def not_converged(*a, **k):
        return False, "verify still failing after wave budget"

    monkeypatch.setattr(impl, "_verify_and_fix_group", not_converged)
    runner = FakeRunner(FakeArtifacts(dict(rows)))
    out = asyncio.run(impl._maybe_run_e2e_boundary_repair_wave(
        runner, _feature(), _Dag(), group_idx=1, tasks_by_id={},
        all_results=[], handover=impl.HandoverDoc(), dag_sha256="d",
    ))
    assert out == ""  # non-blocking; items stay for end-of-DAG
    ledger = json.loads(runner.artifacts.rows["e2e-wave-ledger"])
    assert ledger["waves"][0]["approved"] is False

    async def blocked(*a, **k):
        return False, impl._workflow_blocker_text("repo unreachable")

    monkeypatch.setattr(impl, "_verify_and_fix_group", blocked)
    runner2 = FakeRunner(FakeArtifacts(dict(rows)))
    out2 = asyncio.run(impl._maybe_run_e2e_boundary_repair_wave(
        runner2, _feature(), _Dag(), group_idx=1, tasks_by_id={},
        all_results=[], handover=impl.HandoverDoc(), dag_sha256="d",
    ))
    assert impl._is_workflow_blocker_text(out2)  # fail-loud path propagates


# ── _verify_and_fix_group plumbing parity ───────────────────────────────────


def test_verify_and_fix_group_new_params_default_to_today():
    sig = inspect.signature(impl._verify_and_fix_group)
    assert sig.parameters["max_retries"].default is None  # None => VERIFY_RETRIES
    assert sig.parameters["verify_key_stage_prefix"].default == ""  # '' => same keys
