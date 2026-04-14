"""iriai-build-v2 monitoring dashboard.

Usage:
    python dashboard.py [--port 8080] [--bridge-channel C_CHANNEL ...]
"""
from __future__ import annotations

import asyncio
import asyncio.subprocess
import collections
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any

import asyncpg
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
import uvicorn

from iriai_build_v2.workflows.bugfix_v2.proof import feature_root_from_workspace

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://danielzhang@localhost:5431/iriai_build_v2",
)

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


# ── Bridge subprocess manager ──────────────────────────────────────────────


class BridgeManager:
    """Manages the Slack bridge as a child process with output capture."""

    def __init__(self, config: dict[str, str | bool | None]) -> None:
        self.config = config
        self.process: asyncio.subprocess.Process | None = None
        self.lines: collections.deque[str] = collections.deque(maxlen=5000)
        self.line_count: int = 0
        self.subscribers: list[asyncio.Queue[str]] = []
        self._reader_task: asyncio.Task | None = None

    def _build_cmd(self) -> list[str]:
        cmd = [
            sys.executable, "-m", "iriai_build_v2.interfaces.cli.app",
            "slack", "--channel", str(self.config["channel"]),
        ]
        if self.config.get("workspace"):
            cmd += ["--workspace", str(self.config["workspace"])]
        if self.config.get("mode"):
            cmd += ["--mode", str(self.config["mode"])]
        if self.config.get("agent_runtime"):
            cmd += ["--agent-runtime", str(self.config["agent_runtime"])]
        if self.config.get("claude_only"):
            cmd.append("--claude-only")
        if self.config.get("budget"):
            cmd.append("--budget")
        return cmd

    def _build_env(self) -> dict[str, str]:
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}
        dashboard_base_url = self.config.get("dashboard_base_url")
        if dashboard_base_url:
            env["IRIAI_DASHBOARD_BASE_URL"] = str(dashboard_base_url)
        return env

    async def start(self) -> None:
        if self.process and self.process.returncode is None:
            return
        self.process = await asyncio.create_subprocess_exec(
            *self._build_cmd(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=self._build_env(),
        )
        self._reader_task = asyncio.create_task(self._read_output())

    async def stop(self) -> None:
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=10)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None

    async def restart(self) -> None:
        self._append_line(f"{time.strftime('%H:%M:%S')} --- RESTARTING BRIDGE ---")
        await self.stop()
        await self.start()

    async def _read_output(self) -> None:
        assert self.process and self.process.stdout
        async for raw in self.process.stdout:
            line = _ANSI_RE.sub("", raw.decode("utf-8", errors="replace").rstrip("\n"))
            # Also print to dashboard's own stderr so the operator can see it
            print(line, file=sys.stderr, flush=True)
            self._append_line(line)
        rc = self.process.returncode
        self._append_line(f"{time.strftime('%H:%M:%S')} --- BRIDGE EXITED (code={rc}) ---")

    def _append_line(self, line: str) -> None:
        self.lines.append(line)
        self.line_count += 1
        for q in self.subscribers:
            q.put_nowait(line)

    def status(self) -> dict:
        running = self.process is not None and self.process.returncode is None
        return {
            "running": running,
            "pid": self.process.pid if self.process else None,
            "exit_code": self.process.returncode if self.process else None,
            "line_count": self.line_count,
            "buffer_size": len(self.lines),
            "dashboard_base_url": self.config.get("dashboard_base_url"),
        }


# ── App setup ──────────────────────────────────────────────────────────────

bridge_config: dict[str, str | bool | None] = {}
bridge: BridgeManager | None = None
dashboard_config: dict[str, int | bool | str | None] = {"port": 8080}
dashboard_tunnel: Any | None = None

app = FastAPI(title="iriai-build-v2 Dashboard")
pool: asyncpg.Pool | None = None


async def _maybe_start_dashboard_tunnel() -> str | None:
    global dashboard_tunnel
    configured = os.environ.get("IRIAI_DASHBOARD_BASE_URL", "").rstrip("/")
    port = int(dashboard_config.get("port") or 8080)

    if not bridge_config.get("channel"):
        return configured or None

    from iriai_build_v2.services.tunnel import CloudflaredUrlTunnel

    try:
        dashboard_tunnel = CloudflaredUrlTunnel()
        public_url = await dashboard_tunnel.start(f"http://localhost:{port}")
    except Exception:
        dashboard_tunnel = None
        public_url = None

    if public_url:
        print(f"Dashboard tunnel started: {public_url}", file=sys.stderr, flush=True)
        return public_url.rstrip("/")

    return configured or None


@app.on_event("startup")
async def _startup():
    global pool, bridge
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=5)
    dashboard_base_url = await _maybe_start_dashboard_tunnel()
    if dashboard_base_url:
        bridge_config["dashboard_base_url"] = dashboard_base_url
    if bridge_config.get("channel"):
        bridge = BridgeManager(bridge_config)
        await bridge.start()


@app.on_event("shutdown")
async def _shutdown():
    if bridge:
        await bridge.stop()
    if dashboard_tunnel:
        await dashboard_tunnel.stop()
    if pool:
        await pool.close()


# ── Response cache ─────────────────────────────────────────────────────────

_CACHE_TTL = 3.0  # seconds
_response_cache: dict[str, tuple[float, str, dict]] = {}  # feature_id → (ts, etag, data)
_UI_DIST = Path(__file__).resolve().parent / "dashboard-ui" / "dist"
_BUGFLOW_HEALTHS = {
    "idle", "running", "fix-loop", "awaiting-user", "blocked", "degraded", "complete-ish", "complete",
}


def _evict_stale_cache() -> None:
    now = time.monotonic()
    stale = [k for k, (ts, _, _) in _response_cache.items() if now - ts > _CACHE_TTL]
    for k in stale:
        del _response_cache[k]


# ── API ─────────────────────────────────────────────────────────────────────


