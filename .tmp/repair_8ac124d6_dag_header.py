from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

import psycopg


FEATURE_ID = "8ac124d6"
DB_URL = "postgresql://danielzhang@localhost:5431/iriai_build_v2"
FEATURE_DIR = Path("/Users/danielzhang/src/iriai/.iriai/artifacts/features") / FEATURE_ID
BACKUP_DIR = Path("/tmp") / f"{FEATURE_ID}-dag-header-repair-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

DECISION_IDS = [
    "D-120",
    "D-136",
    "D-139",
    "D-369",
    "D-372",
    "D-SF1-11",
    "D-SF2-11",
    "D-SF2-12",
]

BANNED_HEADER_PATTERNS = [
    "KeychainBridge",
    "STUDIO_MAIN_IPC_SOCK",
    "SecretsResolver",
    "ipc_protocol.*Keychain",
    "Main IPC.*Keychain",
    "Keychain.*Main IPC",
]


def stable_json(data: object) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)


def digest_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def digest_obj(data: object) -> str:
    return digest_text(json.dumps(data, sort_keys=True, ensure_ascii=False))


def latest(conn: psycopg.Connection, key: str) -> str:
    row = conn.execute(
        """
        select value::text
        from artifacts
        where feature_id = %s and key = %s
        order by created_at desc, id desc
        limit 1
        """,
        (FEATURE_ID, key),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"missing artifact row {key}")
    return row[0]


def backup(name: str, text: str) -> None:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", name)
    (BACKUP_DIR / safe).write_text(text, encoding="utf-8")


def put(conn: psycopg.Connection, key: str, text: str) -> None:
    conn.execute(
        "insert into artifacts (feature_id, key, value) values (%s, %s, %s)",
        (FEATURE_ID, key, text),
    )


def update_chunk_digest(record: dict) -> None:
    chunk = record.get("chunk")
    if not isinstance(chunk, dict):
        return
    content = {
        key: value
        for key, value in record.items()
        if key not in {"chunk", "citations"}
    }
    chunk["content_digest"] = digest_obj(content)


def repair_strategy(strategy: dict) -> dict:
    strategy = json.loads(json.dumps(strategy))
    for ws in strategy.get("workstreams", []):
        if ws.get("id") == "WS-A":
            ws["rationale"] = (
                "SF-1, SF-2, and SF-3 form a mutually-coupled foundation cluster "
                "with bidirectional contracts: SF-1 consumes SF-3's client lib; "
                "SF-2 implements SF-3's server surface; SF-1 spawns SF-2 via the "
                "process_spawn launcher contract; SF-2 owns setup-check probing, "
                "CLI-delegated auth status, and backend bridge events; SF-2 emits "
                "setup_check_updated which SF-1's Setup Check view host mirrors. "
                "The bridge envelope, state-snapshot model, push-event catalog, "
                "REST auth, per-launch nonce, and spawn env schema must be "
                "co-designed in one planning pass to keep the 30-minute-to-understand "
                "budget. Splitting risks protocol drift and setup/auth state "
                "inconsistencies."
            )
            update_chunk_digest(ws)

    strategy["shared_infrastructure"] = [
        "Bridge protocol envelope + state-snapshot resync + push-event catalog + command_ack semantics (SF-3) — consumed by every other subfeature via WebSocket",
        f"CLI-delegated provider authentication boundary — app-owned surfaces never store or broker provider secrets; locked by {', '.join(DECISION_IDS)}",
        "Backend spawn env schema (STUDIO_DB_DSN, STUDIO_LAUNCH_NONCE, STUDIO_HTTP_PORT, STUDIO_CLAUDE_BIN, STUDIO_CODEX_BIN, etc.) — SF-1 → SF-2 launcher contract",
        "ConfigService.snapshot() read surface + compose_worker_env(workflow_id, project_id, dsn) — SF-2 owned, consumed by SF-5 for worker-spawn env + required_actors gate; worker env is scratch-built from resolved binary paths, HOME/CLI config carry-list, and explicit required-secrets status with no os.environ inheritance",
        "Workflow CATALOG registry (build_feature_workflow, build_bugfix_workflow with required_actors metadata) — SF-7 owned, surfaced by SF-5's list_workflow_types()",
        "ArtifactService + PhaseStore + TaskStore API (read/write/commit/blame/diff + per-task dependency registration + phase-state transitions + conflict registration) — SF-6 owned, consumed by SF-5/SF-7/SF-10/SF-12/SF-13/SF-14",
        "Checkpoint trigger API + resume manifest schema — SF-10 owned, invoked by SF-2 lifespan, SF-5 lifecycle transitions, SF-7 orchestrator events, SF-8 message state, SF-6 phase state",
        "Chat event payload schemas (user_turn, orch_turn, outgoing_message, incoming_message, dispatch, result, live_edit) + anchor identifier format (phase_id + conversation_ts) — SF-11 owned, produced by SF-7/SF-8/SF-9",
        "Chat shell host API (mount props phaseId+workflowId, pane sizing callbacks, keyboard-focus handoff) — SF-11 owned, embedded by SF-12/SF-13/SF-14",
        "Scope metadata feeds for studioSidebar: workspace_roots on WorkflowSummary (SF-5) and directories on project_updated (SF-4) — both consumed by SF-1's studioSidebar",
        "inject_edit_metadata command routing contract (SF-1 producer → SF-3 router → SF-9 consumer) + idempotency/debounce on (workflow_id, file_path, digest)",
    ]
    return strategy


