from __future__ import annotations

from iriai_compose import Feature, Interview, Phase, WorkflowRunner

from ....models.outputs import DesignDecisions, Envelope, envelope_done
from ....models.state import BuildState
from ....roles import designer, user
from ..._common import gate_and_revise


class DesignPhase(Phase):
    name = "design"

    async def execute(
        self, runner: WorkflowRunner, feature: Feature, state: BuildState
    ) -> BuildState:
        envelope: Envelope[DesignDecisions] = await runner.run(
            Interview(
                questioner=designer,
                responder=user,
                initial_prompt=(
                    "Based on the PRD, I'll propose design decisions including component "
                    "structure, user flows, and interaction patterns. Let me ask a few "
                    "clarifying questions about your UX preferences first."
                ),
                output_type=Envelope[DesignDecisions],
                done=envelope_done,
            ),
            feature,
            phase_name=self.name,
        )

        design = envelope.output
        assert design is not None

        design, design_text = await gate_and_revise(
            runner, feature, self.name,
            artifact=design, actor=designer, output_type=DesignDecisions,
            approver=user, label="Design decisions",
        )

        await runner.artifacts.put("design", design_text, feature=feature)
        state.design = design_text
        return state
