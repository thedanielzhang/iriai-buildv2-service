from __future__ import annotations

from copy import deepcopy
from typing import Any

from ...models.outputs import SubfeatureDecomposition
from ...planning_signals import BACKGROUND_RESPONSE

PLANNING_CONTROL_KEY = "planning_control"
STEP_INTERACTIVE = "interactive"
STEP_AGENT_FILL = "agent_fill"
STEP_PENDING = "pending"
STEP_RUNNING = "running"
STEP_COMPLETE = "complete"
STEP_BLOCKED = "blocked"

_ARTIFACT_PREFIXES = ("prd", "design", "plan", "system-design", "test-plan")
_BROAD_STEPS = ("prd", "design", "architecture", "decomposition", "reconciliation")
_SUBFEATURE_STEPS = ("pm", "design", "architecture", "test_planning")


def _default_step(mode: str = STEP_INTERACTIVE) -> dict[str, Any]:
    return {
        "mode": mode,
        "mode_selected": False,
        "status": STEP_PENDING,
        "provenance": "",
        "resolver": "",
        "thread_id": "",
        "last_updated": "",
        "background_task": {"active": False, "status": "", "step": "", "reason": ""},
    }


def default_planning_control() -> dict[str, Any]:
    return {
        "current_stage": "scoping",
        "decision_seq": 0,
        "broad_steps": {step: _default_step() for step in _BROAD_STEPS},
        "subfeatures": {},
        "provenance": {prefix: "" for prefix in _ARTIFACT_PREFIXES},
    }


def load_planning_control(*, state: Any | None = None, feature: Any | None = None) -> dict[str, Any]:
    control = default_planning_control()
    source = None
    if state is not None:
        source = getattr(state, "metadata", {}) or {}
    if (not source or PLANNING_CONTROL_KEY not in source) and feature is not None:
        source = getattr(feature, "metadata", {}) or {}
    existing = deepcopy((source or {}).get(PLANNING_CONTROL_KEY, {}))
    if not isinstance(existing, dict):
        existing = {}
    control.update(existing)
    control["broad_steps"] = {
        **default_planning_control()["broad_steps"],
        **(existing.get("broad_steps", {}) if isinstance(existing.get("broad_steps"), dict) else {}),
    }
    control["provenance"] = {
        **default_planning_control()["provenance"],
        **(existing.get("provenance", {}) if isinstance(existing.get("provenance"), dict) else {}),
    }
    control["decision_seq"] = int(existing.get("decision_seq", control.get("decision_seq", 0)) or 0)
    control["subfeatures"] = deepcopy(
        existing.get("subfeatures", {}) if isinstance(existing.get("subfeatures"), dict) else {}
    )
    return control


async def persist_planning_control(runner: Any, feature: Any, state: Any, control: dict[str, Any]) -> None:
    if getattr(state, "metadata", None) is None:
        state.metadata = {}
    state.metadata[PLANNING_CONTROL_KEY] = deepcopy(control)
    feature_meta = dict(getattr(feature, "metadata", {}) or {})
    feature_meta[PLANNING_CONTROL_KEY] = deepcopy(control)
    feature.metadata = feature_meta

    feature_store = getattr(runner, "feature_store", None)
    if feature_store and hasattr(feature_store, "update_metadata"):
        await feature_store.update_metadata(
            feature.id,
            {PLANNING_CONTROL_KEY: deepcopy(control)},
        )


def set_current_stage(control: dict[str, Any], stage: str) -> None:
    control["current_stage"] = stage


def mark_compiled_provenance(control: dict[str, Any], artifact_prefix: str, values: list[str]) -> None:
    normalized = {v for v in values if v}
    if not normalized:
        return
    if len(normalized) == 1:
        control["provenance"][artifact_prefix] = next(iter(normalized))
    else:
        control["provenance"][artifact_prefix] = "mixed"


