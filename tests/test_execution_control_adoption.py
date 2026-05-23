"""Slice 12d -- tests for the in-flight adoption workflow.

Covers the doc-12 release-control adoption surface:

- The marker key/body helpers (``adoption_marker_artifact_key``,
  ``is_adoption_marker_key``, ``ADOPTION_MARKER_KEY_PREFIX``).
- The adoption command ``adopt_in_flight_feature`` — the 3 fail-closed paths
  (landing-gate not "go", artifact-id / candidate-commit mismatch, double-
  adopt) + the idempotency mechanism.
- The marker reader ``read_adoption_marker`` — absence vs presence vs corrupt
  body.
- The resume guard ``assert_feature_adopted_or_legacy`` — pass-through under
  UNSET/DISABLED env flag (legacy mode); refusal under ENABLED + absent
  marker; return-record under ENABLED + present marker.
- Back-import guard against ``workflows.develop.phases.implementation``.

Per the prompt: "Test surface must be COMPREHENSIVE (this is a critical-path
contract). Cover positive, negative, idempotent, and back-import paths."
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from iriai_build_v2.execution_control import adoption
from iriai_build_v2.execution_control.adoption import (
    ADOPTION_MARKER_KEY_PREFIX,
    AdoptionMarkerCorruptError,
    ControlPlaneAdoptionError,
    adopt_in_flight_feature,
    adoption_marker_artifact_key,
    assert_feature_adopted_or_legacy,
    is_adoption_marker_key,
    read_adoption_marker,
)
from iriai_build_v2.execution_control.atomic_landing import (
    AtomicLandingGateResult,
    InFlightAdoptionRecord,
)
from iriai_build_v2.execution_control.startup import (
    IRIAI_EXEC_CONTROL_PLANE_ENABLED_ENV,
)


# ── helpers ─────────────────────────────────────────────────────────────────


class _FakeArtifactStore:
    """In-memory artifact store keyed by ``(feature_id, key)``.

    Records every ``put`` / ``get`` so tests can assert idempotency and
    write counts directly.
    """

    def __init__(self) -> None:
        self._data: dict[tuple[str, str], Any] = {}
        self.put_calls: list[tuple[str, str, Any]] = []
        self.get_calls: list[tuple[str, str]] = []

    async def get(self, key: str, *, feature: Any) -> Any | None:
        feature_id = getattr(feature, "id", feature)
        self.get_calls.append((feature_id, key))
        return self._data.get((feature_id, key))

    async def put(self, key: str, value: Any, *, feature: Any) -> None:
        feature_id = getattr(feature, "id", feature)
        self.put_calls.append((feature_id, key, value))
        self._data[(feature_id, key)] = value


def _fake_feature(feature_id: str = "feat0001") -> SimpleNamespace:
    return SimpleNamespace(id=feature_id, name="sample feature")


def _passed_landing_gate(
    *,
    candidate_id: str = "candidate-2026-05",
    candidate_commit: str = "abc123deadbeef",
    deploy_artifact_id: str = "artifact-2026-05-23",
) -> AtomicLandingGateResult:
    """A minimally-populated passing landing-gate result.

    Note: ``AtomicLandingGateResult.__init__`` runs ``evaluate_go_requires``
    in its model_validator and refuses ``passed=True`` unless every
    readiness gate is present + decided_by/decided_at/rollback_runbook_id /
    ci_matrix / metrics are set + forbidden_partial_controls_enabled is
    empty + blockers is empty. So we populate the full happy-path bundle.
    """

    from iriai_build_v2.execution_control.atomic_landing import (
        REQUIRED_READINESS_GATES,
    )

    return AtomicLandingGateResult(
        candidate_id=candidate_id,
        candidate_commit=candidate_commit,
        deploy_artifact_id=deploy_artifact_id,
        passed=True,
        operational_decision="go",
        required_tests=["pytest tests/ -q"],
        required_gate_results={name: "passed" for name in REQUIRED_READINESS_GATES},
        ci_matrix_run_id="ci-run-42",
        metrics_snapshot_id=4242,
        decided_by="operator-alice",
        decided_at=datetime(2026, 5, 23, 16, 0, tzinfo=timezone.utc),
        rollback_runbook_id="runbook-v3.1",
        forbidden_partial_controls_enabled=[],
        blockers=[],
    )


def _adoption_kwargs(
    *,
    landing: AtomicLandingGateResult,
    feature: Any,
    artifact_store: Any,
    **overrides: Any,
) -> dict[str, Any]:
    """Helper -- minimal valid kwargs for ``adopt_in_flight_feature``."""

    base: dict[str, Any] = {
        "feature": feature,
        "landing_gate_result": landing,
        "candidate_commit": landing.candidate_commit,
        "deploy_artifact_id": landing.deploy_artifact_id,
        "artifact_store": artifact_store,
        "legacy_root_dag_artifact_id": 4242,
        "legacy_root_dag_sha256": "f" * 64,
        "completed_checkpoint_range": (0, 3),
        "next_effective_group_idx": 4,
        "projection_digest": "p" * 64,
        "active_regroup_artifact_ids": [101, 102],
        "workspace_snapshot_ids": [201],
        "adopted_by": "operator-alice",
        "landing_gate_result_id": "alg-42",
        "feature_state_at_adoption": "quiesce-before-group-4",
        "pre_adoption_baseline": {"completed_groups": 3, "queue_depth": 0},
        "notes": "first safe boundary after dag-group:3 commit-proof",
        "now": datetime(2026, 5, 23, 17, 0, tzinfo=timezone.utc),
    }
    base.update(overrides)
    return base


# ── marker key/body helpers ─────────────────────────────────────────────────


def test_adoption_marker_key_prefix_matches_doc_12() -> None:
    """Doc 12 § 'In-Flight Cutover Policy' line 69 names the key pattern
    ``execution-control-adoption:{feature_id}``; pin the prefix constant
    verbatim."""

    assert ADOPTION_MARKER_KEY_PREFIX == "execution-control-adoption:"


def test_adoption_marker_artifact_key_format() -> None:
    """The helper returns ``execution-control-adoption:{feature_id}``."""

    assert adoption_marker_artifact_key("abc123") == (
        "execution-control-adoption:abc123"
    )
    assert adoption_marker_artifact_key("feat0001") == (
        "execution-control-adoption:feat0001"
    )


def test_adoption_marker_artifact_key_rejects_blank_feature_id() -> None:
    """A blank ``feature_id`` is rejected -- a marker key without a feature
    id cannot be used as an idempotency key."""

    with pytest.raises(ValueError):
        adoption_marker_artifact_key("")
    with pytest.raises(ValueError):
        adoption_marker_artifact_key("   ")
    with pytest.raises(ValueError):
        adoption_marker_artifact_key(123)  # type: ignore[arg-type]


def test_is_adoption_marker_key_recognizes_prefix() -> None:
    """The helper returns ``True`` for keys under the prefix, ``False``
    otherwise."""

    assert is_adoption_marker_key("execution-control-adoption:abc123") is True
    assert is_adoption_marker_key("execution-control-adoption:") is True
    assert is_adoption_marker_key("execution-control-legacy:abc123") is False
    assert is_adoption_marker_key("dag-group:0") is False
    assert is_adoption_marker_key("") is False
    assert is_adoption_marker_key(None) is False  # type: ignore[arg-type]


# ── adopt_in_flight_feature happy path ─────────────────────────────────────


@pytest.mark.asyncio
async def test_adopt_in_flight_feature_happy_path() -> None:
    """A landed go decision + matching artifacts + no prior marker writes
    the marker exactly once and returns the typed record."""

    store = _FakeArtifactStore()
    feature = _fake_feature()
    landing = _passed_landing_gate()

    record = await adopt_in_flight_feature(
        **_adoption_kwargs(landing=landing, feature=feature, artifact_store=store)
    )

    # Returned record carries the inputs.
    assert isinstance(record, InFlightAdoptionRecord)
    assert record.feature_id == "feat0001"
    assert record.candidate_commit == landing.candidate_commit
    assert record.deploy_artifact_id == landing.deploy_artifact_id
    assert record.completed_checkpoint_range == (0, 3)
    assert record.next_effective_group_idx == 4
    assert record.adopted_by == "operator-alice"
    assert record.landing_gate_result_id == "alg-42"

    # The marker was written EXACTLY ONCE under the doc-12 key.
    expected_key = adoption_marker_artifact_key("feat0001")
    assert len(store.put_calls) == 1
    assert store.put_calls[0][0] == "feat0001"
    assert store.put_calls[0][1] == expected_key

    # The body parses back into the same record.
    body = store.put_calls[0][2]
    rebuilt = InFlightAdoptionRecord.model_validate_json(body)
    assert rebuilt == record


@pytest.mark.asyncio
async def test_adopt_in_flight_feature_marker_body_is_serializable() -> None:
    """The marker body is exactly the ``model_dump_json()`` of the typed
    record -- the legacy ``_execution_control_marker_payload`` reader at
    ``implementation.py:2472`` consumes JSON dicts under this same key."""

    store = _FakeArtifactStore()
    feature = _fake_feature()
    landing = _passed_landing_gate()

    record = await adopt_in_flight_feature(
        **_adoption_kwargs(landing=landing, feature=feature, artifact_store=store)
    )

    body = store.put_calls[0][2]
    parsed = json.loads(body)
    assert isinstance(parsed, dict)
    # The legacy reader matches on ``feature_id`` (see
    # _execution_control_marker_is_valid at implementation.py:2488); the
    # marker MUST carry the feature_id field as a top-level key.
    assert parsed["feature_id"] == record.feature_id
    # Doc-12 verbatim keys are present.
    for key in (
        "candidate_commit",
        "deploy_artifact_id",
        "legacy_root_dag_artifact_id",
        "completed_checkpoint_range",
        "rollback_disposition",
    ):
        assert key in parsed


# ── adopt_in_flight_feature fail-closed paths ──────────────────────────────


@pytest.mark.asyncio
async def test_adopt_in_flight_feature_refuses_no_go_landing_gate() -> None:
    """Fail-closed path 1: landing-gate is not 'go' -> refuse."""

    store = _FakeArtifactStore()
    feature = _fake_feature()
    no_go = AtomicLandingGateResult.no_go(
        candidate_id="candidate-2026-05",
        candidate_commit="abc123deadbeef",
        deploy_artifact_id="artifact-2026-05-23",
        blockers=["ci-matrix:full_regression:failed"],
    )

    with pytest.raises(ControlPlaneAdoptionError) as excinfo:
        await adopt_in_flight_feature(
            **_adoption_kwargs(
                landing=no_go, feature=feature, artifact_store=store
            )
        )
    message = str(excinfo.value)
    assert "feat0001" in message
    assert "not 'go'" in message
    assert "passed=False" in message
    assert "operational_decision='no_go'" in message
    # No marker was written.
    assert store.put_calls == []


@pytest.mark.asyncio
async def test_adopt_in_flight_feature_refuses_candidate_commit_mismatch() -> None:
    """Fail-closed path 2 (variant A): caller-supplied candidate_commit
    does not match the landing-gate's value -> refuse."""

    store = _FakeArtifactStore()
    feature = _fake_feature()
    landing = _passed_landing_gate(candidate_commit="abc123deadbeef")

    with pytest.raises(ControlPlaneAdoptionError) as excinfo:
        await adopt_in_flight_feature(
            **_adoption_kwargs(
                landing=landing,
                feature=feature,
                artifact_store=store,
                candidate_commit="999wrongcommit",
            )
        )
    message = str(excinfo.value)
    assert "candidate_commit" in message
    assert "abc123deadbeef" in message
    assert "999wrongcommit" in message
    assert "does not match" in message
    assert store.put_calls == []


