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
from datetime import datetime, timezone
from typing import Any

import asyncpg
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
import uvicorn

from iriai_build_v2.workflows.bugfix_v2.proof import feature_root_from_workspace
from iriai_build_v2.runtime_policy import (
    DEFAULT_RUNTIME_POLICY,
    PRIMARY_IMPL_SECONDARY_REVIEW_POLICY,
    SUPPORTED_RUNTIME_POLICIES,
)

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
        if self.config.get("runtime_policy"):
            cmd += ["--runtime-policy", str(self.config["runtime_policy"])]
        if self.config.get("claude_only"):
            cmd.append("--claude-only")
        if self.config.get("budget"):
            cmd.append("--budget")
        if self.config.get("autonomous_remainder"):
            cmd.append("--autonomous-remainder")
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
_PUBLIC_FORBIDDEN_PATTERNS = [
    re.compile(r"/Users/[^\s`\"')]+"),
    re.compile(r"/private/(?:var|tmp)/[^\s`\"')]+"),
    re.compile(r"/var/folders/[^\s`\"')]+"),
    re.compile(r"(?<![\w.-])\.iriai(?:/|\b)"),
    re.compile(r"(?<![\w.-])\.iriai-context(?:/|\b)"),
    re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{16,}"),
]

_EXHIBIT_SOURCE_PREFIXES = (
    "public-",
    "prd",
    "design",
    "plan",
    "system-design",
    "test-plan",
    "decisions",
    "decomposition",
    "artifact-audit",
    "artifact-backfill",
    "planning-index",
    "gate-review",
)
_EXHIBIT_SOURCE_KEYS = {
    "project",
    "scope",
    "implementation",
    "handover",
    "dag",
    "dag:strategy",
    "enhancement-backlog",
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

        artifact_meta_rows: list[asyncpg.Record] = []
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
                "     OR key LIKE 'dag-repair-%' "
                "     OR key LIKE 'contradiction:dag-repair:%' "
                "     OR key LIKE 'contradiction-rejected:dag-repair:%' "
                "     OR key LIKE 'public-%' "
                "     OR key LIKE 'enhancement-%' "
                "     OR key IN ('project', 'scope', 'prd', 'prd:broad', "
                "                'design:broad', 'plan:broad', 'system-design:broad', "
                "                'test-plan:broad', 'decomposition', 'decomposition-structured') "
                "     OR key = 'implementation' OR key = 'handover') "
                "ORDER BY key, id DESC",
                feature_id,
            )
            artifact_meta_rows = await conn.fetch(
                "SELECT DISTINCT ON (key) key, created_at "
                "FROM artifacts WHERE feature_id = $1 "
                "AND (key LIKE 'public-%' "
                "     OR key LIKE 'prd%' OR key LIKE 'design%' "
                "     OR key LIKE 'plan%' OR key LIKE 'system-design%' "
                "     OR key LIKE 'test-plan%' OR key LIKE 'decisions%' "
                "     OR key LIKE 'decomposition%' OR key LIKE 'artifact-audit%' "
                "     OR key LIKE 'artifact-backfill%' OR key LIKE 'planning-index%' "
                "     OR key LIKE 'gate-review%' "
                "     OR key IN ('project', 'scope', 'dag', 'dag:strategy', 'implementation', 'handover')) "
                "ORDER BY key, id DESC",
                feature_id,
            )

            # 3. All verify/bug artifacts with full history (for timeline)
            timeline_rows = await conn.fetch(
                "SELECT key, value, created_at FROM artifacts "
                "WHERE feature_id = $1 "
                "AND (key LIKE 'dag-verify:%' OR key LIKE 'dag-fix:%' OR key LIKE 'dag-verify-rca:%' "
                "     OR key LIKE 'dag-repair-%' "
                "     OR key LIKE 'contradiction:dag-repair:%' "
                "     OR key LIKE 'contradiction-rejected:dag-repair:%' "
                "     OR key LIKE 'public-%' "
                "     OR key LIKE 'bug-%' OR key LIKE '%-verdict') "
                "ORDER BY created_at DESC LIMIT 500",
                feature_id,
            )

        # 4. Recent events
        events = await conn.fetch(
            "SELECT event_type, source, content, created_at "
            "FROM events WHERE feature_id = $1 "
            "ORDER BY created_at DESC LIMIT 250",
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
    artifact_catalog = dict(artifacts)
    for r in artifact_meta_rows:
        artifact_catalog.setdefault(r["key"], ("", r["created_at"].isoformat()))

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
                    "dependencies": task_def.get("dependencies", []),
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

    dag_repair = _assemble_dag_repair_metrics(
        timeline_rows,
        artifacts,
        dag_info,
    )
    agent_exhibit_base = _assemble_agent_activity(
        events,
        artifacts=artifacts,
        groups=groups,
        timeline=timeline,
    )
    active_agent = (
        agent_exhibit_base["active_agents"][0]["name"]
        if agent_exhibit_base["active_agents"]
        else None
    )
    public_exhibit = _assemble_public_exhibit(
        feat=feat,
        artifacts=artifacts,
        artifact_catalog=artifact_catalog,
        timeline=timeline,
        events=event_list,
        agent_activity=agent_exhibit_base,
        dag_info=dag_info,
        groups=groups,
        workstreams=workstreams_list,
        dag_repair=dag_repair,
        active_gate=active_gate,
        gates=gates,
        last_activity_at=last_activity_at,
    )

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
        "dag_repair": dag_repair,
        "events": event_list,
        "active_agent": active_agent,
        "public_exhibit": public_exhibit,
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


def _row_created_at(row: Any) -> Any:
    return row["created_at"]


def _row_key(row: Any) -> str:
    return str(row["key"])


def _row_value(row: Any) -> str:
    return str(row["value"] or "")


def _parse_time(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        raw = value.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _seconds_between(start: datetime | None, end: datetime | None) -> int | None:
    if not start or not end:
        return None
    return max(0, int((end - start).total_seconds()))


def _latest_time(values: list[datetime]) -> datetime | None:
    return max(values) if values else None


def _earliest_time(values: list[datetime]) -> datetime | None:
    return min(values) if values else None


def _dag_repair_cycle_key(key: str) -> tuple[int, str, str] | None:
    patterns: list[tuple[str, str]] = [
        (r"^dag-repair-preflight:g(\d+):retry-(.+)$", "preflight"),
        (r"^dag-verify:g(\d+):(initial|retry-.+)$", "verify"),
        (r"^dag-repair-lens:g(\d+):[^:]+:retry-(.+)$", "lens"),
        (r"^dag-repair-expanded-verify:g(\d+):retry-(.+)$", "expanded"),
        (r"^dag-repair-triage:g(\d+):retry-(.+)$", "triage"),
        (r"^dag-repair-rca:g(\d+):.+:retry-(.+)$", "rca"),
        (r"^dag-repair-dispatch:g(\d+):retry-(.+)$", "dispatch"),
        (r"^dag-fix:g(\d+):retry-(.+)$", "fix"),
        (r"^dag-repair-reverify:g(\d+):.+:retry-(.+)$", "focused_reverify"),
        (r"^dag-repair-result-sanitize:g(\d+):retry-(.+)$", "sanitize"),
        (r"^dag-repair-fix-error:g(\d+):.+:retry-(.+):round-.+$", "fix_error"),
        (r"^contradiction(?:-rejected)?:dag-repair:g(\d+):retry-(.+):.+$", "contradiction"),
    ]
    for pattern, event_type in patterns:
        match = re.match(pattern, key)
        if not match:
            continue
        retry = match.group(2)
        if retry.startswith("retry-"):
            retry = retry[len("retry-"):]
        return int(match.group(1)), retry, event_type
    return None


def _dag_stage_duration(
    start_times: list[datetime],
    end_times: list[datetime],
) -> int | None:
    start = _earliest_time(start_times)
    end = _latest_time(end_times)
    return _seconds_between(start, end)


def _dag_fix_applied_count(value: str) -> int:
    payload = _safe_dict(value)
    summary = _text(payload.get("summary") or value)
    match = re.search(r"applied\s+(\d+)\s+root-cause-group", summary)
    if match:
        return int(match.group(1))
    return 1 if summary else 0


def _dag_sanitizer_counts(value: str) -> dict[str, int]:
    payload = _safe_dict(value)
    counts = payload.get("counts") if isinstance(payload.get("counts"), dict) else {}
    return {
        "ignored": int(payload.get("ignored_path_count") or 0),
        "rewritten": int(payload.get("rewritten_path_count") or 0),
        "invalid": int(payload.get("invalid_product_path_count") or 0),
        "artifact_context": int(counts.get("artifact_context") or 0),
        "external_reference": int(counts.get("external_reference") or 0),
    }


def _assemble_dag_repair_metrics(
    timeline_rows: list[asyncpg.Record],
    artifacts: dict[str, tuple[str, str]],
    dag_info: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not dag_info:
        return None

    total_groups = int(dag_info.get("total_groups") or 0)
    completed_group_indices = sorted(
        int(match.group(1))
        for key in artifacts
        if (match := re.match(r"^dag-group:(\d+)$", key))
    )
    completed_groups = len(completed_group_indices)
    latest_checkpoint_group = (
        max(completed_group_indices) if completed_group_indices else None
    )
    active_group_index: int | None = None
    for idx in range(total_groups):
        if idx not in set(completed_group_indices):
            active_group_index = idx
            break

    cycle_events: dict[tuple[int, str], dict[str, Any]] = {}
    for row in sorted(timeline_rows, key=lambda r: _row_created_at(r)):
        key = _row_key(row)
        parsed = _dag_repair_cycle_key(key)
        if not parsed:
            continue
        group_idx, retry, event_type = parsed
        created = _parse_time(_row_created_at(row))
        if created is None:
            continue
        value = _row_value(row)
        cycle = cycle_events.setdefault((group_idx, retry), {
            "group_idx": group_idx,
            "retry": retry,
            "events": [],
            "times": collections.defaultdict(list),
            "values": collections.defaultdict(list),
        })
        cycle["events"].append({"key": key, "type": event_type, "created_at": created})
        cycle["times"][event_type].append(created)
        cycle["values"][event_type].append(value)

    now = datetime.now(timezone.utc)
    cycles: list[dict[str, Any]] = []
    summary_counts = {
        "expanded_verify_runs": 0,
        "fix_groups_scheduled": 0,
        "fix_groups_applied": 0,
        "final_preflight_failures": 0,
        "sanitizer_ignored_paths": 0,
        "sanitizer_rewritten_paths": 0,
        "sanitizer_invalid_paths": 0,
    }
    latest_active_cycle: dict[str, Any] | None = None

    for (group_idx, retry), cycle in sorted(cycle_events.items()):
        times: dict[str, list[datetime]] = cycle["times"]
        values: dict[str, list[str]] = cycle["values"]
        all_times = [event["created_at"] for event in cycle["events"]]
        started = _earliest_time(all_times)
        checkpoint_raw = artifacts.get(f"dag-group:{group_idx}")
        checkpoint_time = _parse_time(checkpoint_raw[1]) if checkpoint_raw else None
        ended = checkpoint_time or _latest_time(all_times)

        latest_verify_value = values.get("verify", [""])[-1] if values.get("verify") else ""
        latest_verify = _safe_dict(latest_verify_value)
        latest_verify_approved = bool(latest_verify.get("approved", False))
        final_blocker_summary = _text(latest_verify.get("summary"))

        is_active_group = active_group_index == group_idx
        if checkpoint_time:
            status = "passed"
        elif latest_verify_value and not latest_verify_approved:
            status = "failed"
        elif is_active_group:
            status = "running"
            ended = now
        else:
            status = "waiting"

        dispatch_value = values.get("dispatch", [""])[-1] if values.get("dispatch") else ""
        dispatch_payload = _safe_dict(dispatch_value)
        schedule = dispatch_payload.get("schedule") if isinstance(dispatch_payload.get("schedule"), list) else []
        scheduled = sum(
            len(item.get("group_ids", []))
            for item in schedule
            if isinstance(item, dict)
        )
        applied = sum(_dag_fix_applied_count(value) for value in values.get("fix", []))
        contradiction_count = int(dispatch_payload.get("contradiction_group_count") or 0)
        rejected_contradiction_count = int(dispatch_payload.get("rejected_contradiction_count") or 0)

        sanitizer_counts = {"ignored": 0, "rewritten": 0, "invalid": 0}
        for value in values.get("sanitize", []):
            counts = _dag_sanitizer_counts(value)
            sanitizer_counts["ignored"] += counts["ignored"]
            sanitizer_counts["rewritten"] += counts["rewritten"]
            sanitizer_counts["invalid"] += counts["invalid"]

        if values.get("expanded"):
            summary_counts["expanded_verify_runs"] += len(values["expanded"])
        summary_counts["fix_groups_scheduled"] += scheduled
        summary_counts["fix_groups_applied"] += applied
        if latest_verify_value and not latest_verify_approved and "preflight failed" in final_blocker_summary.lower():
            summary_counts["final_preflight_failures"] += 1
        summary_counts["sanitizer_ignored_paths"] += sanitizer_counts["ignored"]
        summary_counts["sanitizer_rewritten_paths"] += sanitizer_counts["rewritten"]
        summary_counts["sanitizer_invalid_paths"] += sanitizer_counts["invalid"]

        stage_durations = {
            "preflight_initial": _dag_stage_duration(times.get("preflight", []), times.get("verify", []))
            if retry == "initial" else None,
            "normal_verify": _dag_stage_duration(times.get("preflight", []), times.get("verify", []))
            if retry == "initial" else None,
            "expanded_verify": _dag_stage_duration(times.get("lens", []), times.get("expanded", [])),
            "triage_rca": _dag_stage_duration(times.get("triage", []), times.get("rca", [])),
            "dispatch": 0 if times.get("dispatch") else None,
            "fix": _dag_stage_duration(times.get("dispatch", []), times.get("fix", [])),
            "focused_reverify": _dag_stage_duration(
                times.get("focused_reverify", []),
                times.get("focused_reverify", []),
            ),
            "final_preflight_verify": _dag_stage_duration(times.get("preflight", []), times.get("verify", []))
            if retry != "initial" else None,
        }
        cycle_record = {
            "group_idx": group_idx,
            "retry": retry,
            "started_at": started.isoformat() if started else None,
            "ended_at": ended.isoformat() if ended else None,
            "duration_seconds": _seconds_between(started, ended),
            "status": status,
            "stage_durations": {
                key: value for key, value in stage_durations.items()
                if value is not None
            },
            "lens_count": len(values.get("lens", [])),
            "rca_group_count": len(values.get("rca", [])),
            "fixable_group_count": int(dispatch_payload.get("fixable_group_count") or 0),
            "scheduled_round_count": len(schedule),
            "applied_fix_count": applied,
            "contradiction_count": contradiction_count,
            "rejected_contradiction_count": rejected_contradiction_count,
            "final_blocker_summary": final_blocker_summary,
        }
        cycles.append(cycle_record)

    active_elapsed_seconds: int | None = None
    if active_group_index is not None:
        active_times = [
            _parse_time(row["created_at"])
            for row in timeline_rows
            if (
                (parsed := _dag_repair_cycle_key(_row_key(row)))
                and parsed[0] == active_group_index
            )
        ]
        active_start = _earliest_time([value for value in active_times if value])
        if active_start is None and latest_checkpoint_group is not None:
            raw = artifacts.get(f"dag-group:{latest_checkpoint_group}")
            active_start = _parse_time(raw[1]) if raw else None
        active_elapsed_seconds = _seconds_between(active_start, now)

    retry_count_for_active = 0
    if active_group_index is not None:
        retry_count_for_active = len({
            cycle["retry"]
            for cycle in cycles
            if cycle["group_idx"] == active_group_index and cycle["retry"] != "initial"
        })

    cycles.sort(key=lambda cycle: cycle.get("started_at") or "")
    if active_group_index is not None:
        active_cycles = [
            cycle for cycle in cycles
            if cycle["group_idx"] == active_group_index
        ]
        latest_active_cycle = active_cycles[-1] if active_cycles else None

    return {
        "active_group_index": active_group_index,
        "latest_checkpoint_group": latest_checkpoint_group,
        "current_cycle": latest_active_cycle,
        "cycles": cycles[-12:],
        "summary": {
            "completed_groups": completed_groups,
            "total_groups": total_groups,
            "active_group_elapsed_seconds": active_elapsed_seconds,
            "retry_count_for_active_group": retry_count_for_active,
            **summary_counts,
        },
    }


def _public_text_is_safe(value: Any) -> bool:
    text = _text(value)
    if len(text) > 20_000:
        return False
    return not any(pattern.search(text) for pattern in _PUBLIC_FORBIDDEN_PATTERNS)


def _public_payload_is_safe(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return _public_text_is_safe(value)
    if isinstance(value, (int, float, bool)):
        return True
    if isinstance(value, list):
        return all(_public_payload_is_safe(item) for item in value)
    if isinstance(value, dict):
        return all(
            _public_text_is_safe(key) and _public_payload_is_safe(item)
            for key, item in value.items()
        )
    return _public_text_is_safe(value)


def _scrub_public_text(value: Any, *, max_len: int = 480) -> str:
    text = _text(value).strip()
    for pattern in _PUBLIC_FORBIDDEN_PATTERNS:
        text = pattern.sub("[redacted]", text)
    text = re.sub(r"\s+", " ", text)
    if len(text) > max_len:
        return text[: max_len - 1].rstrip() + "…"
    return text


def _safe_public_artifact(artifacts: dict[str, tuple[str, str]], key: str) -> dict[str, Any] | list[Any] | None:
    raw = artifacts.get(key)
    if not raw:
        return None
    payload = _safe_json(raw[0])
    if payload is None:
        payload = {"summary": raw[0]}
    if not _public_payload_is_safe(payload):
        return None
    return payload


def _public_content(payload: dict[str, Any] | list[Any] | None) -> Any:
    if isinstance(payload, dict):
        content = payload.get("content")
        if isinstance(content, (dict, list)):
            return content
        return payload
    return payload


def _artifact_family(key: str) -> str:
    if key.startswith("public-"):
        return "public narrative"
    if key.startswith("prd"):
        return "product"
    if key.startswith("design") or key.startswith("system-design"):
        return "design"
    if key.startswith("plan") or key.startswith("decomposition") or key == "dag" or key.startswith("dag:"):
        return "planning"
    if key.startswith("test-plan") or key.startswith("dag-verify") or key.startswith("dag-repair"):
        return "verification"
    if key.startswith("decisions") or key.startswith("contradiction"):
        return "decision"
    if key.startswith("artifact-audit") or key.startswith("artifact-backfill"):
        return "audit"
    if key in {"implementation", "handover"}:
        return "delivery"
    return "artifact"


def _artifact_label(key: str) -> str:
    labels = {
        "project": "Project Context",
        "scope": "Feature Scope",
        "prd:broad": "Broad PRD",
        "design:broad": "Broad Design",
        "plan:broad": "Broad Technical Plan",
        "system-design:broad": "Broad System Design",
        "test-plan:broad": "Broad Test Plan",
        "decomposition": "Subfeature Decomposition",
        "decomposition-structured": "Structured Decomposition",
        "dag:strategy": "DAG Workstream Strategy",
        "dag": "Root Implementation DAG",
        "implementation": "Implementation Report",
        "handover": "Implementation Handover",
        "public-summary": "Public Summary",
        "public-artifact-gallery": "Public Artifact Gallery",
        "public-dag-narrative": "Public DAG Narrative",
        "public-workstream-summary": "Public Workstream Summary",
        "public-milestone-feed": "Public Milestone Feed",
    }
    if key in labels:
        return labels[key]
    if ":" in key:
        family, slug = key.split(":", 1)
        return f"{family.replace('-', ' ').title()} — {slug.replace('-', ' ')}"
    return key.replace("-", " ").title()


def _artifact_description(key: str) -> str:
    family = _artifact_family(key)
    if key.startswith("public-"):
        return "Bridge-generated public presentation layer with provenance and safety checks."
    if family == "product":
        return "Defines product intent, requirements, journeys, and acceptance criteria."
    if family == "design":
        return "Captures experience, architecture, component, and system decisions."
    if family == "planning":
        return "Turns the feature plan into executable slices, workstreams, and task batches."
    if family == "verification":
        return "Records verifier findings, repair cycles, and quality evidence."
    if family == "decision":
        return "Tracks decisions and contradiction resolutions that guide implementation."
    if family == "audit":
        return "Shows migration, sidecar, parity, and traceability audit status."
    if family == "delivery":
        return "Summarizes completed implementation work and handover notes."
    return "Workflow artifact produced by the bridge."


def _artifact_is_exhibit_source(key: str) -> bool:
    return key in _EXHIBIT_SOURCE_KEYS or key.startswith(_EXHIBIT_SOURCE_PREFIXES)


def _extract_project_title(feat: asyncpg.Record, artifacts: dict[str, tuple[str, str]]) -> str:
    project = _safe_dict(artifacts.get("project", ("", ""))[0])
    title = project.get("feature_name") or feat["name"] or feat["id"]
    return _scrub_public_text(title, max_len=120)


def _extract_public_description(artifacts: dict[str, tuple[str, str]]) -> str:
    for key in ("prd:broad", "prd", "scope"):
        raw = artifacts.get(key)
        if not raw:
            continue
        payload = _safe_json(raw[0])
        if isinstance(payload, dict):
            for candidate in (
                payload.get("problem_statement"),
                payload.get("summary"),
                payload.get("description"),
                payload.get("overview"),
            ):
                if candidate:
                    return _scrub_public_text(candidate, max_len=540)
            content = payload.get("content")
            if isinstance(content, dict):
                for candidate in (
                    content.get("problem_statement"),
                    content.get("summary"),
                    content.get("description"),
                    content.get("overview"),
                ):
                    if candidate:
                        return _scrub_public_text(candidate, max_len=540)
        text = raw[0]
        match = re.search(
            r"##\s+(?:Problem Statement|Overview|Summary)\s*\n+(.+?)(?:\n##\s+|\Z)",
            text,
            flags=re.S | re.I,
        )
        if match:
            paragraph = next(
                (part.strip() for part in match.group(1).split("\n\n") if part.strip()),
                "",
            )
            if paragraph:
                return _scrub_public_text(paragraph, max_len=540)
    return "A multi-agent workflow is planning, building, verifying, and documenting this feature."


def _derive_public_health(
    *,
    phase: str,
    groups: list[dict[str, Any]],
    dag_repair: dict[str, Any] | None,
    active_gate: str | None,
) -> str:
    if phase == "complete":
        return "complete"
    if phase == "failed":
        return "blocked"
    if dag_repair and dag_repair.get("current_cycle"):
        cycle = dag_repair["current_cycle"]
        if cycle.get("status") == "failed":
            return "fixing"
        if cycle.get("status") == "running":
            return "running"
    active = next((g for g in groups if g.get("status") == "active"), None)
    if active:
        latest = active.get("verify_steps", [])[-1:] or []
        if latest and latest[0].get("passed") is False:
            return "fixing"
        return "running"
    if active_gate:
        return "quality-gates"
    return "planning"


def _derive_current_focus(
    groups: list[dict[str, Any]],
    dag_repair: dict[str, Any] | None,
    active_gate: str | None,
) -> str:
    active = next((g for g in groups if g.get("status") == "active"), None)
    if dag_repair and dag_repair.get("current_cycle"):
        cycle = dag_repair["current_cycle"]
        blocker = _scrub_public_text(cycle.get("final_blocker_summary"), max_len=220)
        if blocker:
            return f"Repairing implementation batch {cycle.get('group_idx')} after verifier feedback: {blocker}"
    if active:
        tasks = [
            _scrub_public_text(task.get("name") or task.get("id"), max_len=70)
            for task in active.get("tasks", [])
            if task.get("status") == "in_progress"
        ]
        if tasks:
            return f"Implementing batch {active.get('index')}: {', '.join(tasks[:3])}"
        return f"Verifying implementation batch {active.get('index')}"
    if active_gate:
        return f"Running the {active_gate} quality gate"
    return "Preparing the next workflow milestone"


def _derive_next_checkpoint(
    groups: list[dict[str, Any]],
    active_gate: str | None,
    gates: dict[str, bool],
) -> str:
    active = next((g for g in groups if g.get("status") == "active"), None)
    if active:
        return f"Checkpoint batch {active.get('index')} after final verifier approval"
    if active_gate:
        return f"Approve the {active_gate} gate"
    pending_gate = next((name for name, passed in gates.items() if not passed), None)
    if pending_gate:
        return f"Start the {pending_gate} gate"
    return "Publish final handover"


def _assemble_public_summary(
    *,
    feat: asyncpg.Record,
    artifacts: dict[str, tuple[str, str]],
    dag_info: dict[str, Any] | None,
    groups: list[dict[str, Any]],
    dag_repair: dict[str, Any] | None,
    active_gate: str | None,
    gates: dict[str, bool],
    last_activity_at: str | None,
) -> dict[str, Any]:
    generated = _public_content(_safe_public_artifact(artifacts, "public-summary"))
    completed_groups = sum(1 for group in groups if group.get("status") == "complete")
    total_groups = int(dag_info.get("total_groups") or len(groups)) if dag_info else len(groups)
    completed_tasks = sum(int(group.get("completed_count") or 0) for group in groups)
    total_tasks = int(dag_info.get("total_tasks") or 0) if dag_info else sum(int(group.get("task_count") or 0) for group in groups)
    percent_complete = round((completed_groups / total_groups) * 100) if total_groups else 0

    fallback = {
        "title": _extract_project_title(feat, artifacts),
        "tagline": "A live multi-agent build, verification, and delivery exhibit.",
        "description": _extract_public_description(artifacts),
        "phase_label": _scrub_public_text(feat["phase"], max_len=80),
        "status_label": _derive_current_focus(groups, dag_repair, active_gate),
        "progress_narrative": (
            f"{completed_groups} of {total_groups} DAG batches and "
            f"{completed_tasks} of {total_tasks} implementation tasks are checkpointed."
            if total_groups else
            "The feature is still in upstream planning."
        ),
        "current_focus": _derive_current_focus(groups, dag_repair, active_gate),
        "next_checkpoint": _derive_next_checkpoint(groups, active_gate, gates),
        "health": _derive_public_health(
            phase=feat["phase"],
            groups=groups,
            dag_repair=dag_repair,
            active_gate=active_gate,
        ),
        "percent_complete": percent_complete,
        "completed_groups": completed_groups,
        "total_groups": total_groups,
        "completed_tasks": completed_tasks,
        "total_tasks": total_tasks,
        "updated_at": last_activity_at or _ensure_iso(feat["updated_at"]),
        "source": "deterministic-fallback",
    }

    if isinstance(generated, dict):
        merged = dict(fallback)
        for key in (
            "title", "tagline", "description", "phase_label", "status_label",
            "progress_narrative", "current_focus", "next_checkpoint", "health",
        ):
            if generated.get(key):
                merged[key] = _scrub_public_text(generated[key], max_len=900)
        merged["source"] = "public-summary"
        merged["provenance"] = generated.get("provenance") or generated.get("sources") or {}
        return merged

    return fallback


def _assemble_dag_exhibit(
    *,
    artifacts: dict[str, tuple[str, str]],
    dag_info: dict[str, Any] | None,
    groups: list[dict[str, Any]],
    dag_repair: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not dag_info and not groups:
        return None
    generated = _public_content(_safe_public_artifact(artifacts, "public-dag-narrative"))
    active_group = next((group for group in groups if group.get("status") == "active"), None)
    next_groups = [
        {"index": group.get("index"), "task_count": group.get("task_count"), "status": group.get("status")}
        for group in groups
        if group.get("status") != "complete"
    ][:8]
    narrative = ""
    if isinstance(generated, dict):
        narrative = _scrub_public_text(
            generated.get("narrative") or generated.get("summary") or generated.get("description"),
            max_len=900,
        )
    return {
        "narrative": narrative or "The DAG is the execution map: implementation batches checkpoint only after verification passes.",
        "total_groups": int(dag_info.get("total_groups") or len(groups)) if dag_info else len(groups),
        "total_tasks": int(dag_info.get("total_tasks") or 0) if dag_info else sum(int(g.get("task_count") or 0) for g in groups),
        "completed_groups": sum(1 for group in groups if group.get("status") == "complete"),
        "active_group": {
            "index": active_group.get("index"),
            "task_count": active_group.get("task_count"),
            "completed_count": active_group.get("completed_count"),
            "status": active_group.get("status"),
        } if active_group else None,
        "next_groups": next_groups,
        "repair": dag_repair,
        "source": "public-dag-narrative" if isinstance(generated, dict) else "deterministic-fallback",
    }


def _agent_role_label(name: str) -> str:
    lowered = name.lower()
    if "implementer" in lowered:
        return "Implementer"
    if "root-cause" in lowered or "rca" in lowered:
        return "Root-cause analyst"
    if "triage" in lowered:
        return "Triage planner"
    if "verifier" in lowered or "verify" in lowered or "smoke" in lowered:
        return "Verifier"
    if "security" in lowered:
        return "Security reviewer"
    if "regression" in lowered:
        return "Regression tester"
    if "reviewer" in lowered:
        return "Reviewer"
    return "Agent"


def _agent_runtime_label(content: Any) -> str:
    text = _text(content).lower()
    if "codex" in text:
        return "Codex"
    if "claude" in text:
        return "Claude"
    return "Runtime unknown"


def _agent_group_idx(name: str) -> int | None:
    match = re.search(r"(?:^|[-_:])g(\d+)(?:[-_:]|$)", name)
    return int(match.group(1)) if match else None


def _agent_related_artifact_keys(
    name: str,
    *,
    artifacts: dict[str, tuple[str, str]],
    group_idx: int | None,
) -> list[str]:
    candidates: list[tuple[str, str]] = []
    normalized_name = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    group_token = f"g{group_idx}" if group_idx is not None else ""
    for key, (_value, created_at) in artifacts.items():
        key_l = key.lower()
        if group_token and re.search(rf"(?:^|:)dag|dag-", key_l) and group_token in key_l:
            candidates.append((key, created_at))
            continue
        if normalized_name and normalized_name in re.sub(r"[^a-z0-9]+", "-", key_l):
            candidates.append((key, created_at))
    candidates.sort(key=lambda item: item[1], reverse=True)
    return [key for key, _created_at in candidates[:6]]


def _agent_task_context(
    name: str,
    *,
    groups: list[dict[str, Any]],
    group_idx: int | None,
) -> tuple[str | None, str, list[str]]:
    normalized_name = name.lower()
    active_group = next(
        (group for group in groups if group_idx is not None and group.get("index") == group_idx),
        None,
    )
    candidate_tasks = active_group.get("tasks", []) if active_group else []
    best_task: dict[str, Any] | None = None
    best_score = 0
    for task in candidate_tasks:
        task_id = _text(task.get("id"))
        if task_id and task_id.lower() in normalized_name:
            best_task = task
            best_score = 999
            break
        words = {
            word for word in re.split(r"[^a-z0-9]+", _text(task.get("name")).lower())
            if len(word) >= 4
        }
        score = sum(1 for word in words if word in normalized_name)
        if score > best_score:
            best_task = task
            best_score = score

    if best_task and (best_score > 0 or len(candidate_tasks) == 1):
        task_id = _text(best_task.get("id")) or None
        files = [
            _text(item.get("path"))
            for item in best_task.get("file_scope", [])
            if isinstance(item, dict) and item.get("path")
        ]
        preview = _scrub_public_text(
            best_task.get("description")
            or best_task.get("summary")
            or best_task.get("name")
            or task_id,
            max_len=260,
        )
        return task_id, preview, files[:6]

    topic = _agent_topic_from_name(name)
    if "contradiction" in normalized_name:
        return None, f"Resolve contradiction context for {topic} before repair continues." if topic else "Resolve a spec or artifact contradiction before repair continues.", []
    if "triage" in normalized_name:
        return None, f"Group verifier findings for {topic} into repairable root-cause clusters." if topic else "Group verifier findings into repairable root-cause clusters.", []
    if "root-cause" in normalized_name or "rca" in normalized_name:
        return None, f"Analyze RCA cluster {topic} and propose a safe repair plan." if topic else "Identify the root cause and propose a safe repair plan.", []
    if "verify" in normalized_name or "verifier" in normalized_name:
        return None, f"Verify {topic} against DAG gates and product behavior." if topic else "Verify the current DAG group against task gates and product behavior.", []
    if "fix" in normalized_name or "implementer" in normalized_name:
        files: list[str] = []
        for task in candidate_tasks:
            for item in task.get("file_scope", []):
                if isinstance(item, dict) and item.get("path"):
                    files.append(_text(item["path"]))
        return None, f"Apply focused repair for {topic} from the latest RCA and verifier findings." if topic else "Apply a focused repair from the latest RCA and verifier findings.", files[:6]
    return None, f"Continue workflow step for {topic} using the latest canonical artifacts." if topic else "Continue the current workflow step using the latest canonical artifacts.", []


def _artifact_summary_preview(artifacts: dict[str, tuple[str, str]], keys: list[str]) -> str:
    for key in keys:
        raw = artifacts.get(key)
        if not raw:
            continue
        payload = _safe_dict(raw[0])
        for field in ("summary", "hypothesis", "resolution", "rationale", "final_blocker_summary"):
            if payload.get(field):
                return _scrub_public_text(payload[field], max_len=280)
        text = _text(raw[0]).strip()
        if text:
            return _scrub_public_text(text, max_len=280)
    return ""


def _agent_topic_from_name(name: str) -> str:
    cleaned = re.sub(
        r"^(?:root-cause-analyst|implementer|verifier|contradiction-resolver)-dag-g\d+(?:-r\d+)?-",
        "",
        name,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"^(?:rca|fix|lens|verify|contradiction)-", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^(?:G|group)-", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[^a-zA-Z0-9]+", " ", cleaned).strip()
    if not cleaned:
        return ""
    words = [
        word.upper() if len(word) <= 3 and word.isalpha() else word.capitalize()
        for word in cleaned.split()
    ]
    return _scrub_public_text(" ".join(words), max_len=96)


def _enrich_agent_record(
    record: dict[str, Any],
    *,
    artifacts: dict[str, tuple[str, str]],
    groups: list[dict[str, Any]],
) -> dict[str, Any]:
    name = _text(record.get("name"))
    group_idx = _agent_group_idx(name)
    task_id, prompt_preview, related_files = _agent_task_context(
        name,
        groups=groups,
        group_idx=group_idx,
    )
    related_artifact_keys = _agent_related_artifact_keys(
        name,
        artifacts=artifacts,
        group_idx=group_idx,
    )
    output_preview = _scrub_public_text(record.get("summary"), max_len=280)
    if not output_preview or output_preview.lower() in {"codex", "claude", "claude_pool"}:
        output_preview = _artifact_summary_preview(artifacts, related_artifact_keys)
    if not output_preview and record.get("status") == "running":
        output_preview = "Running now; latest output will appear after the agent reports back."

    started = _parse_time(record.get("started_at"))
    ended = _parse_time(record.get("ended_at")) or datetime.now(timezone.utc)
    duration_seconds = _seconds_between(started, ended) if started else None

    return {
        **record,
        "task_id": task_id,
        "group_idx": group_idx,
        "duration_seconds": duration_seconds,
        "prompt_preview": prompt_preview,
        "output_preview": output_preview,
        "related_artifact_keys": related_artifact_keys,
        "related_files": related_files,
    }


def _assemble_agent_activity(
    events: list[asyncpg.Record],
    *,
    artifacts: dict[str, tuple[str, str]] | None = None,
    groups: list[dict[str, Any]] | None = None,
    timeline: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    artifacts = artifacts or {}
    groups = groups or []
    active: dict[str, dict[str, Any]] = {}
    recent_completed: list[dict[str, Any]] = []
    for event in sorted(events, key=lambda item: item["created_at"]):
        event_type = _text(event["event_type"])
        source = _text(event["source"])
        created_at = _ensure_iso(event["created_at"])
        if event_type == "agent_start":
            active[source] = {
                "name": source,
                "role": _agent_role_label(source),
                "runtime": _agent_runtime_label(event["content"]),
                "started_at": created_at,
                "status": "running",
            }
        elif event_type in {"agent_done", "agent_error", "agent_failed"}:
            started = active.pop(source, None)
            recent_completed.append({
                "name": source,
                "role": _agent_role_label(source),
                "runtime": _agent_runtime_label(event["content"]),
                "started_at": started.get("started_at") if started else None,
                "ended_at": created_at,
                "status": "failed" if event_type != "agent_done" else "complete",
                "summary": _scrub_public_text(event["content"], max_len=220),
            })
    active_records = [
        _enrich_agent_record(record, artifacts=artifacts, groups=groups)
        for record in active.values()
    ]
    recent_records = [
        _enrich_agent_record(record, artifacts=artifacts, groups=groups)
        for record in reversed(recent_completed)
    ]
    return {
        "active_agents": sorted(
            active_records,
            key=lambda item: item.get("started_at") or "",
            reverse=True,
        ),
        "recent_agents": recent_records[:24],
    }


def _assemble_agent_exhibit(
    *,
    artifacts: dict[str, tuple[str, str]],
    agent_activity: dict[str, Any],
    dag_repair: dict[str, Any] | None,
) -> dict[str, Any]:
    generated_rounds = [
        {
            "key": key,
            "created_at": created_at,
            "summary": _scrub_public_text((_public_content(_safe_public_artifact(artifacts, key)) or {}).get("summary"), max_len=420)
            if isinstance(_public_content(_safe_public_artifact(artifacts, key)), dict)
            else "",
        }
        for key, (_value, created_at) in artifacts.items()
        if key.startswith("public-agent-round-summary:")
    ]
    generated_rounds.sort(key=lambda item: item["created_at"], reverse=True)
    current_cycle = dag_repair.get("current_cycle") if dag_repair else None
    return {
        **agent_activity,
        "round_summaries": generated_rounds[:8],
        "current_repair_cycle": current_cycle,
        "headline": (
            f"{len(agent_activity['active_agents'])} agent(s) active"
            if agent_activity["active_agents"]
            else "No active agents at this instant"
        ),
    }


def _assemble_artifact_exhibit(artifacts: dict[str, tuple[str, str]]) -> dict[str, Any]:
    generated = _public_content(_safe_public_artifact(artifacts, "public-artifact-gallery"))
    generated_cards = []
    if isinstance(generated, dict) and isinstance(generated.get("cards"), list):
        for card in generated["cards"]:
            if isinstance(card, dict) and _public_payload_is_safe(card):
                generated_cards.append(card)

    deterministic_cards = []
    for key, (_value, created_at) in sorted(artifacts.items()):
        if not _artifact_is_exhibit_source(key):
            continue
        deterministic_cards.append({
            "key": key,
            "title": _artifact_label(key),
            "family": _artifact_family(key),
            "summary": _artifact_description(key),
            "created_at": created_at,
            "status": "available",
            "public_safe": True,
            "source": "deterministic-inventory",
        })

    cards_by_key: dict[str, dict[str, Any]] = {
        _text(card.get("key")): card for card in deterministic_cards
    }
    for card in generated_cards:
        key = _text(card.get("key"))
        if not key:
            continue
        base = cards_by_key.get(key, {})
        cards_by_key[key] = {
            **base,
            **{
                "key": key,
                "title": _scrub_public_text(card.get("title") or base.get("title") or key, max_len=120),
                "family": _scrub_public_text(card.get("family") or base.get("family") or _artifact_family(key), max_len=60),
                "summary": _scrub_public_text(card.get("summary") or base.get("summary") or "", max_len=460),
                "created_at": base.get("created_at") or card.get("created_at"),
                "status": _scrub_public_text(card.get("status") or base.get("status") or "available", max_len=60),
                "public_safe": True,
                "source": "public-artifact-gallery",
                "provenance": card.get("provenance") or {},
            },
        }

    family_order = {
        "public narrative": 0,
        "product": 1,
        "design": 2,
        "planning": 3,
        "verification": 4,
        "decision": 5,
        "audit": 6,
        "delivery": 7,
    }
    cards = sorted(
        cards_by_key.values(),
        key=lambda card: (
            family_order.get(_text(card.get("family")), 99),
            _text(card.get("title")),
        ),
    )
    return {
        "cards": cards[:120],
        "total_count": len(cards),
        "generated": bool(generated_cards),
    }


def _assemble_workstream_exhibit(
    *,
    artifacts: dict[str, tuple[str, str]],
    workstreams: list[dict[str, Any]],
    groups: list[dict[str, Any]],
) -> dict[str, Any]:
    generated = _public_content(_safe_public_artifact(artifacts, "public-workstream-summary"))
    generated_by_id: dict[str, dict[str, Any]] = {}
    if isinstance(generated, dict):
        for item in generated.get("workstreams", []) if isinstance(generated.get("workstreams"), list) else []:
            if isinstance(item, dict) and item.get("id") and _public_payload_is_safe(item):
                generated_by_id[_text(item["id"])] = item
    cards = []
    active_subfeatures = {
        task.get("subfeature_id")
        for group in groups
        if group.get("status") == "active"
        for task in group.get("tasks", [])
    }
    for ws in workstreams:
        total = int(ws.get("total_tasks") or 0)
        completed = int(ws.get("completed_tasks") or 0)
        generated_card = generated_by_id.get(_text(ws.get("id")), {})
        active = any(slug in active_subfeatures for slug in ws.get("subfeature_slugs", []))
        cards.append({
            "id": ws.get("id"),
            "name": ws.get("name"),
            "summary": _scrub_public_text(
                generated_card.get("summary")
                or f"{len(ws.get('subfeature_slugs', []))} subfeature(s) contributing to the delivery plan.",
                max_len=360,
            ),
            "status": "active" if active else ("complete" if total and completed >= total else "pending"),
            "completed_tasks": completed,
            "total_tasks": total,
            "subfeature_slugs": ws.get("subfeature_slugs", []),
            "depends_on": ws.get("depends_on", []),
        })
    return {
        "summary": _scrub_public_text(
            generated.get("summary") if isinstance(generated, dict) else "",
            max_len=540,
        ) if isinstance(generated, dict) else "Workstreams organize the DAG into coherent delivery lanes.",
        "workstreams": cards,
        "source": "public-workstream-summary" if isinstance(generated, dict) else "deterministic-fallback",
    }


def _assemble_public_milestones(
    *,
    artifacts: dict[str, tuple[str, str]],
    timeline: list[dict[str, Any]],
    groups: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    generated = _public_content(_safe_public_artifact(artifacts, "public-milestone-feed"))
    if isinstance(generated, dict) and isinstance(generated.get("milestones"), list):
        milestones = [
            {
                "title": _scrub_public_text(item.get("title"), max_len=120),
                "summary": _scrub_public_text(item.get("summary"), max_len=420),
                "kind": _scrub_public_text(item.get("kind") or "milestone", max_len=40),
                "created_at": _scrub_public_text(item.get("created_at"), max_len=80),
                "source": "public-milestone-feed",
            }
            for item in generated["milestones"]
            if isinstance(item, dict) and _public_payload_is_safe(item)
        ]
        if milestones:
            return milestones[:20]

    milestones: list[dict[str, Any]] = []
    for group in groups:
        if group.get("status") != "complete":
            continue
        key = f"dag-group:{group.get('index')}"
        created_at = artifacts.get(key, ("", ""))[1]
        if created_at:
            milestones.append({
                "title": f"Batch {group.get('index')} checkpointed",
                "summary": f"{group.get('task_count')} implementation task(s) passed verification and were checkpointed.",
                "kind": "checkpoint",
                "created_at": created_at,
                "source": key,
            })
    for entry in timeline[:8]:
        summary = _scrub_public_text(entry.get("summary"), max_len=260)
        if not summary:
            continue
        milestones.append({
            "title": _artifact_label(entry.get("key", "")),
            "summary": summary,
            "kind": _scrub_public_text(entry.get("type") or "workflow", max_len=40),
            "created_at": entry.get("created_at"),
            "source": entry.get("key"),
        })
    milestones.sort(key=lambda item: _text(item.get("created_at")), reverse=True)
    return milestones[:20]


def _assemble_current_work(
    *,
    groups: list[dict[str, Any]],
    agent_activity: dict[str, Any],
    timeline: list[dict[str, Any]],
    active_gate: str | None,
    gates: dict[str, bool],
) -> dict[str, Any]:
    active_group = next((group for group in groups if group.get("status") == "active"), None)
    active_tasks = []
    if active_group:
        for task in active_group.get("tasks", []):
            if task.get("status") == "complete":
                continue
            active_tasks.append({
                "id": task.get("id"),
                "name": task.get("name") or task.get("id"),
                "status": task.get("status"),
                "summary": _scrub_public_text(task.get("summary") or task.get("description"), max_len=260),
                "repo_path": task.get("repo_path"),
                "subfeature_id": task.get("subfeature_id"),
                "acceptance_criteria": task.get("acceptance_criteria", [])[:8],
                "file_scope": task.get("file_scope", [])[:8],
            })

    recent_outcomes = []
    for entry in sorted(timeline, key=lambda item: _text(item.get("created_at")), reverse=True):
        if entry.get("type") not in {
            "verify", "preflight", "expanded_verify", "triage", "rca", "dispatch",
            "fix", "reverify", "contradiction", "sanitize",
        }:
            continue
        recent_outcomes.append({
            "key": entry.get("key"),
            "type": entry.get("type"),
            "passed": entry.get("passed"),
            "summary": _scrub_public_text(entry.get("summary"), max_len=260),
            "created_at": entry.get("created_at"),
        })
        if len(recent_outcomes) >= 8:
            break

    return {
        "active_group": {
            "index": active_group.get("index"),
            "task_count": active_group.get("task_count"),
            "completed_count": active_group.get("completed_count"),
            "status": active_group.get("status"),
        } if active_group else None,
        "active_tasks": active_tasks,
        "active_agents": agent_activity.get("active_agents", []),
        "recent_outcomes": recent_outcomes,
        "next_checkpoint": _derive_next_checkpoint(groups, active_gate, gates),
    }


def _assemble_public_exhibit(
    *,
    feat: asyncpg.Record,
    artifacts: dict[str, tuple[str, str]],
    timeline: list[dict[str, Any]],
    events: list[dict[str, Any]],
    agent_activity: dict[str, Any],
    dag_info: dict[str, Any] | None,
    groups: list[dict[str, Any]],
    workstreams: list[dict[str, Any]],
    dag_repair: dict[str, Any] | None,
    active_gate: str | None,
    gates: dict[str, bool],
    last_activity_at: str | None,
    artifact_catalog: dict[str, tuple[str, str]] | None = None,
) -> dict[str, Any]:
    catalog = artifact_catalog or artifacts
    artifact_exhibit = _assemble_artifact_exhibit(catalog)
    return {
        "public_summary": _assemble_public_summary(
            feat=feat,
            artifacts=artifacts,
            dag_info=dag_info,
            groups=groups,
            dag_repair=dag_repair,
            active_gate=active_gate,
            gates=gates,
            last_activity_at=last_activity_at,
        ),
        "dag_exhibit": _assemble_dag_exhibit(
            artifacts=artifacts,
            dag_info=dag_info,
            groups=groups,
            dag_repair=dag_repair,
        ),
        "agent_exhibit": _assemble_agent_exhibit(
            artifacts=artifacts,
            agent_activity=agent_activity,
            dag_repair=dag_repair,
        ),
        "current_work": _assemble_current_work(
            groups=groups,
            agent_activity=agent_activity,
            timeline=timeline,
            active_gate=active_gate,
            gates=gates,
        ),
        "artifact_exhibit": artifact_exhibit,
        "workstream_exhibit": _assemble_workstream_exhibit(
            artifacts=artifacts,
            workstreams=workstreams,
            groups=groups,
        ),
        "milestone_feed": _assemble_public_milestones(
            artifacts=artifacts,
            timeline=timeline,
            groups=groups,
        ),
        "operations": {
            "dag_repair": dag_repair,
            "gates": gates,
            "active_gate": active_gate,
            "timeline_count": len(timeline),
            "event_count": len(events),
            "artifact_count": artifact_exhibit["total_count"],
        },
    }


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
    proof_capture_retry_lane_ids = _string_list(queue.get("proof_capture_retry_lane_ids"))
    strategy_pending_cluster_ids = _string_list(queue.get("strategy_pending_cluster_ids"))
    if stalled_lane_ids:
        return f"Recovering stalled lanes: {', '.join(stalled_lane_ids[:3])}"
    if recovering_lane_ids:
        return f"Recovering lanes: {', '.join(recovering_lane_ids[:3])}"
    if proof_capture_retry_lane_ids:
        return f"Recapturing promotion proof for lanes: {', '.join(proof_capture_retry_lane_ids[:3])}"
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

    if (
        _string_list(queue.get("stalled_lane_ids"))
        or _string_list(queue.get("recovering_lane_ids"))
        or _string_list(queue.get("proof_capture_retry_lane_ids"))
        or _string_list(queue.get("strategy_pending_cluster_ids"))
    ):
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
    queue.setdefault("proof_capture_retry_lane_ids", _string_list(queue.get("proof_capture_retry_lane_ids")))
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

    if key.startswith("dag-repair-preflight:"):
        payload = _safe_dict(value)
        passed = bool(payload.get("approved", False))
        count = len(_safe_list(payload.get("concerns")))
        summary = "Preflight passed" if passed else f"Preflight blocked on {count} issue(s)"
        return {"key": key, "type": "preflight", "passed": passed, "summary": summary, "created_at": ts}

    if key.startswith("dag-repair-lens:"):
        payload = _safe_dict(value)
        passed = payload.get("approved")
        parts = key.split(":")
        lens = parts[2] if len(parts) > 2 else "lens"
        summary = _text(payload.get("summary") or f"{lens} lens completed")
        return {
            "key": key,
            "type": "lens",
            "passed": passed if isinstance(passed, bool) else None,
            "summary": summary,
            "created_at": ts,
        }

    if key.startswith("dag-repair-expanded-verify:"):
        payload = _safe_dict(value)
        passed = payload.get("approved")
        summary = _text(payload.get("summary") or "Expanded verify merged")
        return {
            "key": key,
            "type": "expanded-verify",
            "passed": passed if isinstance(passed, bool) else None,
            "summary": summary,
            "created_at": ts,
        }

    if key.startswith("dag-repair-triage:"):
        payload = _safe_dict(value)
        groups = _safe_list(payload.get("groups"))
        summary = f"{len(groups)} DAG repair group(s) triaged" if groups else "DAG repair triage"
        return {"key": key, "type": "triage", "passed": None, "summary": summary, "created_at": ts}

    if key.startswith("dag-repair-rca:"):
        payload = _safe_dict(value)
        summary = _text(payload.get("hypothesis") or payload.get("summary") or "DAG repair RCA")
        return {"key": key, "type": "rca", "passed": None, "summary": summary, "created_at": ts}

    if key.startswith("dag-repair-dispatch:"):
        payload = _safe_dict(value)
        scheduled = sum(
            len(item.get("group_ids", []))
            for item in _safe_list(payload.get("schedule"))
            if isinstance(item, dict)
        )
        summary = (
            f"{scheduled} fix group(s), "
            f"{payload.get('contradiction_group_count', 0)} contradiction(s)"
        )
        return {"key": key, "type": "dispatch", "passed": None, "summary": summary, "created_at": ts}

    if key.startswith("dag-repair-reverify:"):
        payload = _safe_dict(value)
        passed = payload.get("approved")
        summary = _text(payload.get("summary") or "Focused reverify")
        return {
            "key": key,
            "type": "reverify",
            "passed": passed if isinstance(passed, bool) else None,
            "summary": summary,
            "created_at": ts,
        }

    if key.startswith("dag-repair-result-sanitize:"):
        payload = _safe_dict(value)
        summary = (
            f"Sanitized paths: ignored={payload.get('ignored_path_count', 0)}, "
            f"rewritten={payload.get('rewritten_path_count', 0)}, "
            f"invalid={payload.get('invalid_product_path_count', 0)}"
        )
        return {"key": key, "type": "sanitize", "passed": None, "summary": summary, "created_at": ts}

    if key.startswith("dag-repair-fix-error:"):
        payload = _safe_dict(value)
        summary = _text(payload.get("summary") or "DAG repair fix task failed")
        return {"key": key, "type": "fix-error", "passed": False, "summary": summary, "created_at": ts}

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

    if key.startswith("contradiction-rejected:"):
        payload = _safe_dict(value)
        reasons = ", ".join(_text(item) for item in _safe_list(payload.get("rejection_reasons")))
        summary = reasons or "Contradiction resolver rejected output"
        return {"key": key, "type": "decision", "passed": False, "summary": summary, "created_at": ts}

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
    parser.add_argument(
        "--bridge-runtime-policy",
        default=None,
        choices=list(SUPPORTED_RUNTIME_POLICIES),
        help="Runtime routing policy for workflow roles.",
    )
    parser.add_argument(
        "--bridge-claude-pool-codex-review",
        action="store_true",
        help=(
            "Use claude_pool as the primary runtime and Codex as the "
            "secondary review/verification runtime."
        ),
    )
    parser.add_argument("--bridge-claude-only", action="store_true")
    parser.add_argument("--bridge-budget", action="store_true")
    parser.add_argument("--bridge-autonomous-remainder", action="store_true")
    args = parser.parse_args()

    dashboard_config["port"] = args.port

    if args.bridge_channel:
        bridge_agent_runtime = args.bridge_agent_runtime
        bridge_runtime_policy = args.bridge_runtime_policy
        if args.bridge_claude_pool_codex_review:
            if bridge_agent_runtime and bridge_agent_runtime not in {
                "claude_pool", "claude-pool", "pool",
            }:
                parser.error(
                    "--bridge-claude-pool-codex-review cannot be combined "
                    "with a non-Claude-pool --bridge-agent-runtime"
                )
            bridge_agent_runtime = "claude_pool"
            bridge_runtime_policy = PRIMARY_IMPL_SECONDARY_REVIEW_POLICY
        if bridge_runtime_policy == DEFAULT_RUNTIME_POLICY:
            bridge_runtime_policy = None

        bridge_config.update({
            "channel": args.bridge_channel,
            "workspace": args.bridge_workspace,
            "mode": args.bridge_mode,
            "agent_runtime": bridge_agent_runtime,
            "runtime_policy": bridge_runtime_policy,
            "claude_only": args.bridge_claude_only,
            "budget": args.bridge_budget,
            "autonomous_remainder": args.bridge_autonomous_remainder,
        })

    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="info")
