from __future__ import annotations

"""Regression coverage for the commit-hygiene drain-failure recovery plan that
decides whether a task whose durable merge-queue lane `failed` with
`failure_class == "commit_hygiene"` should be re-dispatched (with the real hook
error as feedback), escalated to a clean terminal blocker, or left unchanged.

The exact failure shape this guards against (feature 8ac124d6, DAG group 78,
items 5 / 7): the per-task agent dispatch completed and its sandbox patch was
captured, but on canonical commit the product pre-commit/husky hook rejected the
candidate (a VS Code `ensureNoDisposablesAreLeakedInTestSuite()` hygiene
violation in an agent-generated test). The durable merge queue never re-claims a
`failed` lane and the per-task resume short-circuits the `completed` marker — so
without this plan the failure is a dead-end. A `failed:commit_hygiene` lane below
budget must re-dispatch; at budget it must escalate to a terminal blocker (no
infinite loop); any other lane state is unchanged.
"""

from iriai_build_v2.workflows.develop.phases.implementation import (
    _COMMIT_HYGIENE_RERUN_MAX,
    _commit_hygiene_recovery_plan,
)


def _plan(
    lane_status: str,
    failure_class: str,
    prior_rerun_count: int,
    *,
    max_reruns: int = _COMMIT_HYGIENE_RERUN_MAX,
) -> str:
    return _commit_hygiene_recovery_plan(
        lane_status=lane_status,
        failure_class=failure_class,
        prior_rerun_count=prior_rerun_count,
        max_reruns=max_reruns,
    )


def test_failed_commit_hygiene_below_budget_reruns():
    # THE RECOVERY: a failed commit_hygiene lane with re-run budget remaining
    # must re-dispatch the task (so the agent fixes the hook violation).
    assert _plan("failed", "commit_hygiene", 0) == "rerun"
    assert _plan("failed", "commit_hygiene", _COMMIT_HYGIENE_RERUN_MAX - 1) == "rerun"


def test_failed_commit_hygiene_at_budget_escalates():
    # THE BOUND: once the per-task re-run budget is exhausted, surface a clean
    # terminal blocker instead of looping forever at the merge-queue gate.
    assert _plan("failed", "commit_hygiene", _COMMIT_HYGIENE_RERUN_MAX) == "escalate"
    assert _plan("failed", "commit_hygiene", _COMMIT_HYGIENE_RERUN_MAX + 1) == "escalate"


def test_non_commit_hygiene_failure_is_unchanged():
    # A different failure class is not this recovery's concern.
    assert _plan("failed", "merge_conflict", 0) == "none"
    assert _plan("failed", "checkpoint_contradiction", 0) == "none"
    assert _plan("failed", "", 0) == "none"


def test_non_failed_lane_is_unchanged():
    # A lane that is not terminal-failed is left to the normal resume path.
    assert _plan("integrated", "commit_hygiene", 0) == "none"
    assert _plan("done", "commit_hygiene", 0) == "none"
    assert _plan("queued", "commit_hygiene", 0) == "none"
    assert _plan("", "commit_hygiene", 0) == "none"


def test_bounded_progression_converges_to_escalation():
    # Walk the across-resume progression: each genuine actionable-feedback
    # re-failure increments the per-task counter; below the budget the plan
    # returns rerun, at/after the budget it flips to escalate and stays there —
    # it terminates rather than loops. Budget-relative so it tracks
    # `_COMMIT_HYGIENE_RERUN_MAX` instead of hard-coding the value.
    for n in range(_COMMIT_HYGIENE_RERUN_MAX):
        assert _plan("failed", "commit_hygiene", n) == "rerun"
    assert _plan("failed", "commit_hygiene", _COMMIT_HYGIENE_RERUN_MAX) == "escalate"
    assert _plan("failed", "commit_hygiene", _COMMIT_HYGIENE_RERUN_MAX + 1) == "escalate"
