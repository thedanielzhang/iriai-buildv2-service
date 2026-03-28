from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
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
            "reasoning (why), affected_subfeatures (list of slugs), and "
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
                f"Available subfeature slugs: {', '.join(sf_slugs)}\n\n"
                f"For each revision request, identify:\n"
                f"- description: what needs to change\n"
                f"- reasoning: why (the decision or feedback that prompted it)\n"
                f"- affected_subfeatures: which slugs need updating (from list above)\n"
                f"- cross_subfeature: true if the change spans multiple subfeatures\n\n"
                f"Review:\n{review_text}"
            ),
            output_type=RevisionPlan,
        ),
        feature,
        phase_name=phase_name,
    )

    return result


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

    compiled_text = await get_existing_artifact(runner, feature, compiled_key) or ""

    # ── Gate review artifact key ──
    # Used for both file persistence (agent writes review to disk) and
    # loading prior revision decisions on restart.
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

    # ── Auto-execute prior agreed revisions ──
    # If a prior gate review file exists with revision requests, the user
    # already agreed to them in a previous session.  Extract and execute
    # them now, then present the updated artifact for approval.
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
            )
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
        # The model_validator on ReviewOutcome should already auto-correct
        # this, but defend in depth.
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
        # The agent often describes the revision plan in prose but fails to
        # populate revision_plan.requests in structured output.  Use a Haiku
        # extraction call (same pattern as _extract_review_fields).
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
        )

        # Re-compile
        compiled_text = await compile_artifacts(
            runner, feature, phase_name,
            compiler_actor=compiler_actor,
            decomposition=decomposition,
            artifact_prefix=artifact_prefix,
            broad_key=broad_key,
            final_key=compiled_key,
        )

        # Refresh secondary hosted resources (e.g., re-compile unified mockup,
        # re-convert system design HTML) after the text artifacts are updated.
        if post_compile:
            await post_compile()

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
    post_update: Callable[[str, str], Awaitable[None]] | None = None,
    context_keys: list[str] | None = None,
) -> None:
    """Execute revisions on specific subfeatures per the RevisionPlan.

    Re-runs affected subfeature agents with revision instructions.
    Updates subfeature artifacts in store. Regenerates summaries.
    """
    from ...models.outputs import Envelope, envelope_done
    from ...roles import InterviewActor
    from .._common import HostedInterview

    approver = _get_user()
    _keys = context_keys if context_keys is not None else ["project", "scope"]
    valid_slugs = {sf.slug for sf in decomposition.subfeatures}

    for request in revision_plan.requests:
        for sf_slug in request.affected_subfeatures:
            if sf_slug not in valid_slugs:
                logger.warning(
                    "targeted_revision: skipping unknown subfeature slug %r "
                    "(valid: %s)", sf_slug, ", ".join(sorted(valid_slugs)),
                )
                continue

            sf_key = f"{artifact_prefix}:{sf_slug}"
            existing = await runner.artifacts.get(sf_key, feature=feature) or ""
            if not existing:
                logger.warning(
                    "targeted_revision: no existing artifact for %s — "
                    "revision agent will have no context to revise", sf_key,
                )

            revision_actor = InterviewActor(
                name=f"{artifact_prefix}-sf-{sf_slug}-rev",
                role=base_role,
                context_keys=_keys,
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

            # Prefer file content over Envelope output
            revised_text = None
            mirror = runner.services.get("artifact_mirror")
            if mirror:
                from ...services.artifacts import _key_to_path
                path = mirror.feature_dir(feature.id) / _key_to_path(sf_key)
                if path.exists():
                    revised_text = path.read_text(encoding="utf-8").strip()
            if not revised_text:
                revised_text = to_str(envelope.output)
            await runner.artifacts.put(sf_key, revised_text, feature=feature)

            # Update the hosted doc so the browser shows the revised version
            hosting = runner.services.get("hosting")
            if hosting:
                await hosting.update(feature.id, sf_key, revised_text)
            if post_update:
                await post_update(sf_key, revised_text)

            # Regenerate summary (best-effort — don't crash pipeline if this fails)
            try:
                await generate_summary(runner, feature, artifact_prefix, sf_slug)
            except Exception:
                logger.warning(
                    "targeted_revision: summary generation failed for %s:%s — "
                    "continuing (revision was applied successfully)",
                    artifact_prefix, sf_slug, exc_info=True,
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