@app.get("/api/feature/{feature_id}")
async def get_feature(feature_id: str, request: Request):
    """Return assembled dashboard state for one feature."""
    assert pool
    _evict_stale_cache()

    async with pool.acquire() as conn:
        # 0. Lightweight version check for ETag
        version = await conn.fetchrow(
            "SELECT f.updated_at, "
            "  COALESCE((SELECT MAX(id) FROM artifacts WHERE feature_id = $1), 0) AS max_art, "
            "  COALESCE((SELECT MAX(id) FROM events WHERE feature_id = $1), 0) AS max_evt, "
            "  GREATEST("
            "    (SELECT MAX(created_at) FROM artifacts WHERE feature_id = $1), "
            "    (SELECT MAX(created_at) FROM events WHERE feature_id = $1)"
            "  ) AS last_activity_at "
            "FROM features f WHERE f.id = $1",
            feature_id,
        )
        if not version:
            raise HTTPException(404, f"Feature {feature_id!r} not found")

        etag = f'"{version["updated_at"]}:{version["max_art"]}:{version["max_evt"]}"'
        last_activity_at = (
            version["last_activity_at"].isoformat()
            if version["last_activity_at"]
            else None
        )

        # Check If-None-Match
        if_none_match = request.headers.get("if-none-match")
        if if_none_match == etag:
            return Response(status_code=304, headers={"ETag": etag})

        # Check in-memory cache
        if feature_id in _response_cache:
            _, cached_etag, cached_data = _response_cache[feature_id]
            if cached_etag == etag:
                return Response(
                    content=json.dumps(cached_data),
                    media_type="application/json",
                    headers={"ETag": etag},
                )

        # 1. Feature metadata
        feat = await conn.fetchrow(
            "SELECT id, name, phase, workflow_name, updated_at, metadata "
            "FROM features WHERE id = $1",
            feature_id,
        )
        if not feat:
            raise HTTPException(404, f"Feature {feature_id!r} not found")

        if feat["workflow_name"] == "bugfix-v2":
            rows = await conn.fetch(
                "SELECT DISTINCT ON (key) key, value, created_at "
                "FROM artifacts WHERE feature_id = $1 "
                "AND (key LIKE 'bugflow-%' OR key LIKE 'bug-%' "
                "     OR key LIKE 'obs-verdict:%' OR key LIKE 'contradiction:%') "
                "ORDER BY key, id DESC",
                feature_id,
            )
            timeline_rows = await conn.fetch(
                "SELECT key, value, created_at FROM artifacts "
                "WHERE feature_id = $1 "
                "AND (key LIKE 'bugflow-%' OR key LIKE 'bug-%' "
                "     OR key LIKE 'obs-verdict:%' OR key LIKE 'contradiction:%') "
                "ORDER BY created_at DESC LIMIT 250",
                feature_id,
            )
        else:
            # 2. Latest artifacts (append-only: latest = highest id per key)
            rows = await conn.fetch(
                "SELECT DISTINCT ON (key) key, value, created_at "
                "FROM artifacts WHERE feature_id = $1 "
                "AND (key LIKE 'dag%' OR key LIKE 'bug-%' "
                "     OR key LIKE 'enhancement-%' "
                "     OR key = 'implementation' OR key = 'handover') "
                "ORDER BY key, id DESC",
                feature_id,
            )

            # 3. All verify/bug artifacts with full history (for timeline)
            timeline_rows = await conn.fetch(
                "SELECT key, value, created_at FROM artifacts "
                "WHERE feature_id = $1 "
                "AND (key LIKE 'dag-verify:%' OR key LIKE 'dag-fix:%' OR key LIKE 'dag-verify-rca:%' "
                "     OR key LIKE 'bug-%' OR key LIKE '%-verdict') "
                "ORDER BY created_at DESC LIMIT 100",
                feature_id,
            )

        # 4. Recent events
        events = await conn.fetch(
            "SELECT event_type, source, content, created_at "
            "FROM events WHERE feature_id = $1 "
            "ORDER BY created_at DESC LIMIT 50",
            feature_id,
        )

    if feat["workflow_name"] == "bugfix-v2":
        result = _assemble_bugflow_response(
            feat=feat,
            rows=rows,
            timeline_rows=timeline_rows,
            events=events,
            feature_id=feature_id,
            last_activity_at=last_activity_at,
            request_base_url=str(request.base_url).rstrip("/"),
        )
        _response_cache[feature_id] = (time.monotonic(), etag, result)
        return Response(
            content=json.dumps(result, default=str),
            media_type="application/json",
            headers={"ETag": etag},
        )

    # ── Assemble response ───────────────────────────────────────────
    artifacts: dict[str, tuple[str, str]] = {}  # key → (value, created_at)
    for r in rows:
        artifacts[r["key"]] = (r["value"], r["created_at"].isoformat())

    # Parse DAG
    dag_info = None
    if "dag" in artifacts:
        try:
            dag = json.loads(artifacts["dag"][0])
            tasks_by_id = {t["id"]: t for t in dag.get("tasks", [])}
            exec_order = dag.get("execution_order", [])
            dag_info = {
                "total_tasks": len(dag.get("tasks", [])),
                "total_groups": len(exec_order),
                "execution_order": exec_order,
            }
        except (json.JSONDecodeError, KeyError):
            pass

    # Workstreams
    workstreams_list = []
    if "dag:strategy" in artifacts:
        try:
            ws_data = json.loads(artifacts["dag:strategy"][0])
            for ws in ws_data.get("workstreams", []):
                total = 0
                completed = 0
                for slug in ws.get("subfeature_slugs", []):
                    sf_key = f"dag:{slug}"
                    if sf_key in artifacts:
                        try:
                            sf_dag = json.loads(artifacts[sf_key][0])
                            sf_tasks = sf_dag.get("tasks", [])
                            total += len(sf_tasks)
                            for t in sf_tasks:
                                if f"dag-task:{t['id']}" in artifacts:
                                    completed += 1
                        except (json.JSONDecodeError, KeyError):
                            pass
                workstreams_list.append({
                    "id": ws.get("id", ""),
                    "name": ws.get("name", ""),
                    "subfeature_slugs": ws.get("subfeature_slugs", []),
                    "depends_on": ws.get("depends_on", []),
                    "total_tasks": total,
                    "completed_tasks": completed,
                })
        except (json.JSONDecodeError, KeyError):
            pass

    # Group statuses
    groups = []
    if dag_info:
        active_found = False
        for i, task_ids in enumerate(dag_info["execution_order"]):
            gkey = f"dag-group:{i}"
            if gkey in artifacts:
                status = "complete"
            elif not active_found:
                status = "active"
                active_found = True
            else:
                status = "pending"

            # Task detail for this group
            task_details = []
            completed_count = 0
            for tid in task_ids:
                tkey = f"dag-task:{tid}"
                task_def = tasks_by_id.get(tid, {})
                task_name = task_def.get("name", "")
                task_summary = ""
                task_status = "pending"
                if tkey in artifacts:
                    task_status = "complete"
                    completed_count += 1
                    try:
                        result = json.loads(artifacts[tkey][0])
                        task_summary = result.get("summary", "")
                    except (json.JSONDecodeError, KeyError):
                        pass
                elif status == "active":
                    task_status = "in_progress"
                task_details.append({
                    "id": tid,
                    "name": task_name,
                    "status": task_status,
                    "summary": task_summary,
                    "description": task_def.get("description", ""),
                    "subfeature_id": task_def.get("subfeature_id", ""),
                    "repo_path": task_def.get("repo_path", ""),
                    "file_scope": task_def.get("file_scope", []),
                    "acceptance_criteria": task_def.get("acceptance_criteria", []),
                })

            # Collect verify artifacts for this group
            verify_steps = []
            verify_prefix = f"dag-verify:g{i}:"
            fix_prefix = f"dag-fix:g{i}:"
            rca_prefix = f"dag-verify-rca:g{i}:"
            for r in timeline_rows:
                k = r["key"]
                if k.startswith(verify_prefix):
                    approved = False
                    summary = ""
                    try:
                        v = json.loads(r["value"])
                        approved = v.get("approved", False)
                        summary = v.get("summary", "")
                    except (json.JSONDecodeError, KeyError):
                        summary = r["value"] if r["value"] else ""
                    step_type = "verify"
                    key_suffix = k[len(verify_prefix):]
                    if key_suffix.startswith("retry"):
                        step_type = "re-verify"
                    verify_steps.append({
                        "key": k,
                        "type": step_type,
                        "passed": approved,
                        "summary": summary,
                        "created_at": r["created_at"].isoformat(),
                    })
                elif k.startswith(rca_prefix):
                    summary = ""
                    try:
                        v = json.loads(r["value"])
                        summary = v.get("hypothesis", "")
                    except (json.JSONDecodeError, KeyError):
                        summary = r["value"] if r["value"] else ""
                    verify_steps.append({
                        "key": k,
                        "type": "rca",
                        "passed": None,
                        "summary": summary,
                        "created_at": r["created_at"].isoformat(),
                    })
                elif k.startswith(fix_prefix):
                    summary = ""
                    try:
                        v = json.loads(r["value"])
                        summary = v.get("summary", "")
                    except (json.JSONDecodeError, KeyError):
                        summary = r["value"] if r["value"] else ""
                    verify_steps.append({
                        "key": k,
                        "type": "fix",
                        "passed": None,
                        "summary": summary,
                        "created_at": r["created_at"].isoformat(),
                    })

            # Collect fix artifacts for active/failed groups
            fix_steps = []
            for r in timeline_rows:
                k = r["key"]
                if not k.startswith("bug-"):
                    continue
                entry = _parse_timeline_entry(k, r["value"], r["created_at"])
                if entry:
                    fix_steps.append(entry)

            groups.append({
                "index": i,
                "task_count": len(task_ids),
                "completed_count": completed_count,
                "status": status,
                "tasks": task_details,
                "verify_steps": sorted(verify_steps, key=lambda x: x["created_at"]),
                "fix_steps": sorted(fix_steps, key=lambda x: x["created_at"]) if status in ("active", "complete") else [],
            })

    # Enhancement group — appears after the last DAG group
    if dag_info and "enhancement-backlog" in artifacts:
        try:
            backlog = json.loads(artifacts["enhancement-backlog"][0])
            enh_items = backlog.get("items", [])
        except (json.JSONDecodeError, KeyError):
            enh_items = []

        if enh_items:
            enh_idx = len(dag_info["execution_order"])
            enh_gkey = f"dag-group:{enh_idx}"

            # Determine status
            all_dag_done = all(g["status"] == "complete" for g in groups)
            if enh_gkey in artifacts:
                enh_status = "complete"
            elif all_dag_done:
                enh_status = "active"
            else:
                enh_status = "pending"

            # Check decomposition for per-repo tasks
            enh_task_details = []
            enh_completed = 0
            if "enhancement-decomposition" in artifacts:
                try:
                    decomp = json.loads(artifacts["enhancement-decomposition"][0])
                    for rt in decomp.get("tasks", []):
                        tid = f"enhancement-{rt['repo_path']}"
                        tkey = f"dag-task:{tid}"
                        t_status = "pending"
                        t_summary = ""
                        if tkey in artifacts:
                            t_status = "complete"
                            enh_completed += 1
                            try:
                                result = json.loads(artifacts[tkey][0])
                                t_summary = result.get("summary", "")
                            except (json.JSONDecodeError, KeyError):
                                pass
                        elif enh_status == "active":
                            t_status = "in_progress"
                        enh_task_details.append({
                            "id": tid,
                            "name": f"Fix enhancements in {rt['repo_path']} ({len(rt.get('item_indices', []))} items)",
                            "status": t_status,
                            "summary": t_summary,
                            "description": rt.get("summary", ""),
                            "subfeature_id": "",
                            "repo_path": rt["repo_path"],
                            "file_scope": [],
                            "acceptance_criteria": [],
                        })
                except (json.JSONDecodeError, KeyError):
                    pass

            # Fallback: single enhancement-all task
            if not enh_task_details:
                tid = "enhancement-all"
                tkey = f"dag-task:{tid}"
                t_status = "pending"
                t_summary = ""
                if tkey in artifacts:
                    t_status = "complete"
                    enh_completed = 1
                    try:
                        result = json.loads(artifacts[tkey][0])
                        t_summary = result.get("summary", "")
                    except (json.JSONDecodeError, KeyError):
                        pass
                elif enh_status == "active":
                    t_status = "in_progress"
                enh_task_details = [{
                    "id": tid,
                    "name": f"Fix enhancement backlog ({len(enh_items)} items)",
                    "status": t_status,
                    "summary": t_summary,
                    "description": "",
                    "subfeature_id": "",
                    "repo_path": "",
                    "file_scope": [],
                    "acceptance_criteria": [],
                }]

            # Collect verify/fix artifacts for enhancement group
            enh_verify_steps = []
            verify_prefix = f"dag-verify:g{enh_idx}:"
            fix_prefix = f"dag-fix:g{enh_idx}:"
            rca_prefix = f"dag-verify-rca:g{enh_idx}:"
            for r in timeline_rows:
                k = r["key"]
                if k.startswith(verify_prefix):
                    approved = False
                    summary = ""
                    try:
                        v = json.loads(r["value"])
                        approved = v.get("approved", False)
                        summary = v.get("summary", "")
                    except (json.JSONDecodeError, KeyError):
                        summary = r["value"] if r["value"] else ""
                    step_type = "re-verify" if "retry" in k else "verify"
                    enh_verify_steps.append({
                        "key": k, "type": step_type, "passed": approved,
                        "summary": summary, "created_at": r["created_at"].isoformat(),
                    })
                elif k.startswith(rca_prefix):
                    summary = ""
                    try:
                        v = json.loads(r["value"])
                        summary = v.get("hypothesis", "")
                    except (json.JSONDecodeError, KeyError):
                        summary = r["value"] if r["value"] else ""
                    enh_verify_steps.append({
                        "key": k, "type": "rca", "passed": None,
                        "summary": summary, "created_at": r["created_at"].isoformat(),
                    })
                elif k.startswith(fix_prefix):
                    summary = ""
                    try:
                        v = json.loads(r["value"])
                        summary = v.get("summary", "")
                    except (json.JSONDecodeError, KeyError):
                        summary = r["value"] if r["value"] else ""
                    enh_verify_steps.append({
                        "key": k, "type": "fix", "passed": None,
                        "summary": summary, "created_at": r["created_at"].isoformat(),
                    })

            groups.append({
                "index": enh_idx,
                "task_count": len(enh_task_details),
                "completed_count": enh_completed,
                "status": enh_status,
                "tasks": enh_task_details,
                "verify_steps": sorted(enh_verify_steps, key=lambda x: x["created_at"]),
                "fix_steps": [],
                "is_enhancement": True,
            })

            # Update totals
            dag_info["total_groups"] += 1
            dag_info["total_tasks"] += len(enh_task_details)

    # Gates
    gate_names = ["code-review", "security", "test-authoring", "qa", "integration", "verifier"]
    gates = {}
    for g in gate_names:
        gates[g] = f"dag-gate:{g}" in artifacts

    # Active gate + its sub-activity timeline
    gate_source_map = {
        "code-review": "code_reviewer",
        "security": "security_auditor",
        "qa": "qa_engineer",
        "integration": "integration_tester",
        "verifier": "verifier",
    }
    # Map gate names to their verdict artifact keys
    gate_verdict_key = {
        "code-review": "review-verdict",
        "security": "security-verdict",
        "qa": "qa-verdict",
        "integration": "integration-verdict",
        "verifier": "verifier-verdict",
    }

    active_gate = None
    active_gate_steps: list[dict] = []
    all_groups_done = dag_info and all(g["status"] == "complete" for g in groups)
    if all_groups_done:
        for g in gate_names:
            if not gates[g]:
                active_gate = g
                source = gate_source_map.get(g)
                verdict_key = gate_verdict_key.get(g)
                if source:
                    for r in timeline_rows:
                        k = r["key"]
                        if f":{source}:" in k or k.endswith(f":{source}") or k == verdict_key:
                            entry = _parse_timeline_entry(
                                k, r["value"], r["created_at"],
                            )
                            if entry:
                                active_gate_steps.append(entry)
                    active_gate_steps.sort(key=lambda x: x["created_at"])
                break

    # Timeline
    timeline = []
    for r in timeline_rows:
        entry = _parse_timeline_entry(r["key"], r["value"], r["created_at"])
        if entry:
            timeline.append(entry)

    # Events
    event_list = []
    for e in events:
        event_list.append({
            "event_type": e["event_type"],
            "source": e["source"],
            "content": e["content"] or "",
            "created_at": e["created_at"].isoformat(),
        })

    # Active agent
    active_agent = None
    for e in events:
        if e["event_type"] == "agent_start":
            active_agent = e["source"]
            break  # events ordered DESC by created_at

    result = {
        "id": feat["id"],
        "name": feat["name"],
        "phase": feat["phase"],
        "workflow_name": feat["workflow_name"],
        "updated_at": feat["updated_at"].isoformat(),
        "last_activity_at": last_activity_at,
        "dag": dag_info,
        "groups": groups,
        "gates": gates,
        "active_gate": active_gate,
        "active_gate_steps": active_gate_steps,
        "timeline": timeline,
        "workstreams": workstreams_list,
        "events": event_list,
        "active_agent": active_agent,
    }

    # Cache and return with ETag
    _response_cache[feature_id] = (time.monotonic(), etag, result)
    return Response(
        content=json.dumps(result, default=str),
        media_type="application/json",
        headers={"ETag": etag},
    )


