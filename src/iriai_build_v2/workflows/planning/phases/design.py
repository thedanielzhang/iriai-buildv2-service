from __future__ import annotations

import json as _json
import logging
from pathlib import Path

from iriai_compose import Feature, Phase, WorkflowRunner, to_str

from ....models.outputs import (
    DesignDecisions,
    RevisionPlan,
    RevisionRequest,
    SubfeatureDecomposition,
)
from ....models.state import BuildState
from ....roles import (
    design_compiler,
    designer_role,
    lead_designer,
    lead_designer_gate_reviewer,
    lead_designer_reviewer,
    user,
)
from ..._common import (
    broad_interview,
    compile_artifacts,
    get_existing_artifact,
    integration_review,
    interview_gate_review,
    per_subfeature_loop,
)
from ..._common._helpers import targeted_revision

logger = logging.getLogger(__name__)


def _make_sf_prompt(sf, context: str) -> str:
    """Build the initial interview prompt for a per-subfeature designer agent."""
    return (
        f"You are the designer for the **{sf.name}** subfeature (ID: {sf.id}, slug: {sf.slug}).\n\n"
        f"**Description:** {sf.description}\n\n"
        "The broad design system has been established (see context below). "
        "Create detailed component definitions, journey UX annotations, "
        "interaction patterns, and responsive behavior for this subfeature.\n\n"
        "Search the codebase for existing UI patterns to reuse. "
        "Document all interfaces and edges to other subfeatures explicitly.\n\n"
        f"## Context from prior work\n\n{context}"
    )


class DesignPhase(Phase):
    name = "design"

    async def execute(
        self, runner: WorkflowRunner, feature: Feature, state: BuildState
    ) -> BuildState:
        # ── Step 1: Resume check ──
        existing_design = await get_existing_artifact(runner, feature, "design")
        if existing_design:
            logger.info("Compiled design exists — skipping to end")
            state.design = existing_design
            return state

        # Load decomposition from state or artifact store
        decomposition = await self._load_decomposition(runner, feature, state)

        # ── Step 2: Broad Design Interview ──
        # Anti-pattern: do NOT create BroadDesignSystem — reuse DesignDecisions
        _, broad_text = await broad_interview(
            runner, feature, self.name,
            lead_actor=lead_designer,
            output_type=DesignDecisions,
            artifact_key="design:broad",
            artifact_label="Broad Design System",
            initial_prompt=(
                f"I'm going to establish the design foundation for: {feature.name}\n\n"
                "We'll define the visual language, color palette, typography, spacing, "
                "and shared component patterns that ALL subfeature designers will build on. "
                "This is about the design system, not individual components.\n\n"
                "What aesthetic direction are you looking for?"
            ),
        )

        # ── Step 3: Per-Subfeature Design Loop (sequential) ──
        sf_artifacts = await per_subfeature_loop(
            runner, feature, self.name,
            decomposition=decomposition,
            base_role=designer_role,
            output_type=DesignDecisions,
            artifact_prefix="design",
            broad_key="design:broad",
            make_prompt=_make_sf_prompt,
        )

        # Host per-subfeature mockups
        for sf in decomposition.subfeatures:
            await self._host_sf_mockup(runner, feature, sf.slug)

        # ── Step 4: Integration Review ──
        review = await integration_review(
            runner, feature, self.name,
            lead_actor=lead_designer_reviewer,
            decomposition=decomposition,
            artifact_prefix="design",
            broad_key="design:broad",
        )

        if review.verdict == "needs_revision" and review.revision_instructions:
            logger.info("Design integration review needs revision")
            plan = RevisionPlan(requests=[
                RevisionRequest(
                    description=instruction,
                    reasoning="Design integration review finding",
                    affected_subfeatures=[sf_slug],
                )
                for sf_slug, instruction in review.revision_instructions.items()
            ])
            await targeted_revision(
                runner, feature, self.name,
                revision_plan=plan,
                decomposition=decomposition,
                base_role=designer_role,
                output_type=DesignDecisions,
                artifact_prefix="design",
            )

        # ── Step 5: Compilation ──
        compiled_design, compiled_text = await compile_artifacts(
            runner, feature, self.name,
            compiler_actor=design_compiler,
            decomposition=decomposition,
            artifact_prefix="design",
            broad_key="design:broad",
            output_type=DesignDecisions,
            final_key="design",
        )

        # ── Step 6: Interview-Based Gate Review ──
        final_text = await interview_gate_review(
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

        state.design = final_text
        return state

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

    @staticmethod
    async def _host_sf_mockup(
        runner: WorkflowRunner, feature: Feature, sf_slug: str
    ) -> str | None:
        """Find and host a mockup HTML for a specific subfeature."""
        hosting = runner.services.get("hosting")
        mirror = runner.services.get("artifact_mirror")
        if not hosting or not mirror:
            return None

        artifact_dir = mirror.feature_dir(feature.id)

        # Search in outputs dir and workspace root
        search_dirs: list[Path] = []
        for ws in runner._workspaces.values():
            outputs = ws.path / ".iriai" / "features" / feature.slug / "outputs"
            if outputs.is_dir():
                search_dirs.append(outputs)
            search_dirs.append(ws.path)
        search_dirs.append(artifact_dir)

        source: Path | None = None
        for d in search_dirs:
            matches = sorted(d.glob(f"mockup*{sf_slug}*.html"))
            if matches:
                source = matches[0]
                break

        if source is None:
            return None

        try:
            content = source.read_text(encoding="utf-8")
            mockup_key = f"mockup:{sf_slug}"
            url = await hosting.push_qa(
                feature.id, mockup_key, content,
                f"Mockup — {sf_slug}",
            )
            logger.info("Subfeature mockup hosted at %s (found at %s)", url, source)
            return url
        except Exception:
            logger.warning("Failed to host subfeature mockup for %s", sf_slug, exc_info=True)
            return None
