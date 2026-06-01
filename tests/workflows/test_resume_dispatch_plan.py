from __future__ import annotations

"""Regression coverage for the strict-resume dispatch plan that decides whether a
task whose newest terminal dispatch SUCCEEDED at the process level should be
replayed, re-run fresh, or surfaced as a terminal blocker.

The exact failure shape this guards against (feature 8ac124d6, DAG group 78,
TASK-9-3): the agent's dispatch SUCCEEDED at the process level but it
self-reported a `partial` deliverable, so capture-time contract validation never
ran. The old L1 resume optimization replayed that attempt by its idempotency key,
reproducing the partial result, which the durable merge queue then refused — an
infinite resume->reject loop. A `partial` (non-`completed`) marker must instead
trigger a GENUINE fresh dispatch past the superseding success, bounded so a task
that never converges escalates with a clean terminal blocker.
"""

from iriai_build_v2.workflows.develop.phases.implementation import (
    _PARTIAL_RESUME_RERUN_HEADROOM,
    _resume_dispatch_plan_for_succeeded_terminal,
)


def _plan(succeeded_attempt: int, marker_status: str, *, infra_max_retries: int = 5):
    return _resume_dispatch_plan_for_succeeded_terminal(
        succeeded_attempt=succeeded_attempt,
        marker_status=marker_status,
        infra_max_retries=infra_max_retries,
        partial_rerun_headroom=_PARTIAL_RESUME_RERUN_HEADROOM,
    )


def test_completed_marker_replays_the_succeeded_attempt():
    # A `completed` deliverable is honored by replaying exactly its attempt index
    # (skips the superseded stale infra-failures) — unchanged behavior.
    plan, attempts = _plan(5, "completed")
    assert plan == "replay"
    assert attempts == (5,)


def test_partial_marker_reruns_fresh_instead_of_replaying():
    # THE BUG FIX: a `partial` marker must NOT replay the succeeded attempt
    # (which would reproduce the partial the merge queue refuses). It must
    # dispatch a FRESH attempt index past the success.
    plan, attempts = _plan(5, "partial")
    assert plan == "rerun"
    # Fresh indices start strictly AFTER the succeeded attempt — never a replay
    # of index 5, and never the stale infra-failures at indices 0..4.
    assert list(attempts) == [6, 7]
    assert 5 not in list(attempts)
    assert min(attempts) == 6


def test_missing_marker_is_treated_like_non_completed_and_reruns():
    plan, attempts = _plan(5, "")
    assert plan == "rerun"
    assert min(attempts) == 6


def test_partial_rerun_budget_is_bounded_and_converges_to_terminal_blocker():
    # Walk the across-resume progression for the TASK-9-3 shape
    # (infra_max_retries=5, headroom=2 -> max attempt index 7). Each resume sees
    # the newest succeeded attempt advance by one; the plan must terminate
    # rather than loop forever at the merge-queue gate.
    plan5, attempts5 = _plan(5, "partial")
    assert plan5 == "rerun" and list(attempts5) == [6, 7]

    plan6, attempts6 = _plan(6, "partial")
    assert plan6 == "rerun" and list(attempts6) == [7]

    # Budget exhausted: no fresh index remains -> terminal blocker, not a replay
    # and not an empty re-run range.
    plan7, attempts7 = _plan(7, "partial")
    assert plan7 == "blocked"
    assert attempts7 is None


def test_completed_marker_replays_even_past_the_rerun_budget():
    # A genuinely `completed` task is always honored, regardless of attempt index.
    plan, attempts = _plan(9, "completed")
    assert plan == "replay"
    assert attempts == (9,)
