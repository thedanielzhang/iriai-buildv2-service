from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from pydantic import ValidationError

from .models import (
    ActionLevel,
    EvidencePacket,
    FailureClass,
    SupervisorAssessment,
    SupervisorEvidenceBundle,
    SupervisorInvestigationRequest,
    SupervisorSeedPacket,
)
from .tools import SupervisorEvidenceToolbox

logger = logging.getLogger(__name__)

_SUPERVISOR_ROLE_PROMPT = """\
You are the iriai-build-v2 workflow supervisor.

You receive a deterministic seed packet plus optional evidence bundles from
local artifacts, events, bridge logs, and worktree probes. You own the current
status/root-cause assessment; the deterministic classifier is only a hint.

Rules:
- Ground every important claim in the citations already present in the packet.
- Separate facts from inference.
- Say the current action or next step.
- Do not produce generic encouragement or template language.
- For failure/root-cause/revision/update questions, do not answer with only
  live workflow liveness or cursor freshness. Include the concrete latest
  material failure reason, the most recent repair/update action, and whether
  that failure is still current or historical.
- If a failure is historical, still name the specific failure and what evidence
  superseded it. "Historical commit failures predate the cursor" is not enough.
- If the operator asks what failed and the supplied evidence lacks failure/RCA/
  fix detail, request more evidence before returning an assessment.
- Background digests should name the specific material change since the prior
  digest, or stay quiet.
- Do not claim you took an action unless the packet/action record says so.
- Keep the answer short enough for Slack.
- Return JSON only.
- If you need more evidence, return:
  {"type":"request_evidence","requests":[{"reason":"...","artifact_keys":[],"artifact_prefixes":[],"artifact_ids":[],"event_after_id":0,"include_bridge":true,"include_worktrees":true,"sql":[]}]}
- If you have enough evidence, return:
  {"type":"assessment","assessment":{"status":"...","message":"...","facts":[],"inferences":[],"citations":[],"confidence":0.0,"recommended_action":"observe","proposed_action":null}}
- recommended_action MUST be exactly one of: "observe", "digest", "recommend",
  "act_guarded", or "stop/escalate". Put domain-specific next steps such as
  commit hygiene, stale metadata repair, or product repair in status/message,
  not in recommended_action.
- Use proposed_action only for guarded mutations or dry runs: "restart_bridge" or
  "supervisor_maintainer_dry_run". Leave it null for normal status/product repair.
- Evidence is append-only. Treat rows chronologically by id/created_at. Older
  commit failures, stale metadata, or failed initial verifies are historical if
  newer same-group verify/checkpoint/progress evidence supersedes them.
- The seed fact `current_workflow`, when present, is authoritative for live
  "current status/health" questions. Use historical artifacts for context, but
  do not let older group artifacts override the current workflow snapshot.
- If the operator asks about a historical group, answer that history and include
  a short header noting the live group when `current_workflow.group_idx` differs.
- Do not call the workflow currently blocked unless the latest material same-group
  evidence is itself a blocker.
"""
_SUPERVISOR_SESSION_METADATA = {
    "max_session_chars": 200_000,
    "keep_recent_messages": 12,
}


