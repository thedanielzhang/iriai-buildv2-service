"""Deterministic, resumable runtime alternation for planning fan-out.

The ``alternating`` runtime policy (``runtime_policy.py``) is meant to spread
agent work ~50/50 across a PRIMARY (Claude) and a SECONDARY (Codex) runtime.
For the ``develop`` workflow this is already implemented by the DAG-group
round-robin in ``develop/execution/dispatcher.py``.  Planning historically ran
every *lead* actor (PM / designer / architect / test-planner / reviewers) on
the primary runtime and only routed the *shadow* agent-fill responders to the
secondary, so a planning run is effectively Claude-only and keeps hitting the
Claude usage cap.

This module adds an ADDITIVE, opt-in tagger that the planning phases consult
to decide whether a given fan-out work item runs on ``"primary"`` or
``"secondary"``.  It is:

* **Gated** — only alternates when the active policy is ``"alternating"`` AND a
  real secondary runtime exists (its name differs from the primary's).  Under
  ``single_agent_runtime`` / no-secondary the function always returns
  ``"primary"`` so behavior is bit-for-bit unchanged.
* **Deterministic + resumable** — the runtime for a work item is a pure
  function of a STABLE key set (e.g. the sorted subfeature slugs) and the
  item's key, NOT call order or randomness.  A resumed run re-derives the same
  assignment, so a task that already has a session keeps its original runtime
  and we avoid re-dispatch churn / session-continuity breakage.
* **Balanced** — assignment is by parity of the item's index within the sorted
  key set, giving an exact 50/50 split (within one) over the set.

Nothing here mutates state; it only computes a routing string that callers feed
into ``make_thread_actor(..., runtime=...)``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from ...runtime_policy import DEFAULT_RUNTIME_POLICY, normalize_runtime_policy

#: Step / role kinds that MUST stay on the primary runtime for correctness even
#: under the alternating policy.  Empty by default — the operator wants
#: everything distributed and Codex structured-output parity is confirmed (it
#: enforces ``output_type`` via ``--output-schema`` + validate/repair).  Add a
#: step key here only if a specific task type proves unreliable on the secondary.
PRIMARY_ONLY_PLANNING_STEPS: frozenset[str] = frozenset()


def secondary_alternation_enabled(
    *,
    runtime_policy: str | None,
    primary_runtime_name: str | None,
    secondary_runtime_name: str | None,
) -> bool:
    """Return whether planning fan-out should alternate onto the secondary.

    True only when (a) the resolved policy is the ``alternating`` default and
    (b) a real secondary runtime exists — i.e. both names are present and the
    secondary's name differs from the primary's.  Under ``single_agent_runtime``
    the bootstrap builds the secondary with the SAME name as the primary, so
    this returns False and the caller keeps everything on the primary.
    """
    try:
        resolved = normalize_runtime_policy(runtime_policy)
    except ValueError:
        resolved = normalize_runtime_policy(None)
    if resolved != DEFAULT_RUNTIME_POLICY:
        # Non-alternating policies (e.g. primary-impl-secondary-review) drive
        # their own routing elsewhere; planning leads stay on primary.
        return False
    primary = (primary_runtime_name or "").strip()
    secondary = (secondary_runtime_name or "").strip()
    if not secondary:
        return False
    return secondary != primary


def _stable_index(key: str, ordered_keys: Iterable[str]) -> int | None:
    """Return *key*'s position within the sorted unique *ordered_keys*."""
    unique_sorted = sorted({str(k) for k in ordered_keys if str(k)})
    try:
        return unique_sorted.index(str(key))
    except ValueError:
        return None


def alternating_runtime_for(
    key: str,
    *,
    ordered_keys: Iterable[str],
    runtime_policy: str | None,
    primary_runtime_name: str | None,
    secondary_runtime_name: str | None,
    step: str | None = None,
) -> str:
    """Return ``"primary"`` or ``"secondary"`` for a planning work item.

    Deterministic: the result depends only on *key*'s sorted-index parity
    within *ordered_keys* (a stable set such as the subfeature slugs), so a
    resumed run produces the identical assignment.

    Always returns ``"primary"`` when alternation is disabled (non-alternating
    policy, no real secondary) or when *step* is in
    :data:`PRIMARY_ONLY_PLANNING_STEPS`.
    """
    if step is not None and step in PRIMARY_ONLY_PLANNING_STEPS:
        return "primary"
    if not secondary_alternation_enabled(
        runtime_policy=runtime_policy,
        primary_runtime_name=primary_runtime_name,
        secondary_runtime_name=secondary_runtime_name,
    ):
        return "primary"
    index = _stable_index(key, ordered_keys)
    if index is None:
        # Unknown key — fall back to primary rather than guessing.
        return "primary"
    return "secondary" if index % 2 == 1 else "primary"


def runtime_names_from_runner(runner: Any) -> tuple[str | None, str | None]:
    """Best-effort extraction of (primary_name, secondary_name) from a runner."""
    primary = getattr(getattr(runner, "agent_runtime", None), "name", None)
    secondary = getattr(getattr(runner, "secondary_runtime", None), "name", None)
    return primary, secondary


def runtime_policy_from_runner(runner: Any) -> str:
    """Read the active runtime policy from the runner's services map."""
    services = getattr(runner, "services", None)
    if isinstance(services, Mapping):
        policy = services.get("runtime_policy")
        if policy:
            return str(policy)
    return DEFAULT_RUNTIME_POLICY


def planning_alternation_runtime(
    runner: Any,
    *,
    key: str,
    ordered_keys: Iterable[str],
    step: str | None = None,
) -> str:
    """Convenience wrapper that pulls policy + runtime names off *runner*.

    Returns the routing string (``"primary"`` / ``"secondary"``) for the given
    planning work item, honoring the active policy and the presence of a real
    secondary runtime.  Safe to call unconditionally from planning phases.
    """
    primary_name, secondary_name = runtime_names_from_runner(runner)
    return alternating_runtime_for(
        key,
        ordered_keys=ordered_keys,
        runtime_policy=runtime_policy_from_runner(runner),
        primary_runtime_name=primary_name,
        secondary_runtime_name=secondary_name,
        step=step,
    )
