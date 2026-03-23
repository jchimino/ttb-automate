-- TTB Compliance Audit Log
-- Every assessment is written here — immutable record

-- pgvector extension — required for CFR RAG embeddings
CREATE EXTENSION IF NOT EXISTS vector;

-- ── Audit log ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS assessments (
    id              SERIAL PRIMARY KEY,
    submission_id   TEXT NOT NULL,
    decision        TEXT NOT NULL,          -- APPROVE | REVIEW | DENY
    brand_name      TEXT,
    model           TEXT,
    strategy        TEXT,                   -- vision | reconcile
    fields_json     TEXT,
    reasoning       TEXT,
    raw_response    TEXT,                   -- complete LLM output, never truncated
    assessed_at     TIMESTAMP DEFAULT NOW(),
    human_decision  TEXT,                   -- auditor override: APPROVE | DENY
    reviewed_by     TEXT,
    reviewed_at     TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_sub  ON assessments(submission_id);
CREATE INDEX IF NOT EXISTS idx_dec  ON assessments(decision);
CREATE INDEX IF NOT EXISTS idx_time ON assessments(assessed_at);

-- ── CFR RAG chunks ────────────────────────────────────────────────────────────
-- Stores chunked 27 CFR text with embeddings for retrieval-augmented assessment.
-- Populated at assess-service startup by cfr_loader.py.
-- Embedding dimension 384 matches nomic-embed-text via Ollama.
CREATE TABLE IF NOT EXISTS cfr_chunks (
    id           SERIAL PRIMARY KEY,
    cfr_part     TEXT NOT NULL,             -- e.g. "4", "5", "7", "16"
    section      TEXT NOT NULL,             -- e.g. "4.21", "5.35", "Part 16"
    commodity    TEXT,                      -- Wine | Spirits | Malt | NULL (applies to all)
    topic        TEXT NOT NULL,             -- e.g. "class_type", "health_warning", "abv"
    chunk_text   TEXT NOT NULL,             -- the actual regulation text
    embedding    vector(384),               -- nomic-embed-text embedding
    source       TEXT DEFAULT 'eCFR',       -- source of the text
    loaded_at    TIMESTAMP DEFAULT NOW()
);

-- IVFFlat index for fast approximate nearest-neighbour search
-- lists=50 is appropriate for a few thousand chunks (all of Parts 4, 5, 7, 16)
CREATE INDEX IF NOT EXISTS idx_cfr_embedding
    ON cfr_chunks USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 50);

CREATE INDEX IF NOT EXISTS idx_cfr_part     ON cfr_chunks(cfr_part);
CREATE INDEX IF NOT EXISTS idx_cfr_topic    ON cfr_chunks(topic);
CREATE INDEX IF NOT EXISTS idx_cfr_commodity ON cfr_chunks(commodity);