def _ensure_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _safe_json(value: Any) -> Any | None:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _safe_dict(value: Any) -> dict[str, Any]:
    parsed = _safe_json(value)
    return parsed if isinstance(parsed, dict) else {}


def _safe_list(value: Any) -> list[Any]:
    parsed = _safe_json(value)
    if isinstance(parsed, list):
        return parsed
    if isinstance(value, list):
        return value
    return []


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, default=str)


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value if v not in (None, "")]
    if isinstance(value, tuple):
        return [str(v) for v in value if v not in (None, "")]
    if isinstance(value, str):
        parsed = _safe_json(value)
        if isinstance(parsed, list):
            return [str(v) for v in parsed if v not in (None, "")]
        return [value] if value else []
    return []


def _parse_fix_attempts_blob(raw: str | None) -> list[dict[str, Any]]:
    if not raw:
        return []
    attempts: list[dict[str, Any]] = []
    depth = 0
    start = None
    for i, ch in enumerate(raw):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    obj = json.loads(raw[start : i + 1])
                    if isinstance(obj, dict) and obj.get("bug_id"):
                        attempts.append(obj)
                except Exception:
                    pass
                start = None
    return attempts


def _normalize_bugflow_health(value: Any) -> str | None:
    text = _text(value).strip().lower()
    return text if text in _BUGFLOW_HEALTHS else None


def _status_text_from_queue(queue: dict[str, Any]) -> str:
    for key in ("status_text", "active_step", "summary"):
        value = _text(queue.get(key)).strip()
        if value:
            return value
    return ""


def _default_bugflow_counts() -> dict[str, int]:
    return {
        "intake_pending": 0,
        "awaiting_confirmation": 0,
        "queued": 0,
        "active_fix": 0,
        "pending_retriage": 0,
        "blocked": 0,
        "resolved": 0,
    }


def _report_lane(status: str) -> str:
    value = (status or "").strip().lower()
    if value.startswith("resolved") or value in {"complete", "closed"}:
        return "resolved"
    if value in {"blocked", "cancelled"}:
        return "blocked"
    if value in {"pending_retriage"}:
        return "pending_retriage"
    if value in {"active_fix", "active", "fixing", "triage", "rca", "reverify", "regression", "pushing"}:
        return "active_fix"
    if value in {"awaiting_confirmation", "clarification_pending", "waiting_for_confirmation"}:
        return "awaiting_confirmation"
    if value in {"intake_pending", "classification_pending", "validation_pending"}:
        return "intake_pending"
    return "queued"


def _report_thread_status(report: dict[str, Any]) -> str:
    explicit = _text(report.get("thread_status")).strip()
    if explicit:
        return explicit
    lane = _report_lane(_text(report.get("status")))
    if lane == "intake_pending":
        return "interview open"
    if lane == "awaiting_confirmation":
        return "waiting on user"
    return "ready"


def _key_refs_cluster(key: str, cluster_id: str | None) -> bool:
    if not cluster_id:
        return False
    return (
        f":{cluster_id}:" in key
        or key.endswith(f":{cluster_id}")
        or key.endswith(cluster_id)
    )


def _artifact_lookup(rows: list[asyncpg.Record]) -> dict[str, tuple[str, str]]:
    return {r["key"]: (r["value"], _ensure_iso(r["created_at"]) or "") for r in rows}