@pytest.mark.asyncio
async def test_adopt_in_flight_feature_refuses_deploy_artifact_mismatch() -> None:
    """Fail-closed path 2 (variant B): caller-supplied deploy_artifact_id
    does not match the landing-gate's value -> refuse."""

    store = _FakeArtifactStore()
    feature = _fake_feature()
    landing = _passed_landing_gate(deploy_artifact_id="artifact-2026-05-23")

    with pytest.raises(ControlPlaneAdoptionError) as excinfo:
        await adopt_in_flight_feature(
            **_adoption_kwargs(
                landing=landing,
                feature=feature,
                artifact_store=store,
                deploy_artifact_id="artifact-2026-05-22-wrong",
            )
        )
    message = str(excinfo.value)
    assert "deploy_artifact_id" in message
    assert "artifact-2026-05-23" in message
    assert "artifact-2026-05-22-wrong" in message
    assert store.put_calls == []


@pytest.mark.asyncio
async def test_adopt_in_flight_feature_refuses_double_adopt_different_triple() -> None:
    """Fail-closed path 3 (single-shot per feature): an existing marker
    against a DIFFERENT candidate triple cannot be silently overwritten."""

    store = _FakeArtifactStore()
    feature = _fake_feature()
    landing = _passed_landing_gate()

    # First adoption -- happy path.
    record_1 = await adopt_in_flight_feature(
        **_adoption_kwargs(landing=landing, feature=feature, artifact_store=store)
    )
    assert len(store.put_calls) == 1

    # Second adoption with a DIFFERENT candidate_commit / deploy_artifact_id
    # against a different landing gate -- refuse.
    landing_2 = _passed_landing_gate(
        candidate_commit="def456nextrelease",
        deploy_artifact_id="artifact-2026-06-01",
    )
    with pytest.raises(ControlPlaneAdoptionError) as excinfo:
        await adopt_in_flight_feature(
            **_adoption_kwargs(
                landing=landing_2, feature=feature, artifact_store=store
            )
        )
    message = str(excinfo.value)
    assert "existing adoption marker" in message
    assert "feat0001" in message
    # No second marker write.
    assert len(store.put_calls) == 1


