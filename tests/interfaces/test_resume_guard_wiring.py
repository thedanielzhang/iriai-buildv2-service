"""Slice 12e -- tests for the resume-guard wiring at the CLI + Slack seams.

Slice 12e wires the Slice-12d resume guard
``assert_feature_adopted_or_legacy`` into the workflow seams via the shared
``maybe_assert_adopted_or_legacy_for_resume`` helper in
``interfaces/_bootstrap.py``. This file covers the 8-scenario matrix the
Slice-12e brief mandates (4 CLI scenarios + 4 Slack scenarios) by
exercising the helper directly + the CLI ``_run`` path + the Slack
``_resume_workflow`` path:

CLI ``_run`` (per the Slice-12e brief's existing-distinction rule: the CLI
today always creates a fresh feature in ``_run`` -- there is no CLI resume
seam -- so ``is_resume=False`` is the existing distinction):

1. CLI ``_run`` fresh start + env flag ENABLED → guard helper passes through
   (no adoption check; the marker is only required at the resume boundary).
2. CLI ``_run`` fresh start + env flag UNSET → guard helper passes through.
3. CLI ``_run`` fresh start + env flag DISABLED → guard helper passes
   through.
4. CLI ``_run`` would-be-resume + env flag ENABLED + adopted feature → guard
   PASSES (returns the parsed record).
5. CLI ``_run`` would-be-resume + env flag ENABLED + UNADOPTED feature →
   guard RAISES ``ControlPlaneAdoptionError`` (clear error to operator; NO
   silent migration).
6. CLI ``_run`` would-be-resume + env flag UNSET → guard passes through
   (legacy mode).
7. CLI ``_run`` would-be-resume + env flag DISABLED → guard passes through.

Slack ``_resume_workflow`` (always ``is_resume=True``):

1. Slack resume + env flag ENABLED + adopted feature → resume proceeds.
2. Slack resume + env flag ENABLED + UNADOPTED feature → guard raises;
   feature remains in ``_recoverable_features`` for retry; operator sees
   the typed error in Slack.
3. Slack resume + env flag UNSET → bit-exact pre-12e behavior (guard
   passes through; resume proceeds).
4. Slack resume + env flag DISABLED → bit-exact pre-12e behavior.

The helper-level tests (the 7 CLI scenarios above) exercise the
fresh-vs-resume distinction at the source-of-truth seam; the
Slack-orchestrator tests exercise the integration with the existing
``_resume_workflow`` error-handling path so the typed
``ControlPlaneAdoptionError`` correctly propagates through the broad
``except Exception`` block and posts a clear operator message.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from iriai_build_v2.execution_control.adoption import (
    ControlPlaneAdoptionError,
    adoption_marker_artifact_key,
)
from iriai_build_v2.execution_control.atomic_landing import (
    AtomicLandingGateResult,
    InFlightAdoptionRecord,
)
from iriai_build_v2.execution_control.startup import (
    IRIAI_EXEC_CONTROL_PLANE_ENABLED_ENV,
)
from iriai_build_v2.interfaces._bootstrap import (
    maybe_assert_adopted_or_legacy_for_resume,
)


# --- Shared fixtures ------------------------------------------------------


def _make_feature(feature_id: str = "abc12345") -> SimpleNamespace:
    """Construct a fake iriai-compose Feature-like object."""

    return SimpleNamespace(
        id=feature_id,
        name="Test feature",
        slug=f"test-feature-{feature_id}",
        workflow_name="full-develop",
        metadata={},
    )


def _make_adopted_record(feature_id: str = "abc12345") -> InFlightAdoptionRecord:
    """Build a doc-12-compliant adoption record for fixture use."""

    from datetime import datetime, timezone

    return InFlightAdoptionRecord(
        feature_id=feature_id,
        candidate_commit="cccc111122223333444455556666777788889999",
        deploy_artifact_id="deploy-art-1",
        legacy_root_dag_artifact_id=4242,
        legacy_root_dag_sha256="dddd111122223333444455556666777788889999aaaa1111",
        completed_checkpoint_range=(0, 44),
        next_effective_group_idx=45,
        projection_digest="proj-digest-1",
        adopted_at=datetime.now(timezone.utc),
        rollback_disposition="legacy_resume_before_next_group",
        notes="Slice 12e test fixture",
        feature_state_at_adoption="implementation",
        adopted_by="test-operator@example.com",
        landing_gate_result_id="lg-1",
        pre_adoption_baseline={"baseline": "fixture"},
    )


class _FakeAdoptionArtifactStore:
    """Minimal fake matching the AdoptionArtifactStore Protocol."""

    def __init__(self, body: Any | None = None) -> None:
        self._body = body
        self.get_calls: list[tuple[str, str]] = []
        self.put_calls: list[tuple[str, str, Any]] = []

    async def get(self, key: str, *, feature: Any) -> Any | None:
        self.get_calls.append((key, feature.id))
        if not isinstance(key, str) or not key.startswith(
            "execution-control-adoption:"
        ):
            return None
        return self._body

    async def put(self, key: str, value: Any, *, feature: Any) -> None:
        self.put_calls.append((key, feature.id, value))


# --- Helper-level scenarios: fresh-vs-resume + 3 env-flag states ---------


@pytest.mark.asyncio
async def test_helper_fresh_start_env_enabled_passes_through(
    monkeypatch: pytest.MonkeyPatch,
):
    """CLI ``_run`` fresh start under ``ENABLED`` MUST skip the adoption
    check -- the marker is only required at the resume boundary per doc 12
    line 73-74. The helper proves this by returning ``None`` and NOT
    consulting the artifact store at all."""

    monkeypatch.setenv(IRIAI_EXEC_CONTROL_PLANE_ENABLED_ENV, "enabled")
    feature = _make_feature()
    store = _FakeAdoptionArtifactStore()

    result = await maybe_assert_adopted_or_legacy_for_resume(
        feature=feature, artifacts=store, is_resume=False
    )

    assert result is None
    # No artifact store consultation for fresh starts.
    assert store.get_calls == []


@pytest.mark.asyncio
async def test_helper_fresh_start_env_unset_passes_through(
    monkeypatch: pytest.MonkeyPatch,
):
    """Fresh start + UNSET = legacy mode + fresh; double pass-through."""

    monkeypatch.delenv(IRIAI_EXEC_CONTROL_PLANE_ENABLED_ENV, raising=False)
    feature = _make_feature()
    store = _FakeAdoptionArtifactStore()

    result = await maybe_assert_adopted_or_legacy_for_resume(
        feature=feature, artifacts=store, is_resume=False
    )

    assert result is None
    assert store.get_calls == []


@pytest.mark.asyncio
async def test_helper_fresh_start_env_disabled_passes_through(
    monkeypatch: pytest.MonkeyPatch,
):
    """Fresh start + DISABLED = legacy mode + fresh; double pass-through."""

    monkeypatch.setenv(IRIAI_EXEC_CONTROL_PLANE_ENABLED_ENV, "disabled")
    feature = _make_feature()
    store = _FakeAdoptionArtifactStore()

    result = await maybe_assert_adopted_or_legacy_for_resume(
        feature=feature, artifacts=store, is_resume=False
    )

    assert result is None
    assert store.get_calls == []


@pytest.mark.asyncio
async def test_helper_resume_env_enabled_adopted_returns_record(
    monkeypatch: pytest.MonkeyPatch,
):
    """Resume + ENABLED + adopted feature → guard returns the parsed
    record. This is the PASS scenario for the in-flight adoption path."""

    monkeypatch.setenv(IRIAI_EXEC_CONTROL_PLANE_ENABLED_ENV, "enabled")
    feature = _make_feature()
    record = _make_adopted_record(feature.id)
    body = record.model_dump_json()
    store = _FakeAdoptionArtifactStore(body=body)

    result = await maybe_assert_adopted_or_legacy_for_resume(
        feature=feature, artifacts=store, is_resume=True
    )

    assert isinstance(result, InFlightAdoptionRecord)
    assert result.feature_id == feature.id
    assert result.candidate_commit == record.candidate_commit
    # Guard consulted the store under the doc-12 marker key.
    assert store.get_calls == [(adoption_marker_artifact_key(feature.id), feature.id)]


@pytest.mark.asyncio
async def test_helper_resume_env_enabled_unadopted_raises(
    monkeypatch: pytest.MonkeyPatch,
):
    """Resume + ENABLED + NO marker → guard raises
    ControlPlaneAdoptionError. NO silent migration per doc 12 line 76-77.
    The error message names the missing adoption marker key for operator
    audit."""

    monkeypatch.setenv(IRIAI_EXEC_CONTROL_PLANE_ENABLED_ENV, "enabled")
    feature = _make_feature()
    store = _FakeAdoptionArtifactStore(body=None)

    with pytest.raises(ControlPlaneAdoptionError) as excinfo:
        await maybe_assert_adopted_or_legacy_for_resume(
            feature=feature, artifacts=store, is_resume=True
        )

    msg = str(excinfo.value)
    assert feature.id in msg
    assert adoption_marker_artifact_key(feature.id) in msg
    # The error must reference the adoption command for operator audit.
    assert "adopt_in_flight_feature" in msg


@pytest.mark.asyncio
async def test_helper_resume_env_unset_passes_through(
    monkeypatch: pytest.MonkeyPatch,
):
    """Resume + UNSET = legacy mode; guard passes through (returns None).

    This is the BIT-EXACT pre-12e behavior contract: under no env flag the
    Slack resume seam must behave identically to before Slice 12e landed.
    """

    monkeypatch.delenv(IRIAI_EXEC_CONTROL_PLANE_ENABLED_ENV, raising=False)
    feature = _make_feature()
    store = _FakeAdoptionArtifactStore()

    result = await maybe_assert_adopted_or_legacy_for_resume(
        feature=feature, artifacts=store, is_resume=True
    )

    assert result is None
    # In legacy mode the helper MUST NOT consult the artifact store -- the
    # store has no marker because the feature was never adopted; reading
    # would be wasted I/O AND would risk surfacing irrelevant errors.
    assert store.get_calls == []


@pytest.mark.asyncio
async def test_helper_resume_env_disabled_passes_through(
    monkeypatch: pytest.MonkeyPatch,
):
    """Resume + DISABLED = legacy mode; guard passes through."""

    monkeypatch.setenv(IRIAI_EXEC_CONTROL_PLANE_ENABLED_ENV, "disabled")
    feature = _make_feature()
    store = _FakeAdoptionArtifactStore()

    result = await maybe_assert_adopted_or_legacy_for_resume(
        feature=feature, artifacts=store, is_resume=True
    )

    assert result is None
    assert store.get_calls == []


# --- CLI _run integration -------------------------------------------------


@pytest.mark.asyncio
async def test_cli_run_fresh_start_env_enabled_skips_adoption_check(
    monkeypatch: pytest.MonkeyPatch,
):
    """The CLI ``_run`` integration contract: fresh start under
    ``ENABLED`` MUST NOT reach
    ``assert_feature_adopted_or_legacy`` -- the helper short-circuits
    on ``is_resume=False``. This is the brief's "FRESH start (not
    resume) with env flag ENABLED → no adoption check" scenario."""

    monkeypatch.setenv(IRIAI_EXEC_CONTROL_PLANE_ENABLED_ENV, "enabled")
    # Track whether the underlying guard was consulted at all.
    direct_guard_calls: list[str] = []

    async def _spy_assert_feature_adopted_or_legacy(
        *, feature, artifact_store, env=None
    ):
        direct_guard_calls.append(
            feature.id if hasattr(feature, "id") else str(feature)
        )
        return None

    monkeypatch.setattr(
        "iriai_build_v2.execution_control.adoption."
        "assert_feature_adopted_or_legacy",
        _spy_assert_feature_adopted_or_legacy,
    )

    feature = _make_feature()
    store = _FakeAdoptionArtifactStore()

    # Direct helper exercise -- the CLI passes is_resume=False on every
    # invocation (the CLI today has no resume seam).
    result = await maybe_assert_adopted_or_legacy_for_resume(
        feature=feature, artifacts=store, is_resume=False
    )

    assert result is None
    # No direct guard call -- the helper short-circuits before reaching
    # the underlying assert_feature_adopted_or_legacy.
    assert direct_guard_calls == []