def _find_latest_entry(
    timeline_rows: list[asyncpg.Record],
    *,
    key: str | None = None,
    prefix: str | None = None,
    cluster_id: str | None = None,
) -> dict[str, Any] | None:
    for row in timeline_rows:
        row_key = row["key"]
        if key and row_key != key:
            continue
        if prefix and not row_key.startswith(prefix):
            continue
        if cluster_id and not _key_refs_cluster(row_key, cluster_id):
            continue
        return {
            "key": row_key,
            "value": row["value"],
            "created_at": _ensure_iso(row["created_at"]),
        }
    return None


def _derive_bugflow_status_text(
    queue: dict[str, Any],
    active_lanes: list[dict[str, Any]],
    promoting_lane: dict[str, Any] | None,
    active_report: dict[str, Any] | None,
    counts: dict[str, int],
) -> str:
    explicit = _status_text_from_queue(queue)
    if explicit:
        return explicit
    recovering_lane_ids = _string_list(queue.get("recovering_lane_ids"))
    stalled_lane_ids = _string_list(queue.get("stalled_lane_ids"))
    strategy_pending_cluster_ids = _string_list(queue.get("strategy_pending_cluster_ids"))
    if stalled_lane_ids:
        return f"Recovering stalled lanes: {', '.join(stalled_lane_ids[:3])}"
    if recovering_lane_ids:
        return f"Recovering lanes: {', '.join(recovering_lane_ids[:3])}"
    if strategy_pending_cluster_ids:
        return f"Strategy pending for clusters: {', '.join(strategy_pending_cluster_ids[:3])}"
    if promoting_lane:
        lid = promoting_lane.get("lane_id") or "lane"
        return f"Promoting {lid}"
    if active_lanes:
        labels = ", ".join(
            _text(lane.get("lane_id") or "lane")
            for lane in active_lanes[:3]
        )
        return f"Active lanes: {labels}"
    if active_report:
        rid = active_report.get("report_id") or "report"
        step = _text(active_report.get("current_step")).strip()
        if step:
            return f"{step} {rid}"
        return f"Reviewing {rid}"
    open_count = sum(
        counts.get(key, 0)
        for key in ("intake_pending", "awaiting_confirmation", "queued", "active_fix", "pending_retriage", "blocked")
    )
    if open_count == 0 and counts.get("resolved", 0) > 0:
        return "Queue clear — waiting for new bug reports"
    if open_count == 0:
        return "Idle — waiting for new bug reports"
    return f"{open_count} reports in queue"


def _derive_bugflow_health(
    queue: dict[str, Any],
    reports: list[dict[str, Any]],
    active_lanes: list[dict[str, Any]],
    promoting_lane: dict[str, Any] | None,
    active_report: dict[str, Any] | None,
    counts: dict[str, int],
) -> str:
    explicit = _normalize_bugflow_health(queue.get("health"))
    if explicit:
        return explicit

    if _string_list(queue.get("stalled_lane_ids")) or _string_list(queue.get("recovering_lane_ids")) or _string_list(queue.get("strategy_pending_cluster_ids")):
        return "degraded"
    if counts.get("blocked", 0) > 0 or _text(queue.get("active_step")).strip().lower().startswith("blocked"):
        return "blocked"

    waiting_reports = [
        r for r in reports
        if _report_lane(_text(r.get("status"))) == "awaiting_confirmation"
        or "waiting" in _report_thread_status(r)
    ]
    if waiting_reports:
        return "awaiting-user"

    active_step = _text(queue.get("active_step")).lower()
    if "awaiting" in active_step or "waiting" in active_step:
        return "awaiting-user"
    if promoting_lane:
        return "running"
    if active_lanes:
        failed_reverify = any(lane.get("latest_verify_passed") is False for lane in active_lanes)
        failed_regression = any(lane.get("latest_regression_passed") is False for lane in active_lanes)
        if failed_reverify or failed_regression:
            return "fix-loop"
        return "running"
    if active_report:
        return "running"

    open_count = sum(
        counts.get(key, 0)
        for key in ("intake_pending", "awaiting_confirmation", "queued", "active_fix", "pending_retriage", "blocked")
    )
    if open_count == 0 and counts.get("resolved", 0) > 0:
        return "complete-ish"
    if open_count == 0:
        return "idle"
    return "running"