def repair_decomposition(decomp: dict) -> dict:
    decomp = json.loads(json.dumps(decomp))
    decomp["decomposition_rationale"] = (
        "14-subfeature split across 5 infrastructure, 5 core execution, and 4 UI "
        "concerns. Reconciliation pass 4 applies six deltas: (1) Setup Check "
        "ownership split — SF-2 owns backend probes/verdict/REST/push event; SF-1 "
        "owns the renderer view host, webview, and IStudioSetupCheckService mirror; "
        "new SF-2 → SF-1 event_producer edge locks the setup_check_updated payload. "
        "(2) Workflow catalog ownership pinned via Option A — SF-7 owns "
        "workflows/catalog.py (build_feature_workflow / build_bugfix_workflow "
        "factories) and roles/ (bundled prompts); SF-5 owns list_workflow_types(), "
        "the start_workflow command, the required_actors gate, and the workflow "
        "lifecycle state machine; new SF-7 → SF-5 service_api edge locks the "
        "CATALOG registry contract. (3) New SF-5 → SF-2 service_api edge for "
        "ConfigService.snapshot() read surface and compose_worker_env(...). "
        "(4) Provider authentication is CLI-delegated per locked decisions "
        f"{', '.join(DECISION_IDS)}: there is no app-managed provider-secret "
        "broker and no runtime secrets channel between SF-1 and SF-2; the "
        "pre-existing SF-1 → SF-2 process_spawn edge remains the one-shot launcher "
        "contract. (5) inject_edit_metadata routing kept per architecture §5.4 — "
        "two edges: SF-1 → SF-3 command_producer (save-event fallback when "
        "FSEvents is denied) and SF-3 → SF-9 command_consumer (bridge → worker-host "
        "stdio routing into the live-edit consumer). (6) Optional scope-metadata "
        "rename applied: SF-4 → SF-1 contract is now 'directories sub-field on "
        "project_updated event' instead of 'project_directories on ProjectSummary'."
    )

    for sf in decomp.get("subfeatures", []):
        slug = sf.get("slug")
        if slug == "vscode-fork-shell":
            sf["description"] = (
                "The thin VS Code fork layer. Covers product.json overrides, the "
                "three custom contrib modules (studioLauncher for startup routing + "
                "Setup Check view host + Setup Check React webview + "
                "IStudioSetupCheckService (renderer-side mirror), studioWorkflow for "
                "WorkflowTabEditorInput/Pane, studioSidebar for context-dependent "
                "sidebar scoping), the renderer DI services "
                "(IStudioBackendLifecycleService, IStudioBridgeService façade, "
                "IStudioScopeService, IStudioConfigService, IStudioSetupCheckService), "
                "Electron main helpers (EnvironmentResolver, SingleInstanceLock, "
                "PortAllocator, PythonResolver, BackendProcessSupervisor), the editor "
                "save-event listener that emits inject_edit_metadata bridge commands "
                "when FSEvents is denied, and the build + distribution pipeline."
            )
            update_chunk_digest(sf)
        elif slug == "backend-foundation-setup":
            sf["description"] = (
                "FastAPI application, embedded PostgreSQL via pgserver + "
                "PostgresManager, Alembic migrations with targeted-backup policy, "
                "ConfigService + DependencyProbeService + Setup Check verdict logic "
                "(block/warn/pass) + CLI auth probes with optional env fallback, "
                "/setup-check/* REST endpoints + setup_check_updated push event, "
                "/health, /diagnostics/bundle, structured logging and log rotation. "
                "compose_worker_env(workflow_id, project_id, dsn) is the canonical "
                "worker-spawn env composer."
            )
            update_chunk_digest(sf)

    repaired_edges: list[dict] = []
    for edge in decomp.get("edges", []):
        if (
            edge.get("from_subfeature") == "vscode-fork-shell"
            and edge.get("to_subfeature") == "backend-foundation-setup"
            and edge.get("interface_type") == "ipc_protocol"
        ):
            continue
        if (
            edge.get("from_subfeature") == "vscode-fork-shell"
            and edge.get("to_subfeature") == "backend-foundation-setup"
            and edge.get("interface_type") == "process_spawn"
        ):
            edge["data_contract"] = (
                "Backend spawn argv + environment variable schema "
                "(STUDIO_DB_DSN, STUDIO_LAUNCH_NONCE, STUDIO_HTTP_PORT, "
                "STUDIO_CLAUDE_BIN, STUDIO_CODEX_BIN, etc.) + /health readiness "
                "contract."
            )
            edge["description"] = (
                "Electron main spawns the backend process and injects resolved "
                "launcher env (PATH snapshot, nonce, binary paths, project/home "
                "context, and CLI auth carry-list). One-shot launcher contract at "
                "backend start only; not a runtime provider-secret channel."
            )
            update_chunk_digest(edge)
        if (
            edge.get("from_subfeature") == "workflow-supervisor-workspace"
            and edge.get("to_subfeature") == "backend-foundation-setup"
            and edge.get("interface_type") == "service_api"
        ):
            edge["data_contract"] = (
                "ConfigService.snapshot() read surface: {resolved_binary_paths: "
                "{claude, codex, git, node}, provider_auth_status, permissions: "
                "{fsevents, full_disk_access}, default_runtime, ports}. "
                "compose_worker_env(workflow_id, project_id, dsn) -> dict[str, str]: "
                "resolved PATH + STUDIO_CLAUDE_BIN / STUDIO_CODEX_BIN / "
                "STUDIO_GIT_BIN + HOME/CLI config carry-list + explicit "
                "required-secrets status; env is scratch-built with no os.environ "
                "inheritance."
            )
            update_chunk_digest(edge)
        repaired_edges.append(edge)
    decomp["edges"] = repaired_edges
    return decomp


