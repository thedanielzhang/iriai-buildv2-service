from __future__ import annotations

import hashlib
import html
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ...models.outputs import (
    AcceptanceCriterion,
    APICallPath,
    APICallStep,
    APIEndpoint,
    ArchitecturalRisk,
    ArtifactAuditIssue,
    ArtifactAuditReport,
    ArtifactBackfillArtifactStatus,
    ArtifactBackfillStatus,
    ArtifactBackfillSubfeatureStatus,
    ChecklistItem,
    ChunkMeta,
    ComponentDef,
    DecisionLedger,
    DecisionRecord,
    DesignDecisions,
    EdgeCaseItem,
    Entity,
    EntityField,
    EntityRelation,
    FileScope,
    ImplementationStep,
    Journey,
    JourneyVerification,
    JourneyVerifyStep,
    JourneyStep,
    JourneyUXAnnotation,
    PRD,
    ServiceConnection,
    ServiceNode,
    Requirement,
    SharedPlanningIndex,
    SliceInputChunkSet,
    StructuredArtifact,
    StructuredArtifactEnvelope,
    SubfeatureDecomposition,
    SubfeaturePlanningIndex,
    TestAcceptanceCriterion,
    TestPlan,
    TestScenario,
    TraceRefs,
    TechnicalPlan,
    VerifyBlock,
    VerifiableState,
    PlanningChunkNode,
    PlanningChunkEdge,
    SystemDesign,
)
from ...services.artifacts import _key_to_path, _sd_source_path, structured_artifact_key
from ...services.markdown import to_markdown
from ._decisions import parse_decision_ledger

logger = logging.getLogger(__name__)