def _assemble_bugflow_response(
    *,
    feat: asyncpg.Record,
    rows: list[asyncpg.Record],
    timeline_rows: list[asyncpg.Record],
    events: list[asyncpg.Record],
    feature_id: str,
    last_activity_at: str | None,
    request_base_url: str,
) -> dict[str, Any]:
    metadata = feat["metadata"] if isinstance(feat["metadata"], dict) else _safe_dict(feat["metadata"])
    artifacts = _artifact_lookup(rows)

    queue_raw = artifacts.get("bugflow-queue", ("{}", ""))[0]
    queue = _safe_dict(queue_raw)
    if not queue and _text(queue_raw).strip():
        queue = {"active_step": _text(queue_raw).strip()}
    queue.setdefault("source_feature_id", metadata.get("source_feature_id"))
    queue.setdefault(
        "dashboard_url",
        metadata.get("dashboard_url") or f"{request_base_url}/feature/{feature_id}",
    )
    queue.setdefault("dashboard_message_ts", metadata.get("dashboard_message_ts"))
    queue.setdefault("pending_retriage_ids", _string_list(queue.get("pending_retriage_ids")))
    queue.setdefault("blocked_ids", _string_list(queue.get("blocked_ids")))
    queue.setdefault("recovering_lane_ids", _string_list(queue.get("recovering_lane_ids")))
    queue.setdefault("stalled_lane_ids", _string_list(queue.get("stalled_lane_ids")))
    queue.setdefault("strategy_pending_cluster_ids", _string_list(queue.get("strategy_pending_cluster_ids")))

    counts = _default_bugflow_counts()
    raw_counts = queue.get("counts")
    has_explicit_counts = isinstance(raw_counts, dict)
    if isinstance(raw_counts, dict):
        for key, value in raw_counts.items():
            try:
                counts[str(key)] = int(value)
            except Exception:
                continue

    reports: list[dict[str, Any]] = []
    clusters: list[dict[str, Any]] = []
    lanes: list[dict[str, Any]] = []
    decisions_by_id: dict[str, dict[str, Any]] = {}
    proofs_by_key: dict[str, dict[str, Any]] = {}
    failure_bundles_by_key: dict[str, dict[str, Any]] = {}
    strategy_decisions_by_key: dict[str, dict[str, Any]] = {}

    for row in rows:
        key = row["key"]
        created_at = _ensure_iso(row["created_at"])
        if key.startswith("bugflow-report:"):
            report_id = key.split(":", 1)[1]
            payload = _safe_dict(row["value"])
            report = dict(payload)
            report.setdefault("report_id", report_id)
            report.setdefault("title", report.get("name") or report_id)
            report.setdefault("status", report.get("state") or "queued")
            report.setdefault("summary", _text(report.get("summary") or report.get("description")))
            report.setdefault("validation_summary", _text(report.get("validation_summary")))
            report.setdefault("current_step", _text(report.get("current_step")))
            report.setdefault("cluster_id", report.get("cluster_id"))
            report.setdefault("category", report.get("category") or "bug")
            report.setdefault("severity", report.get("severity") or "unknown")
            report.setdefault("decision_id", report.get("decision_id"))
            report.setdefault("updated_at", _ensure_iso(report.get("updated_at")) or created_at)
            report.setdefault("created_at", created_at)
            report.setdefault("strategy_mode", "")
            report.setdefault("strategy_reason", "")
            report.setdefault("strategy_round", 0)
            report.setdefault("stable_failure_family", "")
            report.setdefault("strategy_decision_key", "")
            report.setdefault("latest_failure_bundle_key", "")
            report.setdefault("latest_strategy_notice_key", "")
            report.setdefault("strategy_required_evidence_modes", _string_list(report.get("strategy_required_evidence_modes")))
            report.setdefault("terminal_reason_kind", "")
            report.setdefault("terminal_reason_summary", "")
            report["thread_status"] = _report_thread_status(report)
            reports.append(report)
        elif key.startswith("bugflow-cluster:"):
            cluster_id = key.split(":", 1)[1]
            payload = _safe_dict(row["value"])
            cluster = dict(payload)
            cluster.setdefault("cluster_id", cluster_id)
            cluster.setdefault("report_ids", _string_list(cluster.get("report_ids")))
            cluster.setdefault("status", cluster.get("state") or "queued")
            cluster.setdefault("likely_root_cause", _text(cluster.get("likely_root_cause")))
            cluster.setdefault("affected_files", _string_list(cluster.get("affected_files")))
            cluster.setdefault("repo_paths", _string_list(cluster.get("repo_paths")))
            cluster.setdefault("schedule_round", cluster.get("schedule_round"))
            cluster.setdefault("schedule_total_rounds", cluster.get("schedule_total_rounds"))
            cluster.setdefault("attempt_number", cluster.get("attempt_number"))
            cluster.setdefault("updated_at", _ensure_iso(cluster.get("updated_at")) or created_at)
            cluster.setdefault("created_at", created_at)
            cluster.setdefault("strategy_mode", "")
            cluster.setdefault("strategy_reason", "")
            cluster.setdefault("strategy_round", 0)
            cluster.setdefault("stable_failure_family", "")
            cluster.setdefault("strategy_decision_key", "")
            cluster.setdefault("stable_bundle_key", "")
            cluster.setdefault("similar_cluster_ids", _string_list(cluster.get("similar_cluster_ids")))
            cluster.setdefault("strategy_status", "")
            cluster.setdefault("strategy_started_at", _ensure_iso(cluster.get("strategy_started_at")))
            cluster.setdefault("strategy_decided_at", _ensure_iso(cluster.get("strategy_decided_at")))
            cluster.setdefault("strategy_applied_at", _ensure_iso(cluster.get("strategy_applied_at")))
            clusters.append(cluster)
        elif key.startswith("bugflow-lane:"):
            lane_id = key.split(":", 1)[1]
            payload = _safe_dict(row["value"])
            lane = dict(payload)
            lane.setdefault("lane_id", lane_id)
            lane.setdefault("report_ids", _string_list(lane.get("report_ids")))
            lane.setdefault("status", lane.get("state") or "planned")
            lane.setdefault("category", _text(lane.get("category") or "bug"))
            lane.setdefault("lock_scope", _string_list(lane.get("lock_scope")))
            lane.setdefault("repo_paths", _string_list(lane.get("repo_paths")))
            lane.setdefault("latest_rca_keys", _string_list(lane.get("latest_rca_keys")))
            lane.setdefault("latest_verify_keys", _string_list(lane.get("latest_verify_keys")))
            lane.setdefault("latest_regression_keys", _string_list(lane.get("latest_regression_keys")))
            lane.setdefault("modified_files", _string_list(lane.get("modified_files")))
            lane.setdefault("updated_at", _ensure_iso(lane.get("updated_at")) or created_at)
            lane.setdefault("created_at", created_at)
            lane.setdefault("execution_state", "")
            lane.setdefault("execution_nonce", "")
            lane.setdefault("execution_kind", "")
            lane.setdefault("execution_owner", "")
            lane.setdefault("execution_started_at", _ensure_iso(lane.get("execution_started_at")))
            lane.setdefault("last_progress_at", _ensure_iso(lane.get("last_progress_at")))
            lane.setdefault("execution_failure_kind", "")
            lane.setdefault("execution_failure_reason", "")
            lanes.append(lane)
        elif key == "bugflow-decisions":
            payload = _safe_json(row["value"])
            raw_decisions = payload if isinstance(payload, list) else payload.get("decisions", []) if isinstance(payload, dict) else []
            for idx, decision in enumerate(raw_decisions):
                if not isinstance(decision, dict):
                    continue
                decision_id = _text(decision.get("decision_id") or decision.get("id") or f"decision-{idx + 1}")
                decisions_by_id[decision_id] = {
                    "decision_id": decision_id,
                    "report_ids": _string_list(decision.get("report_ids") or decision.get("reports")),
                    "title": _text(decision.get("title") or decision.get("summary") or decision_id),
                    "old_expectation": _text(decision.get("old_expectation") or decision.get("prior_expectation")),
                    "new_decision": _text(decision.get("new_decision") or decision.get("decision") or decision.get("resolution")),
                    "approved": bool(decision.get("approved", True)),
                    "created_at": _ensure_iso(decision.get("created_at")) or created_at,
                    "summary": _text(decision.get("summary") or decision.get("decision") or decision.get("resolution")),
                    "source_key": key,
                }
        elif key.startswith("bugflow-proof:"):
            parts = key.split(":")
            if len(parts) >= 3:
                report_id = parts[1]
                stage = parts[2]
                payload = _safe_dict(row["value"])
                proofs_by_key[key] = {
                    "key": key,
                    "report_id": report_id,
                    "stage": stage,
                    "bundle_url": _text(payload.get("bundle_url")),
                    "primary_artifact_url": _text(payload.get("primary_artifact_url")),
                    "created_at": _ensure_iso(payload.get("created_at")) or created_at,
                    "bundle": payload.get("bundle") if isinstance(payload.get("bundle"), dict) else {},
                }
        elif key.startswith("bugflow-failure-bundle:"):
            payload = _safe_dict(row["value"])
            payload.setdefault("key", key)
            payload.setdefault("strategy_round", payload.get("strategy_round"))
            failure_bundles_by_key[key] = payload
        elif key.startswith("bugflow-strategy:"):
            payload = _safe_dict(row["value"])
            payload.setdefault("key", key)
            strategy_decisions_by_key[key] = payload

    if not has_explicit_counts:
        for report in reports:
            lane = _report_lane(_text(report.get("status")))
            counts[lane] = counts.get(lane, 0) + 1

    for row in timeline_rows:
        key = row["key"]
        if not key.startswith("contradiction:"):
            continue
        decision_id = key.replace("contradiction:", "decision:")
        if decision_id in decisions_by_id:
            continue
        entry = _parse_timeline_entry(key, row["value"], row["created_at"])
        decisions_by_id[decision_id] = {
            "decision_id": decision_id,
            "report_ids": [],
            "title": key,
            "old_expectation": "",
            "new_decision": entry["summary"] if entry else _text(row["value"]),
            "approved": False,
            "created_at": _ensure_iso(row["created_at"]) or "",
            "summary": entry["summary"] if entry else _text(row["value"]),
            "source_key": key,
        }

    decisions = sorted(
        decisions_by_id.values(),
        key=lambda decision: decision.get("created_at") or "",
        reverse=True,
    )

    fix_attempts = _parse_fix_attempts_blob(artifacts.get("bug-fix-attempts", ("", ""))[0])
    obs_verdicts_by_report: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    report_timeline_by_report: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)

    for row in timeline_rows:
        key = row["key"]
        entry = _parse_timeline_entry(key, row["value"], row["created_at"])
        if key.startswith("bugflow-report:"):
            report_id = key.split(":", 1)[1]
            if entry:
                report_timeline_by_report[report_id].append(entry)
        elif key.startswith("obs-verdict:"):
            parts = key.split(":")
            if len(parts) >= 2 and entry:
                obs_verdicts_by_report[parts[1]].append(entry)

    latest_timeline_entries = _artifact_lookup(timeline_rows)

    def _resolve_entry_summary(exact_key: str | None, prefix: str, cluster_id: str | None) -> tuple[str, bool | None]:
        if exact_key and exact_key in artifacts:
            raw_value, raw_created = artifacts[exact_key]
            parsed = _parse_timeline_entry(exact_key, raw_value, raw_created)
            if parsed:
                return parsed["summary"], parsed["passed"]
        if exact_key and exact_key in latest_timeline_entries:
            raw_value, raw_created = latest_timeline_entries[exact_key]
            parsed = _parse_timeline_entry(exact_key, raw_value, raw_created)
            if parsed:
                return parsed["summary"], parsed["passed"]
        if exact_key:
            return "", None
        match = _find_latest_entry(timeline_rows, prefix=prefix, cluster_id=cluster_id)
        if match is None and prefix in {"bug-dispatch:", "bug-regression:"}:
            match = _find_latest_entry(timeline_rows, prefix=prefix, cluster_id=None)
        if match:
            parsed = _parse_timeline_entry(match["key"], match["value"], match["created_at"])
            if parsed:
                return parsed["summary"], parsed["passed"]
        return "", None

    for cluster in clusters:
        cluster_id = cluster.get("cluster_id")
        rca_summary, _ = _resolve_entry_summary(cluster.get("latest_rca_key"), "bug-rca:", cluster_id)
        dispatch_summary, _ = _resolve_entry_summary(cluster.get("latest_dispatch_key"), "bug-dispatch:", cluster_id)
        reverify_summary, reverify_passed = _resolve_entry_summary(cluster.get("latest_reverify_key"), "bug-reverify:", cluster_id)
        regression_summary, regression_passed = _resolve_entry_summary(cluster.get("latest_regression_key"), "bug-regression:", cluster_id)
        cluster["latest_rca_summary"] = rca_summary
        cluster["latest_reverify_summary"] = reverify_summary
        cluster["latest_regression_summary"] = regression_summary
        cluster["latest_reverify_passed"] = reverify_passed
        cluster["latest_regression_passed"] = regression_passed

        dispatch_payload = _safe_json(dispatch_summary) if dispatch_summary.startswith("{") else _safe_json(
            artifacts.get(cluster.get("latest_dispatch_key") or "", ("", ""))[0]
        )
        if isinstance(dispatch_payload, dict):
            cluster["round_plan"] = [
                f"Round {int(item.get('round', 0)) + 1}: {', '.join(_string_list(item.get('group_ids')))}"
                for item in _safe_list(dispatch_payload.get("schedule"))
            ]
        else:
            cluster["round_plan"] = []

        matching_attempts = [
            attempt
            for attempt in fix_attempts
            if attempt.get("group_id") == cluster.get("group_id")
            or attempt.get("group_id") == cluster_id
        ]
        cluster["latest_fix_summary"] = _text(matching_attempts[-1].get("fix_applied")) if matching_attempts else ""

    lane_by_id = {lane["lane_id"]: lane for lane in lanes if lane.get("lane_id")}
    cluster_by_id = {cluster["cluster_id"]: cluster for cluster in clusters if cluster.get("cluster_id")}

    for lane in lanes:
        lane_id = lane.get("lane_id")
        rca_summary = ""
        if lane.get("latest_rca_keys"):
            rca_summary, _ = _resolve_entry_summary(lane["latest_rca_keys"][-1], "bug-rca:", lane.get("source_cluster_id"))
        verify_summary = ""
        verify_passed = None
        if lane.get("latest_verify_keys"):
            verify_summary, verify_passed = _resolve_entry_summary(lane["latest_verify_keys"][-1], "bug-reverify:", lane_id)
        regression_summary = ""
        regression_passed = None
        if lane.get("latest_regression_keys"):
            regression_summary, regression_passed = _resolve_entry_summary(lane["latest_regression_keys"][-1], "bug-regression:", lane_id)
        lane["latest_rca_summary"] = rca_summary or _text(lane.get("latest_rca_summary"))
        lane["latest_verify_summary"] = verify_summary or _text(lane.get("latest_verify_summary"))
        lane["latest_regression_summary"] = regression_summary or _text(lane.get("latest_regression_summary"))
        lane["latest_verify_passed"] = verify_passed
        lane["latest_regression_passed"] = regression_passed
        if lane.get("source_cluster_id") and lane["source_cluster_id"] in cluster_by_id:
            lane["cluster"] = cluster_by_id[lane["source_cluster_id"]]

    for report in reports:
        cluster_id = _text(report.get("cluster_id")).strip() or None
        lane_id = _text(report.get("lane_id")).strip() or None
        detail_entries = list(report_timeline_by_report.get(report["report_id"], []))
        report["observation_verdicts"] = sorted(
            obs_verdicts_by_report.get(report["report_id"], []),
            key=lambda entry: entry.get("created_at") or "",
        )
        cluster_group_id = _text(report.get("cluster", {}).get("group_id")) if isinstance(report.get("cluster"), dict) else ""
        if not cluster_group_id and cluster_id and cluster_id in cluster_by_id:
            cluster_group_id = _text(cluster_by_id[cluster_id].get("group_id"))
        report["fix_attempts"] = [
            attempt for attempt in fix_attempts
            if attempt.get("group_id") == cluster_group_id
            or attempt.get("group_id") == cluster_id
            or attempt.get("bug_id") == report["report_id"]
        ]
        if report.get("decision_id") and report["decision_id"] in decisions_by_id:
            report["decision"] = decisions_by_id[report["decision_id"]]
        elif decisions:
            matching = next(
                (decision for decision in decisions if report["report_id"] in decision.get("report_ids", [])),
                None,
            )
            if matching:
                report["decision"] = matching
        if cluster_id and cluster_id in cluster_by_id:
            report["cluster"] = cluster_by_id[cluster_id]
            if not lane_id:
                lane_id = _text(report["cluster"].get("lane_id")).strip() or None
        if lane_id and lane_id in lane_by_id:
            report["lane"] = lane_by_id[lane_id]
        strategy_key = _text(report.get("strategy_decision_key"))
        failure_bundle_key = _text(report.get("latest_failure_bundle_key"))
        if strategy_key and strategy_key in strategy_decisions_by_key:
            report["strategy_decision"] = strategy_decisions_by_key[strategy_key]
        if failure_bundle_key and failure_bundle_key in failure_bundles_by_key:
            report["latest_failure_bundle"] = failure_bundles_by_key[failure_bundle_key]
        latest_proof_key = _text(report.get("latest_proof_key"))
        terminal_proof_key = _text(report.get("terminal_proof_key"))
        if latest_proof_key and latest_proof_key in proofs_by_key:
            report["latest_proof"] = proofs_by_key[latest_proof_key]
        if terminal_proof_key and terminal_proof_key in proofs_by_key:
            report["terminal_proof"] = proofs_by_key[terminal_proof_key]
        for artifact_key in [latest_proof_key, terminal_proof_key, failure_bundle_key, strategy_key]:
            if not artifact_key:
                continue
            if any(entry.get("key") == artifact_key for entry in detail_entries):
                continue
            raw = artifacts.get(artifact_key) or latest_timeline_entries.get(artifact_key)
            if not raw:
                continue
            parsed = _parse_timeline_entry(artifact_key, raw[0], raw[1])
            if parsed:
                detail_entries.append(parsed)
        report["detail_timeline"] = sorted(
            detail_entries,
            key=lambda entry: entry.get("created_at") or "",
        )

    for cluster in clusters:
        strategy_key = _text(cluster.get("strategy_decision_key"))
        failure_bundle_key = _text(cluster.get("stable_bundle_key"))
        if strategy_key and strategy_key in strategy_decisions_by_key:
            cluster["strategy_decision"] = strategy_decisions_by_key[strategy_key]
        if failure_bundle_key and failure_bundle_key in failure_bundles_by_key:
            cluster["stable_bundle"] = failure_bundles_by_key[failure_bundle_key]

    reports.sort(key=lambda report: (report.get("updated_at") or "", report.get("report_id") or ""), reverse=True)
    lanes.sort(key=lambda lane: (lane.get("updated_at") or "", lane.get("lane_id") or ""), reverse=True)
    clusters.sort(key=lambda cluster: (cluster.get("updated_at") or "", cluster.get("cluster_id") or ""), reverse=True)

    active_lanes: list[dict[str, Any]] = []
    active_lane_ids = _string_list(queue.get("active_lane_ids"))
    if active_lane_ids:
        active_lanes = [lane_by_id[lane_id] for lane_id in active_lane_ids if lane_id in lane_by_id]
    if not active_lanes:
        active_lanes = [
            lane for lane in lanes
            if _text(lane.get("status")) in {"active_fix", "active_verify"}
        ]

    promoting_lane = None
    promoting_lane_id = _text(queue.get("promoting_lane_id")).strip() or None
    if promoting_lane_id and promoting_lane_id in lane_by_id:
        promoting_lane = lane_by_id[promoting_lane_id]
    if promoting_lane is None:
        promoting_lane = next((lane for lane in lanes if _text(lane.get("status")) == "promoting"), None)

    verified_pending_promotion = [
        lane_by_id[lane_id]
        for lane_id in _string_list(queue.get("verified_pending_promotion_ids"))
        if lane_id in lane_by_id
    ]
    if not verified_pending_promotion:
        verified_pending_promotion = [
            lane for lane in lanes if _text(lane.get("status")) == "verified_pending_promotion"
        ]

    active_report = None
    active_report_id = _text(queue.get("active_report_id")).strip() or None
    if active_report_id:
        active_report = next((report for report in reports if report["report_id"] == active_report_id), None)
    if active_report is None:
        active_report = next(
            (report for report in reports if _report_lane(_text(report.get("status"))) == "active_fix"),
            None,
        )

    repo_status_raw = artifacts.get("bugflow-repo-status", ("{}", ""))[0]
    repo_status_payload = _safe_json(repo_status_raw)
    if isinstance(repo_status_payload, list):
        repo_status = {"repos": repo_status_payload}
    elif isinstance(repo_status_payload, dict):
        repo_status = dict(repo_status_payload)
    else:
        repo_status = {"repos": []}
    repo_status.setdefault("branch_name", _text(repo_status.get("branch_name")))
    repo_status["repos"] = [
        {
            "repo_path": _text(repo.get("repo_path") or repo.get("path")),
            "repo_name": _text(repo.get("repo_name") or repo.get("name") or Path(_text(repo.get("repo_path") or repo.get("path"))).name),
            "last_pushed_commit": _text(repo.get("last_pushed_commit") or repo.get("commit")),
            "status": _text(repo.get("status") or repo.get("push_status")),
            "touched": bool(repo.get("touched", True)),
            "last_push_at": _ensure_iso(repo.get("last_push_at")),
        }
        for repo in _safe_list(repo_status.get("repos"))
        if isinstance(repo, dict)
    ]
    repo_status.setdefault(
        "has_unpushed_verified_work",
        bool(repo_status.get("has_unpushed_verified_work"))
        or any(repo["status"] in {"verified", "pending_push"} for repo in repo_status["repos"]),
    )
    repo_status.setdefault("unpromoted_lane_ids", _string_list(repo_status.get("unpromoted_lane_ids")))

    timeline_entries = []
    for row in timeline_rows:
        entry = _parse_timeline_entry(row["key"], row["value"], row["created_at"])
        if entry:
            timeline_entries.append(entry)
    seen_timeline_keys = {entry["key"] for entry in timeline_entries}
    for row in rows:
        if row["key"] in seen_timeline_keys:
            continue
        if row["key"] not in {"bugflow-repo-status", "bugflow-promotion-queue"}:
            continue
        entry = _parse_timeline_entry(row["key"], row["value"], row["created_at"])
        if entry:
            timeline_entries.append(entry)

    sections = [
        {
            "name": "Queue / Intake",
            "entries": [entry for entry in timeline_entries if entry["type"] in {"queue", "report", "cluster", "lane"}],
        },
        {
            "name": "Fix Engine",
            "entries": [
                entry
                for entry in timeline_entries
                if entry["type"] in {
                    "triage",
                    "rca",
                    "dispatch",
                    "reverify",
                    "regression",
                    "fix",
                    "observation",
                    "proof",
                    "strategy",
                    "failure_bundle",
                }
            ],
        },
        {
            "name": "Decisions",
            "entries": [entry for entry in timeline_entries if entry["type"] in {"decision"}],
        },
        {
            "name": "Pushes",
            "entries": [entry for entry in timeline_entries if entry["type"] in {"push", "promotion"}],
        },
    ]
    sections = [section for section in sections if section["entries"]]

    status_text = _derive_bugflow_status_text(queue, active_lanes, promoting_lane, active_report, counts)
    health = _derive_bugflow_health(queue, reports, active_lanes, promoting_lane, active_report, counts)

    event_list = [
        {
            "event_type": event["event_type"],
            "source": event["source"],
            "content": event["content"] or "",
            "created_at": event["created_at"].isoformat(),
        }
        for event in events
    ]
    active_agent = next(
        (event["source"] for event in events if event["event_type"] == "agent_start"),
        None,
    )

    bugflow = {
        "source_feature_id": _text(queue.get("source_feature_id") or metadata.get("source_feature_id")),
        "dashboard_url": _text(queue.get("dashboard_url") or metadata.get("dashboard_url") or f"{request_base_url}/feature/{feature_id}"),
        "dashboard_message_ts": _text(queue.get("dashboard_message_ts") or metadata.get("dashboard_message_ts")),
        "health": health,
        "status_text": status_text,
        "active_step": _text(queue.get("active_step") or status_text),
        "active_report_id": _text(queue.get("active_report_id") or (active_report or {}).get("report_id")),
        "active_cluster_id": _text(queue.get("active_cluster_id")),
        "active_lane_ids": _string_list(queue.get("active_lane_ids")),
        "verified_pending_promotion_ids": _string_list(queue.get("verified_pending_promotion_ids")),
        "promoting_lane_id": _text(queue.get("promoting_lane_id") or (promoting_lane or {}).get("lane_id")),
        "promotion_status_text": _text(queue.get("promotion_status_text")),
        "active_round": queue.get("active_round"),
        "total_rounds": queue.get("total_rounds"),
        "active_attempt": queue.get("active_attempt"),
        "counts": counts,
        "pending_retriage_ids": _string_list(queue.get("pending_retriage_ids")),
        "blocked_ids": _string_list(queue.get("blocked_ids")),
        "recovering_lane_ids": _string_list(queue.get("recovering_lane_ids")),
        "stalled_lane_ids": _string_list(queue.get("stalled_lane_ids")),
        "strategy_pending_cluster_ids": _string_list(queue.get("strategy_pending_cluster_ids")),
        "last_transition_at": _ensure_iso(queue.get("last_transition_at")) or last_activity_at,
        "reports": reports,
        "lanes": lanes,
        "clusters": clusters,
        "active_lanes": active_lanes,
        "verified_pending_promotion": verified_pending_promotion,
        "promoting_lane": promoting_lane,
        "decisions": decisions,
        "repo_status": repo_status,
        "timeline_sections": sections,
        "artifact_timeline": timeline_entries,
    }

    return {
        "id": feat["id"],
        "name": feat["name"],
        "phase": feat["phase"],
        "workflow_name": feat["workflow_name"],
        "updated_at": feat["updated_at"].isoformat(),
        "last_activity_at": last_activity_at,
        "source_feature_id": bugflow["source_feature_id"] or None,
        "dashboard_url": bugflow["dashboard_url"] or None,
        "bugflow": bugflow,
        "dag": None,
        "groups": [],
        "gates": {},
        "active_gate": None,
        "active_gate_steps": [],
        "timeline": bugflow["artifact_timeline"],
        "workstreams": [],
        "events": event_list,
        "active_agent": active_agent,
    }


