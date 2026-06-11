"""Readiness item-2 (P0-2/P-9): multi-index CHK quiesce + driver-self-clear hook.

Covers (v2 binding directive — CHK migration application delegated to the
DRIVER, runbook §17):
- ``IRIAI_QUIESCE_GROUP_INDEXES`` list parsing (+ legacy explicit fallback and
  the REMOVED implicit group-44 default);
- ``_maybe_quiesce_before_group_dispatch`` firing at EACH listed boundary,
  today-parity with no env set, and the fail-loud no-hook WARN;
- the default hook: paused hook_result carrying migration_files / probes /
  the exact clear psql, NO operator query filed, the DONE-by-driver
  OPERATOR-ACTIONS record written once across re-entry, and the driver-style
  complete marker (copied identity) skipping the boundary WITHOUT re-invoking
  the hook;
- flag-gated registration in BOTH runner-build sites.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from iriai_build_v2.models.outputs import ImplementationDAG, ImplementationTask
from iriai_build_v2.workflows.develop.execution import quiesce_gate
from iriai_build_v2.workflows.develop.execution.control_plane import (
    DAG_QUIESCE_AFTER_GROUP_ENV,
    DAG_QUIESCE_GROUP_INDEXES_ENV,
    _dag_quiesce_group_indexes,
)
from iriai_build_v2.workflows.develop.execution.quiesce_gate import (
    DAG_QUIESCE_OPERATOR_GATE_ENV,
    default_dag_quiesce_hook,
    register_default_dag_quiesce_hook,
)
from iriai_build_v2.workflows.develop.phases import implementation as implementation_module


# --------------------------------------------------------------------------- #
# Env parsing
# --------------------------------------------------------------------------- #


def _clear_envs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DAG_QUIESCE_GROUP_INDEXES_ENV, raising=False)
    monkeypatch.delenv(DAG_QUIESCE_AFTER_GROUP_ENV, raising=False)


def test_group_indexes_env_parses_list(monkeypatch):
    _clear_envs(monkeypatch)
    monkeypatch.setenv(DAG_QUIESCE_GROUP_INDEXES_ENV, " 3, 7 ,12 ,3 ")
    assert _dag_quiesce_group_indexes() == {3, 7, 12}


def test_group_indexes_env_empty_string_means_no_quiesce(monkeypatch):
    _clear_envs(monkeypatch)
    monkeypatch.setenv(DAG_QUIESCE_GROUP_INDEXES_ENV, "")
    # Even with the legacy env ALSO set, an explicit empty list wins.
    monkeypatch.setenv(DAG_QUIESCE_AFTER_GROUP_ENV, "7")
    assert _dag_quiesce_group_indexes() == set()


def test_group_indexes_invalid_tokens_are_warn_skipped(monkeypatch, caplog):
    _clear_envs(monkeypatch)
    monkeypatch.setenv(DAG_QUIESCE_GROUP_INDEXES_ENV, "3,oops,7")
    with caplog.at_level(logging.WARNING):
        assert _dag_quiesce_group_indexes() == {3, 7}
    assert any("oops" in r.message for r in caplog.records)


def test_group_indexes_legacy_explicit_env_still_honored(monkeypatch):
    _clear_envs(monkeypatch)
    monkeypatch.setenv(DAG_QUIESCE_AFTER_GROUP_ENV, "7")
    assert _dag_quiesce_group_indexes() == {7}
    monkeypatch.setenv(DAG_QUIESCE_AFTER_GROUP_ENV, "off")
    assert _dag_quiesce_group_indexes() == set()


def test_group_indexes_default_is_empty_no_group_44_leftover(monkeypatch):
    """The prior feature's implicit DEFAULT 44 must NOT leak into the set."""
    _clear_envs(monkeypatch)
    assert _dag_quiesce_group_indexes() == set()


# --------------------------------------------------------------------------- #
# Quiesce primitive: multi-boundary + today-parity + no-hook fail-loud WARN
# --------------------------------------------------------------------------- #


