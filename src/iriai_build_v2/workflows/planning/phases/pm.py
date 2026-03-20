from __future__ import annotations

import logging

from iriai_compose import Feature, Phase, WorkflowRunner, to_str

from ....models.outputs import PRD
from ....models.state import BuildState
from ....roles import (
    lead_pm,
    lead_pm_decomposer,
    lead_pm_gate_reviewer,
    lead_pm_reviewer,
    pm_compiler,
    pm_role,
    user,
)
from ..._common import (
    broad_interview,
    compile_artifacts,
    decompose_and_gate,
    get_existing_artifact,
    integration_review,
    interview_gate_review,
    per_subfeature_loop,
)

logger = logging.getLogger(__name__)


def _make_sf_prompt(sf, context: str) -> str:
    """Build the initial interview prompt for a per-subfeature PM agent."""
    edges_desc = ""
    if hasattr(sf, "requirement_ids") and sf.requirement_ids:
        edges_desc += f"\nBroad requirement IDs mapped to this subfeature: {', '.join(sf.requirement_ids)}"

    return (
        f"You are the PM for the **{sf.name}** subfeature (ID: {sf.id}, slug: {sf.slug}).\n\n"
        f"**Description:** {sf.description}\n"
        f"{sf.rationale and f'**Rationale:** {sf.rationale}' or ''}\n"
        f"{edges_desc}\n\n"
        "Your job is to interview the user and produce a detailed PRD scoped to this subfeature. "
        "Ask as many questions as needed for maximum depth. Document all interfaces "
        "and edges to other subfeatures explicitly.\n\n"
        f"## Context from prior work\n\n{context}"
    )


class PMPhase(Phase):
    name = "pm"

    async def execute(
        self, runner: WorkflowRunner, feature: Feature, state: BuildState
    ) -> BuildState:
        # ── Step 1: Resume check ──
        # If compiled PRD already exists, skip to gate review
        existing_prd = await get_existing_artifact(runner, feature, "prd")
        if existing_prd:
            logger.info("Compiled PRD exists — skipping to gate review")
            state.prd = existing_prd
            return state

        # ── Step 2: Broad Requirements Interview ──
        _, broad_text = await broad_interview(
            runner, feature, self.name,
            lead_actor=lead_pm,
            output_type=PRD,
            artifact_key="prd:broad",
            artifact_label="Broad PRD",
            initial_prompt=(
                f"I'm going to help you define high-level requirements for: {feature.name}\n\n"
                "We'll start with a broad overview, then decompose into subfeatures "
                "for detailed interviews. What is the main goal of this feature?"
            ),
        )

        # ── Step 3: Subfeature Decomposition ──
        decomposition = await decompose_and_gate(
            runner, feature, self.name,
            lead_actor=lead_pm_decomposer,
            approver=user,
            broad_artifact_key="prd:broad",
        )
        state.decomposition = to_str(decomposition)

        # ── Step 4: Per-Subfeature PRD Loop (sequential) ──
        await per_subfeature_loop(
            runner, feature, self.name,
            decomposition=decomposition,
            base_role=pm_role,
            output_type=PRD,
            artifact_prefix="prd",
            broad_key="prd:broad",
            make_prompt=_make_sf_prompt,
        )

        # ── Step 5: Integration Review ──
        review = await integration_review(
            runner, feature, self.name,
            lead_actor=lead_pm_reviewer,
            decomposition=decomposition,
            artifact_prefix="prd",
            broad_key="prd:broad",
        )

        if review.verdict == "needs_revision" and review.revision_instructions:
            logger.info("Integration review needs revision — re-running affected subfeatures")
            # Re-run affected subfeatures with revision instructions
            from ..._common._helpers import targeted_revision
            from ....models.outputs import RevisionPlan, RevisionRequest
            plan = RevisionPlan(requests=[
                RevisionRequest(
                    description=instruction,
                    reasoning="Integration review finding",
                    affected_subfeatures=[sf_slug],
                )
                for sf_slug, instruction in review.revision_instructions.items()
            ])
            await targeted_revision(
                runner, feature, self.name,
                revision_plan=plan,
                decomposition=decomposition,
                base_role=pm_role,
                output_type=PRD,
                artifact_prefix="prd",
            )

        # ── Step 6: Compilation ──
        compiled_prd, compiled_text = await compile_artifacts(
            runner, feature, self.name,
            compiler_actor=pm_compiler,
            decomposition=decomposition,
            artifact_prefix="prd",
            broad_key="prd:broad",
            output_type=PRD,
            final_key="prd",
        )

        # ── Step 7: Interview-Based Gate Review ──
        final_text = await interview_gate_review(
            runner, feature, self.name,
            lead_actor=lead_pm_gate_reviewer,
            decomposition=decomposition,
            artifact_prefix="prd",
            compiled_key="prd",
            base_role=pm_role,
            output_type=PRD,
            compiler_actor=pm_compiler,
            broad_key="prd:broad",
        )

        state.prd = final_text
        return state
