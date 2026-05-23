from __future__ import annotations

import json
from pathlib import Path
from typing import Any


INDEX_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "execution_control_plane"
    / "compatibility_consumers.json"
)

REQUIRED_PREFIXES = {
    "dag-task:",
    "dag-task-contract:",
    "dag-contract-verdict:",
    "dag-sandbox-patch:",
    "dag-verify:",
    "dag-commit-failure:",
    "dag-group:",
    "dag-regroup:",
    "dag-regroup-active:",
    "dag-merge-queue:",
}

REQUIRED_CONSUMERS = {
    "legacy resume": ("resume",),
    "verification/repair": ("verify", "repair"),
    "regroup": ("regroup",),
    "post-test guard": ("post", "test", "guard"),
    "dashboard": ("dashboard",),
    "public dashboard": ("public", "dashboard"),
    "supervisor classifier": ("supervisor", "classifier"),
    "supervisor evidence": ("supervisor", "evidence"),
    "supervisor MCP": ("supervisor", "mcp"),
    "supervisor Slack": ("supervisor", "slack"),
    "queue recovery": ("queue", "recovery"),
}


def _load_index() -> list[dict[str, Any]]:
    assert INDEX_PATH.exists(), (
        "missing Slice 00 compatibility consumer index fixture: "
        f"{INDEX_PATH}"
    )
    assert INDEX_PATH.is_file(), f"compatibility index path is not a file: {INDEX_PATH}"

    with INDEX_PATH.open(encoding="utf-8") as handle:
        data = json.load(handle)

    if isinstance(data, dict):
        entries = data.get("consumers", data.get("compatibility_consumers"))
    else:
        entries = data
    assert isinstance(entries, list), (
        f"{INDEX_PATH} must contain a list or an object with a consumers list"
    )
    assert entries, f"{INDEX_PATH} must contain at least one consumer"
    assert all(isinstance(entry, dict) for entry in entries), (
        f"{INDEX_PATH} must contain only consumer objects"
    )
    return list(entries)


def _strings(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {value}
    if isinstance(value, list):
        found: set[str] = set()
        for item in value:
            found |= _strings(item)
        return found
    if isinstance(value, dict):
        found = set()
        for item in value.values():
            found |= _strings(item)
        return found
    return {str(value)}


def _entry_name(entry: dict[str, Any]) -> str:
    for key in (
        "consumer_name",
        "consumer_role",
        "consumer_id",
        "name",
        "consumer",
        "id",
    ):
        value = entry.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _entry_text(entry: dict[str, Any]) -> str:
    return " ".join(sorted(_strings(entry))).lower().replace("-", " ")


def _entry_prefixes(entry: dict[str, Any]) -> set[str]:
    prefixes: set[str] = set()
    for key in (
        "legacy_artifact_key_prefixes",
        "artifact_prefixes_read",
        "legacy_prefixes",
        "artifact_key_prefixes",
        "prefixes",
        "reads",
    ):
        for value in _strings(entry.get(key)):
            if value.startswith("dag-"):
                prefixes.add(value)
    return prefixes


def test_compatibility_consumer_index_covers_required_legacy_prefixes():
    entries = _load_index()

    observed: set[str] = set()
    for entry in entries:
        observed |= _entry_prefixes(entry)

    assert REQUIRED_PREFIXES <= observed, (
        "compatibility index is missing legacy artifact prefixes: "
        f"{sorted(REQUIRED_PREFIXES - observed)}"
    )


def test_compatibility_consumer_index_covers_required_surfaces():
    entries = _load_index()
    matched_entries_by_label: dict[str, list[str]] = {}

    for label, tokens in REQUIRED_CONSUMERS.items():
        matched = [
            _entry_name(entry) or _entry_text(entry)[:80]
            for entry in entries
            if all(token in _entry_text(entry) for token in tokens)
        ]
        matched_entries_by_label[label] = matched

    missing = [
        label
        for label, matched_entries in matched_entries_by_label.items()
        if not matched_entries
    ]
    assert not missing, (
        "compatibility index is missing required consumers/surfaces: "
        f"{missing}"
    )


def test_compatibility_consumer_entries_are_actionable_for_migration():
    entries = _load_index()

    for entry in entries:
        name = _entry_name(entry)
        assert name, f"consumer entry is missing a stable name: {entry}"
        assert _entry_prefixes(entry), (
            f"consumer {name!r} must list legacy artifact key prefixes read"
        )

        code_citations = entry.get(
            "code_citation",
            entry.get("code_citations", entry.get("files")),
        )
        assert _strings(code_citations), (
            f"consumer {name!r} must include code citation evidence"
        )

        assert _strings(
            entry.get("current_body_read_behavior", entry.get("body_read_behavior"))
        ), f"consumer {name!r} must record current body-read behavior"
        assert _strings(
            entry.get(
                "bounded_read_alternative",
                entry.get("bounded_alternative", entry.get("bounded_read_status")),
            )
        ), f"consumer {name!r} must record bounded-read status or an alternative"
        assert _strings(entry.get("consumer_role", entry.get("migration_owner"))), (
            f"consumer {name!r} must name its compatibility role or migration owner"
        )
