"""Extended task types for the build workflow."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import Field

from iriai_compose import Interview, to_str

if TYPE_CHECKING:
    from iriai_compose import Feature, WorkflowRunner

    from ...services.hosting import DocHostingService

logger = logging.getLogger(__name__)


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

    async def on_start(
        self,
        runner: WorkflowRunner,
        feature: Feature,
    ) -> None:
        """Inject artifact output paths and wrap done predicate for file detection."""
        all_keys = [self.artifact_key] if self.artifact_key else []
        all_keys.extend(self.additional_artifact_keys)
        if not all_keys:
            return

        mirror = runner.services.get("artifact_mirror")
        if not mirror:
            return

        from ...services.artifacts import _key_to_path

        paths: list[str] = []
        for key in all_keys:
            path = mirror.feature_dir(feature.id) / _key_to_path(key)
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

        # Wrap done predicate to detect file-based completion.
        # Priority: (1) standard envelope_done, (2) agent-reported artifact_path,
        # (3) new file at expected path with no pending question.
        if self.artifact_key:
            artifact_file_path = mirror.feature_dir(feature.id) / _key_to_path(self.artifact_key)
            file_existed_before = artifact_file_path.exists()
            original_done = self.done

            def file_aware_done(response: Any) -> bool:
                # Envelope question field is authoritative — never exit
                # while the agent has a pending question for the user
                question = getattr(response, "question", "")
                if question:
                    return False
                if original_done and original_done(response):
                    return True
                # Agent reported artifact_path — verify file exists
                reported = getattr(response, "artifact_path", "")
                if reported:
                    p = Path(reported)
                    if p.exists() and p.stat().st_size > 100:
                        return True
                # Fallback: detect new file at expected path
                if (
                    not file_existed_before
                    and artifact_file_path.exists()
                    and artifact_file_path.stat().st_size > 100
                ):
                    return True
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

        mirror = runner.services.get("artifact_mirror")
        all_keys = [self.artifact_key] if self.artifact_key else []
        all_keys.extend(self.additional_artifact_keys)

        # Agent-reported artifact path (flat field on Envelope)
        reported_path = getattr(result, "artifact_path", "")

        for key in all_keys:
            if not key:
                continue

            # 1. Check agent-reported artifact_path (primary key only)
            if reported_path and key == self.artifact_key:
                p = Path(reported_path)
                if p.exists():
                    text = p.read_text(encoding="utf-8").strip()
                    if text:
                        url = await hosting.push(
                            feature.id,
                            key,
                            text,
                            f"{self.artifact_label} — {feature.name}",
                        )
                        logger.info("Artifact %s hosted from reported path %s at %s", key, reported_path, url)
                        continue

            # 2. Check if agent wrote this artifact to disk at the derived path
            if mirror:
                from ...services.artifacts import _key_to_path

                path = mirror.feature_dir(feature.id) / _key_to_path(key)
                if path.exists():
                    text = path.read_text(encoding="utf-8").strip()
                    if text:
                        url = await hosting.push(
                            feature.id,
                            key,
                            text,
                            f"{self.artifact_label} — {feature.name}",
                        )
                        logger.info("Artifact %s hosted from file at %s", key, url)
                        continue

            # Secondary key with no file — log warning (phase code handles separately)
            if key != self.artifact_key:
                logger.warning(
                    "No file found for secondary artifact %s — "
                    "phase code must handle this artifact separately",
                    key,
                )
                continue

            # 3. Fallback: extract from Envelope output (primary key only)
            if key == self.artifact_key:
                output = getattr(result, "output", None)
                if output is not None:
                    # Validate the Envelope output has actual content
                    if not _has_content(output):
                        raise RuntimeError(
                            f"HostedInterview: agent set complete=true but artifact "
                            f"'{key}' has no content. The agent must write the "
                            f"artifact to a file OR populate the structured output fields."
                        )
                    text = to_str(output)
                else:
                    text = to_str(result)

                if not text:
                    raise RuntimeError(
                        f"HostedInterview produced empty artifact for {key}"
                    )

                url = await hosting.push(
                    feature.id,
                    key,
                    text,
                    f"{self.artifact_label} — {feature.name}",
                )
                logger.info("Artifact %s hosted from Envelope at %s", key, url)


def _has_content(model: Any) -> bool:
    """Check if a Pydantic model has any non-default content worth using.

    Returns False if all string fields are empty and all list fields are empty
    (ignoring 'complete' flag). This catches the case where the agent sets
    complete=true but doesn't populate any actual content.
    """
    from pydantic import BaseModel

    if not isinstance(model, BaseModel):
        return bool(model)

    for name in model.model_fields:
        if name == "complete":
            continue
        value = getattr(model, name)
        if isinstance(value, str) and value:
            return True
        if isinstance(value, list) and value:
            return True
        if isinstance(value, dict) and value:
            return True
        if isinstance(value, BaseModel) and _has_content(value):
            return True
    return False