def replace_block(text: str, heading: str, replacement_json: str) -> str:
    marker = f"## {heading}"
    start = text.find(marker)
    if start < 0:
        raise RuntimeError(f"missing heading {marker}")
    json_start = text.find("\n", start)
    if json_start < 0:
        raise RuntimeError(f"missing heading newline {marker}")
    json_start += 1
    while json_start < len(text) and text[json_start] == "\n":
        json_start += 1
    end_marker = "\n---"
    end = text.find(end_marker, json_start)
    if end < 0:
        raise RuntimeError(f"missing block terminator after {marker}")
    return text[:json_start] + replacement_json.rstrip() + "\n" + text[end:]


def header_scope(text: str) -> str:
    candidates = [
        pos
        for token in ["\n## Subfeature: Cluster 1", "\n<!-- SF: vscode-fork-shell -->"]
        if (pos := text.find(token)) >= 0
    ]
    if not candidates:
        raise RuntimeError("could not identify header scope")
    return text[: min(candidates)]


def count_header_hits(text: str) -> dict[str, int]:
    scope = header_scope(text)
    return {
        pattern: len(re.findall(pattern, scope, flags=re.IGNORECASE))
        for pattern in BANNED_HEADER_PATTERNS
    }


def evidence_section(before_hits: dict[str, int], after_hits: dict[str, int]) -> str:
    before_total = sum(before_hits.values())
    return "\n".join(
        [
            "<!-- BEGIN MANUAL DAG HEADER REPAIR EVIDENCE -->",
            "## DAG Header Repair Evidence",
            "",
            "- rca_id: RCA-DAG-HEADER-2026-04",
            "- root_cause: the root DAG compiler faithfully copied stale broad/decomposition header text even though the per-subfeature bundles and locked decisions had already moved to CLI-delegated auth. Gate-review revision routing treated those header-surface requests like subfeature DAG edits, so targeted revision did not repair the deterministic header source.",
            "- remediation: latest `dag:strategy`, `decomposition`, and `decomposition-structured` rows were repaired as the source of truth; the compiled DAG and compile source bundle were regenerated from those repaired rows; the stale failed `gate-review:dag` resume artifact was cleared so the next cycle reviews the repaired bundle.",
            f"- locked_decisions: {', '.join(f'`{item}`' for item in DECISION_IDS)}",
            "- validator_scope: compiled DAG top-level header only, ending before the first subfeature bundle.",
            f"- validator_dry_run_before_total: {before_total}",
            f"- validator_dry_run_after_total: {sum(after_hits.values())}",
            "- validator_dry_run_before:",
            *[f"  - `{key}`: {value}" for key, value in before_hits.items()],
            "- validator_dry_run_after:",
            *[f"  - `{key}`: {value}" for key, value in after_hits.items()],
            "- grep_gate_wiring_evidence: standing compile-path guard wired at `src/iriai_build_v2/workflows/planning/phases/task_planning.py::TaskPlanningPhase._validate_root_dag_header_consistency`; the same header-scope probes must remain zero before opening another DAG review cycle.",
            "<!-- END MANUAL DAG HEADER REPAIR EVIDENCE -->",
        ]
    )


