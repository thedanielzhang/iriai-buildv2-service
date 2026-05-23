"""Canonical home for the execution control plane.

Per ``docs/execution-control-plane/11-refactor-map.md`` § "Boundary-level
API contracts" row for ``execution/control_plane.py``:
"``ExecutionControlPlane.run(feature, state, runner, adapter) ->
DagExecutionOutcome``. State-machine orchestration, wave/group
sequencing, transition ordering, quiesce propagation. Must not own:
Git commands, provider calls, direct artifact body scans, legacy key
construction."

This module is CREATED by Slice 12a-1 (the FOURTH CREATE pattern in
the Slice 11/12 refactor series, after Slice 11a ``execution/types.py``
+ Slice 11l ``execution/post_dag_gates.py`` + Slice 11m
``execution/post_test_guard.py``). Slice 12a-1 establishes the
canonical home with the cheap PURE-helper batch — the deferred-final
extraction of ``ImplementationPhase.execute`` orchestration glue +
``_implement_dag`` + the post-DAG gate inner sequence are PR 11.12
12a-2 + 12a-3 (future iterations) per the split decision recorded in
the implementation journal BEFORE-chunk entry.

**Slice 12a-1 inventory finding (cheap pure batch).** The exhaustive
search of ``workflows/develop/phases/implementation.py`` (32489 lines
at end of Slice 11n / 498 function definitions) for PURE primitives
that directly fit ``control_plane.py``'s "quiesce propagation"
mandate returned 6 candidates: 2 constants
(``DAG_QUIESCE_AFTER_GROUP_ENV`` + ``DEFAULT_DAG_QUIESCE_AFTER_GROUP``)
and 4 helpers (``_dag_quiesce_after_group`` env-driven getter +
``_quiesce_marker_matches`` pure dict matcher +
``_workflow_blocker_text`` central marker-prefixing primitive used
50+ times throughout ``implementation.py`` +
``_is_workflow_blocker_text`` marker predicate). All depend only on
stdlib + module-logger + ``_SANDBOX_WORKFLOW_BLOCKER_MARKER`` (already
moved to ``execution/types.py`` by Slice 11a).

The orchestration-glue surface (``ImplementationPhase`` class +
``_implement_dag`` + ``_maybe_quiesce_before_group_dispatch`` +
``_resolve_active_regroup_before_group_dispatch`` + the post-DAG gate
inner sequence) is PHASE-COUPLED and STAYS in ``implementation.py``
in Slice 12a-1. The follow-on Slice 12a-2 + 12a-3 own the typed
``ExecutionControlPlane.run`` facade + the
``ImplementationPhase.execute`` shrink to phase adaptation + service
assembly + quiesce propagation + post-DAG gate delegation +
compatibility wrapper exports (per doc 11 § "PR 11.12").

This module must NOT import from ``workflows.develop.phases.
implementation`` (compatibility flows point IN, never OUT — locked by
a back-import guard test in
``tests/workflows/develop/execution/test_control_plane_extraction.py``).

Every public name here is re-exported from
``workflows/develop/phases/implementation.py`` via a Slice-12a-1 shim
import block (parallel sibling to the Slice 11a-11l blocks at
``implementation.py:309-807``), so every existing
``from iriai_build_v2.workflows.develop.phases.``
``implementation import X``
keeps resolving to the same object after the Slice-12a-1 extraction
(the doc-11 § "How To Use This Map" four-question contract). The
identity contract is locked by a sibling test.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from .types import _SANDBOX_WORKFLOW_BLOCKER_MARKER


logger = logging.getLogger(__name__)


__all__ = [
    "DAG_QUIESCE_AFTER_GROUP_ENV",
    "DEFAULT_DAG_QUIESCE_AFTER_GROUP",
    "_dag_quiesce_after_group",
    "_is_workflow_blocker_text",
    "_quiesce_marker_matches",
    "_workflow_blocker_text",
]


# --- Quiesce-propagation env config ----------------------------------------

DAG_QUIESCE_AFTER_GROUP_ENV = "IRIAI_DAG_QUIESCE_AFTER_GROUP"
"""Env var that overrides the default group index to quiesce after.

Moved byte-for-byte from ``workflows/develop/phases/implementation.py``
``:829`` by Slice 12a-1.
"""

DEFAULT_DAG_QUIESCE_AFTER_GROUP = 44
"""Default group index after which DAG dispatch quiesces.

Moved byte-for-byte from ``workflows/develop/phases/implementation.py``
``:843`` by Slice 12a-1. The Slice 09 G45-G73 regroup window starts at
group 45, so the default quiesce point is one group BEFORE the regroup
window opens.
"""


def _dag_quiesce_after_group() -> int | None:
    raw = os.environ.get(DAG_QUIESCE_AFTER_GROUP_ENV)
    if raw is None or not raw.strip():
        return DEFAULT_DAG_QUIESCE_AFTER_GROUP
    if raw.strip().lower() in {"0", "false", "no", "off", "disabled"}:
        return None
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            "Invalid %s=%r; using default group %d",
            DAG_QUIESCE_AFTER_GROUP_ENV,
            raw,
            DEFAULT_DAG_QUIESCE_AFTER_GROUP,
        )
        return DEFAULT_DAG_QUIESCE_AFTER_GROUP


# --- Quiesce-marker identity comparison ------------------------------------


def _quiesce_marker_matches(
    payload: dict[str, Any],
    expected_identity: dict[str, Any],
) -> bool:
    for key, expected in expected_identity.items():
        if payload.get(key) != expected:
            return False
    return True


# --- Workflow-blocker text marker primitives -------------------------------


def _workflow_blocker_text(message: str) -> str:
    return (
        message
        if _SANDBOX_WORKFLOW_BLOCKER_MARKER in str(message)
        else f"{_SANDBOX_WORKFLOW_BLOCKER_MARKER}: {message}"
    )


def _is_workflow_blocker_text(message: str) -> bool:
    return _SANDBOX_WORKFLOW_BLOCKER_MARKER in str(message)
