from __future__ import annotations

import json as _json
import logging
from typing import Any

from iriai_compose import Feature, Phase, WorkflowRunner, to_str

from ....models.outputs import (
    DesignDecisions,
    Envelope,
    IntegrationReview,
    PRD,
    SubfeatureDecomposition,
    TechnicalPlan,
    envelope_done,
)
from ....models.state import BuildState
from ....roles import (
    architect_agent_fill_responder,
    design_agent_fill_responder,
    lead_architect_broad,
    lead_architect_reviewer,
    lead_designer_broad,
    lead_pm,
    lead_pm_decomposer,
    pm_agent_fill_responder,
    user,
)
from ..._common import (
    Gate,
    ThreadedHostedInterview,
    gate_feedback_text,
    get_existing_artifact,
    get_gate_resume_artifact,
    get_resumable_artifact,
    integration_review,
)
from .._control import (
    STEP_AGENT_FILL,
    STEP_COMPLETE,
    STEP_PENDING,
    STEP_RUNNING,
    get_broad_step_record,
    load_planning_control,
    persist_planning_control,
    set_background_state,
    set_current_stage,
    set_step_mode,
    set_step_status,
    set_thread_runtime_metadata,
    sync_subfeature_threads,
)
from .._decisions import refresh_decision_ledger
from .._stage_helpers import (
    continue_threaded_interview_in_background,
    outcome_background_requested,
    push_artifact_if_present,
    read_single_artifact_text,
    thread_outcome_pending_response,
)
from .._threading import ensure_planning_thread, make_thread_actor, make_thread_user

logger = logging.getLogger(__name__)


def _parse_model(output_type: type[Any], text: str) -> Any:
    return output_type.model_validate(_json.loads(text))


def _parse_decomposition_if_valid(text: str) -> SubfeatureDecomposition | None:
    try:
        return _parse_model(SubfeatureDecomposition, text)
    except Exception:
        return None


def _merge_provenance(current: str, new_value: str) -> str:
    values = {value for value in (current, new_value) if value}
    if not values:
        return ""
    if len(values) == 1:
        return next(iter(values))
    return "mixed"


def _build_gate_label_with_review_urls(
    runner: WorkflowRunner,
    *,
    label: str,
    artifact_keys: list[str],
) -> str:
    clean_label = "\n".join(
        line for line in label.splitlines()
        if "Review in browser:" not in line
    ).strip()
    hosting = runner.services.get("hosting")
    if not hosting or not artifact_keys:
        return clean_label
    urls = [hosting.get_url(key) for key in artifact_keys]
    urls = [url for url in urls if url]
    if not urls:
        return clean_label
    return clean_label + "\nReview in browser: " + " | ".join(urls)


async def _run_broad_interview(
    runner: WorkflowRunner,
    feature: Feature,
    state: BuildState,
    control: dict[str, Any],
    *,
    phase_name: str,
    step: str,
    label: str,
    lead_actor: Any,
    background_actor: Any,
    output_type: type[Any],
    artifact_key: str,
    artifact_label: str,
    initial_prompt: str,
    resolver: str,
    handle: Any,
) -> tuple[str, str]:
    questioner = make_thread_actor(
        lead_actor,
        handle=handle,
        suffix=step,
        context_keys=list(getattr(lead_actor, "context_keys", []) or []),
    )
    background_responder = make_thread_actor(
        background_actor,
        handle=handle,
        suffix=f"{step}-shadow",
        runtime="secondary",
        context_keys=list(getattr(background_actor, "context_keys", []) or []),
    )
    threaded_user = make_thread_user(user, resolver=resolver)

    interview = ThreadedHostedInterview(
        questioner=questioner,
        responder=threaded_user,
        background_responder=background_responder,
        initial_prompt=initial_prompt,
        output_type=Envelope[output_type],
        done=envelope_done,
        artifact_key=artifact_key,
        artifact_label=artifact_label,
        thread_label=label,
        mode=get_broad_step_record(control, step).get("mode", "interactive"),
    )
    result = await runner.run(interview, feature, phase_name=phase_name)
    provenance = "agent_fill" if get_broad_step_record(control, step).get("mode") == STEP_AGENT_FILL else "human"

    if outcome_background_requested(result):
        set_step_mode(control, step=step, mode=STEP_AGENT_FILL)
        set_background_state(
            control,
            step=step,
            active=True,
            status="running",
            reason="user_finish_in_background",
        )
        await persist_planning_control(runner, feature, state, control)
        result = await continue_threaded_interview_in_background(
            runner,
            feature,
            questioner=questioner,
            background_responder=background_responder,
            pending_response=thread_outcome_pending_response(result),
            context_keys=list(getattr(questioner, "context_keys", []) or []),
            output_type=Envelope[output_type],
            done=envelope_done,
            label=label,
        )
        provenance = "mixed"
        set_background_state(control, step=step, active=False, status="complete")

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
        label=f"{artifact_label} — {feature.name}",
    )
    return artifact_text, provenance


