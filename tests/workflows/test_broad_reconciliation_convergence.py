"""The broad-reconciliation Step loop must CONVERGE.

`_run_broad_reconciliation_stage` re-runs the lead's `integration_review` with a
CLEARED session each round and only exits on `needs_revision == False`. With no
guard the reviewer surfaced new marginal/advisory findings indefinitely (~16
rounds observed) and could never emit a clean PASS. The loop now uses the same
cross-round gate-ledger convergence machinery as plan-review:

- an IDENTICAL unfixed finding set re-raised every round fails fast (fixpoint,
  NOT a turn cap) instead of grinding forever;
- a finding already resolved in a prior round is suppressed via dedup, so a
  re-review that surfaces nothing new converges to PASS;
- the normal case (a genuinely-clean review) still exits exactly as before.

These drive the REAL `_run_broad_reconciliation_stage` with a stubbed reviewer
(`integration_review`) and minimal thread/control plumbing — no live DB.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import iriai_build_v2.workflows.planning.phases.broad as broad
from iriai_build_v2.models.outputs import IntegrationReview, SubfeatureDecomposition


class _FakeArtifacts:
    """In-memory artifact store satisfying the gate-ledger helpers' get/put."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def get(self, key: str, *, feature=None) -> str | None:
        return self._store.get(key)

    async def put(self, key: str, text: str, *, feature=None) -> None:
        self._store[key] = text


class _FakeRunner:
    def __init__(self) -> None:
        self.artifacts = _FakeArtifacts()
        self.services: dict[str, object] = {}


@pytest.fixture
def _stubbed_loop_env(monkeypatch):
    """Stub out the thread/control plumbing so the loop body runs in isolation.

    Only the convergence machinery (ledger load/dedup/assert/update/save) and the
    stubbed `integration_review` actually execute.
    """
    handle = SimpleNamespace(resolver=None, thread_id="t", thread_ts="")

    async def _ensure_planning_thread(*a, **k):
        return handle

    async def _persist_planning_control(*a, **k):
        return None

    def _noop(*a, **k):
        return None

    monkeypatch.setattr(broad, "ensure_planning_thread", _ensure_planning_thread)
    monkeypatch.setattr(broad, "persist_planning_control", _persist_planning_control)
    monkeypatch.setattr(broad, "set_thread_runtime_metadata", _noop)
    monkeypatch.setattr(broad, "set_step_status", _noop)
    monkeypatch.setattr(broad, "make_thread_actor", lambda *a, **k: object())
    monkeypatch.setattr(broad, "make_thread_user", lambda *a, **k: object())
    monkeypatch.setattr(broad, "get_broad_step_record", lambda control, step: {})

    async def _no_existing(*a, **k):
        return ""

    monkeypatch.setattr(broad, "get_existing_artifact", _no_existing)
    return handle


def _decomposition() -> SubfeatureDecomposition:
    return SubfeatureDecomposition(subfeatures=[], edges=[])


@pytest.mark.asyncio
async def test_repeated_identical_findings_fail_fast_not_infinite(
    _stubbed_loop_env, monkeypatch
):
    # The reviewer returns the SAME needs_revision=True + identical
    # revision_instructions every round. The loop must TERMINATE (raise the
    # convergence assertion) rather than grind forever.
    calls = {"n": 0}
    same = {"prd": "tighten the wording of REQ-1 (advisory)"}

    async def _stub_review(*a, **k):
        calls["n"] += 1
        if calls["n"] > 20:  # safety net: the guard must trip well before this
            raise AssertionError("loop did not converge — ran away")
        return IntegrationReview(
            needs_revision=True, revision_instructions=dict(same)
        )

    applied = {"n": 0}

    async def _stub_apply(*a, **k):
        applied["n"] += 1
        return _decomposition()

    monkeypatch.setattr(broad, "integration_review", _stub_review)
    monkeypatch.setattr(broad, "_apply_broad_reconciliation_revisions", _stub_apply)

    runner = _FakeRunner()
    with pytest.raises(RuntimeError, match="not converging"):
        await broad._run_broad_reconciliation_stage(
            runner,
            SimpleNamespace(id="feat-1", name="Feat"),
            SimpleNamespace(),
            {},
            phase_name="broad",
            decomposition=_decomposition(),
        )
    # The same finding set recurred and tripped the guard; the loop did not run
    # away (well under the 20-round safety net).
    assert calls["n"] <= 5


@pytest.mark.asyncio
async def test_resolved_then_re_raised_finding_converges_to_pass(
    _stubbed_loop_env, monkeypatch
):
    # Round 1 raises F1 (gets applied + recorded). Round 2 surfaces a DIFFERENT
    # finding F2 (so F1 is marked resolved). Round 3 re-raises F1 — dedup against
    # the resolved ledger empties it, so the loop converges to PASS without
    # raising.
    seq = [
        {"prd": "F1: add missing acceptance criterion"},
        {"design": "F2: align token naming"},
        {"prd": "F1: add missing acceptance criterion"},
    ]
    calls = {"n": 0}

    async def _stub_review(*a, **k):
        i = calls["n"]
        calls["n"] += 1
        if i < len(seq):
            return IntegrationReview(
                needs_revision=True, revision_instructions=dict(seq[i])
            )
        raise AssertionError("loop should have converged before exhausting seq")

    async def _stub_apply(*a, **k):
        return _decomposition()

    monkeypatch.setattr(broad, "integration_review", _stub_review)
    monkeypatch.setattr(broad, "_apply_broad_reconciliation_revisions", _stub_apply)

    runner = _FakeRunner()
    result = await broad._run_broad_reconciliation_stage(
        runner,
        SimpleNamespace(id="feat-2", name="Feat"),
        SimpleNamespace(),
        {},
        phase_name="broad",
        decomposition=_decomposition(),
    )
    # Converged to PASS via dedup on the re-raised resolved finding.
    assert isinstance(result, SubfeatureDecomposition)
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_clean_review_returns_immediately_behavior_preserved(
    _stubbed_loop_env, monkeypatch
):
    # The normal case: a genuinely-clean review (needs_revision=False) exits on
    # the first round exactly as before the guard was added.
    async def _stub_review(*a, **k):
        return IntegrationReview(needs_revision=False)

    async def _stub_apply(*a, **k):
        raise AssertionError("clean review must not apply any revisions")

    monkeypatch.setattr(broad, "integration_review", _stub_review)
    monkeypatch.setattr(broad, "_apply_broad_reconciliation_revisions", _stub_apply)

    runner = _FakeRunner()
    result = await broad._run_broad_reconciliation_stage(
        runner,
        SimpleNamespace(id="feat-3", name="Feat"),
        SimpleNamespace(),
        {},
        phase_name="broad",
        decomposition=_decomposition(),
    )
    assert isinstance(result, SubfeatureDecomposition)
