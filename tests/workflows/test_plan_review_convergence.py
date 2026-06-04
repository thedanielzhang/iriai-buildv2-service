"""The plan-review Step-1 loop must CONVERGE (it ground for 3h+ on feature
ada28430). These exercise the cross-cycle gate-ledger convergence machinery the
loop now uses: a re-raised, unfixed finding set terminates (fail-fast, never an
infinite grind); a resolved finding is suppressed so the loop reaches a fixpoint;
and a genuinely-new finding still gets a revision pass. NOT a turn cap.
"""

from __future__ import annotations

import pytest

from iriai_build_v2.models.outputs import (
    GateReviewLedger,
    RevisionPlan,
    RevisionRequest,
)
from iriai_build_v2.workflows._common._helpers import (
    _assert_gate_requests_are_converging,
    _dedup_revision_requests,
    _update_gate_ledger,
)

SRC = "plan-review"


def _plan(*descriptions: str) -> RevisionPlan:
    return RevisionPlan(
        requests=[
            RevisionRequest(description=d, reasoning="r", severity="major")
            for d in descriptions
        ]
    )


def _digest(plan: RevisionPlan) -> str:
    import hashlib

    return hashlib.sha256(
        "\x00".join(sorted(r.description for r in plan.requests)).encode()
    ).hexdigest()[:16]


def test_repeated_unfixed_finding_set_fails_fast_not_infinite():
    # Mirrors the loop wiring: each cycle assert-before-update on the SAME finding
    # set (same digest). After _MAX_SAME_DIGEST_GATE_ATTEMPTS it must RAISE rather
    # than loop forever (the ada28430 hang).
    ledger = GateReviewLedger()
    plan = _plan("decision D-GR-7 does not resolve to a ledger entry")
    digest = _digest(plan)
    raised = False
    for cycle in range(1, 8):  # bounded: must raise well before this
        deduped, _ = _dedup_revision_requests(plan, ledger, SRC)
        assert deduped.requests, "unfixed finding must NOT be silently dropped"
        try:
            _assert_gate_requests_are_converging(
                deduped, ledger, SRC, artifact_digest=digest
            )
        except RuntimeError:
            raised = True
            break
        ledger = _update_gate_ledger(
            ledger, deduped, SRC, cycle, artifact_digest=digest
        )
    assert raised, "a repeating unfixed finding set must fail fast, not grind"


def test_resolved_finding_is_suppressed_reaching_fixpoint():
    # Cycle 1 raises finding F1; cycle 2's review no longer raises it (it was
    # fixed) -> _update_gate_ledger marks it resolved -> a later plan re-raising
    # the SAME description is dedup'd to empty (loop converges via the fixpoint).
    ledger = GateReviewLedger()
    p1 = _plan("F1: missing AC for clear-filters")
    ledger = _update_gate_ledger(ledger, p1, SRC, 1, artifact_digest=_digest(p1))
    # Cycle 2: a DIFFERENT finding (F1 absent) -> F1 marked resolved.
    p2 = _plan("F2: unrelated wording nit")
    ledger = _update_gate_ledger(ledger, p2, SRC, 2, artifact_digest=_digest(p2))
    assert any(
        f.status == "resolved" and "F1" in f.description for f in ledger.findings
    )
    # Cycle 3: F1 re-raised -> suppressed by dedup against the resolved ledger.
    deduped, suppressed = _dedup_revision_requests(_plan("F1: missing AC for clear-filters"), ledger, SRC)
    assert deduped.requests == []  # fixpoint: nothing new -> loop breaks
    assert suppressed


def test_genuinely_new_finding_still_gets_a_pass():
    # A brand-new finding must NOT be suppressed (no over-suppression).
    ledger = GateReviewLedger()
    p1 = _plan("F1: missing AC")
    ledger = _update_gate_ledger(ledger, p1, SRC, 1, artifact_digest=_digest(p1))
    p2 = _plan("F2: brand new security gap")
    deduped, _ = _dedup_revision_requests(p2, ledger, SRC)
    assert [r.description for r in deduped.requests] == ["F2: brand new security gap"]