# ── adopt_in_flight_feature idempotency ─────────────────────────────────────


@pytest.mark.asyncio
async def test_adopt_in_flight_feature_is_idempotent_same_triple() -> None:
    """Idempotency: the same ``(feature_id, candidate_commit,
    deploy_artifact_id)`` triple re-finds the existing marker and returns
    the persisted record WITHOUT double-writing."""

    store = _FakeArtifactStore()
    feature = _fake_feature()
    landing = _passed_landing_gate()

    # First adoption -- happy path. Records 1 put.
    record_1 = await adopt_in_flight_feature(
        **_adoption_kwargs(landing=landing, feature=feature, artifact_store=store)
    )
    assert len(store.put_calls) == 1

    # Second adoption with the SAME triple -- idempotent: re-reads the
    # existing marker. Should return the SAME record without writing again.
    record_2 = await adopt_in_flight_feature(
        **_adoption_kwargs(landing=landing, feature=feature, artifact_store=store)
    )
    assert len(store.put_calls) == 1  # NO second write
    assert record_2 == record_1


# ── read_adoption_marker ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_read_adoption_marker_returns_none_when_absent() -> None:
    """Absent marker -> ``None`` (the typical not-yet-adopted path)."""

    store = _FakeArtifactStore()
    feature = _fake_feature()
    record = await read_adoption_marker(feature=feature, artifact_store=store)
    assert record is None