class _Artifacts:
    def __init__(self, store: dict[str, str] | None = None) -> None:
        self.store: dict[str, str] = dict(store or {})

    async def get(self, key: str, *, feature):
        del feature
        return self.store.get(key, "")

    async def put(self, key: str, value: str, *, feature):
        del feature
        self.store[key] = value


def _dag(groups: int) -> ImplementationDAG:
    return ImplementationDAG(
        tasks=[
            ImplementationTask(id=f"TASK-{i}", name=f"Task {i}", description=f"T{i}")
            for i in range(groups)
        ],
        execution_order=[[f"TASK-{i}"] for i in range(groups)],
        complete=True,
    )


@pytest.mark.asyncio
async def test_primitive_fires_at_each_listed_boundary(monkeypatch):
    _clear_envs(monkeypatch)
    monkeypatch.setenv(DAG_QUIESCE_GROUP_INDEXES_ENV, "1,3")
    feature = SimpleNamespace(id="feat-multi", slug="multi", metadata={})
    dag = _dag(6)
    artifacts = _Artifacts({
        "dag-group:1": json.dumps({"group_idx": 1}),
        "dag-group:3": json.dumps({"group_idx": 3}),
    })
    calls: list[tuple[int, int]] = []

    def _hook(**kwargs):
        calls.append((kwargs["after_group_idx"], kwargs["before_group_idx"]))
        return {"approved": True, "status": "ready"}

    runner = SimpleNamespace(artifacts=artifacts, services={"dag_quiesce_hook": _hook})

    for group_idx in (2, 3, 4):
        failure = await implementation_module._maybe_quiesce_before_group_dispatch(
            runner, feature, dag, group_idx=group_idx,
        )
        assert failure == ""

    assert calls == [(1, 2), (3, 4)]  # group 3 (after_group=2) is NOT listed
    assert json.loads(artifacts.store["dag-quiesce:g1-before-g2"])["status"] == "complete"
    assert json.loads(artifacts.store["dag-quiesce:g3-before-g4"])["status"] == "complete"
    assert "dag-quiesce:g2-before-g3" not in artifacts.store


@pytest.mark.asyncio
async def test_primitive_no_envs_means_no_quiesce_at_group_45(monkeypatch):
    """Today-parity replacement: the implicit 44 default no longer fires."""
    _clear_envs(monkeypatch)
    feature = SimpleNamespace(id="feat-no44", slug="no44", metadata={})
    dag = ImplementationDAG(
        tasks=[ImplementationTask(id="TASK-45", name="t", description="t")],
        execution_order=[*([] for _ in range(45)), ["TASK-45"]],
        complete=True,
    )
    artifacts = _Artifacts({"dag-group:44": json.dumps({"group_idx": 44})})
    hook_calls: list[str] = []
    runner = SimpleNamespace(
        artifacts=artifacts,
        services={"dag_quiesce_hook": lambda **k: hook_calls.append("x")},
    )

    failure = await implementation_module._maybe_quiesce_before_group_dispatch(
        runner, feature, dag, group_idx=45,
    )

    assert failure == ""
    assert hook_calls == []
    assert "dag-quiesce:g44-before-g45" not in artifacts.store


@pytest.mark.asyncio
async def test_primitive_listed_boundary_without_hook_warns_and_continues(
    monkeypatch, caplog,
):
    _clear_envs(monkeypatch)
    monkeypatch.setenv(DAG_QUIESCE_GROUP_INDEXES_ENV, "1")
    feature = SimpleNamespace(id="feat-nohook", slug="nohook", metadata={})
    dag = _dag(3)
    artifacts = _Artifacts({"dag-group:1": json.dumps({"group_idx": 1})})
    runner = SimpleNamespace(artifacts=artifacts, services={})

    with caplog.at_level(logging.WARNING):
        failure = await implementation_module._maybe_quiesce_before_group_dispatch(
            runner, feature, dag, group_idx=2,
        )

    assert failure == ""  # dispatch continues (today-parity), but loudly
    assert json.loads(artifacts.store["dag-quiesce:g1-before-g2"])["status"] == "complete"
    assert any(
        "NO dag_quiesce_hook" in r.message and "dag-quiesce:g1-before-g2" in r.message
        for r in caplog.records
    )