async def _refresh_broad_decisions(
    runner: WorkflowRunner,
    feature: Feature,
    state: BuildState,
    control: dict[str, Any],
    *,
    source_phase: str,
    artifact_kind: str,
    source_text: str,
) -> None:
    await refresh_decision_ledger(
        runner,
        feature,
        ledger_key="decisions:broad",
        label="Broad Decision Ledger",
        source_phase=source_phase,
        artifact_kind=artifact_kind,
        state=state,
        control=control,
        source_texts=[source_text],
    )


def _build_broad_reconciliation_prompt(
    artifact_label: str,
    instruction: str,
    current_text: str,
) -> str:
    return (
        f"Please revise {artifact_label} based on this broad reconciliation finding:\n\n"
        f"{instruction}\n\nCurrent draft:\n{current_text}"
    )


def _build_decomposition_interview_actors(handle: Any) -> tuple[Any, Any, Any]:
    questioner = make_thread_actor(
        lead_pm_decomposer,
        handle=handle,
        suffix="decomposition",
        context_keys=list(getattr(lead_pm_decomposer, "context_keys", []) or []),
    )
    background_responder = make_thread_actor(
        pm_agent_fill_responder,
        handle=handle,
        suffix="decomposition-shadow",
        runtime="secondary",
        context_keys=list(getattr(pm_agent_fill_responder, "context_keys", []) or []),
    )
    threaded_user = make_thread_user(user, resolver=handle.resolver)
    return questioner, background_responder, threaded_user


async def _run_decomposition_interview(
    runner: WorkflowRunner,
    feature: Feature,
    state: BuildState,
    control: dict[str, Any],
    *,
    phase_name: str,
    initial_prompt: str,
    handle: Any,
) -> tuple[str, SubfeatureDecomposition, str]:
    step = "decomposition"
    record = get_broad_step_record(control, step)
    questioner, background_responder, _ = _build_decomposition_interview_actors(handle)
    interview = ThreadedHostedInterview(
        questioner=questioner,
        responder=make_thread_user(user, resolver=handle.resolver),
        background_responder=background_responder,
        initial_prompt=initial_prompt,
        output_type=Envelope[SubfeatureDecomposition],
        done=envelope_done,
        artifact_key="decomposition",
        artifact_label="Subfeature Decomposition",
        thread_label="Broad Decomposition",
        mode=record.get("mode", "interactive"),
        prefer_structured_output=True,
    )
    result = await runner.run(interview, feature, phase_name=phase_name)
    provenance = "agent_fill" if record.get("mode") == STEP_AGENT_FILL else "human"

    if outcome_background_requested(result):
        set_step_mode(control, step=step, mode=STEP_AGENT_FILL)
        set_background_state(
            control,
            step=step,
            active=True,
            status="running",
            reason="user_finish_in_background",
        )
        await persist_planning_control(runner, feature, state, control)
        result = await continue_threaded_interview_in_background(
            runner,
            feature,
            questioner=questioner,
            background_responder=background_responder,
            pending_response=thread_outcome_pending_response(result),
            context_keys=list(getattr(questioner, "context_keys", []) or []),
            output_type=Envelope[SubfeatureDecomposition],
            done=envelope_done,
            label="Broad Decomposition",
        )
        provenance = "mixed"
        set_background_state(control, step=step, active=False, status="complete")

    output = getattr(result, "output", None)
    if isinstance(output, SubfeatureDecomposition):
        decomp_text = output.model_dump_json()
        await push_artifact_if_present(
            runner,
            feature,
            artifact_key="decomposition",
            artifact_text=decomp_text,
            label="Subfeature Decomposition",
        )
        return decomp_text, output, provenance

    decomp_text = await read_single_artifact_text(
        runner,
        feature,
        artifact_key="decomposition",
        result=result,
    )
    decomposition = _parse_model(SubfeatureDecomposition, decomp_text)
    return decomp_text, decomposition, provenance


