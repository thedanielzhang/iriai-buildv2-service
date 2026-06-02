"""Unit tests for compute_testable_acs_for_tasks + scenario selection."""

from __future__ import annotations

from types import SimpleNamespace

from iriai_build_v2.models.outputs import (
    TestAcceptanceCriterion,
    TestPlan,
    TestScenario,
)
from iriai_build_v2.workflows.develop.e2e.selection import (
    compute_testable_acs_for_tasks,
    select,
    select_testable_scenarios,
)


def _ac(ac_id, method):
    return TestAcceptanceCriterion(id=ac_id, verification_method=method)


def _task(task_id, gates):
    return SimpleNamespace(id=task_id, verification_gates=gates)


def _plan():
    return TestPlan(
        acceptance_criteria=[
            _ac("AC-badge-1", "e2e"),
            _ac("AC-badge-2", "visual"),
            _ac("AC-chat-1", "integration"),
            _ac("AC-unit-1", "unit"),  # not testable by e2e track
            _ac("AC-manual-1", "manual"),
            _ac("AC-future-1", "e2e"),  # gated by a not-done task
            _ac("AC-waived-1", "e2e"),
        ],
        test_scenarios=[
            TestScenario(id="S-badge", linked_acceptance=["AC-badge-1", "AC-badge-2"]),
            TestScenario(id="S-chat", linked_acceptance=["AC-chat-1"]),
            # links a non-testable AC -> not subset -> excluded
            TestScenario(id="S-mixed", linked_acceptance=["AC-badge-1", "AC-unit-1"]),
            TestScenario(id="S-empty", linked_acceptance=[]),
        ],
    )


def _tasks():
    return [
        _task("T1", ["AC-badge-1", "AC-badge-2"]),
        _task("T2", ["AC-chat-1", "AC-unit-1", "AC-manual-1", "AC-waived-1"]),
        _task("T9", ["AC-future-1"]),  # NOT done
    ]


CONTRACT = SimpleNamespace(
    canonical_ac_ids=[
        "AC-badge-1", "AC-badge-2", "AC-chat-1", "AC-unit-1",
        "AC-manual-1", "AC-waived-1",
    ],
    global_obligation_ac_ids=[],
    waived_ac_ids=["AC-waived-1"],
)


def test_only_done_gated_testable_methods_selected():
    acs = compute_testable_acs_for_tasks(
        _plan(), CONTRACT, done_task_ids={"T1", "T2"}, tasks=_tasks()
    )
    ids = {a.id for a in acs}
    assert ids == {"AC-badge-1", "AC-badge-2", "AC-chat-1"}
    # unit/manual excluded, future (not-done) excluded, waived excluded
    assert "AC-unit-1" not in ids and "AC-manual-1" not in ids
    assert "AC-future-1" not in ids and "AC-waived-1" not in ids


def test_obligation_filter_excludes_non_canonical():
    contract = SimpleNamespace(
        canonical_ac_ids=["AC-badge-1"],  # only badge-1 is an obligation
        global_obligation_ac_ids=[],
        waived_ac_ids=[],
    )
    acs = compute_testable_acs_for_tasks(
        _plan(), contract, done_task_ids={"T1", "T2"}, tasks=_tasks()
    )
    assert {a.id for a in acs} == {"AC-badge-1"}


def test_global_obligation_included():
    contract = SimpleNamespace(
        canonical_ac_ids=[],
        global_obligation_ac_ids=["AC-chat-1"],
        waived_ac_ids=[],
    )
    acs = compute_testable_acs_for_tasks(
        _plan(), contract, done_task_ids={"T1", "T2"}, tasks=_tasks()
    )
    assert {a.id for a in acs} == {"AC-chat-1"}


def test_no_contract_skips_obligation_filter():
    acs = compute_testable_acs_for_tasks(
        _plan(), None, done_task_ids={"T1", "T2"}, tasks=_tasks()
    )
    # without a contract there is no waived/obligation filter, so the done-gated
    # e2e/visual/integration ACs include AC-waived-1 too (unit/manual excluded).
    assert {a.id for a in acs} == {
        "AC-badge-1", "AC-badge-2", "AC-chat-1", "AC-waived-1",
    }


def test_scenario_subset_selection():
    testable = {"AC-badge-1", "AC-badge-2", "AC-chat-1"}
    scs = select_testable_scenarios(_plan(), testable)
    ids = {s.id for s in scs}
    assert ids == {"S-badge", "S-chat"}
    # mixed (links non-testable) and empty are excluded
    assert "S-mixed" not in ids and "S-empty" not in ids


def test_select_combines_acs_and_scenarios():
    res = select(_plan(), CONTRACT, done_task_ids={"T1", "T2"}, tasks=_tasks())
    assert res.testable_ac_ids == {"AC-badge-1", "AC-badge-2", "AC-chat-1"}
    assert {s.id for s in res.scenarios} == {"S-badge", "S-chat"}
