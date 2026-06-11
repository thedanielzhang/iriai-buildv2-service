"""Item-3 verdict hardening (IRIAI_STRICT_VERDICT_DISPOSITION) unit tests.

Flag OFF (default/unset) must be byte-for-byte today's behavior:
exact case-sensitive severity membership, exact ``== "FAIL"`` check results,
no coherence re-ask. Flag ON: normalized severities (off-vocab fail-closed to
blocker), fail-closed check results, ONE approved=false coherence re-ask.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from iriai_build_v2.models.outputs import Check, Gap, Issue, Verdict
from iriai_build_v2.workflows.develop.execution import verification as verif
from iriai_build_v2.workflows.develop.phases import implementation as impl

FLAG = "IRIAI_STRICT_VERDICT_DISPOSITION"


def _v(**kw: Any) -> Verdict:
    kw.setdefault("approved", True)
    kw.setdefault("summary", "s")
    return Verdict(**kw)


# ── flag OFF: parity with today's behavior ──────────────────────────────────


def test_off_case_variant_blocker_does_not_block(monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    v = _v(concerns=[Issue(severity="Blocker", description="d")])
    assert impl._is_approved(v) is True


def test_off_off_vocab_critical_does_not_block(monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    v = _v(concerns=[Issue(severity="critical", description="d")])
    assert impl._is_approved(v) is True


def test_off_lowercase_fail_check_passes(monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    v = _v(checks=[Check(criterion="c", result="fail")])
    assert impl._is_approved(v) is True


def test_off_exact_blocker_blocks(monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    v = _v(concerns=[Issue(severity="blocker", description="d")])
    assert impl._is_approved(v) is False


def test_off_partition_parks_case_variant_blocker(monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    v = _v(concerns=[Issue(severity="Blocker", description="d")])
    blocking, enhancements = impl._partition_verdict(v, "code_reviewer")
    assert blocking.concerns == []
    assert len(enhancements) == 1 and enhancements[0].severity == "Blocker"


def test_off_effectively_approved_unnormalized(monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    v = _v(approved=True, concerns=[Issue(severity="Critical", description="d")])
    assert verif._verdict_effectively_approved(v) is True


def test_off_no_coherence_reask(monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    called = []

    async def fake_ask(*a: Any, **k: Any) -> Verdict:  # pragma: no cover
        called.append(1)
        return _v()

    monkeypatch.setattr(impl, "_run_bound_diagnostic_ask", fake_ask)
    v = _v(approved=False)
    out = asyncio.run(impl._coherence_reask_if_incoherent(
        None, None, v, base_actor=object(), runtime=None, source="t",
        phase_name="implementation", feature_root=None, lane_id="t",
    ))
    assert out is v
    assert called == []


# ── flag ON: normalization ──────────────────────────────────────────────────


@pytest.mark.parametrize("severity", ["Blocker", "BLOCKER", "critical", "P0", " blocker "])
def test_on_blocking_severity_variants_block(monkeypatch, severity):
    monkeypatch.setenv(FLAG, "1")
    v = _v(concerns=[Issue(severity=severity, description="d")])
    assert impl._is_approved(v) is False


def test_on_unknown_severity_fail_closed_blocks(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    v = _v(gaps=[Gap(category="g", description="d", severity="weird-tier")])
    assert impl._is_approved(v) is False


@pytest.mark.parametrize("severity", ["minor", "Nit", "info", "suggestion", "low"])
def test_on_deferrable_severities_do_not_block(monkeypatch, severity):
    monkeypatch.setenv(FLAG, "1")
    v = _v(concerns=[Issue(severity=severity, description="d")])
    assert impl._is_approved(v) is True


@pytest.mark.parametrize("result", ["fail", "FAILED", "Fail", "garbage-result"])
def test_on_check_results_fail_closed(monkeypatch, result):
    monkeypatch.setenv(FLAG, "1")
    v = _v(checks=[Check(criterion="c", result=result)])
    assert impl._is_approved(v) is False


@pytest.mark.parametrize("result", ["PASS", "pass", "satisfied", "not-needed", "N/A", ""])
def test_on_passing_check_results_pass(monkeypatch, result):
    monkeypatch.setenv(FLAG, "1")
    v = _v(checks=[Check(criterion="c", result=result)])
    assert impl._is_approved(v) is True


def test_on_partition_keeps_normalized_blockers(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    v = _v(concerns=[
        Issue(severity="Blocker", description="real blocker"),
        Issue(severity="Info", description="fyi"),
    ])
    blocking, enhancements = impl._partition_verdict(v, "code_reviewer")
    assert [c.description for c in blocking.concerns] == ["real blocker"]
    assert len(enhancements) == 1
    assert enhancements[0].severity == "nit"  # normalized canonical value


def test_on_effectively_approved_normalizes(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    v = _v(approved=True, concerns=[Issue(severity="Critical", description="d")])
    assert verif._verdict_effectively_approved(v) is False


def test_normalize_severity_aliases():
    assert impl._normalize_severity("critical") == "blocker"
    assert impl._normalize_severity("High") == "major"
    assert impl._normalize_severity("medium") == "minor"
    assert impl._normalize_severity("informational") == "nit"
    assert impl._normalize_severity("nonsense") == "blocker"
    assert impl._normalize_severity("") == ""
    assert impl._normalize_severity("major") == "major"


# ── flag ON: coherence re-ask ───────────────────────────────────────────────


def _run_reask(monkeypatch, original: Verdict, reask_result: Any) -> Any:
    calls: list[dict[str, Any]] = []

    async def fake_ask(*a: Any, **k: Any) -> Any:
        calls.append(k)
        if isinstance(reask_result, Exception):
            raise reask_result
        return reask_result

    monkeypatch.setattr(impl, "_run_bound_diagnostic_ask", fake_ask)
    out = asyncio.run(impl._coherence_reask_if_incoherent(
        None, None, original, base_actor=object(), runtime=None,
        source="code_reviewer", phase_name="implementation",
        feature_root=None, lane_id="post-dag:code-review-coherence",
    ))
    return out, calls


def test_on_reask_confirms_approval(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    original = _v(approved=False, summary="vague unease")
    confirmed = _v(approved=True, summary="confirmed fine")
    out, calls = _run_reask(monkeypatch, original, confirmed)
    assert len(calls) == 1
    assert out.approved is True
    assert impl._is_approved(out) is True


def test_on_reask_restates_blockers(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    original = _v(approved=False, summary="vague unease")
    restated = _v(approved=False, summary="now concrete",
                  concerns=[Issue(severity="blocker", description="b")])
    out, calls = _run_reask(monkeypatch, original, restated)
    assert len(calls) == 1
    assert impl._is_approved(out) is False


def test_on_reask_still_incoherent_fails_closed(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    original = _v(approved=False, summary="vague unease")
    still_bad = _v(approved=False, summary="still vague")
    out, calls = _run_reask(monkeypatch, original, still_bad)
    assert len(calls) == 1
    assert impl._is_approved(out) is False  # synthetic blocker appended
    assert any("Strict verdict disposition" in c.description for c in out.concerns)


def test_on_reask_exception_fails_closed(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    original = _v(approved=False, summary="vague unease")
    out, calls = _run_reask(monkeypatch, original, RuntimeError("provider down"))
    assert len(calls) == 1
    assert impl._is_approved(out) is False


def test_on_coherent_verdicts_skip_reask(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    called = []

    async def fake_ask(*a: Any, **k: Any) -> Verdict:  # pragma: no cover
        called.append(1)
        return _v()

    monkeypatch.setattr(impl, "_run_bound_diagnostic_ask", fake_ask)
    # approved=True → skip; approved=False WITH blocker → skip (already blocking)
    for v in (
        _v(approved=True),
        _v(approved=False, concerns=[Issue(severity="blocker", description="d")]),
    ):
        out = asyncio.run(impl._coherence_reask_if_incoherent(
            None, None, v, base_actor=object(), runtime=None, source="t",
            phase_name="implementation", feature_root=None, lane_id="t",
        ))
        assert out is v
    assert called == []
