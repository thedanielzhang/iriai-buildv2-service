"""Unit tests for bridge (findings->backlog) and status (rollup/paging)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from iriai_build_v2.workflows.develop.e2e.bridge import (
    LaneBuildFailure,
    bridge_build_failures,
    bridge_findings,
)
from iriai_build_v2.workflows.develop.e2e.models import (
    E2ESpecRecord,
    E2EVerdictRecord,
)
from iriai_build_v2.workflows.develop.e2e.status import (
    CapturingPoster,
    build_status,
    emit_status,
    green_pointer_for,
    material_digest,
    page_critical,
)


class FakeRegistry:
    """Mimics the real store: BaseModel values are JSON-ified to dicts on put."""

    def __init__(self) -> None:
        self.store: dict = {}

    async def get_raw(self, key):
        return self.store.get(key)

    async def put_raw(self, key, value):
        self.store[key] = value.model_dump(mode="json") if isinstance(
            value, BaseModel
        ) else value

    async def put_status(self, status):
        self.store["e2e-status"] = status.model_dump(mode="json")

    async def get_status(self):
        return self.store.get("e2e-status")


def _reg(spec_id, status, fclass, critical=False, linked=None, summary="boom"):
    return (
        E2EVerdictRecord(spec_id=spec_id, status=status, failure_class=fclass,
                         critical=critical, summary=summary),
        E2ESpecRecord(spec_id=spec_id, linked_ac_ids=linked or [], spec_path="t.spec.ts"),
    )


@pytest.mark.asyncio
async def test_non_critical_regression_lands_in_backlog_with_ac_ids():
    reg = FakeRegistry()
    v, s = _reg("S1", "fail", "regression", linked=["AC-7", "AC-8"])
    res = await bridge_findings(reg, [v], {"S1": s}, checkpoint_label="group 79")
    assert len(res.appended) == 1
    item = res.appended[0]
    assert item.severity == "minor"
    assert "AC-7" in item.description and "AC-8" in item.description
    assert "group 79" in item.description
    # persisted to the enhancement-backlog artifact
    assert reg.store["enhancement-backlog"]["items"][0]["severity"] == "minor"


@pytest.mark.asyncio
async def test_backlog_dedupes_identical_findings():
    reg = FakeRegistry()
    v, s = _reg("S1", "fail", "regression", linked=["AC-7"], summary="badge regressed")
    r1 = await bridge_findings(reg, [v], {"S1": s}, checkpoint_label="g79")
    assert len(r1.appended) == 1
    r2 = await bridge_findings(reg, [v], {"S1": s}, checkpoint_label="g79")
    assert len(r2.appended) == 0 and r2.deduped  # deduped, not re-appended


@pytest.mark.asyncio
async def test_intended_change_and_flaky_are_not_findings():
    reg = FakeRegistry()
    v_ic, s_ic = _reg("S1", "fail", "intended_change")
    v_fk, s_fk = _reg("S2", "pass", "flaky")
    res = await bridge_findings(reg, [v_ic, v_fk], {"S1": s_ic, "S2": s_fk},
                                checkpoint_label="g79")
    assert res.appended == [] and res.critical == []


@pytest.mark.asyncio
async def test_critical_regression_is_paged_not_backlogged():
    reg = FakeRegistry()
    v, s = _reg("S1", "fail", "regression", critical=True, linked=["AC-1"])
    res = await bridge_findings(reg, [v], {"S1": s}, checkpoint_label="g79")
    assert res.appended == []
    assert len(res.critical) == 1 and res.critical[0].spec_id == "S1"
    assert "enhancement-backlog" not in reg.store  # nothing backlogged


@pytest.mark.asyncio
async def test_build_failure_lands_in_backlog_as_build_finding():
    reg = FakeRegistry()
    fails = [LaneBuildFailure(lane="playwright.config.badge.ts",
                              error='"SanitizedMarkdown" is not exported by ...')]
    res = await bridge_build_failures(reg, fails, checkpoint_label="group 80",
                                      file="src/webviews/projectSurface/vite.config.ts")
    assert len(res.appended) == 1
    item = res.appended[0]
    assert item.source == "e2e_preview_build" and item.category == "build"
    assert item.severity == "major"
    assert "group 80" in item.description and "badge" in item.description
    assert item.file == "src/webviews/projectSurface/vite.config.ts"
    assert reg.store["enhancement-backlog"]["items"][0]["category"] == "build"


@pytest.mark.asyncio
async def test_build_failures_sharing_root_cause_dedupe():
    reg = FakeRegistry()
    err = '"SanitizedMarkdown" is not exported by studio/packages/markdown-sanitizer'
    fails = [
        LaneBuildFailure(lane="playwright.config.badge.ts", error=err),
        LaneBuildFailure(lane="playwright.config.lifecycle.ts", error=err),
    ]
    res = await bridge_build_failures(reg, fails, checkpoint_label="group 80")
    # same root-cause error -> Jaccard dedupe collapses to a single finding
    assert len(res.appended) == 1 and len(res.deduped) == 1


@pytest.mark.asyncio
async def test_status_emits_card_only_on_material_change():
    reg = FakeRegistry()
    poster = CapturingPoster()
    cp = SimpleNamespace(group_idx=79, result_commits=lambda: {"iriai-studio": "0d480cd"})
    smokes = [SimpleNamespace(status="pass", surface="web")]
    verdicts = [E2EVerdictRecord(spec_id="S1", status="pass")]
    st = build_status(checkpoint=cp, smokes=smokes, verdicts=verdicts,
                      preview_url="http://x")
    assert st.boot_smoke == "pass" and st.passed == 1
    posted1 = await emit_status(reg, st, poster=poster)
    assert posted1 and len(poster.cards) == 1
    # same status -> no re-post (material dedupe)
    posted2 = await emit_status(reg, st, poster=poster)
    assert not posted2 and len(poster.cards) == 1
    # a material change -> re-post
    st2 = build_status(checkpoint=cp, smokes=smokes,
                       verdicts=[E2EVerdictRecord(spec_id="S2", status="fail",
                                                  failure_class="regression")],
                       preview_url="http://x")
    posted3 = await emit_status(reg, st2, poster=poster)
    assert posted3 and len(poster.cards) == 2


@pytest.mark.asyncio
async def test_critical_page_is_not_deduped_and_writes_blocker():
    reg = FakeRegistry()
    poster = CapturingPoster()
    crit = E2EVerdictRecord(spec_id="S1", status="fail", failure_class="regression",
                            critical=True, summary="real dep down")
    smoke_fail = SimpleNamespace(status="fail", surface="web", detail="webServer down")
    n1 = await page_critical(reg, poster=poster, checkpoint_label="g79",
                             critical_regressions=[crit],
                             boot_smoke_failures=[smoke_fail])
    assert n1 == 2 and len(poster.cards) == 2
    assert "e2e-blocker" in reg.store
    # paging again pages again (NOT deduped) — a real blocker can't be swallowed
    n2 = await page_critical(reg, poster=poster, checkpoint_label="g79",
                             critical_regressions=[crit])
    assert n2 == 1 and len(poster.cards) == 3


def test_green_pointer_requires_pass_and_no_critical():
    cp = SimpleNamespace(group_idx=79, result_commits=lambda: {"r": "c"})
    assert green_pointer_for(cp, boot_smoke="pass", open_critical_regressions=0) is not None
    assert green_pointer_for(cp, boot_smoke="fail", open_critical_regressions=0) is None
    assert green_pointer_for(cp, boot_smoke="pass", open_critical_regressions=1) is None


def test_material_digest_changes_on_boot_smoke():
    s1 = build_status(checkpoint=None, smokes=[], verdicts=[])
    s2 = build_status(
        checkpoint=None,
        smokes=[SimpleNamespace(status="fail", surface="web")], verdicts=[],
    )
    assert material_digest(s1) != material_digest(s2)
