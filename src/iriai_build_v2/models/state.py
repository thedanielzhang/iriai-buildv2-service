from __future__ import annotations

from pydantic import BaseModel, Field


class BuildState(BaseModel):
    scope: str = ""
    prd: str = ""
    design: str = ""
    plan: str = ""
    system_design: str = ""
    decomposition: str = ""
    user_notes: str = ""
    dag: str = ""
    implementation: str = ""
    handover: str = ""
    observations: str = ""
    phase: str = "scoping"
    metadata: dict[str, object] = Field(default_factory=dict)


class BugFixState(BaseModel):
    bug_report: str = ""
    reproduction: str = ""
    baseline: str = ""
    root_cause_a: str = ""
    root_cause_b: str = ""
    fix: str = ""
    verification: str = ""
    regression: str = ""
    handover: str = ""
    preview_url: str = ""
    project: str = ""
    phase: str = "intake"
    metadata: dict[str, object] = Field(default_factory=dict)


class BugFixV2State(BaseModel):
    source_feature_id: str = ""
    source_feature_name: str = ""
    source_workspace_path: str = ""
    project: str = ""
    queue_summary: str = ""
    decision_summary: str = ""
    history_summary: str = ""
    phase: str = "bugflow-setup"
    metadata: dict[str, object] = Field(default_factory=dict)