def ensure_subfeature_threads(control: dict[str, Any], decomposition: SubfeatureDecomposition) -> None:
    subfeatures = control.setdefault("subfeatures", {})
    for sf in decomposition.subfeatures:
        record = deepcopy(subfeatures.get(sf.slug, {})) if isinstance(subfeatures.get(sf.slug), dict) else {}
        record.setdefault("thread_id", f"subfeature:{sf.slug}")
        record.setdefault("resolver", "")
        record.setdefault("thread_ts", "")
        record.setdefault("label", sf.name)
        record.setdefault("status", STEP_PENDING)
        record.setdefault(
            "background_task",
            {"active": False, "status": "", "step": "", "reason": ""},
        )
        steps = record.get("steps", {}) if isinstance(record.get("steps"), dict) else {}
        for step in _SUBFEATURE_STEPS:
            existing = deepcopy(steps.get(step, {})) if isinstance(steps.get(step), dict) else {}
            merged = _default_step()
            merged.update(existing)
            merged.setdefault("thread_id", record["thread_id"])
            steps[step] = merged
        record["steps"] = steps
        subfeatures[sf.slug] = record


def sync_subfeature_threads(control: dict[str, Any], decomposition: SubfeatureDecomposition) -> None:
    """Synchronize pre-subfeature thread metadata to the reconciled decomposition."""
    subfeatures = control.setdefault("subfeatures", {})
    valid_slugs = {sf.slug for sf in decomposition.subfeatures}
    for slug in list(subfeatures):
        if slug not in valid_slugs:
            del subfeatures[slug]
    ensure_subfeature_threads(control, decomposition)


def get_thread_record(control: dict[str, Any], slug: str) -> dict[str, Any]:
    return control.setdefault("subfeatures", {}).setdefault(slug, {
        "thread_id": f"subfeature:{slug}",
        "resolver": "",
        "thread_ts": "",
        "label": slug,
        "status": STEP_PENDING,
        "background_task": {"active": False, "status": "", "step": "", "reason": ""},
        "steps": {step: _default_step() for step in _SUBFEATURE_STEPS},
    })


def get_step_record(control: dict[str, Any], slug: str, step: str) -> dict[str, Any]:
    record = get_thread_record(control, slug)
    steps = record.setdefault("steps", {})
    if step not in steps:
        steps[step] = _default_step()
    return steps[step]


def get_broad_step_record(control: dict[str, Any], step: str) -> dict[str, Any]:
    broad = control.setdefault("broad_steps", {})
    if step not in broad:
        broad[step] = _default_step()
    return broad[step]


def set_thread_runtime_metadata(
    control: dict[str, Any],
    *,
    slug: str | None = None,
    step: str,
    resolver: str,
    thread_id: str,
    thread_ts: str = "",
    label: str = "",
) -> None:
    if slug is None:
        record = get_broad_step_record(control, step)
        record["resolver"] = resolver
        record["thread_id"] = thread_id
        if thread_ts:
            record["thread_ts"] = thread_ts
        if label:
            record["label"] = label
        return

    thread = get_thread_record(control, slug)
    thread["resolver"] = resolver
    thread["thread_id"] = thread_id
    if thread_ts:
        thread["thread_ts"] = thread_ts
    if label:
        thread["label"] = label
    step_record = get_step_record(control, slug, step)
    step_record["resolver"] = resolver
    step_record["thread_id"] = thread_id


def set_step_mode(control: dict[str, Any], *, step: str, mode: str, slug: str | None = None) -> None:
    record = get_broad_step_record(control, step) if slug is None else get_step_record(control, slug, step)
    record["mode"] = mode
    record["mode_selected"] = True


def set_step_status(
    control: dict[str, Any],
    *,
    step: str,
    status: str,
    slug: str | None = None,
    provenance: str | None = None,
) -> None:
    record = get_broad_step_record(control, step) if slug is None else get_step_record(control, slug, step)
    record["status"] = status
    if provenance:
        record["provenance"] = provenance


def set_background_state(
    control: dict[str, Any],
    *,
    step: str,
    active: bool,
    slug: str | None = None,
    status: str = "",
    reason: str = "",
) -> None:
    if slug is None:
        record = get_broad_step_record(control, step)
        record["background_task"] = {
            "active": active,
            "status": status,
            "step": step,
            "reason": reason,
        }
        return

    thread = get_thread_record(control, slug)
    thread["background_task"] = {
        "active": active,
        "status": status,
        "step": step,
        "reason": reason,
    }
    step_record = get_step_record(control, slug, step)
    step_record["background_task"] = deepcopy(thread["background_task"])
