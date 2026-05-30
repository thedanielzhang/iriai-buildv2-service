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


def test_format_prior_attempts_trims_608kb_class_payload_below_window():
    # Regression: a 608KB-class prior-attempts payload (well under the old
    # 1_000_000-char cap) overflowed the model window and wedged the RCA agent
    # in error_max_structured_output_retries. Each attempt body is repeated
    # across three fields, so 50_000-char bodies make ~150KB blocks; ten of them
    # is ~1.5MB raw. The tightened cap must trim this to a single recent block
    # (~150KB), well below the window-danger size — this assertion FAILS if the
    # cap is loosened back toward the 1M value that shipped the dead-stall.
    out = _format_prior_attempts([_attempt(n, body="z" * 50_000) for n in range(1, 11)])
    assert "Attempt 10 (BUG-10)" in out  # newest always kept
    assert "older omitted" in out  # nothing silently dropped
    assert len(out) < 300_000
    assert _MAX_PRIOR_ATTEMPTS_CHARS <= 250_000  # lock the intent, not just the effect
