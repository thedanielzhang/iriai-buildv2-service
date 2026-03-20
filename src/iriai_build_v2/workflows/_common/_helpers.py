from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, TypeVar

from pydantic import BaseModel

from iriai_compose import AgentActor, Ask, Gate, Interview, to_str
from iriai_compose.actors import Actor, Role

if TYPE_CHECKING:
    from iriai_compose import Feature, WorkflowRunner

    from ...models.outputs import (
        IntegrationReview,
        ReviewOutcome,
        SubfeatureDecomposition,
    )

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


async def get_existing_artifact(
    runner: WorkflowRunner,
    feature: Feature,
    artifact_key: str,
) -> str | None:
    """Check DB store first, then fall back to filesystem mirror.

    Artifacts are written to disk by ``hosting.push()`` during interviews
    but only saved to the DB after ``gate_and_revise`` completes.  If the
    workflow was interrupted mid-gate, the artifact exists on disk but not
    in the DB.
    """
    # 1. Try the DB artifact store
    text = await runner.artifacts.get(artifact_key, feature=feature)
    if text:
        return text

    # 2. Fall back to filesystem mirror
    mirror = runner.services.get("artifact_mirror")
    if not mirror:
        return None

    from ...services.artifacts import _key_to_path

    rel_path = _key_to_path(artifact_key)
    path = mirror.feature_dir(feature.id) / rel_path
    if not path.exists():
        return None

    content = path.read_text(encoding="utf-8").strip()
    return content if content else None


def _revision_done(result: Any, output_type: type) -> bool:
    """Check if a revision Interview produced a complete artifact.

    Returns True if the result has the ``complete`` flag set, or if the
    model has any substantive content (non-empty string/list fields).
    This allows the agent to ask clarifying questions before producing
    the final output.
    """
    if hasattr(result, "complete") and result.complete:
        return True
    # Check if the model has any actual content
    if isinstance(result, BaseModel):
        for name in result.model_fields:
            if name == "complete":
                continue
            value = getattr(result, name)
            if isinstance(value, str) and value:
                return True
            if isinstance(value, list) and value:
                return True
    return False


async def gate_and_revise(
    runner: WorkflowRunner,
    feature: Feature,
    phase_name: str,
    *,
    artifact: BaseModel | str,
    actor: Actor,
    output_type: type[T],
    approver: Actor,
    label: str,
    artifact_key: str | None = None,
    annotation_keys: list[str] | None = None,
) -> tuple[T | BaseModel | str, str]:
    """Approve/revise loop. Returns ``(artifact, artifact_text)``.

    Presents *artifact* to *approver* via a Gate.  On rejection the *actor*
    is asked to revise using the feedback.  When *artifact_key* is provided,
    browser annotations are collected from the hosting service automatically.
    """
    artifact_text = to_str(artifact) if isinstance(artifact, BaseModel) else artifact
    artifact_name = label.split("\n", 1)[0]

    # Strip pre-embedded URLs so we can rebuild them fresh each iteration
    clean_label = "\n".join(
        line for line in label.splitlines()
        if "Review in browser:" not in line
    ).strip()

    # Keys to look up URLs for (annotation_keys includes mockup, etc.)
    url_keys = annotation_keys or ([artifact_key] if artifact_key else [])

    while True:
        # Rebuild review URLs from hosting each iteration (ports may change after update)
        gate_label = clean_label
        hosting = runner.services.get("hosting")
        if hosting and url_keys:
            urls = [hosting.get_url(k) for k in url_keys]
            urls = [u for u in urls if u]
            if urls:
                gate_label += "\nReview in browser: " + " | ".join(urls)

        approved = await runner.run(
            Gate(approver=approver, prompt=f"{gate_label}:\n\n{artifact_text}\n\nApprove?"),
            feature,
            phase_name=phase_name,
        )
        if approved is True:
            break

        feedback = str(approved) if isinstance(approved, str) else "Please revise."

        # Collect browser annotations AFTER rejection — the user annotates
        # while reviewing, then clicks reject.
        collect_keys = annotation_keys or ([artifact_key] if artifact_key else [])
        if collect_keys:
            hosting = runner.services.get("hosting")
            if hosting:
                all_annotations: list[dict] = []
                for ck in collect_keys:
                    try:
                        anns = await hosting.try_collect(ck)
                        logger.warning("[diag] gate_and_revise: collected %d annotations for %r", len(anns), ck)
                        all_annotations.extend(anns)
                    except Exception:
                        logger.warning("[diag] gate_and_revise: try_collect(%r) raised", ck, exc_info=True)
                if all_annotations:
                    ann_lines = [
                        f"- [{a['data'].get('selected_text', '')}] {a['comment']}"
                        for a in all_annotations if a.get("comment")
                    ]
                    if ann_lines:
                        feedback += "\n\nReviewer annotations:\n" + "\n".join(ann_lines)

                # Clear annotations so they don't carry over to the next iteration
                for ck in collect_keys:
                    try:
                        await hosting.clear_feedback(ck)
                    except Exception:
                        logger.debug("Failed to clear feedback for %r", ck)

        revision_prompt = (
            f"Here is the current {artifact_name.lower()}:\n\n"
            f"{artifact_text}\n\n"
            f"---\n\n"
            f"Revise the COMPLETE {artifact_name.lower()} based on this feedback. "
            f"Output the full document with all sections, not just the changes:\n\n"
            f"{feedback}"
        )
        artifact = await runner.run(
            Interview(
                questioner=actor,
                responder=approver,
                initial_prompt=revision_prompt,
                output_type=output_type,
                done=lambda result: _revision_done(result, output_type),
            ),
            feature,
            phase_name=phase_name,
        )
        artifact_text = to_str(artifact)

        # Update the hosted doc so the browser shows the revised version
        if artifact_key:
            hosting = runner.services.get("hosting")
            if hosting:
                await hosting.update(feature.id, artifact_key, artifact_text)

    return artifact, artifact_text


