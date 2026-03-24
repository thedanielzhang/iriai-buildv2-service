from __future__ import annotations

import json as _json
import logging

from iriai_compose import Feature, Phase, WorkflowRunner, to_str

from ....models.outputs import (
    GlobalImplementationStrategy,
    ImplementationDAG,
    RevisionPlan,
    RevisionRequest,
    SubfeatureDecomposition,
)
from ....models.state import BuildState
from ....roles import (
    dag_compiler,
    lead_task_planner,
    lead_task_planner_gate_reviewer,
    lead_task_planner_reviewer,
    planning_lead_role,
    user,
)
from ..._common import (
    broad_interview,
    compile_artifacts,
    get_existing_artifact,
    integration_review,
    interview_gate_review,
    per_subfeature_loop,
)
from ..._common._helpers import targeted_revision

logger = logging.getLogger(__name__)


def _make_sf_prompt(sf, context: str) -> str:
    """Build the initial interview prompt for a per-subfeature task planner agent."""
    return (
        f"You are the task planner for the **{sf.name}** subfeature (ID: {sf.id}, slug: {sf.slug}).\n\n"
        f"**Description:** {sf.description}\n\n"
        "Break the technical plan for this subfeature into parallelizable "
        "implementation tasks. Each task needs file scope, acceptance criteria, "
        "counterexamples, requirement traceability, and a subfeature_id.\n\n"
        f"Set `subfeature_id` to '{sf.id}' on every task you produce.\n\n"
        f"## Context from prior work\n\n{context}"
    )


class TaskPlanningPhase(Phase):
    name = "task-planning"

    async def execute(
        self, runner: WorkflowRunner, feature: Feature, state: BuildState
    ) -> BuildState:
        # ── Step 1: Resume check ──
        # DB = gate-approved → skip entirely
        approved_dag = await runner.artifacts.get("dag", feature=feature)
        if approved_dag:
            logger.info("Gate-approved DAG exists — skipping")
            state.dag = approved_dag
            return state

        # Filesystem-only = compiled but not gate-reviewed → jump to gate review
        compiled_dag = await get_existing_artifact(runner, feature, "dag")
        if compiled_dag:
            logger.info("Compiled DAG exists but not gate-reviewed — running gate review")
            decomposition = await self._load_decomposition(runner, feature, state)
            final_text = await interview_gate_review(
                runner, feature, self.name,
                lead_actor=lead_task_planner_gate_reviewer,
                decomposition=decomposition,
                artifact_prefix="dag",
                compiled_key="dag",
                base_role=planning_lead_role,
                output_type=ImplementationDAG,
                compiler_actor=dag_compiler,
                broad_key="dag:strategy",
                context_keys=["project", "scope", "prd", "design", "plan", "system-design"],
            )
            await runner.artifacts.put("dag", final_text, feature=feature)
            state.dag = final_text
            return state

        decomposition = await self._load_decomposition(runner, feature, state)

        # ── Step 2: Global Implementation Strategy ──
        strategy, strategy_text = await broad_interview(
            runner, feature, self.name,
            lead_actor=lead_task_planner,
            output_type=GlobalImplementationStrategy,
            artifact_key="dag:strategy",
            artifact_label="Global Implementation Strategy",
            initial_prompt=(
                f"I'm going to establish the global implementation strategy for: {feature.name}\n\n"
                "We need to determine the subfeature execution order, shared infrastructure "
                "tasks, cross-subfeature dependencies, and parallel opportunities.\n\n"
                "Let me ask about constraints, team size, and dependencies."
            ),
        )

        # ── Step 3: Per-Subfeature Task Planning Loop (sequential) ──
        # Use execution order from strategy if available
        ordered_decomp = decomposition
        if hasattr(strategy, "subfeature_execution_order") and strategy.subfeature_execution_order:
            order = strategy.subfeature_execution_order
            sf_by_slug = {sf.slug: sf for sf in decomposition.subfeatures}
            ordered_sfs = [sf_by_slug[slug] for slug in order if slug in sf_by_slug]
            # Append any not in the order
            remaining = [sf for sf in decomposition.subfeatures if sf.slug not in set(order)]
            ordered_decomp = SubfeatureDecomposition(
                subfeatures=ordered_sfs + remaining,
                edges=decomposition.edges,
                decomposition_rationale=decomposition.decomposition_rationale,
                complete=decomposition.complete,
            )

        await per_subfeature_loop(
            runner, feature, self.name,
            decomposition=ordered_decomp,
            base_role=planning_lead_role,
            output_type=ImplementationDAG,
            artifact_prefix="dag",
            broad_key="dag:strategy",
            make_prompt=_make_sf_prompt,
            context_keys=["project", "scope", "prd", "design", "plan", "system-design"],
        )

        # ── Step 4: DAG Integration Review ──
        review = await integration_review(
            runner, feature, self.name,
            lead_actor=lead_task_planner_reviewer,
            decomposition=ordered_decomp,
            artifact_prefix="dag",
            broad_key="dag:strategy",
        )

        if review.needs_revision:
            if not review.revision_instructions:
                logger.error(
                    "TaskPlanningPhase: integration review needs_revision=True but "
                    "revision_instructions is empty — skipping revision"
                )
            else:
                logger.info(
                    "DAG integration review needs revision — re-running %d subfeatures",
                    len(review.revision_instructions),
                )
                plan = RevisionPlan(requests=[
                    RevisionRequest(
                        description=instruction,
                        reasoning="DAG integration review finding",
                        affected_subfeatures=[sf_slug],
                    )
                    for sf_slug, instruction in review.revision_instructions.items()
                ])
                await targeted_revision(
                    runner, feature, self.name,
                    revision_plan=plan,
                    decomposition=ordered_decomp,
                    base_role=planning_lead_role,
                    output_type=ImplementationDAG,
                    artifact_prefix="dag",
                    context_keys=["project", "scope", "prd", "design", "plan", "system-design"],
                )

        # ── Step 5: DAG Compilation ──
        dag_text = await compile_artifacts(
            runner, feature, self.name,
            compiler_actor=dag_compiler,
            decomposition=ordered_decomp,
            artifact_prefix="dag",
            broad_key="dag:strategy",
            final_key="dag",
        )

        # ── Step 6: Interview-Based Gate Review ──
        final_text = await interview_gate_review(
            runner, feature, self.name,
            lead_actor=lead_task_planner_gate_reviewer,
            decomposition=ordered_decomp,
            artifact_prefix="dag",
            compiled_key="dag",
            base_role=planning_lead_role,
            output_type=ImplementationDAG,
            compiler_actor=dag_compiler,
            broad_key="dag:strategy",
            context_keys=["project", "scope", "prd", "design", "plan", "system-design"],
        )

        await runner.artifacts.put("dag", final_text, feature=feature)
        state.dag = final_text
        return state

    @staticmethod
    async def _load_decomposition(
        runner: WorkflowRunner, feature: Feature, state: BuildState
    ) -> SubfeatureDecomposition:
        """Load decomposition from state or artifact store."""
        decomp_text = state.decomposition
        if not decomp_text:
            decomp_text = await runner.artifacts.get("decomposition", feature=feature) or ""
        if decomp_text:
            try:
                return SubfeatureDecomposition.model_validate(_json.loads(decomp_text))
            except Exception:
                pass
        return SubfeatureDecomposition()