async def _revise_broad_artifact_from_reconciliation(
    runner: WorkflowRunner,
    feature: Feature,
    state: BuildState,
    control: dict[str, Any],
    *,
    phase_name: str,
    step: str,
    thread_id: str,
    label: str,
    lead_actor: Any,
    background_actor: Any,
    output_type: type[Any],
    artifact_key: str,
    artifact_label: str,
    instruction: str,
    source_phase: str,
    artifact_kind: str,
    state_field: str,
) -> str:
    record = get_broad_step_record(control, step)
    handle = await ensure_planning_thread(
        runner,
        feature,
        thread_id=thread_id,
        label=label,
        existing_thread_ts=str(record.get("thread_ts", "") or ""),
    )
    set_thread_runtime_metadata(
        control,
        step=step,
        resolver=handle.resolver,
        thread_id=handle.thread_id,
        thread_ts=handle.thread_ts,
        label=label,
    )
    await persist_planning_control(runner, feature, state, control)

    current_text = await get_resumable_artifact(runner, feature, artifact_key) or ""
    if not current_text:
        raise RuntimeError(f"Cannot reconcile {artifact_key}: no current draft is available")

    revised_text, revised_provenance = await _run_broad_interview(
        runner,
        feature,
        state,
        control,
        phase_name=phase_name,
        step=step,
        label=label,
        lead_actor=lead_actor,
        background_actor=background_actor,
        output_type=output_type,
        artifact_key=artifact_key,
        artifact_label=artifact_label,
        initial_prompt=_build_broad_reconciliation_prompt(
            artifact_label,
            instruction,
            current_text,
        ),
        resolver=handle.resolver,
        handle=handle,
    )
    await runner.artifacts.put(artifact_key, revised_text, feature=feature)
    set_step_status(
        control,
        step=step,
        status=STEP_COMPLETE,
        provenance=_merge_provenance(record.get("provenance") or "", revised_provenance),
    )
    setattr(state, state_field, revised_text)
    await persist_planning_control(runner, feature, state, control)
    await _refresh_broad_decisions(
        runner,
        feature,
        state,
        control,
        source_phase=source_phase,
        artifact_kind=artifact_kind,
        source_text=revised_text,
    )
    return revised_text


async def _revise_decomposition_from_reconciliation(
    runner: WorkflowRunner,
    feature: Feature,
    state: BuildState,
    control: dict[str, Any],
    *,
    phase_name: str,
    instruction: str,
    decomposition: SubfeatureDecomposition,
) -> SubfeatureDecomposition:
    step = "decomposition"
    record = get_broad_step_record(control, step)
    handle = await ensure_planning_thread(
        runner,
        feature,
        thread_id="broad:decomposition",
        label="Broad Decomposition",
        existing_thread_ts=str(record.get("thread_ts", "") or ""),
    )
    set_thread_runtime_metadata(
        control,
        step=step,
        resolver=handle.resolver,
        thread_id=handle.thread_id,
        thread_ts=handle.thread_ts,
        label="Broad Decomposition",
    )
    await persist_planning_control(runner, feature, state, control)

    current_text = await get_resumable_artifact(runner, feature, "decomposition") or to_str(decomposition)
    decomp_text, revised_decomposition, revised_provenance = await _run_decomposition_interview(
        runner,
        feature,
        state,
        control,
        phase_name=phase_name,
        initial_prompt=(
            "Please revise the decomposition based on this broad reconciliation finding:\n\n"
            f"{instruction}\n\nCurrent decomposition:\n{current_text}\n\n"
            "Honor any constraints already captured in the broad decision ledger."
        ),
        handle=handle,
    )
    await runner.artifacts.put("decomposition", decomp_text, feature=feature)
    set_step_status(
        control,
        step=step,
        status=STEP_COMPLETE,
        provenance=_merge_provenance(record.get("provenance") or "", revised_provenance),
    )
    state.decomposition = to_str(revised_decomposition)
    await persist_planning_control(runner, feature, state, control)
    return revised_decomposition


