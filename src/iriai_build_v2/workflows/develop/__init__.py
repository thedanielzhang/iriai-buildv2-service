from __future__ import annotations

__all__ = ["FullDevelopWorkflow"]


def __getattr__(name: str):
    if name != "FullDevelopWorkflow":
        raise AttributeError(name)
    from .workflow import FullDevelopWorkflow

    globals()[name] = FullDevelopWorkflow
    return FullDevelopWorkflow
