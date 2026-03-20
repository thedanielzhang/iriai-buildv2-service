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
                            "Review the technical plan for completeness and correctness. "
                            "Verify all PRD requirements are addressed and implementation "
                            "steps are actionable."
                        ),
                        output_type=Verdict,
                    ),
                    Ask(
                        actor=plan_security_reviewer,
                        prompt=(
                            "Review the technical plan for security concerns. Check for "
                            "potential vulnerabilities, insecure patterns, and missing "
                            "security considerations."
                        ),
                        output_type=Verdict,
                    ),
                    Ask(
                        actor=citation_reviewer,
                        prompt=(
                            "Verify all citations in the compiled artifacts are valid. "
                            "Check code references exist (use Read/Glob), verify decision "
                            "IDs match the decision log, and validate research references."
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

            # Route feedback to architect for revision
            feedback_parts = []
            if not completeness_verdict.approved:
                feedback_parts.append(f"Completeness issues:\n{to_str(completeness_verdict)}")
            if not security_verdict.approved:
                feedback_parts.append(f"Security issues:\n{to_str(security_verdict)}")
            if not citation_verdict.approved:
                feedback_parts.append(f"Citation issues:\n{to_str(citation_verdict)}")
            feedback = "\n\n".join(feedback_parts)

            revised_plan: TechnicalPlan = await runner.run(
                Ask(
                    actor=architect,
                    prompt=f"Fix these review issues in the technical plan:\n\n{feedback}",
                    output_type=TechnicalPlan,
                ),
                feature,
                phase_name=self.name,
            )
            plan_text = to_str(revised_plan)
            await runner.artifacts.put("plan", plan_text, feature=feature)
            state.plan = plan_text

            hosting = runner.services.get("hosting")
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
