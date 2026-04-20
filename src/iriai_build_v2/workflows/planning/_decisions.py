from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from html import unescape
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ...models.outputs import (
    DecisionLedger,
    DecisionRecord,
    DesignDecisions,
    PRD,
    ScopeOutput,
    SubfeatureDecomposition,
    SystemDesign,
    TechnicalPlan,
    TestPlan,
)
from ...services.markdown import to_markdown
from ._control import load_planning_control, persist_planning_control

_DEFAULT_APPLIES_TO = ["prd", "design", "plan", "system-design", "dag"]
_DECISION_SECTION_NAMES = {
    "decision log",
    "decisions",
    "architecture decisions",
    "user decisions",
}
GLOBAL_DECISIONS_KEY = "decisions:global"


def artifact_applies_to(artifact_kind: str) -> list[str]:
    if artifact_kind == "scope":
        return list(_DEFAULT_APPLIES_TO)
    if artifact_kind == "prd":
        return ["prd", "design", "plan", "system-design", "dag"]
    if artifact_kind == "design":
        return ["design", "plan", "system-design", "dag"]
    if artifact_kind in {"plan", "system-design"}:
        return ["plan", "system-design", "dag"]
    # Test-plan decisions are testing-specific (mocking strategy, coverage
    # tradeoffs, environment needs) — they inform the DAG via verification
    # gates but do NOT propagate back to PRD / design / plan.
    if artifact_kind == "test-plan":
        return ["test-plan", "dag"]
    return list(_DEFAULT_APPLIES_TO)


