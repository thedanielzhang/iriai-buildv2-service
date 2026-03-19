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
    annotation_keys: list[str] | None = None,
) -> tuple[T | BaseModel | str, str]:
    """Approve/revise loop. Returns ``(artifact, artifact_text)``.

    Presents *artifact* to *approver* via a Gate.  On rejection the *actor*
    is asked to revise using the feedback.  When *artifact_key* is provided,
    browser annotations are collected from the hosting service automatically.
    """
    artifact_text = to_str(artifact) if isinstance(artifact, BaseModel) else artifact
    artifact_name = label.split("\n", 1)[0]

    # Strip pre-embedded URLs so we can rebuild them fresh each iteration
    clean_label = "\n".join(
        line for line in label.splitlines()
        if "Review in browser:" not in line
    ).strip()

    # Keys to look up URLs for (annotation_keys includes mockup, etc.)
    url_keys = annotation_keys or ([artifact_key] if artifact_key else [])

    while True:
        # Rebuild review URLs from hosting each iteration (ports may change after update)
        gate_label = clean_label
        hosting = runner.services.get("hosting")
        if hosting and url_keys:
            urls = [hosting.get_url(k) for k in url_keys]
            urls = [u for u in urls if u]
            if urls:
                gate_label += "\nReview in browser: " + " | ".join(urls)

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
        collect_keys = annotation_keys or ([artifact_key] if artifact_key else [])
        if collect_keys:
            hosting = runner.services.get("hosting")
            if hosting:
                all_annotations: list[dict] = []
                for ck in collect_keys:
                    try:
                        anns = await hosting.try_collect(ck)
                        logger.warning("[diag] gate_and_revise: collected %d annotations for %r", len(anns), ck)
                        all_annotations.extend(anns)
                    except Exception:
                        logger.warning("[diag] gate_and_revise: try_collect(%r) raised", ck, exc_info=True)
                if all_annotations:
                    ann_lines = [
                        f"- [{a['data'].get('selected_text', '')}] {a['comment']}"
                        for a in all_annotations if a.get("comment")
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
