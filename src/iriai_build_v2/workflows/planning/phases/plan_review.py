from __future__ import annotations

import json as _json
import logging

from iriai_compose import Ask, Feature, Phase, Respond, WorkflowRunner, to_str

from ....models.outputs import (
    PRD,
    DesignDecisions,
    SubfeatureDecomposition,
    SystemDesign,
    TechnicalPlan,
    Verdict,
)
from ....models.state import BuildState
from ....roles import (
    architect,
    architect_role,
    citation_reviewer,
    design_compiler,
    designer_role,
    lead_architect_gate_reviewer,
    lead_designer_gate_reviewer,
    lead_pm_gate_reviewer,
    plan_arch_compiler,
    plan_completeness_reviewer,
    plan_security_reviewer,
    pm_compiler,
    pm_role,
    sysdesign_compiler,
    user,
)
from ..._common import interview_gate_review

logger = logging.getLogger(__name__)

WARN_AFTER_CYCLES = 3

_PRD_KEYWORDS = [
    "requirement", "prd", "journey", "acceptance criteria", "user story",
    "REQ-", "J-", "AC-", "precondition", "user flow",
]
_DESIGN_KEYWORDS = [
    "design", "component", "mockup", "UX", "UI", "CMP-", "visual",
    "layout", "responsive", "accessibility", "interaction pattern",
]


def _classify_concerns(*verdicts: Verdict) -> dict[str, list[str]]:
    """Classify verdict concerns by which artifact they belong to.

    Returns ``{"prd": [...], "design": [...], "plan": [...]}``.
    Concerns matching PRD keywords go to the PM; design keywords to the
    designer; everything else to the architect.
    """
    classified: dict[str, list[str]] = {"prd": [], "design": [], "plan": []}

    for verdict in verdicts:
        if verdict.approved:
            continue
        for concern in verdict.concerns:
            text = f"{concern.description} {concern.file}".lower()
            if any(kw.lower() in text for kw in _PRD_KEYWORDS):
                classified["prd"].append(concern.description)
            elif any(kw.lower() in text for kw in _DESIGN_KEYWORDS):
                classified["design"].append(concern.description)
            else:
                classified["plan"].append(concern.description)
        for gap in verdict.gaps:
            text = f"{gap.description} {gap.category}".lower()
            if any(kw.lower() in text for kw in _PRD_KEYWORDS):
                classified["prd"].append(gap.description)
            elif any(kw.lower() in text for kw in _DESIGN_KEYWORDS):
                classified["design"].append(gap.description)
            else:
                classified["plan"].append(gap.description)

    return classified