# ── Subfeature decomposition helpers ─────────────────────────────────────────


async def broad_interview(
    runner: WorkflowRunner,
    feature: Feature,
    phase_name: str,
    *,
    lead_actor: Actor,
    output_type: type[T],
    artifact_key: str,
    artifact_label: str,
    initial_prompt: str,
) -> tuple[T, str]:
    """Run the broad requirements/design/architecture interview.

    Checks for an existing artifact first (resume-safe).
    Returns ``(artifact, artifact_text)``.
    """
    from ...models.outputs import Envelope, envelope_done
    from .._common import HostedInterview

    existing = await get_existing_artifact(runner, feature, artifact_key)
    if existing:
        logger.info("Broad artifact %s exists — skipping interview", artifact_key)
        import json as _json
        try:
            artifact = output_type.model_validate(_json.loads(existing))
        except Exception:
            return existing, existing  # type: ignore[return-value]
        hosting = runner.services.get("hosting")
        if hosting:
            await hosting.push(feature.id, artifact_key, existing, f"{artifact_label} — {feature.name}")
        return artifact, existing

    envelope = await runner.run(
        HostedInterview(
            questioner=lead_actor,
            responder=_get_user(),
            initial_prompt=initial_prompt,
            output_type=Envelope[output_type],
            done=envelope_done,
            artifact_key=artifact_key,
            artifact_label=artifact_label,
        ),
        feature,
        phase_name=phase_name,
    )
    artifact = envelope.output
    artifact_text = to_str(artifact)
    await runner.artifacts.put(artifact_key, artifact_text, feature=feature)
    return artifact, artifact_text