async def _apply_broad_reconciliation_revisions(
    runner: WorkflowRunner,
    feature: Feature,
    state: BuildState,
    control: dict[str, Any],
    *,
    phase_name: str,
    decomposition: SubfeatureDecomposition,
    revision_instructions: dict[str, str],
) -> SubfeatureDecomposition:
    for target in ("prd", "design", "architecture", "decomposition"):
        instruction = revision_instructions.get(target, "").strip()
        if not instruction:
            continue
        if target == "prd":
            await _revise_broad_artifact_from_reconciliation(
                runner,
                feature,
                state,
                control,
                phase_name=phase_name,
                step="prd",
                thread_id="broad:prd",
                label="Broad PRD",
                lead_actor=lead_pm,
                background_actor=pm_agent_fill_responder,
                output_type=PRD,
                artifact_key="prd:broad",
                artifact_label="Broad PRD",
                instruction=instruction,
                source_phase="broad-prd",
                artifact_kind="prd",
                state_field="prd",
            )
            continue
        if target == "design":
            await _revise_broad_artifact_from_reconciliation(
                runner,
                feature,
                state,
                control,
                phase_name=phase_name,
                step="design",
                thread_id="broad:design",
                label="Broad Design",
                lead_actor=lead_designer_broad,
                background_actor=design_agent_fill_responder,
                output_type=DesignDecisions,
                artifact_key="design:broad",
                artifact_label="Broad Design System",
                instruction=instruction,
                source_phase="broad-design",
                artifact_kind="design",
                state_field="design",
            )
            continue
        if target == "architecture":
            await _revise_broad_artifact_from_reconciliation(
                runner,
                feature,
                state,
                control,
                phase_name=phase_name,
                step="architecture",
                thread_id="broad:architecture",
                label="Broad Architecture",
                lead_actor=lead_architect_broad,
                background_actor=architect_agent_fill_responder,
                output_type=TechnicalPlan,
                artifact_key="plan:broad",
                artifact_label="Broad Architecture",
                instruction=instruction,
                source_phase="broad-architecture",
                artifact_kind="plan",
                state_field="plan",
            )
            continue
        decomposition = await _revise_decomposition_from_reconciliation(
            runner,
            feature,
            state,
            control,
            phase_name=phase_name,
            instruction=instruction,
            decomposition=decomposition,
        )
    return decomposition