@pytest.mark.asyncio
async def test_read_adoption_marker_returns_record_when_present() -> None:
    """A persisted marker round-trips back into an ``InFlightAdoptionRecord``."""

    store = _FakeArtifactStore()
    feature = _fake_feature()
    landing = _passed_landing_gate()

    written = await adopt_in_flight_feature(
        **_adoption_kwargs(landing=landing, feature=feature, artifact_store=store)
    )

    read = await read_adoption_marker(feature=feature, artifact_store=store)
    assert read == written


@pytest.mark.asyncio
async def test_read_adoption_marker_raises_on_corrupt_body() -> None:
    """A malformed marker body (not valid JSON for InFlightAdoptionRecord)
    raises ``AdoptionMarkerCorruptError`` -- callers MUST NOT silently treat
    a corrupt marker as 'not adopted yet'."""

    store = _FakeArtifactStore()
    feature = _fake_feature()
    # Plant a body that does NOT validate (missing required fields).
    store._data[(feature.id, adoption_marker_artifact_key(feature.id))] = (
        '{"feature_id": "feat0001"}'
    )

    with pytest.raises(AdoptionMarkerCorruptError) as excinfo:
        await read_adoption_marker(feature=feature, artifact_store=store)
    assert "feat0001" in str(excinfo.value)
    assert "InFlightAdoptionRecord" in str(excinfo.value)


@pytest.mark.asyncio
async def test_read_adoption_marker_accepts_dict_body() -> None:
    """Some artifact stores parse JSON eagerly and return ``dict``; the
    reader coerces dicts back through ``json.dumps`` before validating."""

    store = _FakeArtifactStore()
    feature = _fake_feature()
    landing = _passed_landing_gate()

    # First, do a real adoption to get a valid record.
    written = await adopt_in_flight_feature(
        **_adoption_kwargs(landing=landing, feature=feature, artifact_store=store)
    )

    # Now overwrite the stored value with the parsed dict equivalent.
    parsed = json.loads(written.model_dump_json())
    store._data[(feature.id, adoption_marker_artifact_key(feature.id))] = parsed

    read = await read_adoption_marker(feature=feature, artifact_store=store)
    assert read == written


