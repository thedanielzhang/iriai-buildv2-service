from __future__ import annotations

from importlib import import_module

_EXPORTS = {
    "PlanningWorkflow": ".planning",
    "FullDevelopWorkflow": ".develop",
    "BugFixWorkflow": ".bugfix",
    "BugFixV2Workflow": ".bugfix_v2",
    "TrackedWorkflowRunner": "._runner",
}

__all__ = [
    "PlanningWorkflow",
    "FullDevelopWorkflow",
    "BugFixWorkflow",
    "BugFixV2Workflow",
    "TrackedWorkflowRunner",
]


def __getattr__(name: str):
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(name)
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value
