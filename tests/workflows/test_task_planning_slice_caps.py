"""Env-tunable slice-context caps (defect N-2).

Regression tests for the S1 task-planning block: the slice-context caps were
hardcoded (_SLICE_CONTEXT_SOFT_CAP_BYTES = 180_000), so S1's 187,022-byte
target-only package (peer=0) was over budget on EVERY retry mode and the
phase blocked after strategy exhaustion. The caps are now read at module
import from IRIAI_TASK_PLANNING_SLICE_CONTEXT_CAP_BYTES /
IRIAI_TASK_PLANNING_SLICE_PEER_CAP_BYTES (defaults unchanged), and the
over-budget attempt error carries the full size_breakdown so the next
overflow is diagnosable.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from iriai_build_v2.workflows.planning.phases import task_planning as tp
from iriai_build_v2.workflows.planning.phases.task_planning import (
    TaskPlanningPhase,
    _cap_bytes_from_env,
)

CONTEXT_CAP_ENV = "IRIAI_TASK_PLANNING_SLICE_CONTEXT_CAP_BYTES"
PEER_CAP_ENV = "IRIAI_TASK_PLANNING_SLICE_PEER_CAP_BYTES"


class TestCapBytesFromEnv:
    def test_default_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(CONTEXT_CAP_ENV, raising=False)
        assert _cap_bytes_from_env(CONTEXT_CAP_ENV, 180_000) == 180_000

    def test_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(CONTEXT_CAP_ENV, "260000")
        assert _cap_bytes_from_env(CONTEXT_CAP_ENV, 180_000) == 260_000

    def test_invalid_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(CONTEXT_CAP_ENV, "not-a-number")
        assert _cap_bytes_from_env(CONTEXT_CAP_ENV, 180_000) == 180_000

    def test_non_positive_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The budget guard must never be silently disabled via 0/-1."""
        monkeypatch.setenv(CONTEXT_CAP_ENV, "0")
        assert _cap_bytes_from_env(CONTEXT_CAP_ENV, 180_000) == 180_000
        monkeypatch.setenv(CONTEXT_CAP_ENV, "-5")
        assert _cap_bytes_from_env(CONTEXT_CAP_ENV, 180_000) == 180_000


def _module_caps_in_subprocess(extra_env: dict[str, str]) -> list[int]:
    """Import task_planning in a fresh interpreter and report the module
    constants — proves the module-level wiring, not just the helper."""
    code = (
        "import json\n"
        "from iriai_build_v2.workflows.planning.phases import task_planning as tp\n"
        "print(json.dumps([tp._SLICE_CONTEXT_SOFT_CAP_BYTES, tp._SLICE_PEER_CAP_BYTES]))\n"
    )
    env = {k: v for k, v in os.environ.items() if k not in (CONTEXT_CAP_ENV, PEER_CAP_ENV)}
    env.update(extra_env)
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        check=True,
        timeout=120,
    )
    return json.loads(proc.stdout.strip().splitlines()[-1])


class TestModuleLevelCapWiring:
    def test_defaults_without_env(self) -> None:
        assert _module_caps_in_subprocess({}) == [180_000, 60_000]

    def test_env_override(self) -> None:
        caps = _module_caps_in_subprocess({
            CONTEXT_CAP_ENV: "260000",
            PEER_CAP_ENV: "90000",
        })
        assert caps == [260_000, 90_000]
        # The S1 launch scenario: a 187,022-byte target-only package must fit.
        assert caps[0] > 187_022


class TestOverBudgetPredicateAndError:
    def test_raised_cap_admits_s1_sized_package(self, monkeypatch: pytest.MonkeyPatch) -> None:
        s1_total = 187_022
        breakdown = {"target": s1_total, "peer": 0}
        # With the old hardcoded cap S1 was over budget even target-only…
        monkeypatch.setattr(tp, "_SLICE_CONTEXT_SOFT_CAP_BYTES", 180_000)
        assert TaskPlanningPhase._slice_context_over_budget(
            s1_total, breakdown, mode_label="target-only",
        )
        # …with the env-raised cap it dispatches.
        monkeypatch.setattr(tp, "_SLICE_CONTEXT_SOFT_CAP_BYTES", 260_000)
        assert not TaskPlanningPhase._slice_context_over_budget(
            s1_total, breakdown, mode_label="target-only",
        )

    def test_peer_cap_still_enforced_for_non_target_modes(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(tp, "_SLICE_CONTEXT_SOFT_CAP_BYTES", 260_000)
        monkeypatch.setattr(tp, "_SLICE_PEER_CAP_BYTES", 60_000)
        breakdown = {"target": 50_000, "peer": 70_000}
        assert TaskPlanningPhase._slice_context_over_budget(
            120_000, breakdown, mode_label="all-workstream-peers",
        )
        assert not TaskPlanningPhase._slice_context_over_budget(
            120_000, breakdown, mode_label="target-only",
        )

    def test_error_message_carries_full_breakdown(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(tp, "_SLICE_CONTEXT_SOFT_CAP_BYTES", 180_000)
        monkeypatch.setattr(tp, "_SLICE_PEER_CAP_BYTES", 60_000)
        breakdown = {"target": 150_000, "peer": 30_000, "decisions": 7_022}
        message = TaskPlanningPhase._slice_over_budget_error(187_022, breakdown)
        assert "187022 bytes" in message
        assert "peer=30000" in message
        assert "cap=180000" in message
        assert "peer_cap=60000" in message
        # Every layer of the breakdown must be present for diagnosability.
        assert "decisions=7022" in message
        assert "target=150000" in message