def upsert_evidence(text: str, evidence: str) -> str:
    pattern = re.compile(
        r"\n*<!-- BEGIN MANUAL DAG HEADER REPAIR EVIDENCE -->.*?"
        r"<!-- END MANUAL DAG HEADER REPAIR EVIDENCE -->\n*",
        re.DOTALL,
    )
    text = pattern.sub("\n", text).rstrip()
    generated_marker = "\n<!-- BEGIN GENERATED DAG GATE SURFACES -->"
    idx = text.find(generated_marker)
    if idx >= 0:
        return text[:idx].rstrip() + "\n\n" + evidence + "\n\n" + text[idx:].lstrip()
    return text.rstrip() + "\n\n" + evidence + "\n"


def update_ledger(text: str, compiled_digest: str) -> str:
    data = json.loads(text)
    note = (
        f"manual-dag-header-repair: repaired dag:strategy, decomposition, "
        f"decomposition-structured, compiled dag.md, and cleared stale gate-review "
        f"resume artifact [compiled_digest={compiled_digest}]"
    )
    for finding in data.get("findings", []):
        if finding.get("id") in {"GF-028", "GF-029", "GF-030", "GF-031"}:
            attempts = finding.setdefault("revision_attempts", [])
            if note not in attempts:
                attempts.append(note)
            finding["status"] = "fix_attempted"
    return stable_json(data)