# ── assert_feature_adopted_or_legacy ────────────────────────────────────────


@pytest.mark.asyncio
async def test_assert_feature_adopted_or_legacy_passthrough_when_flag_unset() -> None:
    """Fail-OPEN (legacy) when env flag is UNSET: returns ``None`` so the
    caller continues on the legacy executor. NO automatic migration."""

    store = _FakeArtifactStore()
    feature = _fake_feature()

    result = await assert_feature_adopted_or_legacy(
        feature=feature,
        artifact_store=store,
        env={},
    )
    assert result is None
    # The guard does NOT need to read the marker in legacy mode.
    assert store.get_calls == []


@pytest.mark.asyncio
async def test_assert_feature_adopted_or_legacy_passthrough_when_flag_disabled() -> None:
    """Same fail-open semantics for explicit DISABLED."""

    store = _FakeArtifactStore()
    feature = _fake_feature()

    result = await assert_feature_adopted_or_legacy(
        feature=feature,
        artifact_store=store,
        env={IRIAI_EXEC_CONTROL_PLANE_ENABLED_ENV: "false"},
    )
    assert result is None
    assert store.get_calls == []


@pytest.mark.asyncio
async def test_assert_feature_adopted_or_legacy_refuses_when_enabled_and_marker_absent() -> None:
    """When the env flag is ENABLED and no marker is present, the guard
    refuses with ``ControlPlaneAdoptionError`` -- doc 12 § 'In-Flight
    Cutover Policy' mandates no silent migration."""

    store = _FakeArtifactStore()
    feature = _fake_feature()

    with pytest.raises(ControlPlaneAdoptionError) as excinfo:
        await assert_feature_adopted_or_legacy(
            feature=feature,
            artifact_store=store,
            env={IRIAI_EXEC_CONTROL_PLANE_ENABLED_ENV: "true"},
        )
    message = str(excinfo.value)
    assert "feat0001" in message
    assert "IRIAI_EXEC_CONTROL_PLANE_ENABLED" in message
    assert "no adoption marker exists" in message
    assert "adopt_in_flight_feature" in message
    # The expected marker key appears in the error for operator audit.
    assert adoption_marker_artifact_key("feat0001") in message


@pytest.mark.asyncio
async def test_assert_feature_adopted_or_legacy_returns_record_when_enabled_and_present() -> None:
    """When the env flag is ENABLED and the marker is present, the guard
    returns the parsed record so the caller can verify against current
    state and proceed."""

    store = _FakeArtifactStore()
    feature = _fake_feature()
    landing = _passed_landing_gate()

    written = await adopt_in_flight_feature(
        **_adoption_kwargs(landing=landing, feature=feature, artifact_store=store)
    )

    result = await assert_feature_adopted_or_legacy(
        feature=feature,
        artifact_store=store,
        env={IRIAI_EXEC_CONTROL_PLANE_ENABLED_ENV: "true"},
    )
    assert result == written


@pytest.mark.asyncio
async def test_assert_feature_adopted_or_legacy_propagates_corrupt_marker() -> None:
    """A corrupt marker under ENABLED flag surfaces as ``AdoptionMarker
    CorruptError`` -- the guard does NOT swallow it as 'not adopted'."""

    store = _FakeArtifactStore()
    feature = _fake_feature()
    store._data[(feature.id, adoption_marker_artifact_key(feature.id))] = (
        '{"bogus": true}'
    )

    with pytest.raises(AdoptionMarkerCorruptError):
        await assert_feature_adopted_or_legacy(
            feature=feature,
            artifact_store=store,
            env={IRIAI_EXEC_CONTROL_PLANE_ENABLED_ENV: "true"},
        )


@pytest.mark.asyncio
async def test_assert_feature_adopted_or_legacy_accepts_bare_feature_id_string() -> None:
    """The guard accepts a bare ``feature_id`` string in addition to a
    full ``Feature`` object (for callers that do not have the full Feature
    in hand at resume time)."""

    store = _FakeArtifactStore()
    # No marker, env flag UNSET -> legacy mode.
    result = await assert_feature_adopted_or_legacy(
        feature="feat0001",
        artifact_store=store,
        env={},
    )
    assert result is None


