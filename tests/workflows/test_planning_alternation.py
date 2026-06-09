"""Tests for the deterministic planning runtime-alternation tagger.

Covers the operator requirements for the opt-in ``alternating`` policy:

* deterministic + ~50/50 over a stable id set, and IDEMPOTENT across a
  simulated resume (same key set + shuffled call order → same assignment);
* under ``single_agent_runtime`` / no-secondary, NOTHING is tagged secondary;
* a non-alternating policy keeps everything on the primary;
* the per-runner convenience wrapper honors the runner's services policy +
  runtime names.
"""

from __future__ import annotations

import random

import pytest

from iriai_build_v2.runtime_policy import (
    DEFAULT_RUNTIME_POLICY,
    PRIMARY_IMPL_SECONDARY_REVIEW_POLICY,
)
from iriai_build_v2.workflows.planning._alternation import (
    PRIMARY_ONLY_PLANNING_STEPS,
    alternating_runtime_for,
    planning_alternation_runtime,
    secondary_alternation_enabled,
)


def _assign(keys, **kw):
    defaults = dict(
        runtime_policy="alternating",
        primary_runtime_name="claude",
        secondary_runtime_name="codex",
    )
    defaults.update(kw)
    return {k: alternating_runtime_for(k, ordered_keys=keys, **defaults) for k in keys}


def test_alternating_is_deterministic_and_balanced():
    keys = [f"sf-{i:02d}" for i in range(20)]
    out = _assign(keys)
    counts = {
        "primary": list(out.values()).count("primary"),
        "secondary": list(out.values()).count("secondary"),
    }
    # Exact 50/50 over an even, stable key set.
    assert counts["primary"] == counts["secondary"] == 10
    # Running again yields identical assignment (pure function of key set).
    assert _assign(keys) == out


def test_alternating_balanced_within_one_for_odd_set():
    keys = [f"edge-{i}" for i in range(7)]
    out = _assign(keys)
    secondary = list(out.values()).count("secondary")
    primary = list(out.values()).count("primary")
    assert abs(primary - secondary) <= 1
    assert primary + secondary == 7


def test_idempotent_across_simulated_resume_with_shuffled_call_order():
    keys = [f"sf-{i:02d}" for i in range(15)]
    first = _assign(keys)
    # A resumed run re-derives the key set in a different iteration/call order;
    # the assignment must NOT depend on call order, only on the stable key set.
    shuffled = list(keys)
    random.Random(1234).shuffle(shuffled)
    resumed = {
        k: alternating_runtime_for(
            k,
            ordered_keys=shuffled,
            runtime_policy="alternating",
            primary_runtime_name="claude",
            secondary_runtime_name="codex",
        )
        for k in shuffled
    }
    assert resumed == first


def test_single_runtime_tags_nothing_secondary():
    # single_agent_runtime → bootstrap builds secondary with SAME name as primary.
    keys = [f"sf-{i}" for i in range(10)]
    out = _assign(keys, secondary_runtime_name="claude")
    assert set(out.values()) == {"primary"}


def test_no_secondary_tags_nothing_secondary():
    keys = [f"sf-{i}" for i in range(10)]
    out = _assign(keys, secondary_runtime_name=None)
    assert set(out.values()) == {"primary"}
    out_empty = _assign(keys, secondary_runtime_name="")
    assert set(out_empty.values()) == {"primary"}


def test_non_alternating_policy_keeps_everything_primary():
    keys = [f"sf-{i}" for i in range(10)]
    out = _assign(keys, runtime_policy=PRIMARY_IMPL_SECONDARY_REVIEW_POLICY)
    assert set(out.values()) == {"primary"}


def test_primary_only_step_exclude_list_is_respected():
    # The exclude list defaults empty (everything distributed), but the
    # mechanism must force primary when a step is excluded.
    keys = ["sf-a", "sf-b"]
    # sf-b is index 1 → would normally be secondary.
    assert (
        alternating_runtime_for(
            "sf-b",
            ordered_keys=keys,
            runtime_policy="alternating",
            primary_runtime_name="claude",
            secondary_runtime_name="codex",
            step="__forced_primary__",
        )
        == "secondary"
    ), "control: an unknown step should still alternate"

    # Monkeypatch-style: add a step to the exclude frozenset locally by patching.
    import iriai_build_v2.workflows.planning._alternation as alt

    original = alt.PRIMARY_ONLY_PLANNING_STEPS
    try:
        alt.PRIMARY_ONLY_PLANNING_STEPS = frozenset({"locked"})
        assert (
            alt.alternating_runtime_for(
                "sf-b",
                ordered_keys=keys,
                runtime_policy="alternating",
                primary_runtime_name="claude",
                secondary_runtime_name="codex",
                step="locked",
            )
            == "primary"
        )
    finally:
        alt.PRIMARY_ONLY_PLANNING_STEPS = original


def test_unknown_key_falls_back_to_primary():
    out = alternating_runtime_for(
        "not-in-set",
        ordered_keys=["sf-a", "sf-b"],
        runtime_policy="alternating",
        primary_runtime_name="claude",
        secondary_runtime_name="codex",
    )
    assert out == "primary"


def test_secondary_alternation_enabled_gate():
    assert secondary_alternation_enabled(
        runtime_policy="alternating",
        primary_runtime_name="claude",
        secondary_runtime_name="codex",
    )
    # codex-review alias → not the alternating default → disabled.
    assert not secondary_alternation_enabled(
        runtime_policy="codex-review",
        primary_runtime_name="claude",
        secondary_runtime_name="codex",
    )
    # same name primary/secondary → no real secondary → disabled.
    assert not secondary_alternation_enabled(
        runtime_policy="alternating",
        primary_runtime_name="codex",
        secondary_runtime_name="codex",
    )


class _FakeRuntime:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeRunner:
    def __init__(self, primary: str, secondary: str | None, policy: str) -> None:
        self.agent_runtime = _FakeRuntime(primary)
        self.secondary_runtime = _FakeRuntime(secondary) if secondary else None
        self.services = {"runtime_policy": policy}


def test_planning_alternation_runtime_wrapper_uses_runner_state():
    keys = [f"sf-{i}" for i in range(6)]
    runner = _FakeRunner("claude", "codex", DEFAULT_RUNTIME_POLICY)
    out = {
        k: planning_alternation_runtime(runner, key=k, ordered_keys=keys)
        for k in keys
    }
    assert list(out.values()).count("secondary") == 3

    # claude-only runner (same secondary name) → all primary.
    runner_single = _FakeRunner("claude", "claude", DEFAULT_RUNTIME_POLICY)
    out_single = {
        k: planning_alternation_runtime(runner_single, key=k, ordered_keys=keys)
        for k in keys
    }
    assert set(out_single.values()) == {"primary"}


def test_exclude_list_is_empty_by_default():
    # The operator wants planning fully distributed; nothing pinned by default.
    assert PRIMARY_ONLY_PLANNING_STEPS == frozenset()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
