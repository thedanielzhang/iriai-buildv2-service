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
        # DB = gate-approved → skip entirely
        approved_design = await runner.artifacts.get("design", feature=feature)
        if approved_design:
            logger.info("Gate-approved design exists — skipping")
            state.design = approved_design
            return state

        # Filesystem-only = compiled but not gate-reviewed → compile mockup + gate review
        compiled_design = await get_existing_artifact(runner, feature, "design")
        if compiled_design:
            logger.info("Compiled design exists but not gate-reviewed — running gate review")
            decomposition = await self._load_decomposition(runner, feature, state)

            # Compile unified mockup — skip if already exists on disk
            mirror = runner.services.get("artifact_mirror")
            unified_mockup_path = Path(mirror.feature_dir(feature.id)) / "mockup-unified.html" if mirror else None
            if unified_mockup_path and unified_mockup_path.exists():
                logger.info("Unified mockup already exists — skipping recompilation")
                hosting_svc = runner.services.get("hosting")
                if hosting_svc:
                    content = unified_mockup_path.read_text(encoding="utf-8")
                    unified_mockup_url = await hosting_svc.push_qa(
                        feature.id, "mockup:unified", content,
                        f"Unified Mockup — {feature.name}",
                    )
                else:
                    unified_mockup_url = None
            else:
                unified_mockup_url = await self._compile_mockup(runner, feature, decomposition)
            mockup_urls: dict[str, str] = {}
            if unified_mockup_url:
                mockup_urls["Unified Mockup"] = unified_mockup_url
            hosting = runner.services.get("hosting")
            if hosting:
                for sf in decomposition.subfeatures:
                    url = hosting.get_url(f"mockup:{sf.slug}")
                    if url:
                        mockup_urls[f"Mockup: {sf.name}"] = url

            # Callback to re-compile mockup after each revision cycle
            async def _refresh_mockup_resume() -> None:
                url = await self._compile_mockup(runner, feature, decomposition)
                if url:
                    mockup_urls["Unified Mockup"] = url

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
                context_keys=["project", "scope", "prd"],
                additional_urls=mockup_urls or None,
                post_compile=_refresh_mockup_resume,
            )
            # DB write now happens inside interview_gate_review() on approval.
            state.design = final_text
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
            context_keys=["project", "scope", "prd"],
        )

        # Host per-subfeature mockups and collect URLs
        mockup_urls: dict[str, str] = {}
        for sf in decomposition.subfeatures:
            url = await self._host_sf_mockup(runner, feature, sf.slug)
            if url:
                mockup_urls[f"Mockup: {sf.name}"] = url

        # ── Step 4: Integration Review ──
        review = await integration_review(
            runner, feature, self.name,
            lead_actor=lead_designer_reviewer,
            decomposition=decomposition,
            artifact_prefix="design",
            broad_key="design:broad",
        )

        if review.needs_revision:
            if not review.revision_instructions:
                logger.error(
                    "DesignPhase: integration review needs_revision=True but "
                    "revision_instructions is empty — skipping revision"
                )
            else:
                logger.info(
                    "Design integration review needs revision — re-running %d subfeatures",
                    len(review.revision_instructions),
                )
                plan = RevisionPlan(requests=[
                    RevisionRequest(
                        description=instruction,
                        reasoning="Design integration review finding",
                        affected_subfeatures=[sf_slug],
                    )
                    for sf_slug, instruction in review.revision_instructions.items()
                ])
                revision_result = await targeted_revision(
                    runner, feature, self.name,
                    revision_plan=plan,
                    decomposition=decomposition,
                    base_role=designer_role,
                    output_type=DesignDecisions,
                    artifact_prefix="design",
                    context_keys=["project", "scope", "prd"],
                )
                if not revision_result.ok:
                    failure_text = "; ".join(
                        f"{failure.artifact_prefix}:{failure.slug} — {failure.reason}"
                        for failure in revision_result.failed
                    )
                    raise RuntimeError(
                        "Design integration review targeted revision failed: "
                        + failure_text
                    )

        # ── Step 5: Compilation ──
        compiled_text = await compile_artifacts(
            runner, feature, self.name,
            compiler_actor=design_compiler,
            decomposition=decomposition,
            artifact_prefix="design",
            broad_key="design:broad",
            final_key="design",
        )

        # ── Step 5b: Compile unified mockup (agent-driven) ──
        unified_mockup_url = await self._compile_mockup(runner, feature, decomposition)
        if unified_mockup_url:
            mockup_urls["Unified Mockup"] = unified_mockup_url

        # Callback to re-compile mockup after each revision cycle
        async def _refresh_mockup() -> None:
            url = await self._compile_mockup(runner, feature, decomposition)
            if url:
                mockup_urls["Unified Mockup"] = url

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
            context_keys=["project", "scope", "prd"],
            additional_urls=mockup_urls or None,
            post_compile=_refresh_mockup,
        )

        # DB write now happens inside interview_gate_review() on approval.
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
    async def _compile_mockup(
        runner: WorkflowRunner, feature: Feature, decomposition: SubfeatureDecomposition
    ) -> str | None:
        """Agent-driven compilation of per-subfeature mockups into a unified mockup.

        The lead designer reads the compiled PRD and design-decisions.md plus
        all per-subfeature mockup.html files, then produces a single unified
        mockup with consistent design tokens and visual language.
        """
        from iriai_compose import Ask

        from ....services.artifacts import _key_to_path

        mirror = runner.services.get("artifact_mirror")
        if not mirror:
            return None

        feature_dir = Path(mirror.feature_dir(feature.id))

        # Collect per-subfeature mockup paths
        mockup_paths: list[tuple[str, Path]] = []
        for sf in decomposition.subfeatures:
            sf_dir = feature_dir / "subfeatures" / sf.slug
            matches = sorted(sf_dir.glob("mockup*.html"))
            if matches:
                mockup_paths.append((sf.name, matches[0]))

        if not mockup_paths:
            return None

        # Build source document pointing to all inputs
        compiled_design_path = feature_dir / _key_to_path("design")
        compiled_prd_path = feature_dir / _key_to_path("prd")
        output_path = feature_dir / "mockup-unified.html"

        source_lines = [
            "# Mockup Compilation Sources\n",
            f"## Compiled PRD\n**Read from:** `{compiled_prd_path}`\n",
            f"## Compiled Design Decisions\n**Read from:** `{compiled_design_path}`\n",
            "## Per-Subfeature Mockups\n",
        ]
        for sf_name, path in mockup_paths:
            source_lines.append(f"- **{sf_name}**: `{path}`")

        sources_path = feature_dir / "compile-sources-mockup.md"
        sources_path.write_text("\n".join(source_lines), encoding="utf-8")

        await runner.run(
            Ask(
                actor=lead_designer,
                prompt=(
                    f"Merge {len(mockup_paths)} per-subfeature mockups into a single "
                    "unified mockup HTML file.\n\n"
                    "Rules:\n"
                    "- Read the compiled design-decisions.md FIRST — it is the "
                    "  authoritative source for colors, typography, spacing, and "
                    "  component patterns\n"
                    "- Read the compiled PRD — it defines the requirements, "
                    "  user journeys, and acceptance criteria that the mockup "
                    "  must represent\n"
                    "- Read each per-subfeature mockup.html\n"
                    "- Produce ONE unified mockup.html with:\n"
                    "  - A single consistent set of CSS design tokens\n"
                    "  - Tab/nav navigation between subfeature views\n"
                    "  - All subfeature UIs represented\n"
                    "  - Visual consistency across all views\n"
                    "- Resolve any token conflicts (colors, spacing) using the "
                    "  compiled design decisions as the source of truth\n"
                    "- Ensure all user journeys from the PRD are represented "
                    "  in the mockup flows\n\n"
                    f"**Compiled PRD:** `{compiled_prd_path}`\n"
                    f"**Read source list from:** `{sources_path}`\n"
                    f"**Write the unified mockup to:** `{output_path}`\n"
                ),
            ),
            feature,
            phase_name="design",
        )

        if not output_path.exists():
            logger.warning("Mockup compiler did not write output to %s", output_path)
            return None

        # Host the unified mockup
        hosting = runner.services.get("hosting")
        if hosting:
            content = output_path.read_text(encoding="utf-8")
            url = await hosting.push_qa(
                feature.id, "mockup:unified", content,
                f"Unified Mockup — {feature.name}",
            )
            logger.info("Unified mockup hosted at %s", url)
            return url
        return None

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
