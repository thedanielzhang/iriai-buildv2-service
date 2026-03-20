from __future__ import annotations

import logging

from iriai_compose import Feature, Phase, WorkflowRunner

from ....models.outputs import PRD, Envelope, envelope_done
from ....models.state import BuildState
from ....roles import pm, user
from ..._common import HostedInterview, gate_and_revise, get_existing_artifact

logger = logging.getLogger(__name__)


class PMPhase(Phase):
    name = "pm"

    async def execute(
        self, runner: WorkflowRunner, feature: Feature, state: BuildState
    ) -> BuildState:
        # Check if PRD already exists (DB or filesystem — resuming after restart)
        existing_prd_text = await get_existing_artifact(runner, feature, "prd")

        if existing_prd_text:
            logger.info("PRD artifact exists — skipping interview, resuming at gate")
            try:
                import json as _json
                data = _json.loads(existing_prd_text)
                prd = PRD.model_validate(data)
            except Exception:
                prd = existing_prd_text

            hosting = runner.services.get("hosting")
            if hosting:
                await hosting.push(
                    feature.id, "prd", existing_prd_text,
                    f"PRD — {feature.name}",
                )
        else:
            envelope: Envelope[PRD] = await runner.run(
                HostedInterview(
                    questioner=pm,
                    responder=user,
                    initial_prompt=(
                        f"I'm going to help you define requirements for: {feature.name}\n\n"
                        "Let me ask some clarifying questions to build a comprehensive PRD. "
                        "What is the main goal of this feature?"
                    ),
                    output_type=Envelope[PRD],
                    done=envelope_done,
                    artifact_key="prd",
                    artifact_label="PRD",
                ),
                feature,
                phase_name=self.name,
            )

            prd = envelope.output

        prd, prd_text = await gate_and_revise(
            runner, feature, self.name,
            artifact=prd, actor=pm, output_type=PRD,
            approver=user, label="PRD",
            artifact_key="prd",
        )

        await runner.artifacts.put("prd", prd_text, feature=feature)
        state.prd = prd_text
        return state