# --------------------------------------------------------------------------- #
# Default hook behavior (driver self-clear)
# --------------------------------------------------------------------------- #


def _hook_runner(tmp_path: Path, store: dict[str, str] | None = None):
    (tmp_path / ".iriai").mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(
        artifacts=_Artifacts(store),
        services={"workspace_manager": SimpleNamespace(_base=str(tmp_path))},
    )


def _payload() -> dict:
    return {"next_group_task_ids": ["TASK-9"], "status": "started"}


@pytest.fixture()
def _gate_env(monkeypatch):
    monkeypatch.setenv(DAG_QUIESCE_GROUP_INDEXES_ENV, "3,8")
    monkeypatch.delenv(DAG_QUIESCE_AFTER_GROUP_ENV, raising=False)


@pytest.mark.asyncio
async def test_hook_pauses_with_driver_self_clear_payload(tmp_path, _gate_env):
    store = {
        "dag-group:4": json.dumps({
            "group_idx": 4,
            "task_ids": ["TASK-7"],
            "results": [{
                "task_id": "TASK-7",
                "files_created": ["db/migrations/0012_add_links.sql"],
                "files_modified": ["app/api/routers/core.py"],
            }],
        }),
        "dag-group:8": json.dumps({
            "group_idx": 8,
            "task_ids": ["TASK-8"],
            "results": [{
                "task_id": "TASK-8",
                "files_modified": ["data-service/migrations/0013_backfill.py"],
            }],
        }),
    }
    runner = _hook_runner(tmp_path, store)
    (tmp_path / ".iriai" / "chk-probes.json").write_text(json.dumps({
        "8": ["curl -fsS http://127.0.0.1:8060/health",
              "psql -c 'select count(*) from links'"],
    }))
    feature = SimpleNamespace(id="featX", slug="featx", metadata={})

    result = await default_dag_quiesce_hook(
        runner=runner, feature=feature, payload=_payload(),
        after_group_idx=8, before_group_idx=9,
    )

    assert result["approved"] is False
    assert result["status"] == "paused"
    assert result["done_by"] == "driver"
    assert result["clear_method"] == "marker-write"
    assert "g8->g9" in result["reason"]
    # Migration FILE LIST + PROBE LIST + exact clear psql in the hook result
    # (the primitive embeds it in the paused marker).
    assert result["migration_files"] == [
        "data-service/migrations/0013_backfill.py",
        "db/migrations/0012_add_links.sql",
    ]
    assert result["probes"] == [
        "curl -fsS http://127.0.0.1:8060/health",
        "psql -c 'select count(*) from links'",
    ]
    assert result["clear_command"].startswith("INSERT INTO artifacts")
    assert "feature_id='featX'" in result["clear_command"]
    assert "key='dag-quiesce:g8-before-g9'" in result["clear_command"]
    assert '"cleared_by":"driver"' in result["clear_command"]
    hints = result["batch_hints"]
    # Boundary covers groups SINCE the previous listed boundary (3): 4..8.
    assert hints["groups_covered"] == [4, 5, 6, 7, 8]
    assert hints["covered_task_ids"] == ["TASK-7", "TASK-8"]
    assert hints["next_group_task_ids"] == ["TASK-9"]

    # NO operator query is filed (driver self-clear, no new interactive surfaces).
    qdir = tmp_path / ".iriai" / "operator-queries"
    assert not qdir.exists() or not list(qdir.glob("*.query.json"))

    actions = (tmp_path / ".iriai" / "OPERATOR-ACTIONS.md").read_text()
    assert "DONE-by-driver" in actions
    assert "CHK boundary g8->g9" in actions
    assert "0012_add_links.sql" in actions
    assert "curl -fsS http://127.0.0.1:8060/health" in actions
    assert "INSERT INTO artifacts" in actions
    assert "LOCAL kaya compose dev DB ONLY" in actions
    assert result["operator_actions_entry"] == "written"


