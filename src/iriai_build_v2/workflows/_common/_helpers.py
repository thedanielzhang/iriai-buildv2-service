from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

from pydantic import BaseModel

from iriai_compose import Ask, Gate, to_str
from iriai_compose.actors import Actor

if TYPE_CHECKING:
    from iriai_compose import Feature, WorkflowRunner

T = TypeVar("T", bound=BaseModel)


async def gate_and_revise(
    runner: WorkflowRunner,
    feature: Feature,
    phase_name: str,
    *,
    artifact: BaseModel,
    actor: Actor,
    output_type: type[T],
    approver: Actor,
    label: str,
    max_attempts: int = 3,
) -> tuple[T | BaseModel, str]:
    """Approve/revise loop. Returns ``(artifact, artifact_text)``.

    Presents *artifact* to *approver* via a Gate.  On rejection the *actor*
    is asked to revise using the feedback, up to *max_attempts* times.

    The off-by-one is avoided: the loop gates first, then revises only if
    rejected **and** attempts remain.  After the loop the most-recent
    artifact (original or last revision) is always returned.
    """
    artifact_text = to_str(artifact)

    for attempt in range(max_attempts):
        approved = await runner.run(
            Gate(approver=approver, prompt=f"{label}:\n\n{artifact_text}\n\nApprove?"),
            feature,
            phase_name=phase_name,
        )
        if approved is True:
            break

        feedback = str(approved) if isinstance(approved, str) else "Please revise."
        artifact = await runner.run(
            Ask(
                actor=actor,
                prompt=f"Revise the {label.lower()} based on this feedback:\n\n{feedback}",
                output_type=output_type,
            ),
            feature,
            phase_name=phase_name,
        )
        artifact_text = to_str(artifact)

    return artifact, artifact_text