async def _run_broad_reconciliation_stage(
    runner: WorkflowRunner,
    feature: Feature,
    state: BuildState,
    control: dict[str, Any],
    *,
    phase_name: str,
    decomposition: SubfeatureDecomposition,
) -> SubfeatureDecomposition:
    step = "reconciliation"
    record = get_broad_step_record(control, step)
    handle = await ensure_planning_thread(
        runner,
        feature,
        thread_id="broad:reconciliation",
        label="Broad Reconciliation",
        existing_thread_ts=str(record.get("thread_ts", "") or ""),
    )
    set_thread_runtime_metadata(
        control,
        step=step,
        resolver=handle.resolver,
        thread_id=handle.thread_id,
        thread_ts=handle.thread_ts,
        label="Broad Reconciliation",
    )
    await persist_planning_control(runner, feature, state, control)

    review_text = await get_existing_artifact(runner, feature, "integration-review:broad") or ""
    if record.get("status") == STEP_COMPLETE and review_text:
        try:
            IntegrationReview.model_validate(_json.loads(review_text))
            return decomposition
        except Exception:
            logger.warning(
                "Broad reconciliation was marked complete without a valid review artifact; reopening",
            )
            set_step_status(control, step=step, status=STEP_PENDING)
            await persist_planning_control(runner, feature, state, control)
    elif record.get("status") == STEP_COMPLETE and not review_text:
        logger.warning(
            "Broad reconciliation was marked complete without a stored review artifact; reopening",
        )
        set_step_status(control, step=step, status=STEP_PENDING)
        await persist_planning_control(runner, feature, state, control)

    reviewer = make_thread_actor(
        lead_architect_reviewer,
        handle=handle,
        suffix="reconciliation",
        context_keys=list(getattr(lead_architect_reviewer, "context_keys", []) or []),
    )
    threaded_user = make_thread_user(user, resolver=handle.resolver)
    set_step_status(control, step=step, status=STEP_RUNNING)
    await persist_planning_control(runner, feature, state, control)

    while True:
        review = await integration_review(
            runner,
            feature,
            phase_name,
            lead_actor=reviewer,
            decomposition=decomposition,
            artifact_prefix="broad",
            review_key_suffix="broad",
            artifact_keys_by_target={
                "prd": "prd:broad",
                "design": "design:broad",
                "architecture": "plan:broad",
                "decomposition": "decomposition",
            },
            target_label="revision targets",
            use_cached_review=False,
            responder=threaded_user,
            prefer_local_artifacts=True,
        )
        if not review.needs_revision:
            set_step_status(control, step=step, status=STEP_COMPLETE, provenance=record.get("provenance") or "human")
            await persist_planning_control(runner, feature, state, control)
            return decomposition
        if not review.revision_instructions:
            raise RuntimeError(
                "Broad reconciliation requested revisions but did not provide revision_instructions",
            )
        decomposition = await _apply_broad_reconciliation_revisions(
            runner,
            feature,
            state,
            control,
            phase_name=phase_name,
            decomposition=decomposition,
            revision_instructions=review.revision_instructions,
        )