async def decompose_and_gate(
    runner: WorkflowRunner,
    feature: Feature,
    phase_name: str,
    *,
    lead_actor: Actor,
    approver: Actor,
    broad_artifact_key: str,
) -> SubfeatureDecomposition:
    """Run decomposition interview, gate with user, store and return approved decomposition."""
    from ...models.outputs import Envelope, SubfeatureDecomposition, envelope_done
    from .._common import HostedInterview

    existing = await get_existing_artifact(runner, feature, "decomposition")
    if existing:
        logger.info("Decomposition exists — skipping")
        import json as _json
        return SubfeatureDecomposition.model_validate(_json.loads(existing))

    envelope = await runner.run(
        HostedInterview(
            questioner=lead_actor,
            responder=approver,
            initial_prompt=(
                "Based on the broad requirements, I need to decompose this feature into "
                "subfeatures. Each subfeature will get its own dedicated PM interview. "
                "Let me ask some questions about how to split this up."
            ),
            output_type=Envelope[SubfeatureDecomposition],
            done=envelope_done,
            artifact_key="decomposition",
            artifact_label="Subfeature Decomposition",
        ),
        feature,
        phase_name=phase_name,
    )
    decomposition = envelope.output

    # Gate the decomposition
    decomp_text = to_str(decomposition)
    approved = await runner.run(
        Gate(approver=approver, prompt=f"Subfeature Decomposition:\n\n{decomp_text}\n\nApprove this decomposition?"),
        feature,
        phase_name=phase_name,
    )
    if approved is not True:
        # Re-run with feedback
        feedback = str(approved) if isinstance(approved, str) else "Please revise."
        envelope = await runner.run(
            HostedInterview(
                questioner=lead_actor,
                responder=approver,
                initial_prompt=f"Please revise the decomposition based on this feedback:\n\n{feedback}\n\nCurrent decomposition:\n{decomp_text}",
                output_type=Envelope[SubfeatureDecomposition],
                done=envelope_done,
                artifact_key="decomposition",
                artifact_label="Subfeature Decomposition",
            ),
            feature,
            phase_name=phase_name,
        )
        decomposition = envelope.output

    decomp_text = to_str(decomposition)
    await runner.artifacts.put("decomposition", decomp_text, feature=feature)
    return decomposition


def _build_subfeature_context(
    decomposition: SubfeatureDecomposition,
    current_slug: str,
    completed_artifacts: dict[str, str],
    completed_summaries: dict[str, str],
    broad_text: str,
    decomposition_text: str,
) -> str:
    """Build tiered context for a subfeature agent. Inline, no separate class.

    Tier 1: broad artifact + decomposition (always full text)
    Tier 2: full text for edge-connected completed subfeatures
    Tier 3: summary for unconnected completed subfeatures
    """
    sections: list[str] = []

    # Tier 1: always full
    if broad_text:
        sections.append(f"## Broad Artifact\n\n{broad_text}")
    if decomposition_text:
        sections.append(f"## Decomposition\n\n{decomposition_text}")

    # Determine connected slugs via edges
    connected_slugs = {
        e.to_subfeature if e.from_subfeature == current_slug else e.from_subfeature
        for e in decomposition.edges
        if current_slug in (e.from_subfeature, e.to_subfeature)
    } & set(completed_artifacts.keys())

    unconnected_slugs = set(completed_artifacts.keys()) - connected_slugs

    # Tier 2: full text for connected
    for slug in sorted(connected_slugs):
        text = completed_artifacts.get(slug)
        if text:
            sections.append(f"## Subfeature: {slug} (connected — full text)\n\n{text}")

    # Tier 3: summary for unconnected
    for slug in sorted(unconnected_slugs):
        summary = completed_summaries.get(slug)
        if summary:
            sections.append(f"## Subfeature: {slug} (summary)\n\n{summary}")

    return "\n\n---\n\n".join(sections)


async def generate_summary(
    runner: WorkflowRunner,
    feature: Feature,
    artifact_prefix: str,
    sf_slug: str,
) -> str:
    """Generate a Tier 3 summary of a subfeature artifact and store it."""
    from ...roles import summarizer_role

    full_text = await runner.artifacts.get(f"{artifact_prefix}:{sf_slug}", feature=feature)
    if not full_text:
        return ""

    summary = await runner.run(
        Ask(
            actor=AgentActor(name=f"summarizer-{sf_slug}", role=summarizer_role),
            prompt=(
                f"Summarize this {artifact_prefix} document. Include:\n"
                "- Title and overview (1-2 sentences)\n"
                "- All requirement IDs (REQ-*) with one-line descriptions\n"
                "- All journey IDs (J-*) with one-line descriptions\n"
                "- All edge/interface descriptions to other subfeatures\n"
                "- All data entity names and key fields\n"
                "Do NOT include full text of requirements, journeys, or acceptance criteria.\n\n"
                f"{full_text}"
            ),
        ),
        feature,
    )
    summary_text = to_str(summary)
    await runner.artifacts.put(f"{artifact_prefix}-summary:{sf_slug}", summary_text, feature=feature)
    return summary_text


