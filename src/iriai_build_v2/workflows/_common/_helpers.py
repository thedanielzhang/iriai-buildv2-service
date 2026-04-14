from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
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

# ── Prompt offloading ────────────────────────────────────────────────────────

PROMPT_FILE_THRESHOLD = 100_000  # chars — offload to files above this


def _offload_if_large(
    prompt: str,
    context_base: Path | None,
    label: str,
) -> str:
    """Write *prompt* to a file if it exceeds the threshold, returning a
    compact Read-pointer prompt.  If the prompt is small enough or there
    is no writable *context_base*, returns *prompt* unchanged.
    """
    if len(prompt) <= PROMPT_FILE_THRESHOLD or context_base is None:
        return prompt
    context_dir = context_base / ".iriai-context"
    context_dir.mkdir(parents=True, exist_ok=True)
    file_name = f"{label}.md"
    file_path = context_dir / file_name
    file_path.write_text(prompt, encoding="utf-8")
    rel_path = f".iriai-context/{file_name}"
    logger.info(
        "Prompt offloaded to %s (%d chars)", rel_path, len(prompt),
    )
    return (
        f"Your full task prompt is in `{rel_path}` ({len(prompt)} chars).\n"
        f"**Read that file** before proceeding."
    )


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
    post_update: Callable[[str, str], Awaitable[None]] | None = None,
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

        # Resolve artifact file path so the agent can write revisions to disk
        artifact_path = None
        if artifact_key:
            mirror = runner.services.get("artifact_mirror")
            if mirror:
                from ...services.artifacts import _key_to_path
                artifact_path = mirror.feature_dir(feature.id) / _key_to_path(artifact_key)

        from ...models.outputs import Envelope, envelope_done

        revision_prompt = (
            f"Here is the current {artifact_name.lower()}:\n\n"
            f"{artifact_text}\n\n"
            f"---\n\n"
            f"Revise the {artifact_name.lower()} based on this feedback. "
            f"Ask clarifying questions if the feedback is ambiguous. "
            f"Output the full document with all sections, not just the changes:\n\n"
            f"{feedback}"
        )
        if artifact_path:
            revision_prompt += (
                f"\n\nWrite the revised artifact to: `{artifact_path}`\n"
                f"Then set `complete = true` in the structured output."
            )

        envelope = await runner.run(
            Interview(
                questioner=actor,
                responder=approver,
                initial_prompt=revision_prompt,
                output_type=Envelope[output_type],
                done=envelope_done,
            ),
            feature,
            phase_name=phase_name,
        )

        artifact = envelope.output if isinstance(envelope, Envelope) and envelope.output else envelope

        # Prefer file content over to_str(BaseModel) JSON
        artifact_text = to_str(artifact)
        if artifact_path and artifact_path.exists():
            file_text = artifact_path.read_text(encoding="utf-8").strip()
            if file_text:
                artifact_text = file_text
                artifact = file_text

        # Update the hosted doc so the browser shows the revised version
        if artifact_key:
            hosting = runner.services.get("hosting")
            if hosting:
                await hosting.update(feature.id, artifact_key, artifact_text)
            if post_update:
                await post_update(artifact_key, artifact_text)

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
    context_keys: list[str] | None = None,
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
    _keys = context_keys if context_keys is not None else ["project", "scope"]

    broad_text = await runner.artifacts.get(broad_key, feature=feature) or ""
    decomp_text = await runner.artifacts.get("decomposition", feature=feature) or ""

    for sf in decomposition.subfeatures:
        sf_key = f"{artifact_prefix}:{sf.slug}"

        # Resume check: DB = approved (artifacts.put only happens after gate)
        approved_text = await runner.artifacts.get(sf_key, feature=feature)
        if approved_text:
            logger.info("Subfeature artifact %s approved — skipping", sf_key)
            completed_artifacts[sf.slug] = approved_text
            summary = await runner.artifacts.get(f"{artifact_prefix}-summary:{sf.slug}", feature=feature)
            if summary:
                completed_summaries[sf.slug] = summary
            continue

        # Draft check: file exists on disk but not approved (agent wrote it, gate not done)
        draft_text = await get_existing_artifact(runner, feature, sf_key)
        if draft_text:
            logger.info("Subfeature artifact %s exists as draft — running gate", sf_key)
            sf_actor = InterviewActor(
                name=f"{artifact_prefix}-sf-{sf.slug}",
                role=base_role,
                context_keys=_keys,
            )
            # Host the draft so the gate card has a review URL
            hosting = runner.services.get("hosting")
            if hosting:
                await hosting.push(
                    feature.id, sf_key, draft_text,
                    f"{artifact_prefix.upper()} — {sf.name}",
                )
            sf_artifact, sf_text = await gate_and_revise(
                runner, feature, phase_name,
                artifact=draft_text, actor=sf_actor, output_type=output_type,
                approver=approver, label=f"{artifact_prefix.upper()} — {sf.name}",
                artifact_key=sf_key,
            )
            sf_text = to_str(sf_artifact) if isinstance(sf_artifact, BaseModel) else sf_text
            await runner.artifacts.put(sf_key, sf_text, feature=feature)
            completed_artifacts[sf.slug] = sf_text
            summary = await generate_summary(runner, feature, artifact_prefix, sf.slug)
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
            context_keys=_keys,
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

        # Check if agent wrote artifact to disk (preferred over Envelope output)
        sf_text = None
        mirror = runner.services.get("artifact_mirror")
        if mirror:
            from ...services.artifacts import _key_to_path

            path = mirror.feature_dir(feature.id) / _key_to_path(sf_key)
            if path.exists():
                sf_text = path.read_text(encoding="utf-8").strip()

        if not sf_text:
            sf_artifact = envelope.output
            sf_text = to_str(sf_artifact)
            # Validate the Envelope output has actual content — fail fast
            # if the agent set complete=true but didn't write a file or
            # populate the structured output fields.
            if sf_artifact is not None:
                from .._common._tasks import _has_content

                if not _has_content(sf_artifact):
                    raise RuntimeError(
                        f"Agent set complete=true for '{sf_key}' but produced "
                        f"no content. The agent must write the artifact to a "
                        f"file OR populate the structured output fields."
                    )

        # Gate this subfeature's artifact
        sf_artifact, sf_text = await gate_and_revise(
            runner, feature, phase_name,
            artifact=sf_text, actor=sf_actor, output_type=output_type,
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

    Guarantees: when ``needs_revision`` is True, ``revision_instructions``
    is a non-empty dict with valid subfeature slugs.  If the agent's structured
    output is incomplete, a follow-up extraction call fills in the gap.
    """
    from ...models.outputs import Envelope, IntegrationReview, envelope_done
    from .._common import HostedInterview

    review_key = f"integration-review:{phase_name}"
    sf_slugs = [sf.slug for sf in decomposition.subfeatures]

    existing = await get_existing_artifact(runner, feature, review_key)
    if existing:
        logger.info("Integration review %s exists — checking cached data", review_key)
        import json as _json
        try:
            review = IntegrationReview.model_validate(_json.loads(existing))
        except Exception:
            # Stored artifact is not valid JSON (e.g. markdown from file) —
            # fall through to re-run the interview.
            logger.warning(
                "integration_review: cached artifact for %s is not valid "
                "IntegrationReview JSON — will re-run", review_key,
            )
            review = None

        if review is not None:
            _normalize_review_slugs(review, sf_slugs)
            needs_extraction = (
                review.needs_revision
                and not review.revision_instructions
            )
            if needs_extraction:
                logger.warning(
                    "integration_review: cached review needs revision "
                    "but has no usable revision_instructions — extracting"
                )
                review_file_text = _read_artifact_file(runner, feature, review_key)
                if review_file_text:
                    extracted = await _extract_review_fields(
                        runner, feature, phase_name, review_file_text, sf_slugs,
                    )
                    if extracted.revision_instructions:
                        review.revision_instructions = extracted.revision_instructions
                        logger.info(
                            "integration_review: extracted revision_instructions "
                            "for %d subfeatures from cached review",
                            len(review.revision_instructions),
                        )
                # Persist the normalized/updated review
                review_text = to_str(review)
                await runner.artifacts.put(
                    review_key, review_text, feature=feature,
                )
            return review

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

    # Write context to file so the lead agent can read it
    # (avoids inlining potentially huge content in the prompt)
    from pathlib import Path

    mirror = runner.services.get("artifact_mirror")
    if mirror:
        context_path = Path(mirror.feature_dir(feature.id)) / f"integration-review-sources-{artifact_prefix}.md"
    else:
        import tempfile
        context_path = Path(tempfile.mkdtemp()) / f"integration-review-sources-{artifact_prefix}.md"
    context_path.parent.mkdir(parents=True, exist_ok=True)
    context_path.write_text(context, encoding="utf-8")

    envelope = await runner.run(
        HostedInterview(
            questioner=lead_actor,
            responder=_get_user(),
            initial_prompt=(
                f"I've reviewed all {len(decomposition.subfeatures)} subfeature artifacts. "
                "Let me walk through the cross-subfeature integration points and check for "
                "consistency. I may have some questions.\n\n"
                f"Available subfeature slugs for revision_instructions: "
                f"{', '.join(sf_slugs)}\n\n"
                f"**Read the full context from:** `{context_path}`"
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

    # ── Hardening: ensure structured fields are populated ──
    # The HostedInterview pattern encourages file-based output (output=null).
    # When the agent writes a rich review file but leaves the structured
    # IntegrationReview empty, we extract fields from the file.

    review_file_text = _read_artifact_file(runner, feature, review_key)

    if review is None:
        logger.warning(
            "integration_review: envelope.output is None — "
            "extracting structured fields from review file"
        )
        if review_file_text:
            review = await _extract_review_fields(
                runner, feature, phase_name, review_file_text, sf_slugs,
            )
        else:
            logger.error(
                "integration_review: no envelope output AND no review file — "
                "returning empty review"
            )
            review = IntegrationReview(needs_revision=False)

    _normalize_review_slugs(review, sf_slugs)

    if review.needs_revision and not review.revision_instructions:
        logger.warning(
            "integration_review: needs_revision=True but "
            "revision_instructions is empty — extracting from review file"
        )
        if review_file_text:
            extracted = await _extract_review_fields(
                runner, feature, phase_name, review_file_text, sf_slugs,
            )
            if extracted.revision_instructions:
                _normalize_review(extracted, sf_slugs)
                review.revision_instructions = extracted.revision_instructions
                logger.info(
                    "integration_review: extracted revision_instructions for %d subfeatures",
                    len(review.revision_instructions),
                )
            else:
                logger.error(
                    "integration_review: extraction also produced empty "
                    "revision_instructions — revisions will not run"
                )

    review_text = to_str(review)
    await runner.artifacts.put(review_key, review_text, feature=feature)
    return review


def _normalize_review_slugs(
    review: IntegrationReview,
    sf_slugs: list[str],
) -> None:
    """Normalize revision_instructions keys to valid subfeature slugs.

    Agents sometimes use labels like 'SF-1', 'SF-2' instead of actual slugs.
    This maps ordinal labels to slugs by position, and removes any keys that
    don't match valid slugs.

    Also fixes backward compat: if revision_instructions has content but
    needs_revision is False (old ``verdict`` field missing from new schema),
    set needs_revision to True.
    """
    if not review.revision_instructions:
        return

    valid = set(sf_slugs)
    normalized: dict[str, str] = {}
    removed: list[str] = []

    for key, instruction in review.revision_instructions.items():
        if key in valid:
            normalized[key] = instruction
        else:
            # Try ordinal mapping: SF-1 → sf_slugs[0], SF-2 → sf_slugs[1], etc.
            mapped = False
            for prefix in ("SF-", "sf-", "sf", "SF"):
                if key.startswith(prefix):
                    try:
                        idx = int(key[len(prefix):]) - 1  # 1-based → 0-based
                        if 0 <= idx < len(sf_slugs):
                            normalized[sf_slugs[idx]] = instruction
                            mapped = True
                    except ValueError:
                        pass
                    break
            if not mapped:
                removed.append(key)

    if removed:
        logger.warning(
            "_normalize_review_slugs: removed unmapped keys: %s", removed,
        )
    if normalized != review.revision_instructions:
        logger.info(
            "_normalize_review_slugs: remapped keys → %s",
            list(normalized.keys()),
        )

    review.revision_instructions = normalized

    # Backward compat: old schema used a 'verdict' string field. If
    # revision_instructions is populated but needs_revision is False,
    # the old data had a non-empty verdict that got dropped during parsing.
    if normalized and not review.needs_revision:
        logger.info(
            "_normalize_review_slugs: revision_instructions non-empty but "
            "needs_revision=False — setting to True (backward compat)"
        )
        review.needs_revision = True


def _read_artifact_file(
    runner: WorkflowRunner, feature: Feature, artifact_key: str,
) -> str | None:
    """Read artifact content from the filesystem mirror, if available."""
    from pathlib import Path

    from ...services.artifacts import _key_to_path

    mirror = runner.services.get("artifact_mirror")
    if not mirror:
        return None
    path = Path(mirror.feature_dir(feature.id)) / _key_to_path(artifact_key)
    if path.exists():
        text = path.read_text(encoding="utf-8").strip()
        return text if text else None
    return None


async def _extract_review_fields(
    runner: WorkflowRunner,
    feature: Feature,
    phase_name: str,
    review_text: str,
    sf_slugs: list[str],
) -> IntegrationReview:
    """Extract structured IntegrationReview fields from review prose.

    Uses a lightweight Haiku call with structured output.
    """
    from iriai_compose.actors import Role

    from ...models.outputs import IntegrationReview

    extractor_role = Role(
        name="review-extractor",
        prompt=(
            "You extract structured fields from integration review prose. "
            "Read the review and produce an IntegrationReview with needs_revision (bool), "
            "revision_instructions (dict mapping subfeature slugs to instructions), "
            "contradictions, gaps, and edge_consistency."
        ),
        tools=[],
        model="claude-haiku-4-5-20251001",
        effort="high",
    )

    result = await runner.run(
        Ask(
            actor=AgentActor(name="review-extractor", role=extractor_role),
            prompt=(
                f"Extract structured review fields from this integration review.\n\n"
                f"Available subfeature slugs: {', '.join(sf_slugs)}\n\n"
                f"For revision_instructions: map each subfeature slug that needs "
                f"changes to a specific instruction describing what to change. "
                f"Only use slugs from the list above.\n\n"
                f"If the review identifies contradictions or issues that require "
                f"changes, set needs_revision to true and populate "
                f"revision_instructions.\n\n"
                f"Review:\n{review_text}"
            ),
            output_type=IntegrationReview,
        ),
        feature,
        phase_name=phase_name,
    )

    return result


async def _extract_revision_plan(
    runner: WorkflowRunner,
    feature: Feature,
    phase_name: str,
    review_text: str,
    decomposition: SubfeatureDecomposition,
) -> Any:  # RevisionPlan
    """Extract structured RevisionPlan from gate review prose.

    Uses a lightweight Haiku call with structured output.
    Same pattern as _extract_review_fields for integration reviews.
    """
    from iriai_compose.actors import Role

    from ...models.outputs import RevisionPlan

    sf_slugs = [sf.slug for sf in decomposition.subfeatures]

    extractor_role = Role(
        name="revision-extractor",
        prompt=(
            "You extract structured revision requests from gate review prose. "
            "Read the review and produce a RevisionPlan with a list of "
            "RevisionRequest objects. Each request needs: description (what to change), "
            "reasoning (why), affected_subfeatures (list of slugs), "
            "affected_artifact_types (which artifact types to revise), and "
            "cross_subfeature (true if spans multiple)."
        ),
        tools=[],
        model="claude-haiku-4-5-20251001",
        effort="high",
    )

    result = await runner.run(
        Ask(
            actor=AgentActor(name="revision-extractor", role=extractor_role),
            prompt=(
                f"Extract revision requests from this gate review.\n\n"
                f"Available subfeature slugs: {', '.join(sf_slugs)}\n"
                f"Available artifact types: prd, design, plan, system-design\n\n"
                f"For each revision request, identify:\n"
                f"- description: what needs to change\n"
                f"- reasoning: why (the decision or feedback that prompted it)\n"
                f"- affected_subfeatures: which slugs need updating (from list above)\n"
                f"- affected_artifact_types: which artifact types to revise "
                f"(prd, design, plan, system-design). Look for mentions like "
                f"'plan rewrite', 'design revision', 'system design update'. "
                f"If unclear, leave empty (all types will be revised).\n"
                f"- cross_subfeature: true if the change spans multiple subfeatures\n\n"
                f"Review:\n{review_text}"
            ),
            output_type=RevisionPlan,
        ),
        feature,
        phase_name=phase_name,
    )

    return result


# ── Gate review convergence tracking ──────────────────────────────────────


DEFERRABLE_SEVERITIES = frozenset({"minor", "nit"})  # only these get deferred


def _text_overlap(a: str, b: str) -> float:
    """Word-level Jaccard similarity between two strings."""
    words_a = set(a.lower().split())
    words_b = set(b.lower().split())
    if not words_a or not words_b:
        return 0.0
    return len(words_a & words_b) / len(words_a | words_b)


async def _load_gate_ledger(
    runner: WorkflowRunner, feature: Feature, artifact_prefix: str,
) -> Any:  # GateReviewLedger
    """Load the gate review ledger for an artifact type."""
    from ...models.outputs import GateReviewLedger

    raw = await runner.artifacts.get(
        f"gate-review-ledger:{artifact_prefix}", feature=feature,
    )
    if raw:
        try:
            return GateReviewLedger.model_validate_json(raw)
        except Exception:
            logger.warning("Failed to parse gate review ledger for %s — starting fresh", artifact_prefix)
    return GateReviewLedger()


async def _save_gate_ledger(
    runner: WorkflowRunner, feature: Feature,
    ledger: Any, artifact_prefix: str,
) -> None:
    """Save the gate review ledger."""
    await runner.artifacts.put(
        f"gate-review-ledger:{artifact_prefix}",
        ledger.model_dump_json(), feature=feature,
    )


def _dedup_revision_requests(
    plan: Any, ledger: Any, source: str,
) -> tuple[Any, list]:  # (RevisionPlan, list[GateReviewFinding])
    """Remove revision requests that match resolved ledger entries.

    Returns (filtered_plan, suppressed_findings).
    """
    resolved = [
        f for f in ledger.findings
        if f.status == "resolved" and f.source == source
    ]
    if not resolved:
        return plan, []

    new_requests = []
    suppressed = []
    for req in plan.requests:
        is_dup = False
        for r in resolved:
            if _text_overlap(req.description, r.description) > 0.5:
                is_dup = True
                suppressed.append(r)
                break
        if not is_dup:
            new_requests.append(req)

    filtered = plan.model_copy(update={"requests": new_requests})
    return filtered, suppressed


def _update_gate_ledger(
    ledger: Any, plan: Any, source: str, cycle: int,
) -> Any:  # GateReviewLedger
    """Update ledger: mark resolved findings, add new ones, track attempts.

    Mirrors implementation.py _update_ledger() logic.
    """
    from ...models.outputs import GateReviewFinding

    current_descs = {r.description for r in plan.requests}

    # Mark previously-open findings as resolved if absent from current plan
    for f in ledger.findings:
        if f.source == source and f.status in ("open", "fix_attempted"):
            if not any(_text_overlap(f.description, d) > 0.5 for d in current_descs):
                f.status = "resolved"
                f.cycle_resolved = cycle

    # Track attempts on existing open findings, add new findings
    existing_descs = {f.description for f in ledger.findings}
    next_id = len(ledger.findings) + 1

    for req in plan.requests:
        # Check if this matches an existing open finding
        matched = False
        for f in ledger.findings:
            if f.source == source and f.status in ("open", "fix_attempted") and \
               _text_overlap(req.description, f.description) > 0.5:
                f.status = "fix_attempted"
                f.revision_attempts.append(f"cycle-{cycle}: {req.description}")
                matched = True
                break

        if not matched and req.description not in existing_descs:
            ledger.findings.append(GateReviewFinding(
                id=f"GF-{next_id:03d}",
                source=source,
                description=req.description,
                reasoning=req.reasoning,
                affected_subfeatures=req.affected_subfeatures,
                severity=req.severity,
                status="open",
                cycle_introduced=cycle,
            ))
            next_id += 1

    ledger.cycle = cycle
    return ledger


async def _classify_revision_severity(
    runner: WorkflowRunner, feature: Feature,
    phase_name: str, plan: Any,
) -> Any:  # RevisionPlan
    """Classify severity of revision requests via Haiku.

    Only classifies requests where severity is empty.
    """
    unclassified = [r for r in plan.requests if not r.severity]
    if not unclassified:
        return plan

    descriptions = "\n".join(
        f"{i+1}. {r.description}" for i, r in enumerate(unclassified)
    )

    from iriai_compose.actors import Role

    classifier_role = Role(
        name="severity-classifier",
        prompt=(
            "You classify revision requests by severity. "
            "blocker = factual error, missing requirement, spec contradiction. "
            "major = significant gap, unclear spec, structural issue. "
            "minor = style, formatting, wording improvement. "
            "nit = cosmetic, optional preference."
        ),
        tools=[],
        model="claude-haiku-4-5-20251001",
        effort="low",
    )

    from ...models.outputs import SeverityClassification

    try:
        result = await runner.run(
            Ask(
                actor=AgentActor(name="severity-classifier", role=classifier_role),
                prompt=(
                    f"Classify each revision request as blocker, major, minor, or nit.\n\n"
                    f"{descriptions}\n\n"
                    f"Return a list of severity strings, one per request, in the same order."
                ),
                output_type=SeverityClassification,
            ),
            feature,
            phase_name=phase_name,
        )
        severities = result.severities if hasattr(result, "severities") else []
    except Exception:
        logger.warning("Severity classification failed — defaulting to blocker", exc_info=True)
        severities = []

    # Apply classifications back to the unclassified requests
    for i, req in enumerate(unclassified):
        if i < len(severities) and severities[i] in ("blocker", "major", "minor", "nit"):
            req.severity = severities[i]
        else:
            req.severity = "blocker"  # conservative default

    return plan


def _partition_revision_plan(
    plan: Any, source: str,
) -> tuple[Any, list]:  # (RevisionPlan, list[RevisionRequest])
    """Split revision plan into blocking and deferred requests.

    Only ``minor`` and ``nit`` are deferred.  Everything else — including
    unknown/non-standard severity values — is treated as blocking.
    """
    deferred = [r for r in plan.requests if r.severity in DEFERRABLE_SEVERITIES]
    blocking = [r for r in plan.requests if r.severity not in DEFERRABLE_SEVERITIES]

    filtered = plan.model_copy(update={"requests": blocking})
    return filtered, deferred


async def _append_gate_enhancements(
    runner: WorkflowRunner, feature: Feature,
    items: list, artifact_prefix: str,
) -> None:
    """Append deferred revision requests to the gate enhancement backlog."""
    if not items:
        return

    from ...models.outputs import EnhancementBacklog, EnhancementItem

    key = f"gate-enhancement-backlog:{artifact_prefix}"
    raw = await runner.artifacts.get(key, feature=feature)
    if raw:
        try:
            backlog = EnhancementBacklog.model_validate_json(raw)
        except Exception:
            backlog = EnhancementBacklog()
    else:
        backlog = EnhancementBacklog()

    existing_descs = [i.description for i in backlog.items]
    new_items = []
    for req in items:
        if req.description in existing_descs:
            continue
        if any(_text_overlap(req.description, d) > 0.5 for d in existing_descs):
            continue
        new_items.append(EnhancementItem(
            source=artifact_prefix,
            severity=req.severity or "minor",
            description=req.description,
            task_context=f"gate-review:{artifact_prefix}",
        ))
        existing_descs.append(req.description)

    if not new_items:
        return
    backlog.items.extend(new_items)
    await runner.artifacts.put(key, backlog.model_dump_json(), feature=feature)
    logger.info(
        "Gate enhancement backlog (%s): +%d items, %d dupes skipped (total: %d)",
        artifact_prefix, len(new_items), len(items) - len(new_items), len(backlog.items),
    )


def _build_prior_revision_context(
    ledger: Any, cycle: int, context_base: Any = None,
) -> str:
    """Build markdown of prior review history for the gate reviewer.

    No truncation — all findings are included with full descriptions.
    If the result exceeds the prompt file threshold, offloads to a file.
    """
    from pathlib import Path

    if not ledger.findings:
        return ""

    resolved = [f for f in ledger.findings if f.status == "resolved"]
    open_findings = [f for f in ledger.findings if f.status in ("open", "fix_attempted")]

    parts = [f"\n\n## Prior Review History (cycle {cycle}, {len(ledger.findings)} findings tracked)\n"]

    if resolved:
        parts.append(f"### Resolved ({len(resolved)}) — do NOT re-raise these")
        for f in resolved:
            parts.append(f"- ~~{f.id}: {f.description}~~ (resolved cycle {f.cycle_resolved})")

    if open_findings:
        parts.append(f"\n### Still Open ({len(open_findings)})")
        for f in open_findings:
            attempts = f", {len(f.revision_attempts)} prior attempts" if f.revision_attempts else ""
            parts.append(f"- {f.id}: {f.description} [{f.severity or 'unclassified'}]{attempts}")

    result = "\n".join(parts)

    base = Path(context_base) if context_base else None
    return _offload_if_large(result, base, "gate-review-history")


async def compile_artifacts(
    runner: WorkflowRunner,
    feature: Feature,
    phase_name: str,
    *,
    compiler_actor: Actor,
    decomposition: SubfeatureDecomposition,
    artifact_prefix: str,
    broad_key: str,
    final_key: str,
) -> str:
    """Compile per-subfeature artifacts into a single final artifact.

    The compiler writes the unified document to a file (bypassing structured
    output token limits), and we read it back for artifact storage.
    """
    from pathlib import Path

    from ...services.artifacts import _key_to_path

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

    # Resolve output file path
    mirror = runner.services.get("artifact_mirror")
    if mirror:
        feature_dir = Path(mirror.feature_dir(feature.id))
        file_path = feature_dir / _key_to_path(final_key)
        file_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        import tempfile
        feature_dir = Path(tempfile.mkdtemp())
        file_path = feature_dir / f"{final_key}.md"
        file_path.parent.mkdir(parents=True, exist_ok=True)

    # Write source artifacts to file so the compiler can read them
    # (avoids inlining potentially huge content in the prompt)
    sources_path = feature_dir / f"compile-sources-{artifact_prefix}.md"
    sources_path.parent.mkdir(parents=True, exist_ok=True)
    sources_path.write_text(source_text, encoding="utf-8")

    await runner.run(
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
                f"**Read the source artifacts from:** `{sources_path}`\n"
                f"**Write the complete compiled document to:** `{file_path}`\n"
            ),
        ),
        feature,
        phase_name=phase_name,
    )

    # Read the compiled file
    if not file_path.exists():
        raise RuntimeError(
            f"Compiler did not write output to {file_path}"
        )
    compiled_text = file_path.read_text(encoding="utf-8").strip()
    if not compiled_text:
        raise RuntimeError(
            f"Compiler wrote empty file at {file_path}"
        )

    # NOTE: we intentionally do NOT store to the DB here.  The compiled
    # artifact lives on the filesystem until the gate review approves it.
    # The calling phase stores to DB after gate-review approval so that
    # the resume check can distinguish "compiled" from "gate-approved".

    # Host the compiled artifact
    hosting = runner.services.get("hosting")
    if hosting:
        await hosting.push(feature.id, final_key, compiled_text, f"Compiled {artifact_prefix.upper()} — {feature.name}")

    return compiled_text


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
    post_update: Callable[[str, str], Awaitable[None]] | None = None,
    context_keys: list[str] | None = None,
    additional_urls: dict[str, str] | None = None,
    post_compile: Callable[[], Awaitable[None]] | None = None,
    warn_after_cycles: int = 3,
) -> str:
    """Interview-based gate review. Replaces gate_and_revise for compiled artifacts.

    Flow:
    1. Lead interviews user: "Is there anything you'd like changed?"
    2. If changes requested: produce RevisionPlan, route to affected subfeature agents
    3. Re-compile and re-present
    4. Loop until user approves

    Convergence tracking (mirrors implementation.py verify/fix cycle):
    - GateReviewLedger tracks findings across review cycles
    - Dedup suppresses re-raised issues that were already resolved
    - Severity classification defers minor/nit requests to enhancement backlog
    - Prior revision context injected into reviewer prompt
    """
    from ...models.outputs import Envelope, ReviewOutcome, envelope_done

    from .._common import HostedInterview

    compiled_text = await get_existing_artifact(runner, feature, compiled_key) or ""

    # ── Gate review artifact key ──
    from pathlib import Path

    gate_review_key = f"gate-review:{artifact_prefix}"
    mirror = runner.services.get("artifact_mirror")

    # Load prior gate review from disk (survives bridge restarts)
    prior_review_text = ""
    gate_review_path = None
    if mirror:
        from ...services.artifacts import _key_to_path

        gate_review_path = Path(mirror.feature_dir(feature.id)) / _key_to_path(gate_review_key)
        if gate_review_path.exists():
            prior_review_text = gate_review_path.read_text(encoding="utf-8").strip()

    # ── Initialize convergence tracking ──
    gate_ledger = await _load_gate_ledger(runner, feature, artifact_prefix)
    review_cycle = gate_ledger.cycle

    # ── Auto-execute prior agreed revisions ──
    if prior_review_text:
        logger.info(
            "interview_gate_review: found prior gate review — extracting "
            "and executing agreed revisions before presenting for approval"
        )
        extracted_plan = await _extract_revision_plan(
            runner, feature, phase_name,
            review_text=prior_review_text,
            decomposition=decomposition,
        )
        if extracted_plan.requests:
            logger.info(
                "interview_gate_review: executing %d prior revision requests",
                len(extracted_plan.requests),
            )
            await targeted_revision(
                runner, feature, phase_name,
                revision_plan=extracted_plan,
                decomposition=decomposition,
                base_role=base_role,
                output_type=output_type,
                artifact_prefix=artifact_prefix,
                post_update=post_update,
                context_keys=context_keys,
                checkpoint_prefix=f"gate-{review_cycle}",
            )
            # Track auto-executed revisions in the ledger
            review_cycle += 1
            gate_ledger = _update_gate_ledger(
                gate_ledger, extracted_plan, artifact_prefix, review_cycle,
            )
            gate_ledger.cycle = review_cycle
            await _save_gate_ledger(runner, feature, gate_ledger, artifact_prefix)

            # Re-compile with revisions applied
            compiled_text = await compile_artifacts(
                runner, feature, phase_name,
                compiler_actor=compiler_actor,
                decomposition=decomposition,
                artifact_prefix=artifact_prefix,
                broad_key=broad_key,
                final_key=compiled_key,
            )
            if post_compile:
                await post_compile()
            # Clear the prior review file — revisions have been applied
            if gate_review_path and gate_review_path.exists():
                gate_review_path.unlink()
                prior_review_text = ""
        else:
            logger.warning(
                "interview_gate_review: prior gate review exists but "
                "extraction produced no revision requests"
            )

    while True:
        # ── Cycle tracking ──
        review_cycle += 1
        if review_cycle > warn_after_cycles:
            logger.warning(
                "Gate review cycle %d for %s (exceeded %d without approval)",
                review_cycle, artifact_prefix, warn_after_cycles,
            )

        # Clear any prior gate reviewer session so each review iteration
        # starts fresh — prevents auto-approval from session continuity
        # and cross-gate contamination when actors are reused.
        await _clear_agent_session(runner, lead_actor, feature)

        hosting = runner.services.get("hosting")
        review_url = hosting.get_url(compiled_key) if hosting else ""
        url_note = f"\nReview in browser: {review_url}" if review_url else ""

        extra_links = ""
        if additional_urls:
            links = "\n".join(
                f"- **{label}**: {url}" for label, url in additional_urls.items()
            )
            extra_links = f"\n\nAdditional resources for review:\n{links}"

        # ── Build prior revision context for the reviewer ──
        context_base = Path(mirror.feature_dir(feature.id)) if mirror else None
        prior_context = _build_prior_revision_context(gate_ledger, review_cycle, context_base)

        envelope = await runner.run(
            HostedInterview(
                questioner=lead_actor,
                responder=_get_user(),
                initial_prompt=(
                    f"**[MODE: Gate Review]** You are in Gate Review mode. "
                    f"Review the compiled **{artifact_prefix}** artifact below and discuss "
                    f"with the user. Do NOT start a Broad Architecture, Requirements, "
                    f"or Design interview.\n\n"
                    f"I've compiled the {artifact_prefix} from all subfeatures. "
                    f"Please review it and let me know if there is anything you'd like changed.{url_note}"
                    f"{extra_links}\n\n"
                    f"Compiled artifact for review:\n{compiled_text}"
                    f"{prior_context}"
                ),
                output_type=Envelope[ReviewOutcome],
                done=envelope_done,
                artifact_key=gate_review_key,
                artifact_label=f"Gate Review — {artifact_prefix}",
            ),
            feature,
            phase_name=phase_name,
        )

        outcome: ReviewOutcome = envelope.output

        # Guard: agent set approved=True but also populated revision_plan.
        if outcome.approved and outcome.revision_plan.requests:
            logger.warning(
                "interview_gate_review: agent set approved=True but "
                "revision_plan has %d requests — overriding to approved=False",
                len(outcome.revision_plan.requests),
            )
            outcome.approved = False

        if outcome.approved:
            break

        # ── Fallback: extract revision plan from gate review file ──
        if not outcome.revision_plan.requests:
            logger.warning(
                "interview_gate_review: approved=False but revision_plan.requests "
                "is empty — extracting from gate review file"
            )
            review_file_text = _read_artifact_file(runner, feature, gate_review_key)
            if not review_file_text and prior_review_text:
                review_file_text = prior_review_text

            if review_file_text:
                extracted_plan = await _extract_revision_plan(
                    runner, feature, phase_name,
                    review_text=review_file_text,
                    decomposition=decomposition,
                )
                if extracted_plan.requests:
                    outcome.revision_plan = extracted_plan
                    logger.info(
                        "interview_gate_review: extracted %d revision requests from text",
                        len(extracted_plan.requests),
                    )
                else:
                    logger.error(
                        "interview_gate_review: extraction also produced empty "
                        "revision_plan — revisions will not run"
                    )
            else:
                logger.error(
                    "interview_gate_review: no gate review file to extract from"
                )

        # ── Convergence: dedup + partition ──
        if outcome.revision_plan.requests:
            # Classify severity if missing
            if any(not r.severity for r in outcome.revision_plan.requests):
                outcome.revision_plan = await _classify_revision_severity(
                    runner, feature, phase_name, outcome.revision_plan,
                )

            # Dedup against resolved findings
            outcome.revision_plan, suppressed = _dedup_revision_requests(
                outcome.revision_plan, gate_ledger, artifact_prefix,
            )
            if suppressed:
                logger.info(
                    "interview_gate_review: suppressed %d duplicate revision requests for %s",
                    len(suppressed), artifact_prefix,
                )

            # Partition blocking vs deferred
            outcome.revision_plan, deferred = _partition_revision_plan(
                outcome.revision_plan, artifact_prefix,
            )
            if deferred:
                await _append_gate_enhancements(runner, feature, deferred, artifact_prefix)
                logger.info(
                    "interview_gate_review: deferred %d minor revision requests for %s",
                    len(deferred), artifact_prefix,
                )

            # If all requests were resolved/deferred, skip revision+recompile
            if not outcome.revision_plan.requests:
                logger.info(
                    "interview_gate_review: all revision requests resolved/deferred "
                    "for %s — skipping recompile", artifact_prefix,
                )
                gate_ledger.cycle = review_cycle
                await _save_gate_ledger(runner, feature, gate_ledger, artifact_prefix)
                continue  # re-present artifact without recompilation

        # Update prior_review_text for next iteration
        updated_review = _read_artifact_file(runner, feature, gate_review_key)
        if updated_review:
            prior_review_text = updated_review

        # Execute targeted revisions
        await targeted_revision(
            runner, feature, phase_name,
            revision_plan=outcome.revision_plan,
            decomposition=decomposition,
            base_role=base_role,
            output_type=output_type,
            artifact_prefix=artifact_prefix,
            post_update=post_update,
            context_keys=context_keys,
            checkpoint_prefix=f"gate-{review_cycle}",
        )

        # ── Update ledger after revisions ──
        gate_ledger = _update_gate_ledger(
            gate_ledger, outcome.revision_plan, artifact_prefix, review_cycle,
        )
        gate_ledger.cycle = review_cycle
        await _save_gate_ledger(runner, feature, gate_ledger, artifact_prefix)

        # Re-compile
        compiled_text = await compile_artifacts(
            runner, feature, phase_name,
            compiler_actor=compiler_actor,
            decomposition=decomposition,
            artifact_prefix=artifact_prefix,
            broad_key=broad_key,
            final_key=compiled_key,
        )

        # Refresh secondary hosted resources
        if post_compile:
            await post_compile()

    # ── Persist gate-approved artifact to DB immediately ──
    # compile_artifacts() intentionally writes to the filesystem mirror only,
    # deferring the DB write until gate approval.  Writing here (inside
    # interview_gate_review rather than in the calling phase) eliminates the
    # crash window between approval and the caller's runner.artifacts.put().
    await runner.artifacts.put(compiled_key, compiled_text, feature=feature)

    # ── Mark all open findings as resolved on approval ──
    for f in gate_ledger.findings:
        if f.status in ("open", "fix_attempted"):
            f.status = "resolved"
            f.cycle_resolved = review_cycle
    gate_ledger.cycle = review_cycle
    await _save_gate_ledger(runner, feature, gate_ledger, artifact_prefix)

    return compiled_text


# ── Patch-based revision helpers ─────────────────────────────────────────────


def _parse_markdown_sections(
    text: str,
) -> list[tuple[str, int, int, int]]:
    """Parse document into sections by markdown or HTML headers.

    Supports:
      - Markdown: ## Header, ### Header, #### Header
      - HTML: <h2>Header</h2>, <h3>Header</h3>, <h4>Header</h4>
      - HTML with code: <h4><code>ID</code>: Title</h4>

    Returns list of (header_line, level, start_offset, end_offset).
    Each section spans from its header to the next header at the same or
    higher level (lower number).
    """
    import re

    # Match both markdown headers and HTML headers
    header_re = re.compile(
        r"^(?:"
        r"(#{2,})\s+(.+)"                     # markdown: ## ...
        r"|"
        r"\s*<h([2-6])[^>]*>(.+?)</h\3>"      # HTML: <h3>...</h3>
        r")",
        re.MULTILINE,
    )
    matches = list(header_re.finditer(text))
    if not matches:
        return []

    sections: list[tuple[str, int, int, int]] = []
    for i, m in enumerate(matches):
        header_line = m.group(0).strip()
        # Determine level: markdown group 1 or HTML group 3
        if m.group(1):
            level = len(m.group(1))
        else:
            level = int(m.group(3))
        start = m.start()
        end = len(text)
        for j in range(i + 1, len(matches)):
            nm = matches[j]
            next_level = len(nm.group(1)) if nm.group(1) else int(nm.group(3))
            if next_level <= level:
                end = nm.start()
                break
        sections.append((header_line, level, start, end))
    return sections


def _clean_header(text: str) -> str:
    """Strip markdown/HTML markup from a header for comparison."""
    import re

    text = re.sub(r"^#{2,}\s*", "", text.strip())
    text = re.sub(r"</?h[2-6][^>]*>", "", text)
    text = re.sub(r"</?code>", "", text)
    return text.strip()


def _find_section(
    sections: list[tuple[str, int, int, int]],
    target: str,
    occurrence: int = 1,
) -> tuple[str, int, int, int] | None:
    """Find a section by header prefix match.

    Handles markdown headers, HTML headers, and targets like:
      "### STEP-5:", "Overview", "<h3>Services</h3>", "CP-14"

    occurrence=1 returns the first match, occurrence=2 the second, etc.
    """
    target_clean = _clean_header(target)
    found = 0

    for header, level, start, end in sections:
        header_clean = _clean_header(header)
        if header_clean.startswith(target_clean) or target_clean in header_clean:
            found += 1
            if found == occurrence:
                return (header, level, start, end)
    return None


def _count_matching_sections(
    sections: list[tuple[str, int, int, int]], target: str,
) -> int:
    """Count how many sections match the target."""
    target_clean = _clean_header(target)
    count = 0
    for header, _lvl, _s, _e in sections:
        header_clean = _clean_header(header)
        if header_clean.startswith(target_clean) or target_clean in header_clean:
            count += 1
    return count


def _apply_patches(text: str, patches: list) -> str:
    """Apply section-level patches to markdown/HTML text.

    Handles duplicate headers by tracking per-target occurrence counts.
    When multiple patches target the same header, they are applied to
    successive occurrences (1st, 2nd, 3rd, ...).

    Re-parses after each patch to handle offset shifts.
    Unmatched targets are logged and skipped.
    """
    # Track how many times each target has been used so far,
    # so successive patches to the same header hit successive occurrences.
    target_usage: dict[str, int] = {}

    for patch in patches:
        # FULL_DOCUMENT: replace entire artifact content
        if patch.target.strip().upper() == "FULL_DOCUMENT":
            if patch.operation == "replace":
                text = patch.content.rstrip("\n") + "\n"
            continue

        target_key = _clean_header(patch.target)
        target_usage[target_key] = target_usage.get(target_key, 0) + 1
        occurrence = target_usage[target_key]

        sections = _parse_markdown_sections(text)
        match = _find_section(sections, patch.target, occurrence=occurrence)

        # If the nth occurrence doesn't exist, try the first (for non-duplicate targets)
        if not match and occurrence > 1:
            logger.warning(
                "Patch target %r occurrence %d not found — skipping (may be duplicate header issue)",
                patch.target, occurrence,
            )
            continue

        if patch.operation == "replace":
            if not match:
                logger.warning(
                    "Patch target not found for replace: %r — skipping",
                    patch.target,
                )
                continue
            _, match_level, start, end = match
            # Only replace up to the first child section, preserving children.
            # A "child" is any section at a deeper level within this section's range.
            replace_end = end
            for ch, clvl, cs, ce in sections:
                if cs > start and cs < end and clvl > match_level:
                    replace_end = cs
                    break
            content = patch.content.rstrip("\n") + "\n\n"
            text = text[:start] + content + text[replace_end:]

        elif patch.operation == "insert_after":
            if not match:
                logger.warning(
                    "Patch target not found for insert_after: %r — appending to end",
                    patch.target,
                )
                text = text.rstrip("\n") + "\n\n" + patch.content.rstrip("\n") + "\n\n"
                continue
            _, _, _, end = match
            content = patch.content.rstrip("\n") + "\n\n"
            text = text[:end] + content + text[end:]

        elif patch.operation == "delete":
            if not match:
                logger.warning(
                    "Patch target not found for delete: %r — skipping",
                    patch.target,
                )
                continue
            _, _, start, end = match
            text = text[:start] + text[end:]

        elif patch.operation == "find_replace":
            if not match:
                logger.warning(
                    "Patch target not found for find_replace: %r — skipping",
                    patch.target,
                )
                continue
            _, _, start, end = match
            section_text = text[start:end]
            if not patch.find or patch.find not in section_text:
                logger.warning(
                    "find_replace: text %r not found in section %r — skipping",
                    (patch.find or "")[:50], patch.target[:40],
                )
                continue
            new_section = section_text.replace(patch.find, patch.content, 1)
            text = text[:start] + new_section + text[end:]

        else:
            logger.warning("Unknown patch operation: %r — skipping", patch.operation)

    return text


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
    post_update: Callable[[str, str], Awaitable[None]] | None = None,
    context_keys: list[str] | None = None,
    checkpoint_prefix: str = "",
    prior_decisions: str = "",
) -> None:
    """Execute revisions on specific subfeatures per the RevisionPlan.

    Re-runs affected subfeature agents with revision instructions.
    Updates subfeature artifacts in store. One-shot Ask per SF (no multi-turn).
    Summaries are NOT regenerated here — caller should batch them if needed.

    If checkpoint_prefix is set, completed revisions are marked in the artifact
    store. On restart, already-completed revisions are skipped.
    """
    import asyncio as _asyncio

    from iriai_compose.actors import Role

    from ...config import BUDGET_TIERS

    _keys = context_keys if context_keys is not None else ["project", "scope"]
    valid_slugs = {sf.slug for sf in decomposition.subfeatures}

    # Merge ALL requests per SF slug — same SF may appear in multiple
    # requests; we collect them all so the revision agent sees every change.
    sf_requests: dict[str, list[Any]] = {}
    for request in revision_plan.requests:
        for sf_slug in request.affected_subfeatures:
            if sf_slug not in valid_slugs:
                logger.warning(
                    "targeted_revision: skipping unknown subfeature slug %r "
                    "(valid: %s)", sf_slug, ", ".join(sorted(valid_slugs)),
                )
                continue
            sf_requests.setdefault(sf_slug, []).append(request)
    revision_tasks: list[tuple[list[Any], str]] = [
        (reqs, slug) for slug, reqs in sf_requests.items()
    ]

    # Skip already-completed revisions (checkpoint)
    if checkpoint_prefix:
        filtered: list[tuple[list[Any], str]] = []
        for reqs, slug in revision_tasks:
            marker = await runner.artifacts.get(
                f"revision-done:{checkpoint_prefix}:{artifact_prefix}:{slug}",
                feature=feature,
            )
            if marker:
                logger.info(
                    "targeted_revision: %s:%s already revised — skipping",
                    artifact_prefix, slug,
                )
            else:
                filtered.append((reqs, slug))
        revision_tasks = filtered

    async def _revise_one(requests: list[Any], sf_slug: str) -> None:
        request = requests[0]  # Primary request (for checkpoint compat)
        import json as _json

        from ...models.outputs import ArtifactPatchSet

        sf_key = f"{artifact_prefix}:{sf_slug}"
        existing = await runner.artifacts.get(sf_key, feature=feature) or ""
        if not existing:
            logger.warning(
                "targeted_revision: no existing artifact for %s — "
                "revision agent will have no context to revise", sf_key,
            )

        # ── Two-phase checkpoint ──────────────────────────────────
        # Phase 1: patches saved to DB (can resume without API call)
        # Phase 2: revision-done marker (patches applied successfully)
        patch_key = (
            f"patches:{checkpoint_prefix}:{artifact_prefix}:{sf_slug}"
            if checkpoint_prefix
            else f"patches:{artifact_prefix}:{sf_slug}"
        )

        patch_set: ArtifactPatchSet | None = None

        # Try loading saved patches from a prior run
        saved_json = await runner.artifacts.get(patch_key, feature=feature)
        if saved_json:
            try:
                patch_set = ArtifactPatchSet.model_validate(_json.loads(saved_json))
                logger.info(
                    "targeted_revision: loaded %d saved patches for %s — skipping API call",
                    len(patch_set.patches), sf_key,
                )
            except Exception:
                logger.warning(
                    "targeted_revision: failed to parse saved patches for %s — regenerating",
                    sf_key,
                )
                patch_set = None

        if patch_set is None:
            # Generate patches via API
            # Use opus model name directly; Codex runtime will use its own
            # default model (the model string is a hint, not a hard requirement)
            revision_role = Role(
                name=base_role.name,
                prompt=base_role.prompt,
                tools=[],
                model=BUDGET_TIERS["opus"],
            )
            revision_actor = AgentActor(
                name=f"{artifact_prefix}-sf-{sf_slug}-rev",
                role=revision_role,
                context_keys=_keys,
            )

            decisions_block = ""
            if prior_decisions:
                decisions_block = (
                    f"\n\n## Mandatory Decisions (all prior cycles)\n"
                    f"Apply ALL of these decisions. They are hard requirements.\n\n"
                    f"{prior_decisions}\n\n"
                )

            # Build combined change instructions from ALL requests for this SF
            changes_parts = []
            for i, req in enumerate(requests, 1):
                changes_parts.append(
                    f"**Change {i}:** {req.description}\n"
                    f"**Reasoning:** {req.reasoning}"
                )
            changes_block = "\n\n".join(changes_parts)

            patch_set = await runner.run(
                Ask(
                    actor=revision_actor,
                    prompt=(
                        f"Revise the {artifact_prefix} for subfeature '{sf_slug}' "
                        f"by producing a list of PATCHES.\n\n"
                        f"{changes_block}\n\n"
                        f"Address ALL {len(requests)} change(s) in a single patch set.\n"
                        f"{decisions_block}\n"
                        f"IMPORTANT: Do NOT rewrite the entire document. Produce targeted "
                        f"patches only for sections that need to change.\n\n"
                        f"TARGETING RULES for {artifact_prefix}:\n"
                        + (
                            f"- Target individual steps by unique header "
                            f"(e.g. '### STEP-5:', '## Architecture', '## File Manifest').\n"
                            f"- Each STEP has a unique ID — use it as the target.\n\n"
                            if artifact_prefix == "plan" else
                            f"- For system-designs (HTML): target unique section headers "
                            f"like 'Overview', 'Services', 'CP-14', 'ENT-30'.\n\n"
                            if artifact_prefix == "system-design" else
                            f"- This artifact may have non-unique subsection headers. "
                            f"Produce a SINGLE patch with target 'FULL_DOCUMENT' to "
                            f"replace the entire artifact content. Include all sections.\n\n"
                        )
                        + f"For each patch specify:\n"
                        f"- target: the header text of the section to modify "
                        f"(or 'FULL_DOCUMENT' for complete replacement)\n"
                        f"- operation: 'replace' (replace section intro, children preserved), "
                        f"'insert_after' (add new section after target), 'delete', or "
                        f"'find_replace' (surgical text swap within a section)\n"
                        f"- content: the replacement content (for replace/insert_after/find_replace)\n"
                        f"- find: the exact text to find within the section (for find_replace only)\n"
                        f"- reasoning: brief explanation\n\n"
                        f"Use 'find_replace' for small targeted changes within a section "
                        f"(fixing field names, changing specific values). "
                        f"Use 'replace' only when the entire section intro needs rewriting.\n\n"
                        f"Unchanged sections are preserved automatically.\n\n"
                        f"If you have questions that MUST be answered before you can "
                        f"produce correct patches, return an empty patches list and put "
                        f"your questions in the summary field. You will get a chance to "
                        f"discuss with the user and then produce patches. Only do this "
                        f"for genuine ambiguities — not for optional improvements.\n\n"
                        f"Current artifact:\n{existing}"
                    ),
                    output_type=ArtifactPatchSet,
                ),
                feature,
                phase_name=phase_name,
            )

            # If agent returned questions instead of patches, escalate to interview
            if not patch_set.patches and patch_set.summary:
                logger.info(
                    "targeted_revision: agent has questions for %s — escalating to interview",
                    sf_key,
                )
                from ...models.outputs import Envelope, envelope_done
                from .._common import HostedInterview

                interview_actor = AgentActor(
                    name=f"{artifact_prefix}-sf-{sf_slug}-rev-q",
                    role=revision_role,
                    context_keys=_keys,
                )
                await runner.run(
                    HostedInterview(
                        questioner=interview_actor,
                        responder=_get_user(),
                        initial_prompt=(
                            f"I need clarification before revising {artifact_prefix} "
                            f"for subfeature '{sf_slug}':\n\n"
                            f"{patch_set.summary}\n\n"
                            f"Please answer these questions so I can produce the patches."
                        ),
                        output_type=Envelope[ArtifactPatchSet],
                        done=envelope_done,
                        artifact_key=f"revision-questions:{artifact_prefix}:{sf_slug}",
                        artifact_label=f"Revision Questions — {sf_slug}",
                    ),
                    feature,
                    phase_name=phase_name,
                )

                # Load the answers from the discussion file
                answers = ""
                q_mirror = runner.services.get("artifact_mirror")
                if q_mirror:
                    from ...services.artifacts import _key_to_path
                    q_path = (
                        q_mirror.feature_dir(feature.id)
                        / _key_to_path(f"revision-questions:{artifact_prefix}:{sf_slug}")
                    )
                    if q_path.exists():
                        answers = q_path.read_text(encoding="utf-8").strip()

                # Second Ask with the answers
                patch_set = await runner.run(
                    Ask(
                        actor=revision_actor,
                        prompt=(
                            f"Revise the {artifact_prefix} for subfeature '{sf_slug}' "
                            f"by producing a list of PATCHES.\n\n"
                            f"**Change requested:** {request.description}\n"
                            f"**Reasoning:** {request.reasoning}\n"
                            f"{decisions_block}\n"
                            f"**Clarification from user:**\n{answers}\n\n"
                            f"Now produce the patches.\n\n"
                            f"Current artifact:\n{existing}"
                        ),
                        output_type=ArtifactPatchSet,
                    ),
                    feature,
                    phase_name=phase_name,
                )

            # Phase 1 checkpoint: save patches immediately
            await runner.artifacts.put(
                patch_key, patch_set.model_dump_json(indent=2), feature=feature,
            )
            logger.info(
                "targeted_revision: saved %d patches for %s",
                len(patch_set.patches), sf_key,
            )

        if not patch_set.patches:
            logger.info(
                "targeted_revision: no patches produced for %s — skipping",
                sf_key,
            )
            return

        logger.info(
            "targeted_revision: applying %d patches to %s",
            len(patch_set.patches), sf_key,
        )
        revised_text = _apply_patches(existing, patch_set.patches)

        # Size guard: reject revisions that shrink the artifact by >50%.
        # Protects against sonnet producing meta-descriptions or truncated output.
        existing_size = len(existing)
        revised_size = len(revised_text)
        if existing_size > 0 and revised_size < existing_size * 0.5:
            logger.error(
                "targeted_revision: rejecting %s — revision too small "
                "(%d → %d bytes, %.0f%% shrink)",
                sf_key, existing_size, revised_size,
                (1 - revised_size / existing_size) * 100,
            )
            return

        await runner.artifacts.put(sf_key, revised_text, feature=feature)

        # Write to disk via artifact mirror
        mirror = runner.services.get("artifact_mirror")
        if mirror:
            from ...services.artifacts import _key_to_path
            path = mirror.feature_dir(feature.id) / _key_to_path(sf_key)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(revised_text, encoding="utf-8")

        # Update the hosted doc so the browser shows the revised version
        hosting = runner.services.get("hosting")
        if hosting:
            await hosting.update(feature.id, sf_key, revised_text)
        if post_update:
            await post_update(sf_key, revised_text)

        # Checkpoint: mark this revision as done so restarts skip it
        if checkpoint_prefix:
            await runner.artifacts.put(
                f"revision-done:{checkpoint_prefix}:{artifact_prefix}:{sf_slug}",
                "done",
                feature=feature,
            )

    logger.info(
        "targeted_revision: dispatching %d SF revisions in parallel for %s",
        len(revision_tasks), artifact_prefix,
    )
    results = await _asyncio.gather(
        *[_revise_one(reqs, slug) for reqs, slug in revision_tasks],
        return_exceptions=True,
    )
    for i, res in enumerate(results):
        if isinstance(res, BaseException):
            logger.error(
                "targeted_revision: %s:%s crashed: %s",
                artifact_prefix, revision_tasks[i][1], res,
            )


def _get_user() -> Actor:
    """Lazy import of the user actor to avoid circular imports."""
    from ...roles import user
    return user


async def _clear_agent_session(
    runner: WorkflowRunner, actor: Actor, feature: Feature
) -> None:
    """Delete persisted agent session so the next invoke starts fresh.

    Used by interview_gate_review to prevent session continuity between
    rejection→revision→re-review iterations, and to prevent cross-gate
    contamination when the same actor is used for sequential gate reviews.
    """
    session_key = f"{actor.name}:{feature.id}"
    if hasattr(runner, "sessions") and runner.sessions:
        await runner.sessions.delete(session_key)
    runtime = getattr(runner, "agent_runtime", None)
    if runtime:
        msgs = getattr(runtime, "_session_messages", None)
        if isinstance(msgs, dict):
            msgs.pop(session_key, None)
        ctx = getattr(runtime, "_session_context", None)
        if isinstance(ctx, dict):
            ctx.pop(session_key, None)
