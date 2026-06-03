"""Deterministic triage tests: assertion digest + classification (AC7, AC8)."""

from __future__ import annotations

from iriai_build_v2.models.outputs import TestAcceptanceCriterion
from iriai_build_v2.workflows.develop.e2e.triage import (
    assertion_digest,
    classify,
    compute_author_assertion_digests,
)


def _ac(ac_id, pass_condition, vs="", js="", description=""):
    return TestAcceptanceCriterion(
        id=ac_id, pass_condition=pass_condition,
        linked_verifiable_state_id=vs, linked_journey_step_id=js,
        description=description,
    )


def test_digest_ignores_cosmetic_description_and_wording():
    a = _ac("AC-1", "Badge shows the unread count", vs="comp#badge",
            description="Original wording")
    # cosmetic-only edits: different description, whitespace/case in pass_condition
    b = _ac("AC-1", "  badge SHOWS the unread   count ", vs="comp#badge",
            description="Totally reworded description")
    assert assertion_digest(a) == assertion_digest(b)


def test_digest_changes_on_semantic_pass_condition_change():
    a = _ac("AC-1", "Badge shows the unread count", vs="comp#badge")
    b = _ac("AC-1", "Badge shows the unread count plus a dot", vs="comp#badge")
    assert assertion_digest(a) != assertion_digest(b)


def test_digest_changes_on_linked_state_change():
    a = _ac("AC-1", "x", vs="comp#badge")
    b = _ac("AC-1", "x", vs="comp#header")
    assert assertion_digest(a) != assertion_digest(b)


# ---- AC7: classification (controlled) --------------------------------------

def test_unchanged_assertion_failure_is_regression():
    author = {"AC-1": "d1"}
    current = {"AC-1": "d1"}  # unchanged
    r = classify(author, current, "fail")
    assert r.failure_class == "regression"
    assert r.is_finding


def test_changed_assertion_with_prior_green_and_ratified_is_intended_change():
    author = {"AC-1": "d1"}
    current = {"AC-1": "d2"}  # changed
    r = classify(author, current, "fail",
                 prior_status_at_author_commit="pass", ratified=True)
    assert r.failure_class == "intended_change"
    assert not r.is_finding
    assert r.changed_ac_ids == ["AC-1"]


def test_flaky_flip_is_quarantined_not_regression():
    author = {"AC-1": "d1"}
    current = {"AC-1": "d1"}
    r = classify(author, current, "fail", flaky=True)
    assert r.failure_class == "flaky"
    assert not r.is_finding


def test_relaxation_without_ratification_is_refused():
    # assertion changed but the change was NOT ratified (two-key) -> regression
    author = {"AC-1": "d1"}
    current = {"AC-1": "d2"}
    r = classify(author, current, "fail",
                 prior_status_at_author_commit="pass", ratified=False)
    assert r.failure_class == "regression"


def test_pass_is_no_finding():
    r = classify({"AC-1": "d1"}, {"AC-1": "d1"}, "pass")
    assert r.failure_class == ""
    assert not r.is_finding


# ---- AC8: overlapping-change guard -----------------------------------------

def test_overlapping_change_prior_red_is_regression_not_laundered():
    # an AC edit AND a real regression coincide: prior spec was ALREADY red at
    # author_commit, so the assertion delta must NOT launder it to intended_change.
    author = {"AC-1": "d1"}
    current = {"AC-1": "d2"}  # assertion changed in the same window
    r = classify(author, current, "fail",
                 prior_status_at_author_commit="fail", ratified=True)
    assert r.failure_class == "regression"
    assert "overlapping" in r.reason


def test_changed_assertion_without_prior_replay_fails_closed():
    author = {"AC-1": "d1"}
    current = {"AC-1": "d2"}
    r = classify(author, current, "fail", prior_status_at_author_commit=None)
    assert r.failure_class == "regression"  # fail closed


def test_compute_author_assertion_digests():
    acs = [_ac("AC-1", "x"), _ac("AC-2", "y")]
    digs = compute_author_assertion_digests(acs)
    assert set(digs) == {"AC-1", "AC-2"}
    assert digs["AC-1"] != digs["AC-2"]
