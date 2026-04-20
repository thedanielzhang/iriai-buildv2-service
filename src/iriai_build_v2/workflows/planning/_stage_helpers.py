from __future__ import annotations

from pathlib import Path
from typing import Any

from iriai_compose import to_str

from ...services.artifacts import _key_to_path
from ...models.outputs import RevisionPlan, RevisionRequest, SubfeatureDecomposition
from .._common import Choose, Gate, get_existing_artifact
from .._common._tasks import ThreadedInterviewOutcome
from ._threading import build_agent_fill_prompt, write_thread_context_file, write_thread_file


async def continue_threaded_interview_in_background(
    runner: Any,
    feature: Any,
    *,
    questioner: Any,
    background_responder: Any,
    pending_response: Any,
    context_keys: list[str] | None,
    output_type: type[Any] | None,
    done: Any,
    label: str,
) -> Any:
    response = pending_response
    while True:
        answer = await runner.resolve(
            background_responder,
            build_agent_fill_prompt(label=label, response_text=to_str(response)),
            feature=feature,
        )
        result = await runner.resolve(
            questioner,
            f"The responder replied:\n\n{to_str(answer)}",
            feature=feature,
            context_keys=context_keys,
            output_type=output_type,
            continuation=True,
        )
        if done(result):
            return result
        response = result


async def read_single_artifact_text(
    runner: Any,
    feature: Any,
    *,
    artifact_key: str,
    result: Any,
) -> str:
    text = await get_existing_artifact(runner, feature, artifact_key)
    if text:
        return text
    output = getattr(result, "output", None)
    if output is not None:
        return to_str(output)
    return to_str(result)


async def push_artifact_if_present(
    runner: Any,
    feature: Any,
    *,
    artifact_key: str,
    artifact_text: str,
    label: str,
) -> None:
    if not artifact_text:
        return
    await runner.artifacts.put(artifact_key, artifact_text, feature=feature)
    hosting = runner.services.get("hosting")
    if hosting:
        await hosting.push(feature.id, artifact_key, artifact_text, label)


def build_subfeature_context_text(
    decomposition: SubfeatureDecomposition,
    current_slug: str,
    *,
    broad_sections: list[tuple[str, str]],
    own_sections: list[tuple[str, str]],
    stage_artifacts: dict[str, str],
    stage_summaries: dict[str, str],
    decision_sections: list[tuple[str, str]] | None = None,
) -> str:
    sections: list[str] = []
    for title, text in broad_sections:
        if text:
            sections.append(f"## {title}\n\n{text}")
    for title, text in own_sections:
        if text:
            sections.append(f"## {title}\n\n{text}")
    for title, text in decision_sections or []:
        if text:
            sections.append(f"## {title}\n\n{text}")

    upstream = [
        edge.from_subfeature
        for edge in decomposition.edges
        if edge.to_subfeature == current_slug and edge.from_subfeature in stage_artifacts
    ]
    if upstream:
        parts = []
        for slug in upstream:
            parts.append(f"### Upstream: {slug}\n\n{stage_artifacts[slug]}")
        sections.append("## Completed Upstream Subfeatures\n\n" + "\n\n".join(parts))

    remaining = [
        slug for slug in stage_summaries
        if slug not in upstream and slug != current_slug
    ]
    if remaining:
        parts = []
        for slug in remaining:
            parts.append(f"### {slug}\n\n{stage_summaries[slug]}")
        sections.append("## Other Completed Subfeature Summaries\n\n" + "\n\n".join(parts))

    return "\n\n".join(sections)


def build_related_decision_sections(
    decomposition: SubfeatureDecomposition,
    current_slug: str,
    *,
    broad_text: str,
    own_text: str,
    completed_artifacts: dict[str, str],
    completed_summaries: dict[str, str],
) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    if broad_text:
        sections.append(("Broad Decision Ledger", broad_text))
    if own_text:
        sections.append(("Current Subfeature Decisions", own_text))

    upstream = [
        edge.from_subfeature
        for edge in decomposition.edges
        if edge.to_subfeature == current_slug and edge.from_subfeature in completed_artifacts
    ]
    if upstream:
        body = "\n\n".join(
            f"### Upstream: {slug}\n\n{completed_artifacts[slug]}"
            for slug in upstream
        )
        sections.append(("Completed Upstream Decisions", body))

    remaining = [
        slug for slug in completed_summaries
        if slug not in upstream and slug != current_slug
    ]
    if remaining:
        body = "\n\n".join(
            f"### {slug}\n\n{completed_summaries[slug]}"
            for slug in remaining
        )
        sections.append(("Other Decision Summaries", body))

    return sections


