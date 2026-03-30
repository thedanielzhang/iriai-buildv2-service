"""iriai-build-v2 monitoring dashboard.

Usage:
    python dashboard.py [--port 8080]
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone

import asyncpg
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://danielzhang@localhost:5431/iriai_build_v2",
)

app = FastAPI(title="iriai-build-v2 Dashboard")
pool: asyncpg.Pool | None = None


@app.on_event("startup")
async def _startup():
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=5)


@app.on_event("shutdown")
async def _shutdown():
    if pool:
        await pool.close()


# ── API ─────────────────────────────────────────────────────────────────────


@app.get("/api/feature/{feature_id}")
async def get_feature(feature_id: str):
    """Return assembled dashboard state for one feature."""
    assert pool
    async with pool.acquire() as conn:
        # 1. Feature metadata
        feat = await conn.fetchrow(
            "SELECT id, name, phase, workflow_name, updated_at "
            "FROM features WHERE id = $1",
            feature_id,
        )
        if not feat:
            raise HTTPException(404, f"Feature {feature_id!r} not found")

        # 2. Latest artifacts (append-only: latest = highest id per key)
        rows = await conn.fetch(
            "SELECT DISTINCT ON (key) key, value, created_at "
            "FROM artifacts WHERE feature_id = $1 "
            "AND (key LIKE 'dag%' OR key LIKE 'bug-%' "
            "     OR key = 'implementation' OR key = 'handover') "
            "ORDER BY key, id DESC",
            feature_id,
        )

        # 3. All verify/bug artifacts with full history (for timeline)
        timeline_rows = await conn.fetch(
            "SELECT key, value, created_at FROM artifacts "
            "WHERE feature_id = $1 "
            "AND (key LIKE 'dag-verify:%' OR key LIKE 'bug-%') "
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
                task_name = ""
                task_summary = ""
                task_status = "pending"
                if tid in tasks_by_id:
                    task_name = tasks_by_id[tid].get("name", "")
                if tkey in artifacts:
                    task_status = "complete"
                    completed_count += 1
                    try:
                        result = json.loads(artifacts[tkey][0])
                        task_summary = result.get("summary", "")[:200]
                    except (json.JSONDecodeError, KeyError):
                        pass
                elif status == "active":
                    task_status = "in_progress"
                task_details.append({
                    "id": tid,
                    "name": task_name,
                    "status": task_status,
                    "summary": task_summary,
                })

            # Collect verify artifacts for this group
            verify_steps = []
            prefix = f"dag-verify:g{i}:"
            for r in timeline_rows:
                if r["key"].startswith(prefix):
                    approved = False
                    summary = ""
                    try:
                        v = json.loads(r["value"])
                        approved = v.get("approved", False)
                        summary = v.get("summary", "")[:200]
                    except (json.JSONDecodeError, KeyError):
                        summary = r["value"][:200] if r["value"] else ""
                    step_type = "verify"
                    key_suffix = r["key"][len(prefix):]
                    if key_suffix.startswith("retry"):
                        step_type = "re-verify"
                    verify_steps.append({
                        "key": r["key"],
                        "type": step_type,
                        "passed": approved,
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

    # Gates
    gate_names = ["code-review", "security", "qa", "integration", "verifier"]
    gates = {}
    for g in gate_names:
        gates[g] = f"dag-gate:{g}" in artifacts

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

    return {
        "id": feat["id"],
        "name": feat["name"],
        "phase": feat["phase"],
        "workflow_name": feat["workflow_name"],
        "updated_at": feat["updated_at"].isoformat(),
        "dag": dag_info,
        "groups": groups,
        "gates": gates,
        "timeline": timeline,
        "events": event_list,
    }


def _parse_timeline_entry(key: str, value: str, created_at) -> dict | None:
    """Parse a timeline artifact into a display entry."""
    ts = created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at)

    if key.startswith("dag-verify:"):
        passed = False
        summary = ""
        try:
            v = json.loads(value)
            passed = v.get("approved", False)
            summary = v.get("summary", "")[:300]
        except (json.JSONDecodeError, KeyError):
            summary = value[:300] if value else ""
        return {"key": key, "type": "verify", "passed": passed, "summary": summary, "created_at": ts}

    if key.startswith("bug-rca:"):
        summary = ""
        try:
            v = json.loads(value)
            summary = v.get("hypothesis", "")[:300]
        except (json.JSONDecodeError, KeyError):
            summary = value[:300] if value else ""
        return {"key": key, "type": "rca", "passed": None, "summary": summary, "created_at": ts}

    if key.startswith("bug-reverify:"):
        passed = False
        summary = ""
        try:
            v = json.loads(value)
            passed = v.get("approved", False)
            summary = v.get("summary", "")[:300]
        except (json.JSONDecodeError, KeyError):
            summary = value[:300] if value else ""
        return {"key": key, "type": "reverify", "passed": passed, "summary": summary, "created_at": ts}

    if key.startswith("bug-regression:"):
        passed = False
        summary = ""
        try:
            v = json.loads(value)
            passed = v.get("approved", False)
            summary = v.get("summary", "")[:300]
        except (json.JSONDecodeError, KeyError):
            summary = value[:300] if value else ""
        return {"key": key, "type": "regression", "passed": passed, "summary": summary, "created_at": ts}

    if key.startswith("bug-triage:"):
        summary = ""
        try:
            v = json.loads(value)
            groups = v.get("groups", [])
            summary = f"{len(groups)} bug groups identified"
        except (json.JSONDecodeError, KeyError):
            summary = value[:300] if value else ""
        return {"key": key, "type": "triage", "passed": None, "summary": summary, "created_at": ts}

    if key == "bug-fix-attempts":
        summary = ""
        try:
            # This is a text blob of multiple JSON objects
            count = value.count('"bug_id"')
            summary = f"{count} fix attempt(s) recorded"
        except Exception:
            summary = "fix attempts"
        return {"key": key, "type": "fix-attempts", "passed": None, "summary": summary, "created_at": ts}

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


# ── SPA ─────────────────────────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML_CONTENT


HTML_CONTENT = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>iriai build monitor</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root {
  --bg-0: #0a0c10;
  --bg-1: #0f1218;
  --bg-2: #151a23;
  --bg-3: #1c2231;
  --border: #252d3d;
  --border-bright: #334155;
  --text-0: #e2e8f0;
  --text-1: #94a3b8;
  --text-2: #64748b;
  --green: #22c55e;
  --green-dim: #166534;
  --amber: #f59e0b;
  --amber-dim: #92400e;
  --red: #ef4444;
  --red-dim: #991b1b;
  --blue: #3b82f6;
  --blue-dim: #1e3a5f;
  --cyan: #06b6d4;
  --purple: #a78bfa;
  --mono: 'IBM Plex Mono', monospace;
  --sans: 'DM Sans', system-ui, sans-serif;
}

* { margin: 0; padding: 0; box-sizing: border-box; }

body {
  background: var(--bg-0);
  color: var(--text-0);
  font-family: var(--sans);
  font-size: 14px;
  line-height: 1.5;
  min-height: 100vh;
}

/* ── Scanline overlay for atmosphere ─────────────────── */
body::after {
  content: '';
  position: fixed;
  inset: 0;
  pointer-events: none;
  z-index: 9999;
  background: repeating-linear-gradient(
    0deg,
    transparent,
    transparent 2px,
    rgba(0,0,0,0.03) 2px,
    rgba(0,0,0,0.03) 4px
  );
}

/* ── Top bar ─────────────────────────────────────────── */
.topbar {
  position: sticky;
  top: 0;
  z-index: 100;
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 0 24px;
  height: 52px;
  background: var(--bg-1);
  border-bottom: 1px solid var(--border);
  backdrop-filter: blur(12px);
}

.topbar-brand {
  font-family: var(--mono);
  font-weight: 600;
  font-size: 13px;
  letter-spacing: 0.05em;
  color: var(--cyan);
  text-transform: uppercase;
  white-space: nowrap;
  cursor: pointer;
}

.topbar-brand:hover { color: var(--text-0); }

.topbar-tabs {
  display: flex;
  gap: 2px;
  flex: 1;
  overflow-x: auto;
  scrollbar-width: none;
}
.topbar-tabs::-webkit-scrollbar { display: none; }

.tab {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 14px;
  font-family: var(--mono);
  font-size: 12px;
  color: var(--text-2);
  background: transparent;
  border: 1px solid transparent;
  border-radius: 6px;
  cursor: pointer;
  white-space: nowrap;
  transition: all 0.15s;
}
.tab:hover { color: var(--text-1); background: var(--bg-2); }
.tab.active {
  color: var(--text-0);
  background: var(--bg-2);
  border-color: var(--border);
}
.tab .phase-dot {
  width: 7px; height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
}
.tab .close-tab {
  opacity: 0;
  font-size: 14px;
  line-height: 1;
  color: var(--text-2);
  cursor: pointer;
  transition: opacity 0.15s;
}
.tab:hover .close-tab, .tab.active .close-tab { opacity: 1; }
.tab .close-tab:hover { color: var(--red); }

.add-feature-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 30px; height: 30px;
  border-radius: 6px;
  border: 1px dashed var(--border);
  color: var(--text-2);
  font-size: 16px;
  cursor: pointer;
  transition: all 0.15s;
  flex-shrink: 0;
}
.add-feature-btn:hover { border-color: var(--cyan); color: var(--cyan); }

/* ── Add feature modal ───────────────────────────────── */
.modal-overlay {
  display: none;
  position: fixed;
  inset: 0;
  z-index: 200;
  background: rgba(0,0,0,0.6);
  backdrop-filter: blur(4px);
  align-items: flex-start;
  justify-content: center;
  padding-top: 120px;
}
.modal-overlay.open { display: flex; }

.modal {
  background: var(--bg-2);
  border: 1px solid var(--border);
  border-radius: 12px;
  width: 420px;
  max-width: 90vw;
  overflow: hidden;
}
.modal input {
  width: 100%;
  padding: 16px 20px;
  background: transparent;
  border: none;
  border-bottom: 1px solid var(--border);
  color: var(--text-0);
  font-family: var(--mono);
  font-size: 15px;
  outline: none;
}
.modal input::placeholder { color: var(--text-2); }
.modal-results {
  max-height: 300px;
  overflow-y: auto;
}
.modal-result {
  padding: 12px 20px;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 12px;
  transition: background 0.1s;
}
.modal-result:hover { background: var(--bg-3); }
.modal-result .mr-id {
  font-family: var(--mono);
  font-size: 12px;
  color: var(--cyan);
  background: var(--bg-1);
  padding: 2px 8px;
  border-radius: 4px;
}
.modal-result .mr-name {
  font-size: 13px;
  color: var(--text-1);
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ── Main content ────────────────────────────────────── */
.main {
  padding: 24px;
  max-width: 1400px;
  margin: 0 auto;
}

/* ── Overview grid ───────────────────────────────────── */
.overview-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 16px;
}

.feature-card {
  background: var(--bg-1);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 20px;
  cursor: pointer;
  transition: all 0.2s;
  position: relative;
  overflow: hidden;
}
.feature-card:hover {
  border-color: var(--border-bright);
  transform: translateY(-2px);
  box-shadow: 0 8px 24px rgba(0,0,0,0.3);
}
.feature-card::before {
  content: '';
  position: absolute;
  top: 0; left: 0; right: 0;
  height: 3px;
}
.feature-card[data-phase="implementation"]::before { background: var(--amber); }
.feature-card[data-phase="complete"]::before { background: var(--green); }
.feature-card[data-phase="failed"]::before { background: var(--red); }
.feature-card:not([data-phase="implementation"]):not([data-phase="complete"]):not([data-phase="failed"])::before {
  background: var(--blue);
}

.fc-header { display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 12px; }
.fc-name { font-weight: 600; font-size: 15px; }
.fc-id { font-family: var(--mono); font-size: 11px; color: var(--text-2); margin-top: 2px; }

.phase-badge {
  font-family: var(--mono);
  font-size: 10px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  padding: 3px 8px;
  border-radius: 4px;
  white-space: nowrap;
}
.phase-badge[data-phase="implementation"] { color: var(--amber); background: var(--amber-dim); }
.phase-badge[data-phase="complete"] { color: var(--green); background: var(--green-dim); }
.phase-badge[data-phase="failed"] { color: var(--red); background: var(--red-dim); }
.phase-badge:not([data-phase="implementation"]):not([data-phase="complete"]):not([data-phase="failed"]) {
  color: var(--blue); background: var(--blue-dim);
}

.fc-progress { margin-bottom: 8px; }
.fc-progress-bar {
  height: 4px;
  background: var(--bg-3);
  border-radius: 2px;
  overflow: hidden;
}
.fc-progress-fill {
  height: 100%;
  border-radius: 2px;
  background: var(--green);
  transition: width 0.5s ease;
}
.fc-progress-text {
  font-family: var(--mono);
  font-size: 11px;
  color: var(--text-2);
  margin-top: 4px;
}

.fc-time {
  font-family: var(--mono);
  font-size: 11px;
  color: var(--text-2);
}

/* ── Empty state ─────────────────────────────────────── */
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 80px 20px;
  text-align: center;
}
.empty-state .es-icon {
  font-size: 48px;
  margin-bottom: 16px;
  opacity: 0.3;
}
.empty-state .es-title {
  font-size: 18px;
  font-weight: 600;
  margin-bottom: 8px;
  color: var(--text-1);
}
.empty-state .es-sub {
  font-size: 13px;
  color: var(--text-2);
  max-width: 360px;
}
.empty-state .es-btn {
  margin-top: 20px;
  padding: 10px 24px;
  background: var(--bg-2);
  border: 1px solid var(--border);
  border-radius: 8px;
  color: var(--cyan);
  font-family: var(--mono);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s;
}
.empty-state .es-btn:hover { border-color: var(--cyan); }

/* ── Workstream view ─────────────────────────────────── */
.ws-header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 24px;
}
.ws-back {
  width: 32px; height: 32px;
  display: flex; align-items: center; justify-content: center;
  border-radius: 6px;
  border: 1px solid var(--border);
  color: var(--text-2);
  cursor: pointer;
  font-size: 16px;
  transition: all 0.15s;
  flex-shrink: 0;
}
.ws-back:hover { border-color: var(--text-1); color: var(--text-0); }
.ws-title { font-size: 20px; font-weight: 700; flex: 1; }
.ws-meta {
  font-family: var(--mono);
  font-size: 11px;
  color: var(--text-2);
}

/* ── DAG flow ────────────────────────────────────────── */
.section { margin-bottom: 24px; }
.section-title {
  font-family: var(--mono);
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--text-2);
  margin-bottom: 12px;
}

.dag-flow {
  background: var(--bg-1);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 24px;
  overflow-x: auto;
}

.dag-main {
  display: flex;
  align-items: center;
  gap: 0;
  min-width: fit-content;
  padding-bottom: 8px;
}

.dag-node {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  position: relative;
}
.dag-circle {
  width: 44px; height: 44px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-family: var(--mono);
  font-size: 11px;
  font-weight: 600;
  border: 2px solid var(--border);
  color: var(--text-2);
  background: var(--bg-2);
  transition: all 0.3s;
  position: relative;
  z-index: 2;
}
.dag-circle.complete {
  border-color: var(--green);
  color: var(--green);
  background: var(--green-dim);
}
.dag-circle.active {
  border-color: var(--amber);
  color: var(--amber);
  background: var(--amber-dim);
  animation: pulse-glow 2s ease-in-out infinite;
}
@keyframes pulse-glow {
  0%, 100% { box-shadow: 0 0 0 0 rgba(245,158,11,0.3); }
  50% { box-shadow: 0 0 12px 4px rgba(245,158,11,0.15); }
}
.dag-label {
  font-family: var(--mono);
  font-size: 10px;
  color: var(--text-2);
  white-space: nowrap;
}
.dag-edge {
  width: 32px;
  height: 2px;
  background: var(--border);
  flex-shrink: 0;
}
.dag-edge.complete { background: var(--green-dim); }

/* ── Fix loop branch ─────────────────────────────────── */
.fix-branch {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px dashed var(--border);
}
.fix-branch-title {
  font-family: var(--mono);
  font-size: 10px;
  color: var(--amber);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  margin-bottom: 10px;
  display: flex;
  align-items: center;
  gap: 6px;
}
.fix-branch-flow {
  display: flex;
  align-items: center;
  gap: 0;
  overflow-x: auto;
  padding-bottom: 4px;
}
.fix-step {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 3px;
}
.fix-pill {
  font-family: var(--mono);
  font-size: 10px;
  padding: 4px 10px;
  border-radius: 12px;
  white-space: nowrap;
  border: 1px solid;
}
.fix-pill.pass { color: var(--green); border-color: var(--green-dim); background: rgba(34,197,94,0.08); }
.fix-pill.fail { color: var(--red); border-color: var(--red-dim); background: rgba(239,68,68,0.08); }
.fix-pill.neutral { color: var(--purple); border-color: #4c1d95; background: rgba(167,139,250,0.08); }
.fix-arrow {
  color: var(--text-2);
  font-size: 14px;
  padding: 0 4px;
  flex-shrink: 0;
}

/* ── Task list ───────────────────────────────────────── */
.task-list {
  background: var(--bg-1);
  border: 1px solid var(--border);
  border-radius: 10px;
  overflow: hidden;
}
.task-item {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--border);
  font-size: 13px;
}
.task-item:last-child { border-bottom: none; }
.task-icon { width: 18px; text-align: center; flex-shrink: 0; padding-top: 1px; }
.task-icon.complete { color: var(--green); }
.task-icon.in_progress { color: var(--amber); }
.task-icon.pending { color: var(--text-2); }
.task-id {
  font-family: var(--mono);
  font-size: 11px;
  color: var(--cyan);
  background: var(--bg-0);
  padding: 1px 6px;
  border-radius: 3px;
  flex-shrink: 0;
}
.task-name { color: var(--text-1); flex: 1; }
.task-summary {
  font-size: 12px;
  color: var(--text-2);
  margin-top: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 600px;
}

/* ── Gates ────────────────────────────────────────────── */
.gates-row {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.gate-pill {
  font-family: var(--mono);
  font-size: 11px;
  padding: 6px 14px;
  border-radius: 20px;
  border: 1px solid;
  white-space: nowrap;
}
.gate-pill.passed { color: var(--green); border-color: var(--green-dim); background: rgba(34,197,94,0.08); }
.gate-pill.pending { color: var(--text-2); border-color: var(--border); background: var(--bg-2); }

/* ── Timeline ────────────────────────────────────────── */
.timeline {
  background: var(--bg-1);
  border: 1px solid var(--border);
  border-radius: 10px;
  overflow: hidden;
}
.tl-item {
  padding: 12px 16px;
  border-bottom: 1px solid var(--border);
  cursor: pointer;
  transition: background 0.1s;
}
.tl-item:last-child { border-bottom: none; }
.tl-item:hover { background: var(--bg-2); }
.tl-item-header {
  display: flex;
  align-items: center;
  gap: 8px;
}
.tl-type {
  font-family: var(--mono);
  font-size: 10px;
  font-weight: 600;
  text-transform: uppercase;
  padding: 2px 7px;
  border-radius: 3px;
}
.tl-type.verify { color: var(--blue); background: var(--blue-dim); }
.tl-type.rca { color: var(--purple); background: #2e1065; }
.tl-type.reverify { color: var(--cyan); background: #083344; }
.tl-type.regression { color: var(--amber); background: var(--amber-dim); }
.tl-type.triage { color: #f472b6; background: #500724; }
.tl-type.fix-attempts { color: var(--red); background: var(--red-dim); }
.tl-pass { font-family: var(--mono); font-size: 11px; font-weight: 600; }
.tl-pass.pass { color: var(--green); }
.tl-pass.fail { color: var(--red); }
.tl-key {
  font-family: var(--mono);
  font-size: 10px;
  color: var(--text-2);
  flex: 1;
  text-align: right;
}
.tl-time {
  font-family: var(--mono);
  font-size: 10px;
  color: var(--text-2);
}
.tl-summary {
  font-size: 12px;
  color: var(--text-2);
  margin-top: 6px;
  display: none;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 300px;
  overflow-y: auto;
  line-height: 1.6;
}
.tl-item.expanded .tl-summary { display: block; }

/* ── Events ──────────────────────────────────────────── */
.events-toggle {
  font-family: var(--mono);
  font-size: 11px;
  color: var(--text-2);
  cursor: pointer;
  padding: 8px 0;
  display: flex;
  align-items: center;
  gap: 6px;
}
.events-toggle:hover { color: var(--text-1); }
.events-table {
  display: none;
  background: var(--bg-1);
  border: 1px solid var(--border);
  border-radius: 10px;
  overflow: hidden;
  font-size: 12px;
}
.events-table.open { display: block; }
.ev-row {
  display: grid;
  grid-template-columns: 160px 120px 80px 1fr;
  padding: 8px 12px;
  border-bottom: 1px solid var(--border);
  font-family: var(--mono);
  font-size: 11px;
  color: var(--text-2);
}
.ev-row:last-child { border-bottom: none; }

/* ── Loading state ───────────────────────────────────── */
.loading {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 60px;
  color: var(--text-2);
  font-family: var(--mono);
  font-size: 13px;
}
.spinner {
  width: 16px; height: 16px;
  border: 2px solid var(--border);
  border-top-color: var(--cyan);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  margin-right: 10px;
}
@keyframes spin { to { transform: rotate(360deg); } }

/* ── Poll indicator ──────────────────────────────────── */
.poll-dot {
  width: 6px; height: 6px;
  border-radius: 50%;
  background: var(--green);
  margin-left: auto;
  flex-shrink: 0;
  opacity: 0.5;
  transition: opacity 0.3s;
}
.poll-dot.active { opacity: 1; animation: blink 0.3s; }
@keyframes blink { 50% { opacity: 0.2; } }
</style>
</head>
<body>

<div class="topbar">
  <div class="topbar-brand" onclick="showOverview()">IRIAI BUILD</div>
  <div class="topbar-tabs" id="tabs"></div>
  <div class="add-feature-btn" onclick="openModal()" title="Add feature">+</div>
  <div class="poll-dot" id="pollDot"></div>
</div>

<div class="modal-overlay" id="modal" onclick="if(event.target===this)closeModal()">
  <div class="modal">
    <input id="searchInput" placeholder="Enter feature ID or search by name..." oninput="debounceSearch()" onkeydown="if(event.key==='Escape')closeModal()">
    <div class="modal-results" id="searchResults"></div>
  </div>
</div>

<div class="main" id="content"></div>

<script>
// ── State ───────────────────────────────────────────────
let trackedFeatures = JSON.parse(localStorage.getItem('iriai_tracked') || '[]');
let featureData = {};
let activeView = 'overview'; // 'overview' | feature_id
let pollInterval = null;
let searchTimeout = null;

// ── Init ────────────────────────────────────────────────
renderTabs();
showOverview();
startPolling();

// ── Polling ─────────────────────────────────────────────
function startPolling() {
  if (pollInterval) clearInterval(pollInterval);
  pollAll();
  pollInterval = setInterval(pollAll, 10000);
}

async function pollAll() {
  const dot = document.getElementById('pollDot');
  dot.classList.add('active');
  setTimeout(() => dot.classList.remove('active'), 300);

  const fetches = trackedFeatures.map(id =>
    fetch('/api/feature/' + id)
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d) featureData[id] = d; })
      .catch(() => {})
  );
  await Promise.all(fetches);
  renderTabs();
  renderContent();
}

// ── Tabs ────────────────────────────────────────────────
function renderTabs() {
  const el = document.getElementById('tabs');
  el.innerHTML = trackedFeatures.map(id => {
    const d = featureData[id];
    const name = d ? d.name : id;
    const phase = d ? d.phase : '';
    const isActive = activeView === id;
    return '<div class="tab ' + (isActive ? 'active' : '') + '" onclick="showWorkstream(\\'' + id + '\\')">'
      + '<span class="phase-dot" style="background:' + phaseColor(phase) + '"></span>'
      + '<span>' + escHtml(name.length > 24 ? name.slice(0,22) + '..' : name) + '</span>'
      + '<span class="close-tab" onclick="event.stopPropagation();removeFeature(\\'' + id + '\\')">&times;</span>'
      + '</div>';
  }).join('');
}

function phaseColor(p) {
  if (p === 'implementation') return 'var(--amber)';
  if (p === 'complete') return 'var(--green)';
  if (p === 'failed') return 'var(--red)';
  return 'var(--blue)';
}

// ── Views ───────────────────────────────────────────────
function showOverview() {
  activeView = 'overview';
  renderTabs();
  renderContent();
}

function showWorkstream(id) {
  activeView = id;
  renderTabs();
  renderContent();
  // Immediate fetch
  fetch('/api/feature/' + id)
    .then(r => r.ok ? r.json() : null)
    .then(d => { if (d) { featureData[id] = d; renderContent(); } })
    .catch(() => {});
}

function renderContent() {
  const el = document.getElementById('content');
  if (activeView === 'overview') {
    renderOverview(el);
  } else {
    renderWorkstream(el, activeView);
  }
}

// ── Overview ────────────────────────────────────────────
function renderOverview(el) {
  if (trackedFeatures.length === 0) {
    el.innerHTML = '<div class="empty-state">'
      + '<div class="es-icon">&#9678;</div>'
      + '<div class="es-title">No features tracked</div>'
      + '<div class="es-sub">Add a feature by its ID to start monitoring build progress.</div>'
      + '<div class="es-btn" onclick="openModal()">+ Add Feature</div>'
      + '</div>';
    return;
  }

  el.innerHTML = '<div class="overview-grid">'
    + trackedFeatures.map(id => {
      const d = featureData[id];
      if (!d) return '<div class="feature-card" onclick="showWorkstream(\\'' + id + '\\')"><div class="loading"><div class="spinner"></div>Loading ' + id + '...</div></div>';

      const dag = d.dag;
      let progressPct = 0;
      let statusText = d.phase;
      if (dag && d.groups) {
        const done = d.groups.filter(g => g.status === 'complete').length;
        progressPct = Math.round((done / dag.total_groups) * 100);
        const active = d.groups.find(g => g.status === 'active');
        if (active) {
          const remaining = active.task_count - active.completed_count;
          const hasVerify = active.verify_steps && active.verify_steps.length > 0;
          if (hasVerify && !active.verify_steps[active.verify_steps.length - 1].passed) {
            statusText = 'Fix loop on G' + active.index;
          } else {
            statusText = 'G' + active.index + '/' + dag.total_groups + ' — ' + remaining + ' tasks left';
          }
        } else if (done === dag.total_groups) {
          statusText = 'DAG complete — gates pending';
        }
      }

      return '<div class="feature-card" data-phase="' + d.phase + '" onclick="showWorkstream(\\'' + id + '\\')">'
        + '<div class="fc-header">'
        + '  <div><div class="fc-name">' + escHtml(d.name) + '</div><div class="fc-id">' + d.id + '</div></div>'
        + '  <span class="phase-badge" data-phase="' + d.phase + '">' + d.phase + '</span>'
        + '</div>'
        + (dag ? '<div class="fc-progress">'
          + '<div class="fc-progress-bar"><div class="fc-progress-fill" style="width:' + progressPct + '%"></div></div>'
          + '<div class="fc-progress-text">' + escHtml(statusText) + '</div>'
          + '</div>' : '')
        + '<div class="fc-time">' + relTime(d.updated_at) + '</div>'
        + '</div>';
    }).join('')
    + '</div>';
}

// ── Workstream ──────────────────────────────────────────
function renderWorkstream(el, id) {
  const d = featureData[id];
  if (!d) {
    el.innerHTML = '<div class="loading"><div class="spinner"></div>Loading feature...</div>';
    return;
  }

  let html = '';

  // Header
  html += '<div class="ws-header">'
    + '<div class="ws-back" onclick="showOverview()">&#8592;</div>'
    + '<div class="ws-title">' + escHtml(d.name) + '</div>'
    + '<span class="phase-badge" data-phase="' + d.phase + '">' + d.phase + '</span>'
    + '<div class="ws-meta">' + d.id + ' &middot; ' + d.workflow_name + ' &middot; ' + relTime(d.updated_at) + '</div>'
    + '</div>';

  // DAG flow
  if (d.dag && d.groups) {
    html += '<div class="section">'
      + '<div class="section-title">DAG Progress &mdash; ' + d.dag.total_tasks + ' tasks in ' + d.dag.total_groups + ' groups</div>'
      + '<div class="dag-flow">'
      + '<div class="dag-main">';

    let activeGroup = null;
    d.groups.forEach((g, i) => {
      if (i > 0) html += '<div class="dag-edge ' + (g.status === 'complete' || d.groups[i-1].status === 'complete' ? 'complete' : '') + '"></div>';
      html += '<div class="dag-node">'
        + '<div class="dag-circle ' + g.status + '">G' + g.index + '</div>'
        + '<div class="dag-label">' + g.completed_count + '/' + g.task_count + '</div>'
        + '</div>';
      if (g.status === 'active') activeGroup = g;
    });

    html += '</div>'; // dag-main

    // Fix loop branch for active group
    if (activeGroup && (activeGroup.verify_steps.length > 0 || activeGroup.fix_steps.length > 0)) {
      html += renderFixBranch(activeGroup);
    }

    html += '</div></div>'; // dag-flow, section
  } else {
    html += '<div class="section">'
      + '<div class="section-title">Implementation</div>'
      + '<div class="dag-flow" style="text-align:center;color:var(--text-2);padding:40px">'
      + 'No DAG yet &mdash; feature is in <strong>' + d.phase + '</strong> phase'
      + '</div></div>';
  }

  // Active group tasks
  if (d.groups) {
    const active = d.groups.find(g => g.status === 'active');
    if (active) {
      html += '<div class="section">'
        + '<div class="section-title">Group ' + active.index + ' Tasks &mdash; ' + active.completed_count + '/' + active.task_count + ' complete</div>'
        + '<div class="task-list">'
        + active.tasks.map(t => {
          const icon = t.status === 'complete' ? '&#10003;' : t.status === 'in_progress' ? '&#9672;' : '&#9675;';
          return '<div class="task-item">'
            + '<div class="task-icon ' + t.status + '">' + icon + '</div>'
            + '<span class="task-id">' + t.id + '</span>'
            + '<div><div class="task-name">' + escHtml(t.name) + '</div>'
            + (t.summary ? '<div class="task-summary">' + escHtml(t.summary) + '</div>' : '')
            + '</div></div>';
        }).join('')
        + '</div></div>';
    }
  }

  // Gates
  if (d.gates) {
    const allDagDone = d.groups && d.groups.every(g => g.status === 'complete');
    html += '<div class="section">'
      + '<div class="section-title">Post-DAG Gates</div>'
      + '<div class="gates-row">'
      + Object.entries(d.gates).map(([name, passed]) =>
        '<div class="gate-pill ' + (passed ? 'passed' : 'pending') + '">'
        + (passed ? '&#10003; ' : '&#9675; ') + name
        + '</div>'
      ).join('')
      + '</div></div>';
  }

  // Timeline
  if (d.timeline && d.timeline.length > 0) {
    html += '<div class="section">'
      + '<div class="section-title">Verify / Fix Timeline</div>'
      + '<div class="timeline">'
      + d.timeline.map(t => {
        const passLabel = t.passed === true ? '<span class="tl-pass pass">PASS</span>'
          : t.passed === false ? '<span class="tl-pass fail">FAIL</span>' : '';
        return '<div class="tl-item" onclick="this.classList.toggle(\\' expanded\\')">'
          + '<div class="tl-item-header">'
          + '<span class="tl-type ' + t.type + '">' + t.type + '</span>'
          + passLabel
          + '<span class="tl-key">' + escHtml(t.key) + '</span>'
          + '<span class="tl-time">' + relTime(t.created_at) + '</span>'
          + '</div>'
          + '<div class="tl-summary">' + escHtml(t.summary) + '</div>'
          + '</div>';
      }).join('')
      + '</div></div>';
  }

  // Events
  if (d.events && d.events.length > 0) {
    html += '<div class="section">'
      + '<div class="events-toggle" onclick="this.nextElementSibling.classList.toggle(\\' open\\')">&#9656; Event Log (' + d.events.length + ')</div>'
      + '<div class="events-table">'
      + d.events.map(e =>
        '<div class="ev-row">'
        + '<span>' + relTime(e.created_at) + '</span>'
        + '<span>' + e.event_type + '</span>'
        + '<span>' + e.source + '</span>'
        + '<span>' + escHtml(e.content.slice(0, 80)) + '</span>'
        + '</div>'
      ).join('')
      + '</div></div>';
  }

  el.innerHTML = html;
}

function renderFixBranch(group) {
  // Merge verify steps and fix steps into a unified branch flow
  const steps = [];

  group.verify_steps.forEach(v => {
    steps.push({ type: v.type, passed: v.passed, time: v.created_at, key: v.key });
  });

  // Add fix-related steps from the group's fix_steps (bug-rca, bug-reverify, etc.)
  group.fix_steps.forEach(f => {
    if (f.type !== 'fix-attempts') {
      steps.push({ type: f.type, passed: f.passed, time: f.created_at, key: f.key });
    }
  });

  steps.sort((a, b) => a.time.localeCompare(b.time));

  if (steps.length === 0) return '';

  let html = '<div class="fix-branch">'
    + '<div class="fix-branch-title">&#8627; Fix Loop</div>'
    + '<div class="fix-branch-flow">';

  steps.forEach((s, i) => {
    if (i > 0) html += '<span class="fix-arrow">&#8594;</span>';
    const cls = s.passed === true ? 'pass' : s.passed === false ? 'fail' : 'neutral';
    const label = s.type === 'verify' ? 'verify' : s.type === 're-verify' ? 'reverify' : s.type;
    const icon = s.passed === true ? '&#10003;' : s.passed === false ? '&#10007;' : '&#8226;';
    html += '<div class="fix-step"><div class="fix-pill ' + cls + '">' + icon + ' ' + label + '</div></div>';
  });

  html += '</div></div>';
  return html;
}

// ── Modal ───────────────────────────────────────────────
function openModal() {
  document.getElementById('modal').classList.add('open');
  const input = document.getElementById('searchInput');
  input.value = '';
  input.focus();
  document.getElementById('searchResults').innerHTML = '';
}

function closeModal() {
  document.getElementById('modal').classList.remove('open');
}

function debounceSearch() {
  clearTimeout(searchTimeout);
  searchTimeout = setTimeout(doSearch, 250);
}

async function doSearch() {
  const q = document.getElementById('searchInput').value.trim();
  if (!q) { document.getElementById('searchResults').innerHTML = ''; return; }

  try {
    const r = await fetch('/api/search?q=' + encodeURIComponent(q));
    const results = await r.json();
    document.getElementById('searchResults').innerHTML = results.map(f =>
      '<div class="modal-result" onclick="addFeature(\\'' + f.id + '\\')">'
      + '<span class="mr-id">' + f.id + '</span>'
      + '<span class="mr-name">' + escHtml(f.name) + '</span>'
      + '<span class="phase-badge" data-phase="' + f.phase + '">' + f.phase + '</span>'
      + '</div>'
    ).join('') || '<div style="padding:16px;color:var(--text-2);text-align:center">No matches</div>';
  } catch(e) {
    document.getElementById('searchResults').innerHTML = '<div style="padding:16px;color:var(--red)">Search failed</div>';
  }
}

function addFeature(id) {
  if (!trackedFeatures.includes(id)) {
    trackedFeatures.push(id);
    localStorage.setItem('iriai_tracked', JSON.stringify(trackedFeatures));
  }
  closeModal();
  showWorkstream(id);
  pollAll();
}

function removeFeature(id) {
  trackedFeatures = trackedFeatures.filter(f => f !== id);
  localStorage.setItem('iriai_tracked', JSON.stringify(trackedFeatures));
  delete featureData[id];
  if (activeView === id) showOverview();
  renderTabs();
  renderContent();
}

// ── Helpers ─────────────────────────────────────────────
function escHtml(s) {
  if (!s) return '';
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function relTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  const now = Date.now();
  const sec = Math.floor((now - d.getTime()) / 1000);
  if (sec < 60) return sec + 's ago';
  if (sec < 3600) return Math.floor(sec / 60) + 'm ago';
  if (sec < 86400) return Math.floor(sec / 3600) + 'h ago';
  return Math.floor(sec / 86400) + 'd ago';
}
</script>
</body>
</html>
"""


if __name__ == "__main__":
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8080
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
