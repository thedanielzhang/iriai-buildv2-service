"""Extended task types for the build workflow."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import Field, PrivateAttr

from iriai_compose import Ask, Task, to_str
from iriai_compose.prompts import Select

if TYPE_CHECKING:
    from iriai_compose import Feature, WorkflowRunner

    from ...services.hosting import DocHostingService

from ..planning._control import BACKGROUND_RESPONSE, STEP_INTERACTIVE
from ..planning._threading import build_agent_fill_prompt

logger = logging.getLogger(__name__)


def _current_phase_name(explicit: Any = None) -> str:
    if isinstance(explicit, str) and explicit:
        return explicit
    try:
        from ...workflows._runner import _phase_name_var

        phase_name = _phase_name_var.get()
        if phase_name:
            return str(phase_name)
    except Exception:
        pass
    try:
        from iriai_compose.runner import _current_phase_var

        phase_name = _current_phase_var.get()
        if phase_name:
            return str(phase_name)
    except Exception:
        pass
    return ""


class Interview(Task):
    """Multi-turn interview loop owned by build-v2."""

    questioner: Any
    responder: Any
    initial_prompt: str
    output_type: Any = None
    done: Any

    async def execute(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        **kwargs: Any,
    ) -> Any:
        response = await runner.run(
            Ask(
                actor=self.questioner,
                prompt=self.initial_prompt,
                context_keys=self.context_keys,
                output_type=self.output_type,
            ),
            feature,
            **kwargs,
        )

        if self.done(response):
            return response

        while True:
            answer = await runner.run(
                Ask(actor=self.responder, prompt=to_str(response)),
                feature,
                **kwargs,
            )
            result = await runner.run(
                Ask(
                    actor=self.questioner,
                    prompt=f"The user responded:\n\n{to_str(answer)}",
                    context_keys=self.context_keys,
                    output_type=self.output_type,
                    continuation=True,
                ),
                feature,
                **kwargs,
            )
            if self.done(result):
                return result
            response = result


class Gate(Task):
    """Approval gate owned by build-v2."""

    approver: Any
    prompt: str

    async def execute(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        **kwargs: Any,
    ) -> Any:
        choice = await runner.run(
            Ask(
                actor=self.approver,
                prompt=self.prompt,
                input=Select(options=["Approve", "Reject", "Give feedback"]),
                input_type=Select,
                context_keys=self.context_keys,
            ),
            feature,
            kind="approve",
            **kwargs,
        )
        feedback = _gate_feedback_from_choice(choice)
        if feedback is not None:
            feedback = feedback.strip()
            return feedback if feedback else False
        if choice == "Give feedback":
            return await runner.run(
                Ask(actor=self.approver, prompt="Please provide your feedback:"),
                feature,
                **kwargs,
            )
        return choice is True or choice == "Approve"


class Choose(Task):
    """Selection task owned by build-v2."""

    chooser: Any
    prompt: str
    options: list[str]

    async def execute(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        **kwargs: Any,
    ) -> Any:
        return await runner.run(
            Ask(
                actor=self.chooser,
                prompt=self.prompt,
                input=Select(options=self.options),
                input_type=Select,
                context_keys=self.context_keys,
            ),
            feature,
            **kwargs,
        )


class Respond(Task):
    """Free-form response task owned by build-v2."""

    responder: Any
    prompt: str

    async def execute(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        **kwargs: Any,
    ) -> Any:
        return await runner.run(
            Ask(
                actor=self.responder,
                prompt=self.prompt,
                context_keys=self.context_keys,
            ),
            feature,
            **kwargs,
        )


class Notify(Task):
    """One-way notification that never creates interactive pending state."""

    message: str

    async def execute(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        **kwargs: Any,
    ) -> None:
        runtimes = getattr(runner, "interaction_runtimes", {}) or {}
        runtime = runtimes.get("terminal") or next(iter(runtimes.values()), None)
        notify = getattr(runtime, "notify", None)
        if callable(notify):
            await notify(
                feature_id=feature.id,
                phase_name=_current_phase_name(kwargs.get("phase_name")),
                message=self.message,
            )
            return
        print(self.message)


@dataclass
class ThreadedInterviewOutcome:
    result: Any | None = None
    background_requested: bool = False
    pending_response: Any | None = None


class HostedInterview(Interview):
    """Interview that pushes artifacts to DocHostingService on completion.

    Supports file-based artifacts: agents write files to the artifact mirror
    path using the Write tool.  ``on_start`` injects paths into the prompt
    and wraps the ``done`` predicate to detect file writes.  ``on_done``
    checks the agent-reported ``artifact_path`` first, then falls back to
    the derived mirror path, then to Envelope output.

    For multi-artifact interviews (e.g. architecture: plan + system-design),
    pass secondary keys via ``additional_artifact_keys``.
    """

    artifact_key: str = ""
    artifact_label: str = ""
    additional_artifact_keys: list[str] = Field(default_factory=list)
    prefer_structured_output: bool = False
    _artifact_output_paths: dict[str, Path] = PrivateAttr(default_factory=dict)
    _artifact_files_existed_before: dict[str, bool] = PrivateAttr(default_factory=dict)

    def _all_artifact_keys(self) -> list[str]:
        keys = [self.artifact_key] if self.artifact_key else []
        keys.extend(self.additional_artifact_keys)
        return [key for key in keys if key]

    def _staging_path(self, mirror: Any, feature: Feature, key: str) -> Path:
        from ...services.artifacts import _key_to_path

        final_rel = Path(_key_to_path(key))
        return mirror.feature_dir(feature.id) / ".staging" / final_rel

    @staticmethod
    def _output_attr_name(key: str) -> str:
        return key.split(":", 1)[0].replace("-", "_")

    def _structured_artifact_text(self, result: Any, key: str) -> tuple[str, str] | None:
        output = getattr(result, "output", None)
        if output is None:
            return None

        if self.prefer_structured_output and key == self.artifact_key and _has_content(output):
            text = to_str(output).strip()
            if text:
                return text, "structured output"

        attr_name = self._output_attr_name(key)
        if hasattr(output, attr_name):
            value = getattr(output, attr_name)
            if _has_content(value):
                text = to_str(value).strip()
                if text:
                    return text, f"structured output field `{attr_name}`"

        if key == self.artifact_key and _has_content(output):
            text = to_str(output).strip()
            if text:
                return text, "Envelope output"
        return None

    def _reported_path_text(self, result: Any, key: str) -> tuple[str, str] | None:
        if key != self.artifact_key:
            return None
        reported_path = getattr(result, "artifact_path", "")
        if not reported_path:
            return None
        path = Path(reported_path)
        if not path.exists():
            return None
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            return None
        return text, f"reported path `{reported_path}`"

    def _mirror_path_text(
        self,
        mirror: Any,
        feature: Feature,
        key: str,
    ) -> tuple[str, str] | None:
        path = self._artifact_output_paths.get(key) or self._staging_path(mirror, feature, key)
        if path.exists():
            text = path.read_text(encoding="utf-8").strip()
            if text:
                return text, f"staging path `{path}`"

        from ...services.artifacts import _key_to_path

        final_path = mirror.feature_dir(feature.id) / _key_to_path(key)
        if final_path.exists():
            text = final_path.read_text(encoding="utf-8").strip()
            if text:
                return text, f"legacy file path `{final_path}`"
        return None

    def _resolve_artifact_payload(
        self,
        result: Any,
        *,
        mirror: Any,
        feature: Feature,
        key: str,
    ) -> tuple[str, str] | None:
        if self.prefer_structured_output and key == self.artifact_key:
            structured = self._structured_artifact_text(result, key)
            if structured is not None:
                return structured

        reported = self._reported_path_text(result, key)
        if reported is not None:
            return reported

        if mirror:
            mirrored = self._mirror_path_text(mirror, feature, key)
            if mirrored is not None:
                return mirrored

        return self._structured_artifact_text(result, key)

    def _artifacts_ready(self, result: Any, *, mirror: Any, feature: Feature) -> bool:
        return all(
            self._resolve_artifact_payload(result, mirror=mirror, feature=feature, key=key)
            is not None
            for key in self._all_artifact_keys()
        )

    def _has_new_artifact_file(self, result: Any, *, mirror: Any, feature: Feature) -> bool:
        reported = getattr(result, "artifact_path", "")
        if reported:
            reported_path = Path(reported)
            if reported_path.exists() and reported_path.stat().st_size > 0:
                return True

        if not mirror:
            return False

        for key in self._all_artifact_keys():
            path = self._artifact_output_paths.get(key) or self._staging_path(mirror, feature, key)
            if (
                path.exists()
                and path.stat().st_size > 0
                and not self._artifact_files_existed_before.get(key, False)
            ):
                return True
        return False

    async def _rollback_artifact_keys(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        keys: list[str],
    ) -> None:
        for key in reversed(keys):
            try:
                await runner.artifacts.delete(key, feature=feature)
            except Exception:
                logger.warning("Failed to roll back artifact %s", key, exc_info=True)

    async def _rollback_hosted_artifacts(
        self,
        hosting: DocHostingService,
        feature: Feature,
        keys: list[str],
    ) -> None:
        for key in reversed(keys):
            try:
                await hosting.delete(feature.id, key)
            except Exception:
                logger.warning("Failed to roll back hosted artifact %s", key, exc_info=True)

    async def on_start(
        self,
        runner: WorkflowRunner,
        feature: Feature,
    ) -> None:
        """Inject artifact output paths and wrap done predicate for file detection."""
        all_keys = self._all_artifact_keys()
        if not all_keys:
            return

        mirror = runner.services.get("artifact_mirror")
        if not mirror:
            return

        from ...services.artifacts import _key_to_path

        paths: list[str] = []
        for key in all_keys:
            path = self._staging_path(mirror, feature, key)
            self._artifact_output_paths[key] = path
            self._artifact_files_existed_before[key] = path.exists()
            paths.append(f"- `{key}` → `{path}`")

        self.initial_prompt += (
            f"\n\n## Artifact Output\n"
            f"When the discussion is complete and you have gathered all user input, "
            f"write the final artifacts to these paths:\n"
            + "\n".join(paths)
            + "\n\n"
            "Use the Write tool. Write markdown for document artifacts, "
            "JSON for data artifacts (like system design).\n"
            "Then set `complete = true` and `artifact_path` to the path you wrote. "
            "The `output` field can be left null — the file is the real artifact.\n"
            "**IMPORTANT:** Always present your analysis and questions in the "
            "`question` field first. Do NOT write artifacts or set `complete = true` "
            "until the user has responded and all concerns are resolved.\n"
        )
        if self.prefer_structured_output:
            self.initial_prompt += (
                "\nFor this artifact, the structured `output` is the canonical source of truth. "
                "Make sure it is fully populated and matches the final artifact exactly.\n"
            )

        # Wrap done predicate to detect file-based completion.
        # Priority: (1) standard envelope_done, (2) agent-reported artifact_path,
        # (3) new file at expected path with no pending question.
        if self.artifact_key:
            original_done = self.done

            def file_aware_done(response: Any) -> bool:
                # Envelope question field is authoritative — never exit
                # while the agent has a pending question for the user
                question = getattr(response, "question", "")
                if question:
                    return False
                if original_done and original_done(response):
                    return self._artifacts_ready(response, mirror=mirror, feature=feature)
                if self._has_new_artifact_file(response, mirror=mirror, feature=feature):
                    return self._artifacts_ready(response, mirror=mirror, feature=feature)
                return False

            self.done = file_aware_done

    async def on_done(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        *,
        result: Any = None,
        error: BaseException | None = None,
    ) -> None:
        if error or result is None:
            return

        hosting: DocHostingService | None = runner.services.get("hosting")
        if not hosting:
            raise RuntimeError(
                "DocHostingService not available — was workflow on_start called?"
            )
        feature_label = getattr(feature, "name", getattr(feature, "id", "artifact"))

        mirror = runner.services.get("artifact_mirror")
        resolved_payloads: dict[str, tuple[str, str]] = {}
        for key in self._all_artifact_keys():
            payload = self._resolve_artifact_payload(result, mirror=mirror, feature=feature, key=key)
            if payload is None:
                if key == self.artifact_key:
                    raise RuntimeError(
                        f"HostedInterview: agent set complete=true but artifact "
                        f"'{key}' has no content. The agent must write the artifact "
                        f"to a file OR populate the structured output fields."
                    )
                raise RuntimeError(
                    f"HostedInterview: required additional artifact '{key}' was not "
                    f"written. The agent must write every declared artifact before "
                    f"marking the interview complete."
                )
            resolved_payloads[key] = payload

        written_keys: list[str] = []
        try:
            for key, (text, _) in resolved_payloads.items():
                await runner.artifacts.put(key, text, feature=feature)
                written_keys.append(key)
        except Exception:
            await self._rollback_artifact_keys(runner, feature, written_keys)
            raise

        hosted_keys: list[str] = []
        try:
            for key, (text, source) in resolved_payloads.items():
                try:
                    url = await hosting.push(
                        feature.id,
                        key,
                        text,
                        f"{self.artifact_label} — {feature_label}",
                    )
                except Exception:
                    await self._rollback_hosted_artifacts(
                        hosting,
                        feature,
                        hosted_keys + [key],
                    )
                    await self._rollback_artifact_keys(runner, feature, written_keys)
                    raise
                hosted_keys.append(key)
                logger.info("Artifact %s hosted from %s at %s", key, source, url)
        except Exception:
            if len(hosted_keys) == len(resolved_payloads):
                await self._rollback_artifact_keys(runner, feature, written_keys)
            raise


class ThreadedHostedInterview(HostedInterview):
    """Hosted interview that can hand off to a background responder."""

    thread_label: str = ""
    allow_background: bool = True
    mode: str = STEP_INTERACTIVE
    background_responder: Any | None = None

    def _serialize_response(self, response: Any) -> str:
        if not self.allow_background:
            return to_str(response)
        try:
            data = json.loads(to_str(response))
        except (json.JSONDecodeError, TypeError):
            return to_str(response)
        if not isinstance(data, dict):
            return to_str(response)
        if data.get("question"):
            data["allow_background"] = True
            if self.thread_label:
                data["thread_label"] = self.thread_label
            return json.dumps(data)
        return to_str(response)

    async def execute(self, runner: WorkflowRunner, feature: Feature) -> Any:
        response = await runner.resolve(
            self.questioner,
            self.initial_prompt,
            feature=feature,
            context_keys=self.context_keys,
            output_type=self.output_type,
        )
        if self.done(response):
            return response

        active_responder = self.responder if self.mode == STEP_INTERACTIVE else self.background_responder
        while True:
            if active_responder is None:
                raise RuntimeError("ThreadedHostedInterview requires a responder")

            if self.mode == STEP_INTERACTIVE:
                answer = await runner.resolve(
                    active_responder,
                    self._serialize_response(response),
                    feature=feature,
                    kind="respond",
                )
                if answer == BACKGROUND_RESPONSE and self.background_responder is not None:
                    return ThreadedInterviewOutcome(
                        background_requested=True,
                        pending_response=response,
                    )
            else:
                answer = await runner.resolve(
                    active_responder,
                    build_agent_fill_prompt(
                        label=self.thread_label or self.artifact_label,
                        response_text=to_str(response),
                    ),
                    feature=feature,
                )

            result = await runner.resolve(
                self.questioner,
                f"The responder replied:\n\n{to_str(answer)}",
                feature=feature,
                context_keys=self.context_keys,
                output_type=self.output_type,
                continuation=True,
            )
            if self.done(result):
                return result
            response = result

    async def on_done(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        *,
        result: Any = None,
        error: BaseException | None = None,
    ) -> None:
        if isinstance(result, ThreadedInterviewOutcome):
            if result.background_requested:
                return
            result = result.result
        await super().on_done(runner, feature, result=result, error=error)


def _has_content(model: Any) -> bool:
    """Check if a Pydantic model has any non-default content worth using.

    Returns False if all string fields are empty and all list fields are empty
    (ignoring 'complete' flag). This catches the case where the agent sets
    complete=true but doesn't populate any actual content.
    """
    from pydantic import BaseModel

    if not isinstance(model, BaseModel):
        return bool(model)

    for name in type(model).model_fields:
        if name == "complete":
            continue
        value = getattr(model, name)
        if isinstance(value, bool) and value:
            return True
        if isinstance(value, str) and value:
            return True
        if isinstance(value, list) and value:
            return True
        if isinstance(value, dict) and value:
            return True
        if isinstance(value, BaseModel) and _has_content(value):
            return True
    return False


def _gate_feedback_from_choice(choice: Any) -> str | None:
    """Extract structured gate feedback from runtime-specific rejection objects."""
    if isinstance(choice, dict):
        feedback = choice.get("feedback")
        if isinstance(feedback, str):
            return feedback
        return None
    feedback = getattr(choice, "feedback", None)
    if isinstance(feedback, str):
        return feedback
    return None
