CREATE TABLE IF NOT EXISTS features (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    slug          TEXT NOT NULL UNIQUE,
    workflow_name TEXT NOT NULL,
    workspace_id  TEXT NOT NULL,
    phase         TEXT NOT NULL DEFAULT 'pm',
    metadata      JSONB NOT NULL DEFAULT '{}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS events (
    id          BIGSERIAL PRIMARY KEY,
    feature_id  TEXT NOT NULL REFERENCES features(id),
    event_type  TEXT NOT NULL,
    source      TEXT NOT NULL,
    content     TEXT,
    metadata    JSONB NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_events_feature ON events(feature_id, created_at);

CREATE TABLE IF NOT EXISTS artifacts (
    id          BIGSERIAL PRIMARY KEY,
    feature_id  TEXT NOT NULL REFERENCES features(id),
    key         TEXT NOT NULL,
    value       TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_artifacts_feature_key ON artifacts(feature_id, key, id DESC);

CREATE TABLE IF NOT EXISTS execution_journal_rows (
    id                          BIGSERIAL PRIMARY KEY,
    feature_id                  TEXT NOT NULL REFERENCES features(id),
    idempotency_key             TEXT NOT NULL,
    entry_type                  TEXT NOT NULL,
    status                      TEXT NOT NULL,
    dispatcher_state            TEXT NOT NULL DEFAULT 'requested',
    actor                       TEXT NOT NULL DEFAULT '',
    runtime                     TEXT NOT NULL DEFAULT '',
    dag_sha256                  TEXT NOT NULL DEFAULT '',
    group_idx                   INTEGER,
    task_id                     TEXT,
    request_digest              TEXT NOT NULL,
    payload                     JSONB NOT NULL DEFAULT '{}',
    requires_legacy_visibility  BOOLEAN NOT NULL DEFAULT FALSE,
    projection_mode             TEXT NOT NULL DEFAULT 'legacy_compatibility',
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT execution_journal_rows_feature_idempotency_key
        UNIQUE (feature_id, idempotency_key),
    CONSTRAINT execution_journal_rows_status_check
        CHECK (status IN ('started', 'succeeded', 'failed', 'cancelled', 'incomplete')),
    CONSTRAINT execution_journal_rows_dispatcher_state_check
        CHECK (dispatcher_state IN (
            'requested', 'attempt_started', 'context_prepared',
            'runtime_invoking', 'runtime_returned', 'patch_capturing',
            'output_normalizing', 'evidence_recording', 'succeeded',
            'failed', 'cancelled', 'incomplete'
        )),
    CONSTRAINT execution_journal_rows_projection_mode_check
        CHECK (projection_mode IN ('legacy_compatibility'))
);
CREATE INDEX IF NOT EXISTS idx_execution_journal_rows_feature_status
    ON execution_journal_rows(feature_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_execution_journal_rows_feature_type
    ON execution_journal_rows(feature_id, entry_type, id DESC);
CREATE INDEX IF NOT EXISTS idx_execution_journal_rows_dag_group
    ON execution_journal_rows(feature_id, dag_sha256, group_idx, id DESC)
    WHERE group_idx IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_execution_journal_rows_task
    ON execution_journal_rows(feature_id, task_id, id DESC)
    WHERE task_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS public_dashboard_outbox (
    id              BIGSERIAL PRIMARY KEY,
    event_id        TEXT NOT NULL UNIQUE,
    feature_id      TEXT NOT NULL REFERENCES features(id),
    event_type      TEXT NOT NULL,
    schema_version  INTEGER NOT NULL DEFAULT 1,
    visibility      TEXT NOT NULL DEFAULT 'internal',
    payload         JSONB NOT NULL DEFAULT '{}',
    status          TEXT NOT NULL DEFAULT 'pending',
    attempt_count   INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    published_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_public_dashboard_outbox_status
    ON public_dashboard_outbox(status, id);
CREATE INDEX IF NOT EXISTS idx_public_dashboard_outbox_feature
    ON public_dashboard_outbox(feature_id, id DESC);

CREATE TABLE IF NOT EXISTS supervisor_slack_digest_state (
    id                       BIGSERIAL PRIMARY KEY,
    feature_id               TEXT NOT NULL REFERENCES features(id),
    dedupe_key               TEXT NOT NULL,
    snapshot_version         TEXT NOT NULL,
    signature_hash           TEXT NOT NULL,
    semantic_signature_hash  TEXT NOT NULL DEFAULT '',
    classification           TEXT NOT NULL DEFAULT '',
    recommended_action       TEXT NOT NULL DEFAULT '',
    group_idx                INTEGER,
    retry                    INTEGER,
    status                   TEXT NOT NULL DEFAULT 'pending',
    channel                  TEXT NOT NULL DEFAULT '',
    thread_ts                TEXT,
    message_ts               TEXT,
    send_reason              TEXT NOT NULL DEFAULT '',
    suppress_reason          TEXT NOT NULL DEFAULT '',
    citations                JSONB NOT NULL DEFAULT '[]',
    payload                  JSONB NOT NULL DEFAULT '{}',
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    delivered_at             TIMESTAMPTZ,
    CONSTRAINT supervisor_slack_digest_state_feature_dedupe
        UNIQUE (feature_id, dedupe_key),
    CONSTRAINT supervisor_slack_digest_state_status_check
        CHECK (status IN ('pending', 'delivered', 'suppressed', 'failed'))
);
CREATE INDEX IF NOT EXISTS idx_supervisor_slack_digest_state_feature_status
    ON supervisor_slack_digest_state(feature_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_supervisor_slack_digest_state_signature
    ON supervisor_slack_digest_state(feature_id, signature_hash, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_supervisor_slack_digest_state_semantic
    ON supervisor_slack_digest_state(feature_id, semantic_signature_hash, updated_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS ux_supervisor_slack_digest_state_active_signature
    ON supervisor_slack_digest_state(feature_id, signature_hash)
    WHERE status IN ('pending', 'delivered', 'suppressed');
CREATE UNIQUE INDEX IF NOT EXISTS ux_supervisor_slack_digest_state_active_semantic
    ON supervisor_slack_digest_state(feature_id, semantic_signature_hash)
    WHERE status IN ('pending', 'delivered', 'suppressed')
      AND COALESCE((payload->>'semantic_dedupe')::boolean, FALSE);

CREATE TABLE IF NOT EXISTS supervisor_slack_digest_audit (
    id                       BIGSERIAL PRIMARY KEY,
    feature_id               TEXT NOT NULL REFERENCES features(id),
    dedupe_key               TEXT NOT NULL,
    snapshot_version         TEXT NOT NULL,
    decision                 TEXT NOT NULL,
    reason                   TEXT NOT NULL DEFAULT '',
    signature_hash           TEXT NOT NULL DEFAULT '',
    semantic_signature_hash  TEXT NOT NULL DEFAULT '',
    channel                  TEXT NOT NULL DEFAULT '',
    thread_ts                TEXT,
    message_ts               TEXT,
    citations                JSONB NOT NULL DEFAULT '[]',
    payload                  JSONB NOT NULL DEFAULT '{}',
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT supervisor_slack_digest_audit_decision_check
        CHECK (decision IN ('attempt', 'delivered', 'suppress', 'failed'))
);
CREATE INDEX IF NOT EXISTS idx_supervisor_slack_digest_audit_feature
    ON supervisor_slack_digest_audit(feature_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_supervisor_slack_digest_audit_dedupe
    ON supervisor_slack_digest_audit(feature_id, dedupe_key, id DESC);

-- Slice 10d (doc 10 "Supervisor And Dashboard Integration" § "Slack Dedupe And
-- Suppression"): the typed-control-plane Slack dedupe store. Distinct from the
-- legacy artifact-classifier-driven `supervisor_slack_digest_*` tables above —
-- these two are the doc-10 `SupervisorDigestDedupeStore` contract, keyed by a
-- stable JSON digest over `SupervisorDigestKey`. They are AUDIT state, NOT
-- execution authority (doc 10: "They are audit state, not execution
-- authority. Do not use artifacts for dedupe state"). Their key prefixes are
-- pre-registered in `supervisor/read_only.py` so a supervisor-owned projection
-- of this state passes the Slice-10c-1 read-only contract.
CREATE TABLE IF NOT EXISTS supervisor_digest_state (
    id                       BIGSERIAL PRIMARY KEY,
    feature_id               TEXT NOT NULL REFERENCES features(id),
    group_idx                INTEGER,
    dedupe_key               TEXT NOT NULL,
    last_snapshot_version    TEXT NOT NULL DEFAULT '',
    classification           TEXT NOT NULL,
    recommended_action       TEXT NOT NULL DEFAULT '',
    recommended_route        TEXT NOT NULL DEFAULT '',
    last_sent_at             TIMESTAMPTZ,
    suppressed_count         INTEGER NOT NULL DEFAULT 0,
    last_digest_payload      JSONB NOT NULL DEFAULT '{}',
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT supervisor_digest_state_feature_dedupe
        UNIQUE (feature_id, dedupe_key)
);
CREATE INDEX IF NOT EXISTS idx_supervisor_dedupe_state_updated
    ON supervisor_digest_state(feature_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_supervisor_dedupe_state_group
    ON supervisor_digest_state(feature_id, group_idx, updated_at DESC);

CREATE TABLE IF NOT EXISTS supervisor_digest_audit (
    id                       BIGSERIAL PRIMARY KEY,
    state_id                 BIGINT REFERENCES supervisor_digest_state(id),
    feature_id               TEXT NOT NULL REFERENCES features(id),
    group_idx                INTEGER,
    dedupe_key               TEXT NOT NULL,
    snapshot_version         TEXT NOT NULL,
    should_send              BOOLEAN NOT NULL,
    reason                   TEXT NOT NULL,
    citation_refs            JSONB NOT NULL DEFAULT '[]',
    slack_channel            TEXT NOT NULL DEFAULT '',
    slack_thread_ts          TEXT NOT NULL DEFAULT '',
    slack_message_ts         TEXT NOT NULL DEFAULT '',
    payload                  JSONB NOT NULL DEFAULT '{}',
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_supervisor_dedupe_audit_feature
    ON supervisor_digest_audit(feature_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_supervisor_dedupe_audit_key
    ON supervisor_digest_audit(feature_id, dedupe_key, id DESC);
CREATE INDEX IF NOT EXISTS idx_supervisor_dedupe_audit_group
    ON supervisor_digest_audit(feature_id, group_idx, id DESC);

CREATE TABLE IF NOT EXISTS execution_artifact_projections (
    id                         BIGSERIAL PRIMARY KEY,
    typed_row_id               BIGINT NOT NULL REFERENCES execution_journal_rows(id) ON DELETE CASCADE,
    feature_id                 TEXT NOT NULL REFERENCES features(id),
    artifact_id                BIGINT NOT NULL REFERENCES artifacts(id),
    source_table               TEXT NOT NULL DEFAULT 'execution_journal_rows',
    source_id                  BIGINT,
    projection_owner           TEXT NOT NULL DEFAULT '',
    projection_kind            TEXT NOT NULL DEFAULT '',
    projection_key             TEXT NOT NULL,
    projection_sha256          TEXT NOT NULL,
    legacy_event_id            BIGINT REFERENCES events(id),
    dashboard_outbox_event_id  TEXT REFERENCES public_dashboard_outbox(event_id),
    payload                    JSONB NOT NULL DEFAULT '{}',
    idempotency_key            TEXT NOT NULL,
    created_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT execution_artifact_projections_feature_idempotency_key
        UNIQUE (feature_id, idempotency_key),
    CONSTRAINT execution_artifact_projections_row_key_digest
        UNIQUE (typed_row_id, projection_key, projection_sha256)
);
CREATE INDEX IF NOT EXISTS idx_execution_artifact_projections_row
    ON execution_artifact_projections(typed_row_id, id);
CREATE INDEX IF NOT EXISTS idx_execution_artifact_projections_artifact
    ON execution_artifact_projections(artifact_id);
CREATE INDEX IF NOT EXISTS idx_execution_artifact_projections_feature_key
    ON execution_artifact_projections(feature_id, projection_key, id DESC);
CREATE INDEX IF NOT EXISTS idx_execution_artifact_projections_legacy_event
    ON execution_artifact_projections(legacy_event_id)
    WHERE legacy_event_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_execution_artifact_projections_dashboard_event
    ON execution_artifact_projections(dashboard_outbox_event_id)
    WHERE dashboard_outbox_event_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS workspace_snapshots (
    id                        BIGSERIAL PRIMARY KEY,
    feature_id                TEXT NOT NULL REFERENCES features(id),
    idempotency_key           TEXT NOT NULL,
    execution_journal_row_id  BIGINT NOT NULL REFERENCES execution_journal_rows(id) ON DELETE CASCADE,
    dag_sha256                TEXT NOT NULL DEFAULT '',
    group_idx                 INTEGER,
    attempt_id                INTEGER,
    stage                     TEXT NOT NULL DEFAULT '',
    repo_id                   TEXT NOT NULL DEFAULT '',
    canonical_path            TEXT NOT NULL DEFAULT '',
    registry_digest           TEXT NOT NULL DEFAULT '',
    snapshot_digest           TEXT NOT NULL,
    payload                   JSONB NOT NULL DEFAULT '{}',
    captured_at               TIMESTAMPTZ,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT workspace_snapshots_feature_idempotency_key
        UNIQUE (feature_id, idempotency_key),
    CONSTRAINT workspace_snapshots_feature_digest
        UNIQUE (feature_id, dag_sha256, group_idx, stage, repo_id, snapshot_digest)
);
CREATE INDEX IF NOT EXISTS idx_workspace_snapshots_feature_repo
    ON workspace_snapshots(feature_id, repo_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_workspace_snapshots_execution_row
    ON workspace_snapshots(execution_journal_row_id);
CREATE INDEX IF NOT EXISTS idx_workspace_snapshots_registry_digest
    ON workspace_snapshots(feature_id, registry_digest, id DESC);

CREATE TABLE IF NOT EXISTS sandbox_leases (
    id                        BIGSERIAL PRIMARY KEY,
    feature_id                TEXT NOT NULL REFERENCES features(id),
    idempotency_key           TEXT NOT NULL,
    execution_journal_row_id  BIGINT NOT NULL REFERENCES execution_journal_rows(id) ON DELETE CASCADE,
    dag_sha256                TEXT NOT NULL DEFAULT '',
    group_idx                 INTEGER NOT NULL,
    attempt_no                INTEGER NOT NULL,
    mode                      TEXT NOT NULL,
    status                    TEXT NOT NULL DEFAULT 'allocating',
    lease_owner               TEXT NOT NULL DEFAULT '',
    leased_until              TIMESTAMPTZ NOT NULL,
    lease_version             INTEGER NOT NULL DEFAULT 0,
    base_snapshot_ids         JSONB NOT NULL DEFAULT '[]',
    sandbox_root              TEXT NOT NULL DEFAULT '',
    sandbox_id                TEXT NOT NULL DEFAULT '',
    manifest_path             TEXT NOT NULL DEFAULT '',
    repo_ids                  JSONB NOT NULL DEFAULT '[]',
    base_commits              JSONB NOT NULL DEFAULT '{}',
    task_ids                  JSONB NOT NULL DEFAULT '[]',
    contract_ids              JSONB NOT NULL DEFAULT '[]',
    writable_roots            JSONB NOT NULL DEFAULT '[]',
    readonly_roots            JSONB NOT NULL DEFAULT '[]',
    blocked_roots             JSONB NOT NULL DEFAULT '[]',
    patch_summary_ids         JSONB NOT NULL DEFAULT '[]',
    lease_digest              TEXT NOT NULL,
    payload                   JSONB NOT NULL DEFAULT '{}',
    created_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT sandbox_leases_feature_idempotency_key
        UNIQUE (feature_id, idempotency_key),
    CONSTRAINT sandbox_leases_feature_attempt
        UNIQUE (feature_id, dag_sha256, group_idx, attempt_no, mode),
    CONSTRAINT sandbox_leases_feature_sandbox_id
        UNIQUE (feature_id, sandbox_id),
    CONSTRAINT sandbox_leases_status_check
        CHECK (status IN (
            'allocating', 'allocated', 'binding', 'running', 'capturing',
            'captured', 'released', 'retained', 'failed', 'poisoned'
        )),
    CONSTRAINT sandbox_leases_mode_check
        CHECK (mode IN ('wave', 'task', 'repair', 'canonicalization'))
);
CREATE INDEX IF NOT EXISTS idx_sandbox_leases_recovery
    ON sandbox_leases(status, leased_until, id)
    WHERE status IN (
        'allocating', 'allocated', 'binding', 'running',
        'capturing', 'captured', 'retained'
    );
CREATE INDEX IF NOT EXISTS idx_sandbox_leases_feature_group
    ON sandbox_leases(feature_id, dag_sha256, group_idx, id DESC);
CREATE INDEX IF NOT EXISTS idx_sandbox_leases_execution_row
    ON sandbox_leases(execution_journal_row_id);

CREATE TABLE IF NOT EXISTS sandbox_repo_bindings (
    id                       BIGSERIAL PRIMARY KEY,
    feature_id               TEXT NOT NULL REFERENCES features(id),
    idempotency_key          TEXT NOT NULL,
    sandbox_lease_id         BIGINT NOT NULL REFERENCES sandbox_leases(id) ON DELETE CASCADE,
    repo_id                  TEXT NOT NULL,
    sandbox_repo_root        TEXT NOT NULL DEFAULT '',
    canonical_repo_root      TEXT NOT NULL DEFAULT '',
    base_snapshot_id         BIGINT NOT NULL REFERENCES workspace_snapshots(id),
    base_commit              TEXT NOT NULL DEFAULT '',
    writable                 BOOLEAN NOT NULL DEFAULT TRUE,
    writable_roots           JSONB NOT NULL DEFAULT '[]',
    readonly_roots           JSONB NOT NULL DEFAULT '[]',
    blocked_canonical_roots  JSONB NOT NULL DEFAULT '[]',
    status                   TEXT NOT NULL DEFAULT 'active',
    binding_digest           TEXT NOT NULL,
    payload                  JSONB NOT NULL DEFAULT '{}',
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT sandbox_repo_bindings_feature_idempotency_key
        UNIQUE (feature_id, idempotency_key),
    CONSTRAINT sandbox_repo_bindings_lease_repo
        UNIQUE (sandbox_lease_id, repo_id),
    CONSTRAINT sandbox_repo_bindings_lease_repo_digest
        UNIQUE (sandbox_lease_id, repo_id, binding_digest),
    CONSTRAINT sandbox_repo_bindings_status_check
        CHECK (status IN ('active', 'released', 'poisoned'))
);
CREATE INDEX IF NOT EXISTS idx_sandbox_repo_bindings_lease
    ON sandbox_repo_bindings(sandbox_lease_id, repo_id);
CREATE INDEX IF NOT EXISTS idx_sandbox_repo_bindings_recovery
    ON sandbox_repo_bindings(feature_id, status, updated_at DESC)
    WHERE status IN ('active', 'poisoned');
CREATE INDEX IF NOT EXISTS idx_sandbox_repo_bindings_snapshot
    ON sandbox_repo_bindings(base_snapshot_id);

CREATE TABLE IF NOT EXISTS runtime_workspace_bindings (
    id                     BIGSERIAL PRIMARY KEY,
    feature_id             TEXT NOT NULL REFERENCES features(id),
    idempotency_key        TEXT NOT NULL,
    sandbox_lease_id       BIGINT NOT NULL REFERENCES sandbox_leases(id) ON DELETE CASCADE,
    attempt_id             BIGINT NOT NULL,
    runtime_name           TEXT NOT NULL,
    cwd                    TEXT NOT NULL DEFAULT '',
    workspace_override     TEXT NOT NULL DEFAULT '',
    manifest_path          TEXT NOT NULL DEFAULT '',
    repo_roots             JSONB NOT NULL DEFAULT '{}',
    writable_roots         JSONB NOT NULL DEFAULT '[]',
    readonly_roots         JSONB NOT NULL DEFAULT '[]',
    blocked_roots          JSONB NOT NULL DEFAULT '[]',
    env                    JSONB NOT NULL DEFAULT '{}',
    role_metadata          JSONB NOT NULL DEFAULT '{}',
    role_metadata_digest   TEXT NOT NULL,
    status                 TEXT NOT NULL DEFAULT 'bound',
    binding_digest         TEXT NOT NULL,
    payload                JSONB NOT NULL DEFAULT '{}',
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT runtime_workspace_bindings_feature_idempotency_key
        UNIQUE (feature_id, idempotency_key),
    CONSTRAINT runtime_workspace_bindings_lease_runtime_attempt
        UNIQUE (sandbox_lease_id, runtime_name, attempt_id),
    CONSTRAINT runtime_workspace_bindings_lease_runtime_attempt_digest
        UNIQUE (sandbox_lease_id, runtime_name, attempt_id, binding_digest),
    CONSTRAINT runtime_workspace_bindings_status_check
        CHECK (status IN ('bound', 'started', 'finished', 'failed', 'poisoned'))
);
CREATE INDEX IF NOT EXISTS idx_runtime_workspace_bindings_attempt
    ON runtime_workspace_bindings(attempt_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_runtime_workspace_bindings_lease
    ON runtime_workspace_bindings(sandbox_lease_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_runtime_workspace_bindings_recovery
    ON runtime_workspace_bindings(feature_id, status, updated_at DESC)
    WHERE status IN ('bound', 'started', 'failed', 'poisoned');

CREATE TABLE IF NOT EXISTS task_deliverable_contracts (
    id                        BIGSERIAL PRIMARY KEY,
    feature_id                TEXT NOT NULL REFERENCES features(id),
    idempotency_key           TEXT NOT NULL,
    execution_journal_row_id  BIGINT REFERENCES execution_journal_rows(id) ON DELETE CASCADE,
    dag_sha256                TEXT NOT NULL DEFAULT '',
    source_dag_artifact_id    BIGINT REFERENCES artifacts(id),
    source_dag_sha256         TEXT NOT NULL DEFAULT '',
    group_idx                 INTEGER NOT NULL,
    task_id                   TEXT NOT NULL,
    repo_id                   TEXT NOT NULL DEFAULT '',
    repo_path                 TEXT NOT NULL DEFAULT '',
    required_paths            JSONB NOT NULL DEFAULT '[]',
    allowed_paths             JSONB NOT NULL DEFAULT '[]',
    read_only_paths           JSONB NOT NULL DEFAULT '[]',
    forbidden_paths           JSONB NOT NULL DEFAULT '[]',
    generated_outputs         JSONB NOT NULL DEFAULT '[]',
    acceptance_criteria       JSONB NOT NULL DEFAULT '[]',
    verification_gates        JSONB NOT NULL DEFAULT '[]',
    execution_policy          JSONB NOT NULL DEFAULT '{}',
    non_goals                 JSONB NOT NULL DEFAULT '[]',
    dependency_task_ids       JSONB NOT NULL DEFAULT '[]',
    unknown_write_set         BOOLEAN NOT NULL DEFAULT FALSE,
    compile_warnings          JSONB NOT NULL DEFAULT '[]',
    normalized_contract_json  JSONB NOT NULL DEFAULT '{}',
    contract_digest           TEXT NOT NULL,
    status                    TEXT NOT NULL DEFAULT 'active',
    payload                   JSONB NOT NULL DEFAULT '{}',
    created_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT task_deliverable_contracts_feature_idempotency_key
        UNIQUE (feature_id, idempotency_key),
    CONSTRAINT task_deliverable_contracts_scope_digest
        UNIQUE (feature_id, dag_sha256, group_idx, task_id, contract_digest),
    CONSTRAINT task_deliverable_contracts_identity
        UNIQUE (id, feature_id, dag_sha256, group_idx, task_id),
    CONSTRAINT task_deliverable_contracts_status_check
        CHECK (status IN ('active', 'superseded', 'cancelled'))
);
CREATE UNIQUE INDEX IF NOT EXISTS uniq_task_contracts_active_scope
    ON task_deliverable_contracts(feature_id, dag_sha256, group_idx, task_id)
    WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_task_contracts_active_group
    ON task_deliverable_contracts(feature_id, dag_sha256, group_idx, task_id)
    WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_task_contracts_source_artifact
    ON task_deliverable_contracts(source_dag_artifact_id)
    WHERE source_dag_artifact_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_task_contracts_execution_row
    ON task_deliverable_contracts(execution_journal_row_id)
    WHERE execution_journal_row_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS evidence_nodes (
    id                        BIGSERIAL PRIMARY KEY,
    feature_id                TEXT NOT NULL REFERENCES features(id),
    idempotency_key           TEXT NOT NULL,
    execution_journal_row_id  BIGINT REFERENCES execution_journal_rows(id) ON DELETE CASCADE,
    attempt_id                BIGINT,
    contract_id               BIGINT REFERENCES task_deliverable_contracts(id),
    snapshot_id               BIGINT REFERENCES workspace_snapshots(id),
    group_idx                 INTEGER,
    stage                     TEXT NOT NULL DEFAULT '',
    kind                      TEXT NOT NULL,
    name                      TEXT NOT NULL DEFAULT '',
    status                    TEXT NOT NULL DEFAULT 'approved',
    deterministic             BOOLEAN NOT NULL DEFAULT TRUE,
    source_ref                TEXT NOT NULL DEFAULT '',
    artifact_id               BIGINT REFERENCES artifacts(id),
    artifact_key              TEXT NOT NULL DEFAULT '',
    event_id                  BIGINT REFERENCES events(id),
    input_refs                JSONB NOT NULL DEFAULT '[]',
    output_refs               JSONB NOT NULL DEFAULT '[]',
    failure_id                BIGINT,
    verdict_id                BIGINT REFERENCES evidence_nodes(id),
    content_hash              TEXT NOT NULL,
    summary                   TEXT NOT NULL DEFAULT '',
    metadata                  JSONB NOT NULL DEFAULT '{}',
    payload                   JSONB NOT NULL DEFAULT '{}',
    started_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at               TIMESTAMPTZ,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT evidence_nodes_feature_idempotency_key
        UNIQUE (feature_id, idempotency_key),
    CONSTRAINT evidence_nodes_status_check
        CHECK (status IN ('pending', 'running', 'approved', 'rejected', 'failed', 'skipped')),
    CONSTRAINT evidence_nodes_kind_check
        CHECK (kind IN (
            'context_package', 'runtime_invocation', 'raw_output',
            'structured_result', 'runtime_failure_context',
            'failure_route_decision', 'repair_request', 'retry_request',
            'repair_outcome', 'retry_outcome',
            'sandbox_patch_summary', 'contract_verdict',
            'gate_request', 'candidate_manifest', 'deterministic_gate',
            'raw_verifier', 'expanded_lens', 'aggregate_verdict',
            'merge_gate', 'checkpoint_gate',
            'merge_proof', 'commit_proof'
        ))
);
CREATE INDEX IF NOT EXISTS idx_evidence_feature_kind
    ON evidence_nodes(feature_id, kind, id DESC);
CREATE INDEX IF NOT EXISTS idx_evidence_feature_group_stage
    ON evidence_nodes(feature_id, group_idx, stage, kind, id DESC);
CREATE INDEX IF NOT EXISTS idx_evidence_status
    ON evidence_nodes(feature_id, status, id DESC)
    WHERE status IN ('pending', 'running', 'rejected', 'failed');
CREATE INDEX IF NOT EXISTS idx_evidence_attempt
    ON evidence_nodes(attempt_id, id)
    WHERE attempt_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_evidence_artifact
    ON evidence_nodes(artifact_id)
    WHERE artifact_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_evidence_event
    ON evidence_nodes(event_id)
    WHERE event_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_evidence_contract
    ON evidence_nodes(contract_id, id)
    WHERE contract_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_evidence_content_hash
    ON evidence_nodes(feature_id, content_hash);

CREATE TABLE IF NOT EXISTS evidence_graphs (
    id                          BIGSERIAL PRIMARY KEY,
    feature_id                  TEXT NOT NULL REFERENCES features(id),
    idempotency_key             TEXT NOT NULL,
    execution_journal_row_id    BIGINT NOT NULL REFERENCES execution_journal_rows(id) ON DELETE CASCADE,
    aggregate_evidence_node_id  BIGINT NOT NULL REFERENCES evidence_nodes(id),
    projection_key              TEXT NOT NULL,
    projection_sha256           TEXT NOT NULL,
    dag_sha256                  TEXT NOT NULL DEFAULT '',
    group_idx                   INTEGER,
    stage                       TEXT NOT NULL DEFAULT '',
    proof_digest                TEXT NOT NULL DEFAULT '',
    graph_payload_digest        TEXT NOT NULL,
    required_edge_ids           JSONB NOT NULL DEFAULT '[]',
    payload                     JSONB NOT NULL DEFAULT '{}',
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT evidence_graphs_feature_idempotency_key
        UNIQUE (feature_id, idempotency_key),
    CONSTRAINT evidence_graphs_row_projection_digest
        UNIQUE (execution_journal_row_id, projection_key, graph_payload_digest)
);
CREATE INDEX IF NOT EXISTS idx_evidence_graphs_feature_projection
    ON evidence_graphs(feature_id, projection_key, id DESC);
CREATE INDEX IF NOT EXISTS idx_evidence_graphs_feature_group
    ON evidence_graphs(feature_id, dag_sha256, group_idx, stage, id DESC);
CREATE INDEX IF NOT EXISTS idx_evidence_graphs_aggregate
    ON evidence_graphs(aggregate_evidence_node_id);

CREATE TABLE IF NOT EXISTS evidence_edges (
    id                     BIGSERIAL PRIMARY KEY,
    feature_id             TEXT NOT NULL REFERENCES features(id),
    idempotency_key        TEXT NOT NULL,
    evidence_graph_id      BIGINT NOT NULL REFERENCES evidence_graphs(id) ON DELETE CASCADE,
    graph_edge_id          TEXT NOT NULL,
    from_graph_node_id     TEXT NOT NULL DEFAULT '',
    to_graph_node_id       TEXT NOT NULL DEFAULT '',
    from_evidence_node_id  BIGINT REFERENCES evidence_nodes(id),
    to_evidence_node_id    BIGINT REFERENCES evidence_nodes(id),
    kind                   TEXT NOT NULL DEFAULT '',
    required               BOOLEAN NOT NULL DEFAULT FALSE,
    edge_digest            TEXT NOT NULL,
    payload                JSONB NOT NULL DEFAULT '{}',
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT evidence_edges_feature_idempotency_key
        UNIQUE (feature_id, idempotency_key),
    CONSTRAINT evidence_edges_graph_edge_id
        UNIQUE (evidence_graph_id, graph_edge_id)
);
CREATE INDEX IF NOT EXISTS idx_evidence_edges_graph
    ON evidence_edges(evidence_graph_id, id);
CREATE INDEX IF NOT EXISTS idx_evidence_edges_from_node
    ON evidence_edges(from_evidence_node_id)
    WHERE from_evidence_node_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_evidence_edges_to_node
    ON evidence_edges(to_evidence_node_id)
    WHERE to_evidence_node_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS public_display_jobs (
    id                    BIGSERIAL PRIMARY KEY,
    job_id                TEXT NOT NULL UNIQUE,
    feature_id            TEXT NOT NULL REFERENCES features(id),
    job_type              TEXT NOT NULL,
    reason                TEXT NOT NULL,
    group_idx             INTEGER,
    priority              INTEGER NOT NULL DEFAULT 100,
    source_artifact_keys  JSONB NOT NULL DEFAULT '[]',
    source_digests        JSONB NOT NULL DEFAULT '{}',
    payload               JSONB NOT NULL DEFAULT '{}',
    idempotency_key       TEXT NOT NULL UNIQUE,
    status                TEXT NOT NULL DEFAULT 'pending',
    attempt_count         INTEGER NOT NULL DEFAULT 0,
    result_artifact_key   TEXT,
    last_error            TEXT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at            TIMESTAMPTZ,
    completed_at          TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_public_display_jobs_status
    ON public_display_jobs(status, priority, id);
CREATE INDEX IF NOT EXISTS idx_public_display_jobs_feature
    ON public_display_jobs(feature_id, id DESC);

CREATE TABLE IF NOT EXISTS sessions (
    session_key TEXT PRIMARY KEY,
    session_id  TEXT,
    metadata    JSONB NOT NULL DEFAULT '{}',
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Slice 08: durable merge queue. The queue is the only canonical product
-- mutation path. attempt_id/failure_id/checkpoint_projection_id are plain
-- BIGINT (no FK) to match the as-built journal: there is no execution_attempts
-- or typed_failures table, and projection links follow the evidence_nodes
-- failure_id convention.
CREATE TABLE IF NOT EXISTS merge_queue_items (
    id                          BIGSERIAL PRIMARY KEY,
    feature_id                  TEXT NOT NULL REFERENCES features(id),
    dag_sha256                  TEXT NOT NULL,
    group_idx                   INTEGER NOT NULL,
    repo_id                     TEXT NOT NULL DEFAULT '',
    repo_path                   TEXT NOT NULL DEFAULT '',
    attempt_id                  BIGINT,
    contract_ids                JSONB NOT NULL DEFAULT '[]',
    patch_evidence_ids          JSONB NOT NULL DEFAULT '[]',
    gate_evidence_ids           JSONB NOT NULL DEFAULT '[]',
    pre_queue_gate_evidence_id  BIGINT REFERENCES evidence_nodes(id),
    post_apply_gate_evidence_id BIGINT REFERENCES evidence_nodes(id),
    base_commit                 TEXT NOT NULL,
    head_commit                 TEXT NOT NULL DEFAULT '',
    status                      TEXT NOT NULL DEFAULT 'queued',
    priority                    INTEGER NOT NULL DEFAULT 100,
    lease_owner                 TEXT,
    leased_until                TIMESTAMPTZ,
    lease_version               INTEGER NOT NULL DEFAULT 0,
    result_commit               TEXT NOT NULL DEFAULT '',
    merge_proof_evidence_id     BIGINT REFERENCES evidence_nodes(id),
    commit_proof_evidence_id    BIGINT REFERENCES evidence_nodes(id),
    checkpoint_gate_evidence_id BIGINT REFERENCES evidence_nodes(id),
    checkpoint_evidence_id      BIGINT REFERENCES evidence_nodes(id),
    checkpoint_projection_id    BIGINT,
    checkpoint_coverage_digest  TEXT NOT NULL DEFAULT '',
    checkpoint_body_sha256      TEXT NOT NULL DEFAULT '',
    retry_of_queue_item_id      BIGINT REFERENCES merge_queue_items(id),
    failure_id                  BIGINT,
    request_digest              TEXT NOT NULL,
    idempotency_key             TEXT NOT NULL UNIQUE,
    payload                     JSONB NOT NULL DEFAULT '{}',
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT merge_queue_items_identity
        UNIQUE (id, feature_id, dag_sha256, group_idx),
    CONSTRAINT merge_queue_items_status_check
        CHECK (status IN (
            'queued', 'leased', 'applying', 'verifying', 'committing',
            'integrated', 'checkpointing', 'done', 'failed', 'poisoned',
            'cancelled'
        )),
    CONSTRAINT merge_queue_items_pre_queue_gate_check
        CHECK (status IN ('failed', 'poisoned', 'cancelled')
               OR pre_queue_gate_evidence_id IS NOT NULL),
    CONSTRAINT merge_queue_items_merge_proof_check
        CHECK (status NOT IN (
                   'verifying', 'committing', 'integrated', 'checkpointing',
                   'done'
               )
               OR merge_proof_evidence_id IS NOT NULL),
    CONSTRAINT merge_queue_items_post_apply_gate_check
        CHECK (status NOT IN (
                   'committing', 'integrated', 'checkpointing', 'done'
               )
               OR post_apply_gate_evidence_id IS NOT NULL),
    CONSTRAINT merge_queue_items_commit_proof_check
        CHECK (status NOT IN ('integrated', 'checkpointing', 'done')
               OR commit_proof_evidence_id IS NOT NULL),
    CONSTRAINT merge_queue_items_checkpoint_digests_check
        CHECK (status NOT IN ('checkpointing', 'done')
               OR (checkpoint_coverage_digest <> ''
                   AND checkpoint_body_sha256 <> '')),
    CONSTRAINT merge_queue_items_done_check
        CHECK (status <> 'done'
               OR (checkpoint_gate_evidence_id IS NOT NULL
                   AND checkpoint_evidence_id IS NOT NULL
                   AND checkpoint_projection_id IS NOT NULL
                   AND result_commit <> ''))
);
CREATE INDEX IF NOT EXISTS idx_merge_queue_claim
    ON merge_queue_items(feature_id, status, priority, id)
    WHERE status IN ('queued', 'leased');
CREATE INDEX IF NOT EXISTS idx_merge_queue_lease_expiry
    ON merge_queue_items(leased_until, id)
    WHERE status = 'leased';
CREATE INDEX IF NOT EXISTS idx_merge_queue_active_recovery
    ON merge_queue_items(feature_id, leased_until, status, id)
    WHERE status IN ('applying', 'verifying', 'committing', 'checkpointing');
CREATE INDEX IF NOT EXISTS idx_merge_queue_group
    ON merge_queue_items(feature_id, dag_sha256, group_idx, id DESC);
CREATE INDEX IF NOT EXISTS idx_merge_queue_result_commit
    ON merge_queue_items(feature_id, result_commit)
    WHERE result_commit <> '';
CREATE INDEX IF NOT EXISTS idx_merge_queue_retry_source
    ON merge_queue_items(retry_of_queue_item_id)
    WHERE retry_of_queue_item_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uniq_merge_queue_retry_source_active
    ON merge_queue_items(retry_of_queue_item_id)
    WHERE retry_of_queue_item_id IS NOT NULL AND status <> 'cancelled';

CREATE TABLE IF NOT EXISTS merge_queue_task_coverage (
    id               BIGSERIAL PRIMARY KEY,
    queue_item_id    BIGINT NOT NULL REFERENCES merge_queue_items(id) ON DELETE RESTRICT,
    feature_id       TEXT NOT NULL REFERENCES features(id),
    dag_sha256       TEXT NOT NULL,
    group_idx        INTEGER NOT NULL,
    task_id          TEXT NOT NULL,
    contract_id      BIGINT NOT NULL REFERENCES task_deliverable_contracts(id),
    coverage_digest  TEXT NOT NULL,
    idempotency_key  TEXT NOT NULL UNIQUE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT merge_queue_task_coverage_item_task
        UNIQUE (queue_item_id, task_id),
    CONSTRAINT merge_queue_task_coverage_parent_fkey
        FOREIGN KEY (queue_item_id, feature_id, dag_sha256, group_idx)
        REFERENCES merge_queue_items(id, feature_id, dag_sha256, group_idx),
    CONSTRAINT merge_queue_task_coverage_contract_fkey
        FOREIGN KEY (contract_id, feature_id, dag_sha256, group_idx, task_id)
        REFERENCES task_deliverable_contracts(
            id, feature_id, dag_sha256, group_idx, task_id
        )
);
CREATE INDEX IF NOT EXISTS idx_merge_queue_task_coverage_group
    ON merge_queue_task_coverage(feature_id, dag_sha256, group_idx, task_id, queue_item_id);
CREATE INDEX IF NOT EXISTS idx_merge_queue_task_coverage_item
    ON merge_queue_task_coverage(queue_item_id, id);

CREATE TABLE IF NOT EXISTS merge_queue_repo_targets (
    id                   BIGSERIAL PRIMARY KEY,
    queue_item_id        BIGINT NOT NULL REFERENCES merge_queue_items(id) ON DELETE RESTRICT,
    feature_id           TEXT NOT NULL REFERENCES features(id),
    dag_sha256           TEXT NOT NULL,
    group_idx            INTEGER NOT NULL,
    repo_id              TEXT NOT NULL,
    repo_path            TEXT NOT NULL,
    base_commit          TEXT NOT NULL,
    expected_head        TEXT NOT NULL DEFAULT '',
    pre_apply_head       TEXT NOT NULL DEFAULT '',
    applied_head         TEXT NOT NULL DEFAULT '',
    result_commit        TEXT NOT NULL DEFAULT '',
    tree_sha             TEXT NOT NULL DEFAULT '',
    no_dirty_snapshot_id BIGINT REFERENCES workspace_snapshots(id),
    status               TEXT NOT NULL DEFAULT 'pending',
    target_digest        TEXT NOT NULL,
    idempotency_key      TEXT NOT NULL UNIQUE,
    payload              JSONB NOT NULL DEFAULT '{}',
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT merge_queue_repo_targets_status_check
        CHECK (status IN (
            'pending', 'pre_apply_recorded', 'applied', 'committed', 'clean',
            'failed', 'poisoned'
        )),
    CONSTRAINT merge_queue_repo_targets_pre_apply_check
        CHECK (status NOT IN (
                   'pre_apply_recorded', 'applied', 'committed', 'clean'
               )
               OR pre_apply_head <> ''),
    CONSTRAINT merge_queue_repo_targets_applied_check
        CHECK (status NOT IN ('applied', 'committed', 'clean')
               OR applied_head <> ''),
    CONSTRAINT merge_queue_repo_targets_committed_check
        CHECK (status NOT IN ('committed', 'clean')
               OR (result_commit <> '' AND tree_sha <> '')),
    CONSTRAINT merge_queue_repo_targets_clean_check
        CHECK (status <> 'clean' OR no_dirty_snapshot_id IS NOT NULL),
    CONSTRAINT merge_queue_repo_targets_item_repo
        UNIQUE (queue_item_id, repo_id),
    CONSTRAINT merge_queue_repo_targets_item_repo_digest
        UNIQUE (queue_item_id, repo_id, target_digest),
    CONSTRAINT merge_queue_repo_targets_parent_fkey
        FOREIGN KEY (queue_item_id, feature_id, dag_sha256, group_idx)
        REFERENCES merge_queue_items(id, feature_id, dag_sha256, group_idx)
);
CREATE INDEX IF NOT EXISTS idx_merge_queue_repo_targets_group
    ON merge_queue_repo_targets(feature_id, dag_sha256, group_idx, queue_item_id, repo_id);
CREATE INDEX IF NOT EXISTS idx_merge_queue_repo_targets_recovery
    ON merge_queue_repo_targets(feature_id, status, updated_at DESC)
    WHERE status IN ('pre_apply_recorded', 'applied', 'committed');

-- Slice 09: regroup overlay and scheduler feedback. The typed
-- `execution_regroup_overlays` row is canonical for new control-plane regroup
-- decisions; the legacy `dag-regroup:*` artifacts are synchronous
-- compatibility projections of typed state. The root `dag` is never
-- overwritten. `base_dag_artifact_id`/`latest_successful_validation_id`/
-- `active_marker_projection_id` are plain BIGINT (no FK) to match the
-- as-built journal convention (artifacts and projection links are not modelled
-- as a FK-bearing table; see the Slice 08 `merge_queue_items` comment).
CREATE TABLE IF NOT EXISTS execution_regroup_overlays (
    id                              BIGSERIAL PRIMARY KEY,
    feature_id                      TEXT NOT NULL REFERENCES features(id),
    overlay_id                      TEXT NOT NULL,
    overlay_slug                    TEXT NOT NULL,
    status                          TEXT NOT NULL,
    artifact_key                    TEXT NOT NULL,
    source_dag_key                  TEXT NOT NULL,
    base_dag_artifact_id            BIGINT NOT NULL,
    base_dag_sha256                 TEXT NOT NULL,
    checkpointed_group              INTEGER NOT NULL,
    group_idx_offset                INTEGER NOT NULL,
    last_original_group             INTEGER,
    overlay_sha256                  TEXT NOT NULL,
    validation_digest               TEXT NOT NULL,
    latest_successful_validation_id BIGINT,
    active_marker_projection_id     BIGINT,
    payload_json                    JSONB NOT NULL,
    compatibility_artifact_ids      JSONB NOT NULL DEFAULT '[]',
    activated_at                    TIMESTAMPTZ,
    rolled_back_at                  TIMESTAMPTZ,
    idempotency_key                 TEXT NOT NULL UNIQUE,
    created_at                      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT execution_regroup_overlays_status_check
        CHECK (status IN (
            'staged', 'active', 'rolled_back', 'superseded', 'rejected'
        )),
    CONSTRAINT execution_regroup_overlays_feature_overlay
        UNIQUE (feature_id, overlay_id)
);
-- At most one `active` overlay per feature.
CREATE UNIQUE INDEX IF NOT EXISTS uniq_regroup_overlay_active
    ON execution_regroup_overlays(feature_id)
    WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_regroup_overlay_base
    ON execution_regroup_overlays(
        feature_id, source_dag_key, base_dag_artifact_id, base_dag_sha256
    );
CREATE INDEX IF NOT EXISTS idx_regroup_overlay_status
    ON execution_regroup_overlays(feature_id, status, updated_at DESC);

-- Typed validation attempts for `execution_regroup_overlays`. One row per
-- `validate_overlay` run (09b); re-validating the same overlay id with the
-- same digest is idempotent on `idempotency_key`.
CREATE TABLE IF NOT EXISTS execution_regroup_validations (
    id                 BIGSERIAL PRIMARY KEY,
    feature_id         TEXT NOT NULL REFERENCES features(id),
    overlay_id         TEXT NOT NULL,
    overlay_row_id     BIGINT NOT NULL REFERENCES execution_regroup_overlays(id),
    valid              BOOLEAN NOT NULL,
    reason             TEXT NOT NULL DEFAULT '',
    validation_digest  TEXT NOT NULL,
    details_json       JSONB NOT NULL DEFAULT '{}',
    evidence_ids       JSONB NOT NULL DEFAULT '[]',
    idempotency_key    TEXT NOT NULL UNIQUE,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_regroup_validation_overlay
    ON execution_regroup_validations(feature_id, overlay_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_regroup_validation_valid
    ON execution_regroup_validations(feature_id, valid, created_at DESC);

-- Typed scheduler-feedback lane/barrier windows + recommendation payloads
-- (09d). Advisory evidence only: feedback never writes an active marker.
CREATE TABLE IF NOT EXISTS execution_scheduler_feedback (
    id                  BIGSERIAL PRIMARY KEY,
    feedback_id         TEXT NOT NULL,
    feature_id          TEXT NOT NULL REFERENCES features(id),
    window_start_group  INTEGER NOT NULL,
    window_end_group    INTEGER NOT NULL,
    lane                TEXT NOT NULL,
    barrier             TEXT NOT NULL,
    sample_count        INTEGER NOT NULL DEFAULT 0,
    recommended_cap     INTEGER NOT NULL,
    current_cap         INTEGER NOT NULL,
    data_quality        TEXT NOT NULL,
    confidence          TEXT NOT NULL,
    metric_ids          JSONB NOT NULL DEFAULT '[]',
    evidence_ids        JSONB NOT NULL DEFAULT '[]',
    payload_json        JSONB NOT NULL,
    idempotency_key     TEXT NOT NULL UNIQUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT execution_scheduler_feedback_data_quality_check
        CHECK (data_quality IN (
            'sufficient', 'insufficient', 'mixed', 'stale'
        )),
    CONSTRAINT execution_scheduler_feedback_confidence_check
        CHECK (confidence IN ('low', 'medium', 'high'))
);
CREATE INDEX IF NOT EXISTS idx_scheduler_feedback_window
    ON execution_scheduler_feedback(
        feature_id, window_start_group, window_end_group, created_at DESC
    );
CREATE INDEX IF NOT EXISTS idx_scheduler_feedback_lane_barrier
    ON execution_scheduler_feedback(feature_id, lane, barrier, created_at DESC);