async def _run_broad_artifact_stage(
    runner: WorkflowRunner,
    feature: Feature,
    state: BuildState,
    control: dict[str, Any],
    *,
    phase_name: str,
    step: str,
    thread_id: str,
    label: str,
    lead_actor: Any,
    background_actor: Any,
    output_type: type[Any],
    artifact_key: str,
    artifact_label: str,
    initial_prompt: str,
) -> str:
    record = get_broad_step_record(control, step)
    handle = await ensure_planning_thread(
        runner,
        feature,
        thread_id=thread_id,
        label=label,
        existing_thread_ts=str(record.get("thread_ts", "") or ""),
    )
    set_thread_runtime_metadata(
        control,
        step=step,
        resolver=handle.resolver,
        thread_id=handle.thread_id,
        thread_ts=handle.thread_ts,
        label=label,
    )
    await persist_planning_control(runner, feature, state, control)

    approved_text = await runner.artifacts.get(artifact_key, feature=feature) or ""
    if record.get("status") == STEP_COMPLETE and approved_text:
        provenance = record.get("provenance") or "human"
        await persist_planning_control(runner, feature, state, control)
        await push_artifact_if_present(
            runner,
            feature,
            artifact_key=artifact_key,
            artifact_text=approved_text,
            label=f"{artifact_label} — {feature.name}",
        )
        return approved_text
    if record.get("status") == STEP_COMPLETE and not approved_text:
        logger.warning(
            "Broad step %s was marked complete without an approved artifact; reopening approval flow",
            step,
        )
        set_step_status(control, step=step, status=STEP_PENDING)
        await persist_planning_control(runner, feature, state, control)

    if not record.get("mode_selected"):
        choice = await choose_step_mode(
            runner,
            feature,
            chooser=make_thread_user(user, resolver=handle.resolver),
            phase_name=phase_name,
            prompt=f"How should I handle {label}?",
        )
        set_step_mode(
            control,
            step=step,
            mode=STEP_AGENT_FILL if choice == "Finish in background" else "interactive",
        )
        await persist_planning_control(runner, feature, state, control)

    threaded_user = make_thread_user(user, resolver=handle.resolver)
    draft_text = await get_resumable_artifact(runner, feature, artifact_key)
    provenance = record.get("provenance") or ("agent_fill" if record.get("mode") == STEP_AGENT_FILL else "human")

    if not draft_text:
        draft_text, provenance = await _run_broad_interview(
            runner,
            feature,
            state,
            control,
            phase_name=phase_name,
            step=step,
            label=label,
            lead_actor=lead_actor,
            background_actor=background_actor,
            output_type=output_type,
            artifact_key=artifact_key,
            artifact_label=artifact_label,
            initial_prompt=initial_prompt,
            resolver=handle.resolver,
            handle=handle,
        )

    while True:
        gate_label = _build_gate_label_with_review_urls(
            runner,
            label=artifact_label,
            artifact_keys=[artifact_key],
        )
        approved = await runner.run(
            Gate(
                approver=threaded_user,
                prompt=(
                    f"{gate_label}:\n\n{draft_text}\n\n"
                    "Accept this draft for broad reconciliation?"
                ),
            ),
            feature,
            phase_name=phase_name,
        )
        if approved is True:
            break
        feedback = gate_feedback_text(approved)
        revised_text, revised_provenance = await _run_broad_interview(
            runner,
            feature,
            state,
            control,
            phase_name=phase_name,
            step=step,
            label=label,
            lead_actor=lead_actor,
            background_actor=background_actor,
            output_type=output_type,
            artifact_key=artifact_key,
            artifact_label=artifact_label,
            initial_prompt=(
                f"Please revise {artifact_label} based on this feedback:\n\n"
                f"{feedback}\n\nCurrent draft:\n{draft_text}"
            ),
            resolver=handle.resolver,
            handle=handle,
        )
        draft_text = revised_text
        provenance = _merge_provenance(provenance, revised_provenance)

    await runner.artifacts.put(artifact_key, draft_text, feature=feature)
    set_step_status(control, step=step, status=STEP_COMPLETE, provenance=provenance)
    await persist_planning_control(runner, feature, state, control)
    return draft_text