def _parse_timeline_entry(key: str, value: str, created_at) -> dict | None:
    """Parse a timeline artifact into a display entry."""
    ts = created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at)

    if key == "bugflow-queue":
        payload = _safe_dict(value)
        counts = payload.get("counts") if isinstance(payload.get("counts"), dict) else {}
        count_bits = ", ".join(
            f"{name}={count}"
            for name, count in counts.items()
            if isinstance(count, int) and count > 0
        )
        active_step = _text(payload.get("active_step") or payload.get("status_text") or payload.get("summary"))
        summary = active_step or count_bits or "Queue snapshot updated"
        return {"key": key, "type": "queue", "passed": None, "summary": summary, "created_at": ts}

    if key.startswith("bugflow-report:"):
        report_id = key.split(":", 1)[1]
        payload = _safe_dict(value)
        status = _text(payload.get("status") or payload.get("state")).strip()
        current_step = _text(payload.get("current_step")).strip()
        summary = " — ".join(part for part in [report_id, status, current_step] if part)
        if not summary:
            summary = report_id
        return {"key": key, "type": "report", "passed": None, "summary": summary, "created_at": ts}

    if key.startswith("bugflow-cluster:"):
        cluster_id = key.split(":", 1)[1]
        payload = _safe_dict(value)
        status = _text(payload.get("status") or payload.get("state")).strip()
        phase = _text(payload.get("current_phase")).strip()
        summary = " — ".join(part for part in [cluster_id, phase or status] if part)
        if not summary:
            summary = cluster_id
        return {"key": key, "type": "cluster", "passed": None, "summary": summary, "created_at": ts}

    if key.startswith("bugflow-lane:"):
        lane_id = key.split(":", 1)[1]
        payload = _safe_dict(value)
        status = _text(payload.get("status") or payload.get("state")).strip()
        phase = _text(payload.get("current_phase") or payload.get("promotion_status")).strip()
        summary = " — ".join(part for part in [lane_id, phase or status] if part)
        if not summary:
            summary = lane_id
        return {"key": key, "type": "lane", "passed": None, "summary": summary, "created_at": ts}

    if key.startswith("bugflow-proof:"):
        parts = key.split(":")
        report_id = parts[1] if len(parts) > 1 else "report"
        stage = parts[2] if len(parts) > 2 else "proof"
        payload = _safe_dict(value)
        bundle = payload.get("bundle") if isinstance(payload.get("bundle"), dict) else {}
        summary = _text(bundle.get("summary") or payload.get("bundle_url") or f"{report_id} {stage} proof")
        return {"key": key, "type": "proof", "passed": None, "summary": summary, "created_at": ts}

    if key.startswith("bugflow-failure-bundle:"):
        payload = _safe_dict(value)
        cluster_id = key.split(":")[1] if len(key.split(":")) > 1 else "cluster"
        family = _text(payload.get("stable_failure_family")).strip()
        summary = _text(payload.get("bundle_summary")).strip()
        label = " — ".join(part for part in [cluster_id, family or summary] if part)
        return {
            "key": key,
            "type": "failure_bundle",
            "passed": None,
            "summary": label or cluster_id,
            "created_at": ts,
        }

    if key.startswith("bugflow-strategy:"):
        payload = _safe_dict(value)
        cluster_id = key.split(":")[1] if len(key.split(":")) > 1 else "cluster"
        mode = _text(payload.get("strategy_mode")).strip()
        reasoning = _text(payload.get("reasoning") or payload.get("bundle_summary")).strip()
        label = " — ".join(part for part in [cluster_id, mode, reasoning] if part)
        return {
            "key": key,
            "type": "strategy",
            "passed": None,
            "summary": label or cluster_id,
            "created_at": ts,
        }

    if key == "bugflow-decisions":
        payload = _safe_json(value)
        if isinstance(payload, list):
            count = len(payload)
        elif isinstance(payload, dict):
            count = len(_safe_list(payload.get("decisions")))
        else:
            count = 0
        summary = f"{count} confirmed decisions" if count else _text(value)
        return {"key": key, "type": "decision", "passed": None, "summary": summary, "created_at": ts}

    if key == "bugflow-repo-status":
        payload = _safe_dict(value)
        repos = _safe_list(payload.get("repos"))
        branch = _text(payload.get("branch_name"))
        repo_names = ", ".join(
            _text(repo.get("repo_name") or repo.get("name") or Path(_text(repo.get("repo_path") or repo.get("path"))).name)
            for repo in repos if isinstance(repo, dict)
        )
        summary = repo_names or branch or "Repo status updated"
        return {"key": key, "type": "push", "passed": None, "summary": summary, "created_at": ts}

    if key == "bugflow-promotion-queue":
        payload = _safe_dict(value)
        summary = _text(payload.get("status_text") or payload.get("promoting_lane_id"))
        summary = summary or "Promotion queue updated"
        return {"key": key, "type": "promotion", "passed": None, "summary": summary, "created_at": ts}

    if key.startswith("dag-verify-rca:"):
        summary = ""
        try:
            v = json.loads(value)
            summary = v.get("hypothesis", "")
        except (json.JSONDecodeError, KeyError):
            summary = value if value else ""
        return {"key": key, "type": "rca", "passed": None, "summary": summary, "created_at": ts}

    if key.startswith("dag-fix:"):
        summary = ""
        try:
            v = json.loads(value)
            summary = v.get("summary", "")
        except (json.JSONDecodeError, KeyError):
            summary = value if value else ""
        return {"key": key, "type": "fix", "passed": None, "summary": summary, "created_at": ts}

    if key.startswith("dag-verify:"):
        passed = False
        summary = ""
        try:
            v = json.loads(value)
            passed = v.get("approved", False)
            summary = v.get("summary", "")
        except (json.JSONDecodeError, KeyError):
            summary = value if value else ""
        return {"key": key, "type": "verify", "passed": passed, "summary": summary, "created_at": ts}

    if key.startswith("bug-rca:"):
        summary = ""
        try:
            v = json.loads(value)
            summary = v.get("hypothesis", "")
        except (json.JSONDecodeError, KeyError):
            summary = value if value else ""
        return {"key": key, "type": "rca", "passed": None, "summary": summary, "created_at": ts}

    if key.startswith("bug-reverify:"):
        passed = False
        summary = ""
        try:
            v = json.loads(value)
            passed = v.get("approved", False)
            summary = v.get("summary", "")
        except (json.JSONDecodeError, KeyError):
            summary = value if value else ""
        return {"key": key, "type": "reverify", "passed": passed, "summary": summary, "created_at": ts}

    if key.startswith("bug-regression:"):
        passed = False
        summary = ""
        try:
            v = json.loads(value)
            passed = v.get("approved", False)
            summary = v.get("summary", "")
        except (json.JSONDecodeError, KeyError):
            summary = value if value else ""
        return {"key": key, "type": "regression", "passed": passed, "summary": summary, "created_at": ts}

    if key.startswith("bug-triage:"):
        summary = ""
        try:
            v = json.loads(value)
            groups = v.get("groups", [])
            summary = f"{len(groups)} bug groups identified"
        except (json.JSONDecodeError, KeyError):
            summary = value if value else ""
        return {"key": key, "type": "triage", "passed": None, "summary": summary, "created_at": ts}

    if key.startswith("bug-dispatch:"):
        summary = ""
        try:
            json.loads(value)  # validate JSON
            summary = value  # pass full JSON for expanded view
        except (json.JSONDecodeError, KeyError):
            summary = value if value else ""
        return {"key": key, "type": "dispatch", "passed": None, "summary": summary, "created_at": ts}

    if key == "bug-fix-attempts":
        return {"key": key, "type": "fix-attempts", "passed": None, "summary": value or "", "created_at": ts}

    if key.startswith("obs-verdict:"):
        passed = False
        summary = ""
        try:
            v = json.loads(value)
            passed = bool(v.get("approved", False))
            summary = v.get("summary", "")
        except (json.JSONDecodeError, KeyError):
            summary = value if value else ""
        return {"key": key, "type": "observation", "passed": passed, "summary": summary, "created_at": ts}

    if key.startswith("contradiction:"):
        summary = value if value else ""
        return {"key": key, "type": "decision", "passed": None, "summary": summary, "created_at": ts}

    if key.endswith("-verdict"):
        passed = False
        summary = ""
        try:
            v = json.loads(value)
            # Mirror _is_approved: only pass if no blocker/major concerns/gaps and no FAIL checks
            blocking = {"blocker", "major"}
            has_blocking = any(
                c.get("severity") in blocking for c in v.get("concerns", [])
            ) or any(
                g.get("severity") in blocking for g in v.get("gaps", [])
            ) or any(
                ch.get("result") == "FAIL" for ch in v.get("checks", [])
            )
            passed = not has_blocking
            summary = v.get("summary", "")
        except (json.JSONDecodeError, KeyError):
            summary = value if value else ""
        return {"key": key, "type": "verdict", "passed": passed, "summary": summary, "created_at": ts}

    return None


