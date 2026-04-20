from __future__ import annotations

import asyncio
import json as _json
import logging
from typing import Any

from pydantic import BaseModel

from iriai_compose import Feature, Phase, WorkflowRunner, to_str

from ....models.outputs import (
    ArchitectureOutput,
    DesignDecisions,
    Envelope,
    PRD,
    RevisionPlan,
    RevisionRequest,
    SubfeatureDecomposition,
    SystemDesign,
    TechnicalPlan,
    TestPlan,
    envelope_done,
)
from ....models.state import BuildState
from ....roles import (
    architect,
    architect_agent_fill_responder,
    architect_role,
    design_agent_fill_responder,
    design_compiler,
    designer,
    designer_role,
    lead_architect,
    lead_architect_gate_reviewer,
    lead_architect_reviewer,
    lead_designer_gate_reviewer,
    lead_designer_reviewer,
    lead_pm_gate_reviewer,
    lead_pm_reviewer,
    plan_arch_compiler,
    pm,
    pm_agent_fill_responder,
    pm_compiler,
    pm_role,
    sysdesign_compiler,
    test_planner,
    test_planner_agent_fill_responder,
    test_planner_role,
    user,
)
from ..._common import (
    ThreadedHostedInterview,
    compile_artifacts,
    gate_and_revise,
    generate_summary,
    get_existing_artifact,
    get_gate_approved_artifact,
    get_gate_resume_artifact,
    integration_review,
    interview_gate_review,
    targeted_revision,
)
from ..._common._helpers import _clear_agent_session
from .._control import (
    STEP_AGENT_FILL,
    STEP_BLOCKED,
    STEP_COMPLETE,
    STEP_PENDING,
    STEP_RUNNING,
    _SUBFEATURE_STEPS,
    ensure_subfeature_threads,
    get_step_record,
    get_thread_record,
    load_planning_control,
    mark_compiled_provenance,
    persist_planning_control,
    set_background_state,
    set_current_stage,
    set_step_mode,
    set_step_status,
    set_thread_runtime_metadata,
    sync_subfeature_threads,
)
from .._decisions import (
    compile_decision_ledger,
    refresh_decision_ledger,
    sync_compiled_decision_mirrors,
)
from .._stage_helpers import (
    _artifact_source_path,
    build_related_decision_sections,
    build_revision_plan,
    build_subfeature_context_text,
    choose_step_mode,
    continue_threaded_interview_in_background,
    planning_index_artifact_key,
    prepare_subfeature_context_artifacts,
    outcome_background_requested,
    push_artifact_if_present,
    read_single_artifact_text,
    thread_outcome_pending_response,
)
from .._threading import ensure_planning_thread, make_thread_actor, make_thread_user

logger = logging.getLogger(__name__)


def _parse_decomposition(text: str) -> SubfeatureDecomposition:
    return SubfeatureDecomposition.model_validate(_json.loads(text))


def _current_step(control: dict[str, Any], slug: str) -> str | None:
    for step in _SUBFEATURE_STEPS:
        if get_step_record(control, slug, step).get("status") != STEP_COMPLETE:
            return step
    return None


def _reset_stale_background_state(
    control: dict[str, Any],
    decomposition: SubfeatureDecomposition,
) -> bool:
    changed = False
    for sf in decomposition.subfeatures:
        thread = get_thread_record(control, sf.slug)
        background = thread.get("background_task", {})
        if not background.get("active"):
            continue
        step = str(background.get("step", "") or _current_step(control, sf.slug) or "")
        if step in set(_SUBFEATURE_STEPS):
            set_background_state(
                control,
                slug=sf.slug,
                step=step,
                active=False,
                status="interrupted",
                reason="resume_reset",
            )
            if get_step_record(control, sf.slug, step).get("status") == STEP_RUNNING:
                set_step_status(control, slug=sf.slug, step=step, status=STEP_PENDING)
        else:
            thread["background_task"] = {
                "active": False,
                "status": "interrupted",
                "step": step,
                "reason": "resume_reset",
            }
        changed = True
        continue

    for sf in decomposition.subfeatures:
        for step in _SUBFEATURE_STEPS:
            if get_step_record(control, sf.slug, step).get("status") != STEP_RUNNING:
                continue
            if get_thread_record(control, sf.slug).get("background_task", {}).get("active"):
                continue
            set_step_status(control, slug=sf.slug, step=step, status=STEP_PENDING)
            changed = True
    return changed


def _subfeature_lock(step_locks: dict[str, asyncio.Lock], slug: str) -> asyncio.Lock:
    lock = step_locks.get(slug)
    if lock is None:
        lock = asyncio.Lock()
        step_locks[slug] = lock
    return lock


async def _ensure_step_mode(
    runner: WorkflowRunner,
    feature: Feature,
    state: BuildState,
    control: dict[str, Any],
    control_lock: asyncio.Lock,
    step_locks: dict[str, asyncio.Lock],
    *,
    sf: Any,
    step: str,
    phase_name: str,
) -> str:
    record = get_step_record(control, sf.slug, step)
    if record.get("mode_selected"):
        return str(record.get("mode", "interactive") or "interactive")

    thread = get_thread_record(control, sf.slug)
    handle = await ensure_planning_thread(
        runner,
        feature,
        thread_id=str(thread.get("thread_id", f"subfeature:{sf.slug}") or f"subfeature:{sf.slug}"),
        label=sf.name,
        existing_thread_ts=str(thread.get("thread_ts", "") or ""),
    )
    set_thread_runtime_metadata(
        control,
        slug=sf.slug,
        step=step,
        resolver=handle.resolver,
        thread_id=handle.thread_id,
        thread_ts=handle.thread_ts,
        label=sf.name,
    )
    async with control_lock:
        await persist_planning_control(runner, feature, state, control)

    async with _subfeature_lock(step_locks, sf.slug):
        record = get_step_record(control, sf.slug, step)
        if record.get("mode_selected"):
            return str(record.get("mode", "interactive") or "interactive")
        title = {
            "pm": "PM",
            "design": "Design",
            "architecture": "Architecture",
            "test_planning": "Test Planning",
        }[step]
        choice = await choose_step_mode(
            runner,
            feature,
            chooser=make_thread_user(user, resolver=handle.resolver),
            phase_name=phase_name,
            prompt=f"How should I handle {sf.name} — {title}?",
        )

    mode = STEP_AGENT_FILL if choice == "Finish in background" else "interactive"
    async with control_lock:
        set_step_mode(control, slug=sf.slug, step=step, mode=mode)
        await persist_planning_control(runner, feature, state, control)
    return mode


def _effective_resume_stage(
    control: dict[str, Any],
    state: BuildState,
    feature: Feature,
) -> str:
    stage_order = {
        "scoping": 0,
        "broad": 1,
        "subfeature": 2,
        "plan-review": 3,
        "task-planning": 4,
    }
    candidates = [
        str(control.get("current_stage", "") or ""),
        str((getattr(state, "metadata", {}) or {}).get("_db_phase", "") or ""),
        str((getattr(feature, "metadata", {}) or {}).get("_db_phase", "") or ""),
    ]
    ranked = [stage for stage in candidates if stage in stage_order]
    if ranked:
        return max(ranked, key=stage_order.__getitem__)
    return next((stage for stage in candidates if stage), "")


async def _load_decomposition(
    runner: WorkflowRunner,
    feature: Feature,
    state: BuildState,
) -> SubfeatureDecomposition:
    text = state.decomposition or await runner.artifacts.get("decomposition", feature=feature) or ""
    if not text:
        return SubfeatureDecomposition()
    return _parse_decomposition(text)


async def _load_completed_stage_maps(
    runner: WorkflowRunner,
    feature: Feature,
    control: dict[str, Any],
    decomposition: SubfeatureDecomposition,
    *,
    step: str,
    artifact_prefix: str,
) -> tuple[dict[str, str], dict[str, str]]:
    artifacts: dict[str, str] = {}
    summaries: dict[str, str] = {}
    for sf in decomposition.subfeatures:
        record = get_step_record(control, sf.slug, step)
        if record.get("status") != STEP_COMPLETE:
            continue
        text = await runner.artifacts.get(f"{artifact_prefix}:{sf.slug}", feature=feature)
        if text:
            artifacts[sf.slug] = text
        summary = await runner.artifacts.get(f"{artifact_prefix}-summary:{sf.slug}", feature=feature)
        if summary:
            summaries[sf.slug] = summary
    return artifacts, summaries


def _pm_prompt(sf: Any, context_path: str, manifest_path: str) -> str:
    edges_desc = ""
    if getattr(sf, "requirement_ids", None):
        edges_desc = f"\nBroad requirement IDs mapped to this subfeature: {', '.join(sf.requirement_ids)}"
    return (
        f"You are the PM for the **{sf.name}** subfeature (ID: {sf.id}, slug: {sf.slug}).\n\n"
        f"**Description:** {sf.description}\n"
        f"{sf.rationale and f'**Rationale:** {sf.rationale}' or ''}\n"
        f"{edges_desc}\n\n"
        "Read the planning context index from the injected context first. Then read the context manifest, "
        "open the referenced files selectively, and use the merged context file as the overview/reference. "
        "After that, interview the stakeholder and produce a "
        "detailed PRD scoped to this subfeature. Document interfaces and edges explicitly.\n\n"
        f"## Context Manifest\n\nRead `{manifest_path}` before proceeding.\n\n"
        f"## Overview Context File\n\nUse `{context_path}` as the overview/reference."
    )


def _design_prompt(sf: Any, context_path: str, manifest_path: str) -> str:
    return (
        f"You are the designer for the **{sf.name}** subfeature (ID: {sf.id}, slug: {sf.slug}).\n\n"
        f"**Description:** {sf.description}\n\n"
        "Read the planning context index from the injected context first. Then read the context manifest, "
        "open the referenced files selectively, and use the merged context file as the overview/reference. "
        "Then create detailed component definitions, journey annotations, "
        "interaction patterns, and responsive behavior for this subfeature. Search the codebase for existing UI "
        "patterns to reuse and document all interfaces and edges.\n\n"
        f"## Context Manifest\n\nRead `{manifest_path}` before proceeding.\n\n"
        f"## Overview Context File\n\nUse `{context_path}` as the overview/reference."
    )


def _architecture_prompt(sf: Any, context_path: str, manifest_path: str) -> str:
    return (
        f"You are the architect for the **{sf.name}** subfeature (ID: {sf.id}, slug: {sf.slug}).\n\n"
        f"**Description:** {sf.description}\n\n"
        "Read the planning context index from the injected context first. Then read the context manifest, "
        "open the referenced files selectively, and use the merged context file as the overview/reference. "
        "Then define implementation steps with file scope, API contracts, "
        "data models, and system design for this subfeature. Ground every decision in the current codebase and "
        "document all interfaces and edges explicitly.\n\n"
        f"## Context Manifest\n\nRead `{manifest_path}` before proceeding.\n\n"
        f"## Overview Context File\n\nUse `{context_path}` as the overview/reference."
    )


