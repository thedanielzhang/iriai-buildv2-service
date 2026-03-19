from __future__ import annotations

import logging
from typing import TYPE_CHECKING, TypeVar

from pydantic import BaseModel

from iriai_compose import Ask, Gate, to_str
from iriai_compose.actors import Actor

if TYPE_CHECKING:
    from iriai_compose import Feature, WorkflowRunner

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


async def gate_and_revise(
    runner: WorkflowRunner,
    feature: Feature,
    phase_name: str,
    *,
    artifact: BaseModel | str,
    actor: Actor,
    output_type: type[T],
    approver: Actor,
    label: str,
    artifact_key: str | None = None,
) -> tuple[T | BaseModel | str, str]:
    """Approve/revise loop. Returns ``(artifact, artifact_text)``.

    Presents *artifact* to *approver* via a Gate.  On rejection the *actor*
    is asked to revise using the feedback.  When *artifact_key* is provided,
    browser annotations are collected from the hosting service automatically.
    """
    artifact_text = to_str(artifact) if isinstance(artifact, BaseModel) else artifact
    artifact_name = label.split("\n", 1)[0]
    base_label = label  # preserve caller's label (may include multiple URLs)

    while True:
        # Refresh review URL each iteration (port may change after hosting.update)
        gate_label = base_label
        if artifact_key and "Review in browser:" not in gate_label:
            hosting = runner.services.get("hosting")
            if hosting:
                url = hosting.get_url(artifact_key)
                if url:
                    gate_label = f"{gate_label}\nReview in browser: {url}"

        approved = await runner.run(
            Gate(approver=approver, prompt=f"{gate_label}:\n\n{artifact_text}\n\nApprove?"),
            feature,
            phase_name=phase_name,
        )
        if approved is True:
            break

        feedback = str(approved) if isinstance(approved, str) else "Please revise."

        # Collect browser annotations AFTER rejection — the user annotates
        # while reviewing, then clicks reject.
        if artifact_key:
            hosting = runner.services.get("hosting")
            if hosting:
                try:
                    annotations = await hosting.try_collect(artifact_key)
                    logger.warning("[diag] gate_and_revise: collected %d annotations for %r", len(annotations), artifact_key)
                except Exception:
                    logger.warning("[diag] gate_and_revise: try_collect raised", exc_info=True)
                    annotations = []
                if annotations:
                    ann_lines = [
                        f"- [{a['data'].get('selected_text', '')}] {a['comment']}"
                        for a in annotations if a.get("comment")
                    ]
                    if ann_lines:
                        feedback += "\n\nReviewer annotations:\n" + "\n".join(ann_lines)

        artifact = await runner.run(
            Ask(
                actor=actor,
                prompt=f"Revise the {artifact_name.lower()} based on this feedback:\n\n{feedback}",
                output_type=output_type,
            ),
            feature,
            phase_name=phase_name,
        )
        artifact_text = to_str(artifact)

        # Update the hosted doc so the browser shows the revised version
        if artifact_key:
            hosting = runner.services.get("hosting")
            if hosting:
                await hosting.update(feature.id, artifact_key, artifact_text)

    return artifact, artifact_text
