from __future__ import annotations

import logging

from iriai_compose import Feature, Phase, WorkflowRunner

from ....models.outputs import (
    ArchitectureOutput,
    Envelope,
    SystemDesign,
    TechnicalPlan,
    envelope_done,
)
from ....models.state import BuildState
from ....roles import architect, user
from ....services.system_design_html import render_system_design_html
from ..._common import HostedInterview, gate_and_revise, get_existing_artifact

logger = logging.getLogger(__name__)


class ArchitecturePhase(Phase):
    name = "architecture"

    async def execute(
        self, runner: WorkflowRunner, feature: Feature, state: BuildState
    ) -> BuildState:
        # Check if plan artifact already exists (DB or filesystem — resuming after restart)
        existing_plan_text = await get_existing_artifact(runner, feature, "plan")
        existing_sd_text = await get_existing_artifact(runner, feature, "system-design")

        if existing_plan_text:
            logger.info("Architecture artifacts exist — skipping interview, resuming at gate")
            try:
                import json as _json
                data = _json.loads(existing_plan_text)
                plan = TechnicalPlan.model_validate(data)
            except Exception:
                plan = existing_plan_text

            try:
                import json as _json
                data = _json.loads(existing_sd_text or "{}")
                system_design = SystemDesign.model_validate(data)
            except Exception:
                system_design = SystemDesign()

            # Re-host existing artifacts
            hosting = runner.services.get("hosting")
            sd_url: str | None = None
            if hosting:
                await hosting.push(
                    feature.id, "plan", existing_plan_text,
                    f"Technical Plan — {feature.name}",
                )
                if existing_sd_text:
                    sd_url = await hosting.push(
                        feature.id, "system-design", existing_sd_text,
                        f"System Design — {feature.name}",
                    )
        else:
            # 1. Interview produces both plan and system design
            envelope: Envelope[ArchitectureOutput] = await runner.run(
                HostedInterview(
                    questioner=architect,
                    responder=user,
                    initial_prompt=(
                        "I'll explore the codebase and ask questions to build a technical plan. "
                        "Let me start by understanding the project structure. "
                        "What area of the codebase should I focus on?"
                    ),
                    output_type=Envelope[ArchitectureOutput],
                    done=envelope_done,
                    artifact_key="plan",
                    artifact_label="Technical Plan",
                ),
                feature,
                phase_name=self.name,
            )

            arch_output = envelope.output
            plan = arch_output.plan
            system_design = arch_output.system_design

            # 2. Render and host system design HTML
            hosting = runner.services.get("hosting")
            sd_url: str | None = None
            if hosting:
                html = render_system_design_html(system_design)
                sd_url = await hosting.push(
                    feature.id,
                    "system-design",
                    html,
                    f"System Design — {feature.name}",
                )
                print(f"\n📐 System Design hosted at: {sd_url}\n", flush=True)

        # 3. Gate the text plan
        plan, plan_text = await gate_and_revise(
            runner,
            feature,
            self.name,
            artifact=plan,
            actor=architect,
            output_type=TechnicalPlan,
            approver=user,
            label="Technical plan",
            artifact_key="plan",
        )

        # 4. Gate the system design
        sd_label = (
            f"System Design\nReview in browser: {sd_url}"
            if sd_url
            else "System Design"
        )
        system_design, sd_text = await gate_and_revise(
            runner,
            feature,
            self.name,
            artifact=system_design,
            actor=architect,
            output_type=SystemDesign,
            approver=user,
            label=sd_label,
            artifact_key="system-design",
        )

        # 5. Store artifacts
        await runner.artifacts.put("plan", plan_text, feature=feature)
        await runner.artifacts.put("system-design", sd_text, feature=feature)
        state.plan = plan_text
        state.system_design = sd_text
        return state
