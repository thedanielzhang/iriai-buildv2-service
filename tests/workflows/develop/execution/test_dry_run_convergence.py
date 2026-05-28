from __future__ import annotations

from types import SimpleNamespace

import pytest

from iriai_build_v2.workflows.develop.execution.dry_run_convergence import (
    classify_dry_run_blocker,
    run_dry_run_convergence,
)


@pytest.mark.asyncio
async def test_dry_run_convergence_iterates_until_clean_after_remediation() -> None:
    blocked = True
    calls: list[int] = []
    remediated = []

    async def _dry_run(iteration: int):
        calls.append(iteration)
        if blocked:
            return SimpleNamespace(
                terminal_state="workflow_blocked",
                failure=(
                    "SANDBOX_WORKFLOW_BLOCKER: "
                    "canonical_mutation=pending_durable_merge_queue"
                ),
            )
        return SimpleNamespace(terminal_state="complete", failure="")

    async def _remediate(blockers, attempt):
        nonlocal blocked
        remediated.append((attempt.iteration, blockers[0].category))
        blocked = False

    report = await run_dry_run_convergence(
        _dry_run,
        max_iterations=3,
        remediate=_remediate,
    )

    assert report.clean is True
    assert calls == [0, 1]
    assert remediated == [(0, "control_plane_resume_bug")]
    assert len(report.attempts) == 2


@pytest.mark.asyncio
async def test_dry_run_convergence_stops_with_blocker_without_remediation() -> None:
    report = await run_dry_run_convergence(
        lambda _iteration: {
            "terminal_state": "workflow_blocked",
            "failure": "contract_compile/contract_scope_conflict: legacy files widened scope",
        },
        max_iterations=3,
    )

    assert report.clean is False
    assert len(report.attempts) == 1
    assert report.blockers[0].category == "invalid_artifact_or_contract_input"


def test_dry_run_blocker_classifier_groups_common_pause_classes() -> None:
    assert (
        classify_dry_run_blocker("canonical_mutation=pending_durable_merge_queue")
        == "control_plane_resume_bug"
    )
    assert (
        classify_dry_run_blocker("Runtime dispatch failed (provider_error)")
        == "external_runtime_dependency"
    )
    assert (
        classify_dry_run_blocker("pytest tests/example.py -q failed")
        == "product_implementation_failure"
    )

