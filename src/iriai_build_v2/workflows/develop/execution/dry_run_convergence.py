"""Developer-only dry-run convergence helpers for workflow resume blockers.

The helpers in this module do not mutate workflow state. They run a supplied
dry-run callable, classify blocker/pause text, and optionally let a caller run
an external remediation step before the next fresh dry-run pass.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal


DryRunBlockerCategory = Literal[
    "control_plane_resume_bug",
    "invalid_artifact_or_contract_input",
    "product_implementation_failure",
    "external_runtime_dependency",
    "unknown",
]


_BLOCKER_NEEDLES = (
    "SANDBOX_WORKFLOW_BLOCKER",
    "workflow_blocked",
    "Workflow paused",
    "paused in phase",
    "canonical_mutation=pending_durable_merge_queue",
)


@dataclass(frozen=True)
class DryRunBlocker:
    message: str
    category: DryRunBlockerCategory


@dataclass(frozen=True)
class DryRunAttempt:
    iteration: int
    terminal_state: str
    blockers: tuple[DryRunBlocker, ...]


@dataclass(frozen=True)
class DryRunConvergenceReport:
    clean: bool
    attempts: tuple[DryRunAttempt, ...] = field(default_factory=tuple)

    @property
    def blockers(self) -> tuple[DryRunBlocker, ...]:
        if not self.attempts:
            return ()
        return self.attempts[-1].blockers


DryRunCallable = Callable[[int], Any | Awaitable[Any]]
DryRunRemediator = Callable[
    [Sequence[DryRunBlocker], DryRunAttempt],
    Any | Awaitable[Any],
]


async def run_dry_run_convergence(
    dry_run: DryRunCallable,
    *,
    max_iterations: int = 1,
    remediate: DryRunRemediator | None = None,
) -> DryRunConvergenceReport:
    """Run fresh dry-run attempts until no blockers remain.

    The function never edits repository or workflow state directly. When a
    ``remediate`` callback is supplied, that callback owns any mutation between
    iterations; the next dry-run is always invoked from scratch.
    """

    if max_iterations < 1:
        raise ValueError("max_iterations must be >= 1")
    attempts: list[DryRunAttempt] = []
    for iteration in range(max_iterations):
        outcome = dry_run(iteration)
        if inspect.isawaitable(outcome):
            outcome = await outcome
        attempt = dry_run_attempt_from_outcome(iteration, outcome)
        attempts.append(attempt)
        if not attempt.blockers:
            return DryRunConvergenceReport(clean=True, attempts=tuple(attempts))
        if remediate is None or iteration + 1 >= max_iterations:
            return DryRunConvergenceReport(clean=False, attempts=tuple(attempts))
        remediation = remediate(attempt.blockers, attempt)
        if inspect.isawaitable(remediation):
            await remediation
    return DryRunConvergenceReport(clean=False, attempts=tuple(attempts))


def dry_run_attempt_from_outcome(iteration: int, outcome: Any) -> DryRunAttempt:
    terminal_state = _outcome_terminal_state(outcome)
    text = _outcome_text(outcome)
    blockers = tuple(
        DryRunBlocker(message=message, category=classify_dry_run_blocker(message))
        for message in _extract_blocker_messages(text, terminal_state)
    )
    return DryRunAttempt(
        iteration=iteration,
        terminal_state=terminal_state,
        blockers=blockers,
    )


def classify_dry_run_blocker(message: str) -> DryRunBlockerCategory:
    text = message.lower()
    if (
        "canonical_mutation=pending_durable_merge_queue" in text
        or "context_materialization_failed" in text
        or "sandbox binding" in text
        or "workspaceauthority" in text
        or "stale" in text
        and "resume" in text
    ):
        return "control_plane_resume_bug"
    if (
        "contract_compile" in text
        or "contract_scope_conflict" in text
        or "contract_invalid_path" in text
        or "contract_unknown_criterion" in text
        or "invalid artifact" in text
    ):
        return "invalid_artifact_or_contract_input"
    if (
        "provider_error" in text
        or "timeout" in text
        or "rate limit" in text
        or "external" in text
        or "slack" in text
    ):
        return "external_runtime_dependency"
    if (
        "pytest" in text
        or "ruff" in text
        or "verification failed" in text
        or "product verification" in text
    ):
        return "product_implementation_failure"
    return "unknown"


def _extract_blocker_messages(text: str, terminal_state: str) -> list[str]:
    if not text and terminal_state not in {"workflow_blocked", "quiesced"}:
        return []
    if terminal_state in {"workflow_blocked", "quiesced"}:
        return [text or terminal_state]
    if any(needle in text for needle in _BLOCKER_NEEDLES):
        return [text]
    return []


def _outcome_terminal_state(outcome: Any) -> str:
    if isinstance(outcome, dict):
        return str(outcome.get("terminal_state") or outcome.get("status") or "")
    return str(getattr(outcome, "terminal_state", "") or getattr(outcome, "status", "") or "")


def _outcome_text(outcome: Any) -> str:
    if isinstance(outcome, str):
        return outcome
    if isinstance(outcome, dict):
        parts = [
            outcome.get("failure"),
            outcome.get("reason"),
            outcome.get("message"),
            outcome.get("summary"),
        ]
    else:
        parts = [
            getattr(outcome, "failure", None),
            getattr(outcome, "reason", None),
            getattr(outcome, "message", None),
            getattr(outcome, "summary", None),
        ]
    return "\n".join(str(part) for part in parts if part)