async def per_subfeature_loop(
    runner: WorkflowRunner,
    feature: Feature,
    phase_name: str,
    *,
    decomposition: SubfeatureDecomposition,
    base_role: Role,
    output_type: type[T],
    artifact_prefix: str,
    broad_key: str,
    make_prompt: Any,  # Callable[[Subfeature, str], str]
) -> dict[str, str]:
    """Sequential loop: for each subfeature, interview user, gate, store artifact.

    ``make_prompt(subfeature, context)`` builds the initial interview prompt
    for each subfeature agent.

    Returns ``{slug: artifact_text}`` for all completed subfeatures.
    """
    from ...models.outputs import Envelope, envelope_done
    from ...roles import InterviewActor
    from .._common import HostedInterview

    approver = _get_user()
    completed_artifacts: dict[str, str] = {}
    completed_summaries: dict[str, str] = {}

    broad_text = await runner.artifacts.get(broad_key, feature=feature) or ""
    decomp_text = await runner.artifacts.get("decomposition", feature=feature) or ""

    for sf in decomposition.subfeatures:
        sf_key = f"{artifact_prefix}:{sf.slug}"

        # Resume check
        existing = await get_existing_artifact(runner, feature, sf_key)
        if existing:
            logger.info("Subfeature artifact %s exists — skipping", sf_key)
            completed_artifacts[sf.slug] = existing
            # Load summary too if it exists
            summary = await runner.artifacts.get(f"{artifact_prefix}-summary:{sf.slug}", feature=feature)
            if summary:
                completed_summaries[sf.slug] = summary
            continue

        # Build tiered context
        context = _build_subfeature_context(
            decomposition, sf.slug,
            completed_artifacts, completed_summaries,
            broad_text, decomp_text,
        )

        prompt = make_prompt(sf, context)

        # Create dedicated actor for this subfeature
        sf_actor = InterviewActor(
            name=f"{artifact_prefix}-sf-{sf.slug}",
            role=base_role,
            context_keys=["project", "scope"],
        )

        envelope = await runner.run(
            HostedInterview(
                questioner=sf_actor,
                responder=approver,
                initial_prompt=prompt,
                output_type=Envelope[output_type],
                done=envelope_done,
                artifact_key=sf_key,
                artifact_label=f"{artifact_prefix.upper()} — {sf.name}",
            ),
            feature,
            phase_name=phase_name,
        )

        sf_artifact = envelope.output
        sf_text = to_str(sf_artifact)

        # Gate this subfeature's artifact
        sf_artifact, sf_text = await gate_and_revise(
            runner, feature, phase_name,
            artifact=sf_artifact, actor=sf_actor, output_type=output_type,
            approver=approver, label=f"{artifact_prefix.upper()} — {sf.name}",
            artifact_key=sf_key,
        )
        sf_text = to_str(sf_artifact) if isinstance(sf_artifact, BaseModel) else sf_text

        await runner.artifacts.put(sf_key, sf_text, feature=feature)
        completed_artifacts[sf.slug] = sf_text

        # Generate Tier 3 summary
        summary = await generate_summary(runner, feature, artifact_prefix, sf.slug)
        if summary:
            completed_summaries[sf.slug] = summary

    return completed_artifacts