def parse_decision_ledger(text: str) -> DecisionLedger:
    if not text.strip():
        return DecisionLedger()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict) and "decisions" in data:
        try:
            return DecisionLedger.model_validate(data)
        except Exception:
            pass

    blocks = re.findall(r"```json\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    decisions: list[DecisionRecord] = []
    for block in blocks:
        try:
            decisions.append(DecisionRecord.model_validate(json.loads(unescape(block))))
        except Exception:
            continue
    return DecisionLedger(
        decisions=sorted(decisions, key=_decision_sort_key),
        complete=bool(decisions),
    )


def active_decisions(ledger: DecisionLedger) -> list[DecisionRecord]:
    return sorted(
        (decision for decision in ledger.decisions if decision.status == "active"),
        key=_decision_sort_key,
    )


def render_active_decision_log(
    ledger: DecisionLedger,
    *,
    heading: str = "## Decision Log",
) -> str:
    active = active_decisions(ledger)
    if not active:
        return ""
    lines = [heading, ""]
    for decision in active:
        lines.append(f"1. **{decision.id}**: {decision.statement}")
        if decision.rationale:
            lines.append(f"   - Rationale: {decision.rationale}")
    return "\n".join(lines).strip()


def build_decision_summary_text(ledger: DecisionLedger, *, title: str) -> str:
    active = active_decisions(ledger)
    lines = [f"# {title}"]
    if not active:
        lines.append("")
        lines.append("_No active decisions._")
        return "\n".join(lines) + "\n"
    lines.append("")
    for decision in active:
        lines.append(f"- {decision.id}: {decision.statement}")
    return "\n".join(lines) + "\n"


def extract_decision_statements(text: str, *, artifact_kind: str) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []

    model_candidates: list[str] = []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None

    if data is not None:
        try:
            if artifact_kind == "scope":
                scope = ScopeOutput.model_validate(data)
                model_candidates.extend(scope.user_decisions)
            elif artifact_kind == "prd":
                prd = PRD.model_validate(data)
                model_candidates.extend(prd.decisions)
            elif artifact_kind == "design":
                design = DesignDecisions.model_validate(data)
                model_candidates.extend(design.decisions)
            elif artifact_kind == "plan":
                plan = TechnicalPlan.model_validate(data)
                model_candidates.extend(plan.decisions)
            elif artifact_kind == "system-design":
                system_design = SystemDesign.model_validate(data)
                model_candidates.extend(system_design.decisions)
            elif artifact_kind == "test-plan":
                test_plan = TestPlan.model_validate(data)
                model_candidates.extend(test_plan.decisions)
        except Exception:
            model_candidates = []

    if model_candidates:
        return _dedupe_statements([_strip_decision_prefix(candidate) for candidate in model_candidates])
    return _extract_markdown_decisions(text)


async def refresh_decision_ledger(
    runner: Any,
    feature: Any,
    *,
    ledger_key: str,
    label: str,
    source_phase: str,
    artifact_kind: str,
    state: Any | None = None,
    control: dict[str, Any] | None = None,
    subfeature_slug: str = "",
    statements: list[str] | None = None,
    source_texts: list[str] | None = None,
    source_artifacts: list[tuple[str, str]] | None = None,
    summary_key: str | None = None,
    applies_to: list[str] | None = None,
) -> str:
    collected = list(statements or [])
    for text in source_texts or []:
        collected.extend(extract_decision_statements(text, artifact_kind=artifact_kind))
    for source_kind, text in source_artifacts or []:
        collected.extend(extract_decision_statements(text, artifact_kind=source_kind))

    local_control = control or load_planning_control(state=state, feature=feature)
    async with _decision_lock(runner, feature):
        existing_text = await runner.artifacts.get(ledger_key, feature=feature) or ""
        ledger = parse_decision_ledger(existing_text)
        if ledger_key == GLOBAL_DECISIONS_KEY:
            ledger.title = "Global Decision Ledger"
        changed = _merge_statements(
            ledger,
            statements=collected,
            source_phase=source_phase,
            subfeature_slug=subfeature_slug,
            applies_to=applies_to or artifact_applies_to(artifact_kind),
            control=local_control,
        )
        if changed:
            state_holder = state or SimpleNamespace(metadata={})
            await persist_planning_control(runner, feature, state_holder, local_control)

    ledger.complete = bool(ledger.decisions)
    ledger_text = to_markdown(ledger)
    await _store_decision_text(runner, feature, key=ledger_key, text=ledger_text, label=label)
    if summary_key:
        await _store_decision_text(
            runner,
            feature,
            key=summary_key,
            text=build_decision_summary_text(ledger, title=label),
            label=f"{label} Summary",
        )
    return ledger_text


async def compile_decision_ledger(
    runner: Any,
    feature: Any,
    *,
    phase_name: str,
    decomposition: SubfeatureDecomposition,
    state: Any | None = None,
    control: dict[str, Any] | None = None,
) -> str:
    local_control = control or load_planning_control(state=state, feature=feature)
    async with _decision_lock(runner, feature):
        broad_text = await runner.artifacts.get("decisions:broad", feature=feature) or ""
        broad_ledger = parse_decision_ledger(broad_text)
        global_text = await runner.artifacts.get(GLOBAL_DECISIONS_KEY, feature=feature) or ""
        global_ledger = parse_decision_ledger(global_text)
        compiled_text = await runner.artifacts.get("decisions", feature=feature) or ""
        compiled_ledger = parse_decision_ledger(compiled_text)

        sf_ledgers: list[tuple[Any, str, DecisionLedger]] = []
        for sf in decomposition.subfeatures:
            sf_text = await runner.artifacts.get(f"decisions:{sf.slug}", feature=feature) or ""
            sf_ledgers.append((sf, sf_text, parse_decision_ledger(sf_text)))

        global_ledger, global_text = await _migrate_global_decisions(
            runner,
            feature,
            compiled_ledger=compiled_ledger,
            broad_ledger=broad_ledger,
            global_ledger=global_ledger,
            sf_ledgers=[ledger for _sf, _text, ledger in sf_ledgers],
            existing_global_text=global_text,
        )

        merged: dict[str, DecisionRecord] = {}
        for ledger in [broad_ledger, *[ledger for _sf, _text, ledger in sf_ledgers], global_ledger]:
            for decision in ledger.decisions:
                merged[decision.id] = decision.model_copy(deep=True)

        parts: list[str] = []
        if broad_text:
            parts.append(f"## Broad Decisions\n\n{broad_text}")
        if global_text:
            parts.append(f"## Global Decisions\n\n{global_text}")
        for sf, sf_text, _ledger in sf_ledgers:
            if sf_text:
                parts.append(f"## Decisions: {sf.name} ({sf.slug})\n\n{sf_text}")

        merged_ledger = DecisionLedger(
            title="Decision Ledger",
            decisions=sorted(merged.values(), key=_decision_sort_key),
            complete=bool(merged),
        )
        await _write_compile_sources(
            runner,
            feature,
            filename="compile-sources-decisions.md",
            content="\n\n---\n\n".join(parts),
        )
        if state is not None and control is not None:
            await persist_planning_control(runner, feature, state, local_control)
        decisions_text = to_markdown(merged_ledger)
        await _store_decision_text(
            runner,
            feature,
            key="decisions",
            text=decisions_text,
            label="Decision Ledger",
        )
        return decisions_text


async def rebuild_canonical_decisions(
    runner: Any,
    feature: Any,
    *,
    phase_name: str,
    decomposition: SubfeatureDecomposition,
    state: Any | None = None,
    control: dict[str, Any] | None = None,
    plan_text: str = "",
    system_design_text: str = "",
) -> tuple[str, str, str]:
    decisions_text = await compile_decision_ledger(
        runner,
        feature,
        phase_name=phase_name,
        decomposition=decomposition,
        state=state,
        control=control,
    )
    updated_plan, updated_system_design = await sync_compiled_decision_mirrors(
        runner,
        feature,
        plan_text=plan_text,
        system_design_text=system_design_text,
    )
    return decisions_text, updated_plan, updated_system_design


async def sync_compiled_decision_mirrors(
    runner: Any,
    feature: Any,
    *,
    plan_text: str = "",
    system_design_text: str = "",
) -> tuple[str, str]:
    decisions_text = await runner.artifacts.get("decisions", feature=feature) or ""
    ledger = parse_decision_ledger(decisions_text)
    if not active_decisions(ledger):
        return plan_text, system_design_text

    updated_plan = plan_text or await runner.artifacts.get("plan", feature=feature) or ""
    updated_system_design = (
        system_design_text
        or await runner.artifacts.get("system-design", feature=feature)
        or ""
    )

    if updated_plan:
        updated_plan = _upsert_markdown_section(
            updated_plan,
            "## Decision Log",
            render_active_decision_log(ledger, heading="## Decision Log"),
        )
        await _store_decision_text(
            runner,
            feature,
            key="plan",
            text=updated_plan,
            label="Compiled PLAN",
            refresh_only=True,
        )

    if updated_system_design:
        updated_system_design = _sync_system_design_text(updated_system_design, ledger)
        await _store_decision_text(
            runner,
            feature,
            key="system-design",
            text=updated_system_design,
            label="System Design",
            refresh_only=True,
        )

    return updated_plan, updated_system_design


@asynccontextmanager
async def _decision_lock(runner: Any, feature: Any):
    feature_store = getattr(runner, "feature_store", None)
    if feature_store and hasattr(feature_store, "advisory_lock"):
        async with feature_store.advisory_lock(feature.id, "planning-decisions"):
            yield
        return
    yield


async def _store_decision_text(
    runner: Any,
    feature: Any,
    *,
    key: str,
    text: str,
    label: str,
    refresh_only: bool = False,
) -> None:
    await runner.artifacts.put(key, text, feature=feature)
    mirror = runner.services.get("artifact_mirror")
    if mirror:
        mirror.write_artifact(feature.id, key, text)
    hosting = runner.services.get("hosting")
    if not hosting:
        return
    if refresh_only:
        await hosting.update(feature.id, key, text)
    else:
        await hosting.push(feature.id, key, text, label)


async def _write_compile_sources(
    runner: Any,
    feature: Any,
    *,
    filename: str,
    content: str,
) -> None:
    mirror = runner.services.get("artifact_mirror")
    if not mirror:
        return
    path = Path(mirror.feature_dir(feature.id)) / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _merge_statements(
    ledger: DecisionLedger,
    *,
    statements: list[str],
    source_phase: str,
    subfeature_slug: str,
    applies_to: list[str],
    control: dict[str, Any],
) -> bool:
    changed = False
    seen = {_normalized_decision_key(decision.statement): decision for decision in ledger.decisions}
    active_by_id = {decision.id: decision for decision in active_decisions(ledger)}

    for raw_statement in _dedupe_statements(statements):
        statement = raw_statement.strip()
        if not statement:
            continue
        normalized = _normalized_decision_key(statement)
        if normalized in seen:
            continue

        supersedes = [
            decision_id
            for decision_id in _extract_referenced_decision_ids(statement)
            if decision_id in active_by_id
        ]
        for decision_id in supersedes:
            active_by_id[decision_id].status = "superseded"
            changed = True

        decision = DecisionRecord(
            id=_next_decision_id(control),
            statement=_strip_supersession_prefix(statement),
            rationale="",
            status="active",
            supersedes=supersedes,
            source_phase=source_phase,
            subfeature_slug=subfeature_slug,
            applies_to=list(applies_to),
        )
        ledger.decisions.append(decision)
        seen[normalized] = decision
        active_by_id[decision.id] = decision
        changed = True

    if changed:
        ledger.decisions = sorted(ledger.decisions, key=_decision_sort_key)
    return changed


def _normalized_decision_key(statement: str) -> str:
    return re.sub(r"\s+", " ", statement.strip().lower())


def _extract_referenced_decision_ids(statement: str) -> list[str]:
    return re.findall(r"\bD-\d+\b", statement)


def _strip_supersession_prefix(statement: str) -> str:
    return re.sub(
        r"^\s*(?:supersedes?|replace(?:s|d)?|updates?)\s+D-\d+\s*[:\-]\s*",
        "",
        statement,
        flags=re.IGNORECASE,
    ).strip()


def _next_decision_id(control: dict[str, Any]) -> str:
    current = int(control.get("decision_seq", 0) or 0) + 1
    control["decision_seq"] = current
    return f"D-{current}"


def _decision_sort_key(decision: DecisionRecord) -> tuple[int, str]:
    match = re.search(r"D-(\d+)", decision.id)
    if match:
        return (int(match.group(1)), decision.id)
    return (10**9, decision.id)


def _dedupe_statements(statements: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for statement in statements:
        normalized = _normalized_decision_key(statement)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(statement.strip())
    return deduped


def _strip_decision_prefix(statement: str) -> str:
    cleaned = re.sub(r"^\s*\*\*(D-\d+)\*\*\s*:\s*", "", statement)
    cleaned = re.sub(r"^\s*D-\d+\s*:\s*", "", cleaned)
    return cleaned.strip()


def _extract_markdown_decisions(text: str) -> list[str]:
    sections = _markdown_sections(text)
    candidates: list[str] = []
    for heading, section_text in sections:
        if heading.lower() not in _DECISION_SECTION_NAMES:
            continue
        for line in section_text.splitlines():
            match = re.match(r"^\s*(?:[-*]|\d+\.)\s+(.*\S)\s*$", line)
            if not match:
                continue
            statement = _strip_decision_prefix(match.group(1).strip())
            candidates.append(statement)
    if candidates:
        return _dedupe_statements(candidates)

    heading_matches = re.findall(r"^###\s+(D-\d+)\s*[:\-]?\s*(.+)$", text, flags=re.MULTILINE)
    return _dedupe_statements([statement for _decision_id, statement in heading_matches])


def _markdown_sections(text: str) -> list[tuple[str, str]]:
    matches = list(re.finditer(r"^(#{2,6})\s+(.+)$", text, flags=re.MULTILINE))
    sections: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        heading = match.group(2).strip()
        heading = re.sub(r"\s*\[[^\]]+\]\s*$", "", heading)
        sections.append((heading.lower(), text[start:end].strip()))
    return sections


def _upsert_markdown_section(text: str, heading: str, section: str) -> str:
    if not section:
        return text
    pattern = re.compile(
        rf"(?ms)^{re.escape(heading)}\s*\n.*?(?=^##\s|\Z)"
    )
    if pattern.search(text):
        return pattern.sub(section.strip() + "\n\n", text).rstrip() + "\n"
    if text.endswith("\n"):
        return text.rstrip() + "\n\n" + section.strip() + "\n"
    return text + "\n\n" + section.strip() + "\n"


def _sync_system_design_text(text: str, ledger: DecisionLedger) -> str:
    active_strings = [f"{decision.id}: {decision.statement}" for decision in active_decisions(ledger)]
    try:
        data = json.loads(text)
        model = SystemDesign.model_validate(data)
        model.decisions = active_strings
        return model.model_dump_json(indent=2)
    except Exception:
        section = render_active_decision_log(ledger, heading="## Decision Log")
        return _upsert_markdown_section(text, "## Decision Log", section)


async def _migrate_global_decisions(
    runner: Any,
    feature: Any,
    *,
    compiled_ledger: DecisionLedger,
    broad_ledger: DecisionLedger,
    global_ledger: DecisionLedger,
    sf_ledgers: list[DecisionLedger],
    existing_global_text: str,
) -> tuple[DecisionLedger, str]:
    source_ids = {
        decision.id
        for ledger in [broad_ledger, global_ledger, *sf_ledgers]
        for decision in ledger.decisions
    }
    migrated = [
        decision.model_copy(deep=True)
        for decision in compiled_ledger.decisions
        if decision.id not in source_ids
    ]
    if not migrated:
        return global_ledger, existing_global_text

    merged = {decision.id: decision.model_copy(deep=True) for decision in global_ledger.decisions}
    for decision in migrated:
        merged[decision.id] = decision
    global_ledger = DecisionLedger(
        title="Global Decision Ledger",
        decisions=sorted(merged.values(), key=_decision_sort_key),
        complete=bool(merged),
    )
    global_text = to_markdown(global_ledger)
    await _store_decision_text(
        runner,
        feature,
        key=GLOBAL_DECISIONS_KEY,
        text=global_text,
        label="Global Decision Ledger",
    )
    return global_ledger, global_text
