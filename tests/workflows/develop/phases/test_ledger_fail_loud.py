"""Item-4 ledger/backlog fail-loud (IRIAI_LEDGER_FAIL_LOUD) unit tests.

Flag OFF (default/unset) must be byte-for-byte today's behavior: a corrupt
finding-ledger/enhancement-backlog row silently resets to empty (and the next
put overwrites it), resolve-by-absence needs no evidence, gap suppression has
no location guard, and the e2e bridge re-raises the raw parse error.

Flag ON: corrupt rows are quarantined under a sibling key + typed quiesce
(fail-loud marker artifact + OPERATOR-ACTIONS entry; exit via row repair +
restart), auto-resolve requires tree-digest file-change evidence, and gap
suppression requires a category (location) match. Uses the 47-byte-stub
corrupt-row class from the live incidents as the reproduction fixture.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from iriai_compose import Feature

from iriai_build_v2.models.outputs import (
    EnhancementBacklog,
    EnhancementItem,
    FindingLedger,
    FindingRecord,
    Gap,
    Issue,
    Verdict,
)
from iriai_build_v2.workflows.develop.e2e import bridge as e2e_bridge
from iriai_build_v2.workflows.develop.phases import implementation as impl

FLAG = "IRIAI_LEDGER_FAIL_LOUD"

# The 47-byte empty-stub row class from the live W-11 incidents.
CORRUPT_ROW = '{"truncated": tru'


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


def _v(**kw: Any) -> Verdict:
    kw.setdefault("approved", True)
    kw.setdefault("summary", "s")
    return Verdict(**kw)


# ── _load_ledger ────────────────────────────────────────────────────────────


def test_off_corrupt_ledger_resets_fresh(monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    runner = FakeRunner(FakeArtifacts({"finding-ledger": CORRUPT_ROW}))
    ledger = asyncio.run(impl._load_ledger(runner, _feature()))
    assert ledger == FindingLedger()
    assert runner.artifacts.puts == []  # parity: load itself never writes


def test_on_corrupt_ledger_quarantines_and_quiesces(monkeypatch, tmp_path):
    monkeypatch.setenv(FLAG, "1")
    (tmp_path / ".iriai").mkdir()
    runner = FakeRunner(
        FakeArtifacts({"finding-ledger": CORRUPT_ROW}), base=str(tmp_path),
    )
    with pytest.raises(impl.WorkflowQuiesced):
        asyncio.run(impl._load_ledger(runner, _feature()))
    keys = [k for k, _ in runner.artifacts.puts]
    assert "finding-ledger-quarantine" in keys
    assert "workflow-blocker:finding-ledger" in keys
    # The corrupt row itself was never overwritten
    assert runner.artifacts.rows["finding-ledger"] == CORRUPT_ROW
    import json as _json

    quarantine = _json.loads(runner.artifacts.rows["finding-ledger-quarantine"])
    assert quarantine["raw"] == CORRUPT_ROW
    # OPERATOR-ACTIONS entry prepended (fail-loud escalation channel)
    actions = (tmp_path / ".iriai" / "OPERATOR-ACTIONS.md").read_text()
    assert actions.startswith("## [PENDING]")
    assert "finding-ledger" in actions


def test_on_valid_ledger_parses_normally(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    ledger = FindingLedger(findings=[FindingRecord(
        id="F-001", source="verify", description="d",
    )])
    runner = FakeRunner(FakeArtifacts({"finding-ledger": ledger.model_dump_json()}))
    out = asyncio.run(impl._load_ledger(runner, _feature()))
    assert len(out.findings) == 1


def test_on_missing_workspace_still_quiesces(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    runner = FakeRunner(FakeArtifacts({"finding-ledger": CORRUPT_ROW}))
    with pytest.raises(impl.WorkflowQuiesced):
        asyncio.run(impl._load_ledger(runner, _feature()))


# ── _append_enhancements ────────────────────────────────────────────────────


def _items() -> list[EnhancementItem]:
    return [EnhancementItem(source="verify", severity="minor", description="new item")]


def test_off_corrupt_backlog_wiped_by_append(monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    runner = FakeRunner(FakeArtifacts({"enhancement-backlog": CORRUPT_ROW}))
    asyncio.run(impl._append_enhancements(runner, _feature(), _items()))
    # Parity with today: the corrupt row was overwritten with a fresh backlog
    backlog = EnhancementBacklog.model_validate_json(
        runner.artifacts.rows["enhancement-backlog"]
    )
    assert [i.description for i in backlog.items] == ["new item"]


def test_on_corrupt_backlog_quarantines_never_overwrites(monkeypatch, tmp_path):
    monkeypatch.setenv(FLAG, "1")
    (tmp_path / ".iriai").mkdir()
    runner = FakeRunner(
        FakeArtifacts({"enhancement-backlog": CORRUPT_ROW}), base=str(tmp_path),
    )
    with pytest.raises(impl.WorkflowQuiesced):
        asyncio.run(impl._append_enhancements(runner, _feature(), _items()))
    assert runner.artifacts.rows["enhancement-backlog"] == CORRUPT_ROW
    assert "enhancement-backlog-quarantine" in runner.artifacts.rows
    assert "workflow-blocker:enhancement-backlog" in runner.artifacts.rows


def test_on_valid_backlog_appends_normally(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    existing = EnhancementBacklog(items=[EnhancementItem(
        source="verify", severity="nit", description="completely different text",
    )])
    runner = FakeRunner(FakeArtifacts({
        "enhancement-backlog": existing.model_dump_json(),
    }))
    asyncio.run(impl._append_enhancements(runner, _feature(), _items()))
    backlog = EnhancementBacklog.model_validate_json(
        runner.artifacts.rows["enhancement-backlog"]
    )
    assert len(backlog.items) == 2


# ── _update_ledger evidence gating ──────────────────────────────────────────


def _open_ledger(tree_digest: str = "") -> FindingLedger:
    return FindingLedger(findings=[FindingRecord(
        id="F-001", source="code_reviewer", description="real unfixed issue",
        status="open", cycle_introduced=1, tree_digest=tree_digest,
    )])


def test_off_resolve_by_absence_needs_no_evidence(monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    ledger = impl._update_ledger(
        _open_ledger(), _v(), "code_reviewer", 2,
    )
    assert ledger.findings[0].status == "resolved"


def test_on_same_tree_omission_stays_open(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    ledger = impl._update_ledger(
        _open_ledger("digest-A"), _v(), "code_reviewer", 2,
        current_tree_digest="digest-A",
    )
    assert ledger.findings[0].status == "open"


def test_on_changed_tree_omission_resolves(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    ledger = impl._update_ledger(
        _open_ledger("digest-A"), _v(), "code_reviewer", 2,
        current_tree_digest="digest-B",
    )
    assert ledger.findings[0].status == "resolved"


def test_on_missing_evidence_stays_open(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    # No digest on the record (pre-existing ledger) and/or no current digest
    for record_digest, current in (("", "digest-B"), ("digest-A", ""), ("", "")):
        ledger = impl._update_ledger(
            _open_ledger(record_digest), _v(), "code_reviewer", 2,
            current_tree_digest=current,
        )
        assert ledger.findings[0].status == "open"


def test_new_findings_stamp_tree_digest(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    verdict = _v(concerns=[Issue(severity="major", description="fresh issue")])
    ledger = impl._update_ledger(
        FindingLedger(), verdict, "code_reviewer", 1,
        current_tree_digest="digest-A",
    )
    assert ledger.findings[0].tree_digest == "digest-A"


# ── _dedup_findings gap location guard ──────────────────────────────────────


def _resolved_gap_ledger(category: str) -> FindingLedger:
    return FindingLedger(findings=[FindingRecord(
        id="F-001", source="verify", description="missing coverage for area x",
        status="resolved", category=category,
    )])


def test_off_gap_suppressed_without_location(monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    verdict = _v(gaps=[Gap(
        category="totally-different", severity="major",
        description="missing coverage for area x",
    )])
    filtered, suppressed = impl._dedup_findings(
        verdict, _resolved_gap_ledger("testing"), "verify",
    )
    assert filtered.gaps == [] and len(suppressed) == 1


def test_on_gap_not_suppressed_on_category_mismatch(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    verdict = _v(gaps=[Gap(
        category="totally-different", severity="major",
        description="missing coverage for area x",
    )])
    filtered, suppressed = impl._dedup_findings(
        verdict, _resolved_gap_ledger("testing"), "verify",
    )
    assert len(filtered.gaps) == 1 and suppressed == []


def test_on_gap_suppressed_on_category_match(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    verdict = _v(gaps=[Gap(
        category="testing", severity="major",
        description="missing coverage for area x",
    )])
    filtered, suppressed = impl._dedup_findings(
        verdict, _resolved_gap_ledger("testing"), "verify",
    )
    assert filtered.gaps == [] and len(suppressed) == 1


def test_on_gap_not_suppressed_when_both_categories_empty(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    verdict = _v(gaps=[Gap(
        category="", severity="major",
        description="missing coverage for area x",
    )])
    filtered, suppressed = impl._dedup_findings(
        verdict, _resolved_gap_ledger(""), "verify",
    )
    assert len(filtered.gaps) == 1 and suppressed == []


# ── e2e bridge reader (the SECOND enhancement-backlog reader) ───────────────


class FakeRegistry:
    def __init__(self, raw: Any) -> None:
        self.raw = raw
        self.put_calls: list[tuple[str, Any]] = []

    async def get_raw(self, key: str) -> Any:
        return self.raw

    async def put_raw(self, key: str, value: Any) -> None:
        self.put_calls.append((key, value))


def test_bridge_off_corrupt_row_raises_raw(monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    registry = FakeRegistry(CORRUPT_ROW)
    with pytest.raises(Exception) as excinfo:
        asyncio.run(e2e_bridge.bridge_findings(
            registry, [], {}, checkpoint_label="g1",
        ))
    assert not isinstance(excinfo.value, e2e_bridge.E2EBacklogCorruptError)
    assert registry.put_calls == []  # parity: no quarantine write


def test_bridge_on_corrupt_row_quarantines_typed(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    registry = FakeRegistry(CORRUPT_ROW)
    with pytest.raises(e2e_bridge.E2EBacklogCorruptError) as excinfo:
        asyncio.run(e2e_bridge.bridge_findings(
            registry, [], {}, checkpoint_label="g1",
        ))
    assert "enhancement-backlog-quarantine" in str(excinfo.value)
    assert registry.put_calls and registry.put_calls[0][0] == "enhancement-backlog-quarantine"


def test_bridge_on_valid_row_unchanged(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    registry = FakeRegistry(EnhancementBacklog(items=[]).model_dump_json())
    result = asyncio.run(e2e_bridge.bridge_findings(
        registry, [], {}, checkpoint_label="g1",
    ))
    assert result.backlog_size == 0


def test_bridge_build_failures_also_covered(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    registry = FakeRegistry(CORRUPT_ROW)
    with pytest.raises(e2e_bridge.E2EBacklogCorruptError):
        asyncio.run(e2e_bridge.bridge_build_failures(
            registry, [e2e_bridge.LaneBuildFailure(lane="l", error="e")],
            checkpoint_label="g1",
        ))
