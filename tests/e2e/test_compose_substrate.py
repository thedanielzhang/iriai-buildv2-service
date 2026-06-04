"""P4: compose-substrate lifecycle (secret injection, compose.json sidecar,
teardown/GC compose-down) + the tcp_connect readiness probe. No real docker —
the down command is intercepted; the tcp probe uses a real loopback socket.
"""

from __future__ import annotations

import json
import os
import socket
import stat
import time
from pathlib import Path

import pytest

from iriai_build_v2.workflows.develop.e2e import substrate as substrate_module
from iriai_build_v2.workflows.develop.e2e.adapters import probe_tcp_connect
from iriai_build_v2.workflows.develop.e2e.substrate import (
    CloneSubstrate,
    SubstrateError,
)


# --- secret injection (AC-K-10) -----------------------------------------------


def test_inject_secret_file_copies_content_and_locks_perms(tmp_path):
    src = tmp_path / "store" / ".env.local"
    src.parent.mkdir(parents=True)
    src.write_text("AUTH0_SECRET=topsecret\nTEST_USER=qa@x\n")
    checkout = tmp_path / "co"
    checkout.mkdir()
    sub = CloneSubstrate(run_id="s", base_dir=tmp_path / "sc", nice=False)

    dst = sub.inject_secret_file(checkout, src, "common/docker/.env.local")

    assert dst == checkout / "common/docker/.env.local"
    assert dst.read_text() == "AUTH0_SECRET=topsecret\nTEST_USER=qa@x\n"
    # chmod 0600 — owner-only.
    assert stat.S_IMODE(dst.stat().st_mode) == 0o600


def test_inject_secret_file_missing_source_raises(tmp_path):
    checkout = tmp_path / "co"
    checkout.mkdir()
    sub = CloneSubstrate(run_id="s", base_dir=tmp_path / "sc", nice=False)
    with pytest.raises(SubstrateError, match="secret source not found"):
        sub.inject_secret_file(checkout, tmp_path / "nope.env", "dst/.env")


# --- compose.json sidecar + down command --------------------------------------


def test_register_compose_project_persists_sidecar(tmp_path):
    # persist=True: no atexit teardown (this test never brings a stack up).
    sub = CloneSubstrate(
        run_id="cp", base_dir=tmp_path / "sc", nice=False, persist=True
    )
    sub.register_compose_project(
        "e2e_cp",
        workdir="/tmp/co",
        compose_files=["docker-compose.yaml", "override.yaml"],
        env_file="/store/.env.local",
    )
    assert sub._composefile.exists()
    entries = json.loads(sub._composefile.read_text())
    assert entries == [
        {
            "project": "e2e_cp",
            "workdir": "/tmp/co",
            "compose_files": ["docker-compose.yaml", "override.yaml"],
            "env_file": "/store/.env.local",
        }
    ]
    # idempotent — re-registering the same entry does not duplicate.
    sub.register_compose_project(
        "e2e_cp",
        workdir="/tmp/co",
        compose_files=["docker-compose.yaml", "override.yaml"],
        env_file="/store/.env.local",
    )
    assert len(json.loads(sub._composefile.read_text())) == 1


def test_compose_down_argv_builds_command():
    argv = CloneSubstrate._compose_down_argv(
        {
            "project": "e2e_x",
            "workdir": "/tmp/co",
            "compose_files": ["a.yaml", "b.yaml"],
            "env_file": "/s/.env",
        }
    )
    assert argv == [
        "docker", "compose", "-p", "e2e_x",
        "-f", "a.yaml", "-f", "b.yaml",
        "--env-file", "/s/.env",
        "down", "-v", "--remove-orphans",
    ]


def test_compose_down_argv_omits_empty_env_file():
    argv = CloneSubstrate._compose_down_argv(
        {"project": "p", "compose_files": ["c.yaml"], "env_file": ""}
    )
    assert "--env-file" not in argv
    assert argv[-3:] == ["down", "-v", "--remove-orphans"]


# --- teardown / GC run compose down -------------------------------------------


@pytest.mark.asyncio
async def test_teardown_runs_compose_down_then_rmtree(tmp_path):
    sub = CloneSubstrate(run_id="td", base_dir=tmp_path / "sc", nice=False)
    sub.run_dir.mkdir(parents=True)
    sub.register_compose_project(
        "e2e_td", workdir=str(sub.run_dir), compose_files=["c.yaml"], env_file=""
    )

    calls: list[tuple] = []

    async def fake_run(*args, cwd=None, timeout=None):
        calls.append((args, str(cwd) if cwd else None))
        return 0, "", ""

    sub._run = fake_run  # intercept the async docker invocation
    await sub.teardown()

    assert len(calls) == 1
    argv, _ = calls[0]
    assert list(argv)[:4] == ["docker", "compose", "-p", "e2e_td"]
    assert "down" in argv and "-v" in argv
    assert not sub.run_dir.exists()  # rmtree still happens after down


def test_sync_teardown_runs_compose_down(tmp_path, monkeypatch):
    sub = CloneSubstrate(run_id="st", base_dir=tmp_path / "sc", nice=False)
    sub.run_dir.mkdir(parents=True)
    sub.register_compose_project(
        "e2e_st", workdir=str(sub.run_dir), compose_files=["c.yaml"], env_file=""
    )

    downed: list[dict] = []
    monkeypatch.setattr(
        substrate_module, "_compose_down_sync", lambda entry: downed.append(entry)
    )
    sub._sync_teardown()

    assert [e["project"] for e in downed] == ["e2e_st"]
    assert not sub.run_dir.exists()


def test_gc_stale_downs_compose_from_sidecar_before_rmtree(tmp_path, monkeypatch):
    base = tmp_path / "scratch"
    stale = base / "track" / "old"
    stale.mkdir(parents=True)
    (stale / "pids.json").write_text("[]")
    (stale / "compose.json").write_text(
        json.dumps([{"project": "e2e_old", "workdir": str(stale),
                     "compose_files": ["c.yaml"], "env_file": ""}])
    )
    old = time.time() - 10 * 3600
    os.utime(stale, (old, old))

    downed: list[dict] = []
    monkeypatch.setattr(
        substrate_module, "_compose_down_sync", lambda entry: downed.append(entry)
    )
    removed = CloneSubstrate.gc_stale(role="track", base_dir=base)

    assert [e["project"] for e in downed] == ["e2e_old"]
    assert any("old" in r for r in removed)
    assert not stale.exists()


# --- tcp_connect probe --------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_tcp_connect_succeeds_against_listener():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        ok, detail = await probe_tcp_connect(
            f"127.0.0.1:{port}", timeout_s=2.0, interval_s=0.2
        )
        assert ok is True
        assert "ok" in detail
    finally:
        srv.close()


@pytest.mark.asyncio
async def test_probe_tcp_connect_times_out_on_closed_port():
    # Bind+close to get an almost-certainly-unused port, then probe it.
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    ok, detail = await probe_tcp_connect(
        f"127.0.0.1:{port}", timeout_s=1.0, interval_s=0.2
    )
    assert ok is False
    assert "timeout" in detail


@pytest.mark.asyncio
async def test_probe_tcp_connect_rejects_malformed_target():
    ok, detail = await probe_tcp_connect("not-a-host-port", timeout_s=1.0)
    assert ok is False
    assert "invalid tcp target" in detail
