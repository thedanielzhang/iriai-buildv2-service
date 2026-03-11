from __future__ import annotations

from iriai_compose import Feature, Interview, Phase, WorkflowRunner

from ..models.outputs import Envelope, TechnicalPlan, envelope_done
from ..models.state import BuildState
from ..roles import architect, user
from ._helpers import gate_and_revise


class ArchitecturePhase(Phase):
    name = "architecture"

    async def execute(
        self, runner: WorkflowRunner, feature: Feature, state: BuildState
    ) -> BuildState:
        envelope: Envelope[TechnicalPlan] = await runner.run(
            Interview(
                questioner=architect,
                responder=user,
                initial_prompt=(
                    "I'll explore the codebase and ask questions to build a technical plan. "
                    "Let me start by understanding the project structure. "
                    "What area of the codebase should I focus on?"
                ),
                output_type=Envelope[TechnicalPlan],
                done=envelope_done,
            ),
            feature,
            phase_name=self.name,
        )

        plan = envelope.output
        assert plan is not None

        plan, plan_text = await gate_and_revise(
            runner, feature, self.name,
            artifact=plan, actor=architect, output_type=TechnicalPlan,
            approver=user, label="Technical plan",
        )

        await runner.artifacts.put("plan", plan_text, feature=feature)
        state.plan = plan_text
        return state
