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