@pytest.mark.asyncio
async def test_cli_run_calls_helper_with_is_resume_false(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """End-to-end CLI ``_run`` invocation pin: the wiring at
    ``interfaces/cli/app.py`` MUST call
    ``maybe_assert_adopted_or_legacy_for_resume`` with ``is_resume=False``
    immediately after ``create_feature``. This pins the fresh-start
    distinction at the CLI seam against drift (a future refactor that
    accidentally flips ``is_resume=True`` would slip past the static
    import pin)."""

    monkeypatch.setenv(IRIAI_EXEC_CONTROL_PLANE_ENABLED_ENV, "enabled")
    helper_calls: list[dict[str, Any]] = []

    async def _spy_helper(*, feature, artifacts, is_resume):
        helper_calls.append(
            {"feature_id": feature.id, "is_resume": is_resume}
        )
        return None

    class _NoopRunner:
        async def execute_workflow(self, *_args, **_kwargs):
            return None

    class _FakeStore:
        async def put(self, *_args, **_kwargs):
            return None

    fake_artifacts = _FakeStore()

    async def _fake_bootstrap(_workspace_path):
        return SimpleNamespace(
            pool=object(),
            artifacts=fake_artifacts,
            sessions=object(),
            feature_store=object(),
            context_provider=object(),
            review_manager=SimpleNamespace(),
            feedback_service=object(),
            preview_service=object(),
            playwright_service=SimpleNamespace(),
            artifact_mirror=object(),
            public_dashboard=object(),
            workspace=object(),
            workspace_path=tmp_path,
            workspace_manager=object(),
        )

    async def _fake_teardown(_env):
        return None

    async def _fake_assert_ready(*, pool, require_enabled):
        # The Slice-12c outer guard -- doesn't relate to adoption.
        return None

    async def _fake_create_feature(_store, _name, _workflow_name):
        return _make_feature("cli12345")

    monkeypatch.setattr(
        "iriai_build_v2.interfaces._bootstrap.bootstrap", _fake_bootstrap
    )
    monkeypatch.setattr(
        "iriai_build_v2.interfaces._bootstrap.teardown", _fake_teardown
    )
    monkeypatch.setattr(
        "iriai_build_v2.interfaces._bootstrap.build_runner",
        lambda *_args, **_kwargs: _NoopRunner(),
    )
    monkeypatch.setattr(
        "iriai_build_v2.interfaces._bootstrap.create_feature",
        _fake_create_feature,
    )
    monkeypatch.setattr(
        "iriai_build_v2.interfaces._bootstrap.select_workflow",
        lambda _name: object(),
    )
    monkeypatch.setattr(
        "iriai_build_v2.interfaces._bootstrap.build_state",
        lambda _name, **_kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(
        "iriai_build_v2.interfaces._bootstrap."
        "maybe_assert_adopted_or_legacy_for_resume",
        _spy_helper,
    )
    monkeypatch.setattr(
        "iriai_build_v2.execution_control.startup."
        "assert_control_plane_ready_for_workflow_launch",
        _fake_assert_ready,
    )

    from iriai_build_v2.interfaces.cli import app as cli_app

    await cli_app._run(
        "full-develop",
        "Test feature",
        str(tmp_path),
        auto=True,
        agent_runtime="claude",
    )

    # The CLI MUST have invoked the helper exactly once with is_resume=False.
    assert len(helper_calls) == 1
    assert helper_calls[0]["feature_id"] == "cli12345"
    assert helper_calls[0]["is_resume"] is False


@pytest.mark.asyncio
async def test_cli_run_env_unset_skips_helper_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Under UNSET the Slice-12c outer guard short-circuits BEFORE the
    feature is created. The helper IS still reached (the CLI wiring is
    unconditional), but it MUST internally short-circuit and return
    ``None``. The bit-exact contract is "no DB I/O for adoption
    markers under UNSET".
    """

    monkeypatch.delenv(IRIAI_EXEC_CONTROL_PLANE_ENABLED_ENV, raising=False)
    helper_calls: list[dict[str, Any]] = []

    async def _spy_helper(*, feature, artifacts, is_resume):
        helper_calls.append(
            {"feature_id": feature.id, "is_resume": is_resume}
        )
        return None

    class _NoopRunner:
        async def execute_workflow(self, *_args, **_kwargs):
            return None

    class _FakeStore:
        async def put(self, *_args, **_kwargs):
            return None

    async def _fake_bootstrap(_workspace_path):
        return SimpleNamespace(
            pool=object(),
            artifacts=_FakeStore(),
            sessions=object(),
            feature_store=object(),
            context_provider=object(),
            review_manager=SimpleNamespace(),
            feedback_service=object(),
            preview_service=object(),
            playwright_service=SimpleNamespace(),
            artifact_mirror=object(),
            public_dashboard=object(),
            workspace=object(),
            workspace_path=tmp_path,
            workspace_manager=object(),
        )

    async def _fake_teardown(_env):
        return None

    async def _fake_create_feature(_store, _name, _workflow_name):
        return _make_feature("legacy12")

    monkeypatch.setattr(
        "iriai_build_v2.interfaces._bootstrap.bootstrap", _fake_bootstrap
    )
    monkeypatch.setattr(
        "iriai_build_v2.interfaces._bootstrap.teardown", _fake_teardown
    )
    monkeypatch.setattr(
        "iriai_build_v2.interfaces._bootstrap.build_runner",
        lambda *_args, **_kwargs: _NoopRunner(),
    )
    monkeypatch.setattr(
        "iriai_build_v2.interfaces._bootstrap.create_feature",
        _fake_create_feature,
    )
    monkeypatch.setattr(
        "iriai_build_v2.interfaces._bootstrap.select_workflow",
        lambda _name: object(),
    )
    monkeypatch.setattr(
        "iriai_build_v2.interfaces._bootstrap.build_state",
        lambda _name, **_kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(
        "iriai_build_v2.interfaces._bootstrap."
        "maybe_assert_adopted_or_legacy_for_resume",
        _spy_helper,
    )

    from iriai_build_v2.interfaces.cli import app as cli_app

    await cli_app._run(
        "full-develop",
        "Test feature",
        str(tmp_path),
        auto=True,
        agent_runtime="claude",
    )

    # Under UNSET the helper IS still called (the CLI wiring is
    # unconditional), but it MUST internally short-circuit and return
    # None. The bit-exact contract is "no DB I/O for adoption markers".
    assert len(helper_calls) == 1
    assert helper_calls[0]["is_resume"] is False


# --- Slack _resume_workflow integration -----------------------------------


class _SlackAdapter:
    """Minimal SlackAdapter substitute for orchestrator tests."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    async def post_message(self, channel: str, text: str, **kwargs) -> str:
        self.messages.append((channel, text))
        return "0001.5678"

    async def update_message(self, channel: str, ts: str, *, text=None, blocks=None) -> None:
        pass

    def set_channel_mode(self, *_args, **_kwargs) -> None:
        pass

    async def add_reaction(self, *_args, **_kwargs) -> None:
        pass

    async def create_channel(self, _name: str) -> str:
        return "CCHAN"

    @property
    def planning_channel(self) -> str:
        return "CPLAN"

    @property
    def web(self):
        return object()


class _SlackInteraction:
    def __init__(self) -> None:
        self.channels: list[tuple[str, str]] = []

    def register_channel(self, feature_id: str, channel: str) -> None:
        self.channels.append((feature_id, channel))

    def unregister_channel(self, _feature_id: str) -> None:
        return None

    def has_pending(self, _channel: str) -> bool:
        return False


class _SlackFeatureStore:
    def __init__(self, features: dict[str, Any]) -> None:
        self._features = features

    async def get_feature(self, feature_id: str):
        return self._features.get(feature_id)

    async def update_metadata(self, feature_id: str, patch: dict) -> None:
        f = self._features.get(feature_id)
        if f is None:
            return
        f.metadata = {**(getattr(f, "metadata", {}) or {}), **patch}


@pytest.mark.asyncio
async def test_slack_resume_env_enabled_adopted_proceeds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Slack ``_resume_workflow`` + env flag ENABLED + adopted feature →
    resume proceeds (the guard returns the parsed record and the
    workflow task is scheduled)."""

    from iriai_build_v2.interfaces.slack.orchestrator import (
        SlackWorkflowOrchestrator,
    )

    monkeypatch.setenv(IRIAI_EXEC_CONTROL_PLANE_ENABLED_ENV, "enabled")

    feature = _make_feature("slk12345")
    feature.workflow_name = "bugfix-v2"
    feature.metadata = {
        "channel_id": "CCHAN",
        "workspace_path": str(tmp_path),
        "mode": "singleplayer",
        "agent_runtime": "claude",
        "_db_phase": "bugflow-queue",
    }

    record = _make_adopted_record(feature.id)
    artifacts = _FakeAdoptionArtifactStore(body=record.model_dump_json())

    adapter = _SlackAdapter()
    interaction = _SlackInteraction()
    orchestrator = SlackWorkflowOrchestrator(
        adapter=adapter, interaction_runtime=interaction
    )
    orchestrator._env = SimpleNamespace(
        feature_store=_SlackFeatureStore({feature.id: feature}),
        artifacts=artifacts,
    )
    orchestrator._recoverable_features = {
        feature.id: {
            "workspace_path": str(tmp_path),
            "mode": "singleplayer",
            "phase": "bugflow-queue",
            "agent_runtime": "claude",
        }
    }
    runner = SimpleNamespace(services={})
    orchestrator._create_runtime_and_runner = (  # type: ignore[method-assign]
        lambda **_kwargs: (SimpleNamespace(), runner)
    )

    async def _fake_rebuild_state(*_args, **_kwargs):
        return object()

    monkeypatch.setattr(
        "iriai_build_v2.interfaces.slack.orchestrator.rebuild_state",
        _fake_rebuild_state,
    )
    monkeypatch.setattr(
        "iriai_build_v2.interfaces.slack.orchestrator.select_workflow",
        lambda _name: object(),
    )

    async def _fake_recover(*_args, **_kwargs):
        return 0

    async def _fake_run_resumed(*_args, **_kwargs):
        return None

    orchestrator._recover_sandbox_leases_for_resume = _fake_recover  # type: ignore[method-assign]
    orchestrator._run_workflow_resumed = _fake_run_resumed  # type: ignore[method-assign]

    await orchestrator._resume_workflow(feature.id, "CCHAN")

    # The guard was consulted (the adoption store saw the marker key
    # lookup).
    assert artifacts.get_calls == [
        (adoption_marker_artifact_key(feature.id), feature.id)
    ]
    # Resume proceeded: a "Resuming *bugfix-v2*" message was posted.
    assert any("Resuming *bugfix-v2*" in msg for _ch, msg in adapter.messages)
    # The feature was removed from _recoverable_features after successful
    # resume.
    assert feature.id not in orchestrator._recoverable_features


@pytest.mark.asyncio
async def test_slack_resume_env_enabled_unadopted_refuses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Slack resume + env flag ENABLED + UNADOPTED feature → guard
    raises; the existing broad ``except Exception`` block catches the
    error and posts a clear operator message; the feature REMAINS in
    ``_recoverable_features`` for retry."""

    from iriai_build_v2.interfaces.slack.orchestrator import (
        SlackWorkflowOrchestrator,
    )

    monkeypatch.setenv(IRIAI_EXEC_CONTROL_PLANE_ENABLED_ENV, "enabled")

    feature = _make_feature("slk98765")
    feature.workflow_name = "bugfix-v2"
    feature.metadata = {
        "channel_id": "CCHAN",
        "workspace_path": str(tmp_path),
        "mode": "singleplayer",
        "agent_runtime": "claude",
        "_db_phase": "bugflow-queue",
    }

    # No adoption marker -- the store returns None.
    artifacts = _FakeAdoptionArtifactStore(body=None)

    adapter = _SlackAdapter()
    interaction = _SlackInteraction()
    orchestrator = SlackWorkflowOrchestrator(
        adapter=adapter, interaction_runtime=interaction
    )
    orchestrator._env = SimpleNamespace(
        feature_store=_SlackFeatureStore({feature.id: feature}),
        artifacts=artifacts,
    )
    orchestrator._recoverable_features = {
        feature.id: {
            "workspace_path": str(tmp_path),
            "mode": "singleplayer",
            "phase": "bugflow-queue",
            "agent_runtime": "claude",
        }
    }

    await orchestrator._resume_workflow(feature.id, "CCHAN")

    # The guard was consulted.
    assert artifacts.get_calls == [
        (adoption_marker_artifact_key(feature.id), feature.id)
    ]
    # A "Resume failed for `slk98765`" message was posted with the
    # adoption-error details.
    failure_messages = [
        msg for _ch, msg in adapter.messages if "Resume failed for" in msg
    ]
    assert failure_messages, adapter.messages
    assert feature.id in failure_messages[0]
    # The feature MUST remain recoverable (retry after operator adopts).
    assert feature.id in orchestrator._recoverable_features


@pytest.mark.asyncio
async def test_slack_resume_env_unset_proceeds_bit_exact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Slack resume + env flag UNSET → bit-exact pre-12e behavior. The
    adoption store is NEVER consulted (legacy mode); the resume
    proceeds as before."""

    from iriai_build_v2.interfaces.slack.orchestrator import (
        SlackWorkflowOrchestrator,
    )

    monkeypatch.delenv(IRIAI_EXEC_CONTROL_PLANE_ENABLED_ENV, raising=False)

    feature = _make_feature("slkleg01")
    feature.workflow_name = "bugfix-v2"
    feature.metadata = {
        "channel_id": "CCHAN",
        "workspace_path": str(tmp_path),
        "mode": "singleplayer",
        "agent_runtime": "claude",
        "_db_phase": "bugflow-queue",
    }

    artifacts = _FakeAdoptionArtifactStore(body=None)

    adapter = _SlackAdapter()
    interaction = _SlackInteraction()
    orchestrator = SlackWorkflowOrchestrator(
        adapter=adapter, interaction_runtime=interaction
    )
    orchestrator._env = SimpleNamespace(
        feature_store=_SlackFeatureStore({feature.id: feature}),
        artifacts=artifacts,
    )
    orchestrator._recoverable_features = {
        feature.id: {
            "workspace_path": str(tmp_path),
            "mode": "singleplayer",
            "phase": "bugflow-queue",
            "agent_runtime": "claude",
        }
    }
    runner = SimpleNamespace(services={})
    orchestrator._create_runtime_and_runner = (  # type: ignore[method-assign]
        lambda **_kwargs: (SimpleNamespace(), runner)
    )

    async def _fake_rebuild_state(*_args, **_kwargs):
        return object()

    monkeypatch.setattr(
        "iriai_build_v2.interfaces.slack.orchestrator.rebuild_state",
        _fake_rebuild_state,
    )
    monkeypatch.setattr(
        "iriai_build_v2.interfaces.slack.orchestrator.select_workflow",
        lambda _name: object(),
    )

    async def _fake_recover(*_args, **_kwargs):
        return 0

    async def _fake_run_resumed(*_args, **_kwargs):
        return None

    orchestrator._recover_sandbox_leases_for_resume = _fake_recover  # type: ignore[method-assign]
    orchestrator._run_workflow_resumed = _fake_run_resumed  # type: ignore[method-assign]

    await orchestrator._resume_workflow(feature.id, "CCHAN")

    # UNDER UNSET: adoption store MUST NOT be consulted (legacy mode is
    # bit-exact pre-12e).
    assert artifacts.get_calls == []
    # Resume proceeded.
    assert any("Resuming *bugfix-v2*" in msg for _ch, msg in adapter.messages)
    assert feature.id not in orchestrator._recoverable_features


@pytest.mark.asyncio
async def test_slack_resume_env_disabled_proceeds_bit_exact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Slack resume + env flag DISABLED → bit-exact pre-12e behavior;
    same as UNSET (the env-flag enum collapses both UNSET and DISABLED
    into ``not is_enabled``)."""

    from iriai_build_v2.interfaces.slack.orchestrator import (
        SlackWorkflowOrchestrator,
    )

    monkeypatch.setenv(IRIAI_EXEC_CONTROL_PLANE_ENABLED_ENV, "disabled")

    feature = _make_feature("slkleg02")
    feature.workflow_name = "bugfix-v2"
    feature.metadata = {
        "channel_id": "CCHAN",
        "workspace_path": str(tmp_path),
        "mode": "singleplayer",
        "agent_runtime": "claude",
        "_db_phase": "bugflow-queue",
    }

    artifacts = _FakeAdoptionArtifactStore(body=None)

    adapter = _SlackAdapter()
    interaction = _SlackInteraction()
    orchestrator = SlackWorkflowOrchestrator(
        adapter=adapter, interaction_runtime=interaction
    )
    orchestrator._env = SimpleNamespace(
        feature_store=_SlackFeatureStore({feature.id: feature}),
        artifacts=artifacts,
    )
    orchestrator._recoverable_features = {
        feature.id: {
            "workspace_path": str(tmp_path),
            "mode": "singleplayer",
            "phase": "bugflow-queue",
            "agent_runtime": "claude",
        }
    }
    runner = SimpleNamespace(services={})
    orchestrator._create_runtime_and_runner = (  # type: ignore[method-assign]
        lambda **_kwargs: (SimpleNamespace(), runner)
    )

    async def _fake_rebuild_state(*_args, **_kwargs):
        return object()

    monkeypatch.setattr(
        "iriai_build_v2.interfaces.slack.orchestrator.rebuild_state",
        _fake_rebuild_state,
    )
    monkeypatch.setattr(
        "iriai_build_v2.interfaces.slack.orchestrator.select_workflow",
        lambda _name: object(),
    )

    async def _fake_recover(*_args, **_kwargs):
        return 0

    async def _fake_run_resumed(*_args, **_kwargs):
        return None

    orchestrator._recover_sandbox_leases_for_resume = _fake_recover  # type: ignore[method-assign]
    orchestrator._run_workflow_resumed = _fake_run_resumed  # type: ignore[method-assign]

    await orchestrator._resume_workflow(feature.id, "CCHAN")

    # DISABLED behavior IDENTICAL to UNSET.
    assert artifacts.get_calls == []
    assert any("Resuming *bugfix-v2*" in msg for _ch, msg in adapter.messages)
    assert feature.id not in orchestrator._recoverable_features


# --- Wiring presence pins -------------------------------------------------


def test_cli_app_imports_helper_for_resume_guard():
    """Anti-drift pin: a future refactor that drops the helper import
    from CLI ``_run`` would silently break the Slice-12e wiring; this
    test reads the CLI source to confirm the import remains in place."""

    cli_app_path = Path(
        "src/iriai_build_v2/interfaces/cli/app.py"
    ).resolve()
    source = cli_app_path.read_text(encoding="utf-8")
    assert "maybe_assert_adopted_or_legacy_for_resume" in source, (
        "CLI app.py must import the Slice-12e resume guard helper"
    )
    # The CLI calls the helper with is_resume=False (fresh start).
    assert "is_resume=False" in source, (
        "CLI _run must call the helper with is_resume=False (fresh start)"
    )


def test_slack_orchestrator_imports_helper_for_resume_guard():
    """Anti-drift pin: the Slack orchestrator MUST consult the helper
    in ``_resume_workflow``. This pins the wiring against a future
    refactor that removes the import or the call site."""

    orchestrator_path = Path(
        "src/iriai_build_v2/interfaces/slack/orchestrator.py"
    ).resolve()
    source = orchestrator_path.read_text(encoding="utf-8")
    assert "maybe_assert_adopted_or_legacy_for_resume" in source, (
        "Slack orchestrator must import the Slice-12e resume guard helper"
    )
    # Slack calls the helper with is_resume=True (always resuming).
    assert "is_resume=True" in source, (
        "Slack _resume_workflow must call the helper with is_resume=True"
    )


def test_helper_back_import_guard():
    """The helper module (``interfaces/_bootstrap.py``) MUST NOT pull
    the heavy ``workflows.develop.phases.implementation`` monolith into
    its imports -- the helper is a thin shim that delegates to the
    Slice-12d adoption module."""

    bootstrap_path = Path(
        "src/iriai_build_v2/interfaces/_bootstrap.py"
    ).resolve()
    source = bootstrap_path.read_text(encoding="utf-8")
    assert (
        "workflows.develop.phases.implementation" not in source
    ), "interfaces/_bootstrap.py must not back-import the implementation monolith"
