from __future__ import annotations

import json as _json
import logging

from iriai_compose import AgentActor, Feature, Phase, WorkflowRunner, to_str

from ....models.outputs import (
    ArchitectureOutput,
    Envelope,
    RevisionPlan,
    RevisionRequest,
    SubfeatureDecomposition,
    SystemDesign,
    TechnicalPlan,
    envelope_done,
)
from ....models.state import BuildState
from ....roles import (
    InterviewActor,
    architect_role,
    compiler_role,
    lead_architect,
    lead_architect_gate_reviewer,
    lead_architect_reviewer,
    plan_arch_compiler,
    sysdesign_compiler,
    user,
)
from ....services.system_design_html import render_system_design_html
from ..._common import (
    HostedInterview,
    broad_interview,
    compile_artifacts,
    gate_and_revise,
    get_existing_artifact,
    integration_review,
)
from ..._common._helpers import generate_summary, targeted_revision

logger = logging.getLogger(__name__)


def _make_sf_prompt(sf, context: str) -> str:
    """Build the initial interview prompt for a per-subfeature architect agent."""
    return (
        f"You are the architect for the **{sf.name}** subfeature (ID: {sf.id}, slug: {sf.slug}).\n\n"
        f"**Description:** {sf.description}\n\n"
        "The broad architecture has been established (see context below). "
        "Define implementation steps with file scope, API contracts, data models, "
        "and system design for this subfeature.\n\n"
        "You have access to the codebase — ground every decision in existing code. "
        "Document all interfaces and edges to other subfeatures explicitly.\n\n"
        f"## Context from prior work\n\n{context}"
    )