def write_subfeature_context_file(
    runner: Any,
    feature: Any,
    *,
    thread_id: str,
    step: str,
    context_text: str,
) -> str:
    return write_thread_context_file(
        runner,
        feature,
        thread_id=thread_id,
        step=step,
        content=context_text,
    )


def _artifact_source_path(
    runner: Any,
    feature: Any,
    *,
    artifact_key: str,
) -> str | None:
    mirror = runner.services.get("artifact_mirror")
    if not mirror:
        return None
    path = Path(mirror.feature_dir(feature.id)) / _key_to_path(artifact_key)
    if not path.exists():
        return None
    return str(path)


def build_subfeature_context_manifest_text(
    *,
    groups: list[tuple[str, list[tuple[str, str]]]],
) -> str:
    sections: list[str] = ["# Subfeature Context Manifest"]
    for title, entries in groups:
        visible_entries = [(label, path) for label, path in entries if path]
        if not visible_entries:
            continue
        lines = [f"## {title}"]
        for label, path in visible_entries:
            lines.append(f"- **{label}**: `{path}`")
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


def write_subfeature_context_manifest_file(
    runner: Any,
    feature: Any,
    *,
    thread_id: str,
    step: str,
    manifest_text: str,
) -> str:
    return write_thread_file(
        runner,
        feature,
        thread_id=thread_id,
        file_name=f"{step}-context-manifest.md",
        content=manifest_text,
    )


def planning_index_artifact_key(step: str, slug: str) -> str:
    return f"planning-index-{step.replace('_', '-')}:{slug}"


def build_planning_index_text(
    *,
    step_title: str,
    subfeature_name: str,
    context_path: str,
    manifest_path: str,
) -> str:
    return (
        f"Planning context index for {subfeature_name} — {step_title}.\n\n"
        f"Read the context manifest first: `{manifest_path}`\n"
        f"Use the merged overview context file as the canonical overview/reference: `{context_path}`\n"
        "Open the referenced source files selectively instead of loading everything eagerly."
    )


async def prepare_subfeature_context_artifacts(
    runner: Any,
    feature: Any,
    *,
    thread_id: str,
    step: str,
    step_title: str,
    slug: str,
    subfeature_name: str,
    context_text: str,
    source_groups: list[tuple[str, list[tuple[str, str]]]],
) -> tuple[str, str, str]:
    context_path = write_subfeature_context_file(
        runner,
        feature,
        thread_id=thread_id,
        step=step,
        context_text=context_text,
    )
    manifest_text = build_subfeature_context_manifest_text(groups=source_groups)
    manifest_path = write_subfeature_context_manifest_file(
        runner,
        feature,
        thread_id=thread_id,
        step=step,
        manifest_text=manifest_text,
    )
    planning_index_key = planning_index_artifact_key(step, slug)
    await runner.artifacts.put(
        planning_index_key,
        build_planning_index_text(
            step_title=step_title,
            subfeature_name=subfeature_name,
            context_path=context_path,
            manifest_path=manifest_path,
        ),
        feature=feature,
    )
    return context_path, manifest_path, planning_index_key


def build_revision_plan(
    instructions: dict[str, str],
    *,
    reason: str,
    artifact_types: list[str] | None = None,
) -> RevisionPlan:
    return RevisionPlan(
        requests=[
            RevisionRequest(
                description=instruction,
                reasoning=reason,
                affected_subfeatures=[slug],
                affected_artifact_types=list(artifact_types or []),
            )
            for slug, instruction in instructions.items()
        ]
    )


async def choose_step_mode(
    runner: Any,
    feature: Any,
    *,
    chooser: Any,
    phase_name: str,
    prompt: str,
) -> str:
    return await runner.run(
        Choose(
            chooser=chooser,
            prompt=prompt,
            options=["Interactive", "Finish in background"],
        ),
        feature,
        phase_name=phase_name,
    )


async def gate_text(
    runner: Any,
    feature: Any,
    *,
    approver: Any,
    phase_name: str,
    prompt: str,
) -> Any:
    return await runner.run(
        Gate(approver=approver, prompt=prompt),
        feature,
        phase_name=phase_name,
    )


def outcome_background_requested(result: Any) -> bool:
    return isinstance(result, ThreadedInterviewOutcome) and result.background_requested


def thread_outcome_pending_response(result: Any) -> Any:
    if isinstance(result, ThreadedInterviewOutcome):
        return result.pending_response
    return None
