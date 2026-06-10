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
    TargetedRevisionFailure,
    TargetedRevisionResult,
    _assert_gate_requests_are_converging,
    _build_deferred_requests_notice,
    _dedup_revision_requests,
    _is_transient_runtime_failure,
    _partition_revision_plan,
    _update_gate_ledger,
)
from iriai_build_v2.runtimes.claude import (
    ClaudeApiErrorStorm,
    StructuredOutputExhausted,
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


# ── Minor/nit deferral: partition + reviewer-facing deferral notice ──────────
# RCA (kaya system-design gate, cycles 4→6): when ALL of a cycle's revision
# requests are minor/nit they get deferred to the enhancement backlog and the
# gate re-presents — but the reviewer was never told, so it re-filed the
# identical minor request every cycle (3 wasted cycles). The notice built by
# _build_deferred_requests_notice is injected into the NEXT cycle's prompt.


def _req(description: str, severity: str) -> RevisionRequest:
    return RevisionRequest(description=description, reasoning="r", severity=severity)


def test_partition_defers_minor_and_nit_keeps_major_and_unknown_blocking():
    plan = RevisionPlan(
        requests=[
            _req("B: drop the table", "blocker"),
            _req("M: wrong API contract", "major"),
            _req("m: rename a heading", "minor"),
            _req("n: trailing whitespace", "nit"),
            _req("U: unclassified severity", ""),  # unknown -> blocking
        ]
    )
    filtered, deferred = _partition_revision_plan(plan, SRC)
    assert [r.description for r in deferred] == [
        "m: rename a heading",
        "n: trailing whitespace",
    ]
    assert [r.description for r in filtered.requests] == [
        "B: drop the table",
        "M: wrong API contract",
        "U: unclassified severity",
    ]
    # Original plan must not be mutated (model_copy semantics).
    assert len(plan.requests) == 5


def test_deferred_notice_renders_severity_and_truncated_description():
    long_desc = "x" * 300
    notice = _build_deferred_requests_notice(
        [_req("rename a heading", "minor"), _req(long_desc, "nit")]
    )
    # Severity tags + descriptions are rendered as bullets.
    assert "- [minor] rename a heading" in notice
    assert "- [nit] " + "x" * 200 + "…" in notice
    assert "x" * 201 not in notice  # description capped at ~200 chars
    # The anti-loop instructions the reviewer must see.
    assert "DEFERRED to the" in notice
    assert "Do NOT re-file" in notice
    assert "severity 'major'" in notice


def test_deferred_notice_is_empty_for_empty_list():
    assert _build_deferred_requests_notice([]) == ""


# ── Transient-runtime vs content-convergence classification ──────────────────
# RCA (kaya plan run e98bb92e): the Claude account ran OUT OF USAGE mid-cycle
# during the test-plan revision wave → api error storm + the agent CLI was
# SIGTERM'd (ProcessError exit -15) → the revision Ask tasks failed → plan-review
# fail-fasted with "Plan-review revisions failed in cycle 1". That message reads
# like the revision CONTENT could not converge, when the cause was external and
# transient. plan-review must now distinguish the two so an external blip is
# reported as a re-runnable agent-runtime failure, not a content failure.


def _named_exc(name: str, msg: str, *, cause: BaseException | None = None) -> Exception:
    """Build an exception whose class NAME is ``name`` (the classifier matches by
    name, so this exercises ProcessError / TaskExecutionError without importing
    the SDK / iriai_compose)."""
    exc = type(name, (Exception,), {})(msg)
    if cause is not None:
        exc.__cause__ = cause
    return exc


@pytest.mark.parametrize(
    "exc",
    [
        ClaudeApiErrorStorm("provider error storm; produced no output"),
        StructuredOutputExhausted("structured_output is None for TestPlan"),
        _named_exc("ProcessError", "Command failed with exit code -15"),
        _named_exc("ClaudeStreamWatchdogStall", "stream inactivity stall"),
        TimeoutError("stream inactivity"),
        RuntimeError("You're out of extra usage · resets Jun 6 at 9am"),
        RuntimeError("Command failed with exit code -15 (exit code: -15)"),
        RuntimeError("anthropic rate limit exceeded"),
        RuntimeError("server overloaded, please retry"),
    ],
)
def test_transient_runtime_failures_are_classified_transient(exc):
    assert _is_transient_runtime_failure(exc) is True


@pytest.mark.parametrize(
    "exc",
    [
        None,
        ValueError("revised artifact is not valid TestPlan JSON"),
        RuntimeError("test-plan targeted revision failed"),  # synthetic marker
        RuntimeError("batch 0-3 rejected by size guard (5000 -> 200)"),
        KeyError("D-GR-1"),
    ],
)
def test_content_failures_are_not_classified_transient(exc):
    assert _is_transient_runtime_failure(exc) is False


def test_classifier_unwraps_task_execution_error_to_cause():
    # WorkflowRunner.run wraps the real error in TaskExecutionError(__cause__=...).
    transient = _named_exc(
        "TaskExecutionError",
        "Task Ask failed in phase 'plan-review' for feature 'e98bb92e'",
        cause=ClaudeApiErrorStorm("storm"),
    )
    assert _is_transient_runtime_failure(transient) is True
    # str(TaskExecutionError) does NOT carry the cause text, so a plain
    # failure-string match would MISS it — which is exactly why the transient
    # flag is captured at the source (_revise_one) rather than re-derived here.
    assert "storm" not in str(transient)

    content = _named_exc(
        "TaskExecutionError",
        "Task Ask failed in phase 'plan-review'",
        cause=ValueError("unconvergent revision content"),
    )
    assert _is_transient_runtime_failure(content) is False


def test_targeted_revision_failure_transient_defaults_false():
    f = TargetedRevisionFailure(artifact_prefix="test-plan", slug="sf-a", reason="x")
    assert f.transient is False


def test_result_has_only_transient_failures_drives_the_halt_classification():
    # No failures → not "transient-only" (nothing to classify).
    r = TargetedRevisionResult(artifact_prefix="test-plan")
    assert r.has_only_transient_failures is False

    # All failures transient (the quota-crash shape) → plan-review reports a
    # re-runnable agent-runtime halt.
    r.failed.append(
        TargetedRevisionFailure("test-plan", "sf-a", "batch 0-3 failed: quota", transient=True)
    )
    assert r.has_only_transient_failures is True

    # A genuine content failure mixed in flips it back → plan-review must report
    # a content-convergence failure (the more actionable one), not transient.
    r.failed.append(
        TargetedRevisionFailure("test-plan", "sf-b", "invalid JSON", transient=False)
    )
    assert r.has_only_transient_failures is False