# ── env-flag plumbing ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_assert_feature_adopted_or_legacy_propagates_malformed_env_flag() -> None:
    """A malformed env-flag value bubbles up as
    :class:`ControlPlaneEnvFlagError` from the Slice-12c helper -- the
    resume guard does NOT swallow it (silent fallback would be a hidden
    misconfiguration)."""

    from iriai_build_v2.execution_control.startup import (
        ControlPlaneEnvFlagError,
    )

    store = _FakeArtifactStore()
    feature = _fake_feature()

    with pytest.raises(ControlPlaneEnvFlagError):
        await assert_feature_adopted_or_legacy(
            feature=feature,
            artifact_store=store,
            env={IRIAI_EXEC_CONTROL_PLANE_ENABLED_ENV: "maybe"},
        )


@pytest.mark.asyncio
async def test_adopt_in_flight_feature_uses_default_now_when_unspecified() -> None:
    """When ``now`` is not supplied, the command uses the current UTC
    datetime -- pins the adoption_at default behavior."""

    store = _FakeArtifactStore()
    feature = _fake_feature()
    landing = _passed_landing_gate()
    kwargs = _adoption_kwargs(
        landing=landing, feature=feature, artifact_store=store
    )
    del kwargs["now"]

    before = datetime.now(timezone.utc)
    record = await adopt_in_flight_feature(**kwargs)
    after = datetime.now(timezone.utc)

    assert before <= record.adopted_at <= after


# ── back-import guard ──────────────────────────────────────────────────────


def test_adoption_module_has_no_back_import_to_implementation() -> None:
    """Per the prompt hard rule: modules MUST NOT import from
    ``phases/implementation.py`` (compatibility arrow points IN, never
    OUT). Mirrors the Slice 11/12a-1/12b back-import guards."""

    source_path = Path(adoption.__file__)
    text = source_path.read_text(encoding="utf-8")
    forbidden_phrases = (
        "from iriai_build_v2.workflows.develop.phases.implementation",
        "from ..workflows.develop.phases.implementation",
        "from ...workflows.develop.phases.implementation",
        "import iriai_build_v2.workflows.develop.phases.implementation",
        "from iriai_build_v2.workflows.develop.phases",
        "from ..workflows.develop.phases",
        "from ...workflows.develop.phases",
    )
    for phrase in forbidden_phrases:
        assert phrase not in text, (
            f"adoption.py contains forbidden back-import: {phrase!r}"
        )


def test_adoption_module_does_not_import_control_plane_runtime() -> None:
    """Per doc 12: 'This slice defines release-control interfaces, NOT
    executor runtime interfaces.' The adoption module is release-control
    (sibling of atomic_landing.py); it must not import the runtime
    quiesce primitives or the ExecutionControlPlane facade."""

    source_path = Path(adoption.__file__)
    text = source_path.read_text(encoding="utf-8")
    assert "from ..workflows.develop.execution.control_plane import" not in text
    assert (
        "import iriai_build_v2.workflows.develop.execution.control_plane"
        not in text
    )


def test_adoption_module_co_locates_with_atomic_landing_module() -> None:
    """The new module sits next to the Slice-12b ``atomic_landing.py``
    sibling (both under ``execution_control/``)."""

    adoption_path = Path(adoption.__file__)
    landing_path = adoption_path.parent / "atomic_landing.py"
    startup_path = adoption_path.parent / "startup.py"
    assert landing_path.is_file(), (
        f"Slice-12b atomic_landing module should exist at {landing_path}"
    )
    assert startup_path.is_file(), (
        f"Slice-10f/12c startup module should exist at {startup_path}"
    )


def test_adoption_module_all_export_is_complete() -> None:
    """Every name exported from the module's ``__all__`` is bound at the
    module level."""

    for name in adoption.__all__:
        assert hasattr(adoption, name), (
            f"name {name!r} is in __all__ but not bound at module level"
        )


def test_adoption_module_exports_doc_12_surface() -> None:
    """The doc-12 release-control adoption surface is exported."""

    assert "adopt_in_flight_feature" in adoption.__all__
    assert "assert_feature_adopted_or_legacy" in adoption.__all__
    assert "read_adoption_marker" in adoption.__all__
    assert "adoption_marker_artifact_key" in adoption.__all__
    assert "ADOPTION_MARKER_KEY_PREFIX" in adoption.__all__
    assert "ControlPlaneAdoptionError" in adoption.__all__
    assert "AdoptionMarkerCorruptError" in adoption.__all__