@app.get("/api/search")
async def search_features(q: str = Query("", min_length=1)):
    """Search features by ID prefix or name substring."""
    assert pool
    rows = await pool.fetch(
        "SELECT id, name, phase, updated_at FROM features "
        "WHERE id LIKE $1 OR name ILIKE $2 "
        "ORDER BY updated_at DESC LIMIT 10",
        f"{q}%",
        f"%{q}%",
    )
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "phase": r["phase"],
            "updated_at": r["updated_at"].isoformat(),
        }
        for r in rows
    ]


def _ui_index_response() -> FileResponse:
    index_path = _UI_DIST / "index.html"
    if not index_path.exists():
        raise HTTPException(503, "dashboard-ui build not found; run `npm --prefix dashboard-ui build`")
    return FileResponse(index_path)


if _UI_DIST.exists():
    assets_dir = _UI_DIST / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="dashboard-assets")


@app.get("/")
async def serve_dashboard_root():
    return _ui_index_response()


@app.get("/terminal")
async def serve_dashboard_terminal():
    return _ui_index_response()


@app.get("/feature/{feature_id}")
async def serve_dashboard_feature(feature_id: str):
    return _ui_index_response()


@app.get("/proof/{feature_id}/{report_id}/{stage}/{filename:path}")
async def serve_bugflow_proof(
    feature_id: str,
    report_id: str,
    stage: str,
    filename: str,
):
    assert pool
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT slug, metadata FROM features WHERE id = $1",
            feature_id,
        )
    if not row:
        raise HTTPException(404, "Feature not found")

    metadata = row["metadata"] if isinstance(row["metadata"], dict) else _safe_dict(row["metadata"])
    workspace_path = _text(metadata.get("workspace_path"))
    if not workspace_path:
        raise HTTPException(404, "Feature workspace not configured")

    proof_root = feature_root_from_workspace(workspace_path, row["slug"]) / "proof"
    candidate = (proof_root / report_id / stage / filename).resolve()
    root_resolved = proof_root.resolve()
    if candidate != root_resolved and root_resolved not in candidate.parents:
        raise HTTPException(404, "Proof artifact not found")
    if not candidate.exists() or not candidate.is_file():
        raise HTTPException(404, "Proof artifact not found")
    return FileResponse(candidate)