async def _run_decomposition_stage(
    runner: WorkflowRunner,
    feature: Feature,
    state: BuildState,
    control: dict[str, Any],
    *,
    phase_name: str,
) -> SubfeatureDecomposition:
    step = "decomposition"
    record = get_broad_step_record(control, step)
    handle = await ensure_planning_thread(
        runner,
        feature,
        thread_id="broad:decomposition",
        label="Broad Decomposition",
        existing_thread_ts=str(record.get("thread_ts", "") or ""),
    )
    set_thread_runtime_metadata(
        control,
        step=step,
        resolver=handle.resolver,
        thread_id=handle.thread_id,
        thread_ts=handle.thread_ts,
        label="Broad Decomposition",
    )
    await persist_planning_control(runner, feature, state, control)

    existing = await get_gate_resume_artifact(runner, feature, "decomposition")
    existing_decomposition = _parse_decomposition_if_valid(existing) if existing else None
    if existing and not existing_decomposition:
        logger.warning(
            "Ignoring non-JSON decomposition artifact during resume for %s",
            feature.id,
        )
        existing = None
    if existing_decomposition and record.get("status") == STEP_COMPLETE:
        decomp = existing_decomposition
        hosting = runner.services.get("hosting")
        if hosting and hasattr(hosting, "push"):
            await hosting.push(
                feature.id,
                "decomposition",
                existing or to_str(decomp),
                f"Subfeature Decomposition — {feature.name}",
            )
        set_step_status(control, step=step, status=STEP_COMPLETE, provenance=record.get("provenance") or "human")
        await persist_planning_control(runner, feature, state, control)
        return decomp

    if not record.get("mode_selected"):
        choice = await choose_step_mode(
            runner,
            feature,
            chooser=make_thread_user(user, resolver=handle.resolver),
            phase_name=phase_name,
            prompt="How should I handle broad decomposition?",
        )
        set_step_mode(
            control,
            step=step,
            mode=STEP_AGENT_FILL if choice == "Finish in background" else "interactive",
        )
        await persist_planning_control(runner, feature, state, control)

    _, _, threaded_user = _build_decomposition_interview_actors(handle)

    def _read_decomposition(result: Any) -> SubfeatureDecomposition:
        if isinstance(result, SubfeatureDecomposition):
            return result
        output = getattr(result, "output", None)
        if output is not None:
            return output
        text = to_str(result)
        return _parse_model(SubfeatureDecomposition, text)

    provenance = "agent_fill" if record.get("mode") == STEP_AGENT_FILL else "human"
    if existing_decomposition and record.get("status") != STEP_COMPLETE:
        decomp_text = existing
        decomposition = existing_decomposition
        hosting = runner.services.get("hosting")
        if hosting and hasattr(hosting, "push"):
            await hosting.push(
                feature.id,
                "decomposition",
                decomp_text,
                f"Subfeature Decomposition — {feature.name}",
            )
    else:
        decomp_text, decomposition, provenance = await _run_decomposition_interview(
            runner,
            feature,
            state,
            control,
            phase_name=phase_name,
            initial_prompt=(
            "Based on the broad PRD, design, and architecture, decompose this feature into "
            "subfeatures. Each subfeature will be planned in its own persistent thread with "
            "PM, design, then architecture. Honor any constraints already captured in the "
            "broad decision ledger, and ask clarifying questions until the split is crisp."
            ),
            handle=handle,
        )
    approved = await runner.run(
        Gate(
            approver=threaded_user,
            prompt=(
                f"{_build_gate_label_with_review_urls(runner, label='Subfeature Decomposition', artifact_keys=['decomposition'])}:\n\n"
                f"{decomp_text}\n\nAccept this draft for broad reconciliation?"
            ),
        ),
        feature,
        phase_name=phase_name,
    )
    if approved is not True:
        feedback = gate_feedback_text(approved)
        decomp_text, decomposition, revised_provenance = await _run_decomposition_interview(
            runner,
            feature,
            state,
            control,
            phase_name=phase_name,
            initial_prompt=(
                f"Please revise the decomposition based on this feedback:\n\n{feedback}\n\n"
                f"Current decomposition:\n{decomp_text}"
            ),
            handle=handle,
        )
        provenance = _merge_provenance(provenance, revised_provenance)

    await runner.artifacts.put("decomposition", decomp_text, feature=feature)
    hosting = runner.services.get("hosting")
    if hosting and hasattr(hosting, "push"):
        await hosting.push(
            feature.id,
            "decomposition",
            decomp_text,
            f"Subfeature Decomposition — {feature.name}",
        )
    set_step_status(control, step=step, status=STEP_COMPLETE, provenance=provenance)
    await persist_planning_control(runner, feature, state, control)
    return decomposition


async def _collect_subfeature_step_policies(
    runner: WorkflowRunner,
    feature: Feature,
    state: BuildState,
    control: dict[str, Any],
    decomposition: SubfeatureDecomposition,
    *,
    phase_name: str,
) -> None:
    del phase_name
    sync_subfeature_threads(control, decomposition)
    for sf in decomposition.subfeatures:
        thread = control["subfeatures"][sf.slug]
        handle = await ensure_planning_thread(
            runner,
            feature,
            thread_id=str(thread.get("thread_id", f"subfeature:{sf.slug}") or f"subfeature:{sf.slug}"),
            label=sf.name,
            existing_thread_ts=str(thread.get("thread_ts", "") or ""),
        )
        for step, title in (
            ("pm", "PM"),
            ("design", "Design"),
            ("architecture", "Architecture"),
        ):
            del title
            step_record = thread["steps"][step]
            set_thread_runtime_metadata(
                control,
                slug=sf.slug,
                step=step,
                resolver=handle.resolver,
                thread_id=handle.thread_id,
                thread_ts=handle.thread_ts,
                label=sf.name,
            )
            if not step_record.get("mode_selected") and not step_record.get("mode"):
                step_record["mode"] = "interactive"
    await persist_planning_control(runner, feature, state, control)


