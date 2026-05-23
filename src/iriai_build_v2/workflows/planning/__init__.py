from __future__ import annotations

__all__ = ["PlanningWorkflow"]


def __getattr__(name: str):
    if name != "PlanningWorkflow":
        raise AttributeError(name)
    from .workflow import PlanningWorkflow

    globals()[name] = PlanningWorkflow
    return PlanningWorkflow