async def integration_review(
    runner: WorkflowRunner,
    feature: Feature,
    phase_name: str,
    *,
    lead_actor: Actor,
    decomposition: SubfeatureDecomposition,
    artifact_prefix: str,
    broad_key: str,
) -> IntegrationReview:
    """Run lead's integration review interview.

    The lead asks the user clarifying questions about cross-subfeature
    consistency, edge contracts, gaps, and contradictions.
    """
    from ...models.outputs import Envelope, IntegrationReview, envelope_done
    from .._common import HostedInterview

    review_key = f"integration-review:{phase_name}"
    existing = await get_existing_artifact(runner, feature, review_key)
    if existing:
        logger.info("Integration review %s exists — skipping", review_key)
        import json as _json
        return IntegrationReview.model_validate(_json.loads(existing))

    # Build context: broad + decomposition + all subfeature artifacts
    context_parts = []
    broad_text = await runner.artifacts.get(broad_key, feature=feature)
    if broad_text:
        context_parts.append(f"## Broad Artifact\n\n{broad_text}")
    decomp_text = await runner.artifacts.get("decomposition", feature=feature)
    if decomp_text:
        context_parts.append(f"## Decomposition\n\n{decomp_text}")
    for sf in decomposition.subfeatures:
        sf_text = await runner.artifacts.get(f"{artifact_prefix}:{sf.slug}", feature=feature)
        if sf_text:
            context_parts.append(f"## {artifact_prefix}:{sf.slug}\n\n{sf_text}")

    context = "\n\n---\n\n".join(context_parts)

    envelope = await runner.run(
        HostedInterview(
            questioner=lead_actor,
            responder=_get_user(),
            initial_prompt=(
                f"I've reviewed all {len(decomposition.subfeatures)} subfeature artifacts. "
                "Let me walk through the cross-subfeature integration points and check for "
                "consistency. I may have some questions.\n\n"
                f"{context}"
            ),
            output_type=Envelope[IntegrationReview],
            done=envelope_done,
            artifact_key=review_key,
            artifact_label=f"Integration Review — {phase_name}",
        ),
        feature,
        phase_name=phase_name,
    )

    review = envelope.output
    review_text = to_str(review)
    await runner.artifacts.put(review_key, review_text, feature=feature)
    return review


async def compile_artifacts(
    runner: WorkflowRunner,
    feature: Feature,
    phase_name: str,
    *,
    compiler_actor: Actor,
    decomposition: SubfeatureDecomposition,
    artifact_prefix: str,
    broad_key: str,
    output_type: type[T],
    final_key: str,
) -> tuple[T, str]:
    """Compile per-subfeature artifacts into a single final artifact.

    The compiler receives broad + decomposition + all per-subfeature artifacts
    and produces a unified document stored under ``final_key``.
    """
    # Build compiler prompt with all sources
    parts = []
    broad_text = await runner.artifacts.get(broad_key, feature=feature)
    if broad_text:
        parts.append(f"## Broad Artifact ({broad_key})\n\n{broad_text}")
    decomp_text = await runner.artifacts.get("decomposition", feature=feature)
    if decomp_text:
        parts.append(f"## Decomposition\n\n{decomp_text}")
    for sf in decomposition.subfeatures:
        sf_text = await runner.artifacts.get(f"{artifact_prefix}:{sf.slug}", feature=feature)
        if sf_text:
            parts.append(f"## Subfeature: {sf.name} ({sf.slug})\n\n{sf_text}")

    source_text = "\n\n---\n\n".join(parts)

    compiled = await runner.run(
        Ask(
            actor=compiler_actor,
            prompt=(
                f"Compile the following {len(decomposition.subfeatures)} subfeature "
                f"{artifact_prefix} artifacts into a single unified document.\n\n"
                "Rules:\n"
                "- Preserve ALL detail from every subfeature\n"
                "- Re-number IDs globally (REQ-1 through REQ-N, etc.)\n"
                "- Preserve all citations\n"
                "- Add subfeature provenance markers\n"
                "- Merge overlapping content, keeping all fields\n\n"
                f"{source_text}"
            ),
            output_type=output_type,
        ),
        feature,
        phase_name=phase_name,
    )

    compiled_text = to_str(compiled)
    await runner.artifacts.put(final_key, compiled_text, feature=feature)

    # Host the compiled artifact
    hosting = runner.services.get("hosting")
    if hosting:
        await hosting.push(feature.id, final_key, compiled_text, f"Compiled {artifact_prefix.upper()} — {feature.name}")

    return compiled, compiled_text


