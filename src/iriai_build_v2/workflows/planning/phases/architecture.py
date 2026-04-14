from __future__ import annotations

import json as _json
import logging

from pydantic import BaseModel

from iriai_compose import AgentActor, Feature, Phase, WorkflowRunner, to_str

from ....models.outputs import (
    ArchitectureOutput,
    Envelope,
    RevisionPlan,
    RevisionRequest,
    SubfeatureDecomposition,
    SystemDesign,
    TechnicalPlan,
    envelope_done,
)
from ....models.state import BuildState
from ....roles import (
    InterviewActor,
    architect_role,
    lead_architect,
    lead_architect_gate_reviewer,
    lead_architect_reviewer,
    plan_arch_compiler,
    sysdesign_compiler,
    user,
)
from ....services.system_design_html import render_system_design_html
from ..._common import (
    HostedInterview,
    broad_interview,
    compile_artifacts,
    gate_and_revise,
    get_existing_artifact,
    integration_review,
)
from ..._common._helpers import generate_summary, targeted_revision

logger = logging.getLogger(__name__)


def _make_sf_prompt(sf, context: str) -> str:
    """Build the initial interview prompt for a per-subfeature architect agent."""
    return (
        f"You are the architect for the **{sf.name}** subfeature (ID: {sf.id}, slug: {sf.slug}).\n\n"
        f"**Description:** {sf.description}\n\n"
        "The broad architecture has been established (see context below). "
        "Define implementation steps with file scope, API contracts, data models, "
        "and system design for this subfeature.\n\n"
        "You have access to the codebase — ground every decision in existing code. "
        "Document all interfaces and edges to other subfeatures explicitly.\n\n"
        f"## Context from prior work\n\n{context}"
    )


