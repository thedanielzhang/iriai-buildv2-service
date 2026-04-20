from __future__ import annotations

from typing import Any

from iriai_compose import Feature
from iriai_compose.actors import Actor, InteractionActor

from ..planning._control import PLANNING_CONTROL_KEY

_AUTO_USER = InteractionActor(name="auto-user", resolver="auto")
_AUTONOMOUS_PHASES = frozenset({"plan-review", "task-planning", "implementation"})
_AUTONOMOUS_STAGES = frozenset({"plan-review", "task-planning"})


def autonomous_remainder_enabled(
    runner: Any,
    feature: Feature | Any | None,
    *,
    phase_name: str = "",
) -> bool:
    """Return True when the bridge is configured to automate the later flow.

    The CLI flag is intentionally phase-aware so early planning remains human
    driven, while the later tail (plan review → task planning → implementation)
    can run unattended.
    """
    services = getattr(runner, "services", {}) or {}
    if not services.get("autonomous_remainder"):
        return False

    if phase_name in _AUTONOMOUS_PHASES:
        return True

    metadata = dict(getattr(feature, "metadata", {}) or {}) if feature is not None else {}
    planning_control = metadata.get(PLANNING_CONTROL_KEY, {})
    if isinstance(planning_control, dict):
        current_stage = str(planning_control.get("current_stage", "") or "")
        if current_stage in _AUTONOMOUS_STAGES:
            return True

    db_phase = str(metadata.get("_db_phase", "") or "")
    if db_phase in _AUTONOMOUS_PHASES:
        return True

    return False


def interaction_actor_for_phase(
    runner: Any,
    feature: Feature | Any | None,
    *,
    phase_name: str = "",
    fallback: Actor,
) -> Actor:
    """Choose the delegated interaction actor when autonomous mode is active."""
    if autonomous_remainder_enabled(runner, feature, phase_name=phase_name):
        return _AUTO_USER
    return fallback