class ArchitecturePhase(Phase):
    name = "architecture"

    async def execute(
        self, runner: WorkflowRunner, feature: Feature, state: BuildState
    ) -> BuildState:
        # ── Step 1: Resume check ──
        existing_plan = await get_existing_artifact(runner, feature, "plan")
        if existing_plan:
            logger.info("Compiled plan exists — skipping to end")
            state.plan = existing_plan
            existing_sd = await get_existing_artifact(runner, feature, "system-design") or ""
            state.system_design = existing_sd
            return state

        decomposition = await self._load_decomposition(runner, feature, state)

        # ── Step 2: Broad Architecture Interview ──
        # Anti-pattern: do NOT create BroadArchitecture — reuse TechnicalPlan
        _, broad_text = await broad_interview(
            runner, feature, self.name,
            lead_actor=lead_architect,
            output_type=TechnicalPlan,
            artifact_key="plan:broad",
            artifact_label="Broad Architecture",
            initial_prompt=(
                f"I'm going to establish the system architecture for: {feature.name}\n\n"
                "We'll define the system topology, tech stack, deployment model, "
                "security architecture, and API conventions that ALL subfeature architects "
                "will build on.\n\n"
                "Let me start by exploring the codebase to understand existing patterns."
            ),
        )

        # ── Step 3: Per-Subfeature Architecture Loop (sequential) ──
        # Architecture produces ArchitectureOutput (plan + system_design), not just TechnicalPlan.
        # We can't use per_subfeature_loop directly since it expects a single output_type.
        # Instead we do the loop manually.
        await self._per_subfeature_arch_loop(runner, feature, decomposition)

        # ── Step 4: Integration Review ──
        review = await integration_review(
            runner, feature, self.name,
            lead_actor=lead_architect_reviewer,
            decomposition=decomposition,
            artifact_prefix="plan",
            broad_key="plan:broad",
        )

        if review.verdict == "needs_revision" and review.revision_instructions:
            logger.info("Architecture integration review needs revision")
            plan = RevisionPlan(requests=[
                RevisionRequest(
                    description=instruction,
                    reasoning="Architecture integration review finding",
                    affected_subfeatures=[sf_slug],
                )
                for sf_slug, instruction in review.revision_instructions.items()
            ])
            await targeted_revision(
                runner, feature, self.name,
                revision_plan=plan,
                decomposition=decomposition,
                base_role=architect_role,
                output_type=TechnicalPlan,
                artifact_prefix="plan",
            )

        # ── Step 5: Dual Compilation ──
        # 5a: Technical Plan compilation
        _, plan_text = await compile_artifacts(
            runner, feature, self.name,
            compiler_actor=plan_arch_compiler,
            decomposition=decomposition,
            artifact_prefix="plan",
            broad_key="plan:broad",
            output_type=TechnicalPlan,
            final_key="plan",
        )

        # 5b: System Design compilation
        await self._compile_system_design(runner, feature, decomposition)

        # ── Step 6: Dual Interview-Based Gate Review ──
        # 6a: Technical Plan gate review
        plan_text = await self._plan_gate_review(runner, feature, decomposition)

        # 6b: System Design gate review
        sd_text = await self._system_design_gate_review(runner, feature, decomposition)

        state.plan = plan_text
        state.system_design = sd_text
        return state

    async def _per_subfeature_arch_loop(
        self, runner: WorkflowRunner, feature: Feature, decomposition: SubfeatureDecomposition
    ) -> None:
        """Sequential loop for per-subfeature architecture interviews.

        Each produces ArchitectureOutput (TechnicalPlan + SystemDesign).
        Stores plan:{slug} and system-design:{slug} separately.
        """
        from ..._common._helpers import _build_subfeature_context, _get_user

        approver = _get_user()
        completed_plans: dict[str, str] = {}
        completed_summaries: dict[str, str] = {}

        broad_text = await runner.artifacts.get("plan:broad", feature=feature) or ""
        decomp_text = await runner.artifacts.get("decomposition", feature=feature) or ""

        for sf in decomposition.subfeatures:
            plan_key = f"plan:{sf.slug}"
            sd_key = f"system-design:{sf.slug}"

            # Resume check
            existing = await get_existing_artifact(runner, feature, plan_key)
            if existing:
                logger.info("Subfeature arch %s exists — skipping", plan_key)
                completed_plans[sf.slug] = existing
                summary = await runner.artifacts.get(f"plan-summary:{sf.slug}", feature=feature)
                if summary:
                    completed_summaries[sf.slug] = summary
                continue

            # Build tiered context
            context = _build_subfeature_context(
                decomposition, sf.slug,
                completed_plans, completed_summaries,
                broad_text, decomp_text,
            )
            prompt = _make_sf_prompt(sf, context)

            sf_actor = InterviewActor(
                name=f"architect-sf-{sf.slug}",
                role=architect_role,
                context_keys=["project", "scope", "prd", "design"],
            )

            envelope = await runner.run(
                HostedInterview(
                    questioner=sf_actor,
                    responder=approver,
                    initial_prompt=prompt,
                    output_type=Envelope[ArchitectureOutput],
                    done=envelope_done,
                    artifact_key=plan_key,
                    artifact_label=f"Architecture — {sf.name}",
                ),
                feature,
                phase_name=self.name,
            )

            arch_output = envelope.output
            plan_text = to_str(arch_output.plan)
            sd_text = to_str(arch_output.system_design)

            # Host system design HTML per subfeature
            hosting = runner.services.get("hosting")
            if hosting and arch_output.system_design:
                html = render_system_design_html(arch_output.system_design)
                await hosting.push(feature.id, sd_key, html, f"System Design — {sf.name}")

            # Gate the plan (with system design link)
            plan_obj, plan_text = await gate_and_revise(
                runner, feature, self.name,
                artifact=arch_output.plan, actor=sf_actor, output_type=TechnicalPlan,
                approver=approver, label=f"Technical Plan — {sf.name}",
                artifact_key=plan_key,
                annotation_keys=[plan_key, sd_key],
            )
            plan_text = to_str(plan_obj) if not isinstance(plan_text, str) else plan_text

            # Gate the system design
            sd_obj, sd_text = await gate_and_revise(
                runner, feature, self.name,
                artifact=arch_output.system_design, actor=sf_actor, output_type=SystemDesign,
                approver=approver, label=f"System Design — {sf.name}",
                artifact_key=sd_key,
            )
            sd_text = to_str(sd_obj) if not isinstance(sd_text, str) else sd_text

            await runner.artifacts.put(plan_key, plan_text, feature=feature)
            await runner.artifacts.put(sd_key, sd_text, feature=feature)
            completed_plans[sf.slug] = plan_text

            # Generate summary
            summary = await generate_summary(runner, feature, "plan", sf.slug)
            if summary:
                completed_summaries[sf.slug] = summary

    async def _compile_system_design(
        self, runner: WorkflowRunner, feature: Feature, decomposition: SubfeatureDecomposition
    ) -> tuple[SystemDesign, str]:
        """Compile per-subfeature system designs into a single SystemDesign."""
        from iriai_compose import Ask

        parts = []
        decomp_text = await runner.artifacts.get("decomposition", feature=feature)
        if decomp_text:
            parts.append(f"## Decomposition\n\n{decomp_text}")
        for sf in decomposition.subfeatures:
            sd_text = await runner.artifacts.get(f"system-design:{sf.slug}", feature=feature)
            if sd_text:
                parts.append(f"## System Design: {sf.name} ({sf.slug})\n\n{sd_text}")

        source_text = "\n\n---\n\n".join(parts)

        compiled = await runner.run(
            Ask(
                actor=sysdesign_compiler,
                prompt=(
                    f"Compile {len(decomposition.subfeatures)} subfeature system designs "
                    "into a single unified system design.\n\n"
                    "Rules:\n"
                    "- Union all services, deduplicate by id\n"
                    "- Union all connections, deduplicate by (from_id, to_id)\n"
                    "- Union all API endpoints, group by service\n"
                    "- Union all entities, deduplicate by (name, service_id)\n"
                    "- Union all call paths, decisions, risks\n\n"
                    f"{source_text}"
                ),
                output_type=SystemDesign,
            ),
            feature,
            phase_name=self.name,
        )

        sd_text = to_str(compiled)
        await runner.artifacts.put("system-design", sd_text, feature=feature)

        # Host the compiled system design HTML
        hosting = runner.services.get("hosting")
        if hosting:
            html = render_system_design_html(compiled)
            await hosting.push(feature.id, "system-design", html, f"System Design — {feature.name}")

        return compiled, sd_text

    async def _plan_gate_review(
        self, runner: WorkflowRunner, feature: Feature, decomposition: SubfeatureDecomposition
    ) -> str:
        """Interview-based gate review for the compiled technical plan."""
        from ..._common import interview_gate_review
        return await interview_gate_review(
            runner, feature, self.name,
            lead_actor=lead_architect_gate_reviewer,
            decomposition=decomposition,
            artifact_prefix="plan",
            compiled_key="plan",
            base_role=architect_role,
            output_type=TechnicalPlan,
            compiler_actor=plan_arch_compiler,
            broad_key="plan:broad",
        )

    async def _system_design_gate_review(
        self, runner: WorkflowRunner, feature: Feature, decomposition: SubfeatureDecomposition
    ) -> str:
        """Interview-based gate review for the compiled system design."""
        from ..._common import interview_gate_review
        return await interview_gate_review(
            runner, feature, self.name,
            lead_actor=lead_architect_gate_reviewer,
            decomposition=decomposition,
            artifact_prefix="system-design",
            compiled_key="system-design",
            base_role=architect_role,
            output_type=SystemDesign,
            compiler_actor=sysdesign_compiler,
            broad_key="plan:broad",
        )

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
