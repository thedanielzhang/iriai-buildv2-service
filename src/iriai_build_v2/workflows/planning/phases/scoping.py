from __future__ import annotations

from iriai_compose import Feature, Phase, WorkflowRunner

from ....models.outputs import ScopeOutput, Envelope, envelope_done
from ....models.state import BuildState
from ....roles import scoper, user
from ..._common import HostedInterview, gate_and_revise


class ScopingPhase(Phase):
    name = "scoping"

    async def execute(
        self, runner: WorkflowRunner, feature: Feature, state: BuildState
    ) -> BuildState:
        # Skip if scope was pre-loaded (e.g., via --repo flags)
        if state.scope:
            return state

        envelope: Envelope[ScopeOutput] = await runner.run(
            HostedInterview(
                questioner=scoper,
                responder=user,
                initial_prompt=(
                    f"I'm going to scope this feature: {feature.name}\n\n"
                    "I'll ask a few focused questions about which repos are involved, "
                    "what type of change this is, and what's out of scope."
                ),
                output_type=Envelope[ScopeOutput],
                done=envelope_done,
                artifact_key="scope",
                artifact_label="Feature Scope",
            ),
            feature,
            phase_name=self.name,
        )

        scope = envelope.output

        scope, scope_text = await gate_and_revise(
            runner, feature, self.name,
            artifact=scope, actor=scoper, output_type=ScopeOutput,
            approver=user, label="Feature Scope",
            artifact_key="scope",
        )

        await runner.artifacts.put("scope", scope_text, feature=feature)
        state.scope = scope_text

        # Create worktrees from identified repos
        workspace_mgr = runner.services.get("workspace_manager")
        if workspace_mgr and isinstance(scope, ScopeOutput):
            project_ctx = await workspace_mgr.setup_feature_workspace(feature, scope)
            await runner.artifacts.put(
                "project",
                project_ctx.model_dump_json(indent=2),
                feature=feature,
            )

        return state
