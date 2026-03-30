"""iriai-build-v2 monitoring dashboard.

Usage:
    python dashboard.py [--port 8080]
"""
from __future__ import annotations

import json
import os
import sys

import asyncpg
from fastapi import FastAPI, HTTPException, Query
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
                        summary = v.get("summary", "")
                    except (json.JSONDecodeError, KeyError):
                        summary = r["value"] if r["value"] else ""
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

    # Active agent
    active_agent = None
    for e in events:
        if e["event_type"] == "agent_start":
            active_agent = e["source"]
            break  # events ordered DESC by created_at

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
        "workstreams": workstreams_list,
        "events": event_list,
        "active_agent": active_agent,
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


if __name__ == "__main__":
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8080
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
