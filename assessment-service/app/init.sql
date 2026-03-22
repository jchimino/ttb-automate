-- TTB Compliance Audit Log
-- Every assessment is written here — immutable record

CREATE TABLE IF NOT EXISTS assessments (
    id              SERIAL PRIMARY KEY,
    submission_id   TEXT        NOT NULL,
    decision        TEXT        NOT NULL,   -- APPROVE | REVIEW | DENY
    brand_name      TEXT,
    model           TEXT,
    strategy        TEXT,                   -- vision | reconcile
    fields_json     TEXT,
    reasoning       TEXT,
    raw_response    TEXT,                   -- complete LLM output, never truncated
    assessed_at     TIMESTAMP   DEFAULT NOW(),
    human_decision  TEXT,                   -- auditor override: APPROVE | DENY
    reviewed_by     TEXT,
    reviewed_at     TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_sub    ON assessments(submission_id);
CREATE INDEX IF NOT EXISTS idx_dec    ON assessments(decision);
CREATE INDEX IF NOT EXISTS idx_time   ON assessments(assessed_at);
