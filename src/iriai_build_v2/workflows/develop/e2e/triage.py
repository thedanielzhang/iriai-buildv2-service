"""Triage: assertion-scoped provenance + the green-washing guard.

When a deterministic replay goes red we classify into
``regression | intended_change | flaky`` — never letting the loop edit its own
tests to pass. The classification is keyed on an ASSERTION-SCOPED digest we
compute over only the semantic fields (``pass_condition`` +
``linked_verifiable_state_id`` + ``linked_journey_step_id``), NOT the whole-AC
``content_digest`` (which flips on cosmetic wording edits and would
mass-misclassify).

There is NO ``drift`` class: locator-only breaks are plain failures re-authored
under citation (auto-repairing locators is a green-wash vector).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from .models import E2ESpecRecord, E2EVerdictRecord

_WS = re.compile(r"\s+")


def _norm(text: str) -> str:
    return _WS.sub(" ", (text or "").strip().lower())


def assertion_digest(ac: Any) -> str:
    """Digest over ONLY the semantic assertion fields of an acceptance criterion.

    Deliberately excludes ``description``/wording so cosmetic edits don't flip
    the digest. ``pass_condition`` is prose (normalized); the linked ids are
    identifiers (kept verbatim).
    """
    pc = _norm(getattr(ac, "pass_condition", "") or "")
    vs = (getattr(ac, "linked_verifiable_state_id", "") or "").strip()
    js = (getattr(ac, "linked_journey_step_id", "") or "").strip()
    payload = f"{pc}\x1f{vs}\x1f{js}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_author_assertion_digests(acs: list[Any]) -> dict[str, str]:
    """Map AC-id -> assertion-scoped digest for the ACs a spec covers."""
    return {ac.id: assertion_digest(ac) for ac in acs if getattr(ac, "id", "")}


def bind_specs_from_scenarios(
    scenarios: list[Any],
    ac_by_id: dict[str, Any],
    *,
    adapter_id: str,
    author_commit: str,
    source_commit: str = "",
    test_plan_digest: str = "",
    spec_path_for: Callable[[Any], str] | None = None,
    critical_for: Callable[[Any], tuple[bool, str]] | None = None,
) -> list[E2ESpecRecord]:
    """Bind native scenarios to ACs + record assertion-scoped digests + provenance.

    This is the orchestrator-computed provenance (the spec_author agent may also
    run to author/bind native files; the assertion digests are ALWAYS computed
    here over the ACs' semantic fields, never by the agent).
    """
    records: list[E2ESpecRecord] = []
    for sc in scenarios:
        linked = list(getattr(sc, "linked_acceptance", None) or [])
        acs = [ac_by_id[a] for a in linked if a in ac_by_id]
        digests = compute_author_assertion_digests(acs)
        critical, justification = (critical_for(sc) if critical_for else (False, ""))
        records.append(
            E2ESpecRecord(
                spec_id=getattr(sc, "id", ""),
                scenario_id=getattr(sc, "id", ""),
                title=getattr(sc, "name", ""),
                adapter_id=adapter_id,
                priority=getattr(sc, "priority", ""),
                critical=critical,
                critical_justification=justification,
                linked_ac_ids=[ac.id for ac in acs],
                author_assertion_digests=digests,
                author_commit=author_commit,
                source_commit=source_commit,
                test_plan_digest=test_plan_digest,
                spec_path=(spec_path_for(sc) if spec_path_for else ""),
            )
        )
    return records


def native_results_to_verdicts(
    specs: list[E2ESpecRecord],
    native_tests: list[Any],
    *,
    source_commit: str,
) -> list[E2EVerdictRecord]:
    """Map a native @playwright/test run's per-test results onto bound specs.

    A spec matches a native test when the test title/file references the spec's
    title or scenario id. Produces one verdict per spec (status + evidence).
    """
    verdicts: list[E2EVerdictRecord] = []
    for spec in specs:
        match = _match_test(spec, native_tests)
        if match is None:
            verdicts.append(
                E2EVerdictRecord(
                    spec_id=spec.spec_id, source_commit=source_commit,
                    status="skipped", summary="no native test matched", critical=spec.critical,
                )
            )
            continue
        if match.flaky:
            status, fclass = "pass", "flaky"
        elif match.status == "passed":
            status, fclass = "pass", ""
        elif match.status == "skipped":
            status, fclass = "skipped", ""
        else:
            status, fclass = "fail", ""
        verdicts.append(
            E2EVerdictRecord(
                spec_id=spec.spec_id,
                source_commit=source_commit,
                status=status,
                failure_class=fclass,
                summary=(match.error[:300] if status == "fail" else match.title),
                changed_ac_ids=[],
                critical=spec.critical,
                evidence_path=_first_evidence(match),
            )
        )
    return verdicts


def _first_evidence(t: Any) -> str:
    ev = getattr(t, "evidence", None) or []
    if ev:
        return ev[0]
    shots = getattr(t, "screenshots", None) or []
    return shots[0] if shots else ""


def _match_test(spec: E2ESpecRecord, native_tests: list[Any]) -> Any | None:
    needles = [n for n in (spec.title, spec.scenario_id, spec.spec_id) if n]
    for t in native_tests:
        hay = f"{getattr(t, 'title', '')} {getattr(t, 'file', '')}".lower()
        if any(n.lower() in hay for n in needles):
            return t
    return None


@dataclass
class ClassifyResult:
    failure_class: str  # "" (no finding) | regression | intended_change | flaky
    changed_ac_ids: list[str] = field(default_factory=list)
    reason: str = ""

    @property
    def is_finding(self) -> bool:
        return self.failure_class == "regression"


def classify(
    author_digests: dict[str, str],
    current_digests: dict[str, str],
    replay_status: str,
    *,
    prior_status_at_author_commit: str | None = None,
    flaky: bool = False,
    ratified: bool = True,
) -> ClassifyResult:
    """Classify a (possibly red) replay outcome. Pure + deterministic.

    * ``flaky`` (result flipped across retries) ⇒ ``flaky`` (quarantine, no finding).
    * ``replay_status == 'pass'`` ⇒ no finding.
    * red + assertions UNCHANGED ⇒ ``regression`` (the spec has no license to relax).
    * red + assertion CHANGED ⇒ ``intended_change`` candidate, gated by:
        - the prior spec must have been GREEN at ``author_commit`` (overlapping-
          change guard — a regression + an AC edit in the same window must NOT be
          laundered); else ``regression``.
        - the change must be ratified (two-key); else ``regression``.
        - if the prior replay is unavailable, fail closed ⇒ ``regression``.
    """
    changed = sorted(
        ac for ac, d in author_digests.items() if current_digests.get(ac) != d
    )
    if flaky:
        return ClassifyResult("flaky", changed, "result flipped across retries")
    if replay_status == "pass":
        return ClassifyResult("", changed, "spec passed; no finding")

    # replay failed (red):
    if not changed:
        return ClassifyResult(
            "regression", [], "assertions unchanged; spec has no license to relax"
        )
    if prior_status_at_author_commit is None:
        return ClassifyResult(
            "regression",
            changed,
            "assertion changed but prior-commit replay unavailable; fail closed",
        )
    if prior_status_at_author_commit == "fail":
        return ClassifyResult(
            "regression",
            changed,
            "prior spec already red at author_commit (overlapping change); "
            "not laundered to intended_change",
        )
    if not ratified:
        return ClassifyResult(
            "regression",
            changed,
            "assertion change not ratified by independent verifier (two-key); refused",
        )
    return ClassifyResult(
        "intended_change",
        changed,
        "assertion changed; prior spec green at author_commit; ratified",
    )