async def interview_gate_review(
    runner: WorkflowRunner,
    feature: Feature,
    phase_name: str,
    *,
    lead_actor: Actor,
    decomposition: SubfeatureDecomposition,
    artifact_prefix: str,
    compiled_key: str,
    base_role: Role,
    output_type: type[T],
    compiler_actor: Actor,
    broad_key: str,
) -> str:
    """Interview-based gate review. Replaces gate_and_revise for compiled artifacts.

    Flow:
    1. Lead interviews user: "Is there anything you'd like changed?"
    2. If changes requested: produce RevisionPlan, route to affected subfeature agents
    3. Re-compile and re-present
    4. Loop until user approves
    """
    from ...models.outputs import Envelope, ReviewOutcome, envelope_done

    from .._common import HostedInterview

    compiled_text = await runner.artifacts.get(compiled_key, feature=feature) or ""

    while True:
        hosting = runner.services.get("hosting")
        review_url = hosting.get_url(compiled_key) if hosting else ""
        url_note = f"\nReview in browser: {review_url}" if review_url else ""

        envelope = await runner.run(
            HostedInterview(
                questioner=lead_actor,
                responder=_get_user(),
                initial_prompt=(
                    f"I've compiled the {artifact_prefix} from all subfeatures. "
                    f"Please review it and let me know if there is anything you'd like changed.{url_note}\n\n"
                    f"Summary of compiled artifact:\n{compiled_text[:3000]}"
                ),
                output_type=Envelope[ReviewOutcome],
                done=envelope_done,
                artifact_key=compiled_key,
                artifact_label=f"Gate Review — {artifact_prefix.upper()}",
            ),
            feature,
            phase_name=phase_name,
        )

        outcome: ReviewOutcome = envelope.output

        if outcome.approved:
            break

        # Execute targeted revisions
        await targeted_revision(
            runner, feature, phase_name,
            revision_plan=outcome.revision_plan,
            decomposition=decomposition,
            base_role=base_role,
            output_type=output_type,
            artifact_prefix=artifact_prefix,
        )

        # Re-compile
        _, compiled_text = await compile_artifacts(
            runner, feature, phase_name,
            compiler_actor=compiler_actor,
            decomposition=decomposition,
            artifact_prefix=artifact_prefix,
            broad_key=broad_key,
            output_type=output_type,
            final_key=compiled_key,
        )

    return compiled_text


async def targeted_revision(
    runner: WorkflowRunner,
    feature: Feature,
    phase_name: str,
    *,
    revision_plan: Any,  # RevisionPlan
    decomposition: SubfeatureDecomposition,
    base_role: Role,
    output_type: type[T],
    artifact_prefix: str,
) -> None:
    """Execute revisions on specific subfeatures per the RevisionPlan.

    Re-runs affected subfeature agents with revision instructions.
    Updates subfeature artifacts in store. Regenerates summaries.
    """
    from ...models.outputs import Envelope, envelope_done
    from ...roles import InterviewActor
    from .._common import HostedInterview

    approver = _get_user()

    for request in revision_plan.requests:
        for sf_slug in request.affected_subfeatures:
            sf_key = f"{artifact_prefix}:{sf_slug}"
            existing = await runner.artifacts.get(sf_key, feature=feature) or ""

            revision_actor = InterviewActor(
                name=f"{artifact_prefix}-sf-{sf_slug}-rev",
                role=base_role,
                context_keys=["project", "scope"],
            )

            envelope = await runner.run(
                HostedInterview(
                    questioner=revision_actor,
                    responder=approver,
                    initial_prompt=(
                        f"Please revise the {artifact_prefix} for subfeature '{sf_slug}' "
                        f"based on this feedback:\n\n"
                        f"**Change requested:** {request.description}\n"
                        f"**Reasoning:** {request.reasoning}\n\n"
                        f"Current artifact:\n{existing}"
                    ),
                    output_type=Envelope[output_type],
                    done=envelope_done,
                    artifact_key=sf_key,
                    artifact_label=f"Revision — {sf_slug}",
                ),
                feature,
                phase_name=phase_name,
            )

            revised_text = to_str(envelope.output)
            await runner.artifacts.put(sf_key, revised_text, feature=feature)

            # Regenerate summary
            await generate_summary(runner, feature, artifact_prefix, sf_slug)


def _get_user() -> Actor:
    """Lazy import of the user actor to avoid circular imports."""
    from ...roles import user
    return user