@app.get("/favicon.svg")
async def serve_dashboard_favicon():
    path = _UI_DIST / "favicon.svg"
    if not path.exists():
        raise HTTPException(404, "favicon not found")
    return FileResponse(path)


@app.get("/icons.svg")
async def serve_dashboard_icons():
    path = _UI_DIST / "icons.svg"
    if not path.exists():
        raise HTTPException(404, "icons not found")
    return FileResponse(path)


@app.get("/{feature_id}")
async def serve_dashboard_feature_legacy(feature_id: str):
    if feature_id in {"api", "assets"} or "." in feature_id:
        raise HTTPException(404, "Not found")
    return _ui_index_response()


# ── Bridge API ─────────────────────────────────────────────────────────────


@app.get("/api/bridge/status")
async def bridge_status():
    if not bridge:
        raise HTTPException(404, "Bridge not configured (missing --bridge-channel)")
    return bridge.status()


@app.post("/api/bridge/restart")
async def bridge_restart():
    if not bridge:
        raise HTTPException(404, "Bridge not configured (missing --bridge-channel)")
    await bridge.restart()
    return bridge.status()


@app.get("/api/bridge/logs")
async def bridge_logs(after: int = 0):
    """Return log lines after the given cursor. Poll this every ~1s."""
    if not bridge:
        raise HTTPException(404, "Bridge not configured (missing --bridge-channel)")
    current = bridge.line_count
    if after >= current:
        return {"lines": [], "cursor": current}
    # How many lines back from current to start
    skip = current - after
    snapshot = list(bridge.lines)
    new_lines = snapshot[-skip:] if skip <= len(snapshot) else snapshot
    return {"lines": new_lines, "cursor": current}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="iriai-build-v2 dashboard")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--bridge-channel", default=None, help="Slack channel ID for bridge")
    parser.add_argument("--bridge-workspace", default=None)
    parser.add_argument("--bridge-mode", default="multiplayer", choices=["multiplayer", "singleplayer"])
    parser.add_argument("--bridge-agent-runtime", default=None)
    parser.add_argument("--bridge-claude-only", action="store_true")
    parser.add_argument("--bridge-budget", action="store_true")
    args = parser.parse_args()

    dashboard_config["port"] = args.port

    if args.bridge_channel:
        bridge_config.update({
            "channel": args.bridge_channel,
            "workspace": args.bridge_workspace,
            "mode": args.bridge_mode,
            "agent_runtime": args.bridge_agent_runtime,
            "claude_only": args.bridge_claude_only,
            "budget": args.bridge_budget,
        })

    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="info")