class PlanReviewPhase(Phase):
    name = "plan-review"

    async def execute(
        self, runner: WorkflowRunner, feature: Feature, state: BuildState
    ) -> BuildState:
        decomposition = await self._load_decomposition(state, runner, feature)

        # ── Step 1: Auto-fix loop — parallel reviews until all approve ──
        cycle = 0
        while True:
            results = await runner.parallel(
                [
                    Ask(
                        actor=plan_completeness_reviewer,
                        prompt=(
                            "Your goal is to find every gap and inconsistency across all artifacts. "
                            "The PRD, design, plan, and system design were produced by different agents "
                            "— they WILL have drift and contradictions.\n\n"
                            "Focus on:\n"
                            "1. PRD requirements with no corresponding plan step (uncovered requirements)\n"
                            "2. Plan steps that implement something not in the PRD (scope creep)\n"
                            "3. PRD journeys with no verification blocks in the plan\n"
                            "4. Design components with no implementation task\n"
                            "5. PRD ↔ Design contradictions (requirement vs component mismatch)\n"
                            "6. PRD ↔ Plan contradictions (requirement vs implementation mismatch)\n"
                            "7. Design ↔ Plan contradictions (component vs task mismatch)\n"
                            "8. Missing cross-service tasks (shared package changes without consumer updates)\n"
                            "9. Acceptance criteria that are unverifiable given the plan's file scope\n\n"
                            "Every gap gets its own concern entry. A clean PASS means you missed something."
                        ),
                        output_type=Verdict,
                    ),
                    Ask(
                        actor=plan_security_reviewer,
                        prompt=(
                            "Your goal is to find every security gap across all artifacts. "
                            "Check the PRD security profile, then verify the plan actually implements "
                            "every security requirement — not just acknowledges it.\n\n"
                            "Focus on:\n"
                            "1. PRD security profile requirements with no implementation task\n"
                            "2. Endpoints without auth decorators in the plan\n"
                            "3. Data flows handling PII without encryption/masking tasks\n"
                            "4. Missing input validation on user-facing endpoints\n"
                            "5. Missing rate limiting on public endpoints\n"
                            "6. Secrets/credentials hardcoded in task instructions\n"
                            "7. CORS/CSRF gaps in the API design\n"
                            "8. Database migrations without rollback steps\n"
                            "9. Third-party integrations without error handling tasks\n\n"
                            "Every gap gets its own concern entry. A clean PASS means you missed something."
                        ),
                        output_type=Verdict,
                    ),
                    Ask(
                        actor=citation_reviewer,
                        prompt=(
                            "Your goal is to find every broken or missing citation across all artifacts. "
                            "Every decision and claim must be traceable.\n\n"
                            "Focus on:\n"
                            "1. Decision IDs (D-*) referenced in citations that don't exist in the decision log\n"
                            "2. Scope decision references (scope-*) that don't match scope.user_decisions\n"
                            "3. Code references ([Source: path:line]) where the file/function doesn't exist\n"
                            "4. Requirements referenced in plan steps that don't exist in the PRD\n"
                            "5. Journey IDs referenced in verification blocks that don't exist in the PRD\n"
                            "6. Component IDs referenced in tasks that don't exist in the design\n"
                            "7. Claims about library/API behavior without documentation citation\n\n"
                            "Every broken reference gets its own concern entry. A clean PASS means you missed something."
                        ),
                        output_type=Verdict,
                    ),
                ],
                feature,
            )

            completeness_verdict, security_verdict, citation_verdict = results

            if completeness_verdict.approved and security_verdict.approved and citation_verdict.approved:
                break

            review_summary = (
                f"## Completeness Review\n"
                f"{to_str(completeness_verdict)}\n\n"
                f"## Security Review\n"
                f"{to_str(security_verdict)}\n\n"
                f"## Citation Review\n"
                f"{to_str(citation_verdict)}"
            )

            # Escalate to user after WARN_AFTER_CYCLES
            if cycle >= WARN_AFTER_CYCLES:
                logger.warning(
                    "Plan review cycle %d (exceeded %d without approval)",
                    cycle + 1,
                    WARN_AFTER_CYCLES,
                )
                user_input = await runner.run(
                    Respond(
                        responder=user,
                        prompt=(
                            f"Auto-review has run {cycle + 1} cycles without full approval.\n\n"
                            f"{review_summary}\n\n"
                            "Continue auto-fixing or provide guidance for the architect?"
                        ),
                    ),
                    feature,
                    phase_name=self.name,
                )
                review_summary += f"\n\n## User Guidance\n{user_input}"

            # Route feedback to the correct agent based on artifact type
            all_concerns = _classify_concerns(
                completeness_verdict, security_verdict, citation_verdict,
            )
            hosting = runner.services.get("hosting")

            if all_concerns["prd"]:
                prd_feedback = "\n".join(f"- {c}" for c in all_concerns["prd"])
                revised_prd: PRD = await runner.run(
                    Ask(
                        actor=lead_pm_gate_reviewer,
                        prompt=f"Fix these PRD issues:\n\n{prd_feedback}",
                        output_type=PRD,
                    ),
                    feature,
                    phase_name=self.name,
                )
                prd_text = to_str(revised_prd)
                await runner.artifacts.put("prd", prd_text, feature=feature)
                state.prd = prd_text
                if hosting:
                    await hosting.update(feature.id, "prd", prd_text)

            if all_concerns["design"]:
                design_feedback = "\n".join(f"- {c}" for c in all_concerns["design"])
                revised_design: DesignDecisions = await runner.run(
                    Ask(
                        actor=lead_designer_gate_reviewer,
                        prompt=f"Fix these design issues:\n\n{design_feedback}",
                        output_type=DesignDecisions,
                    ),
                    feature,
                    phase_name=self.name,
                )
                design_text = to_str(revised_design)
                await runner.artifacts.put("design", design_text, feature=feature)
                state.design = design_text
                if hosting:
                    await hosting.update(feature.id, "design", design_text)

            if all_concerns["plan"]:
                plan_feedback = "\n".join(f"- {c}" for c in all_concerns["plan"])
                revised_plan: TechnicalPlan = await runner.run(
                    Ask(
                        actor=architect,
                        prompt=f"Fix these plan issues:\n\n{plan_feedback}",
                        output_type=TechnicalPlan,
                    ),
                    feature,
                    phase_name=self.name,
                )
                plan_text = to_str(revised_plan)
                await runner.artifacts.put("plan", plan_text, feature=feature)
                state.plan = plan_text
                if hosting:
                    await hosting.update(feature.id, "plan", plan_text)

            cycle += 1

        # ── Step 2: Interview-based gate reviews on all artifacts ──

        # PRD
        prd_text = await interview_gate_review(
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
        state.prd = prd_text

        # Design
        design_text = await interview_gate_review(
            runner, feature, self.name,
            lead_actor=lead_designer_gate_reviewer,
            decomposition=decomposition,
            artifact_prefix="design",
            compiled_key="design",
            base_role=designer_role,
            output_type=DesignDecisions,
            compiler_actor=design_compiler,
            broad_key="design:broad",
            context_keys=["project", "scope", "prd"],
        )
        state.design = design_text

        # Technical Plan
        plan_text = await interview_gate_review(
            runner, feature, self.name,
            lead_actor=lead_architect_gate_reviewer,
            decomposition=decomposition,
            artifact_prefix="plan",
            compiled_key="plan",
            base_role=architect_role,
            output_type=TechnicalPlan,
            compiler_actor=plan_arch_compiler,
            broad_key="plan:broad",
            context_keys=["project", "scope", "prd", "design"],
        )
        state.plan = plan_text

        # System Design
        if state.system_design:
            sd_text = await interview_gate_review(
                runner, feature, self.name,
                lead_actor=lead_architect_gate_reviewer,
                decomposition=decomposition,
                artifact_prefix="system-design",
                compiled_key="system-design",
                base_role=architect_role,
                output_type=SystemDesign,
                compiler_actor=sysdesign_compiler,
                broad_key="plan:broad",
                context_keys=["project", "scope", "prd", "design"],
            )
            state.system_design = sd_text

        return state

    @staticmethod
    async def _load_decomposition(
        state: BuildState, runner: WorkflowRunner, feature: Feature
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