class BroadPhase(Phase):
    name = "broad"

    async def execute(
        self, runner: WorkflowRunner, feature: Feature, state: BuildState
    ) -> BuildState:
        control = load_planning_control(state=state, feature=feature)
        set_current_stage(control, "broad")
        await persist_planning_control(runner, feature, state, control)

        broad_prd = await _run_broad_artifact_stage(
            runner,
            feature,
            state,
            control,
            phase_name=self.name,
            step="prd",
            thread_id="broad:prd",
            label="Broad PRD",
            lead_actor=lead_pm,
            background_actor=pm_agent_fill_responder,
            output_type=PRD,
            artifact_key="prd:broad",
            artifact_label="Broad PRD",
            initial_prompt=(
                f"I'm going to help you define high-level requirements for: {feature.name}\n\n"
                "We'll start with a broad overview before we split the work into subfeatures. "
                "What is the main goal of this feature?"
            ),
        )
        await _refresh_broad_decisions(
            runner,
            feature,
            state,
            control,
            source_phase="broad-prd",
            artifact_kind="prd",
            source_text=broad_prd,
        )

        broad_design = await _run_broad_artifact_stage(
            runner,
            feature,
            state,
            control,
            phase_name=self.name,
            step="design",
            thread_id="broad:design",
            label="Broad Design",
            lead_actor=lead_designer_broad,
            background_actor=design_agent_fill_responder,
            output_type=DesignDecisions,
            artifact_key="design:broad",
            artifact_label="Broad Design System",
            initial_prompt=(
                f"I'm going to establish the design foundation for: {feature.name}\n\n"
                "Use the broad PRD to define the design system, shared visual language, "
                "tokens, and reusable interaction patterns that every subfeature should inherit."
            ),
        )
        await _refresh_broad_decisions(
            runner,
            feature,
            state,
            control,
            source_phase="broad-design",
            artifact_kind="design",
            source_text=broad_design,
        )

        broad_plan = await _run_broad_artifact_stage(
            runner,
            feature,
            state,
            control,
            phase_name=self.name,
            step="architecture",
            thread_id="broad:architecture",
            label="Broad Architecture",
            lead_actor=lead_architect_broad,
            background_actor=architect_agent_fill_responder,
            output_type=TechnicalPlan,
            artifact_key="plan:broad",
            artifact_label="Broad Architecture",
            initial_prompt=(
                f"I'm going to establish the system architecture for: {feature.name}\n\n"
                "Use the broad PRD and broad design system to define the system topology, "
                "technical constraints, platform conventions, and cross-cutting architecture "
                "that every subfeature should build on."
            ),
        )
        await _refresh_broad_decisions(
            runner,
            feature,
            state,
            control,
            source_phase="broad-architecture",
            artifact_kind="plan",
            source_text=broad_plan,
        )

        state.prd = broad_prd
        state.design = broad_design
        state.plan = broad_plan

        decomposition = await _run_decomposition_stage(
            runner,
            feature,
            state,
            control,
            phase_name=self.name,
        )
        state.decomposition = to_str(decomposition)
        decomposition = await _run_broad_reconciliation_stage(
            runner,
            feature,
            state,
            control,
            phase_name=self.name,
            decomposition=decomposition,
        )
        state.decomposition = to_str(decomposition)

        await _collect_subfeature_step_policies(
            runner,
            feature,
            state,
            control,
            decomposition,
            phase_name=self.name,
        )
        set_current_stage(control, "subfeature")
        await persist_planning_control(runner, feature, state, control)
        return state
