"""Item-10 (R3) e2e-side green-oracle prerequisite tests.

(a) triage.classify wired (classify_verdicts), (b) critical_for threading
(bind + compose run_to_verdicts + compose_critical_for), (c) strict green
oracle (failures/errors/skipped count). Every flag OFF = today, asserted.
"""

from __future__ import annotations

import pytest

from iriai_build_v2.workflows.develop.e2e.adapters.compose import (
    compose_critical_for,
    run_to_verdicts,
)
from iriai_build_v2.workflows.develop.e2e.models import (
    E2ESpecRecord,
    E2EVerdictRecord,
    ProjectProfile,
)
from iriai_build_v2.workflows.develop.e2e.pass_ import (
    _scenario_critical_for,
    _strict_green_counts,
    boundary_repair_enabled,
    critical_binding_enabled,
    triage_classify_enabled,
)
from iriai_build_v2.workflows.develop.e2e.status import (
    build_status,
    green_pointer_for,
    material_digest,
)
from iriai_build_v2.workflows.develop.e2e.triage import (
    assertion_digest,
    classify_verdicts,
)


class _AC:
    def __init__(self, id: str, pass_condition: str) -> None:
        self.id = id
        self.pass_condition = pass_condition
        self.linked_verifiable_state_id = ""
        self.linked_journey_step_id = ""


class _Checkpoint:
    group_idx = 4

    def result_commits(self):
        return {"repo": "abc123"}


def _v(**kw) -> E2EVerdictRecord:
    kw.setdefault("spec_id", "s1")
    return E2EVerdictRecord(**kw)


# ── (a) classify_verdicts ───────────────────────────────────────────────────


def test_classify_plain_fail_unbound_becomes_regression():
    out = classify_verdicts([_v(status="fail")], {}, {})
    assert out[0].failure_class == "regression"


def test_classify_fail_unchanged_assertions_regression():
    ac = _AC("AC-1", "must show total")
    spec = E2ESpecRecord(
        spec_id="s1", linked_ac_ids=["AC-1"],
        author_assertion_digests={"AC-1": assertion_digest(ac)},
    )
    out = classify_verdicts([_v(status="fail")], {"s1": spec}, {"AC-1": ac})
    assert out[0].failure_class == "regression"
    assert out[0].changed_ac_ids == []


def test_classify_fail_changed_assertion_fails_closed_without_prior():
    authored = _AC("AC-1", "must show total")
    edited = _AC("AC-1", "must show subtotal instead")
    spec = E2ESpecRecord(
        spec_id="s1", linked_ac_ids=["AC-1"],
        author_assertion_digests={"AC-1": assertion_digest(authored)},
    )
    out = classify_verdicts([_v(status="fail")], {"s1": spec}, {"AC-1": edited})
    # prior replay unavailable in the pass flow => fail closed = regression
    assert out[0].failure_class == "regression"
    assert out[0].changed_ac_ids == ["AC-1"]


def test_classify_preserves_flaky_and_nonfail():
    out = classify_verdicts(
        [
            _v(status="fail", failure_class="flaky"),
            _v(status="pass"),
            _v(status="error", failure_class="infra"),
            _v(status="skipped"),
        ],
        {}, {},
    )
    assert [v.failure_class for v in out] == ["flaky", "", "infra", ""]
    assert [v.status for v in out] == ["fail", "pass", "error", "skipped"]


# ── (b) critical threading ──────────────────────────────────────────────────


class _T:
    def __init__(self, title, status, flaky=False, error=""):
        self.title, self.status, self.flaky, self.error = title, status, flaky, error


class _Run:
    web_server_ok = True
    started = True
    global_errors: list[str] = []

    def __init__(self, tests):
        self.tests = tests


def test_run_to_verdicts_critical_kwarg_stamps_all_verdicts():
    run = _Run([_T("a", "passed"), _T("b", "failed")])
    out = run_to_verdicts(run, suite="svc", critical=True)
    assert all(v.critical for v in out)
    out_default = run_to_verdicts(run, suite="svc")
    assert not any(v.critical for v in out_default)  # parity: default False