@pytest.mark.asyncio
async def test_hook_probe_file_absent_states_path_checked(tmp_path, _gate_env):
    runner = _hook_runner(tmp_path)
    feature = SimpleNamespace(id="featX", slug="featx", metadata={})

    result = await default_dag_quiesce_hook(
        runner=runner, feature=feature, payload=_payload(),
        after_group_idx=3, before_group_idx=4,
    )

    assert result["status"] == "paused"
    assert result["probes"] == []
    assert "chk-probes.json" in result["probe_note"]
    assert "CHK section" in result["probe_note"]
    actions = (tmp_path / ".iriai" / "OPERATOR-ACTIONS.md").read_text()
    assert "chk-probes.json" in actions  # path checked is stated in the record


@pytest.mark.asyncio
async def test_hook_probe_file_g_prefixed_key(tmp_path, _gate_env):
    runner = _hook_runner(tmp_path)
    (tmp_path / ".iriai" / "chk-probes.json").write_text(
        json.dumps({"g3": ["make smoke"]})
    )
    feature = SimpleNamespace(id="featX", slug="featx", metadata={})

    result = await default_dag_quiesce_hook(
        runner=runner, feature=feature, payload=_payload(),
        after_group_idx=3, before_group_idx=4,
    )

    assert result["probes"] == ["make smoke"]
    assert "probe_note" not in result


@pytest.mark.asyncio
async def test_hook_reentry_writes_record_once(tmp_path, _gate_env):
    runner = _hook_runner(tmp_path)
    feature = SimpleNamespace(id="featX", slug="featx", metadata={})

    first = await default_dag_quiesce_hook(
        runner=runner, feature=feature, payload=_payload(),
        after_group_idx=3, before_group_idx=4,
    )
    second = await default_dag_quiesce_hook(
        runner=runner, feature=feature, payload=_payload(),
        after_group_idx=3, before_group_idx=4,
    )

    assert first["status"] == second["status"] == "paused"
    assert first["operator_actions_entry"] == "written"
    assert second["operator_actions_entry"] == "already-present"
    actions = (tmp_path / ".iriai" / "OPERATOR-ACTIONS.md").read_text()
    assert actions.count("## [PENDING]") == 1  # first-visit-only record