class SupervisorAgent:
    """Compose operator-facing supervisor messages from evidence packets.

    A real agent runtime can be provided for Slack-facing prose. The deterministic
    formatter remains the fallback for tests, runtime outages, and read-only dry
    runs where we still need reliable status text.
    """

    async def compose_message(
        self,
        packet: EvidencePacket,
        *,
        question: str | None = None,
        runtime: Any | None = None,
        feature_id: str | None = None,
        toolbox: SupervisorEvidenceToolbox | None = None,
        initial_bundles: list[SupervisorEvidenceBundle] | None = None,
        assessment_sink: Any | None = None,
        timeout_seconds: float = 90.0,
    ) -> str:
        assessment, bundles, fallback = await self.assess(
            packet,
            question=question,
            runtime=runtime,
            feature_id=feature_id,
            toolbox=toolbox,
            initial_bundles=initial_bundles,
            timeout_seconds=timeout_seconds,
        )
        if assessment_sink is not None:
            await assessment_sink(assessment, bundles, fallback)
        return assessment.message

    async def assess(
        self,
        packet: EvidencePacket,
        *,
        question: str | None = None,
        runtime: Any | None = None,
        feature_id: str | None = None,
        toolbox: SupervisorEvidenceToolbox | None = None,
        initial_bundles: list[SupervisorEvidenceBundle] | None = None,
        timeout_seconds: float = 90.0,
        max_rounds: int = 3,
    ) -> tuple[SupervisorAssessment, list[SupervisorEvidenceBundle], bool]:
        seed = SupervisorSeedPacket(
            feature_id=feature_id or packet.feature_id,
            packet=packet,
        )
        if runtime is None:
            return self.fallback_assessment(packet, question=question), [], True

        bundles: list[SupervisorEvidenceBundle] = list(initial_bundles or [])
        prompt = _agent_prompt(seed, question, bundles)
        try:
            from iriai_compose import Role

            from ..config import BUDGET_TIERS

            role = Role(
                name="workflow-supervisor",
                prompt=_SUPERVISOR_ROLE_PROMPT,
                tools=[],
                model=BUDGET_TIERS["haiku"],
                effort="low",
                metadata=_SUPERVISOR_SESSION_METADATA,
            )
            session_key = (
                f"workflow-supervisor:{feature_id}"
                if feature_id
                else "workflow-supervisor"
            )
            for _round in range(max_rounds):
                response = await asyncio.wait_for(
                    runtime.invoke(role, prompt, session_key=session_key),
                    timeout=timeout_seconds,
                )
                parsed = _parse_agent_response(str(response or ""))
                if isinstance(parsed, SupervisorAssessment):
                    return _guard_assessment_against_seed(packet, parsed), bundles, False
                if not parsed:
                    text = str(response or "").strip()
                    if text:
                        if _looks_like_json(text):
                            break
                        return _plain_text_assessment(packet, text), bundles, False
                    break
                if toolbox is None:
                    break
                new_bundles = await toolbox.gather_many(parsed[:3])
                bundles.extend(new_bundles)
                prompt = _agent_prompt(seed, question, bundles)
        except Exception as exc:
            logger.warning("Supervisor agent message composition failed: %s", exc)
        return self.fallback_assessment(packet, question=question), bundles, True

    def fallback_assessment(
        self,
        packet: EvidencePacket,
        *,
        question: str | None = None,
    ) -> SupervisorAssessment:
        citations = ", ".join(packet.citations[:4]) or "current evidence window"
        group = f"G{packet.group_idx}" if packet.group_idx is not None else "current group"
        retry = f" retry-{packet.retry}" if packet.retry is not None else ""
        prefix = (
            f"Fallback answer for question: {question.strip()} "
            if question
            else "Fallback answer: "
        )
        message = (
            f"{prefix}{group}{retry} is classified as `{packet.classification.value}` "
            f"with {packet.confidence:.0%} confidence. Fact: {citations}. "
            f"Inference: {packet.inference} Action: `{packet.recommended_action.value}`."
        )
        return SupervisorAssessment(
            status=packet.classification.value,
            message=message,
            facts=[citations],
            inferences=[packet.inference],
            citations=packet.citations,
            confidence=packet.confidence,
            recommended_action=packet.recommended_action,
        )

    def answer_status(self, packet: EvidencePacket, *, question: str | None = None) -> str:
        return self.fallback_assessment(packet, question=question).message


def _agent_prompt(
    seed: SupervisorSeedPacket,
    question: str | None,
    bundles: list[SupervisorEvidenceBundle],
) -> str:
    payload = seed.model_dump(mode="json")
    evidence_payload = [bundle.model_dump(mode="json") for bundle in bundles]
    current = seed.packet.facts.get("current_workflow") if seed.packet.facts else None
    detail_guidance = ""
    if _question_requires_failure_detail(question):
        detail_guidance = (
            "## Required Detail For This Question\n"
            "The operator is asking about failure/root cause/revision/update detail. "
            "Your final assessment must include: (1) live state, (2) the concrete "
            "latest material failure reason with paths/tests/hook output when present, "
            "(3) the most recent repair/update action and whether it superseded the "
            "failure, and (4) the next step. If the evidence bundle does not contain "
            "enough detail, request evidence instead of returning a generic status.\n\n"
        )
    return (
        "## Operator Question\n"
        f"{question or 'Provide a current workflow health/status digest.'}\n\n"
        f"{detail_guidance}"
        "## Current Workflow Snapshot JSON\n"
        f"{json.dumps(current, sort_keys=True, indent=2)}\n\n"
        "## Deterministic Seed Packet JSON\n"
        f"{json.dumps(payload, sort_keys=True, indent=2)}\n\n"
        "## Evidence Bundles JSON\n"
        f"{json.dumps(evidence_payload, sort_keys=True, indent=2)}\n\n"
        "Either request more bounded read-only evidence or return the final assessment JSON now."
    )


