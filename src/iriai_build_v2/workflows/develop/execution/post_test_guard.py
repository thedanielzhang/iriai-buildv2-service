"""Canonical home for the post-test readiness guard surface.

Per ``docs/execution-control-plane/11-refactor-map.md`` § "Boundary-level
API contracts" row for ``execution/post_test_guard.py``:
"``PostTestReadinessGuard.assert_ready(feature_id)``. Effective-DAG
completion checks, post-DAG gate completion checks, and no-active-
control-plane-work checks before ``PostTestObservationPhase``. Must
not own: Collecting post-test observations or dispatching product
fixes."

This module is CREATED by Slice 11m (the THIRD CREATE pattern in
Slice 11, after Slice 11a ``execution/types.py`` and Slice 11l
``execution/post_dag_gates.py``).

**Slice 11m inventory finding (legitimate near-no-op):** the
exhaustive search of ``workflows/develop/phases/implementation.py``
(32489 lines at the end of Slice 11l, 489 function definitions)
identified **zero** pure post-test-guard primitives. The closest
semantically-adjacent helpers — the ``_post_dag_gate_*`` family
(Slice 11l canonical home: ``execution/post_dag_gates.py``) and the
``_dag_group_checkpoint_*`` / ``_checkpoint_*_proof`` cluster — are
POST-DAG-GATE concerns, not POST-TEST-GUARD concerns. The actual
post-test logic lives in ``workflows/develop/phases/
post_test_observation.py`` (26 function definitions + the
``PostTestObservationPhase`` class). Per the prompt edge-case clause
for Slice 11m, ``post_test_observation.py`` is a SIBLING PHASE — not
a target for extraction. Slice 11m therefore CREATES this module
with the boundary docstring and an empty ``__all__`` to reserve the
canonical home for the future ``PostTestReadinessGuard.assert_ready()``
implementation, but does not move any helpers.

This module must NOT import from ``workflows.develop.phases.
implementation`` (compatibility flows point IN, never OUT — locked by
a back-import guard test in
``tests/workflows/develop/execution/test_post_test_guard_extraction.py``).

When future slices land the ``PostTestReadinessGuard.assert_ready()``
implementation, every public name added here will be re-exported from
``workflows/develop/phases/implementation.py`` via a parallel sibling
shim block (the Slice-11a / Slice-11l pattern). Until then, the
``__all__`` list is empty and no shim block exists in
``implementation.py``.
"""

from __future__ import annotations


__all__: list[str] = []
