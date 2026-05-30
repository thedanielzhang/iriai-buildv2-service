from __future__ import annotations

from iriai_build_v2.models.outputs import BugFixAttempt
from iriai_build_v2.workflows.develop.phases.implementation import (
    _MAX_PRIOR_ATTEMPTS_CHARS,
    _MAX_PRIOR_ATTEMPTS_SHOWN,
    _format_prior_attempts,
)


def _attempt(n: int, body: str = "x") -> BugFixAttempt:
    return BugFixAttempt(
        bug_id=f"BUG-{n}",
        source_verdict="code_reviewer",
        description=body,
        root_cause=body,
        fix_applied=body,
        re_verify_result="FAIL",
        attempt_number=n,
    )


def test_format_prior_attempts_empty():
    assert _format_prior_attempts([]) == ""


def test_format_prior_attempts_under_cap_includes_all():
    out = _format_prior_attempts([_attempt(n) for n in range(1, 6)])
    assert out.count("### Attempt") == 5
    assert "older omitted" not in out


def test_format_prior_attempts_caps_count_and_keeps_most_recent():
    # 99 small attempts: the count cap is the binding limit.
    out = _format_prior_attempts([_attempt(n) for n in range(1, 100)])
    assert out.count("### Attempt") == _MAX_PRIOR_ATTEMPTS_SHOWN
    assert "Attempt 99 (BUG-99)" in out  # newest kept
    assert "Attempt 1 (BUG-1)" not in out  # oldest dropped
    assert "older omitted" in out
    assert "of 99 attempts" in out


def test_format_prior_attempts_caps_chars():
    # Huge per-attempt bodies: the char cap (not the count) is the binding limit.
    big = "z" * 60_000
    out = _format_prior_attempts([_attempt(n, body=big) for n in range(1, 60)])
    assert "Attempt 59 (BUG-59)" in out  # newest always present
    assert out.count("### Attempt") < 59  # not all included
    # Stays within the char budget plus at most one extra block of headroom.
    assert len(out) <= _MAX_PRIOR_ATTEMPTS_CHARS + 200_000