_FAILURE_DETAIL_RE = re.compile(
    r"\b("
    r"failure|failed|fail|root cause|why|stuck|blocked|blocker|what happened|"
    r"what changed|recent update|most recent|revision|revisions|revise|cycle|"
    r"cycles|retry|retries|fix|fixed|repair|repaired"
    r")\b",
    re.IGNORECASE,
)


def _question_requires_failure_detail(question: str | None) -> bool:
    return bool(question and _FAILURE_DETAIL_RE.search(question))


def _parse_agent_response(
    text: str,
) -> SupervisorAssessment | list[SupervisorInvestigationRequest] | None:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:].strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("type") == "assessment" or "assessment" in payload:
        raw = payload.get("assessment", payload)
        try:
            return _assessment_from_payload(raw)
        except (TypeError, ValidationError):
            return None
    if payload.get("type") == "request_evidence" or "requests" in payload:
        requests = payload.get("requests") or []
        if not isinstance(requests, list):
            return []
        parsed: list[SupervisorInvestigationRequest] = []
        for item in requests[:3]:
            try:
                parsed.append(SupervisorInvestigationRequest.model_validate(item))
            except ValidationError:
                continue
        return parsed
    return None


def _plain_text_assessment(packet: EvidencePacket, text: str) -> SupervisorAssessment:
    return SupervisorAssessment(
        status=packet.classification.value,
        message=text,
        facts=[],
        inferences=[packet.inference],
        citations=packet.citations,
        confidence=packet.confidence,
        recommended_action=packet.recommended_action,
    )


def _guard_assessment_against_seed(
    packet: EvidencePacket,
    assessment: SupervisorAssessment,
) -> SupervisorAssessment:
    if packet.classification != FailureClass.HEALTHY_PROGRESS:
        return assessment
    text = f"{assessment.status} {assessment.message}".lower()
    commit_blocker_claim = "commit" in text and "block" in text
    cites_commit_failure = any("dag-commit-failure:" in citation for citation in assessment.citations)
    seed_success = bool(packet.facts.get("successful_verify_artifacts"))
    if not (commit_blocker_claim and cites_commit_failure and seed_success):
        return assessment
    seed_citations = list(packet.citations)
    return SupervisorAssessment(
        status=FailureClass.HEALTHY_PROGRESS.value,
        message=(
            "Workflow appears healthy based on the latest seed evidence. Older "
            "commit-failure artifacts were present in the investigation, but they "
            "are superseded by newer same-group progress/success evidence; continue "
            "observing unless a new commit failure appears."
        ),
        facts=list(assessment.facts) + [
            "Seed packet reported successful verify/progress evidence after the cited commit failures."
        ],
        inferences=list(assessment.inferences) + [
            "Historical commit-failure rows do not make the current run blocked when newer same-group success evidence exists."
        ],
        citations=seed_citations or assessment.citations,
        confidence=min(assessment.confidence, packet.confidence),
        recommended_action=packet.recommended_action,
        proposed_action=None,
    )


_ACTION_VALUES = {action.value for action in ActionLevel}


def _assessment_from_payload(raw: Any) -> SupervisorAssessment:
    if not isinstance(raw, dict):
        raise TypeError("assessment payload must be an object")
    payload = dict(raw)
    action = str(payload.get("recommended_action") or ActionLevel.OBSERVE.value)
    if action not in _ACTION_VALUES:
        payload["recommended_action"] = _normalize_recommended_action(action).value
        inferences = list(payload.get("inferences") or [])
        inferences.append(
            f"Agent returned non-standard recommended_action {action!r}; "
            f"normalized to {payload['recommended_action']!r}."
        )
        payload["inferences"] = inferences
    if payload.get("proposed_action") not in {
        None,
        "",
        "restart_bridge",
        "supervisor_maintainer_dry_run",
    }:
        payload["proposed_action"] = None
    return SupervisorAssessment.model_validate(payload)


def _normalize_recommended_action(action: str) -> ActionLevel:
    lowered = action.lower().strip().replace("-", "_")
    if any(token in lowered for token in ("stop", "escalate", "blocked")):
        return ActionLevel.STOP_ESCALATE
    if any(token in lowered for token in ("restart", "guarded", "act")):
        return ActionLevel.ACT_GUARDED
    if any(token in lowered for token in ("digest", "progress", "healthy")):
        return ActionLevel.DIGEST
    if any(token in lowered for token in ("repair", "fix", "recommend", "hygiene")):
        return ActionLevel.RECOMMEND
    return ActionLevel.OBSERVE


def _looks_like_json(text: str) -> bool:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`").strip()
        if stripped.lower().startswith("json"):
            stripped = stripped[4:].strip()
    return stripped.startswith(("{", "["))