class ArchitecturePhase(Phase):
    name = "architecture"

    async def execute(
        self, runner: WorkflowRunner, feature: Feature, state: BuildState
    ) -> BuildState:
        # ── Step 1: Resume check ──
        # DB = gate-approved → skip entirely
        approved_plan = await runner.artifacts.get("plan", feature=feature)
        if approved_plan:
            logger.info("Gate-approved plan exists — skipping")
            state.plan = approved_plan
            state.system_design = await runner.artifacts.get("system-design", feature=feature) or ""
            return state

        # Filesystem-only = compiled but not gate-reviewed → jump to gate review
        compiled_plan = await get_existing_artifact(runner, feature, "plan")
        if compiled_plan:
            logger.info("Compiled plan exists but not gate-reviewed — running gate review")
            decomposition = await self._load_decomposition(runner, feature, state)
            plan_text = await self._plan_gate_review(runner, feature, decomposition)
            # DB write now happens inside interview_gate_review() on approval.
            sd_text = await self._system_design_gate_review(runner, feature, decomposition)
            state.plan = plan_text
            state.system_design = sd_text
            return state

        decomposition = await self._load_decomposition(runner, feature, state)

        # ── Step 2: Broad Architecture Interview ──
        # Anti-pattern: do NOT create BroadArchitecture — reuse TechnicalPlan
        _, broad_text = await broad_interview(
            runner, feature, self.name,
            lead_actor=lead_architect,
            output_type=TechnicalPlan,
            artifact_key="plan:broad",
            artifact_label="Broad Architecture",
            initial_prompt=(
                f"I'm going to establish the system architecture for: {feature.name}\n\n"
                "We'll define the system topology, tech stack, deployment model, "
                "security architecture, and API conventions that ALL subfeature architects "
                "will build on.\n\n"
                "Let me start by exploring the codebase to understand existing patterns."
            ),
        )

        # ── Step 3: Per-Subfeature Architecture Loop (sequential) ──
        # Architecture produces ArchitectureOutput (plan + system_design), not just TechnicalPlan.
        # We can't use per_subfeature_loop directly since it expects a single output_type.
        # Instead we do the loop manually.
        await self._per_subfeature_arch_loop(runner, feature, decomposition)

        # ── Step 4: Integration Review ──
        review = await integration_review(
            runner, feature, self.name,
            lead_actor=lead_architect_reviewer,
            decomposition=decomposition,
            artifact_prefix="plan",
            broad_key="plan:broad",
        )

        if review.needs_revision:
            if not review.revision_instructions:
                logger.error(
                    "ArchitecturePhase: integration review needs_revision=True but "
                    "revision_instructions is empty — skipping revision"
                )
            else:
                logger.info(
                    "Architecture integration review needs revision — re-running %d subfeatures",
                    len(review.revision_instructions),
                )
                plan = RevisionPlan(requests=[
                    RevisionRequest(
                        description=instruction,
                        reasoning="Architecture integration review finding",
                        affected_subfeatures=[sf_slug],
                    )
                    for sf_slug, instruction in review.revision_instructions.items()
                ])
                await targeted_revision(
                    runner, feature, self.name,
                    revision_plan=plan,
                    decomposition=decomposition,
                    base_role=architect_role,
                    output_type=TechnicalPlan,
                    artifact_prefix="plan",
                    context_keys=["project", "scope", "prd", "design"],
                )

        # ── Step 5: Dual Compilation ──
        # 5a: Technical Plan compilation
        plan_text = await compile_artifacts(
            runner, feature, self.name,
            compiler_actor=plan_arch_compiler,
            decomposition=decomposition,
            artifact_prefix="plan",
            broad_key="plan:broad",
            final_key="plan",
        )

        # 5b: System Design compilation
        await self._compile_system_design(runner, feature, decomposition)

        # ── Step 6: Dual Interview-Based Gate Review ──
        # 6a: Technical Plan gate review
        plan_text = await self._plan_gate_review(runner, feature, decomposition)

        # 6b: System Design gate review
        sd_text = await self._system_design_gate_review(runner, feature, decomposition)

        # DB writes now happen inside interview_gate_review() on approval.
        state.plan = plan_text
        state.system_design = sd_text
        return state

    async def _per_subfeature_arch_loop(
        self, runner: WorkflowRunner, feature: Feature, decomposition: SubfeatureDecomposition
    ) -> None:
        """Sequential loop for per-subfeature architecture interviews.

        Each produces ArchitectureOutput (TechnicalPlan + SystemDesign).
        Stores plan:{slug} and system-design:{slug} separately.
        """
        from ..._common._helpers import _build_subfeature_context, _get_user

        approver = _get_user()
        completed_plans: dict[str, str] = {}
        completed_summaries: dict[str, str] = {}

        broad_text = await runner.artifacts.get("plan:broad", feature=feature) or ""
        decomp_text = await runner.artifacts.get("decomposition", feature=feature) or ""

        for sf in decomposition.subfeatures:
            plan_key = f"plan:{sf.slug}"
            sd_key = f"system-design:{sf.slug}"

            # Resume check: DB = approved (artifacts.put only happens after gate)
            approved_text = await runner.artifacts.get(plan_key, feature=feature)
            if approved_text:
                logger.info("Subfeature arch %s approved — skipping", plan_key)
                completed_plans[sf.slug] = approved_text
                summary = await runner.artifacts.get(f"plan-summary:{sf.slug}", feature=feature)
                if summary:
                    completed_summaries[sf.slug] = summary
                continue

            # Draft check: file exists but not approved — run gate only
            draft_text = await get_existing_artifact(runner, feature, plan_key)
            if draft_text:
                logger.info("Subfeature arch %s exists as draft — running gate", plan_key)
                sf_actor = InterviewActor(
                    name=f"architect-sf-{sf.slug}",
                    role=architect_role,
                    context_keys=["project", "scope", "prd", "design"],
                )
                # Host plan draft so gate cards have review URLs
                hosting = runner.services.get("hosting")
                if hosting:
                    await hosting.push(feature.id, plan_key, draft_text, f"Technical Plan — {sf.name}")

                # Generate SD from current plan text
                sd_json = await self._convert_and_host_sd(runner, feature, sd_key, draft_text, sf.name)

                # Rehost both plan and SD on every plan revision during the gate loop
                async def _on_plan_revised(key: str, text: str) -> None:
                    await self._rehost_plan_and_sd(
                        runner, feature, plan_key, sd_key, sf.name, text,
                    )

                # Gate the plan
                plan_obj, plan_text = await gate_and_revise(
                    runner, feature, self.name,
                    artifact=draft_text, actor=sf_actor, output_type=TechnicalPlan,
                    approver=approver, label=f"Technical Plan — {sf.name}",
                    artifact_key=plan_key, annotation_keys=[plan_key, sd_key],
                    post_update=_on_plan_revised,
                )
                plan_text = to_str(plan_obj) if isinstance(plan_obj, BaseModel) else plan_text

                # SD was already regenerated by _on_plan_revised during the
                # gate loop — no need to run the extractor again.
                await runner.artifacts.put(sd_key, sd_json, feature=feature)
                await runner.artifacts.put(plan_key, plan_text, feature=feature)
                completed_plans[sf.slug] = plan_text
                summary = await generate_summary(runner, feature, "plan", sf.slug)
                if summary:
                    completed_summaries[sf.slug] = summary
                continue

            # Build tiered context
            context = _build_subfeature_context(
                decomposition, sf.slug,
                completed_plans, completed_summaries,
                broad_text, decomp_text,
            )
            prompt = _make_sf_prompt(sf, context)

            sf_actor = InterviewActor(
                name=f"architect-sf-{sf.slug}",
                role=architect_role,
                context_keys=["project", "scope", "prd", "design"],
            )

            envelope = await runner.run(
                HostedInterview(
                    questioner=sf_actor,
                    responder=approver,
                    initial_prompt=prompt,
                    output_type=Envelope[ArchitectureOutput],
                    done=envelope_done,
                    artifact_key=plan_key,
                    artifact_label=f"Architecture — {sf.name}",
                    additional_artifact_keys=[sd_key],
                ),
                feature,
                phase_name=self.name,
            )

            # Read artifacts from files (preferred) or Envelope output
            from ....services.artifacts import _key_to_path

            plan_text = None
            sd_text = None
            mirror = runner.services.get("artifact_mirror")
            if mirror:
                for key, attr in [(plan_key, "plan"), (sd_key, "system_design")]:
                    path = mirror.feature_dir(feature.id) / _key_to_path(key)
                    if path.exists():
                        text = path.read_text(encoding="utf-8").strip()
                        if text:
                            if attr == "plan":
                                plan_text = text
                            else:
                                sd_text = text

            arch_output = envelope.output
            if not plan_text:
                plan_text = to_str(arch_output.plan) if arch_output else ""
            if not sd_text:
                sd_text = to_str(arch_output.system_design) if arch_output else ""

            # Convert system design markdown to HTML for hosting
            await self._convert_and_host_sd(runner, feature, sd_key, sd_text, sf.name)

            # Post-update callback: re-convert SD after SD-specific revisions
            async def _sd_post_update(key: str, text: str) -> None:
                if key.startswith("system-design"):
                    await self._convert_and_host_sd(runner, feature, key, text, sf.name)

            # Rehost both plan and SD on every plan revision during the gate loop
            async def _on_plan_revised_new(key: str, text: str) -> None:
                await self._rehost_plan_and_sd(
                    runner, feature, plan_key, sd_key, sf.name, text,
                )

            # Gate the plan (with system design link)
            original_plan_text = plan_text
            plan_obj, plan_text = await gate_and_revise(
                runner, feature, self.name,
                artifact=plan_text, actor=sf_actor, output_type=TechnicalPlan,
                approver=approver, label=f"Technical Plan — {sf.name}",
                artifact_key=plan_key,
                annotation_keys=[plan_key, sd_key],
                post_update=_on_plan_revised_new,
            )
            plan_text = to_str(plan_obj) if not isinstance(plan_text, str) else plan_text

            # SD was already regenerated by _on_plan_revised_new during the
            # gate loop — no need to run the extractor again.

            # Gate the system design
            sd_obj, sd_text = await gate_and_revise(
                runner, feature, self.name,
                artifact=sd_text, actor=sf_actor, output_type=SystemDesign,
                approver=approver, label=f"System Design — {sf.name}",
                artifact_key=sd_key,
                post_update=_sd_post_update,
            )
            sd_text = to_str(sd_obj) if not isinstance(sd_text, str) else sd_text

            await runner.artifacts.put(plan_key, plan_text, feature=feature)
            await runner.artifacts.put(sd_key, sd_text, feature=feature)
            completed_plans[sf.slug] = plan_text

            # Generate summary
            summary = await generate_summary(runner, feature, "plan", sf.slug)
            if summary:
                completed_summaries[sf.slug] = summary

    async def _compile_system_design(
        self, runner: WorkflowRunner, feature: Feature, decomposition: SubfeatureDecomposition
    ) -> str:
        """Compile per-subfeature system designs into a single document.

        Uses file-based output to avoid structured output token limits.
        HTML rendering is handled by ``_convert_and_host_sd`` (text→SystemDesign→HTML).
        """
        from pathlib import Path

        from iriai_compose import Ask

        from ....services.artifacts import _key_to_path

        parts = []
        decomp_text = await runner.artifacts.get("decomposition", feature=feature)
        if decomp_text:
            parts.append(f"## Decomposition\n\n{decomp_text}")
        for sf in decomposition.subfeatures:
            sd_text = await runner.artifacts.get(f"system-design:{sf.slug}", feature=feature)
            if sd_text:
                parts.append(f"## System Design: {sf.name} ({sf.slug})\n\n{sd_text}")

        source_text = "\n\n---\n\n".join(parts)

        # Resolve output file path
        mirror = runner.services.get("artifact_mirror")
        if mirror:
            feature_dir = Path(mirror.feature_dir(feature.id))
            file_path = feature_dir / _key_to_path("system-design")
            file_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            import tempfile
            feature_dir = Path(tempfile.mkdtemp())
            file_path = feature_dir / "system-design.md"
            file_path.parent.mkdir(parents=True, exist_ok=True)

        # Write source artifacts to file so the compiler can read them
        sources_path = feature_dir / "compile-sources-system-design.md"
        sources_path.parent.mkdir(parents=True, exist_ok=True)
        sources_path.write_text(source_text, encoding="utf-8")

        await runner.run(
            Ask(
                actor=sysdesign_compiler,
                prompt=(
                    f"Compile {len(decomposition.subfeatures)} subfeature system designs "
                    "into a single unified system design.\n\n"
                    "Rules:\n"
                    "- Union all services, deduplicate by id\n"
                    "- Union all connections, deduplicate by (from_id, to_id)\n"
                    "- Union all API endpoints, group by service\n"
                    "- Union all entities, deduplicate by (name, service_id)\n"
                    "- Union all call paths, decisions, risks\n\n"
                    f"**Read the source artifacts from:** `{sources_path}`\n"
                    f"**Write the complete compiled system design to:** `{file_path}`\n"
                ),
            ),
            feature,
            phase_name=self.name,
        )

        if not file_path.exists():
            raise RuntimeError(f"System design compiler did not write output to {file_path}")
        sd_text = file_path.read_text(encoding="utf-8").strip()
        if not sd_text:
            raise RuntimeError(f"System design compiler wrote empty file at {file_path}")

        # NOTE: do NOT store to DB here — wait until gate review approves.

        # Convert to HTML via the two-pass approach (text→SystemDesign→render)
        await self._convert_and_host_sd(runner, feature, "system-design", sd_text, feature.name)

        return sd_text

    async def _plan_gate_review(
        self, runner: WorkflowRunner, feature: Feature, decomposition: SubfeatureDecomposition
    ) -> str:
        """Interview-based gate review for the compiled technical plan."""
        from ..._common import interview_gate_review
        return await interview_gate_review(
            runner, feature, self.name,
            lead_actor=lead_architect_gate_reviewer,
            decomposition=decomposition,
            artifact_prefix="plan",
            compiled_key="plan",
            base_role=architect_role,
            output_type=TechnicalPlan,
            compiler_actor=plan_arch_compiler,
            broad_key="plan:broad",
            context_keys=["project", "scope", "prd", "design"],
        )

    async def _system_design_gate_review(
        self, runner: WorkflowRunner, feature: Feature, decomposition: SubfeatureDecomposition
    ) -> str:
        """Interview-based gate review for the compiled system design."""
        from ..._common import interview_gate_review

        async def _sd_gate_post_update(key: str, text: str) -> None:
            if key.startswith("system-design"):
                await self._convert_and_host_sd(runner, feature, key, text, feature.name)

        async def _sd_post_compile() -> None:
            """Re-convert the recompiled system design to HTML."""
            sd_text = await get_existing_artifact(runner, feature, "system-design")
            if sd_text:
                await self._convert_and_host_sd(
                    runner, feature, "system-design", sd_text, feature.name,
                )

        return await interview_gate_review(
            runner, feature, self.name,
            lead_actor=lead_architect_gate_reviewer,
            decomposition=decomposition,
            artifact_prefix="system-design",
            compiled_key="system-design",
            base_role=architect_role,
            output_type=SystemDesign,
            compiler_actor=sysdesign_compiler,
            broad_key="plan:broad",
            post_update=_sd_gate_post_update,
            post_compile=_sd_post_compile,
            context_keys=["project", "scope", "prd", "design"],
        )

    async def _rehost_plan_and_sd(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        plan_key: str,
        sd_key: str,
        sf_name: str,
        plan_text: str,
    ) -> None:
        """Re-host both plan and SD after a plan revision.

        Uses ``hosting.push`` (not ``update``) to ensure URLs are registered
        in the hosting service's URL map — ``update`` only writes to disk
        without registering the URL, which causes gate cards to drop the link.
        """
        hosting = runner.services.get("hosting")
        if not hosting:
            return
        await hosting.push(feature.id, plan_key, plan_text, f"Technical Plan — {sf_name}")
        await self._convert_and_host_sd(runner, feature, sd_key, plan_text, sf_name)

    async def _convert_and_host_sd(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        sd_key: str,
        sd_text: str,
        sf_name: str,
    ) -> str:
        """Convert text to structured SystemDesign model, host as HTML, return model JSON.

        Two-pass approach: text → converter produces SystemDesign model via
        constrained decoding → render_system_design_html produces the D3.js
        visualization.  Returns the serialized SystemDesign JSON so callers
        can persist it.

        Also saves the raw source text to a companion ``.md`` file so it
        survives the HTML overwrite by ``hosting.push``.
        """
        if not sd_text:
            return ""

        # Save raw source text before hosting.push overwrites with HTML
        mirror = runner.services.get("artifact_mirror")
        if mirror:
            from ....services.artifacts import _sd_source_path
            source_rel = _sd_source_path(sd_key)
            if source_rel:
                source_path = mirror.feature_dir(feature.id) / source_rel
                source_path.parent.mkdir(parents=True, exist_ok=True)
                source_path.write_text(sd_text, encoding="utf-8")

        hosting = runner.services.get("hosting")

        # Try to parse as existing SystemDesign JSON first
        try:
            data = _json.loads(sd_text)
            sd_model = SystemDesign.model_validate(data)
            if any(getattr(sd_model, f) for f in ("services", "connections", "api_endpoints", "entities")):
                if hosting:
                    html = render_system_design_html(sd_model)
                    await hosting.push(feature.id, sd_key, html, f"System Design — {sf_name}")
                return sd_text  # Already valid JSON
        except Exception:
            pass

        # Convert markdown/text to SystemDesign model via Ask.
        # Uses a dedicated extractor role — NOT compiler_role, whose prompt
        # forbids interpretation and produces empty output from plan text.
        from iriai_compose import Ask
        from iriai_compose.actors import Role as _Role

        sd_extractor_role = _Role(
            name="sd-extractor",
            prompt=(
                "You extract structured system design information from technical "
                "documents. Given a technical plan, architecture doc, or system design "
                "description, populate a SystemDesign object with services, entities, "
                "connections, API endpoints, decisions, and risks. Extract ALL content — "
                "every model/class definition becomes an Entity, every module/package "
                "becomes a ServiceNode, every public function becomes an APIEndpoint."
            ),
            tools=[],
            model="claude-sonnet-4-6",
        )

        try:
            sd_model = await runner.run(
                Ask(
                    actor=AgentActor(name=f"sd-converter-{sd_key}", role=sd_extractor_role),
                    prompt=(
                        "Extract a structured SystemDesign from this document.\n\n"
                        "Map its content to these fields:\n\n"
                        "- **services**: packages, modules, components → ServiceNode "
                        "(id, name, kind=service|database|external|frontend, description, technology)\n"
                        "- **connections**: imports, data flow between services → "
                        "ServiceConnection (from_id, to_id, label, protocol)\n"
                        "- **api_endpoints**: public functions, REST endpoints, CLI commands → "
                        "APIEndpoint (method=GET|POST|PUT|DELETE|PATCH, path, service_id, description)\n"
                        "- **entities**: data models, Pydantic classes, DB tables → "
                        "Entity (id, name, service_id, fields=[EntityField(name, type, constraints)])\n"
                        "- **entity_relations**: references between models → "
                        "EntityRelation (from_entity, to_entity, kind=one-to-many|many-to-many|one-to-one, label)\n"
                        "- **call_paths**: workflows, sequences → APICallPath with APICallStep list\n"
                        "- **decisions**: architecture decisions → list[str]\n"
                        "- **risks**: identified risks → list[str]\n"
                        "- **title**: document title\n"
                        "- **overview**: 2-3 sentence summary\n\n"
                        f"{sd_text}"
                    ),
                    output_type=SystemDesign,
                ),
                feature,
                phase_name=self.name,
            )
            sd_json = sd_model.model_dump_json(indent=2) if isinstance(sd_model, BaseModel) else to_str(sd_model)
            if hosting:
                html = render_system_design_html(sd_model)
                await hosting.push(feature.id, sd_key, html, f"System Design — {sf_name}")
            return sd_json
        except Exception:
            logger.warning("Failed to convert system design %s to HTML", sd_key, exc_info=True)
            # Fall back to hosting the raw text
            if hosting:
                await hosting.push(feature.id, sd_key, sd_text, f"System Design — {sf_name}")
            return sd_text

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
