from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from iriai_compose import AgentActor, Ask
from iriai_compose.actors import Role
from iriai_compose.prompts import Confirm, Select
from iriai_compose.runner import InteractionRuntime

from ..planning_signals import GateRejection


class _ApprovalDecision(BaseModel):
    approved: bool = Field(
        description="True to approve the gate. False to reject/request changes.",
    )
    feedback: str = Field(
        default="",
        description="Concise feedback when approval is denied.",
    )


class _ChoiceDecision(BaseModel):
    choice: str = Field(description="The exact option text to choose.")
    reasoning: str = ""


class _ReplyDecision(BaseModel):
    response: str = Field(
        description="The direct reply to send back to the interviewing agent.",
    )


@dataclass(slots=True)
class AgentDelegateInteractionRuntime(InteractionRuntime):
    """Interaction runtime that delegates user responses to an agent."""

    agent_runtime: Any
    model_hint: str | None = None

    name = "auto"

    async def ask(self, task: Ask, **kwargs: Any) -> str | bool | GateRejection:
        prompt_kind = self._coerce_kind(task, kwargs.get("kind"))
        context = str(kwargs.get("context", "") or "")
        feature_id = str(kwargs.get("feature_id", "") or "")
        phase_name = str(kwargs.get("phase_name", "") or "")

        if prompt_kind == "approve":
            decision = await self._run_delegate_task(
                actor_name="autonomous-approver",
                prompt=self._approval_prompt(task.prompt),
                output_type=_ApprovalDecision,
                context=context,
                feature_id=feature_id,
                phase_name=phase_name,
            )
            if decision.approved:
                return True
            return GateRejection(feedback=decision.feedback.strip())

        if prompt_kind == "choose":
            options = list(getattr(getattr(task, "input", None), "options", None) or kwargs.get("options") or [])
            decision = await self._run_delegate_task(
                actor_name="autonomous-chooser",
                prompt=self._choice_prompt(task.prompt, options),
                output_type=_ChoiceDecision,
                context=context,
                feature_id=feature_id,
                phase_name=phase_name,
            )
            if decision.choice in options:
                return decision.choice
            return options[0] if options else ""

        decision = await self._run_delegate_task(
            actor_name="autonomous-responder",
            prompt=self._reply_prompt(task.prompt),
            output_type=_ReplyDecision,
            context=context,
            feature_id=feature_id,
            phase_name=phase_name,
        )
        return decision.response.strip() or "Proceed with reasonable assumptions."

    def _coerce_kind(self, task: Ask, kind: Any) -> str:
        if kind in {"approve", "choose", "respond"}:
            return str(kind)
        task_input = getattr(task, "input", None)
        if isinstance(task_input, Select):
            return "choose"
        if isinstance(task_input, Confirm):
            return "approve"
        return "respond"

    async def _run_delegate_task(
        self,
        *,
        actor_name: str,
        prompt: str,
        output_type: type[BaseModel],
        context: str,
        feature_id: str,
        phase_name: str,
    ) -> BaseModel:
        role = Role(
            name=actor_name,
            prompt=(
                "You are acting as the delegated human stakeholder for a workflow "
                "that is intentionally running without a person in the loop.\n\n"
                "Rules:\n"
                "- Be decisive and continue the workflow when it is safe to do so.\n"
                "- Ground your answer in the provided context and prompt.\n"
                "- Do not ask for more human input.\n"
                "- Do not restart generic discovery or kickoff interviews.\n"
                "- Prefer responses that preserve completeness and let review/revision loops continue.\n"
                "- If details are missing, choose the most reasonable assumption and say so briefly.\n"
            ),
            tools=[],
            model=self.model_hint,
        )
        actor = AgentActor(name=actor_name, role=role, context_keys=[])
        task = Ask(actor=actor, prompt=prompt, output_type=output_type)
        result = await self.agent_runtime.ask(
            task,
            context=context,
            phase_name=phase_name,
        )
        if isinstance(result, output_type):
            return result
        if isinstance(result, BaseModel):
            return output_type.model_validate(result.model_dump())
        return output_type.model_validate(result)

    @staticmethod
    def _approval_prompt(prompt: str) -> str:
        return (
            "Review the following approval request and decide whether to approve "
            "it or request changes.\n\n"
            "Approve when the artifact/request is coherent and does not warrant "
            "another stakeholder turn. Reject only when further revision is truly "
            "needed, and include concise feedback.\n\n"
            f"{prompt}"
        )

    @staticmethod
    def _choice_prompt(prompt: str, options: list[str]) -> str:
        option_lines = "\n".join(f"- {opt}" for opt in options)
        return (
            "Choose exactly one of the available options.\n"
            "Prefer the option that keeps the workflow moving without cutting "
            "corners. Return the exact option text.\n\n"
            f"{prompt}\n\nAvailable options:\n{option_lines}"
        )

    @staticmethod
    def _reply_prompt(prompt: str) -> str:
        return (
            "Answer the following workflow question as the delegated stakeholder. "
            "Be concise, decisive, and keep the workflow moving. If the safest "
            "course is to proceed with reasonable assumptions, say so explicitly.\n\n"
            f"{prompt}"
        )