def test_compose_critical_for_profile_predicate():
    profile = ProjectProfile(critical_service_names=["spend-api"])
    pred = compose_critical_for(profile)
    assert pred("spend-api") is True
    assert pred("mobile") is False
    empty = compose_critical_for(ProjectProfile())
    assert empty("spend-api") is False  # default [] = no critical suite (today)


def test_scenario_critical_for_p0_only():
    class _Sc:
        def __init__(self, priority):
            self.priority = priority

    assert _scenario_critical_for(_Sc("p0")) == (True, "test-plan p0 scenario")
    assert _scenario_critical_for(_Sc("P0"))[0] is True
    assert _scenario_critical_for(_Sc("p1"))[0] is False
    assert _scenario_critical_for(_Sc(""))[0] is False


# ── (c) strict green oracle ─────────────────────────────────────────────────


def test_build_status_counts_errors_and_skipped():
    verdicts = [
        _v(status="pass"),
        _v(status="fail", failure_class="regression", spec_id="r1"),
        _v(status="error", failure_class="infra"),
        _v(status="skipped"),
    ]
    status = build_status(checkpoint=_Checkpoint(), smokes=[], verdicts=verdicts)
    assert status.errors == 1
    assert status.skipped == 1
    assert status.failed == 1  # regression-class fail counting unchanged


def test_strict_green_counts_helper():
    verdicts = [
        _v(status="fail"),  # unclassified fail still counts (the (c) hole)
        _v(status="fail", failure_class="regression"),
        _v(status="fail", failure_class="flaky"),
        _v(status="fail", failure_class="intended_change"),
        _v(status="error"),
        _v(status="skipped"),
        _v(status="pass"),
    ]
    counts = _strict_green_counts(verdicts)
    assert counts == {"open_failures": 2, "open_errors": 1, "open_skipped": 1}


def test_green_pointer_flag_off_ignores_failures(monkeypatch):
    monkeypatch.delenv("IRIAI_E2E_STRICT_GREEN", raising=False)
    gp = green_pointer_for(
        _Checkpoint(), boot_smoke="pass", open_critical_regressions=0,
        open_failures=7, open_errors=3, open_skipped=2,
    )
    assert gp is not None and gp.group_idx == 4  # parity: today's oracle


def test_green_pointer_strict_blocks_on_failures_errors_skipped(monkeypatch):
    monkeypatch.setenv("IRIAI_E2E_STRICT_GREEN", "1")
    base = dict(boot_smoke="pass", open_critical_regressions=0)
    assert green_pointer_for(_Checkpoint(), **base) is not None  # all clean
    for kw in ({"open_failures": 1}, {"open_errors": 1}, {"open_skipped": 1}):
        assert green_pointer_for(_Checkpoint(), **base, **kw) is None
    # boot/critical conditions still hold under strict
    assert green_pointer_for(
        _Checkpoint(), boot_smoke="fail", open_critical_regressions=0,
    ) is None


def test_material_digest_parity_flag_off(monkeypatch):
    monkeypatch.delenv("IRIAI_E2E_STRICT_GREEN", raising=False)
    a = build_status(checkpoint=_Checkpoint(), smokes=[], verdicts=[])
    b = build_status(
        checkpoint=_Checkpoint(), smokes=[],
        verdicts=[_v(status="error"), _v(status="skipped")],
    )
    assert material_digest(a) == material_digest(b)  # OFF: errors/skipped not material
    monkeypatch.setenv("IRIAI_E2E_STRICT_GREEN", "1")
    assert material_digest(a) != material_digest(b)  # ON: they flip green => material


# ── flag defaults (all OFF = today) ─────────────────────────────────────────


@pytest.mark.parametrize("fn,env", [
    (triage_classify_enabled, "IRIAI_E2E_TRIAGE_CLASSIFY"),
    (critical_binding_enabled, "IRIAI_E2E_CRITICAL_BINDING"),
    (boundary_repair_enabled, "IRIAI_E2E_BOUNDARY_REPAIR"),
])
def test_flags_default_off(monkeypatch, fn, env):
    monkeypatch.delenv(env, raising=False)
    assert fn() is False
    monkeypatch.setenv(env, "1")
    assert fn() is True
