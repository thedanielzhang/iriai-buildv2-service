"""Pydantic model → markdown renderer for artifact display."""

from __future__ import annotations

from pydantic import BaseModel

from ..models.outputs import (
    AcceptanceCriterion,
    ArchitecturalRisk,
    ComponentDef,
    CrossServiceImpact,
    DataEntity,
    DesignDecisions,
    FileScope,
    HandoverDoc,
    ImplementationDAG,
    ImplementationStep,
    Journey,
    JourneyUXAnnotation,
    JourneyVerification,
    PRD,
    Requirement,
    ScopeOutput,
    SecurityProfile,
    TaskAcceptanceCriterion,
    TaskFileScope,
    TechnicalPlan,
    VerifiableState,
)


def _esc(text: str) -> str:
    """Escape HTML entities so marked() renders them as literal text."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def to_markdown(model: BaseModel) -> str:
    """Convert a Pydantic model to a human-readable markdown string."""
    if isinstance(model, PRD):
        return _render_prd(model)
    if isinstance(model, ScopeOutput):
        return _render_scope(model)
    if isinstance(model, DesignDecisions):
        return _render_design(model)
    if isinstance(model, TechnicalPlan):
        return _render_plan(model)
    if isinstance(model, ImplementationDAG):
        return _render_dag(model)
    if isinstance(model, HandoverDoc):
        return _render_handover(model)
    return _render_generic(model)


# ── Per-model renderers ─────────────────────────────────────────────────────


def _render_scope(m: ScopeOutput) -> str:
    parts: list[str] = ["# Feature Scope"]
    if m.summary:
        parts.append(f"\n## Summary\n\n{_esc(m.summary)}")
    if m.scope_type:
        parts.append(f"\n## Scope Type\n\n{_esc(m.scope_type)}")
    if m.repos:
        parts.append("\n## Repositories\n")
        parts.append("| Name | Action | Relevance |")
        parts.append("|---|---|---|")
        for r in m.repos:
            name = _esc(r.name)
            if r.github_url:
                name = f"[{name}]({r.github_url})"
            parts.append(f"| {name} | {_esc(r.action)} | {_esc(r.relevance)} |")
    if m.constraints:
        parts.append("\n## Constraints\n")
        for c in m.constraints:
            parts.append(f"- {_esc(c)}")
    if m.out_of_scope:
        parts.append("\n## Out of Scope\n")
        for item in m.out_of_scope:
            parts.append(f"- {_esc(item)}")
    if m.user_decisions:
        parts.append("\n## User Decisions\n")
        for d in m.user_decisions:
            parts.append(f"- {_esc(d)}")
    return "\n".join(parts) + "\n"


def _render_prd(m: PRD) -> str:
    parts: list[str] = [f"# {_esc(m.title)}"]
    if m.overview:
        parts.append(f"\n## Overview\n\n{_esc(m.overview)}")

    # ── Problem Statement & Target Users (new fields) ──
    if m.problem_statement:
        parts.append(f"\n## Problem Statement\n\n{_esc(m.problem_statement)}")
    if m.target_users:
        parts.append(f"\n## Target Users\n\n{_esc(m.target_users)}")

    # ── Requirements: prefer structured, fall back to legacy ──
    if m.structured_requirements:
        parts.append("\n## Requirements\n")
        parts.append("| ID | Category | Priority | Description |")
        parts.append("|---|---|---|---|")
        for r in m.structured_requirements:
            parts.append(
                f"| {_esc(r.id)} | {_esc(r.category)} | {_esc(r.priority)} | {_esc(r.description)} |"
            )
    elif m.requirements:
        parts.append("\n## Requirements\n")
        for i, r in enumerate(m.requirements, 1):
            parts.append(f"{i}. {_esc(r)}")

    # ── Acceptance Criteria: prefer structured, fall back to legacy ──
    if m.structured_acceptance_criteria:
        parts.append("\n## Acceptance Criteria\n")
        parts.append("| ID | User Action | Expected Observation | Not Criteria | Requirement IDs |")
        parts.append("|---|---|---|---|---|")
        for ac in m.structured_acceptance_criteria:
            req_ids = ", ".join(_esc(rid) for rid in ac.requirement_ids) if ac.requirement_ids else ""
            not_c = _esc(ac.not_criteria) if ac.not_criteria else ""
            parts.append(
                f"| {_esc(ac.id)} | {_esc(ac.user_action)} | {_esc(ac.expected_observation)} | {not_c} | {req_ids} |"
            )
    elif m.acceptance_criteria:
        parts.append("\n## Acceptance Criteria\n")
        for c in m.acceptance_criteria:
            parts.append(f"- {_esc(c)}")

    # ── User Journeys ──
    if m.journeys:
        parts.append("\n## User Journeys\n")
        for j in m.journeys:
            tag = f" ({_esc(j.path_type)})" if j.path_type else ""
            parts.append(f"### {_esc(j.id)}: {_esc(j.name)}{tag}\n")
            parts.append(f"- **Actor:** {_esc(j.actor)}")
            parts.append(f"- **Preconditions:** {_esc(j.preconditions)}")
            if j.failure_trigger:
                parts.append(f"- **Failure Trigger:** {_esc(j.failure_trigger)}")
            if j.related_journey_id:
                parts.append(f"- **Related Journey:** {_esc(j.related_journey_id)}")
            if j.requirement_ids:
                parts.append(f"- **Requirements:** {', '.join(_esc(rid) for rid in j.requirement_ids)}")
            if j.steps:
                parts.append("")
                parts.append("| Step | Action | Observes | Not Criteria |")
                parts.append("|---|---|---|---|")
                for s in j.steps:
                    not_c = _esc(s.not_criteria) if s.not_criteria else ""
                    parts.append(
                        f"| {s.step_number} | {_esc(s.action)} | {_esc(s.observes)} | {not_c} |"
                    )
            parts.append(f"\n**Outcome:** {_esc(j.outcome)}\n")

    # ── Security Profile ──
    if m.security_profile:
        sp = m.security_profile
        has_content = any(
            getattr(sp, f)
            for f in sp.model_fields
        )
        if has_content:
            parts.append("\n## Security Profile\n")
            if sp.compliance_requirements:
                parts.append(f"- **Compliance Requirements:** {_esc(sp.compliance_requirements)}")
            if sp.data_sensitivity:
                parts.append(f"- **Data Sensitivity:** {_esc(sp.data_sensitivity)}")
            if sp.pii_handling:
                parts.append(f"- **PII Handling:** {_esc(sp.pii_handling)}")
            if sp.auth_requirements:
                parts.append(f"- **Auth Requirements:** {_esc(sp.auth_requirements)}")
            if sp.data_retention:
                parts.append(f"- **Data Retention:** {_esc(sp.data_retention)}")
            if sp.third_party_exposure:
                parts.append(f"- **Third-Party Exposure:** {_esc(sp.third_party_exposure)}")
            if sp.data_residency:
                parts.append(f"- **Data Residency:** {_esc(sp.data_residency)}")
            if sp.risk_mitigation_notes:
                parts.append(f"- **Risk Mitigation Notes:** {_esc(sp.risk_mitigation_notes)}")

    # ── Data Entities ──
    if m.data_entities:
        parts.append("\n## Data Entities\n")
        for de in m.data_entities:
            status = "new" if de.is_new else "existing"
            parts.append(f"### {_esc(de.name)} ({status})\n")
            if de.fields:
                parts.append("**Fields:**")
                for f in de.fields:
                    parts.append(f"- {_esc(f)}")
            if de.constraints:
                parts.append("\n**Constraints:**")
                for c in de.constraints:
                    parts.append(f"- {_esc(c)}")
            parts.append("")

    # ── Cross-Service Impacts ──
    if m.cross_service_impacts:
        parts.append("\n## Cross-Service Impacts\n")
        parts.append("| Service | Impact | Action Needed |")
        parts.append("|---|---|---|")
        for csi in m.cross_service_impacts:
            parts.append(
                f"| {_esc(csi.service)} | {_esc(csi.impact)} | {_esc(csi.action_needed)} |"
            )

    # ── Open Questions ──
    if m.open_questions:
        parts.append("\n## Open Questions\n")
        for q in m.open_questions:
            parts.append(f"- {_esc(q)}")

    # ── Out of Scope (legacy) ──
    if m.out_of_scope:
        parts.append("\n## Out of Scope\n")
        for item in m.out_of_scope:
            parts.append(f"- {_esc(item)}")

    return "\n".join(parts) + "\n"


def _render_design(m: DesignDecisions) -> str:
    parts: list[str] = ["# Design Decisions"]
    if m.approach:
        parts.append(f"\n## Approach\n\n{_esc(m.approach)}")

    # ── Journey UX Annotations (structured) ──
    if m.journey_annotations:
        parts.append("\n## Journey UX Annotations\n")
        for ann in m.journey_annotations:
            parts.append(f"### Journey {_esc(ann.journey_id)}\n")
            if ann.step_annotations:
                parts.append("**Step Annotations:**")
                for i, sa in enumerate(ann.step_annotations, 1):
                    parts.append(f"{i}. {_esc(sa)}")
            if ann.error_path_ux:
                parts.append(f"\n**Error Path UX:** {_esc(ann.error_path_ux)}")
            if ann.empty_state_ux:
                parts.append(f"\n**Empty State UX:** {_esc(ann.empty_state_ux)}")
            if ann.not_criteria:
                parts.append("\n**Not Criteria:**")
                for nc in ann.not_criteria:
                    parts.append(f"- {_esc(nc)}")
            parts.append("")

    # ── Component Definitions (structured), fall back to legacy components ──
    if m.component_defs:
        parts.append("\n## Component Definitions\n")
        for cd in m.component_defs:
            parts.append(f"### {_esc(cd.id)}: {_esc(cd.name)} ({_esc(cd.status)})\n")
            if cd.location:
                parts.append(f"- **Location:** `{_esc(cd.location)}`")
            if cd.description:
                parts.append(f"- **Description:** {_esc(cd.description)}")
            if cd.props_variants:
                parts.append(f"- **Props / Variants:** {_esc(cd.props_variants)}")
            if cd.states:
                parts.append(f"- **States:** {', '.join(_esc(s) for s in cd.states)}")
            parts.append("")
    elif m.components:
        parts.append("\n## Components\n")
        for c in m.components:
            parts.append(f"- {_esc(c)}")

    # ── Verifiable States ──
    if m.verifiable_states:
        parts.append("\n## Verifiable States\n")
        parts.append("| Component ID | State | Visual Description |")
        parts.append("|---|---|---|")
        for vs in m.verifiable_states:
            parts.append(
                f"| {_esc(vs.component_id)} | {_esc(vs.state_name)} | {_esc(vs.visual_description)} |"
            )

    # ── Responsive Behavior ──
    if m.responsive_behavior:
        parts.append(f"\n## Responsive Behavior\n\n{_esc(m.responsive_behavior)}")

    # ── Interaction Patterns ──
    if m.interaction_patterns:
        parts.append(f"\n## Interaction Patterns\n\n{_esc(m.interaction_patterns)}")

    # ── Accessibility Notes ──
    if m.accessibility_notes:
        parts.append(f"\n## Accessibility Notes\n\n{_esc(m.accessibility_notes)}")

    # ── Legacy fields ──
    if m.rationale:
        parts.append(f"\n## Rationale\n\n{_esc(m.rationale)}")
    if m.alternatives:
        parts.append("\n## Alternatives Considered\n")
        for a in m.alternatives:
            parts.append(f"- {_esc(a)}")

    return "\n".join(parts) + "\n"


def _render_plan(m: TechnicalPlan) -> str:
    parts: list[str] = ["# Technical Plan"]
    if m.architecture:
        parts.append(f"\n## Architecture\n\n{_esc(m.architecture)}")

    # ── File Manifest (structured), fall back to legacy file lists ──
    if m.file_manifest:
        parts.append("\n## File Manifest\n")
        parts.append("| Path | Action |")
        parts.append("|---|---|")
        for fs in m.file_manifest:
            parts.append(f"| `{_esc(fs.path)}` | {_esc(fs.action)} |")
    else:
        if m.files_to_create:
            parts.append("\n## Files to Create\n")
            for f in m.files_to_create:
                parts.append(f"- `{_esc(f)}`")
        if m.files_to_modify:
            parts.append("\n## Files to Modify\n")
            for f in m.files_to_modify:
                parts.append(f"- `{_esc(f)}`")

    if m.dependencies:
        parts.append("\n## Dependencies\n")
        for d in m.dependencies:
            parts.append(f"- {_esc(d)}")

    # ── Implementation Steps (structured), fall back to legacy ──
    if m.steps:
        parts.append("\n## Implementation Steps\n")
        for step in m.steps:
            parts.append(f"### {_esc(step.id)}: {_esc(step.objective)}\n")
            if step.requirement_ids:
                parts.append(f"- **Requirements:** {', '.join(_esc(r) for r in step.requirement_ids)}")
            if step.journey_ids:
                parts.append(f"- **Journeys:** {', '.join(_esc(j) for j in step.journey_ids)}")
            if step.scope:
                parts.append("\n**File Scope:**\n")
                parts.append("| Path | Action |")
                parts.append("|---|---|")
                for fs in step.scope:
                    parts.append(f"| `{_esc(fs.path)}` | {_esc(fs.action)} |")
            parts.append(f"\n**Instructions:**\n\n{_esc(step.instructions)}")
            if step.acceptance_criteria:
                parts.append("\n**Acceptance Criteria:**\n")
                for ac in step.acceptance_criteria:
                    parts.append(f"- {_esc(ac)}")
            if step.counterexamples:
                parts.append("\n**Counterexamples:**\n")
                for ce in step.counterexamples:
                    parts.append(f"- {_esc(ce)}")
            parts.append("")
    elif m.implementation_steps:
        parts.append("\n## Implementation Steps\n")
        for i, s in enumerate(m.implementation_steps, 1):
            parts.append(f"{i}. {_esc(s)}")

    # ── Journey Verifications ──
    if m.journey_verifications:
        parts.append("\n## Journey Verifications\n")
        for jv in m.journey_verifications:
            parts.append(f"### Journey {_esc(jv.journey_id)}\n")
            if jv.steps:
                for jvs in jv.steps:
                    parts.append(f"**Step {jvs.step_number}:**\n")
                    if jvs.verify_blocks:
                        parts.append("| Type | Expectation |")
                        parts.append("|---|---|")
                        for vb in jvs.verify_blocks:
                            parts.append(f"| {_esc(vb.type)} | {_esc(vb.expectation)} |")
                    if jvs.data_testids:
                        parts.append(f"\n*Test IDs:* {', '.join(f'`{_esc(t)}`' for t in jvs.data_testids)}")
                    parts.append("")

    # ── Architectural Risks (structured), fall back to legacy ──
    if m.architectural_risks:
        parts.append("\n## Architectural Risks\n")
        parts.append("| ID | Severity | Description | Mitigation | Affected Steps |")
        parts.append("|---|---|---|---|---|")
        for ar in m.architectural_risks:
            mitigation = _esc(ar.mitigation) if ar.mitigation else ""
            affected = ", ".join(_esc(s) for s in ar.affected_step_ids) if ar.affected_step_ids else ""
            parts.append(
                f"| {_esc(ar.id)} | {_esc(ar.severity)} | {_esc(ar.description)} | {mitigation} | {affected} |"
            )
    elif m.risks:
        parts.append("\n## Risks\n")
        for r in m.risks:
            parts.append(f"- {_esc(r)}")

    # ── Test ID Registry ──
    if m.testid_registry:
        parts.append("\n## Test ID Registry\n")
        for tid in m.testid_registry:
            parts.append(f"- `{_esc(tid)}`")

    return "\n".join(parts) + "\n"


def _render_dag(m: ImplementationDAG) -> str:
    parts: list[str] = ["# Implementation DAG"]
    parts.append(f"\n**Teams:** {m.num_teams}")

    # ── Requirement Coverage ──
    if m.requirement_coverage:
        parts.append("\n## Requirement Coverage\n")
        parts.append("| Requirement | Task IDs |")
        parts.append("|---|---|")
        for req_id, task_ids in m.requirement_coverage.items():
            parts.append(
                f"| {_esc(req_id)} | {', '.join(_esc(t) for t in task_ids)} |"
            )

    if m.execution_order:
        parts.append("\n## Execution Order\n")
        for i, phase in enumerate(m.execution_order, 1):
            parts.append(f"{i}. {', '.join(_esc(p) for p in phase)}")
    if m.tasks:
        parts.append("\n## Tasks\n")
        for t in m.tasks:
            parts.append(f"### {_esc(t.name)}")
            parts.append(f"\n{_esc(t.description)}")
            parts.append(f"\n- **ID:** {_esc(t.id)}")
            parts.append(f"- **Team:** {t.team}")
            if t.dependencies:
                parts.append(f"- **Dependencies:** {', '.join(_esc(d) for d in t.dependencies)}")

            # ── Structured file_scope, fall back to legacy files ──
            if t.file_scope:
                parts.append("\n**File Scope:**\n")
                parts.append("| Path | Action |")
                parts.append("|---|---|")
                for fs in t.file_scope:
                    parts.append(f"| `{_esc(fs.path)}` | {_esc(fs.action)} |")
            elif t.files:
                parts.append(f"- **Files:** {', '.join(f'`{_esc(f)}`' for f in t.files)}")

            # ── Traceability IDs ──
            if t.requirement_ids:
                parts.append(f"- **Requirements:** {', '.join(_esc(r) for r in t.requirement_ids)}")
            if t.step_ids:
                parts.append(f"- **Steps:** {', '.join(_esc(s) for s in t.step_ids)}")
            if t.journey_ids:
                parts.append(f"- **Journeys:** {', '.join(_esc(j) for j in t.journey_ids)}")

            # ── Acceptance Criteria ──
            if t.acceptance_criteria:
                parts.append("\n**Acceptance Criteria:**\n")
                for ac in t.acceptance_criteria:
                    not_c = f" *(Not: {_esc(ac.not_criteria)})*" if ac.not_criteria else ""
                    parts.append(f"- {_esc(ac.description)}{not_c}")

            # ── Counterexamples ──
            if t.counterexamples:
                parts.append("\n**Counterexamples:**\n")
                for ce in t.counterexamples:
                    parts.append(f"- {_esc(ce)}")

            # ── Security Concerns ──
            if t.security_concerns:
                parts.append("\n**Security Concerns:**\n")
                for sc in t.security_concerns:
                    parts.append(f"- {_esc(sc)}")

            # ── Test ID Assignments ──
            if t.testid_assignments:
                parts.append(f"\n*Test IDs:* {', '.join(f'`{_esc(tid)}`' for tid in t.testid_assignments)}")

            parts.append("")
    return "\n".join(parts) + "\n"


def _render_handover(m: HandoverDoc) -> str:
    parts: list[str] = ["# Implementation Handover"]

    if m.summary_of_prior_work:
        parts.append(f"\n## Prior Work Summary\n\n{_esc(m.summary_of_prior_work)}")

    if m.completed:
        parts.append("\n## Completed Tasks\n")
        parts.append("| Task | Summary | Files | Status |")
        parts.append("|---|---|---|---|")
        for t in m.completed:
            files = ", ".join(f"`{_esc(f)}`" for f in t.files_changed)
            parts.append(
                f"| {_esc(t.task_id)} | {_esc(t.summary)} | {files} | {_esc(t.status)} |"
            )

    if m.failed_attempts:
        parts.append("\n## Failed Attempts (DO NOT REPEAT)\n")
        parts.append("| Task | Summary | Failure Reason |")
        parts.append("|---|---|---|")
        for t in m.failed_attempts:
            parts.append(
                f"| {_esc(t.task_id)} | {_esc(t.summary)} | {_esc(t.failure_reason)} |"
            )

    if m.all_files_changed:
        unique = sorted(set(m.all_files_changed))
        parts.append("\n## All Files Changed\n")
        for f in unique:
            parts.append(f"- `{_esc(f)}`")

    if m.active_risks:
        parts.append("\n## Active Risks\n")
        parts.append("| Severity | Description |")
        parts.append("|---|---|")
        for r in m.active_risks:
            parts.append(f"| {_esc(r.severity)} | {_esc(r.description)} |")

    if m.key_decisions:
        parts.append("\n## Key Decisions\n")
        for d in m.key_decisions:
            parts.append(f"- {_esc(d)}")

    if m.open_issues:
        parts.append("\n## Open Issues\n")
        for issue in m.open_issues:
            parts.append(f"- {_esc(issue)}")

    if m.notes:
        parts.append(f"\n## Notes\n\n{_esc(m.notes)}")

    return "\n".join(parts) + "\n"


# ── Generic fallback ────────────────────────────────────────────────────────


def _render_generic(model: BaseModel) -> str:
    parts: list[str] = [f"# {type(model).__name__}"]
    for name, _field in model.model_fields.items():
        if name == "complete":
            continue
        value = getattr(model, name)
        if not value:
            continue
        heading = name.replace("_", " ").title()
        if isinstance(value, list):
            parts.append(f"\n## {heading}\n")
            for item in value:
                parts.append(f"- {_esc(str(item))}")
        elif isinstance(value, str):
            parts.append(f"\n## {heading}\n\n{_esc(value)}")
        else:
            parts.append(f"\n## {heading}\n\n{value}")
    return "\n".join(parts) + "\n"