def main() -> None:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    conn = psycopg.connect(DB_URL)
    with conn:
        strategy_text = latest(conn, "dag:strategy")
        decomp_text = latest(conn, "decomposition")
        sidecar_text = latest(conn, "decomposition-structured")
        ledger_text = latest(conn, "gate-review-ledger:dag")
        backup("db-dag_strategy.json", strategy_text)
        backup("db-decomposition.json", decomp_text)
        backup("db-decomposition_structured.json", sidecar_text)
        backup("db-gate-review-ledger_dag.json", ledger_text)

        strategy = repair_strategy(json.loads(strategy_text))
        decomp = repair_decomposition(json.loads(decomp_text))
        sidecar = json.loads(sidecar_text)
        sidecar["content"] = repair_decomposition(sidecar["content"])

        new_strategy_text = stable_json(strategy)
        new_decomp_text = stable_json(decomp)
        sidecar["meta"]["source_hash"] = digest_text(new_decomp_text)
        sidecar["meta"]["content_digest"] = digest_obj(sidecar["content"])
        sidecar["meta"]["generated_from"] = "manual_dag_header_repair"
        new_sidecar_text = stable_json(sidecar)

        put(conn, "dag:strategy", new_strategy_text)
        put(conn, "decomposition", new_decomp_text)
        put(conn, "decomposition-structured", new_sidecar_text)

        # Clear the stale failed gate review from resume lookup without deleting history.
        put(conn, "gate-review:dag", "")

        # Repair mirror source artifacts.
        mirrors = {
            "broad/strategy.md": new_strategy_text,
            "decomposition.md": new_decomp_text,
            "decomposition.json": new_sidecar_text,
        }
        for rel, content in mirrors.items():
            path = FEATURE_DIR / rel
            if path.exists():
                backup(f"mirror-{rel}", path.read_text(encoding="utf-8"))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

        # Repair root compiled DAG and compile source bundle by replacing the two
        # root JSON blocks from the repaired source artifacts.
        for rel in ["dag.md", "compile-sources-dag.md", ".staging/dag.md"]:
            path = FEATURE_DIR / rel
            if not path.exists():
                continue
            original = path.read_text(encoding="utf-8")
            backup(f"mirror-{rel}", original)
            before_hits = count_header_hits(original)
            repaired = replace_block(original, "Broad Artifact (dag:strategy)", new_strategy_text)
            repaired = replace_block(repaired, "Decomposition", new_decomp_text)
            after_hits = count_header_hits(repaired)
            repaired = upsert_evidence(repaired, evidence_section(before_hits, after_hits))
            path.write_text(repaired, encoding="utf-8")

        # Clear resumable failed review mirrors. Historical DB rows remain.
        for rel in ["reviews/dag.md", ".staging/reviews/dag.md"]:
            path = FEATURE_DIR / rel
            if path.exists():
                backup(f"mirror-{rel}", path.read_text(encoding="utf-8"))
                path.write_text("", encoding="utf-8")

        compiled_text = (FEATURE_DIR / "dag.md").read_text(encoding="utf-8")
        after_hits = count_header_hits(compiled_text)
        if any(after_hits.values()):
            raise RuntimeError(f"header still has banned hits: {after_hits}")

        ledger_updated = update_ledger(ledger_text, digest_text(compiled_text))
        put(conn, "gate-review-ledger:dag", ledger_updated)

        print(
            json.dumps(
                {
                    "backup_dir": str(BACKUP_DIR),
                    "header_hits_after": after_hits,
                    "compiled_digest": digest_text(compiled_text),
                    "updated_keys": [
                        "dag:strategy",
                        "decomposition",
                        "decomposition-structured",
                        "gate-review:dag",
                        "gate-review-ledger:dag",
                    ],
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