@pytest.mark.asyncio
async def test_hook_without_workspace_still_pauses_with_full_payload(
    tmp_path, _gate_env, caplog,
):
    """No workspace = no OPERATOR-ACTIONS record, but the paused marker still
    carries the full self-clear payload — never a silent approve."""
    runner = SimpleNamespace(artifacts=_Artifacts(), services={})
    feature = SimpleNamespace(id="featX", slug="featx", metadata={})

    with caplog.at_level(logging.ERROR):
        result = await default_dag_quiesce_hook(
            runner=runner, feature=feature, payload=_payload(),
            after_group_idx=3, before_group_idx=4,
        )

    assert result["approved"] is False
    assert result["status"] == "paused"
    assert result["clear_command"].startswith("INSERT INTO artifacts")
    assert result["operator_actions_entry"].startswith("unavailable")
    assert any("workspace_manager" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_hook_never_executes_migrations(tmp_path, _gate_env):
    """Implementation-agent prohibition: the hook has NO migration-executing
    code path — no subprocess/os.system surface in the module at all."""
    import inspect

    source = inspect.getsource(quiesce_gate)
    for forbidden in ("subprocess", "os.system", "create_subprocess"):
        assert forbidden not in source


@pytest.mark.asyncio
async def test_primitive_paused_marker_then_driver_clear_skips_without_hook(
    tmp_path, monkeypatch,
):
    """End-to-end: paused marker embeds the self-clear payload; a driver-style
    complete row (copied identity + cleared_by=driver + probe_evidence) makes
    the primitive return "" WITHOUT re-invoking the hook."""
    monkeypatch.setenv(DAG_QUIESCE_GROUP_INDEXES_ENV, "1")
    monkeypatch.delenv(DAG_QUIESCE_AFTER_GROUP_ENV, raising=False)
    (tmp_path / ".iriai").mkdir()
    feature = SimpleNamespace(id="feat-e2e", slug="e2e", metadata={})
    dag = _dag(3)
    artifacts = _Artifacts({"dag-group:1": json.dumps({"group_idx": 1})})
    hook_calls: list[str] = []

    async def counting_hook(**kwargs):
        hook_calls.append(kwargs["payload"]["status"])
        return await default_dag_quiesce_hook(**kwargs)

    runner = SimpleNamespace(
        artifacts=artifacts,
        services={
            "workspace_manager": SimpleNamespace(_base=str(tmp_path)),
            "dag_quiesce_hook": counting_hook,
        },
    )

    failure = await implementation_module._maybe_quiesce_before_group_dispatch(
        runner, feature, dag, group_idx=2,
    )

    assert "paused by quiesce hook" in failure
    assert hook_calls == ["started"]
    marker = json.loads(artifacts.store["dag-quiesce:g1-before-g2"])
    assert marker["status"] == "paused"
    assert marker["hook_result"]["done_by"] == "driver"
    assert marker["hook_result"]["clear_method"] == "marker-write"
    assert "INSERT INTO artifacts" in marker["hook_result"]["clear_command"]

    # DRIVER SELF-CLEAR: copy the latest paused payload, flip to complete
    # (exactly what the embedded psql does), then restart -> re-entry guard
    # skips the boundary without re-invoking the hook.
    cleared = {**marker, "status": "complete", "cleared_by": "driver",
               "probe_evidence": "all probes green"}
    artifacts.store["dag-quiesce:g1-before-g2"] = json.dumps(cleared)

    failure = await implementation_module._maybe_quiesce_before_group_dispatch(
        runner, feature, dag, group_idx=2,
    )
    assert failure == ""
    assert hook_calls == ["started"]  # hook NOT re-invoked after the clear
    final = json.loads(artifacts.store["dag-quiesce:g1-before-g2"])
    assert final["cleared_by"] == "driver"
    assert final["status"] == "complete"


# --------------------------------------------------------------------------- #
# Registration (flag-gated, both runner-build sites)
# --------------------------------------------------------------------------- #


def test_register_flag_off_is_noop(monkeypatch):
    monkeypatch.delenv(DAG_QUIESCE_OPERATOR_GATE_ENV, raising=False)
    services: dict = {}
    register_default_dag_quiesce_hook(services)
    assert "dag_quiesce_hook" not in services


def test_register_flag_on_registers_default_hook(monkeypatch):
    monkeypatch.setenv(DAG_QUIESCE_OPERATOR_GATE_ENV, "1")
    services: dict = {}
    register_default_dag_quiesce_hook(services)
    assert services["dag_quiesce_hook"] is default_dag_quiesce_hook


def test_register_never_clobbers_existing_hook(monkeypatch):
    monkeypatch.setenv(DAG_QUIESCE_OPERATOR_GATE_ENV, "1")
    sentinel = object()
    services: dict = {"dag_quiesce_hook": sentinel}
    register_default_dag_quiesce_hook(services)
    assert services["dag_quiesce_hook"] is sentinel
    services = {"quiesce_hook": sentinel}
    register_default_dag_quiesce_hook(services)
    assert "dag_quiesce_hook" not in services


def test_both_runner_build_sites_call_registration():
    """Harden-all-paths: BOTH services-dict builders register the gated hook."""
    import iriai_build_v2.interfaces._bootstrap as bootstrap_mod
    import iriai_build_v2.interfaces.slack.orchestrator as orchestrator_mod

    for mod in (bootstrap_mod, orchestrator_mod):
        source = Path(mod.__file__).read_text(encoding="utf-8")
        assert "register_default_dag_quiesce_hook(services)" in source, mod.__name__


def test_flag_default_off(monkeypatch):
    monkeypatch.delenv(DAG_QUIESCE_OPERATOR_GATE_ENV, raising=False)
    assert quiesce_gate.dag_quiesce_operator_gate_enabled() is False
