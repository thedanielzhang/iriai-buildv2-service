from __future__ import annotations

from iriai_compose import Feature, Phase, WorkflowRunner

from ....models.outputs import Envelope, ImplementationDAG, envelope_done
from ....models.state import BuildState
from ....roles import task_planner, user
from ..._common import HostedInterview, gate_and_revise


class TaskPlanningPhase(Phase):
    name = "task-planning"

    async def execute(
        self, runner: WorkflowRunner, feature: Feature, state: BuildState
    ) -> BuildState:
        envelope: Envelope[ImplementationDAG] = await runner.run(
            HostedInterview(
                questioner=task_planner,
                responder=user,
                initial_prompt=(
                    "I'll break the technical plan into parallelizable implementation tasks. "
                    "Let me ask about constraints, team size, and task dependencies."
                ),
                output_type=Envelope[ImplementationDAG],
                done=envelope_done,
                artifact_key="dag",
                artifact_label="Implementation DAG",
            ),
            feature,
            phase_name=self.name,
        )

        dag = envelope.output

        dag, dag_text = await gate_and_revise(
            runner, feature, self.name,
            artifact=dag, actor=task_planner, output_type=ImplementationDAG,
            approver=user, label="Implementation DAG",
            artifact_key="dag",
        )

        await runner.artifacts.put("dag", dag_text, feature=feature)
        state.dag = dag_text
        return state