_REQ_ID_PATTERN = re.compile(r"(?<![A-Za-z0-9])REQ-[A-Za-z0-9][A-Za-z0-9()./-]*(?![A-Za-z0-9])")
_JOURNEY_ID_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])J-[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?![A-Za-z0-9-])"
)
_JOURNEY_STEP_ID_PATTERN = re.compile(r"(?<![A-Za-z0-9])J-[A-Za-z0-9][A-Za-z0-9-]*#step-[A-Za-z0-9-]+(?![A-Za-z0-9])", re.IGNORECASE)
_STEP_ID_PATTERN = re.compile(r"(?<![A-Za-z0-9])STEP-[A-Za-z0-9][A-Za-z0-9-]*(?![A-Za-z0-9])")
_AC_ID_PATTERN = re.compile(r"(?<![A-Za-z0-9])AC-[A-Za-z0-9][A-Za-z0-9-]*(?![A-Za-z0-9])")
_DECISION_ID_PATTERN = re.compile(r"(?<![A-Za-z0-9])D-[A-Za-z0-9][A-Za-z0-9-]*(?![A-Za-z0-9])")
_VERIFIABLE_STATE_ID_PATTERN = re.compile(r"\b[A-Za-z][A-Za-z0-9_-]*#[A-Za-z0-9_-]+\b")
_NFR_PATTERN = re.compile(r"(?<![A-Za-z0-9])NFR(?:-[A-Za-z0-9][A-Za-z0-9-]*)?(?:\s*\([^)]+\))?(?![A-Za-z0-9])")
_H1_PATTERN = re.compile(r"(?m)^#\s+(.+?)\s*$")
_NEXT_H2_HEADING = re.compile(r"(?m)^##\s+\S")
_NEXT_H3_HEADING = re.compile(r"(?m)^###\s+\S")
_STEP_HEADING_PATTERN = re.compile(r"(?m)^###\s+(STEP-[A-Za-z0-9-]+)\s*:?\s*(.*)$")
_PRD_JOURNEY_HEADING_PATTERN = re.compile(
    r"(?m)^#{3,4}\s+(?:Journey\s+)?(J-[A-Za-z0-9.-]+)(?:\s+\([^)]*\))?:\s*(.+)$"
)
_METADATA_LINE_PATTERN = re.compile(r"(?m)^\s*-\s+\*\*([^*]+)\*\*\s*(.+?)\s*$")
_PLAIN_METADATA_LINE_PATTERN = re.compile(r"(?m)^\s*-\s+([A-Za-z][A-Za-z0-9_ /-]+):\s*(.+?)\s*$")
_MARKDOWN_AC_BLOCK_PATTERN = re.compile(
    r"(?ms)^\s*-\s+\*\*(AC-[A-Za-z0-9][A-Za-z0-9-]*)\*\*\s*[—-]\s*(.*?)\s*$"
)
_MARKDOWN_SCENARIO_HEADING_PATTERN = re.compile(r"(?m)^###\s+(.+?)\s*$")
_BOLD_METADATA_SECTION_PATTERN = re.compile(
    r"(?ms)^(?:\s*-\s+)?\*\*([^*]+?)\s*(?::|\.)\*\*\s*(.*?)(?=^\s*(?:-\s+)?\*\*[^*]+?\s*(?::|\.)\*\*|\Z)"
)
_INLINE_BOLD_METADATA_PATTERN = re.compile(r"\*\*([^*]+?)\s*(?::|\.)\*\*")
_NUMERIC_ID_RANGE_PATTERN = re.compile(
    r"\b(?P<start>[A-Za-z][A-Za-z0-9-]*\d+)\s*(?:\.\.|…|through|to|–|—)\s*(?P<end>[A-Za-z][A-Za-z0-9-]*\d+)\b",
    re.IGNORECASE,
)
_INLINE_ID_RANGE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?P<start>[A-Za-z][A-Za-z0-9./()_-]*)\s*(?:\.\.|…|through|to|–|—)\s*(?P<end>[A-Za-z][A-Za-z0-9./()_-]*)(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_NUMERIC_ID_SERIES_PATTERN = re.compile(
    r"\b(?P<base>[A-Za-z][A-Za-z0-9-]*?)(?P<first>\d+)(?:/(?P<rest>\d+(?:/\d+)+))\b"
)

SOURCE_ARTIFACT_KEYS = {
    "decomposition",
    "prd",
    "design",
    "plan",
    "system-design",
    "test-plan",
    "decisions",
}
SOURCE_ARTIFACT_PREFIXES = {
    "decomposition",
    "prd",
    "design",
    "plan",
    "system-design",
    "test-plan",
    "decisions",
}
SHARED_SOURCE_ARTIFACT_KEYS = {
    "decomposition",
    "prd:broad",
    "design:broad",
    "plan:broad",
    "decisions:broad",
    "decisions:global",
}


@dataclass(slots=True)
class NormalizedArtifactResult:
    sidecar_key: str
    sidecar: StructuredArtifact[Any]
    parity_messages: list[str]
    issues: list[ArtifactAuditIssue]


def is_source_artifact_key(key: str) -> bool:
    if key in SOURCE_ARTIFACT_KEYS:
        return True
    if ":" not in key:
        return False
    prefix, _slug = key.split(":", 1)
    return prefix in SOURCE_ARTIFACT_PREFIXES


def artifact_family_for_key(key: str) -> str:
    return key.split(":", 1)[0]


def is_shared_source_artifact_key(key: str) -> bool:
    return key in SHARED_SOURCE_ARTIFACT_KEYS


def _content_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _json_digest(payload: Any) -> str:
    return _content_digest(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def _mirror_delete_artifact(runner: Any, feature: Any, artifact_key: str) -> None:
    mirror = runner.services.get("artifact_mirror")
    if mirror and hasattr(mirror, "delete_artifact"):
        mirror.delete_artifact(feature.id, artifact_key)


def _markdown_h2_body(markdown: str, heading_title: str) -> str:
    if not markdown.strip():
        return ""
    heading_pattern = re.compile(
        rf"(?m)^##\s+(?:\d+(?:\.\d+)*\.?\s+)?{re.escape(heading_title)}\b.*$"
    )
    match = heading_pattern.search(markdown)
    if match is None:
        return ""
    body_start = match.end()
    next_heading = _NEXT_H2_HEADING.search(markdown, body_start)
    body_end = next_heading.start() if next_heading else len(markdown)
    return markdown[body_start:body_end].strip()


def _strip_markdown_ticks(value: str) -> str:
    stripped = value.strip()
    if stripped.startswith("`") and stripped.endswith("`") and len(stripped) >= 2:
        return stripped[1:-1].strip()
    return stripped


def _strip_markdown_inline_formatting(value: str) -> str:
    stripped = _strip_markdown_ticks(value.strip())
    while True:
        updated = stripped
        for opener, closer in (("**", "**"), ("__", "__"), ("*", "*"), ("_", "_")):
            if updated.startswith(opener) and updated.endswith(closer) and len(updated) > len(opener) + len(closer):
                updated = updated[len(opener) : len(updated) - len(closer)].strip()
        if updated == stripped:
            return updated
        stripped = updated


def _parse_markdown_metadata_map(block_text: str) -> dict[str, str]:
    metadata: dict[str, list[str]] = {}

    def _append(label: str, value: str) -> None:
        normalized_label = label.strip().lower().rstrip(".:").strip()
        normalized_value = _strip_markdown_ticks(value.strip())
        if not normalized_label or not normalized_value:
            return
        metadata.setdefault(normalized_label, []).append(normalized_value)

    def _split_nested_metadata(label: str, value: str) -> list[tuple[str, str]]:
        matches = list(_INLINE_BOLD_METADATA_PATTERN.finditer(value))
        if not matches:
            return [(label, value)]
        segments: list[tuple[str, str]] = []
        prefix = value[:matches[0].start()].strip()
        if prefix:
            segments.append((label, prefix))
        for index, match in enumerate(matches):
            start = match.end()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(value)
            segments.append((match.group(1), value[start:end].strip()))
        return segments

    for raw_label, raw_value in _BOLD_METADATA_SECTION_PATTERN.findall(block_text):
        for label, value in _split_nested_metadata(raw_label, raw_value):
            _append(label, value)

    for raw_label, raw_value in _PLAIN_METADATA_LINE_PATTERN.findall(block_text):
        _append(raw_label, raw_value)
    return {
        key: "\n".join(values).strip()
        for key, values in metadata.items()
        if values
    }


def _parse_markdown_bullets(section_text: str) -> list[str]:
    if not section_text.strip():
        return []
    items: list[str] = []
    for raw_line in section_text.splitlines():
        line = raw_line.strip()
        if not line.startswith("- "):
            continue
        item = re.sub(r"^\[[ xX]\]\s*", "", line[2:].strip())
        item = _strip_markdown_ticks(item)
        if item:
            items.append(item)
    return items


def _expand_shorthand_id_list(raw_value: str) -> list[str]:
    values: list[str] = []
    last_full_id = ""
    for raw_part in raw_value.split(","):
        token = _strip_markdown_ticks(raw_part).strip()
        if not token:
            continue
        if token.startswith("-") and last_full_id:
            prefix_match = re.match(r"^(.*-)[A-Za-z0-9]+$", last_full_id)
            if prefix_match is not None:
                token = prefix_match.group(1) + token[1:].strip()
        values.append(token)
        if "-" in token:
            last_full_id = token
    return values


def acceptance_alias_map(canonical_ids: list[str]) -> dict[str, str]:
    alias_map: dict[str, str] = {}
    ambiguous: set[str] = set()
    for canonical_id in canonical_ids:
        if not canonical_id:
            continue
        aliases = {canonical_id}
        suffix_match = re.match(r"^(AC-[A-Za-z0-9][A-Za-z0-9-]*-)(\d+)$", canonical_id)
        if suffix_match is not None:
            aliases.add(f"AC-{suffix_match.group(2)}")
        for alias in aliases:
            existing = alias_map.get(alias)
            if existing is not None and existing != canonical_id:
                ambiguous.add(alias)
                continue
            alias_map[alias] = canonical_id
    for alias in ambiguous:
        alias_map.pop(alias, None)
    return alias_map


def canonicalize_acceptance_ids(ids: list[str], canonical_ids: list[str]) -> list[str]:
    if not ids:
        return []
    alias_map = acceptance_alias_map(canonical_ids)
    canonicalized: list[str] = []
    for ac_id in ids:
        canonicalized.append(alias_map.get(ac_id, ac_id))
    return sorted(dict.fromkeys(canonicalized))


def _requirement_parts(token: str) -> tuple[str, str, str] | None:
    match = re.match(
        r"^(REQ-(?:[A-Za-z0-9]+-)*)((?:\d+))(?:\(([A-Za-z])\)|([A-Za-z]))?$",
        token,
        re.IGNORECASE,
    )
    if match is None:
        return None
    prefix = match.group(1)
    number = match.group(2)
    suffix = (match.group(3) or match.group(4) or "").lower()
    return prefix, number, suffix


def _requirement_token_forms(token: str) -> list[str]:
    normalized = _canonicalize_numeric_suffix(
        _strip_markdown_inline_formatting(_strip_markdown_ticks(token.strip()))
    )
    parts = _requirement_parts(normalized)
    if parts is None:
        return [normalized] if normalized else []
    prefix, number, suffix = parts
    forms: list[str] = [f"{prefix}{number}"]
    if suffix:
        forms.insert(0, f"{prefix}{number}({suffix})")
        forms.insert(0, f"{prefix}{number}{suffix}")
        forms.append(f"{prefix}{number}")
    if prefix != "REQ-":
        short_prefix = "REQ-"
        forms.append(f"{short_prefix}{number}")
        if suffix:
            forms.insert(0, f"{short_prefix}{number}({suffix})")
            forms.insert(0, f"{short_prefix}{number}{suffix}")
    return [form for form in dict.fromkeys(forms) if form]


def requirement_alias_map(canonical_ids: list[str]) -> dict[str, str]:
    alias_map: dict[str, str] = {}
    ambiguous: set[str] = set()

    def _register(alias: str, canonical_id: str) -> None:
        if not alias:
            return
        existing = alias_map.get(alias)
        if existing is not None and existing != canonical_id:
            ambiguous.add(alias)
            return
        alias_map[alias] = canonical_id

    for canonical_id in canonical_ids:
        for alias in _requirement_token_forms(canonical_id):
            _register(alias, canonical_id)

    for alias in ambiguous:
        alias_map.pop(alias, None)
    return alias_map


def canonicalize_requirement_ids(ids: list[str], canonical_ids: list[str]) -> list[str]:
    if not ids:
        return []
    alias_map = requirement_alias_map(canonical_ids)
    canonicalized: list[str] = []
    for requirement_id in ids:
        normalized = _canonicalize_numeric_suffix(
            _strip_markdown_inline_formatting(_strip_markdown_ticks(requirement_id.strip()))
        )
        resolved = ""
        for alias in _requirement_token_forms(normalized):
            resolved = alias_map.get(alias, "")
            if resolved:
                break
        canonicalized.append(resolved or normalized)
    return sorted(dict.fromkeys(item for item in canonicalized if item))


def journey_alias_map(canonical_ids: list[str]) -> dict[str, str]:
    return {
        _strip_markdown_inline_formatting(_strip_markdown_ticks(journey_id.strip())): journey_id
        for journey_id in canonical_ids
        if journey_id
    }


def canonicalize_journey_ids(ids: list[str], canonical_ids: list[str]) -> list[str]:
    if not ids:
        return []
    alias_map = journey_alias_map(canonical_ids)
    canonicalized: list[str] = []
    for journey_id in ids:
        normalized = _strip_markdown_inline_formatting(_strip_markdown_ticks(journey_id.strip()))
        canonicalized.append(alias_map.get(normalized, normalized))
    return sorted(dict.fromkeys(item for item in canonicalized if item))


def verifiable_state_alias_map(canonical_ids: list[str]) -> dict[str, str]:
    return {
        _strip_markdown_inline_formatting(_strip_markdown_ticks(state_id.strip())): state_id
        for state_id in canonical_ids
        if state_id
    }


def canonicalize_verifiable_state_ids(ids: list[str], canonical_ids: list[str]) -> list[str]:
    if not ids:
        return []
    alias_map = verifiable_state_alias_map(canonical_ids)
    canonicalized: list[str] = []
    for state_id in ids:
        normalized = _strip_markdown_inline_formatting(_strip_markdown_ticks(state_id.strip()))
        canonicalized.append(alias_map.get(normalized, normalized))
    return sorted(dict.fromkeys(item for item in canonicalized if item))


def _metadata_value(metadata: dict[str, str], *aliases: str) -> str:
    values = [
        metadata[alias.strip().lower().rstrip(".:").strip()]
        for alias in aliases
        if alias.strip().lower().rstrip(".:").strip() in metadata
    ]
    return "\n".join(value for value in values if value).strip()


def _metadata_lines(raw_value: str) -> list[str]:
    if not raw_value.strip():
        return []
    bullet_lines = [
        _strip_markdown_inline_formatting(match.group(1).strip())
        for match in re.finditer(r"(?m)^\s*-\s+(.+?)\s*$", raw_value)
    ]
    if bullet_lines:
        return [line for line in bullet_lines if line]
    return [line.strip() for line in raw_value.splitlines() if line.strip()]


def _split_numeric_suffix(token: str) -> tuple[str, int, int] | None:
    match = re.match(r"^(?P<stem>.*?)(?P<number>\d+)$", token)
    if match is None:
        return None
    number_text = match.group("number")
    return match.group("stem"), int(number_text), len(number_text)


def _canonicalize_numeric_suffix(token: str) -> str:
    while token and token[-1] in ".,;:":
        token = token[:-1]
    while token.endswith(")") and token.count(")") > token.count("("):
        token = token[:-1]
    while token.endswith("]") and token.count("]") > token.count("["):
        token = token[:-1]
    parts = _split_numeric_suffix(token)
    if parts is None:
        return token
    stem, number, _width = parts
    return f"{stem}{number}"


def _split_structured_id(token: str) -> tuple[str, int, str] | None:
    match = re.match(r"^(?P<stem>.*?)(?P<number>\d+)(?P<suffix>\([a-z]\)|[a-z])?$", token, re.IGNORECASE)
    if match is None:
        return None
    return match.group("stem"), int(match.group("number")), (match.group("suffix") or "")


def _suffix_index(suffix: str) -> tuple[str, int] | None:
    if not suffix:
        return None
    normalized = suffix.strip().lower()
    if normalized.startswith("(") and normalized.endswith(")") and len(normalized) == 3:
        return "paren", ord(normalized[1]) - ord("a")
    if len(normalized) == 1 and "a" <= normalized <= "z":
        return "bare", ord(normalized) - ord("a")
    return None


def _format_structured_id(stem: str, number: int, suffix_index: int | None, suffix_style: str) -> str:
    if suffix_index is None:
        return f"{stem}{number}"
    suffix_char = chr(ord("a") + suffix_index)
    if suffix_style == "paren":
        return f"{stem}{number}({suffix_char})"
    return f"{stem}{number}{suffix_char}"


def _expand_structured_id_range(start: str, end: str) -> list[str]:
    start_token = _canonicalize_numeric_suffix(start.strip())
    end_token = _canonicalize_numeric_suffix(end.strip())
    start_parts = _split_structured_id(start_token)
    end_parts = _split_structured_id(end_token)
    if start_parts is None or end_parts is None:
        return []
    start_stem, start_number, start_suffix = start_parts
    end_stem, end_number, end_suffix = end_parts
    if start_stem != end_stem or end_number < start_number:
        return []

    start_suffix_info = _suffix_index(start_suffix)
    end_suffix_info = _suffix_index(end_suffix)
    result: list[str] = []

    if start_number == end_number:
        if start_suffix_info is None and end_suffix_info is None:
            return [f"{start_stem}{start_number}"]
        if start_suffix_info is None and end_suffix_info is not None:
            end_style, end_index = end_suffix_info
            result.append(f"{start_stem}{start_number}")
            result.extend(
                _format_structured_id(start_stem, start_number, index, end_style)
                for index in range(0, end_index + 1)
            )
            return result
        if start_suffix_info is not None and end_suffix_info is not None:
            start_style, start_index = start_suffix_info
            end_style, end_index = end_suffix_info
            if start_style != end_style or end_index < start_index:
                return []
            return [
                _format_structured_id(start_stem, start_number, index, start_style)
                for index in range(start_index, end_index + 1)
            ]
        return []

    if start_suffix_info is not None:
        return []
    if end_number - start_number > 200:
        return []

    result.extend(f"{start_stem}{number}" for number in range(start_number, end_number))
    result.append(f"{end_stem}{end_number}")
    if end_suffix_info is not None:
        end_style, end_index = end_suffix_info
        result.extend(
            _format_structured_id(end_stem, end_number, index, end_style)
            for index in range(0, end_index + 1)
        )
    return result


def _expand_numeric_range(start: str, end: str) -> list[str]:
    start_parts = _split_numeric_suffix(start)
    end_parts = _split_numeric_suffix(end)
    if start_parts is None or end_parts is None:
        return []
    start_stem, start_number, start_width = start_parts
    end_stem, end_number, end_width = end_parts
    if start_stem != end_stem or end_number < start_number:
        return []
    if end_number - start_number > 200:
        return []
    del start_width, end_width
    return [f"{start_stem}{number}" for number in range(start_number, end_number + 1)]


def _extract_ids_from_text(text: str, pattern: re.Pattern[str]) -> list[str]:
    normalized = _strip_markdown_ticks(_strip_markdown_inline_formatting(text))
    values: set[str] = set()
    for token in pattern.findall(normalized):
        stripped = _strip_markdown_inline_formatting(token)
        if ".." in stripped or "…" in stripped:
            expanded = _expand_structured_id_range(*re.split(r"\s*(?:\.\.|…)\s*", stripped, maxsplit=1))
            if expanded and all(pattern.fullmatch(item) for item in expanded):
                values.update(_canonicalize_numeric_suffix(item) for item in expanded)
                continue
        if "/" in stripped:
            series_match = _NUMERIC_ID_SERIES_PATTERN.fullmatch(stripped)
            if series_match is not None:
                series = [f"{series_match.group('base')}{series_match.group('first')}"] + [
                    f"{series_match.group('base')}{part}"
                    for part in series_match.group("rest").split("/")
                ]
                if all(pattern.fullmatch(item) for item in series):
                    values.update(_canonicalize_numeric_suffix(item) for item in series)
                    continue
        values.add(_canonicalize_numeric_suffix(stripped))
    for match in _NUMERIC_ID_RANGE_PATTERN.finditer(normalized):
        start = _strip_markdown_inline_formatting(match.group("start"))
        end = _strip_markdown_inline_formatting(match.group("end"))
        if pattern.fullmatch(start) and pattern.fullmatch(end):
            values.update(_expand_numeric_range(start, end))
    for match in _INLINE_ID_RANGE_PATTERN.finditer(normalized):
        expanded = _expand_structured_id_range(match.group("start"), match.group("end"))
        if expanded and all(pattern.fullmatch(item) for item in expanded):
            values.update(_canonicalize_numeric_suffix(item) for item in expanded)
    for match in _NUMERIC_ID_SERIES_PATTERN.finditer(normalized):
        base = match.group("base")
        series = [f"{base}{match.group('first')}"] + [f"{base}{part}" for part in match.group("rest").split("/")]
        if all(pattern.fullmatch(item) for item in series):
            values.update(series)
    return sorted(values)


def _normalize_step_id(step_id: str) -> str:
    candidate = step_id.strip().upper()
    if candidate.startswith("STEP-"):
        return candidate
    return f"STEP-{candidate.removeprefix('#STEP-').removeprefix('#')}"


def _scope_parts(artifact_key: str) -> tuple[str, str]:
    if artifact_key == "decomposition":
        return "root", ""
    if ":" not in artifact_key:
        return "root", ""
    prefix, slug = artifact_key.split(":", 1)
    if slug == "broad":
        return "broad", ""
    if slug == "global":
        return "global", ""
    if prefix == "decomposition":
        return "root", ""
    return "subfeature", slug


def _chunkify(
    chunk_type: str,
    artifact_key: str,
    stable_id: str,
    *,
    order: int = 0,
    source_heading: str = "",
    source_line_start: int = 0,
    source_line_end: int = 0,
    payload: Any,
) -> ChunkMeta:
    return ChunkMeta(
        chunk_id=f"{chunk_type}:{artifact_key}:{stable_id}",
        chunk_type=chunk_type,
        order=order,
        content_digest=_json_digest(payload),
        source_heading=source_heading,
        source_line_start=source_line_start,
        source_line_end=source_line_end,
    )


def _rechunk(
    existing: ChunkMeta | None,
    chunk_type: str,
    artifact_key: str,
    stable_id: str,
    *,
    order: int = 0,
    source_heading: str = "",
    source_line_start: int = 0,
    source_line_end: int = 0,
    payload: Any,
) -> ChunkMeta:
    existing = existing or ChunkMeta()
    return _chunkify(
        chunk_type,
        artifact_key,
        stable_id,
        order=order or existing.order,
        source_heading=existing.source_heading or source_heading,
        source_line_start=existing.source_line_start or source_line_start,
        source_line_end=existing.source_line_end or source_line_end,
        payload=payload,
    )


def _normalize_trace_refs(raw_value: str) -> TraceRefs:
    refs = TraceRefs()
    normalized = _strip_markdown_ticks(raw_value)
    refs.requirement_ids = _extract_ids_from_text(normalized, _REQ_ID_PATTERN)
    refs.journey_ids = _extract_ids_from_text(normalized, _JOURNEY_ID_PATTERN)
    refs.journey_step_ids = [_normalize_step_id(step_id) for step_id in _extract_ids_from_text(normalized, _STEP_ID_PATTERN)]
    decision_tokens = _extract_ids_from_text(normalized, _DECISION_ID_PATTERN)
    refs.decision_ids = sorted(token for token in decision_tokens if re.match(r"^D-\d", token))
    refs.decision_aliases = sorted(token for token in decision_tokens if not re.match(r"^D-\d", token))
    refs.nfr_ids = sorted(dict.fromkeys(match.strip() for match in _NFR_PATTERN.findall(normalized)))
    refs.verifiable_state_ids = _extract_ids_from_text(normalized, _VERIFIABLE_STATE_ID_PATTERN)
    refs.acceptance_criterion_ids = _extract_ids_from_text(normalized, _AC_ID_PATTERN)
    direct_tokens = {
        *refs.requirement_ids,
        *refs.journey_ids,
        *refs.journey_step_ids,
        *refs.decision_ids,
        *refs.decision_aliases,
        *refs.nfr_ids,
        *refs.verifiable_state_ids,
        *refs.acceptance_criterion_ids,
    }
    for token in [part.strip() for part in normalized.split(",") if part.strip()]:
        stripped = _strip_markdown_ticks(token)
        if stripped and stripped not in direct_tokens and not any(item in stripped for item in direct_tokens):
            refs.notes.append(stripped)
    refs.notes = sorted(dict.fromkeys(refs.notes))
    return refs


def _merge_trace_refs(*refs_items: TraceRefs) -> TraceRefs:
    merged = TraceRefs()
    for refs in refs_items:
        merged.requirement_ids.extend(refs.requirement_ids)
        merged.journey_ids.extend(refs.journey_ids)
        merged.journey_step_ids.extend(refs.journey_step_ids)
        merged.decision_ids.extend(refs.decision_ids)
        merged.decision_aliases.extend(refs.decision_aliases)
        merged.nfr_ids.extend(refs.nfr_ids)
        merged.verifiable_state_ids.extend(refs.verifiable_state_ids)
        merged.acceptance_criterion_ids.extend(refs.acceptance_criterion_ids)
        merged.notes.extend(refs.notes)
    for field_name in TraceRefs.model_fields:
        setattr(merged, field_name, sorted(dict.fromkeys(getattr(merged, field_name))))
    return merged


def _table_rows(section_text: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for raw_line in section_text.splitlines():
        line = raw_line.strip()
        if not line.startswith("|") or line.count("|") < 2:
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if not cells or all(set(cell) <= {"-"} for cell in cells):
            continue
        rows.append(cells)
    return rows


def _infer_requirement_category(current_heading: str, metadata_tokens: list[str]) -> str:
    normalized_heading = current_heading.lower()
    known_categories = {
        "functional",
        "non-functional",
        "security",
        "performance",
        "reliability",
        "usability",
        "observability",
    }
    for token in metadata_tokens:
        if token in known_categories:
            return token
    if "non-functional" in normalized_heading:
        return "non-functional"
    if "security" in normalized_heading:
        return "security"
    if "performance" in normalized_heading:
        return "performance"
    if "observability" in normalized_heading:
        return "observability"
    return "functional"


def _parse_prd_prose_requirement_line(
    line: str,
    *,
    current_heading: str,
    seen_ids: set[str],
) -> tuple[list[Requirement], bool]:
    stripped = line.strip()
    if not stripped:
        return [], False

    stripped = re.sub(r"^(?:\d+[a-z]?\.?\s+|-\s+)", "", stripped)
    if not stripped.startswith(("**REQ-", "REQ-")):
        return [], False

    head = stripped
    tail = ""
    if stripped.startswith("**"):
        end = stripped.find("**", 2)
        if end != -1:
            head = stripped[2:end].strip()
            tail = stripped[end + 2 :].strip()
        else:
            head = stripped[2:].strip()

    requirement_ids = [
        requirement_id
        for requirement_id in _extract_ids_from_text(head, _REQ_ID_PATTERN)
        if requirement_id not in seen_ids
    ]
    if not requirement_ids:
        return [], False

    metadata_tokens: list[str] = []
    paren_match = re.search(r"\(([^)]*)\)", head)
    if paren_match is not None:
        metadata_tokens.extend(
            token.strip().lower()
            for token in paren_match.group(1).split(",")
            if token.strip()
        )

    label_text = head
    for requirement_id in requirement_ids:
        label_text = label_text.replace(requirement_id, "", 1)
    label_text = re.sub(r"\(([^)]*)\)", "", label_text).strip()
    label_text = label_text.lstrip("—-:").strip()
    label_text = label_text.rstrip(".").strip()

    tail = re.sub(r"^(?:\[[^\]]+\]\s*)+", "", tail).strip()
    tail = tail.lstrip("—-:.").strip()

    description_parts = [part for part in (label_text, tail) if part]
    description = " ".join(description_parts).strip()
    priority = next((token for token in metadata_tokens if token in {"must", "should", "could"}), "must")
    category = _infer_requirement_category(current_heading, metadata_tokens)
    return (
        [
            Requirement(
                id=requirement_id,
                category=category,
                priority=priority,
                description=description,
            )
            for requirement_id in requirement_ids
        ],
        True,
    )


def _parse_prd_requirement_entries(section_text: str) -> list[Requirement]:
    requirements: list[Requirement] = []
    seen_ids: set[str] = set()
    for row in _table_rows(section_text):
        if len(row) < 4 or row[0] == "ID":
            continue
        requirement_id = _strip_markdown_inline_formatting(row[0])
        if not _REQ_ID_PATTERN.fullmatch(requirement_id):
            continue
        requirement = Requirement(
            id=requirement_id,
            category=_strip_markdown_inline_formatting(row[1]) or "functional",
            priority=_strip_markdown_inline_formatting(row[2]) or "must",
            description=_strip_markdown_inline_formatting(row[3]),
        )
        requirements.append(requirement)
        seen_ids.add(requirement_id)

    current_heading = ""
    current_requirements: list[Requirement] = []
    for raw_line in section_text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("### "):
            current_heading = stripped[4:].strip()
            if current_requirements:
                requirements.extend(current_requirements)
                seen_ids.update(requirement.id for requirement in current_requirements)
                current_requirements = []
            continue
        parsed_requirements, matched = _parse_prd_prose_requirement_line(
            line,
            current_heading=current_heading,
            seen_ids=seen_ids,
        )
        if matched:
            if current_requirements:
                requirements.extend(current_requirements)
                seen_ids.update(requirement.id for requirement in current_requirements)
                current_requirements = []
            current_requirements = parsed_requirements
            continue
        if current_requirements and not stripped.startswith("## "):
            for requirement in current_requirements:
                requirement.description = (
                    f"{requirement.description} {stripped}".strip()
                    if requirement.description
                    else stripped
                )

    if current_requirements:
        requirements.extend(current_requirements)
        seen_ids.update(requirement.id for requirement in current_requirements)
    return requirements


def _source_plan_step_ids(markdown: str) -> list[str]:
    steps_section = _markdown_h2_body(markdown, "Implementation Steps") or markdown
    return sorted(
        {
            _normalize_step_id(match.group(1))
            for match in _STEP_HEADING_PATTERN.finditer(steps_section)
        }
    )


def _source_prd_requirement_ids(markdown: str) -> list[str]:
    requirements_section = _markdown_h2_body(markdown, "Requirements")
    return sorted(
        requirement.id
        for requirement in _parse_prd_requirement_entries(requirements_section)
        if requirement.id
    )


def _source_prd_journey_ids(markdown: str) -> list[str]:
    journeys_section = _markdown_h2_body(markdown, "User Journeys")
    values: set[str] = set()
    for match in _PRD_JOURNEY_HEADING_PATTERN.finditer(journeys_section):
        values.update(_extract_ids_from_text(match.group(1), _JOURNEY_ID_PATTERN))
    return sorted(values)


def _source_decision_tokens(markdown: str) -> list[str]:
    ledger = parse_decision_ledger(markdown)
    tokens: set[str] = set()
    for decision in ledger.decisions:
        if decision.id:
            tokens.add(decision.id)
        tokens.update(alias for alias in decision.aliases if alias)
    return sorted(tokens)


def _source_test_plan_acceptance_ids(markdown: str) -> list[str]:
    section_text = _markdown_h2_body(markdown, "Acceptance Criteria")
    tokens: set[str] = {
        _strip_markdown_inline_formatting(match.group(1))
        for match in _MARKDOWN_AC_BLOCK_PATTERN.finditer(section_text)
    }
    for row in _table_rows(section_text):
        if len(row) < 5 or row[0].lower() == "ac-id":
            continue
        token = _strip_markdown_inline_formatting(row[0])
        if _AC_ID_PATTERN.fullmatch(token):
            tokens.add(token)
    return sorted(token for token in tokens if token)


def _parse_prd_from_markdown(markdown: str, artifact_key: str) -> PRD:
    prd = PRD(
        title=_H1_PATTERN.search(markdown).group(1).strip() if _H1_PATTERN.search(markdown) else "",
        overview=_markdown_h2_body(markdown, "Overview"),
        problem_statement=_markdown_h2_body(markdown, "Problem Statement"),
        target_users=_markdown_h2_body(markdown, "Target Users"),
        open_questions=_parse_markdown_bullets(_markdown_h2_body(markdown, "Open Questions")),
        decisions=_parse_markdown_bullets(_markdown_h2_body(markdown, "Decision Log")),
        out_of_scope=_parse_markdown_bullets(_markdown_h2_body(markdown, "Out of Scope")),
        complete=True,
    )

    for order, requirement in enumerate(_parse_prd_requirement_entries(_markdown_h2_body(markdown, "Requirements")), start=1):
        requirement.chunk = _chunkify("req", artifact_key, requirement.id or str(order), order=order, payload=requirement.model_dump(mode="json"))
        prd.structured_requirements.append(requirement)

    for order, row in enumerate(_table_rows(_markdown_h2_body(markdown, "Acceptance Criteria")), start=1):
        if len(row) < 5 or row[0] == "ID":
            continue
        ac = AcceptanceCriterion(
            id=_strip_markdown_inline_formatting(row[0]),
            user_action=_strip_markdown_inline_formatting(row[1]),
            expected_observation=_strip_markdown_inline_formatting(row[2]),
            not_criteria=_strip_markdown_inline_formatting(row[3]),
            requirement_ids=_extract_ids_from_text(row[4], _REQ_ID_PATTERN),
        )
        ac.chunk = _chunkify("prd-ac", artifact_key, ac.id or str(order), order=order, payload=ac.model_dump(mode="json"))
        prd.structured_acceptance_criteria.append(ac)

    journeys_section = _markdown_h2_body(markdown, "User Journeys")
    journey_matches = list(_PRD_JOURNEY_HEADING_PATTERN.finditer(journeys_section))
    for order, match in enumerate(journey_matches, start=1):
        start = match.start()
        end = journey_matches[order].start() if order < len(journey_matches) else len(journeys_section)
        block = journeys_section[start:end]
        journey_ids = _extract_ids_from_text(match.group(1), _JOURNEY_ID_PATTERN)
        name = match.group(2).strip()
        requirement_ids = _extract_ids_from_text(block, _REQ_ID_PATTERN)
        steps: list[JourneyStep] = []
        step_rows = _table_rows(block)
        for step_order, row in enumerate(step_rows, start=1):
            if len(row) < 4 or row[0] == "Step":
                continue
            step = JourneyStep(
                id=f"{journey_id}-STEP-{step_order}",
                step_number=int(row[0]) if row[0].isdigit() else step_order,
                action=row[1],
                observes=row[2],
                not_criteria=row[3],
            )
            step.chunk = _chunkify(
                "journey-step",
                artifact_key,
                f"{journey_ids[0] if journey_ids else 'journey'}:{step.id}",
                order=step_order,
                payload=step.model_dump(mode="json"),
            )
            steps.append(step)
        for offset, journey_id in enumerate(journey_ids, start=0):
            journey = Journey(
                id=journey_id,
                name=name or journey_id,
                actor=re.search(r"\*\*Actor:\*\*\s*(.+)", block).group(1).strip() if re.search(r"\*\*Actor:\*\*\s*(.+)", block) else "",
                preconditions=re.search(r"\*\*Preconditions:\*\*\s*(.+)", block).group(1).strip() if re.search(r"\*\*Preconditions:\*\*\s*(.+)", block) else "",
                path_type=re.search(rf"^###\s+(?:Journey\s+)?{re.escape(match.group(1).strip())}:.*\(([^)]+)\)", block, re.MULTILINE).group(1).strip() if re.search(rf"^###\s+(?:Journey\s+)?{re.escape(match.group(1).strip())}:.*\(([^)]+)\)", block, re.MULTILINE) else "happy",
                failure_trigger=re.search(r"\*\*Failure Trigger:\*\*\s*(.+)", block).group(1).strip() if re.search(r"\*\*Failure Trigger:\*\*\s*(.+)", block) else "",
                steps=steps,
                outcome=re.search(r"\*\*Outcome:\*\*\s*(.+)", block).group(1).strip() if re.search(r"\*\*Outcome:\*\*\s*(.+)", block) else "",
                related_journey_id=re.search(r"\*\*Related Journey:\*\*\s*(.+)", block).group(1).strip() if re.search(r"\*\*Related Journey:\*\*\s*(.+)", block) else "",
                requirement_ids=sorted(set(requirement_ids)),
            )
            journey.chunk = _chunkify(
                "journey",
                artifact_key,
                journey.id or f"{order + offset}",
                order=order + offset,
                payload=journey.model_dump(mode="json"),
            )
            prd.journeys.append(journey)

    return prd


def _parse_design_from_markdown(markdown: str, artifact_key: str) -> DesignDecisions:
    design = DesignDecisions(
        approach=_markdown_h2_body(markdown, "Approach"),
        responsive_behavior=_markdown_h2_body(markdown, "Responsive Behavior"),
        interaction_patterns=_markdown_h2_body(markdown, "Interaction Patterns"),
        accessibility_notes=_markdown_h2_body(markdown, "Accessibility Notes"),
        rationale=_markdown_h2_body(markdown, "Rationale"),
        alternatives=_parse_markdown_bullets(_markdown_h2_body(markdown, "Alternatives Considered")),
        decisions=_parse_markdown_bullets(_markdown_h2_body(markdown, "Decision Log")),
        complete=True,
    )

    components_section = _markdown_h2_body(markdown, "Component Definitions")
    for order, match in enumerate(re.finditer(r"(?m)^###\s+([A-Za-z0-9-]+):\s*(.+?)(?:\s+\(([^)]+)\))?\s*$", components_section), start=1):
        start = match.start()
        end_match = _NEXT_H3_HEADING.search(components_section, match.end())
        end = end_match.start() if end_match else len(components_section)
        block = components_section[start:end]
        component = ComponentDef(
            id=match.group(1).strip(),
            name=match.group(2).strip(),
            status=(match.group(3) or "").strip(),
            location=re.search(r"\*\*Location:\*\*\s*`?(.+?)`?$", block, re.MULTILINE).group(1).strip() if re.search(r"\*\*Location:\*\*\s*`?(.+?)`?$", block, re.MULTILINE) else "",
            description=re.search(r"\*\*Description:\*\*\s*(.+)$", block, re.MULTILINE).group(1).strip() if re.search(r"\*\*Description:\*\*\s*(.+)$", block, re.MULTILINE) else "",
            props_variants=re.search(r"\*\*Props / Variants:\*\*\s*(.+)$", block, re.MULTILINE).group(1).strip() if re.search(r"\*\*Props / Variants:\*\*\s*(.+)$", block, re.MULTILINE) else "",
            states=[item.strip() for item in re.search(r"\*\*States:\*\*\s*(.+)$", block, re.MULTILINE).group(1).split(",") if item.strip()] if re.search(r"\*\*States:\*\*\s*(.+)$", block, re.MULTILINE) else [],
        )
        component.chunk = _chunkify("component", artifact_key, component.id or str(order), order=order, payload=component.model_dump(mode="json"))
        design.component_defs.append(component)

    for order, row in enumerate(_table_rows(_markdown_h2_body(markdown, "Verifiable States")), start=1):
        if len(row) < 3 or row[0] == "Component ID":
            continue
        state_id = f"{row[0]}#{row[1]}"
        state = VerifiableState(id=state_id, component_id=row[0], state_name=row[1], visual_description=row[2])
        state.chunk = _chunkify("state", artifact_key, state.id or str(order), order=order, payload=state.model_dump(mode="json"))
        design.verifiable_states.append(state)

    return design


def _parse_technical_plan_from_markdown(markdown: str, artifact_key: str) -> TechnicalPlan:
    plan = TechnicalPlan(
        architecture=_markdown_h2_body(markdown, "Architecture"),
        decisions=_parse_markdown_bullets(_markdown_h2_body(markdown, "Decision Log")),
        testid_registry=_parse_markdown_bullets(_markdown_h2_body(markdown, "Test ID Registry")),
        complete=True,
    )
    steps_section = _markdown_h2_body(markdown, "Implementation Steps") or markdown
    matches = list(_STEP_HEADING_PATTERN.finditer(steps_section))
    for order, match in enumerate(matches, start=1):
        end = matches[order].start() if order < len(matches) else len(steps_section)
        block = steps_section[match.start():end].strip()
        metadata = _parse_markdown_metadata_map(block)
        step_id = _normalize_step_id(match.group(1))
        title = match.group(2).strip() or step_id
        objective_text = _metadata_value(metadata, "objective")
        scope_rows = _table_rows(
            re.search(r"(?s)\*\*File Scope:\*\*\s*(.+?)(?=\n\*\*|\Z)", block).group(1)
            if re.search(r"(?s)\*\*File Scope:\*\*\s*(.+?)(?=\n\*\*|\Z)", block)
            else ""
        )
        scope = [
            FileScope(path=_strip_markdown_ticks(row[0]), action=row[1])
            for row in scope_rows
            if len(row) >= 2 and row[0] != "Path"
        ]
        instructions = _metadata_value(metadata, "instructions")
        acceptance_lines = _metadata_lines(
            _metadata_value(metadata, "acceptance criteria", "acceptance", "acceptance refs")
        )
        counterexample_lines = _metadata_lines(
            _metadata_value(metadata, "counterexamples", "counterexample")
        )
        acceptance_refs = _normalize_trace_refs(
            _metadata_value(
                metadata,
                "ac refs",
                "acceptance refs",
                "acceptance ids",
                "acceptance criteria",
                "acceptance",
            )
        )
        explicit_refs = _merge_trace_refs(
            _normalize_trace_refs(
                _metadata_value(metadata, "requirements", "requirement refs", "requirement ids")
            ),
            _normalize_trace_refs(
                _metadata_value(metadata, "journeys", "journey refs", "journey ids")
            ),
            _normalize_trace_refs(
                _metadata_value(metadata, "decisions", "decision refs", "decision ids")
            ),
            _normalize_trace_refs(
                _metadata_value(metadata, "nfrs", "nfr refs", "nfr ids")
            ),
            _normalize_trace_refs(
                _metadata_value(metadata, "verifiable states", "verifiable state refs", "verifiable state ids")
            ),
            TraceRefs(
                acceptance_criterion_ids=acceptance_refs.acceptance_criterion_ids,
            ),
        )
        fallback_refs = _normalize_trace_refs(
            "\n".join(part for part in [objective_text, instructions] if part).strip()
        )
        refs = _merge_trace_refs(
            explicit_refs,
            TraceRefs(
                requirement_ids=[] if explicit_refs.requirement_ids else fallback_refs.requirement_ids,
                journey_ids=[] if explicit_refs.journey_ids else fallback_refs.journey_ids,
                decision_ids=[] if explicit_refs.decision_ids else fallback_refs.decision_ids,
                decision_aliases=[] if explicit_refs.decision_aliases else fallback_refs.decision_aliases,
                nfr_ids=[] if explicit_refs.nfr_ids else fallback_refs.nfr_ids,
                verifiable_state_ids=[] if explicit_refs.verifiable_state_ids else fallback_refs.verifiable_state_ids,
                acceptance_criterion_ids=[] if explicit_refs.acceptance_criterion_ids else fallback_refs.acceptance_criterion_ids,
            ),
        )
        owned_ac_ids = sorted(set(refs.acceptance_criterion_ids))
        if match.group(2).strip() and not instructions:
            refs.notes.append(match.group(2).strip())
        step = ImplementationStep(
            id=step_id,
            title=title,
            objective=objective_text or title,
            scope=scope,
            instructions=instructions,
            acceptance_criteria=acceptance_lines,
            counterexamples=counterexample_lines,
            requirement_ids=sorted(set(refs.requirement_ids)),
            journey_ids=sorted(set(refs.journey_ids)),
            refs=refs,
            owned_acceptance_criterion_ids=owned_ac_ids,
        )
        step.chunk = _chunkify("plan-step", artifact_key, step.id, order=order, source_heading=match.group(0).strip(), payload=step.model_dump(mode="json"))
        plan.steps.append(step)

    for order, row in enumerate(_table_rows(_markdown_h2_body(markdown, "File Manifest")), start=1):
        if len(row) < 2 or row[0] == "Path":
            continue
        plan.file_manifest.append(
            FileScope(
                path=_strip_markdown_ticks(row[0]),
                action=row[1],
            )
        )

    journey_verifications_section = (
        _markdown_h2_body(markdown, "Journey Verifications")
        or _markdown_h2_body(markdown, "Journey Verification")
    )
    journey_matches = list(
        re.finditer(r"(?m)^###\s+(?:Journey\s+)?(.+?)\s*$", journey_verifications_section)
    )
    for order, match in enumerate(journey_matches, start=1):
        end = journey_matches[order].start() if order < len(journey_matches) else len(journey_verifications_section)
        block = journey_verifications_section[match.start():end].strip()
        journey_label = _strip_markdown_ticks(match.group(1))
        journey_id = next(iter(_JOURNEY_ID_PATTERN.findall(journey_label)), journey_label)
        verify_steps: list[JourneyVerifyStep] = []
        step_matches = list(re.finditer(r"(?m)^\*\*Step\s+(\d+):\*\*\s*$", block))
        for step_order, step_match in enumerate(step_matches, start=1):
            step_end = step_matches[step_order].start() if step_order < len(step_matches) else len(block)
            step_block = block[step_match.end():step_end].strip()
            verify_rows = _table_rows(step_block)
            verify_blocks = [
                VerifyBlock(type=row[0], expectation=row[1])
                for row in verify_rows
                if len(row) >= 2 and row[0] != "Type"
            ]
            test_ids_match = re.search(r"(?m)^\*Test IDs:\*\s*(.+?)\s*$", step_block)
            data_testids = [
                _strip_markdown_ticks(item)
                for item in (test_ids_match.group(1).split(",") if test_ids_match else [])
                if _strip_markdown_ticks(item)
            ]
            step_number = int(step_match.group(1))
            verify_step = JourneyVerifyStep(
                id=f"{journey_id}-VERIFY-{step_number}",
                step_number=step_number,
                verify_blocks=verify_blocks,
                data_testids=data_testids,
            )
            verify_step.chunk = _chunkify(
                "journey-verify-step",
                artifact_key,
                verify_step.id,
                order=step_order,
                payload=verify_step.model_dump(mode="json"),
            )
            verify_steps.append(verify_step)
        journey_verification = JourneyVerification(journey_id=journey_id, steps=verify_steps)
        journey_verification.chunk = _chunkify(
            "journey-verification",
            artifact_key,
            journey_id or str(order),
            order=order,
            source_heading=match.group(0).strip(),
            payload=journey_verification.model_dump(mode="json"),
        )
        plan.journey_verifications.append(journey_verification)

    for order, row in enumerate(_table_rows(_markdown_h2_body(markdown, "Architectural Risks")), start=1):
        if len(row) < 5 or row[0] == "ID":
            continue
        affected_step_ids = [
            _normalize_step_id(item.strip())
            for item in row[4].split(",")
            if item.strip()
        ]
        risk = ArchitecturalRisk(
            id=row[0],
            severity=row[1],
            description=row[2],
            mitigation=row[3],
            affected_step_ids=affected_step_ids,
        )
        risk.chunk = _chunkify(
            "architectural-risk",
            artifact_key,
            risk.id or str(order),
            order=order,
            payload=risk.model_dump(mode="json"),
        )
        plan.architectural_risks.append(risk)
    return plan


def _parse_test_plan_from_markdown(markdown: str, artifact_key: str) -> TestPlan:
    section_text = _markdown_h2_body(markdown, "Acceptance Criteria")
    criteria: list[TestAcceptanceCriterion] = []
    matches = list(_MARKDOWN_AC_BLOCK_PATTERN.finditer(section_text))
    seen_criterion_ids: set[str] = set()
    for order, match in enumerate(matches, start=1):
        block_start = match.start()
        block_end = matches[order].start() if order < len(matches) else len(section_text)
        block = section_text[block_start:block_end]
        metadata = _parse_markdown_metadata_map(block)
        criterion_id = _strip_markdown_inline_formatting(match.group(1).strip())
        linked_journey_step_ids = sorted(
            {
                token
                for token in _JOURNEY_STEP_ID_PATTERN.findall(metadata.get("linked_journey_step_id", ""))
                if token
            }
        )
        linked_verifiable_state_ids = _extract_ids_from_text(
            metadata.get("linked_verifiable_state_id", ""),
            _VERIFIABLE_STATE_ID_PATTERN,
        )
        refs = _merge_trace_refs(
            _normalize_trace_refs(metadata.get("linked_requirement", "")),
            TraceRefs(
                journey_ids=sorted({token.split("#", 1)[0] for token in linked_journey_step_ids}),
                journey_step_ids=linked_journey_step_ids,
            ),
            TraceRefs(
                verifiable_state_ids=linked_verifiable_state_ids,
            ),
        )
        criterion = TestAcceptanceCriterion(
            id=criterion_id,
            description=match.group(2).strip(),
            linked_requirement=metadata.get("linked_requirement", ""),
            verification_method=metadata.get("verification_method", ""),
            pass_condition=metadata.get("pass_condition", ""),
            linked_verifiable_state_id=metadata.get("linked_verifiable_state_id", ""),
            linked_journey_step_id=metadata.get("linked_journey_step_id", ""),
            refs=refs,
        )
        criterion.chunk = _chunkify("test-ac", artifact_key, criterion.id or str(order), order=order, payload=criterion.model_dump(mode="json"))
        criteria.append(criterion)
        seen_criterion_ids.add(criterion.id)

    for row in _table_rows(section_text):
        if len(row) < 5 or row[0].lower() == "ac-id":
            continue
        criterion_id = _strip_markdown_inline_formatting(row[0])
        if not _AC_ID_PATTERN.fullmatch(criterion_id) or criterion_id in seen_criterion_ids:
            continue
        refs = _normalize_trace_refs(_strip_markdown_inline_formatting(row[2]))
        criterion = TestAcceptanceCriterion(
            id=criterion_id,
            description=_strip_markdown_inline_formatting(row[1]),
            linked_requirement=_strip_markdown_inline_formatting(row[2]),
            verification_method=_strip_markdown_inline_formatting(row[3]),
            pass_condition=_strip_markdown_inline_formatting(row[4]),
            refs=refs,
        )
        order = len(criteria) + 1
        criterion.chunk = _chunkify("test-ac", artifact_key, criterion.id or str(order), order=order, payload=criterion.model_dump(mode="json"))
        criteria.append(criterion)
        seen_criterion_ids.add(criterion.id)

    scenarios_section = _markdown_h2_body(markdown, "Test Scenarios")
    scenario_matches = list(_MARKDOWN_SCENARIO_HEADING_PATTERN.finditer(scenarios_section))
    scenarios: list[TestScenario] = []
    for order, match in enumerate(scenario_matches, start=1):
        block_start = match.start()
        block_end = scenario_matches[order].start() if order < len(scenario_matches) else len(scenarios_section)
        block = scenarios_section[block_start:block_end]
        metadata = _parse_markdown_metadata_map(block)
        scenario_id = f"SCENARIO-{order}"
        linked_acceptance = _expand_shorthand_id_list(metadata.get("linked_acceptance", ""))
        refs = TraceRefs(acceptance_criterion_ids=linked_acceptance[:])
        scenario = TestScenario(
            id=scenario_id,
            name=match.group(1).strip(),
            priority=(metadata.get("priority") or "p1").strip() or "p1",
            linked_acceptance=linked_acceptance,
            preconditions=[metadata["preconditions"]] if metadata.get("preconditions") else [],
            steps=[metadata["steps"]] if metadata.get("steps") else [],
            expected_outcome=metadata.get("expected_outcome", ""),
            refs=refs,
        )
        scenario.chunk = _chunkify("test-scenario", artifact_key, scenario.id, order=order, payload=scenario.model_dump(mode="json"))
        scenarios.append(scenario)

    checklist_items: list[ChecklistItem] = []
    for order, item in enumerate(_parse_markdown_bullets(_markdown_h2_body(markdown, "Verification Checklist")), start=1):
        refs = TraceRefs(acceptance_criterion_ids=sorted(_AC_ID_PATTERN.findall(item)))
        checklist_item = ChecklistItem(id=f"CHECKLIST-{order}", text=item, refs=refs)
        checklist_item.chunk = _chunkify("checklist", artifact_key, checklist_item.id, order=order, payload=checklist_item.model_dump(mode="json"))
        checklist_items.append(checklist_item)

    edge_case_items: list[EdgeCaseItem] = []
    for order, item in enumerate(_parse_markdown_bullets(_markdown_h2_body(markdown, "Edge Cases")), start=1):
        refs = TraceRefs(acceptance_criterion_ids=sorted(_AC_ID_PATTERN.findall(item)))
        edge_case_item = EdgeCaseItem(id=f"EDGE-{order}", text=item, refs=refs)
        edge_case_item.chunk = _chunkify("edge-case", artifact_key, edge_case_item.id, order=order, payload=edge_case_item.model_dump(mode="json"))
        edge_case_items.append(edge_case_item)

    return TestPlan(
        overview=_markdown_h2_body(markdown, "Overview"),
        acceptance_criteria=criteria,
        test_scenarios=scenarios,
        checklist_items=checklist_items,
        edge_case_items=edge_case_items,
        verification_checklist=[item.text for item in checklist_items],
        edge_cases=[item.text for item in edge_case_items],
        mocking_strategy=_markdown_h2_body(markdown, "Mocking Strategy"),
        test_environment=_parse_markdown_bullets(_markdown_h2_body(markdown, "Test Environment")),
        decisions=_parse_markdown_bullets(_markdown_h2_body(markdown, "Decisions")),
        complete=True,
    )


def _parse_decomposition_from_markdown(markdown: str, artifact_key: str) -> SubfeatureDecomposition:
    decomposition = SubfeatureDecomposition(
        decomposition_rationale=_markdown_h2_body(markdown, "Rationale"),
        complete=True,
    )
    subfeature_rows = _table_rows(_markdown_h2_body(markdown, "Subfeatures"))
    for order, row in enumerate(subfeature_rows, start=1):
        if len(row) < 6 or row[0] == "ID":
            continue
        if not row[0].strip() and not row[1].strip():
            rationale = row[3].removeprefix("Rationale: ").strip()
            if rationale and decomposition.subfeatures:
                decomposition.subfeatures[-1].rationale = rationale
            continue
        requirement_ids = [item.strip() for item in row[4].split(",") if item.strip()]
        journey_ids = [item.strip() for item in row[5].split(",") if item.strip()]
        from ...models.outputs import Subfeature, SubfeatureEdge  # local to avoid large import surface
        subfeature = Subfeature(
            id=_strip_markdown_inline_formatting(row[0]),
            slug=row[1].strip("`"),
            name=row[2],
            description=row[3].removeprefix("Rationale: ").strip(),
            requirement_ids=requirement_ids,
            journey_ids=journey_ids,
        )
        subfeature.chunk = _chunkify("subfeature", artifact_key, subfeature.slug or str(order), order=order, payload=subfeature.model_dump(mode="json"))
        decomposition.subfeatures.append(subfeature)

    edge_rows = _table_rows(_markdown_h2_body(markdown, "Dependencies"))
    from ...models.outputs import SubfeatureEdge  # local to avoid large import surface
    for order, row in enumerate(edge_rows, start=1):
        if len(row) < 6 or row[0] == "From":
            continue
        edge = SubfeatureEdge(
            from_subfeature=row[0].strip("`"),
            to_subfeature=row[1].strip("`"),
            interface_type=row[2],
            description=row[3],
            owner=row[4],
            data_contract=row[5],
        )
        edge.chunk = _chunkify("subfeature-edge", artifact_key, f"{edge.from_subfeature}:{edge.to_subfeature}:{order}", order=order, payload=edge.model_dump(mode="json"))
        decomposition.edges.append(edge)
    return decomposition


def _parse_system_design_from_text(text: str, artifact_key: str) -> SystemDesign:
    try:
        return SystemDesign.model_validate(json.loads(text))
    except Exception:
        overview = _markdown_h2_body(text, "Overview") or text.strip()
        return SystemDesign(
            title=_H1_PATTERN.search(text).group(1).strip() if _H1_PATTERN.search(text) else "System Design",
            overview=overview,
            decisions=_parse_markdown_bullets(
                _markdown_h2_body(text, "Decisions")
                or _markdown_h2_body(text, "Decision Log")
            ),
            risks=_parse_markdown_bullets(_markdown_h2_body(text, "Risks")),
            complete=True,
        )


def _normalize_decision_ledger(
    ledger: DecisionLedger,
    artifact_key: str,
) -> DecisionLedger:
    normalized = ledger.model_copy(deep=True)
    for order, decision in enumerate(normalized.decisions, start=1):
        aliases = set(decision.aliases)
        statement = decision.statement or ""
        aliases.update(
            alias
            for alias in re.findall(r"\bD-[A-Za-z0-9][A-Za-z0-9-]*\b", statement)
            if alias != decision.id and not re.match(r"^D-\d", alias)
        )
        normalized.decisions[order - 1].aliases = sorted(aliases)
        normalized.decisions[order - 1].chunk = _chunkify(
            "decision",
            artifact_key,
            decision.id or str(order),
            order=order,
            payload=normalized.decisions[order - 1].model_dump(mode="json"),
        )
    return normalized


def _normalize_prd_model(prd: PRD, artifact_key: str) -> PRD:
    normalized = prd.model_copy(deep=True)
    for order, requirement in enumerate(normalized.structured_requirements, start=1):
        requirement.chunk = _rechunk(
            requirement.chunk,
            "req",
            artifact_key,
            requirement.id or str(order),
            order=order,
            payload=requirement.model_dump(mode="json"),
        )
    for order, criterion in enumerate(normalized.structured_acceptance_criteria, start=1):
        criterion.chunk = _rechunk(
            criterion.chunk,
            "prd-ac",
            artifact_key,
            criterion.id or str(order),
            order=order,
            payload=criterion.model_dump(mode="json"),
        )
    for order, journey in enumerate(normalized.journeys, start=1):
        for step_order, step in enumerate(journey.steps, start=1):
            if not step.id:
                step.id = f"{journey.id}-STEP-{step_order}"
            step.chunk = _rechunk(
                step.chunk,
                "journey-step",
                artifact_key,
                f"{journey.id}:{step.id}",
                order=step_order,
                payload=step.model_dump(mode="json"),
            )
        journey.chunk = _rechunk(
            journey.chunk,
            "journey",
            artifact_key,
            journey.id or str(order),
            order=order,
            payload=journey.model_dump(mode="json"),
        )
    return normalized


def _normalize_design_model(design: DesignDecisions, artifact_key: str) -> DesignDecisions:
    normalized = design.model_copy(deep=True)
    for order, component in enumerate(normalized.component_defs, start=1):
        component.chunk = _rechunk(
            component.chunk,
            "component",
            artifact_key,
            component.id or str(order),
            order=order,
            payload=component.model_dump(mode="json"),
        )
    for order, state in enumerate(normalized.verifiable_states, start=1):
        if not state.id:
            state.id = f"{state.component_id}#{state.state_name}"
        state.chunk = _rechunk(
            state.chunk,
            "state",
            artifact_key,
            state.id,
            order=order,
            payload=state.model_dump(mode="json"),
        )
    for order, annotation in enumerate(normalized.journey_annotations, start=1):
        annotation.chunk = _rechunk(
            annotation.chunk,
            "journey-ux",
            artifact_key,
            annotation.journey_id or str(order),
            order=order,
            payload=annotation.model_dump(mode="json"),
        )
    return normalized


def _normalize_plan_model(plan: TechnicalPlan, artifact_key: str) -> TechnicalPlan:
    normalized = plan.model_copy(deep=True)
    if not normalized.file_manifest:
        normalized.file_manifest = [
            FileScope(path=path, action="create")
            for path in normalized.files_to_create
        ] + [
            FileScope(path=path, action="modify")
            for path in normalized.files_to_modify
        ]
    for order, step in enumerate(normalized.steps, start=1):
        if not step.title:
            step.title = step.objective or step.id
        step.requirement_ids = sorted(dict.fromkeys(step.requirement_ids))
        step.journey_ids = sorted(dict.fromkeys(step.journey_ids))
        step.owned_acceptance_criterion_ids = sorted(dict.fromkeys(step.owned_acceptance_criterion_ids))
        step.refs = _merge_trace_refs(
            step.refs,
            TraceRefs(
                requirement_ids=step.requirement_ids,
                journey_ids=step.journey_ids,
                acceptance_criterion_ids=step.owned_acceptance_criterion_ids,
            ),
        )
        step.chunk = _rechunk(
            step.chunk,
            "plan-step",
            artifact_key,
            step.id or str(order),
            order=order,
            payload=step.model_dump(mode="json"),
        )
    for order, journey_verification in enumerate(normalized.journey_verifications, start=1):
        for step_order, verify_step in enumerate(journey_verification.steps, start=1):
            if not verify_step.id:
                verify_step.id = f"{journey_verification.journey_id}-VERIFY-{verify_step.step_number or step_order}"
            verify_step.data_testids = sorted(dict.fromkeys(verify_step.data_testids))
            verify_step.chunk = _rechunk(
                verify_step.chunk,
                "journey-verify-step",
                artifact_key,
                verify_step.id,
                order=step_order,
                payload=verify_step.model_dump(mode="json"),
            )
        journey_verification.chunk = _rechunk(
            journey_verification.chunk,
            "journey-verification",
            artifact_key,
            journey_verification.journey_id or str(order),
            order=order,
            payload=journey_verification.model_dump(mode="json"),
        )
    for order, risk in enumerate(normalized.architectural_risks, start=1):
        risk.affected_step_ids = [
            _normalize_step_id(step_id)
            for step_id in risk.affected_step_ids
            if step_id
        ]
        risk.chunk = _rechunk(
            risk.chunk,
            "architectural-risk",
            artifact_key,
            risk.id or str(order),
            order=order,
            payload=risk.model_dump(mode="json"),
        )
    return normalized


def _normalize_test_plan_model(test_plan: TestPlan, artifact_key: str) -> TestPlan:
    normalized = test_plan.model_copy(deep=True)
    for order, criterion in enumerate(normalized.acceptance_criteria, start=1):
        criterion.refs = _merge_trace_refs(
            criterion.refs,
            _normalize_trace_refs(criterion.linked_requirement),
            TraceRefs(
                journey_step_ids=[_normalize_step_id(criterion.linked_journey_step_id)]
                if criterion.linked_journey_step_id
                else []
            ),
            TraceRefs(
                verifiable_state_ids=[criterion.linked_verifiable_state_id]
                if criterion.linked_verifiable_state_id
                else []
            ),
        )
        criterion.chunk = _rechunk(
            criterion.chunk,
            "test-ac",
            artifact_key,
            criterion.id or str(order),
            order=order,
            payload=criterion.model_dump(mode="json"),
        )
    for order, scenario in enumerate(normalized.test_scenarios, start=1):
        if not scenario.id:
            scenario.id = f"SCENARIO-{order}"
        scenario.refs = _merge_trace_refs(
            scenario.refs,
            TraceRefs(acceptance_criterion_ids=scenario.linked_acceptance),
        )
        scenario.chunk = _rechunk(
            scenario.chunk,
            "test-scenario",
            artifact_key,
            scenario.id,
            order=order,
            payload=scenario.model_dump(mode="json"),
        )
    if not normalized.checklist_items and normalized.verification_checklist:
        normalized.checklist_items = [
            ChecklistItem(id=f"CHECKLIST-{order}", text=text)
            for order, text in enumerate(normalized.verification_checklist, start=1)
        ]
    if not normalized.edge_case_items and normalized.edge_cases:
        normalized.edge_case_items = [
            EdgeCaseItem(id=f"EDGE-{order}", text=text)
            for order, text in enumerate(normalized.edge_cases, start=1)
        ]
    for order, checklist_item in enumerate(normalized.checklist_items, start=1):
        if not checklist_item.id:
            checklist_item.id = f"CHECKLIST-{order}"
        checklist_item.refs = _merge_trace_refs(
            checklist_item.refs,
            TraceRefs(acceptance_criterion_ids=sorted(_AC_ID_PATTERN.findall(checklist_item.text))),
        )
        checklist_item.chunk = _rechunk(
            checklist_item.chunk,
            "checklist",
            artifact_key,
            checklist_item.id,
            order=order,
            payload=checklist_item.model_dump(mode="json"),
        )
    for order, edge_case_item in enumerate(normalized.edge_case_items, start=1):
        if not edge_case_item.id:
            edge_case_item.id = f"EDGE-{order}"
        edge_case_item.refs = _merge_trace_refs(
            edge_case_item.refs,
            TraceRefs(acceptance_criterion_ids=sorted(_AC_ID_PATTERN.findall(edge_case_item.text))),
        )
        edge_case_item.chunk = _rechunk(
            edge_case_item.chunk,
            "edge-case",
            artifact_key,
            edge_case_item.id,
            order=order,
            payload=edge_case_item.model_dump(mode="json"),
        )
    normalized.verification_checklist = [item.text for item in normalized.checklist_items]
    normalized.edge_cases = [item.text for item in normalized.edge_case_items]
    return normalized


def _normalize_system_design_model(system_design: SystemDesign, artifact_key: str) -> SystemDesign:
    normalized = system_design.model_copy(deep=True)
    for order, service in enumerate(normalized.services, start=1):
        service.chunk = _rechunk(
            service.chunk,
            "service",
            artifact_key,
            service.id or str(order),
            order=order,
            payload=service.model_dump(mode="json"),
        )
    for order, connection in enumerate(normalized.connections, start=1):
        connection.chunk = _rechunk(
            connection.chunk,
            "service-connection",
            artifact_key,
            f"{connection.from_id}:{connection.to_id}:{order}",
            order=order,
            payload=connection.model_dump(mode="json"),
        )
    for order, endpoint in enumerate(normalized.api_endpoints, start=1):
        endpoint.chunk = _rechunk(
            endpoint.chunk,
            "api-endpoint",
            artifact_key,
            f"{endpoint.service_id}:{endpoint.method}:{endpoint.path}",
            order=order,
            payload=endpoint.model_dump(mode="json"),
        )
    for order, call_path in enumerate(normalized.call_paths, start=1):
        for step_order, call_step in enumerate(call_path.steps, start=1):
            call_step.chunk = _rechunk(
                call_step.chunk,
                "api-call-step",
                artifact_key,
                f"{call_path.id}:{call_step.sequence or step_order}",
                order=step_order,
                payload=call_step.model_dump(mode="json"),
            )
        call_path.chunk = _rechunk(
            call_path.chunk,
            "api-call-path",
            artifact_key,
            call_path.id or str(order),
            order=order,
            payload=call_path.model_dump(mode="json"),
        )
    for order, entity in enumerate(normalized.entities, start=1):
        for field_order, field in enumerate(entity.fields, start=1):
            field.chunk = _rechunk(
                field.chunk,
                "entity-field",
                artifact_key,
                f"{entity.id}:{field.name or field_order}",
                order=field_order,
                payload=field.model_dump(mode="json"),
            )
        entity.chunk = _rechunk(
            entity.chunk,
            "entity",
            artifact_key,
            entity.id or str(order),
            order=order,
            payload=entity.model_dump(mode="json"),
        )
    for order, relation in enumerate(normalized.entity_relations, start=1):
        relation.chunk = _rechunk(
            relation.chunk,
            "entity-relation",
            artifact_key,
            f"{relation.from_entity}:{relation.to_entity}:{relation.kind}:{order}",
            order=order,
            payload=relation.model_dump(mode="json"),
        )
    return normalized


def _normalize_decomposition_model(
    decomposition: SubfeatureDecomposition,
    artifact_key: str,
) -> SubfeatureDecomposition:
    normalized = decomposition.model_copy(deep=True)
    for order, subfeature in enumerate(normalized.subfeatures, start=1):
        subfeature.chunk = _rechunk(
            subfeature.chunk,
            "subfeature",
            artifact_key,
            subfeature.slug or subfeature.id or str(order),
            order=order,
            payload=subfeature.model_dump(mode="json"),
        )
    for order, edge in enumerate(normalized.edges, start=1):
        edge.chunk = _rechunk(
            edge.chunk,
            "subfeature-edge",
            artifact_key,
            f"{edge.from_subfeature}:{edge.to_subfeature}:{order}",
            order=order,
            payload=edge.model_dump(mode="json"),
        )
    return normalized


def _normalize_model(family: str, model: BaseModel, artifact_key: str) -> BaseModel:
    if family == "decomposition":
        return _normalize_decomposition_model(model, artifact_key)
    if family == "prd":
        return _normalize_prd_model(model, artifact_key)
    if family == "design":
        return _normalize_design_model(model, artifact_key)
    if family == "plan":
        return _normalize_plan_model(model, artifact_key)
    if family == "system-design":
        return _normalize_system_design_model(model, artifact_key)
    if family == "test-plan":
        return _normalize_test_plan_model(model, artifact_key)
    if family == "decisions":
        return _normalize_decision_ledger(model, artifact_key)
    return model


def normalize_source_model(artifact_key: str, artifact_text: str) -> BaseModel:
    family = artifact_family_for_key(artifact_key)
    try:
        payload = json.loads(artifact_text)
        if family == "decomposition":
            return _normalize_model(family, SubfeatureDecomposition.model_validate(payload), artifact_key)
        if family == "prd":
            return _normalize_model(family, PRD.model_validate(payload), artifact_key)
        if family == "design":
            return _normalize_model(family, DesignDecisions.model_validate(payload), artifact_key)
        if family == "plan":
            return _normalize_model(family, TechnicalPlan.model_validate(payload), artifact_key)
        if family == "system-design":
            return _normalize_model(family, SystemDesign.model_validate(payload), artifact_key)
        if family == "test-plan":
            return _normalize_model(family, TestPlan.model_validate(payload), artifact_key)
        if family == "decisions":
            return _normalize_model(family, DecisionLedger.model_validate(payload), artifact_key)
    except Exception:
        pass

    if family == "decomposition":
        return _normalize_model(family, _parse_decomposition_from_markdown(artifact_text, artifact_key), artifact_key)
    if family == "prd":
        return _normalize_model(family, _parse_prd_from_markdown(artifact_text, artifact_key), artifact_key)
    if family == "design":
        return _normalize_model(family, _parse_design_from_markdown(artifact_text, artifact_key), artifact_key)
    if family == "plan":
        return _normalize_model(family, _parse_technical_plan_from_markdown(artifact_text, artifact_key), artifact_key)
    if family == "system-design":
        return _normalize_model(family, _parse_system_design_from_text(artifact_text, artifact_key), artifact_key)
    if family == "test-plan":
        return _normalize_model(family, _parse_test_plan_from_markdown(artifact_text, artifact_key), artifact_key)
    if family == "decisions":
        return _normalize_model(family, parse_decision_ledger(artifact_text), artifact_key)
    raise ValueError(f"unsupported source artifact key: {artifact_key}")


def build_structured_artifact(
    artifact_key: str,
    artifact_text: str,
    *,
    generated_from: str,
) -> StructuredArtifact[Any]:
    content = normalize_source_model(artifact_key, artifact_text)
    scope_kind, scope_slug = _scope_parts(artifact_key)
    return StructuredArtifact(
        meta=StructuredArtifactEnvelope(
            artifact_family=artifact_family_for_key(artifact_key),
            artifact_key=artifact_key,
            scope_kind=scope_kind,
            scope_slug=scope_slug,
            source_hash=_content_digest(artifact_text),
            content_digest=_json_digest(content.model_dump(mode="json")),
            generated_from=generated_from,
        ),
        content=content,
    )


def render_structured_markdown(sidecar: StructuredArtifact[Any]) -> str:
    return to_markdown(sidecar.content)


def parity_check_structured_artifact(
    artifact_key: str,
    artifact_text: str,
    sidecar: StructuredArtifact[Any],
) -> list[str]:
    family = artifact_family_for_key(artifact_key)
    try:
        parsed_payload = json.loads(artifact_text)
    except json.JSONDecodeError:
        parsed_payload = None
    if isinstance(parsed_payload, dict):
        artifact_text = render_structured_markdown(sidecar)
    messages: list[str] = []
    if family == "plan":
        step_chunk_ids = [step.chunk.chunk_id for step in sidecar.content.steps]
        if any(not chunk_id for chunk_id in step_chunk_ids):
            messages.append("plan step chunk ids are missing after normalization")
        elif len(step_chunk_ids) != len(set(step_chunk_ids)):
            messages.append("plan step chunk ids are not unique after normalization")
        if any(not step.chunk.content_digest for step in sidecar.content.steps):
            messages.append("plan step chunk digests are missing after normalization")
    elif family == "test-plan":
        criterion_chunk_ids = [
            criterion.chunk.chunk_id
            for criterion in sidecar.content.acceptance_criteria
        ]
        if any(not chunk_id for chunk_id in criterion_chunk_ids):
            messages.append("test-plan acceptance-criterion chunk ids are missing after normalization")
        elif len(criterion_chunk_ids) != len(set(criterion_chunk_ids)):
            messages.append("test-plan acceptance-criterion chunk ids are not unique after normalization")
    if family == "plan":
        source_steps = _source_plan_step_ids(artifact_text)
        sidecar_steps = sorted(step.id for step in sidecar.content.steps if step.id)
        if source_steps != sidecar_steps:
            messages.append("plan step ids diverge from source markdown")
        steps_source_text = _markdown_h2_body(artifact_text, "Implementation Steps") or artifact_text
        step_count = len(list(_STEP_HEADING_PATTERN.finditer(steps_source_text)))
        if step_count and step_count != len(sidecar.content.steps):
            messages.append("plan step count diverges from source implementation steps section")
        file_manifest_rows = [
            row
            for row in _table_rows(_markdown_h2_body(artifact_text, "File Manifest"))
            if len(row) >= 2 and row[0] != "Path"
        ]
        if file_manifest_rows and len(file_manifest_rows) != len(sidecar.content.file_manifest):
            messages.append("plan file manifest diverges from source markdown")
        journey_verifications_section = (
            _markdown_h2_body(artifact_text, "Journey Verifications")
            or _markdown_h2_body(artifact_text, "Journey Verification")
        )
        if journey_verifications_section:
            source_journey_count = len(
                list(re.finditer(r"(?m)^###\s+(?:Journey\s+)?(.+?)\s*$", journey_verifications_section))
            )
            sidecar_journey_count = len(sidecar.content.journey_verifications)
            if source_journey_count != sidecar_journey_count:
                messages.append("plan journey-verification count diverges from source markdown")
            source_verify_step_count = len(
                list(re.finditer(r"(?m)^\*\*Step\s+\d+:\*\*\s*$", journey_verifications_section))
            )
            sidecar_verify_step_count = sum(
                len(journey_verification.steps)
                for journey_verification in sidecar.content.journey_verifications
            )
            if source_verify_step_count != sidecar_verify_step_count:
                messages.append("plan journey-verification steps diverge from source markdown")
        risk_rows = [
            row
            for row in _table_rows(_markdown_h2_body(artifact_text, "Architectural Risks"))
            if len(row) >= 5 and row[0] != "ID"
        ]
        if risk_rows and len(risk_rows) != len(sidecar.content.architectural_risks):
            messages.append("plan architectural risks diverge from source markdown")
    elif family == "test-plan":
        source_acs = _source_test_plan_acceptance_ids(artifact_text)
        sidecar_acs = sorted(ac.id for ac in sidecar.content.acceptance_criteria if ac.id)
        if source_acs != sidecar_acs:
            messages.append("test-plan acceptance criteria ids diverge from source markdown")
        raw_linked_requirement_values = re.findall(r"(?m)^\s*-\s+linked_requirement:\s*(.+?)\s*$", artifact_text)
        if raw_linked_requirement_values:
            merged_refs = TraceRefs()
            for criterion in sidecar.content.acceptance_criteria:
                merged_refs = _merge_trace_refs(merged_refs, criterion.refs)
            for raw in raw_linked_requirement_values:
                normalized_raw = _normalize_trace_refs(raw)
                if not set(normalized_raw.requirement_ids).issubset(set(merged_refs.requirement_ids)):
                    messages.append(f"test-plan trace token lost during normalization: {raw.strip()}")
                    continue
                if not set(normalized_raw.decision_ids).issubset(set(merged_refs.decision_ids)):
                    messages.append(f"test-plan trace token lost during normalization: {raw.strip()}")
                    continue
                if not set(normalized_raw.decision_aliases).issubset(set(merged_refs.decision_aliases)):
                    messages.append(f"test-plan trace token lost during normalization: {raw.strip()}")
                    continue
                if not set(normalized_raw.nfr_ids).issubset(set(merged_refs.nfr_ids)):
                    messages.append(f"test-plan trace token lost during normalization: {raw.strip()}")
                    continue
                if not set(normalized_raw.verifiable_state_ids).issubset(set(merged_refs.verifiable_state_ids)):
                    messages.append(f"test-plan trace token lost during normalization: {raw.strip()}")
                    continue
                residual_notes = {note for note in normalized_raw.notes if note}
                if residual_notes and not residual_notes.issubset(set(merged_refs.notes)):
                    messages.append(f"test-plan trace token lost during normalization: {raw.strip()}")
    elif family == "decisions":
        source_decisions = _source_decision_tokens(artifact_text)
        sidecar_decisions = sorted(
            {
                decision.id
                for decision in sidecar.content.decisions
                if decision.id
            }
            | {
                alias
                for decision in sidecar.content.decisions
                for alias in decision.aliases
                if alias
            }
        )
        if source_decisions and not set(sidecar_decisions).issuperset(source_decisions):
            messages.append("decision ids diverge from source ledger")
    elif family == "prd":
        source_reqs = _source_prd_requirement_ids(artifact_text)
        sidecar_reqs = sorted(requirement.id for requirement in sidecar.content.structured_requirements if requirement.id)
        if source_reqs and not set(sidecar_reqs).issuperset(source_reqs):
            messages.append("prd requirement ids diverge from source markdown")
        source_journeys = _source_prd_journey_ids(artifact_text)
        sidecar_journeys = sorted(journey.id for journey in sidecar.content.journeys if journey.id)
        if source_journeys and not set(sidecar_journeys).issuperset(source_journeys):
            messages.append("prd journey ids diverge from source markdown")
    elif family == "design":
        source_states = sorted(set(_VERIFIABLE_STATE_ID_PATTERN.findall(artifact_text)))
        sidecar_states = sorted(state.id for state in sidecar.content.verifiable_states if state.id)
        if source_states and not set(sidecar_states).issuperset(source_states):
            messages.append("design verifiable state ids diverge from source markdown")
    elif family == "system-design":
        source_decisions = _parse_markdown_bullets(
            _markdown_h2_body(artifact_text, "Decisions")
            or _markdown_h2_body(artifact_text, "Decision Log")
        )
        if source_decisions:
            sidecar_decisions = sorted(
                html.unescape(_strip_markdown_inline_formatting(decision))
                for decision in sidecar.content.decisions
                if _strip_markdown_inline_formatting(decision)
            )
            if not set(sidecar_decisions).issuperset(
                html.unescape(_strip_markdown_inline_formatting(decision))
                for decision in source_decisions
            ):
                messages.append("system-design decisions diverge from source markdown")
    return sorted(dict.fromkeys(messages))


async def persist_json_artifact(
    runner: Any,
    feature: Any,
    artifact_key: str,
    model: BaseModel,
) -> None:
    text = model.model_dump_json(indent=2)
    await runner.artifacts.put(artifact_key, text, feature=feature)
    mirror = runner.services.get("artifact_mirror")
    if mirror and hasattr(mirror, "write_artifact"):
        mirror.write_artifact(feature.id, artifact_key, text)


async def load_source_artifact_text(
    runner: Any,
    feature: Any,
    artifact_key: str,
) -> str:
    text = await runner.artifacts.get(artifact_key, feature=feature) or ""
    source_rel = _sd_source_path(artifact_key) if artifact_key.startswith("system-design") else _key_to_path(artifact_key)
    if not source_rel:
        return text
    mirror = runner.services.get("artifact_mirror")
    if not mirror:
        return text
    source_path = Path(mirror.feature_dir(feature.id)) / source_rel
    if not source_path.exists():
        return text
    source_text = source_path.read_text(encoding="utf-8").strip()
    return source_text or text


async def load_structured_artifact(
    runner: Any,
    feature: Any,
    artifact_key: str,
) -> StructuredArtifact[Any] | None:
    sidecar_key = structured_artifact_key(artifact_key)
    payload = await runner.artifacts.get(sidecar_key, feature=feature) or ""
    if not payload:
        mirror = (getattr(runner, "services", {}) or {}).get("artifact_mirror")
        if mirror:
            sidecar_path = Path(mirror.feature_dir(feature.id)) / _key_to_path(sidecar_key)
            if sidecar_path.exists():
                payload = sidecar_path.read_text(encoding="utf-8")
    if not payload:
        return None
    try:
        parsed = json.loads(payload)
        content_type = sidecar_content_type_for_key(artifact_key)
        return StructuredArtifact(
            meta=StructuredArtifactEnvelope.model_validate(parsed.get("meta", {})),
            content=content_type.model_validate(parsed.get("content", {})),
        )
    except Exception:
        logger.warning("Failed to parse structured sidecar for %s", artifact_key, exc_info=True)
        return None


def _system_design_verifiable_state_ids(
    system_design_sidecar: StructuredArtifact[SystemDesign] | None,
) -> list[str]:
    if system_design_sidecar is None:
        return []
    payload = system_design_sidecar.content
    state_text = "\n".join(
        part
        for part in [
            payload.overview,
            *payload.decisions,
            *payload.risks,
        ]
        if part
    )
    return _extract_ids_from_text(state_text, _VERIFIABLE_STATE_ID_PATTERN)


def _rebuild_structured_artifact(
    sidecar: StructuredArtifact[Any],
    content: BaseModel,
) -> StructuredArtifact[Any]:
    meta = sidecar.meta.model_copy(deep=True)
    meta.content_digest = _json_digest(content.model_dump(mode="json"))
    return StructuredArtifact(meta=meta, content=content)


def canonicalize_subfeature_sidecars(
    slug: str,
    artifacts: dict[str, StructuredArtifact[Any]],
    shared_index: SharedPlanningIndex | None = None,
) -> dict[str, StructuredArtifact[Any]]:
    updated: dict[str, StructuredArtifact[Any]] = {
        artifact_key: sidecar.model_copy(deep=True)
        for artifact_key, sidecar in artifacts.items()
    }
    family_map = {
        artifact_family_for_key(artifact_key): sidecar
        for artifact_key, sidecar in updated.items()
    }
    plan_sidecar = family_map.get("plan")
    prd_sidecar = family_map.get("prd")
    design_sidecar = family_map.get("design")
    system_design_sidecar = family_map.get("system-design")
    test_plan_sidecar = family_map.get("test-plan")

    canonical_requirement_ids = sorted(
        {
            *(
                requirement.id
                for requirement in (prd_sidecar.content.structured_requirements if prd_sidecar is not None else [])
                if requirement.id
            ),
            *((shared_index.requirement_ids if shared_index is not None else [])),
        }
    )
    canonical_journey_ids = sorted(
        {
            *(
                journey.id
                for journey in (prd_sidecar.content.journeys if prd_sidecar is not None else [])
                if journey.id
            ),
            *(
                journey_verification.journey_id
                for journey_verification in (plan_sidecar.content.journey_verifications if plan_sidecar is not None else [])
                if journey_verification.journey_id
            ),
            *((shared_index.journey_ids if shared_index is not None else [])),
        }
    )
    canonical_acceptance_ids = [
        criterion.id
        for criterion in (test_plan_sidecar.content.acceptance_criteria if test_plan_sidecar is not None else [])
        if criterion.id
    ]
    canonical_state_ids = sorted(
        {
            *(
                state.id or f"{state.component_id}#{state.state_name}"
                for state in (design_sidecar.content.verifiable_states if design_sidecar is not None else [])
                if state.id or state.component_id
            ),
            *_system_design_verifiable_state_ids(system_design_sidecar),
        }
    )

    if prd_sidecar is not None:
        prd_content = prd_sidecar.content.model_copy(deep=True)
        for criterion in prd_content.structured_acceptance_criteria:
            criterion.requirement_ids = canonicalize_requirement_ids(
                criterion.requirement_ids,
                canonical_requirement_ids,
            )
        for journey in prd_content.journeys:
            journey.requirement_ids = canonicalize_requirement_ids(
                journey.requirement_ids,
                canonical_requirement_ids,
            )
        normalized_prd = _normalize_prd_model(prd_content, prd_sidecar.meta.artifact_key)
        updated[prd_sidecar.meta.artifact_key] = _rebuild_structured_artifact(
            prd_sidecar,
            normalized_prd,
        )
        family_map["prd"] = updated[prd_sidecar.meta.artifact_key]

    if plan_sidecar is not None:
        plan_content = plan_sidecar.content.model_copy(deep=True)
        for step in plan_content.steps:
            step.requirement_ids = canonicalize_requirement_ids(
                step.requirement_ids,
                canonical_requirement_ids,
            )
            step.journey_ids = canonicalize_journey_ids(
                step.journey_ids,
                canonical_journey_ids,
            )
            step.owned_acceptance_criterion_ids = canonicalize_acceptance_ids(
                step.owned_acceptance_criterion_ids,
                canonical_acceptance_ids,
            )
            step.refs.requirement_ids = canonicalize_requirement_ids(
                step.refs.requirement_ids,
                canonical_requirement_ids,
            )
            step.refs.journey_ids = canonicalize_journey_ids(
                step.refs.journey_ids,
                canonical_journey_ids,
            )
            step.refs.verifiable_state_ids = canonicalize_verifiable_state_ids(
                step.refs.verifiable_state_ids,
                canonical_state_ids,
            )
            step.refs.acceptance_criterion_ids = canonicalize_acceptance_ids(
                step.refs.acceptance_criterion_ids,
                canonical_acceptance_ids,
            )
        normalized_plan = _normalize_plan_model(plan_content, plan_sidecar.meta.artifact_key)
        updated[plan_sidecar.meta.artifact_key] = _rebuild_structured_artifact(
            plan_sidecar,
            normalized_plan,
        )
        family_map["plan"] = updated[plan_sidecar.meta.artifact_key]

    if test_plan_sidecar is not None:
        test_plan_content = test_plan_sidecar.content.model_copy(deep=True)
        for criterion in test_plan_content.acceptance_criteria:
            criterion.refs.requirement_ids = canonicalize_requirement_ids(
                criterion.refs.requirement_ids,
                canonical_requirement_ids,
            )
            criterion.refs.journey_ids = canonicalize_journey_ids(
                criterion.refs.journey_ids,
                canonical_journey_ids,
            )
            criterion.refs.verifiable_state_ids = canonicalize_verifiable_state_ids(
                criterion.refs.verifiable_state_ids,
                canonical_state_ids,
            )
            criterion.refs.acceptance_criterion_ids = canonicalize_acceptance_ids(
                criterion.refs.acceptance_criterion_ids,
                canonical_acceptance_ids,
            )
            if criterion.linked_verifiable_state_id:
                canonical_state = canonicalize_verifiable_state_ids(
                    [criterion.linked_verifiable_state_id],
                    canonical_state_ids,
                )
                criterion.linked_verifiable_state_id = canonical_state[0] if canonical_state else criterion.linked_verifiable_state_id
        for scenario in test_plan_content.test_scenarios:
            scenario.linked_acceptance = canonicalize_acceptance_ids(
                scenario.linked_acceptance,
                canonical_acceptance_ids,
            )
            scenario.refs.acceptance_criterion_ids = canonicalize_acceptance_ids(
                scenario.refs.acceptance_criterion_ids,
                canonical_acceptance_ids,
            )
            scenario.refs.requirement_ids = canonicalize_requirement_ids(
                scenario.refs.requirement_ids,
                canonical_requirement_ids,
            )
            scenario.refs.journey_ids = canonicalize_journey_ids(
                scenario.refs.journey_ids,
                canonical_journey_ids,
            )
            scenario.refs.verifiable_state_ids = canonicalize_verifiable_state_ids(
                scenario.refs.verifiable_state_ids,
                canonical_state_ids,
            )
        for checklist_item in test_plan_content.checklist_items:
            checklist_item.refs.acceptance_criterion_ids = canonicalize_acceptance_ids(
                checklist_item.refs.acceptance_criterion_ids,
                canonical_acceptance_ids,
            )
        for edge_case_item in test_plan_content.edge_case_items:
            edge_case_item.refs.acceptance_criterion_ids = canonicalize_acceptance_ids(
                edge_case_item.refs.acceptance_criterion_ids,
                canonical_acceptance_ids,
            )
        normalized_test_plan = _normalize_test_plan_model(
            test_plan_content,
            test_plan_sidecar.meta.artifact_key,
        )
        updated[test_plan_sidecar.meta.artifact_key] = _rebuild_structured_artifact(
            test_plan_sidecar,
            normalized_test_plan,
        )

    return updated


async def normalize_and_persist_source_artifact(
    runner: Any,
    feature: Any,
    artifact_key: str,
    artifact_text: str,
    *,
    generated_from: str,
    slug: str = "",
) -> NormalizedArtifactResult:
    sidecar = build_structured_artifact(artifact_key, artifact_text, generated_from=generated_from)
    parity_messages = parity_check_structured_artifact(artifact_key, artifact_text, sidecar)
    sidecar_key = structured_artifact_key(artifact_key)
    await persist_json_artifact(runner, feature, sidecar_key, sidecar)
    issues = [
        ArtifactAuditIssue(
            classification="parity_failed",
            artifact_family=artifact_family_for_key(artifact_key),
            artifact_key=artifact_key,
            message=message,
        )
        for message in parity_messages
    ]
    return NormalizedArtifactResult(
        sidecar_key=sidecar_key,
        sidecar=sidecar,
        parity_messages=parity_messages,
        issues=issues,
    )


async def refresh_sidecar_for_source_artifact(
    runner: Any,
    feature: Any,
    artifact_key: str,
    artifact_text: str,
    *,
    generated_from: str,
) -> NormalizedArtifactResult | None:
    if not is_source_artifact_key(artifact_key):
        return None

    result = await normalize_and_persist_source_artifact(
        runner,
        feature,
        artifact_key,
        artifact_text,
        generated_from=generated_from,
    )
    status_text = await runner.artifacts.get("artifact-backfill-status", feature=feature) or ""
    status = None
    if status_text:
        try:
            status = ArtifactBackfillStatus.model_validate_json(status_text)
        except Exception:
            logger.warning("Failed to parse artifact-backfill-status; rebuilding", exc_info=True)
    shared = is_shared_source_artifact_key(artifact_key)
    slug = ""
    if ":" in artifact_key:
        _prefix, slug = artifact_key.split(":", 1)
        if slug in {"broad", "global"}:
            slug = ""
    status = update_backfill_status(
        status,
        slug=slug or None,
        artifact_family=artifact_family_for_key(artifact_key),
        source_hash=result.sidecar.meta.source_hash,
        sidecar_key_name=result.sidecar_key,
        sidecar_digest=result.sidecar.meta.content_digest,
        parity_messages=result.parity_messages,
        shared=shared,
    )
    if not shared and slug:
        subfeature_status = status.subfeatures.setdefault(slug, ArtifactBackfillSubfeatureStatus(slug=slug))
        subfeature_status.join_complete = False
        subfeature_status.planning_index_digest = ""
    await persist_json_artifact(runner, feature, "artifact-backfill-status", status)

    delete = getattr(runner.artifacts, "delete", None)
    if shared:
        if callable(delete):
            await delete("planning-index:shared", feature=feature)
            await delete("artifact-audit-summary", feature=feature)
        _mirror_delete_artifact(runner, feature, "planning-index:shared")
        _mirror_delete_artifact(runner, feature, "artifact-audit-summary")
    elif slug:
        if callable(delete):
            await delete(f"planning-index:{slug}", feature=feature)
            await delete(f"artifact-audit:{slug}", feature=feature)
        _mirror_delete_artifact(runner, feature, f"planning-index:{slug}")
        _mirror_delete_artifact(runner, feature, f"artifact-audit:{slug}")
    return result


def _criterion_candidate_steps(
    criterion: TestAcceptanceCriterion,
    steps: list[ImplementationStep],
) -> list[str]:
    refs = criterion.refs
    candidates: list[str] = []
    for step in steps:
        step_refs = _merge_trace_refs(step.refs, TraceRefs(requirement_ids=step.requirement_ids, journey_ids=step.journey_ids))
        if criterion.id and criterion.id in step.owned_acceptance_criterion_ids:
            candidates.append(step.id)
            continue
        if set(refs.requirement_ids) & set(step_refs.requirement_ids):
            candidates.append(step.id)
            continue
        if set(refs.journey_ids) & set(step_refs.journey_ids):
            candidates.append(step.id)
            continue
        if {
            journey_step_id.split("#", 1)[0]
            for journey_step_id in refs.journey_step_ids
            if "#" in journey_step_id
        } & set(step_refs.journey_ids):
            candidates.append(step.id)
            continue
        if set(refs.decision_ids) & set(step_refs.decision_ids):
            candidates.append(step.id)
            continue
        if set(refs.decision_aliases) & set(step_refs.decision_aliases):
            candidates.append(step.id)
            continue
        if set(refs.nfr_ids) & set(step_refs.nfr_ids):
            candidates.append(step.id)
            continue
        if set(refs.verifiable_state_ids) & set(step_refs.verifiable_state_ids):
            candidates.append(step.id)
            continue
    return list(dict.fromkeys(candidates))


def _criterion_owner_step(
    criterion: TestAcceptanceCriterion,
    steps: list[ImplementationStep],
    candidates: list[str],
) -> str:
    if not candidates:
        return ""
    candidate_set = set(candidates)
    explicit_candidates = [
        step.id
        for step in steps
        if step.id in candidate_set and criterion.id in step.owned_acceptance_criterion_ids
    ]
    if explicit_candidates:
        return explicit_candidates[0]
    return candidates[0]


def build_shared_planning_index(
    decomposition_sidecar: StructuredArtifact[SubfeatureDecomposition] | None,
    broad_prd_sidecar: StructuredArtifact[PRD] | None,
    broad_decision_sidecars: list[StructuredArtifact[DecisionLedger]],
) -> SharedPlanningIndex:
    decision_ids: set[str] = set()
    decision_alias_map: dict[str, str] = {}
    source_digests: dict[str, str] = {}
    for sidecar in broad_decision_sidecars:
        source_digests[sidecar.meta.artifact_key] = sidecar.meta.content_digest
        for decision in sidecar.content.decisions:
            decision_ids.add(decision.id)
            for alias in decision.aliases:
                decision_alias_map[alias] = decision.id
    requirement_ids = sorted(
        requirement.id
        for requirement in (broad_prd_sidecar.content.structured_requirements if broad_prd_sidecar else [])
        if requirement.id
    )
    journey_ids = sorted(
        journey.id
        for journey in (broad_prd_sidecar.content.journeys if broad_prd_sidecar else [])
        if journey.id
    )
    if broad_prd_sidecar is not None:
        source_digests[broad_prd_sidecar.meta.artifact_key] = broad_prd_sidecar.meta.content_digest
    if decomposition_sidecar is not None:
        source_digests[decomposition_sidecar.meta.artifact_key] = decomposition_sidecar.meta.content_digest
    edge_descriptions = [
        f"{edge.from_subfeature}->{edge.to_subfeature}:{edge.description}"
        for edge in (decomposition_sidecar.content.edges if decomposition_sidecar else [])
    ]
    subfeature_slugs = [
        subfeature.slug
        for subfeature in (decomposition_sidecar.content.subfeatures if decomposition_sidecar else [])
        if subfeature.slug
    ]
    index = SharedPlanningIndex(
        source_digests=source_digests,
        requirement_ids=requirement_ids,
        journey_ids=journey_ids,
        decision_ids=sorted(decision_ids),
        decision_alias_map=decision_alias_map,
        subfeature_slugs=subfeature_slugs,
        edge_descriptions=edge_descriptions,
    )
    index.index_digest = _json_digest(index.model_dump(mode="json"))
    return index


def build_subfeature_planning_index(
    slug: str,
    artifacts: dict[str, StructuredArtifact[Any]],
    shared_index: SharedPlanningIndex | None = None,
) -> tuple[SubfeaturePlanningIndex, ArtifactAuditReport]:
    canonicalized = canonicalize_subfeature_sidecars(
        slug,
        {sidecar.meta.artifact_key: sidecar for sidecar in artifacts.values()},
        shared_index=shared_index,
    )
    artifacts = {
        artifact_family_for_key(artifact_key): sidecar
        for artifact_key, sidecar in canonicalized.items()
    }
    report = ArtifactAuditReport(slug=slug, complete=True)
    source_digests = {
        sidecar.meta.artifact_key: sidecar.meta.content_digest
        for sidecar in artifacts.values()
    }
    plan = artifacts.get("plan")
    prd = artifacts.get("prd")
    design = artifacts.get("design")
    system_design = artifacts.get("system-design")
    test_plan = artifacts.get("test-plan")
    decisions = artifacts.get("decisions")
    canonical_ac_ids = [
        criterion.id
        for criterion in (test_plan.content.acceptance_criteria if test_plan is not None else [])
        if criterion.id
    ]
    canonical_step_owned_ids: dict[str, list[str]] = {}
    canonical_step_refs: dict[str, TraceRefs] = {}
    if plan is not None:
        for step in plan.content.steps:
            step_refs = step.refs.model_copy(deep=True)
            step_refs.acceptance_criterion_ids = canonicalize_acceptance_ids(
                step_refs.acceptance_criterion_ids,
                canonical_ac_ids,
            )
            canonical_step_refs[step.id] = step_refs
            canonical_step_owned_ids[step.id] = canonicalize_acceptance_ids(
                step.owned_acceptance_criterion_ids,
                canonical_ac_ids,
            )
    candidate_steps = [
        step.model_copy(
            update={
                "refs": canonical_step_refs.get(step.id, step.refs),
                "owned_acceptance_criterion_ids": canonical_step_owned_ids.get(
                    step.id,
                    step.owned_acceptance_criterion_ids,
                ),
            },
            deep=True,
        )
        for step in (plan.content.steps if plan is not None else [])
    ]

    index = SubfeaturePlanningIndex(slug=slug, source_digests=source_digests)
    step_order: list[str] = []
    if plan is not None:
        for step in plan.content.steps:
            index.nodes.append(
                PlanningChunkNode(
                    chunk_id=step.chunk.chunk_id,
                    chunk_type="plan-step",
                    artifact_key=plan.meta.artifact_key,
                    title=step.title or step.objective,
                    order=step.chunk.order,
                    content_digest=step.chunk.content_digest,
                    refs=_merge_trace_refs(
                        canonical_step_refs.get(step.id, step.refs),
                        TraceRefs(
                            requirement_ids=step.requirement_ids,
                            journey_ids=step.journey_ids,
                            acceptance_criterion_ids=canonical_step_owned_ids.get(step.id, step.owned_acceptance_criterion_ids),
                        ),
                    ),
                )
            )
            step_order.append(step.chunk.chunk_id)
    if prd is not None:
        for requirement in prd.content.structured_requirements:
            index.nodes.append(
                PlanningChunkNode(
                    chunk_id=requirement.chunk.chunk_id,
                    chunk_type="req",
                    artifact_key=prd.meta.artifact_key,
                    title=requirement.description,
                    order=requirement.chunk.order,
                    content_digest=requirement.chunk.content_digest,
                )
            )
        for journey in prd.content.journeys:
            index.nodes.append(
                PlanningChunkNode(
                    chunk_id=journey.chunk.chunk_id,
                    chunk_type="journey",
                    artifact_key=prd.meta.artifact_key,
                    title=journey.name,
                    order=journey.chunk.order,
                    content_digest=journey.chunk.content_digest,
                    refs=TraceRefs(requirement_ids=journey.requirement_ids, journey_ids=[journey.id]),
                )
            )
    if design is not None:
        for component in design.content.component_defs:
            index.nodes.append(
                PlanningChunkNode(
                    chunk_id=component.chunk.chunk_id,
                    chunk_type="component",
                    artifact_key=design.meta.artifact_key,
                    title=component.name,
                    order=component.chunk.order,
                    content_digest=component.chunk.content_digest,
                )
            )
        for state in design.content.verifiable_states:
            index.nodes.append(
                PlanningChunkNode(
                    chunk_id=state.chunk.chunk_id,
                    chunk_type="state",
                    artifact_key=design.meta.artifact_key,
                    title=state.id or f"{state.component_id}#{state.state_name}",
                    order=state.chunk.order,
                    content_digest=state.chunk.content_digest,
                    refs=TraceRefs(verifiable_state_ids=[state.id or f"{state.component_id}#{state.state_name}"]),
                )
            )
    system_design_state_ids = _system_design_verifiable_state_ids(system_design)
    if system_design is not None:
        for service in system_design.content.services:
            index.nodes.append(
                PlanningChunkNode(
                    chunk_id=service.chunk.chunk_id,
                    chunk_type="service",
                    artifact_key=system_design.meta.artifact_key,
                    title=service.name,
                    order=service.chunk.order,
                    content_digest=service.chunk.content_digest,
                    refs=TraceRefs(journey_ids=service.journeys),
                )
            )
        for entity in system_design.content.entities:
            index.nodes.append(
                PlanningChunkNode(
                    chunk_id=entity.chunk.chunk_id,
                    chunk_type="entity",
                    artifact_key=system_design.meta.artifact_key,
                    title=entity.name,
                    order=entity.chunk.order,
                    content_digest=entity.chunk.content_digest,
                    refs=TraceRefs(journey_ids=entity.journeys),
                )
            )
        existing_state_chunk_ids = {
            node.chunk_id
            for node in index.nodes
            if node.chunk_type == "state"
        }
        for order, state_id in enumerate(system_design_state_ids, start=1):
            chunk_id = f"state:{system_design.meta.artifact_key}:{state_id}"
            if chunk_id in existing_state_chunk_ids:
                continue
            existing_state_chunk_ids.add(chunk_id)
            index.nodes.append(
                PlanningChunkNode(
                    chunk_id=chunk_id,
                    chunk_type="state",
                    artifact_key=system_design.meta.artifact_key,
                    title=state_id,
                    order=order,
                    content_digest=_content_digest(state_id),
                    refs=TraceRefs(verifiable_state_ids=[state_id]),
                )
            )
    if plan is not None:
        for journey_verification in plan.content.journey_verifications:
            index.nodes.append(
                PlanningChunkNode(
                    chunk_id=journey_verification.chunk.chunk_id,
                    chunk_type="journey-verification",
                    artifact_key=plan.meta.artifact_key,
                    title=journey_verification.journey_id,
                    order=journey_verification.chunk.order,
                    content_digest=journey_verification.chunk.content_digest,
                    refs=TraceRefs(journey_ids=[journey_verification.journey_id]),
                )
            )
    step_candidates: dict[str, list[str]] = {}
    criterion_owner_step_ids: dict[str, str] = {}
    owned_by_step: dict[str, list[str]] = {}
    step_overlay_chunk_ids: dict[str, set[str]] = {
        step.id: set()
        for step in (plan.content.steps if plan is not None else [])
    }
    if test_plan is not None and plan is not None:
        for criterion in test_plan.content.acceptance_criteria:
            index.nodes.append(
                PlanningChunkNode(
                    chunk_id=criterion.chunk.chunk_id,
                    chunk_type="test-ac",
                    artifact_key=test_plan.meta.artifact_key,
                    title=criterion.description,
                    order=criterion.chunk.order,
                    content_digest=criterion.chunk.content_digest,
                    refs=criterion.refs,
                )
            )
            candidates = _criterion_candidate_steps(criterion, candidate_steps)
            step_candidates[criterion.id] = candidates
            owner_step_id = _criterion_owner_step(criterion, candidate_steps, candidates)
            if owner_step_id:
                criterion_owner_step_ids[criterion.id] = owner_step_id
                owned_by_step.setdefault(owner_step_id, []).append(criterion.id)
                for step_id in candidates:
                    step_overlay_chunk_ids.setdefault(step_id, set()).add(criterion.chunk.chunk_id)
            else:
                report.issues.append(
                    ArtifactAuditIssue(
                        classification="artifact_ambiguity",
                        artifact_family="test-plan",
                        artifact_key=test_plan.meta.artifact_key,
                        message=f"{criterion.id} could not be linked to any implementation step",
                        extracted_value=criterion.id,
                    )
                )
        for scenario in test_plan.content.test_scenarios:
            index.nodes.append(
                PlanningChunkNode(
                    chunk_id=scenario.chunk.chunk_id,
                    chunk_type="test-scenario",
                    artifact_key=test_plan.meta.artifact_key,
                    title=scenario.name,
                    order=scenario.chunk.order,
                    content_digest=scenario.chunk.content_digest,
                    refs=scenario.refs,
                )
            )
            for ac_id in canonicalize_acceptance_ids(
                scenario.linked_acceptance or scenario.refs.acceptance_criterion_ids,
                canonical_ac_ids,
            ):
                matching_candidates = step_candidates.get(ac_id, [])
                for step_id in matching_candidates:
                    step_overlay_chunk_ids.setdefault(step_id, set()).add(scenario.chunk.chunk_id)
                if ac_id:
                    criterion_chunk_id = next(
                        (criterion.chunk.chunk_id for criterion in test_plan.content.acceptance_criteria if criterion.id == ac_id),
                        "",
                    )
                    if criterion_chunk_id:
                        index.edges.append(
                            PlanningChunkEdge(
                                from_chunk_id=scenario.chunk.chunk_id,
                                to_chunk_id=criterion_chunk_id,
                                edge_type="scenario_covers_ac",
                            )
                        )
        for checklist_item in test_plan.content.checklist_items:
            index.nodes.append(
                PlanningChunkNode(
                    chunk_id=checklist_item.chunk.chunk_id,
                    chunk_type="checklist",
                    artifact_key=test_plan.meta.artifact_key,
                    title=checklist_item.text,
                    order=checklist_item.chunk.order,
                    content_digest=checklist_item.chunk.content_digest,
                    refs=checklist_item.refs,
                )
            )
            for ac_id in canonicalize_acceptance_ids(
                checklist_item.refs.acceptance_criterion_ids,
                canonical_ac_ids,
            ):
                criterion_chunk_id = next(
                    (criterion.chunk.chunk_id for criterion in test_plan.content.acceptance_criteria if criterion.id == ac_id),
                    "",
                )
                if criterion_chunk_id:
                    index.edges.append(
                        PlanningChunkEdge(
                            from_chunk_id=checklist_item.chunk.chunk_id,
                            to_chunk_id=criterion_chunk_id,
                            edge_type="checklist_covers_ac",
                        )
                    )
        for edge_case_item in test_plan.content.edge_case_items:
            index.nodes.append(
                PlanningChunkNode(
                    chunk_id=edge_case_item.chunk.chunk_id,
                    chunk_type="edge-case",
                    artifact_key=test_plan.meta.artifact_key,
                    title=edge_case_item.text,
                    order=edge_case_item.chunk.order,
                    content_digest=edge_case_item.chunk.content_digest,
                    refs=edge_case_item.refs,
                )
            )
            for ac_id in canonicalize_acceptance_ids(
                edge_case_item.refs.acceptance_criterion_ids,
                canonical_ac_ids,
            ):
                criterion_chunk_id = next(
                    (criterion.chunk.chunk_id for criterion in test_plan.content.acceptance_criteria if criterion.id == ac_id),
                    "",
                )
                if criterion_chunk_id:
                    index.edges.append(
                        PlanningChunkEdge(
                            from_chunk_id=edge_case_item.chunk.chunk_id,
                            to_chunk_id=criterion_chunk_id,
                            edge_type="edge_case_covers_ac",
                        )
                    )

    if decisions is not None:
        for decision in decisions.content.decisions:
            refs = TraceRefs(
                decision_ids=[decision.id],
                decision_aliases=decision.aliases,
            )
            index.nodes.append(
                PlanningChunkNode(
                    chunk_id=decision.chunk.chunk_id or f"decision:{decisions.meta.artifact_key}:{decision.id}",
                    chunk_type="decision",
                    artifact_key=decisions.meta.artifact_key,
                    title=decision.statement,
                    order=decision.chunk.order,
                    content_digest=decision.chunk.content_digest or _json_digest(decision.model_dump(mode="json")),
                    refs=refs,
                )
            )

    if plan is not None:
        for step in plan.content.steps:
            step_refs = _merge_trace_refs(
                canonical_step_refs.get(step.id, step.refs),
                TraceRefs(
                    requirement_ids=step.requirement_ids,
                    journey_ids=step.journey_ids,
                    acceptance_criterion_ids=owned_by_step.get(step.id, []),
                ),
            )
            for requirement_id in step_refs.requirement_ids:
                req_chunk_id = next(
                    (req.chunk.chunk_id for req in (prd.content.structured_requirements if prd is not None else []) if req.id == requirement_id),
                    f"req:{prd.meta.artifact_key if prd is not None else f'prd:{slug}'}:{requirement_id}",
                )
                index.edges.append(PlanningChunkEdge(from_chunk_id=step.chunk.chunk_id, to_chunk_id=req_chunk_id, edge_type="traces_requirement"))
            for journey_id in step_refs.journey_ids:
                journey_chunk_id = next(
                    (journey.chunk.chunk_id for journey in (prd.content.journeys if prd is not None else []) if journey.id == journey_id),
                    next(
                        (
                            journey_verification.chunk.chunk_id
                            for journey_verification in (plan.content.journey_verifications if plan is not None else [])
                            if journey_verification.journey_id == journey_id
                        ),
                        f"journey:{prd.meta.artifact_key if prd is not None else f'prd:{slug}'}:{journey_id}",
                    ),
                )
                index.edges.append(PlanningChunkEdge(from_chunk_id=step.chunk.chunk_id, to_chunk_id=journey_chunk_id, edge_type="traces_journey"))
            for decision_id in step_refs.decision_ids:
                decision_chunk_id = next(
                    (decision.chunk.chunk_id for decision in (decisions.content.decisions if decisions is not None else []) if decision.id == decision_id),
                    f"decision:{decisions.meta.artifact_key if decisions is not None else f'decisions:{slug}'}:{decision_id}",
                )
                index.edges.append(PlanningChunkEdge(from_chunk_id=step.chunk.chunk_id, to_chunk_id=decision_chunk_id, edge_type="traces_decision"))
            for alias in step_refs.decision_aliases:
                canonical_id = shared_index.decision_alias_map.get(alias) if shared_index is not None else ""
                if not canonical_id and decisions is not None:
                    canonical_id = next((decision.id for decision in decisions.content.decisions if alias in decision.aliases), "")
                if canonical_id:
                    decision_chunk_id = next(
                        (decision.chunk.chunk_id for decision in (decisions.content.decisions if decisions is not None else []) if decision.id == canonical_id),
                        f"decision:{decisions.meta.artifact_key if decisions is not None else f'decisions:{slug}'}:{canonical_id}",
                    )
                    index.edges.append(PlanningChunkEdge(from_chunk_id=step.chunk.chunk_id, to_chunk_id=decision_chunk_id, edge_type="traces_decision_alias"))
                else:
                    report.issues.append(
                        ArtifactAuditIssue(
                            classification="alias_resolution_gap",
                            artifact_family="plan",
                            artifact_key=plan.meta.artifact_key,
                            message=f"{step.id} references unresolved decision alias {alias}",
                            extracted_value=alias,
                        )
                    )
            for state_id in step_refs.verifiable_state_ids:
                state_chunk_id = next(
                    (state.chunk.chunk_id for state in (design.content.verifiable_states if design is not None else []) if (state.id or f"{state.component_id}#{state.state_name}") == state_id),
                    next(
                        (
                            f"state:{system_design.meta.artifact_key}:{state_id}"
                            for system_state_id in system_design_state_ids
                            if system_state_id == state_id and system_design is not None
                        ),
                        f"state:{design.meta.artifact_key if design is not None else f'design:{slug}'}:{state_id}",
                    ),
                )
                index.edges.append(PlanningChunkEdge(from_chunk_id=step.chunk.chunk_id, to_chunk_id=state_chunk_id, edge_type="traces_verifiable_state"))
            for nfr_id in step_refs.nfr_ids:
                index.nodes.append(
                    PlanningChunkNode(
                        chunk_id=f"nfr:plan:{slug}:{nfr_id}",
                        chunk_type="nfr",
                        artifact_key=plan.meta.artifact_key,
                        title=nfr_id,
                        order=0,
                        content_digest=_content_digest(nfr_id),
                        refs=TraceRefs(nfr_ids=[nfr_id]),
                    )
                )
                index.edges.append(PlanningChunkEdge(from_chunk_id=step.chunk.chunk_id, to_chunk_id=f"nfr:plan:{slug}:{nfr_id}", edge_type="traces_nfr"))

            owned_ac_ids = sorted(
                set(owned_by_step.get(step.id, []))
            )
            global_ac_ids = sorted(
                criterion_id
                for criterion_id, candidates in step_candidates.items()
                if len(candidates) > 1
                and step.id in candidates
                and criterion_owner_step_ids.get(criterion_id) != step.id
            )
            overlay_chunk_ids = sorted(step_overlay_chunk_ids.get(step.id, set()))
            step_node = next((node for node in index.nodes if node.chunk_id == step.chunk.chunk_id), None)
            if step_node is not None:
                step_node.refs = step_refs
            index.slice_inputs.append(
                SliceInputChunkSet(
                    slice_id=step.id.lower().replace("step-", "slice-"),
                    step_chunk_ids=[step.chunk.chunk_id],
                    overlay_chunk_ids=overlay_chunk_ids,
                    requirement_ids=sorted(set(step.requirement_ids) | set(step.refs.requirement_ids)),
                    journey_ids=sorted(set(step.journey_ids) | set(step.refs.journey_ids)),
                    owned_acceptance_criterion_ids=owned_ac_ids,
                    supporting_acceptance_criterion_ids=sorted(
                        criterion_id
                        for criterion_id, candidates in step_candidates.items()
                        if len(candidates) > 1
                        and step.id in candidates
                        and criterion_owner_step_ids.get(criterion_id) != step.id
                    ),
                    global_obligation_ac_ids=global_ac_ids,
                    required_reference_sources=sorted(
                        source
                        for source, enabled in (
                            ("plan", True),
                            ("prd", prd is not None),
                            ("design", design is not None),
                            ("system-design", system_design is not None),
                            ("test-plan", test_plan is not None),
                            ("decisions", decisions is not None and bool(step.refs.decision_ids or step.refs.decision_aliases)),
                        )
                        if enabled
                    ),
                    content_digest=_json_digest(
                        {
                            "step_chunk_ids": [step.chunk.chunk_id],
                            "overlay_chunk_ids": overlay_chunk_ids,
                            "requirement_ids": sorted(set(step.requirement_ids) | set(step.refs.requirement_ids)),
                            "journey_ids": sorted(set(step.journey_ids) | set(step.refs.journey_ids)),
                            "owned_acceptance_criterion_ids": owned_ac_ids,
                            "supporting_acceptance_criterion_ids": sorted(
                                criterion_id
                                for criterion_id, candidates in step_candidates.items()
                                if len(candidates) > 1
                                and step.id in candidates
                                and criterion_owner_step_ids.get(criterion_id) != step.id
                            ),
                            "global_obligation_ac_ids": global_ac_ids,
                        }
                    ),
                )
            )
    index.step_order = step_order
    report.generated_sidecars = sorted(structured_artifact_key(key) for key in artifacts)
    report.source_hashes = source_digests
    index.index_digest = _json_digest(index.model_dump(mode="json"))
    return index, report


def update_backfill_status(
    status: ArtifactBackfillStatus | None,
    *,
    slug: str | None = None,
    artifact_family: str,
    source_hash: str,
    sidecar_key_name: str,
    sidecar_digest: str,
    parity_messages: list[str],
    shared: bool = False,
    migrated: bool = False,
    join_complete: bool = False,
    planning_index_digest: str = "",
) -> ArtifactBackfillStatus:
    status = status or ArtifactBackfillStatus()
    artifact_status = ArtifactBackfillArtifactStatus(
        status="parity_failed" if parity_messages else ("migrated" if migrated else "backfilled"),
        source_hash=source_hash,
        sidecar_key=sidecar_key_name,
        sidecar_digest=sidecar_digest,
        parity_messages=parity_messages,
    )
    if shared:
        status.shared_statuses[artifact_family] = artifact_status
        return status
    if slug is None:
        raise ValueError("slug is required for subfeature backfill status")
    subfeature_status = status.subfeatures.setdefault(slug, ArtifactBackfillSubfeatureStatus(slug=slug))
    subfeature_status.artifact_statuses[artifact_family] = artifact_status
    if join_complete:
        subfeature_status.join_complete = True
        subfeature_status.planning_index_digest = planning_index_digest
    artifact_states = {item.status for item in subfeature_status.artifact_statuses.values()}
    if "parity_failed" in artifact_states:
        subfeature_status.migration_state = "parity_failed"
    elif join_complete and artifact_states and artifact_states <= {"backfilled", "migrated"}:
        subfeature_status.migration_state = "migrated"
    elif artifact_states:
        subfeature_status.migration_state = "backfilled"
    return status


def sidecar_content_type_for_key(artifact_key: str) -> type[BaseModel]:
    family = artifact_family_for_key(artifact_key)
    if family == "decomposition":
        return SubfeatureDecomposition
    if family == "prd":
        return PRD
    if family == "design":
        return DesignDecisions
    if family == "plan":
        return TechnicalPlan
    if family == "system-design":
        return SystemDesign
    if family == "test-plan":
        return TestPlan
    if family == "decisions":
        return DecisionLedger
    raise ValueError(f"unsupported artifact family for sidecar: {artifact_key}")
