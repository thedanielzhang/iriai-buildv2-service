from __future__ import annotations

import logging
from pathlib import Path

from iriai_compose import Feature, Phase, WorkflowRunner

from ....models.outputs import DesignDecisions, Envelope, envelope_done
from ....models.state import BuildState
from ....roles import designer, user
from ..._common import HostedInterview, gate_and_revise, get_existing_artifact

logger = logging.getLogger(__name__)


class DesignPhase(Phase):
    name = "design"

    async def execute(
        self, runner: WorkflowRunner, feature: Feature, state: BuildState
    ) -> BuildState:
        # Check if design artifact already exists (DB or filesystem — e.g. resuming after restart)
        existing_design_text = await get_existing_artifact(runner, feature, "design")

        if existing_design_text:
            logger.info("Design artifact exists — skipping interview, resuming at gate")
            try:
                import json as _json
                data = _json.loads(existing_design_text)
                design = DesignDecisions.model_validate(data)
            except Exception:
                design = existing_design_text

            # Re-host existing artifacts
            hosting = runner.services.get("hosting")
            if hosting:
                await hosting.push(
                    feature.id, "design", existing_design_text,
                    f"Design Decisions — {feature.name}",
                )
        else:
            # Resolve the outputs path so we can tell the designer exactly where to write
            outputs_path = ""
            project_json = await runner.artifacts.get("project", feature=feature)
            if project_json:
                import json as _json
                try:
                    outputs_path = _json.loads(project_json).get("outputs_path", "")
                except (ValueError, TypeError):
                    pass

            initial_prompt = (
                "Based on the PRD, I'll propose design decisions including component "
                "structure, user flows, and interaction patterns. Let me ask a few "
                "clarifying questions about your UX preferences first."
            )
            if outputs_path:
                initial_prompt += (
                    f"\n\n**IMPORTANT: When you create the mockup HTML file, write it to "
                    f"exactly this path: `{outputs_path}/mockup.html`**"
                )

            envelope: Envelope[DesignDecisions] = await runner.run(
                HostedInterview(
                    questioner=designer,
                    responder=user,
                    initial_prompt=initial_prompt,
                    output_type=Envelope[DesignDecisions],
                    done=envelope_done,
                    artifact_key="design",
                    artifact_label="Design Decisions",
                ),
                feature,
                phase_name=self.name,
            )

            design = envelope.output

        # Host mockup if the designer created one during the interview
        mockup_url = await self._host_mockup(runner, feature)

        # Build gate label with both review URLs
        hosting = runner.services.get("hosting")
        design_url = hosting.get_url("design") if hosting else None

        label = "Design Decisions"
        review_links: list[str] = []
        if design_url:
            review_links.append(f"Design decisions: {design_url}")
        if mockup_url:
            review_links.append(f"Mockup: {mockup_url}")
        if review_links:
            label += "\nReview in browser: " + " | ".join(review_links)

        # Collect annotations from both design decisions and mockup sessions
        ann_keys = ["design"]
        if mockup_url:
            ann_keys.append("mockup")

        design, design_text = await gate_and_revise(
            runner, feature, self.name,
            artifact=design, actor=designer, output_type=DesignDecisions,
            approver=user, label=label,
            artifact_key="design",
            annotation_keys=ann_keys,
        )

        await runner.artifacts.put("design", design_text, feature=feature)
        state.design = design_text
        return state

    @staticmethod
    async def _host_mockup(
        runner: WorkflowRunner, feature: Feature
    ) -> str | None:
        """Find and host the mockup HTML if the designer wrote one.

        Globs for ``mockup*.html`` in multiple locations (the designer
        may name the file unpredictably).  Search order:

        1. Feature outputs dir (``.iriai/features/{slug}/outputs/``)
        2. Workspace root (fallback for misbehaved writes)
        3. Artifact mirror dir (already hosted from a prior session)

        When found outside the artifact mirror, the file is copied there
        so ``rehost_existing`` can find it after a restart.
        """
        hosting = runner.services.get("hosting")
        mirror = runner.services.get("artifact_mirror")
        if not hosting or not mirror:
            return None

        artifact_dir = mirror.feature_dir(feature.id)

        # Directories to glob, in priority order
        search_dirs: list[Path] = []
        for ws in runner._workspaces.values():
            outputs = ws.path / ".iriai" / "features" / feature.slug / "outputs"
            if outputs.is_dir():
                search_dirs.append(outputs)
            search_dirs.append(ws.path)  # workspace root fallback
        search_dirs.append(artifact_dir)

        # Find the first mockup*.html match
        source: Path | None = None
        for d in search_dirs:
            matches = sorted(d.glob("mockup*.html"))
            if matches:
                source = matches[0]
                break

        if source is None:
            return None

        try:
            content = source.read_text(encoding="utf-8")

            # Copy to artifact mirror so rehost_existing picks it up
            target = artifact_dir / "mockup.html"
            if source != target:
                target.write_text(content, encoding="utf-8")
                logger.info("Copied mockup from %s → %s", source, target)

            url = await hosting.push_qa(
                feature.id, "mockup", content,
                f"Mockup — {feature.name}",
            )
            logger.info("Mockup hosted at %s (found at %s)", url, source)
            return url
        except Exception:
            logger.warning("Failed to host mockup", exc_info=True)
            return None