def _test_planning_prompt(sf: Any, context_path: str, manifest_path: str) -> str:
    return (
        f"You are the test planner for the **{sf.name}** subfeature (ID: {sf.id}, slug: {sf.slug}).\n\n"
        f"**Description:** {sf.description}\n\n"
        "The PRD, Design Decisions, Technical Plan, and System Design for this subfeature have been approved. "
        "Read the planning context index from the injected context first. Then read the context manifest, "
        "open the referenced files selectively, and use the merged context file as the overview/reference. "
        "Then produce an agent-friendly test plan: acceptance criteria "
        "(each citing a PRD REQ-id), end-to-end test scenarios, a verification checklist, edge cases, "
        "mocking strategy, and test-environment requirements.\n\n"
        "Consolidate — do not duplicate. Cite verifiable states from the design and journey-step IDs from the "
        "plan by ID rather than restating them.\n\n"
        f"## Context Manifest\n\nRead `{manifest_path}` before proceeding.\n\n"
        f"## Overview Context File\n\nUse `{context_path}` as the overview/reference."
    )


def _lightweight_context_keys(step: str, slug: str) -> list[str]:
    return ["project", "scope", planning_index_artifact_key(step, slug)]


def _source_entries(
    runner: WorkflowRunner,
    feature: Feature,
    *,
    refs: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for label, artifact_key in refs:
        path = _artifact_source_path(runner, feature, artifact_key=artifact_key)
        if path:
            entries.append((label, path))
    return entries


async def _clear_fresh_step_sessions(
    runner: WorkflowRunner,
    feature: Feature,
    *,
    actor: Any,
    shadow: Any,
) -> None:
    await _clear_agent_session(runner, actor, feature)
    await _clear_agent_session(runner, shadow, feature)


async def _complete_single_artifact_step(
    runner: WorkflowRunner,
    feature: Feature,
    *,
    subfeature_lock: asyncio.Lock,
    phase_name: str,
    artifact_key: str,
    artifact_label: str,
    actor: Any,
    approver: Any,
    output_type: type[Any],
    result: Any,
    post_update: Any | None = None,
    annotation_keys: list[str] | None = None,
) -> str:
    artifact_text = await read_single_artifact_text(
        runner,
        feature,
        artifact_key=artifact_key,
        result=result,
    )
    await push_artifact_if_present(
        runner,
        feature,
        artifact_key=artifact_key,
        artifact_text=artifact_text,
        label=artifact_label,
    )
    async with subfeature_lock:
        artifact_obj, artifact_text = await gate_and_revise(
            runner,
            feature,
            phase_name,
            artifact=artifact_text,
            actor=actor,
            output_type=output_type,
            approver=approver,
            label=artifact_label,
            artifact_key=artifact_key,
            annotation_keys=annotation_keys,
            post_update=post_update,
        )
    final_text = to_str(artifact_obj) if isinstance(artifact_obj, BaseModel) else artifact_text
    await runner.artifacts.put(artifact_key, final_text, feature=feature)
    return final_text


async def _run_pm_step(
    runner: WorkflowRunner,
    feature: Feature,
    state: BuildState,
    control: dict[str, Any],
    control_lock: asyncio.Lock,
    subfeature_lock: asyncio.Lock,
    decomposition: SubfeatureDecomposition,
    sf: Any,
    *,
    mode: str,
    resume_response: Any | None = None,
    detach_on_background: bool,
) -> Any:
    thread = get_thread_record(control, sf.slug)
    step_record = get_step_record(control, sf.slug, "pm")
    handle = await ensure_planning_thread(
        runner,
        feature,
        thread_id=str(thread.get("thread_id", f"subfeature:{sf.slug}") or f"subfeature:{sf.slug}"),
        label=sf.name,
        existing_thread_ts=str(thread.get("thread_ts", "") or ""),
    )
    set_thread_runtime_metadata(
        control,
        slug=sf.slug,
        step="pm",
        resolver=handle.resolver,
        thread_id=handle.thread_id,
        thread_ts=handle.thread_ts,
        label=sf.name,
    )
    async with control_lock:
        await persist_planning_control(runner, feature, state, control)

    prd_key = f"prd:{sf.slug}"
    broad_text = await runner.artifacts.get("prd:broad", feature=feature) or ""
    decomp_text = await runner.artifacts.get("decomposition", feature=feature) or ""
    broad_decisions = await runner.artifacts.get("decisions:broad", feature=feature) or ""
    completed_decisions, completed_decision_summaries = await _load_completed_stage_maps(
        runner,
        feature,
        control,
        decomposition,
        step="pm",
        artifact_prefix="decisions",
    )
    completed_artifacts, completed_summaries = await _load_completed_stage_maps(
        runner,
        feature,
        control,
        decomposition,
        step="pm",
        artifact_prefix="prd",
    )
    context_text = build_subfeature_context_text(
        decomposition,
        sf.slug,
        broad_sections=[
            ("Broad PRD", broad_text),
            ("Subfeature Decomposition", decomp_text),
        ],
        own_sections=[],
        stage_artifacts=completed_artifacts,
        stage_summaries=completed_summaries,
        decision_sections=build_related_decision_sections(
            decomposition,
            sf.slug,
            broad_text=broad_decisions,
            own_text="",
            completed_artifacts=completed_decisions,
            completed_summaries=completed_decision_summaries,
        ),
    )
    context_path, manifest_path, _ = await prepare_subfeature_context_artifacts(
        runner,
        feature,
        thread_id=handle.thread_id,
        step="pm",
        step_title="PM",
        slug=sf.slug,
        subfeature_name=sf.name,
        context_text=context_text,
        source_groups=[
            (
                "Broad Artifacts",
                _source_entries(
                    runner,
                    feature,
                    refs=[
                        ("Broad PRD", "prd:broad"),
                        ("Subfeature Decomposition", "decomposition"),
                        ("Broad Decision Ledger", "decisions:broad"),
                    ],
                ),
            ),
            (
                "Completed PRDs",
                _source_entries(
                    runner,
                    feature,
                    refs=[(f"PRD — {slug}", f"prd:{slug}") for slug in completed_artifacts],
                ),
            ),
            (
                "Completed PRD Summaries",
                _source_entries(
                    runner,
                    feature,
                    refs=[(f"PRD Summary — {slug}", f"prd-summary:{slug}") for slug in completed_summaries],
                ),
            ),
            (
                "Completed Decision Ledgers",
                _source_entries(
                    runner,
                    feature,
                    refs=[(f"Decision Ledger — {slug}", f"decisions:{slug}") for slug in completed_decisions],
                ),
            ),
            (
                "Completed Decision Summaries",
                _source_entries(
                    runner,
                    feature,
                    refs=[(f"Decision Summary — {slug}", f"decisions-summary:{slug}") for slug in completed_decision_summaries],
                ),
            ),
        ],
    )

    if step_record.get("status") == STEP_COMPLETE:
        approved_text = await runner.artifacts.get(prd_key, feature=feature) or ""
        if approved_text:
            await push_artifact_if_present(
                runner,
                feature,
                artifact_key=prd_key,
                artifact_text=approved_text,
                label=f"PRD — {sf.name}",
            )
            await generate_summary(runner, feature, "prd", sf.slug)
            return approved_text

    approver = make_thread_user(user, resolver=handle.resolver)
    actor = make_thread_actor(
        pm,
        handle=handle,
        suffix="pm",
        context_keys=_lightweight_context_keys("pm", sf.slug),
    )
    shadow = make_thread_actor(
        pm_agent_fill_responder,
        handle=handle,
        suffix="pm-shadow",
        runtime="secondary",
        context_keys=_lightweight_context_keys("pm", sf.slug),
    )

    draft_text = await get_gate_resume_artifact(runner, feature, prd_key)
    if draft_text and resume_response is None:
        final_text = await _complete_single_artifact_step(
            runner,
            feature,
            subfeature_lock=subfeature_lock,
            phase_name="subfeature",
            artifact_key=prd_key,
            artifact_label=f"PRD — {sf.name}",
            actor=actor,
            approver=approver,
            output_type=PRD,
            result=draft_text,
        )
        provenance = step_record.get("provenance") or ("agent_fill" if mode == STEP_AGENT_FILL else "human")
    else:
        if resume_response is not None:
            result = await continue_threaded_interview_in_background(
                runner,
                feature,
                questioner=actor,
                background_responder=shadow,
                pending_response=resume_response,
                context_keys=["project", "scope"],
                output_type=Envelope[PRD],
                done=envelope_done,
                label=sf.name,
            )
            provenance = "mixed"
        elif mode == STEP_AGENT_FILL:
            await _clear_fresh_step_sessions(runner, feature, actor=actor, shadow=shadow)
            interview = ThreadedHostedInterview(
                questioner=actor,
                responder=approver,
                background_responder=shadow,
                initial_prompt=_pm_prompt(sf, context_path, manifest_path),
                output_type=Envelope[PRD],
                done=envelope_done,
                artifact_key=prd_key,
                artifact_label=f"PRD — {sf.name}",
                thread_label=sf.name,
                mode=STEP_AGENT_FILL,
            )
            result = await runner.run(interview, feature, phase_name="subfeature")
            provenance = "agent_fill"
        else:
            await _clear_fresh_step_sessions(runner, feature, actor=actor, shadow=shadow)
            interview = ThreadedHostedInterview(
                questioner=actor,
                responder=approver,
                background_responder=shadow,
                initial_prompt=_pm_prompt(sf, context_path, manifest_path),
                output_type=Envelope[PRD],
                done=envelope_done,
                artifact_key=prd_key,
                artifact_label=f"PRD — {sf.name}",
                thread_label=sf.name,
                mode="interactive",
            )
            async with subfeature_lock:
                result = await runner.run(interview, feature, phase_name="subfeature")
            if outcome_background_requested(result) and detach_on_background:
                return result
            if outcome_background_requested(result):
                result = await continue_threaded_interview_in_background(
                    runner,
                    feature,
                    questioner=actor,
                    background_responder=shadow,
                    pending_response=thread_outcome_pending_response(result),
                    context_keys=["project", "scope"],
                    output_type=Envelope[PRD],
                    done=envelope_done,
                    label=sf.name,
                )
                provenance = "mixed"
            else:
                provenance = "human"

        final_text = await _complete_single_artifact_step(
            runner,
            feature,
            subfeature_lock=subfeature_lock,
            phase_name="subfeature",
            artifact_key=prd_key,
            artifact_label=f"PRD — {sf.name}",
            actor=actor,
            approver=approver,
            output_type=PRD,
            result=result,
        )

    async with control_lock:
        await refresh_decision_ledger(
            runner,
            feature,
            ledger_key=f"decisions:{sf.slug}",
            label=f"Decision Ledger — {sf.name}",
            source_phase="subfeature-pm",
            artifact_kind="prd",
            state=state,
            control=control,
            subfeature_slug=sf.slug,
            source_texts=[final_text],
            summary_key=f"decisions-summary:{sf.slug}",
        )
    summary = await generate_summary(runner, feature, "prd", sf.slug)
    async with control_lock:
        set_step_status(control, slug=sf.slug, step="pm", status=STEP_COMPLETE, provenance=provenance)
        thread["status"] = "pm-complete"
        await persist_planning_control(runner, feature, state, control)
    return final_text


async def _run_design_step(
    runner: WorkflowRunner,
    feature: Feature,
    state: BuildState,
    control: dict[str, Any],
    control_lock: asyncio.Lock,
    subfeature_lock: asyncio.Lock,
    decomposition: SubfeatureDecomposition,
    sf: Any,
    *,
    mode: str,
    resume_response: Any | None = None,
    detach_on_background: bool,
) -> Any:
    from .design import DesignPhase

    thread = get_thread_record(control, sf.slug)
    step_record = get_step_record(control, sf.slug, "design")
    handle = await ensure_planning_thread(
        runner,
        feature,
        thread_id=str(thread.get("thread_id", f"subfeature:{sf.slug}") or f"subfeature:{sf.slug}"),
        label=sf.name,
        existing_thread_ts=str(thread.get("thread_ts", "") or ""),
    )
    set_thread_runtime_metadata(
        control,
        slug=sf.slug,
        step="design",
        resolver=handle.resolver,
        thread_id=handle.thread_id,
        thread_ts=handle.thread_ts,
        label=sf.name,
    )
    async with control_lock:
        await persist_planning_control(runner, feature, state, control)

    design_key = f"design:{sf.slug}"
    broad_prd = await runner.artifacts.get("prd:broad", feature=feature) or ""
    broad_design = await runner.artifacts.get("design:broad", feature=feature) or ""
    broad_decisions = await runner.artifacts.get("decisions:broad", feature=feature) or ""
    own_prd = await runner.artifacts.get(f"prd:{sf.slug}", feature=feature) or ""
    own_decisions = await runner.artifacts.get(f"decisions:{sf.slug}", feature=feature) or ""
    completed_decisions, completed_decision_summaries = await _load_completed_stage_maps(
        runner,
        feature,
        control,
        decomposition,
        step="pm",
        artifact_prefix="decisions",
    )
    completed_artifacts, completed_summaries = await _load_completed_stage_maps(
        runner,
        feature,
        control,
        decomposition,
        step="design",
        artifact_prefix="design",
    )
    context_text = build_subfeature_context_text(
        decomposition,
        sf.slug,
        broad_sections=[
            ("Broad PRD", broad_prd),
            ("Broad Design System", broad_design),
            ("Subfeature Decomposition", await runner.artifacts.get("decomposition", feature=feature) or ""),
        ],
        own_sections=[("Current Subfeature PRD", own_prd)],
        stage_artifacts=completed_artifacts,
        stage_summaries=completed_summaries,
        decision_sections=build_related_decision_sections(
            decomposition,
            sf.slug,
            broad_text=broad_decisions,
            own_text=own_decisions,
            completed_artifacts=completed_decisions,
            completed_summaries=completed_decision_summaries,
        ),
    )
    context_path, manifest_path, _ = await prepare_subfeature_context_artifacts(
        runner,
        feature,
        thread_id=handle.thread_id,
        step="design",
        step_title="Design",
        slug=sf.slug,
        subfeature_name=sf.name,
        context_text=context_text,
        source_groups=[
            (
                "Broad Artifacts",
                _source_entries(
                    runner,
                    feature,
                    refs=[
                        ("Broad PRD", "prd:broad"),
                        ("Broad Design System", "design:broad"),
                        ("Subfeature Decomposition", "decomposition"),
                        ("Broad Decision Ledger", "decisions:broad"),
                    ],
                ),
            ),
            (
                "Current Subfeature Artifacts",
                _source_entries(
                    runner,
                    feature,
                    refs=[
                        ("Current Subfeature PRD", f"prd:{sf.slug}"),
                        ("Current Decision Ledger", f"decisions:{sf.slug}"),
                    ],
                ),
            ),
            (
                "Completed Designs",
                _source_entries(
                    runner,
                    feature,
                    refs=[(f"Design — {slug}", f"design:{slug}") for slug in completed_artifacts],
                ),
            ),
            (
                "Completed Design Summaries",
                _source_entries(
                    runner,
                    feature,
                    refs=[(f"Design Summary — {slug}", f"design-summary:{slug}") for slug in completed_summaries],
                ),
            ),
            (
                "Completed Decision Ledgers",
                _source_entries(
                    runner,
                    feature,
                    refs=[(f"Decision Ledger — {slug}", f"decisions:{slug}") for slug in completed_decisions],
                ),
            ),
            (
                "Completed Decision Summaries",
                _source_entries(
                    runner,
                    feature,
                    refs=[(f"Decision Summary — {slug}", f"decisions-summary:{slug}") for slug in completed_decision_summaries],
                ),
            ),
        ],
    )

    if step_record.get("status") == STEP_COMPLETE:
        approved_text = await runner.artifacts.get(design_key, feature=feature) or ""
        if approved_text:
            await push_artifact_if_present(
                runner,
                feature,
                artifact_key=design_key,
                artifact_text=approved_text,
                label=f"DESIGN — {sf.name}",
            )
            await DesignPhase._host_sf_mockup(runner, feature, sf.slug)
            await generate_summary(runner, feature, "design", sf.slug)
            return approved_text

    approver = make_thread_user(user, resolver=handle.resolver)
    actor = make_thread_actor(
        designer,
        handle=handle,
        suffix="design",
        context_keys=_lightweight_context_keys("design", sf.slug),
    )
    shadow = make_thread_actor(
        design_agent_fill_responder,
        handle=handle,
        suffix="design-shadow",
        runtime="secondary",
        context_keys=_lightweight_context_keys("design", sf.slug),
    )

    draft_text = await get_gate_resume_artifact(runner, feature, design_key)
    if draft_text and resume_response is None:
        final_text = await _complete_single_artifact_step(
            runner,
            feature,
            subfeature_lock=subfeature_lock,
            phase_name="subfeature",
            artifact_key=design_key,
            artifact_label=f"DESIGN — {sf.name}",
            actor=actor,
            approver=approver,
            output_type=DesignDecisions,
            result=draft_text,
        )
        provenance = step_record.get("provenance") or ("agent_fill" if mode == STEP_AGENT_FILL else "human")
    else:
        if resume_response is not None:
            result = await continue_threaded_interview_in_background(
                runner,
                feature,
                questioner=actor,
                background_responder=shadow,
                pending_response=resume_response,
                context_keys=["project", "scope", "prd"],
                output_type=Envelope[DesignDecisions],
                done=envelope_done,
                label=sf.name,
            )
            provenance = "mixed"
        elif mode == STEP_AGENT_FILL:
            await _clear_fresh_step_sessions(runner, feature, actor=actor, shadow=shadow)
            interview = ThreadedHostedInterview(
                questioner=actor,
                responder=approver,
                background_responder=shadow,
                initial_prompt=_design_prompt(sf, context_path, manifest_path),
                output_type=Envelope[DesignDecisions],
                done=envelope_done,
                artifact_key=design_key,
                artifact_label=f"DESIGN — {sf.name}",
                thread_label=sf.name,
                mode=STEP_AGENT_FILL,
            )
            result = await runner.run(interview, feature, phase_name="subfeature")
            provenance = "agent_fill"
        else:
            await _clear_fresh_step_sessions(runner, feature, actor=actor, shadow=shadow)
            interview = ThreadedHostedInterview(
                questioner=actor,
                responder=approver,
                background_responder=shadow,
                initial_prompt=_design_prompt(sf, context_path, manifest_path),
                output_type=Envelope[DesignDecisions],
                done=envelope_done,
                artifact_key=design_key,
                artifact_label=f"DESIGN — {sf.name}",
                thread_label=sf.name,
                mode="interactive",
            )
            async with subfeature_lock:
                result = await runner.run(interview, feature, phase_name="subfeature")
            if outcome_background_requested(result) and detach_on_background:
                return result
            if outcome_background_requested(result):
                result = await continue_threaded_interview_in_background(
                    runner,
                    feature,
                    questioner=actor,
                    background_responder=shadow,
                    pending_response=thread_outcome_pending_response(result),
                    context_keys=["project", "scope", "prd"],
                    output_type=Envelope[DesignDecisions],
                    done=envelope_done,
                    label=sf.name,
                )
                provenance = "mixed"
            else:
                provenance = "human"

        final_text = await _complete_single_artifact_step(
            runner,
            feature,
            subfeature_lock=subfeature_lock,
            phase_name="subfeature",
            artifact_key=design_key,
            artifact_label=f"DESIGN — {sf.name}",
            actor=actor,
            approver=approver,
            output_type=DesignDecisions,
            result=result,
        )

    await DesignPhase._host_sf_mockup(runner, feature, sf.slug)
    async with control_lock:
        await refresh_decision_ledger(
            runner,
            feature,
            ledger_key=f"decisions:{sf.slug}",
            label=f"Decision Ledger — {sf.name}",
            source_phase="subfeature-design",
            artifact_kind="design",
            state=state,
            control=control,
            subfeature_slug=sf.slug,
            source_texts=[final_text],
            summary_key=f"decisions-summary:{sf.slug}",
        )
    await generate_summary(runner, feature, "design", sf.slug)
    async with control_lock:
        set_step_status(control, slug=sf.slug, step="design", status=STEP_COMPLETE, provenance=provenance)
        thread["status"] = "design-complete"
        await persist_planning_control(runner, feature, state, control)
    return final_text


async def _run_architecture_step(
    runner: WorkflowRunner,
    feature: Feature,
    state: BuildState,
    control: dict[str, Any],
    control_lock: asyncio.Lock,
    subfeature_lock: asyncio.Lock,
    decomposition: SubfeatureDecomposition,
    sf: Any,
    *,
    mode: str,
    resume_response: Any | None = None,
    detach_on_background: bool,
) -> Any:
    from .architecture import ArchitecturePhase

    arch_helpers = ArchitecturePhase()
    thread = get_thread_record(control, sf.slug)
    step_record = get_step_record(control, sf.slug, "architecture")
    handle = await ensure_planning_thread(
        runner,
        feature,
        thread_id=str(thread.get("thread_id", f"subfeature:{sf.slug}") or f"subfeature:{sf.slug}"),
        label=sf.name,
        existing_thread_ts=str(thread.get("thread_ts", "") or ""),
    )
    set_thread_runtime_metadata(
        control,
        slug=sf.slug,
        step="architecture",
        resolver=handle.resolver,
        thread_id=handle.thread_id,
        thread_ts=handle.thread_ts,
        label=sf.name,
    )
    async with control_lock:
        await persist_planning_control(runner, feature, state, control)

    plan_key = f"plan:{sf.slug}"
    sd_key = f"system-design:{sf.slug}"
    broad_prd = await runner.artifacts.get("prd:broad", feature=feature) or ""
    broad_design = await runner.artifacts.get("design:broad", feature=feature) or ""
    broad_plan = await runner.artifacts.get("plan:broad", feature=feature) or ""
    broad_decisions = await runner.artifacts.get("decisions:broad", feature=feature) or ""
    own_prd = await runner.artifacts.get(f"prd:{sf.slug}", feature=feature) or ""
    own_design = await runner.artifacts.get(f"design:{sf.slug}", feature=feature) or ""
    own_decisions = await runner.artifacts.get(f"decisions:{sf.slug}", feature=feature) or ""
    completed_decisions, completed_decision_summaries = await _load_completed_stage_maps(
        runner,
        feature,
        control,
        decomposition,
        step="architecture",
        artifact_prefix="decisions",
    )
    completed_artifacts, completed_summaries = await _load_completed_stage_maps(
        runner,
        feature,
        control,
        decomposition,
        step="architecture",
        artifact_prefix="plan",
    )
    context_text = build_subfeature_context_text(
        decomposition,
        sf.slug,
        broad_sections=[
            ("Broad PRD", broad_prd),
            ("Broad Design System", broad_design),
            ("Broad Architecture", broad_plan),
            ("Subfeature Decomposition", await runner.artifacts.get("decomposition", feature=feature) or ""),
        ],
        own_sections=[
            ("Current Subfeature PRD", own_prd),
            ("Current Subfeature Design", own_design),
        ],
        stage_artifacts=completed_artifacts,
        stage_summaries=completed_summaries,
        decision_sections=build_related_decision_sections(
            decomposition,
            sf.slug,
            broad_text=broad_decisions,
            own_text=own_decisions,
            completed_artifacts=completed_decisions,
            completed_summaries=completed_decision_summaries,
        ),
    )
    context_path, manifest_path, _ = await prepare_subfeature_context_artifacts(
        runner,
        feature,
        thread_id=handle.thread_id,
        step="architecture",
        step_title="Architecture",
        slug=sf.slug,
        subfeature_name=sf.name,
        context_text=context_text,
        source_groups=[
            (
                "Broad Artifacts",
                _source_entries(
                    runner,
                    feature,
                    refs=[
                        ("Broad PRD", "prd:broad"),
                        ("Broad Design System", "design:broad"),
                        ("Broad Architecture", "plan:broad"),
                        ("Subfeature Decomposition", "decomposition"),
                        ("Broad Decision Ledger", "decisions:broad"),
                    ],
                ),
            ),
            (
                "Current Subfeature Artifacts",
                _source_entries(
                    runner,
                    feature,
                    refs=[
                        ("Current Subfeature PRD", f"prd:{sf.slug}"),
                        ("Current Subfeature Design", f"design:{sf.slug}"),
                        ("Current Decision Ledger", f"decisions:{sf.slug}"),
                    ],
                ),
            ),
            (
                "Completed Technical Plans",
                _source_entries(
                    runner,
                    feature,
                    refs=[(f"Technical Plan — {slug}", f"plan:{slug}") for slug in completed_artifacts],
                ),
            ),
            (
                "Completed Plan Summaries",
                _source_entries(
                    runner,
                    feature,
                    refs=[(f"Plan Summary — {slug}", f"plan-summary:{slug}") for slug in completed_summaries],
                ),
            ),
            (
                "Completed Decision Ledgers",
                _source_entries(
                    runner,
                    feature,
                    refs=[(f"Decision Ledger — {slug}", f"decisions:{slug}") for slug in completed_decisions],
                ),
            ),
            (
                "Completed Decision Summaries",
                _source_entries(
                    runner,
                    feature,
                    refs=[(f"Decision Summary — {slug}", f"decisions-summary:{slug}") for slug in completed_decision_summaries],
                ),
            ),
        ],
    )

    if step_record.get("status") == STEP_COMPLETE:
        approved_plan = await runner.artifacts.get(plan_key, feature=feature) or ""
        approved_sd = await runner.artifacts.get(sd_key, feature=feature) or ""
        if approved_plan and approved_sd:
            await push_artifact_if_present(
                runner,
                feature,
                artifact_key=plan_key,
                artifact_text=approved_plan,
                label=f"Technical Plan — {sf.name}",
            )
            await arch_helpers._convert_and_host_sd(runner, feature, sd_key, approved_sd, sf.name)
            await generate_summary(runner, feature, "plan", sf.slug)
            return approved_plan

    approver = make_thread_user(user, resolver=handle.resolver)
    actor = make_thread_actor(
        architect,
        handle=handle,
        suffix="architecture",
        context_keys=_lightweight_context_keys("architecture", sf.slug),
    )
    shadow = make_thread_actor(
        architect_agent_fill_responder,
        handle=handle,
        suffix="architecture-shadow",
        runtime="secondary",
        context_keys=_lightweight_context_keys("architecture", sf.slug),
    )

    resume_plan_text = await get_gate_resume_artifact(runner, feature, plan_key)
    resume_sd_text = await get_gate_resume_artifact(runner, feature, sd_key)
    if resume_plan_text and resume_response is None:
        await push_artifact_if_present(
            runner,
            feature,
            artifact_key=plan_key,
            artifact_text=resume_plan_text,
            label=f"Technical Plan — {sf.name}",
        )
        sd_json = await arch_helpers._convert_and_host_sd(
            runner,
            feature,
            sd_key,
            resume_sd_text or resume_plan_text,
            sf.name,
        )

        async def _on_plan_revised(key: str, text: str) -> None:
            nonlocal sd_json
            sd_json = await arch_helpers._rehost_plan_and_sd(
                runner, feature, plan_key, sd_key, sf.name, text,
            )

        async with subfeature_lock:
            plan_obj, plan_text = await gate_and_revise(
                runner,
                feature,
                "subfeature",
                artifact=resume_plan_text,
                actor=actor,
                output_type=TechnicalPlan,
                approver=approver,
                label=f"Technical Plan — {sf.name}",
                artifact_key=plan_key,
                annotation_keys=[plan_key, sd_key],
                post_update=_on_plan_revised,
            )
        plan_text = to_str(plan_obj) if isinstance(plan_obj, BaseModel) else plan_text
        async with subfeature_lock:
            sd_obj, sd_text = await gate_and_revise(
                runner,
                feature,
                "subfeature",
                artifact=sd_json,
                actor=actor,
                output_type=SystemDesign,
                approver=approver,
                label=f"System Design — {sf.name}",
                artifact_key=sd_key,
            )
        sd_text = to_str(sd_obj) if isinstance(sd_obj, BaseModel) else sd_text
        await runner.artifacts.put(plan_key, plan_text, feature=feature)
        await runner.artifacts.put(sd_key, sd_text, feature=feature)
        final_plan_text = plan_text
        provenance = step_record.get("provenance") or "human"
    else:
        if resume_response is not None:
            result = await continue_threaded_interview_in_background(
                runner,
                feature,
                questioner=actor,
                background_responder=shadow,
                pending_response=resume_response,
                context_keys=["project", "scope", "prd", "design"],
                output_type=Envelope[ArchitectureOutput],
                done=envelope_done,
                label=sf.name,
            )
            provenance = "mixed"
        elif mode == STEP_AGENT_FILL:
            await _clear_fresh_step_sessions(runner, feature, actor=actor, shadow=shadow)
            interview = ThreadedHostedInterview(
                questioner=actor,
                responder=approver,
                background_responder=shadow,
                initial_prompt=_architecture_prompt(sf, context_path, manifest_path),
                output_type=Envelope[ArchitectureOutput],
                done=envelope_done,
                artifact_key=plan_key,
                artifact_label=f"Architecture — {sf.name}",
                additional_artifact_keys=[sd_key],
                thread_label=sf.name,
                mode=STEP_AGENT_FILL,
            )
            result = await runner.run(interview, feature, phase_name="subfeature")
            provenance = "agent_fill"
        else:
            await _clear_fresh_step_sessions(runner, feature, actor=actor, shadow=shadow)
            interview = ThreadedHostedInterview(
                questioner=actor,
                responder=approver,
                background_responder=shadow,
                initial_prompt=_architecture_prompt(sf, context_path, manifest_path),
                output_type=Envelope[ArchitectureOutput],
                done=envelope_done,
                artifact_key=plan_key,
                artifact_label=f"Architecture — {sf.name}",
                additional_artifact_keys=[sd_key],
                thread_label=sf.name,
                mode="interactive",
            )
            async with subfeature_lock:
                result = await runner.run(interview, feature, phase_name="subfeature")
            if outcome_background_requested(result) and detach_on_background:
                return result
            if outcome_background_requested(result):
                result = await continue_threaded_interview_in_background(
                    runner,
                    feature,
                    questioner=actor,
                    background_responder=shadow,
                    pending_response=thread_outcome_pending_response(result),
                    context_keys=["project", "scope", "prd", "design"],
                    output_type=Envelope[ArchitectureOutput],
                    done=envelope_done,
                    label=sf.name,
                )
                provenance = "mixed"
            else:
                provenance = "human"

        from ....services.artifacts import _key_to_path

        plan_text = None
        sd_text = None
        mirror = runner.services.get("artifact_mirror")
        if mirror:
            for key, attr in [(plan_key, "plan"), (sd_key, "system_design")]:
                path = mirror.feature_dir(feature.id) / _key_to_path(key)
                if path.exists():
                    text = path.read_text(encoding="utf-8").strip()
                    if text:
                        if attr == "plan":
                            plan_text = text
                        else:
                            sd_text = text

        arch_output = getattr(result, "output", None)
        if not plan_text and arch_output is not None:
            plan_text = to_str(arch_output.plan)
        if not sd_text and arch_output is not None:
            sd_text = to_str(arch_output.system_design)
        sd_text = sd_text or plan_text or ""

        await arch_helpers._convert_and_host_sd(runner, feature, sd_key, sd_text, sf.name)

        async def _sd_post_update(key: str, text: str) -> None:
            if key.startswith("system-design"):
                await arch_helpers._convert_and_host_sd(runner, feature, key, text, sf.name)

        async def _on_plan_revised(key: str, text: str) -> None:
            nonlocal sd_text
            sd_text = await arch_helpers._rehost_plan_and_sd(
                runner, feature, plan_key, sd_key, sf.name, text,
            )

        async with subfeature_lock:
            plan_obj, plan_text = await gate_and_revise(
                runner,
                feature,
                "subfeature",
                artifact=plan_text or "",
                actor=actor,
                output_type=TechnicalPlan,
                approver=approver,
                label=f"Technical Plan — {sf.name}",
                artifact_key=plan_key,
                annotation_keys=[plan_key, sd_key],
                post_update=_on_plan_revised,
            )
        plan_text = to_str(plan_obj) if isinstance(plan_obj, BaseModel) else plan_text
        async with subfeature_lock:
            sd_obj, sd_text = await gate_and_revise(
                runner,
                feature,
                "subfeature",
                artifact=sd_text or "",
                actor=actor,
                output_type=SystemDesign,
                approver=approver,
                label=f"System Design — {sf.name}",
                artifact_key=sd_key,
                post_update=_sd_post_update,
            )
        sd_text = to_str(sd_obj) if isinstance(sd_obj, BaseModel) else sd_text
        await runner.artifacts.put(plan_key, plan_text, feature=feature)
        await runner.artifacts.put(sd_key, sd_text, feature=feature)
        final_plan_text = plan_text

    final_sd_text = await runner.artifacts.get(sd_key, feature=feature) or ""
    async with control_lock:
        await refresh_decision_ledger(
            runner,
            feature,
            ledger_key=f"decisions:{sf.slug}",
            label=f"Decision Ledger — {sf.name}",
            source_phase="subfeature-architecture",
            artifact_kind="plan",
            state=state,
            control=control,
            subfeature_slug=sf.slug,
            source_artifacts=[
                ("plan", final_plan_text),
                ("system-design", final_sd_text),
            ],
            summary_key=f"decisions-summary:{sf.slug}",
        )
    await generate_summary(runner, feature, "plan", sf.slug)
    async with control_lock:
        set_step_status(control, slug=sf.slug, step="architecture", status=STEP_COMPLETE, provenance=provenance)
        thread["status"] = "architecture-complete"
        await persist_planning_control(runner, feature, state, control)
    return final_plan_text


async def _run_test_planning_step(
    runner: WorkflowRunner,
    feature: Feature,
    state: BuildState,
    control: dict[str, Any],
    control_lock: asyncio.Lock,
    subfeature_lock: asyncio.Lock,
    decomposition: SubfeatureDecomposition,
    sf: Any,
    *,
    mode: str,
    resume_response: Any | None = None,
    detach_on_background: bool,
) -> Any:
    """Per-SF test-planning step.

    Produces ``test-plan:{slug}`` — an agent-friendly test spec consumed by
    task decomposition and by implementation-phase gates (test_author,
    integration_tester, qa_engineer, verifier). Pattern mirrors
    ``_run_pm_step`` / ``_run_design_step`` — single artifact, single gate.
    """
    thread = get_thread_record(control, sf.slug)
    step_record = get_step_record(control, sf.slug, "test_planning")
    handle = await ensure_planning_thread(
        runner,
        feature,
        thread_id=str(thread.get("thread_id", f"subfeature:{sf.slug}") or f"subfeature:{sf.slug}"),
        label=sf.name,
        existing_thread_ts=str(thread.get("thread_ts", "") or ""),
    )
    set_thread_runtime_metadata(
        control,
        slug=sf.slug,
        step="test_planning",
        resolver=handle.resolver,
        thread_id=handle.thread_id,
        thread_ts=handle.thread_ts,
        label=sf.name,
    )
    async with control_lock:
        await persist_planning_control(runner, feature, state, control)

    test_plan_key = f"test-plan:{sf.slug}"
    broad_prd = await runner.artifacts.get("prd:broad", feature=feature) or ""
    broad_design = await runner.artifacts.get("design:broad", feature=feature) or ""
    broad_plan = await runner.artifacts.get("plan:broad", feature=feature) or ""
    broad_decisions = await runner.artifacts.get("decisions:broad", feature=feature) or ""
    own_prd = await runner.artifacts.get(f"prd:{sf.slug}", feature=feature) or ""
    own_design = await runner.artifacts.get(f"design:{sf.slug}", feature=feature) or ""
    own_plan = await runner.artifacts.get(f"plan:{sf.slug}", feature=feature) or ""
    own_sd = await runner.artifacts.get(f"system-design:{sf.slug}", feature=feature) or ""
    own_decisions = await runner.artifacts.get(f"decisions:{sf.slug}", feature=feature) or ""
    completed_decisions, completed_decision_summaries = await _load_completed_stage_maps(
        runner,
        feature,
        control,
        decomposition,
        step="test_planning",
        artifact_prefix="decisions",
    )
    completed_artifacts, completed_summaries = await _load_completed_stage_maps(
        runner,
        feature,
        control,
        decomposition,
        step="test_planning",
        artifact_prefix="test-plan",
    )
    context_text = build_subfeature_context_text(
        decomposition,
        sf.slug,
        broad_sections=[
            ("Broad PRD", broad_prd),
            ("Broad Design System", broad_design),
            ("Broad Architecture", broad_plan),
            ("Subfeature Decomposition", await runner.artifacts.get("decomposition", feature=feature) or ""),
        ],
        own_sections=[
            ("Current Subfeature PRD", own_prd),
            ("Current Subfeature Design", own_design),
            ("Current Subfeature Technical Plan", own_plan),
            ("Current Subfeature System Design", own_sd),
        ],
        stage_artifacts=completed_artifacts,
        stage_summaries=completed_summaries,
        decision_sections=build_related_decision_sections(
            decomposition,
            sf.slug,
            broad_text=broad_decisions,
            own_text=own_decisions,
            completed_artifacts=completed_decisions,
            completed_summaries=completed_decision_summaries,
        ),
    )
    context_path, manifest_path, _ = await prepare_subfeature_context_artifacts(
        runner,
        feature,
        thread_id=handle.thread_id,
        step="test_planning",
        step_title="Test Planning",
        slug=sf.slug,
        subfeature_name=sf.name,
        context_text=context_text,
        source_groups=[
            (
                "Broad Artifacts",
                _source_entries(
                    runner,
                    feature,
                    refs=[
                        ("Broad PRD", "prd:broad"),
                        ("Broad Design System", "design:broad"),
                        ("Broad Architecture", "plan:broad"),
                        ("Subfeature Decomposition", "decomposition"),
                        ("Broad Decision Ledger", "decisions:broad"),
                    ],
                ),
            ),
            (
                "Current Subfeature Artifacts",
                _source_entries(
                    runner,
                    feature,
                    refs=[
                        ("Current Subfeature PRD", f"prd:{sf.slug}"),
                        ("Current Subfeature Design", f"design:{sf.slug}"),
                        ("Current Subfeature Technical Plan", f"plan:{sf.slug}"),
                        ("Current Subfeature System Design", f"system-design:{sf.slug}"),
                        ("Current Decision Ledger", f"decisions:{sf.slug}"),
                    ],
                ),
            ),
            (
                "Completed Test Plans",
                _source_entries(
                    runner,
                    feature,
                    refs=[(f"Test Plan — {slug}", f"test-plan:{slug}") for slug in completed_artifacts],
                ),
            ),
            (
                "Completed Test Plan Summaries",
                _source_entries(
                    runner,
                    feature,
                    refs=[(f"Test Plan Summary — {slug}", f"test-plan-summary:{slug}") for slug in completed_summaries],
                ),
            ),
            (
                "Completed Decision Ledgers",
                _source_entries(
                    runner,
                    feature,
                    refs=[(f"Decision Ledger — {slug}", f"decisions:{slug}") for slug in completed_decisions],
                ),
            ),
            (
                "Completed Decision Summaries",
                _source_entries(
                    runner,
                    feature,
                    refs=[(f"Decision Summary — {slug}", f"decisions-summary:{slug}") for slug in completed_decision_summaries],
                ),
            ),
        ],
    )

    if step_record.get("status") == STEP_COMPLETE:
        approved_text = await runner.artifacts.get(test_plan_key, feature=feature) or ""
        if approved_text:
            await push_artifact_if_present(
                runner,
                feature,
                artifact_key=test_plan_key,
                artifact_text=approved_text,
                label=f"Test Plan — {sf.name}",
            )
            await generate_summary(runner, feature, "test-plan", sf.slug)
            return approved_text

    approver = make_thread_user(user, resolver=handle.resolver)
    actor = make_thread_actor(
        test_planner,
        handle=handle,
        suffix="test-planning",
        context_keys=_lightweight_context_keys("test_planning", sf.slug),
    )
    shadow = make_thread_actor(
        test_planner_agent_fill_responder,
        handle=handle,
        suffix="test-planning-shadow",
        runtime="secondary",
        context_keys=_lightweight_context_keys("test_planning", sf.slug),
    )

    draft_text = await get_gate_resume_artifact(runner, feature, test_plan_key)
    if draft_text and resume_response is None:
        final_text = await _complete_single_artifact_step(
            runner,
            feature,
            subfeature_lock=subfeature_lock,
            phase_name="subfeature",
            artifact_key=test_plan_key,
            artifact_label=f"Test Plan — {sf.name}",
            actor=actor,
            approver=approver,
            output_type=TestPlan,
            result=draft_text,
        )
        provenance = step_record.get("provenance") or ("agent_fill" if mode == STEP_AGENT_FILL else "human")
    else:
        if resume_response is not None:
            result = await continue_threaded_interview_in_background(
                runner,
                feature,
                questioner=actor,
                background_responder=shadow,
                pending_response=resume_response,
                context_keys=["project", "scope", "prd", "design", "plan", "system-design"],
                output_type=Envelope[TestPlan],
                done=envelope_done,
                label=sf.name,
            )
            provenance = "mixed"
        elif mode == STEP_AGENT_FILL:
            await _clear_fresh_step_sessions(runner, feature, actor=actor, shadow=shadow)
            interview = ThreadedHostedInterview(
                questioner=actor,
                responder=approver,
                background_responder=shadow,
                initial_prompt=_test_planning_prompt(sf, context_path, manifest_path),
                output_type=Envelope[TestPlan],
                done=envelope_done,
                artifact_key=test_plan_key,
                artifact_label=f"Test Plan — {sf.name}",
                thread_label=sf.name,
                mode=STEP_AGENT_FILL,
            )
            result = await runner.run(interview, feature, phase_name="subfeature")
            provenance = "agent_fill"
        else:
            await _clear_fresh_step_sessions(runner, feature, actor=actor, shadow=shadow)
            interview = ThreadedHostedInterview(
                questioner=actor,
                responder=approver,
                background_responder=shadow,
                initial_prompt=_test_planning_prompt(sf, context_path, manifest_path),
                output_type=Envelope[TestPlan],
                done=envelope_done,
                artifact_key=test_plan_key,
                artifact_label=f"Test Plan — {sf.name}",
                thread_label=sf.name,
                mode="interactive",
            )
            async with subfeature_lock:
                result = await runner.run(interview, feature, phase_name="subfeature")
            if outcome_background_requested(result) and detach_on_background:
                return result
            if outcome_background_requested(result):
                result = await continue_threaded_interview_in_background(
                    runner,
                    feature,
                    questioner=actor,
                    background_responder=shadow,
                    pending_response=thread_outcome_pending_response(result),
                    context_keys=["project", "scope", "prd", "design", "plan", "system-design"],
                    output_type=Envelope[TestPlan],
                    done=envelope_done,
                    label=sf.name,
                )
                provenance = "mixed"
            else:
                provenance = "human"

        final_text = await _complete_single_artifact_step(
            runner,
            feature,
            subfeature_lock=subfeature_lock,
            phase_name="subfeature",
            artifact_key=test_plan_key,
            artifact_label=f"Test Plan — {sf.name}",
            actor=actor,
            approver=approver,
            output_type=TestPlan,
            result=result,
        )

    async with control_lock:
        await refresh_decision_ledger(
            runner,
            feature,
            ledger_key=f"decisions:{sf.slug}",
            label=f"Decision Ledger — {sf.name}",
            source_phase="subfeature-test-planning",
            artifact_kind="test-plan",
            state=state,
            control=control,
            subfeature_slug=sf.slug,
            source_texts=[final_text],
            summary_key=f"decisions-summary:{sf.slug}",
        )
    await generate_summary(runner, feature, "test-plan", sf.slug)
    async with control_lock:
        set_step_status(control, slug=sf.slug, step="test_planning", status=STEP_COMPLETE, provenance=provenance)
        # test_planning is the final per-SF step — "complete" is truthful here.
        thread["status"] = "complete"
        await persist_planning_control(runner, feature, state, control)
    return final_text


def _step_ready(
    control: dict[str, Any],
    decomposition: SubfeatureDecomposition,
    slug: str,
    step: str,
) -> bool:
    # Decomposition edges are preserved for interface/review context, but
    # they do not control threaded subfeature launch ordering.
    step_record = get_step_record(control, slug, step)
    if step_record.get("status") in {STEP_COMPLETE, STEP_RUNNING}:
        return False
    if get_thread_record(control, slug).get("background_task", {}).get("active"):
        return False
    if control.get("broad_steps", {}).get("reconciliation", {}).get("status") != STEP_COMPLETE:
        return False
    # test_planning is per-subfeature only — no corresponding broad step exists;
    # it only requires architecture to be complete for this subfeature.
    broad_step = {
        "pm": "prd",
        "design": "design",
        "architecture": "architecture",
    }.get(step)
    if broad_step is not None:
        if control.get("broad_steps", {}).get(broad_step, {}).get("status") != STEP_COMPLETE:
            return False
    if step == "design" and get_step_record(control, slug, "pm").get("status") != STEP_COMPLETE:
        return False
    if step == "architecture" and get_step_record(control, slug, "design").get("status") != STEP_COMPLETE:
        return False
    if step == "test_planning" and get_step_record(control, slug, "architecture").get("status") != STEP_COMPLETE:
        return False
    return True


async def _run_global_prd_tail(
    runner: WorkflowRunner,
    feature: Feature,
    state: BuildState,
    control: dict[str, Any],
    decomposition: SubfeatureDecomposition,
) -> list[RevisionRequest]:
    collected: list[RevisionRequest] = []
    feature_label = getattr(feature, "name", feature.id)
    approved_text = await get_gate_approved_artifact(
        runner,
        feature,
        artifact_prefix="prd",
        compiled_key="prd",
    )
    if approved_text:
        state.prd = approved_text
        mark_compiled_provenance(
            control,
            "prd",
            [control.get("broad_steps", {}).get("prd", {}).get("provenance", "")]
            + [
                get_step_record(control, sf.slug, "pm").get("provenance", "")
                for sf in decomposition.subfeatures
            ],
        )
        return collected

    compiled_text = await get_existing_artifact(runner, feature, "prd")
    if compiled_text:
        hosting = runner.services.get("hosting")
        if hosting and (not hasattr(hosting, "get_url") or not hosting.get_url("prd")):
            await hosting.push(feature.id, "prd", compiled_text, f"Compiled PRD — {feature_label}")
        state.prd = await interview_gate_review(
            runner,
            feature,
            "subfeature",
            lead_actor=lead_pm_gate_reviewer,
            decomposition=decomposition,
            artifact_prefix="prd",
            compiled_key="prd",
            base_role=pm_role,
            output_type=PRD,
            compiler_actor=pm_compiler,
            broad_key="prd:broad",
            revision_observer=lambda plan: _collect_revision_requests(collected, plan),
        )
        mark_compiled_provenance(
            control,
            "prd",
            [control.get("broad_steps", {}).get("prd", {}).get("provenance", "")]
            + [
                get_step_record(control, sf.slug, "pm").get("provenance", "")
                for sf in decomposition.subfeatures
            ],
        )
        return collected

    review = await integration_review(
        runner,
        feature,
        "subfeature",
        lead_actor=lead_pm_reviewer,
        decomposition=decomposition,
        artifact_prefix="prd",
        broad_key="prd:broad",
        review_key_suffix="prd",
    )
    if review.needs_revision and review.revision_instructions:
        revision_plan = build_revision_plan(
            review.revision_instructions,
            reason="PM integration review finding",
        )
        collected.extend(revision_plan.requests)
        await targeted_revision(
            runner,
            feature,
            "subfeature",
            revision_plan=revision_plan,
            decomposition=decomposition,
            base_role=pm_role,
            output_type=PRD,
            artifact_prefix="prd",
        )

    await compile_artifacts(
        runner,
        feature,
        "subfeature",
        compiler_actor=pm_compiler,
        decomposition=decomposition,
        artifact_prefix="prd",
        broad_key="prd:broad",
        final_key="prd",
    )
    state.prd = await interview_gate_review(
        runner,
        feature,
        "subfeature",
        lead_actor=lead_pm_gate_reviewer,
        decomposition=decomposition,
        artifact_prefix="prd",
        compiled_key="prd",
        base_role=pm_role,
        output_type=PRD,
        compiler_actor=pm_compiler,
        broad_key="prd:broad",
        revision_observer=lambda plan: _collect_revision_requests(collected, plan),
    )
    mark_compiled_provenance(
        control,
        "prd",
        [control.get("broad_steps", {}).get("prd", {}).get("provenance", "")]
        + [
            get_step_record(control, sf.slug, "pm").get("provenance", "")
            for sf in decomposition.subfeatures
        ],
    )
    return collected


async def _run_global_design_tail(
    runner: WorkflowRunner,
    feature: Feature,
    state: BuildState,
    control: dict[str, Any],
    decomposition: SubfeatureDecomposition,
) -> list[RevisionRequest]:
    from .design import DesignPhase

    collected: list[RevisionRequest] = []
    feature_label = getattr(feature, "name", feature.id)
    approved_text = await get_gate_approved_artifact(
        runner,
        feature,
        artifact_prefix="design",
        compiled_key="design",
    )
    if approved_text:
        state.design = approved_text
        mark_compiled_provenance(
            control,
            "design",
            [control.get("broad_steps", {}).get("design", {}).get("provenance", "")]
            + [
                get_step_record(control, sf.slug, "design").get("provenance", "")
                for sf in decomposition.subfeatures
            ],
        )
        return collected

    compiled_text = await get_existing_artifact(runner, feature, "design")
    if compiled_text:
        hosting = runner.services.get("hosting")
        if hosting and (not hasattr(hosting, "get_url") or not hosting.get_url("design")):
            await hosting.push(feature.id, "design", compiled_text, f"Compiled DESIGN — {feature_label}")
        state.design = await interview_gate_review(
            runner,
            feature,
            "subfeature",
            lead_actor=lead_designer_gate_reviewer,
            decomposition=decomposition,
            artifact_prefix="design",
            compiled_key="design",
            base_role=designer_role,
            output_type=DesignDecisions,
            compiler_actor=design_compiler,
            broad_key="design:broad",
            context_keys=["project", "scope", "prd"],
            revision_observer=lambda plan: _collect_revision_requests(collected, plan),
        )
        mark_compiled_provenance(
            control,
            "design",
            [control.get("broad_steps", {}).get("design", {}).get("provenance", "")]
            + [
                get_step_record(control, sf.slug, "design").get("provenance", "")
                for sf in decomposition.subfeatures
            ],
        )
        return collected

    review = await integration_review(
        runner,
        feature,
        "subfeature",
        lead_actor=lead_designer_reviewer,
        decomposition=decomposition,
        artifact_prefix="design",
        broad_key="design:broad",
        review_key_suffix="design",
    )
    if review.needs_revision and review.revision_instructions:
        revision_plan = build_revision_plan(
            review.revision_instructions,
            reason="Design integration review finding",
        )
        collected.extend(revision_plan.requests)
        await targeted_revision(
            runner,
            feature,
            "subfeature",
            revision_plan=revision_plan,
            decomposition=decomposition,
            base_role=designer_role,
            output_type=DesignDecisions,
            artifact_prefix="design",
            context_keys=["project", "scope"],
        )

    mockup_urls: dict[str, str] = {}
    for sf in decomposition.subfeatures:
        url = await DesignPhase._host_sf_mockup(runner, feature, sf.slug)
        if url:
            mockup_urls[f"Mockup: {sf.name}"] = url

    await compile_artifacts(
        runner,
        feature,
        "subfeature",
        compiler_actor=design_compiler,
        decomposition=decomposition,
        artifact_prefix="design",
        broad_key="design:broad",
        final_key="design",
    )
    unified_mockup = await DesignPhase._compile_mockup(runner, feature, decomposition)
    if unified_mockup:
        mockup_urls["Unified Mockup"] = unified_mockup

    async def _refresh_mockup() -> None:
        refreshed = await DesignPhase._compile_mockup(runner, feature, decomposition)
        if refreshed:
            mockup_urls["Unified Mockup"] = refreshed

    state.design = await interview_gate_review(
        runner,
        feature,
        "subfeature",
        lead_actor=lead_designer_gate_reviewer,
        decomposition=decomposition,
        artifact_prefix="design",
        compiled_key="design",
        base_role=designer_role,
        output_type=DesignDecisions,
        compiler_actor=design_compiler,
        broad_key="design:broad",
        context_keys=["project", "scope", "prd"],
        additional_urls=mockup_urls or None,
        post_compile=_refresh_mockup,
        revision_observer=lambda plan: _collect_revision_requests(collected, plan),
    )
    mark_compiled_provenance(
        control,
        "design",
        [control.get("broad_steps", {}).get("design", {}).get("provenance", "")]
        + [
            get_step_record(control, sf.slug, "design").get("provenance", "")
            for sf in decomposition.subfeatures
        ],
    )
    return collected


async def _run_global_architecture_tail(
    runner: WorkflowRunner,
    feature: Feature,
    state: BuildState,
    control: dict[str, Any],
    decomposition: SubfeatureDecomposition,
) -> list[RevisionRequest]:
    from .architecture import ArchitecturePhase

    arch_helpers = ArchitecturePhase()
    feature_label = getattr(feature, "name", feature.id)
    collected: list[RevisionRequest] = []
    approved_plan = await get_gate_approved_artifact(
        runner,
        feature,
        artifact_prefix="plan",
        compiled_key="plan",
    )
    approved_system_design = await get_gate_approved_artifact(
        runner,
        feature,
        artifact_prefix="system-design",
        compiled_key="system-design",
    )
    if approved_plan and approved_system_design:
        state.plan = approved_plan
        state.system_design = approved_system_design
        prov = [control.get("broad_steps", {}).get("architecture", {}).get("provenance", "")] + [
            get_step_record(control, sf.slug, "architecture").get("provenance", "")
            for sf in decomposition.subfeatures
        ]
        mark_compiled_provenance(control, "plan", prov)
        mark_compiled_provenance(control, "system-design", prov)
        return collected

    compiled_plan = await get_existing_artifact(runner, feature, "plan")
    compiled_system_design = await get_existing_artifact(runner, feature, "system-design")

    if approved_plan:
        state.plan = approved_plan
    elif compiled_plan:
        hosting = runner.services.get("hosting")
        if hosting and (not hasattr(hosting, "get_url") or not hosting.get_url("plan")):
            await hosting.push(feature.id, "plan", compiled_plan, f"Compiled PLAN — {feature_label}")
        state.plan = await interview_gate_review(
            runner,
            feature,
            "subfeature",
            lead_actor=lead_architect_gate_reviewer,
            decomposition=decomposition,
            artifact_prefix="plan",
            compiled_key="plan",
            base_role=architect_role,
            output_type=TechnicalPlan,
            compiler_actor=plan_arch_compiler,
            broad_key="plan:broad",
            context_keys=["project", "scope", "prd", "design"],
            revision_observer=lambda plan: _collect_revision_requests(collected, plan),
        )
    else:
        review = await integration_review(
            runner,
            feature,
            "subfeature",
            lead_actor=lead_architect_reviewer,
            decomposition=decomposition,
            artifact_prefix="plan",
            broad_key="plan:broad",
            review_key_suffix="plan",
        )
        if review.needs_revision and review.revision_instructions:
            revision_plan = build_revision_plan(
                review.revision_instructions,
                reason="Architecture integration review finding",
            )
            collected.extend(revision_plan.requests)
            await targeted_revision(
                runner,
                feature,
                "subfeature",
                revision_plan=revision_plan,
                decomposition=decomposition,
                base_role=architect_role,
                output_type=TechnicalPlan,
                artifact_prefix="plan",
                context_keys=["project", "scope"],
            )
            await targeted_revision(
                runner,
                feature,
                "subfeature",
                revision_plan=revision_plan,
                decomposition=decomposition,
                base_role=architect_role,
                output_type=SystemDesign,
                artifact_prefix="system-design",
                context_keys=["project", "scope"],
                post_update=lambda key, text: arch_helpers._convert_and_host_sd(runner, feature, key, text, feature.name),
            )

        await compile_artifacts(
            runner,
            feature,
            "subfeature",
            compiler_actor=plan_arch_compiler,
            decomposition=decomposition,
            artifact_prefix="plan",
            broad_key="plan:broad",
            final_key="plan",
        )

        state.plan = await interview_gate_review(
            runner,
            feature,
            "subfeature",
            lead_actor=lead_architect_gate_reviewer,
            decomposition=decomposition,
            artifact_prefix="plan",
            compiled_key="plan",
            base_role=architect_role,
            output_type=TechnicalPlan,
            compiler_actor=plan_arch_compiler,
            broad_key="plan:broad",
            context_keys=["project", "scope", "prd", "design"],
            revision_observer=lambda plan: _collect_revision_requests(collected, plan),
        )

    if approved_system_design:
        state.system_design = approved_system_design
    elif compiled_system_design:
        hosting = runner.services.get("hosting")
        if hosting and (not hasattr(hosting, "get_url") or not hosting.get_url("system-design")):
            await hosting.push(feature.id, "system-design", compiled_system_design, f"System Design — {feature_label}")
        state.system_design = await interview_gate_review(
            runner,
            feature,
            "subfeature",
            lead_actor=lead_architect_gate_reviewer,
            decomposition=decomposition,
            artifact_prefix="system-design",
            compiled_key="system-design",
            base_role=architect_role,
            output_type=SystemDesign,
            compiler_actor=sysdesign_compiler,
            broad_key="plan:broad",
            context_keys=["project", "scope", "prd", "design"],
            post_update=lambda key, text: arch_helpers._convert_and_host_sd(runner, feature, key, text, feature.name),
            post_compile=lambda: arch_helpers._compile_system_design(runner, feature, decomposition),
        )
    else:
        await arch_helpers._compile_system_design(runner, feature, decomposition)
        state.system_design = await interview_gate_review(
            runner,
            feature,
            "subfeature",
            lead_actor=lead_architect_gate_reviewer,
            decomposition=decomposition,
            artifact_prefix="system-design",
            compiled_key="system-design",
            base_role=architect_role,
            output_type=SystemDesign,
            compiler_actor=sysdesign_compiler,
            broad_key="plan:broad",
            context_keys=["project", "scope", "prd", "design"],
            post_update=lambda key, text: arch_helpers._convert_and_host_sd(runner, feature, key, text, feature.name),
            post_compile=lambda: arch_helpers._compile_system_design(runner, feature, decomposition),
        )
    prov = [control.get("broad_steps", {}).get("architecture", {}).get("provenance", "")] + [
        get_step_record(control, sf.slug, "architecture").get("provenance", "")
        for sf in decomposition.subfeatures
    ]
    mark_compiled_provenance(control, "plan", prov)
    mark_compiled_provenance(control, "system-design", prov)
    return collected


async def _collect_revision_requests(target: list[RevisionRequest], plan: RevisionPlan) -> None:
    target.extend(plan.requests)


async def _cascade_requests(
    runner: WorkflowRunner,
    feature: Feature,
    decomposition: SubfeatureDecomposition,
    *,
    state: BuildState,
    control: dict[str, Any],
    requests: list[RevisionRequest],
    artifact_prefixes: list[str],
    checkpoint_prefix: str = "",
) -> None:
    """Cascade revision requests to downstream artifacts.

    ``checkpoint_prefix`` disambiguates patch cache keys in
    ``targeted_revision`` (``patches:{checkpoint_prefix}:{artifact_prefix}:{slug}``).
    Without a distinct prefix, a second call from a later tail for the same
    artifact+slug would reload the PREVIOUS tail's cached patches instead of
    generating fresh ones. Callers (the global tail functions) pass their
    tail name so each cascade gets its own namespace.
    """
    if not requests:
        return
    affected_slugs = sorted(
        {
            slug
            for request in requests
            for slug in request.affected_subfeatures
            if slug
        }
    )
    grouped: list[RevisionRequest] = []
    for req in requests:
        grouped.append(
            RevisionRequest(
                description=req.description,
                reasoning=req.reasoning,
                affected_subfeatures=list(req.affected_subfeatures),
            )
        )
    revision_plan = RevisionPlan(requests=grouped)
    if "design" in artifact_prefixes:
        await targeted_revision(
            runner,
            feature,
            "subfeature",
            revision_plan=revision_plan,
            decomposition=decomposition,
            base_role=designer_role,
            output_type=DesignDecisions,
            artifact_prefix="design",
            context_keys=["project", "scope"],
            checkpoint_prefix=checkpoint_prefix,
        )
        for slug in affected_slugs:
            await refresh_decision_ledger(
                runner,
                feature,
                ledger_key=f"decisions:{slug}",
                label=f"Decision Ledger — {slug}",
                source_phase="subfeature-design",
                artifact_kind="design",
                state=state,
                control=control,
                subfeature_slug=slug,
                source_texts=[await runner.artifacts.get(f"design:{slug}", feature=feature) or ""],
                summary_key=f"decisions-summary:{slug}",
            )
            await generate_summary(runner, feature, "design", slug)
    if "plan" in artifact_prefixes:
        await targeted_revision(
            runner,
            feature,
            "subfeature",
            revision_plan=revision_plan,
            decomposition=decomposition,
            base_role=architect_role,
            output_type=TechnicalPlan,
            artifact_prefix="plan",
            context_keys=["project", "scope"],
            checkpoint_prefix=checkpoint_prefix,
        )
        for slug in affected_slugs:
            await refresh_decision_ledger(
                runner,
                feature,
                ledger_key=f"decisions:{slug}",
                label=f"Decision Ledger — {slug}",
                source_phase="subfeature-architecture",
                artifact_kind="plan",
                state=state,
                control=control,
                subfeature_slug=slug,
                source_artifacts=[
                    ("plan", await runner.artifacts.get(f"plan:{slug}", feature=feature) or ""),
                    (
                        "system-design",
                        await runner.artifacts.get(f"system-design:{slug}", feature=feature) or "",
                    ),
                ],
                summary_key=f"decisions-summary:{slug}",
            )
            await generate_summary(runner, feature, "plan", slug)
    if "system-design" in artifact_prefixes:
        from .architecture import ArchitecturePhase

        arch_helpers = ArchitecturePhase()
        await targeted_revision(
            runner,
            feature,
            "subfeature",
            revision_plan=revision_plan,
            decomposition=decomposition,
            base_role=architect_role,
            output_type=SystemDesign,
            artifact_prefix="system-design",
            context_keys=["project", "scope"],
            post_update=lambda key, text: arch_helpers._convert_and_host_sd(runner, feature, key, text, feature.name),
            checkpoint_prefix=checkpoint_prefix,
        )
    if "test-plan" in artifact_prefixes:
        # Test plans are derived from PRD / design / plan / system-design, so
        # any upstream cascade can invalidate AC coverage. Filter to SFs that
        # actually have a test-plan artifact — revising a non-existent
        # test-plan would produce a no-op warning in targeted_revision and a
        # hollow ledger/summary entry here. Mirrors the plan_review.py
        # terminal cascade's filter.
        tp_affected_slugs: list[str] = []
        for slug in affected_slugs:
            if await runner.artifacts.get(f"test-plan:{slug}", feature=feature):
                tp_affected_slugs.append(slug)
        if tp_affected_slugs:
            # Narrow the revision plan to SFs with an existing test-plan so
            # targeted_revision doesn't try to patch missing artifacts.
            tp_slug_set = set(tp_affected_slugs)
            tp_requests = [
                RevisionRequest(
                    description=req.description,
                    reasoning=req.reasoning,
                    affected_subfeatures=[
                        s for s in req.affected_subfeatures if s in tp_slug_set
                    ],
                )
                for req in revision_plan.requests
                if any(s in tp_slug_set for s in req.affected_subfeatures)
            ]
            if tp_requests:
                # Compute the exact set of SFs actually revised by this
                # cascade — the union of filtered affected_subfeatures
                # across tp_requests. Using tp_affected_slugs here would
                # refresh ledgers for SFs with test-plans that were NOT
                # touched by this revision round (spurious churn).
                revised_slugs = sorted({
                    slug
                    for req in tp_requests
                    for slug in req.affected_subfeatures
                })
                await targeted_revision(
                    runner,
                    feature,
                    "subfeature",
                    revision_plan=RevisionPlan(requests=tp_requests),
                    decomposition=decomposition,
                    base_role=test_planner_role,
                    output_type=TestPlan,
                    artifact_prefix="test-plan",
                    context_keys=["project", "scope"],
                    checkpoint_prefix=checkpoint_prefix,
                )
                for slug in revised_slugs:
                    await refresh_decision_ledger(
                        runner,
                        feature,
                        ledger_key=f"decisions:{slug}",
                        label=f"Decision Ledger — {slug}",
                        source_phase="subfeature-test-planning",
                        artifact_kind="test-plan",
                        state=state,
                        control=control,
                        subfeature_slug=slug,
                        source_texts=[
                            await runner.artifacts.get(f"test-plan:{slug}", feature=feature) or ""
                        ],
                        summary_key=f"decisions-summary:{slug}",
                    )
                    await generate_summary(runner, feature, "test-plan", slug)


class SubfeaturePhase(Phase):
    name = "subfeature"

    async def execute(
        self, runner: WorkflowRunner, feature: Feature, state: BuildState
    ) -> BuildState:
        decomposition = await _load_decomposition(runner, feature, state)
        control = load_planning_control(state=state, feature=feature)
        current_stage = _effective_resume_stage(control, state, feature)
        if (
            control.get("broad_steps", {}).get("reconciliation", {}).get("status") != STEP_COMPLETE
            and current_stage in {"subfeature", "plan-review", "task-planning"}
        ):
            set_step_status(
                control,
                step="reconciliation",
                status=STEP_COMPLETE,
                provenance="legacy_compat",
            )
        sync_subfeature_threads(control, decomposition)
        if _reset_stale_background_state(control, decomposition):
            await persist_planning_control(runner, feature, state, control)
        set_current_stage(control, "subfeature")
        await persist_planning_control(runner, feature, state, control)

        control_lock = asyncio.Lock()
        step_locks: dict[str, asyncio.Lock] = {}
        active_tasks: dict[str, asyncio.Task] = {}

        async def _dispatch_step(
            sf: Any,
            step: str,
            *,
            mode: str,
            resume_response: Any | None = None,
            detach_on_background: bool,
        ) -> Any:
            subfeature_lock = _subfeature_lock(step_locks, sf.slug)
            if step == "pm":
                return await _run_pm_step(
                    runner,
                    feature,
                    state,
                    control,
                    control_lock,
                    subfeature_lock,
                    decomposition,
                    sf,
                    mode=mode,
                    resume_response=resume_response,
                    detach_on_background=detach_on_background,
                )
            if step == "design":
                return await _run_design_step(
                    runner,
                    feature,
                    state,
                    control,
                    control_lock,
                    subfeature_lock,
                    decomposition,
                    sf,
                    mode=mode,
                    resume_response=resume_response,
                    detach_on_background=detach_on_background,
                )
            if step == "architecture":
                return await _run_architecture_step(
                    runner,
                    feature,
                    state,
                    control,
                    control_lock,
                    subfeature_lock,
                    decomposition,
                    sf,
                    mode=mode,
                    resume_response=resume_response,
                    detach_on_background=detach_on_background,
                )
            if step != "test_planning":
                raise ValueError(
                    f"Unknown subfeature step {step!r}; expected one of {_SUBFEATURE_STEPS}"
                )
            return await _run_test_planning_step(
                runner,
                feature,
                state,
                control,
                control_lock,
                subfeature_lock,
                decomposition,
                sf,
                mode=mode,
                resume_response=resume_response,
                detach_on_background=detach_on_background,
            )

        async def _run_ready_step(sf: Any, step: str) -> None:
            mode = await _ensure_step_mode(
                runner,
                feature,
                state,
                control,
                control_lock,
                step_locks,
                sf=sf,
                step=step,
                phase_name=self.name,
            )
            if mode == STEP_AGENT_FILL:
                async with control_lock:
                    set_background_state(
                        control,
                        slug=sf.slug,
                        step=step,
                        active=True,
                        status="running",
                        reason="agent_fill",
                    )
                    await persist_planning_control(runner, feature, state, control)
                await _dispatch_step(
                    sf,
                    step,
                    mode=STEP_AGENT_FILL,
                    detach_on_background=False,
                )
                return

            result = await _dispatch_step(
                sf,
                step,
                mode="interactive",
                detach_on_background=True,
            )
            if not outcome_background_requested(result):
                return

            async with control_lock:
                set_step_mode(control, slug=sf.slug, step=step, mode=STEP_AGENT_FILL)
                set_background_state(
                    control,
                    slug=sf.slug,
                    step=step,
                    active=True,
                    status="running",
                    reason="user_finish_in_background",
                )
                await persist_planning_control(runner, feature, state, control)

            await _dispatch_step(
                sf,
                step,
                mode=STEP_AGENT_FILL,
                resume_response=thread_outcome_pending_response(result),
                detach_on_background=False,
            )

        async def _start_task(sf: Any, step: str) -> None:
            active_tasks[f"{sf.slug}:{step}"] = asyncio.create_task(_run_ready_step(sf, step))

        while True:
            for key, task in list(active_tasks.items()):
                if not task.done():
                    continue
                slug, step = key.split(":", 1)
                task_error: BaseException | None = None
                try:
                    await task
                except BaseException as exc:
                    task_error = exc
                finally:
                    active_tasks.pop(key, None)
                    async with control_lock:
                        background = get_thread_record(control, slug).get("background_task", {})
                        if background.get("active") and background.get("step") == step:
                            set_background_state(
                                control,
                                slug=slug,
                                step=step,
                                active=False,
                                status="failed" if task_error else "complete",
                                reason="task_failed" if task_error else "",
                            )
                        if task_error and get_step_record(control, slug, step).get("status") == STEP_RUNNING:
                            set_step_status(control, slug=slug, step=step, status=STEP_PENDING)
                        await persist_planning_control(runner, feature, state, control)
                if task_error:
                    raise task_error

            if all(_current_step(control, sf.slug) is None for sf in decomposition.subfeatures):
                break

            ready: list[tuple[Any, str]] = []
            for sf in decomposition.subfeatures:
                step = _current_step(control, sf.slug)
                if not step:
                    continue
                if _step_ready(control, decomposition, sf.slug, step):
                    ready.append((sf, step))
                else:
                    set_step_status(control, slug=sf.slug, step=step, status=STEP_BLOCKED)

            launched = False
            for sf, step in ready:
                async with control_lock:
                    set_step_status(control, slug=sf.slug, step=step, status=STEP_RUNNING)
                    await persist_planning_control(runner, feature, state, control)
                await _start_task(sf, step)
                launched = True

            if not launched:
                if active_tasks:
                    await asyncio.wait(active_tasks.values(), return_when=asyncio.FIRST_COMPLETED)
                else:
                    blocked = [
                        f"{sf.slug}:{_current_step(control, sf.slug)}"
                        for sf in decomposition.subfeatures
                        if _current_step(control, sf.slug)
                    ]
                    raise RuntimeError(f"Subfeature scheduler deadlocked: {blocked}")

        prd_requests = await _run_global_prd_tail(runner, feature, state, control, decomposition)
        if prd_requests:
            # Test plans derive from PRD acceptance criteria; a PRD revision
            # can invalidate AC-id coverage, so cascade into test-plan too.
            await _cascade_requests(
                runner,
                feature,
                decomposition,
                state=state,
                control=control,
                requests=prd_requests,
                artifact_prefixes=["design", "plan", "system-design", "test-plan"],
                checkpoint_prefix="tail-prd",
            )

        design_requests = await _run_global_design_tail(runner, feature, state, control, decomposition)
        if design_requests:
            # Design changes (verifiable_states, component defs) shift what
            # the test plan references; cascade to test-plan.
            await _cascade_requests(
                runner,
                feature,
                decomposition,
                state=state,
                control=control,
                requests=design_requests,
                artifact_prefixes=["plan", "system-design", "test-plan"],
                checkpoint_prefix="tail-design",
            )

        arch_requests = await _run_global_architecture_tail(runner, feature, state, control, decomposition)
        if arch_requests:
            # Test plans reference plan step-IDs and system-design entities;
            # an architecture revision can shift those, so cascade into
            # test-plan (even though it's the only downstream artifact).
            await _cascade_requests(
                runner,
                feature,
                decomposition,
                state=state,
                control=control,
                requests=arch_requests,
                artifact_prefixes=["test-plan"],
                checkpoint_prefix="tail-architecture",
            )
        await compile_decision_ledger(
            runner,
            feature,
            phase_name=self.name,
            decomposition=decomposition,
            state=state,
            control=control,
        )
        state.plan, state.system_design = await sync_compiled_decision_mirrors(
            runner,
            feature,
            plan_text=state.plan,
            system_design_text=state.system_design,
        )
        set_current_stage(control, "plan-review")
        await persist_planning_control(runner, feature, state, control)
        return state
