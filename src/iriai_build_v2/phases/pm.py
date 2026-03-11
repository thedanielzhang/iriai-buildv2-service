from __future__ import annotations

from iriai_compose import Feature, Interview, Phase, WorkflowRunner

from ..models.outputs import PRD, Envelope, envelope_done
from ..models.state import BuildState
from ..roles import pm, user
from ._helpers import gate_and_revise


class PMPhase(Phase):
    name = "pm"

    async def execute(
        self, runner: WorkflowRunner, feature: Feature, state: BuildState
    ) -> BuildState:
        envelope: Envelope[PRD] = await runner.run(
            Interview(
                questioner=pm,
                responder=user,
                initial_prompt=(
                    f"I'm going to help you define requirements for: {feature.name}\n\n"
                    "Let me ask some clarifying questions to build a comprehensive PRD. "
                    "What is the main goal of this feature?"
                ),
                output_type=Envelope[PRD],
                done=envelope_done,
            ),
            feature,
            phase_name=self.name,
        )

        prd = envelope.output
        assert prd is not None

        prd, prd_text = await gate_and_revise(
            runner, feature, self.name,
            artifact=prd, actor=pm, output_type=PRD,
            approver=user, label="PRD",
        )

        await runner.artifacts.put("prd", prd_text, feature=feature)
        state.prd = prd_text
        return state
