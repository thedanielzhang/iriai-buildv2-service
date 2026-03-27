from __future__ import annotations

import json as _json
import logging

from iriai_compose import Feature, Phase, WorkflowRunner

from ....models.outputs import (
    GlobalImplementationStrategy,
    ImplementationDAG,
    RevisionPlan,
    RevisionRequest,
    SubfeatureDecomposition,
)
from ....models.state import BuildState
from ....roles import (
    InterviewActor,
    dag_compiler,
    planning_lead_role,
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

# ── Lightweight actors with reduced context_keys ─────────────────────────────
# The default lead_task_planner actors have context_keys that pull in ALL
# compiled artifacts (prd, design, plan, system-design = ~1.3M). These
# lightweight variants only get project + scope + decomposition; upstream
# artifacts are injected per-SF in the prompt.

_strategy_planner = InterviewActor(
    name="task-strategy-planner",
    role=planning_lead_role,
    context_keys=["project", "scope", "decomposition"],
)

_sf_task_planner_reviewer = InterviewActor(
    name="sf-task-planner-reviewer",
    role=planning_lead_role,
    context_keys=["project", "scope", "decomposition"],
)

_sf_task_planner_gate_reviewer = InterviewActor(
    name="sf-task-planner-gate-reviewer",
    role=planning_lead_role,
    context_keys=["project", "scope", "decomposition"],
)


# ── Prompt builders ──────────────────────────────────────────────────────────


def _make_sf_prompt_with_upstream(
    sf_upstream: dict[str, dict[str, str]],
) -> callable:
    """Create a make_prompt closure that injects per-SF upstream artifacts.

    ``sf_upstream`` is ``{slug: {prefix: text}}`` preloaded from the artifact store.
    """
    def _make_prompt(sf, context: str) -> str:
        # Inject this SF's upstream artifacts
        upstream_parts: list[str] = []
        sf_artifacts = sf_upstream.get(sf.slug, {})
        for prefix in ("prd", "design", "plan", "system-design"):
            text = sf_artifacts.get(prefix)
            if text:
                upstream_parts.append(f"## {prefix.upper()} for {sf.slug}\n\n{text}")
        upstream_context = "\n\n---\n\n".join(upstream_parts)

        return (
            f"You are the task planner for the **{sf.name}** subfeature "
            f"(ID: {sf.id}, slug: {sf.slug}).\n\n"
            f"**Description:** {sf.description}\n\n"
            "Break the technical plan for this subfeature into parallelizable "
            "implementation tasks. Each task needs file scope, acceptance criteria, "
            "counterexamples, requirement traceability, and a subfeature_id.\n\n"
            f"Set `subfeature_id` to '{sf.id}' on every task you produce.\n\n"
            f"## Upstream Artifacts for {sf.slug}\n\n{upstream_context}\n\n"
            f"## Context from prior work\n\n{context}"
        )
    return _make_prompt


# ── Phase ────────────────────────────────────────────────────────────────────


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
                lead_actor=_sf_task_planner_gate_reviewer,
                decomposition=decomposition,
                artifact_prefix="dag",
                compiled_key="dag",
                base_role=planning_lead_role,
                output_type=ImplementationDAG,
                compiler_actor=dag_compiler,
                broad_key="dag:strategy",
                context_keys=["project", "scope", "decomposition"],
            )
            await runner.artifacts.put("dag", final_text, feature=feature)
            state.dag = final_text
            return state

        decomposition = await self._load_decomposition(runner, feature, state)

        # ── Step 2: Global Implementation Strategy ──
        # The strategy planner gets project + scope + decomposition via
        # context_keys, plus the full compiled plan injected in the prompt.
        # PRD summaries are included instead of the full 415K PRD.
        plan_text = await runner.artifacts.get("plan", feature=feature) or ""
        prd_summaries = await self._load_prd_summaries(runner, feature, decomposition)

        strategy, strategy_text = await broad_interview(
            runner, feature, self.name,
            lead_actor=_strategy_planner,
            output_type=GlobalImplementationStrategy,
            artifact_key="dag:strategy",
            artifact_label="Global Implementation Strategy",
            initial_prompt=(
                f"I'm going to establish the global implementation strategy for: {feature.name}\n\n"
                "We need to determine the subfeature execution order, shared infrastructure "
                "tasks, cross-subfeature dependencies, and parallel opportunities.\n\n"
                "Let me ask about constraints, team size, and dependencies.\n\n"
                f"## Full Technical Plan\n\n{plan_text}\n\n"
                f"## PRD Summaries\n\n{prd_summaries}"
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

        # Preload per-SF upstream artifacts for prompt injection
        sf_upstream = await self._load_sf_upstream(runner, feature, ordered_decomp)

        await per_subfeature_loop(
            runner, feature, self.name,
            decomposition=ordered_decomp,
            base_role=planning_lead_role,
            output_type=ImplementationDAG,
            artifact_prefix="dag",
            broad_key="dag:strategy",
            make_prompt=_make_sf_prompt_with_upstream(sf_upstream),
            context_keys=["project", "scope"],
        )

        # ── Step 4: DAG Integration Review ──
        review = await integration_review(
            runner, feature, self.name,
            lead_actor=_sf_task_planner_reviewer,
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
                    context_keys=["project", "scope"],
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
            lead_actor=_sf_task_planner_gate_reviewer,
            decomposition=ordered_decomp,
            artifact_prefix="dag",
            compiled_key="dag",
            base_role=planning_lead_role,
            output_type=ImplementationDAG,
            compiler_actor=dag_compiler,
            broad_key="dag:strategy",
            context_keys=["project", "scope", "decomposition"],
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

    @staticmethod
    async def _load_sf_upstream(
        runner: WorkflowRunner, feature: Feature, decomposition: SubfeatureDecomposition,
    ) -> dict[str, dict[str, str]]:
        """Preload per-SF upstream artifacts (prd, design, plan, system-design)."""
        result: dict[str, dict[str, str]] = {}
        for sf in decomposition.subfeatures:
            sf_artifacts: dict[str, str] = {}
            for prefix in ("prd", "design", "plan", "system-design"):
                text = await runner.artifacts.get(f"{prefix}:{sf.slug}", feature=feature)
                if text:
                    sf_artifacts[prefix] = text
            result[sf.slug] = sf_artifacts
        return result

    @staticmethod
    async def _load_prd_summaries(
        runner: WorkflowRunner, feature: Feature, decomposition: SubfeatureDecomposition,
    ) -> str:
        """Load PRD summaries for all subfeatures."""
        parts: list[str] = []
        for sf in decomposition.subfeatures:
            summary = await runner.artifacts.get(f"prd-summary:{sf.slug}", feature=feature)
            if summary:
                parts.append(f"### {sf.name} ({sf.slug})\n\n{summary}")
        return "\n\n".join(parts)
