-- 공식 규정 색인 스키마 (#24). 기존 PDF 초안 테이블(hs_documents, hs_chunks)과 독립이다.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS hs_reg_revisions (
    revision_id text PRIMARY KEY,
    regulation_id text NOT NULL,
    title text NOT NULL,
    official_url text NOT NULL,
    text_hash text NOT NULL,
    visibility text NOT NULL DEFAULT 'public' CHECK (visibility IN ('public', 'private')),
    is_current boolean NOT NULL DEFAULT false,
    embedding_model text NOT NULL,
    chunk_params jsonb NOT NULL,
    chunk_count integer NOT NULL CHECK (chunk_count > 0),
    indexed_at timestamptz NOT NULL DEFAULT now()
);

-- 규정마다 현재 개정은 최대 하나다.
CREATE UNIQUE INDEX IF NOT EXISTS hs_reg_revisions_one_current
    ON hs_reg_revisions (regulation_id) WHERE is_current;
CREATE INDEX IF NOT EXISTS hs_reg_revisions_regulation ON hs_reg_revisions (regulation_id);

CREATE TABLE IF NOT EXISTS hs_reg_chunks (
    id text PRIMARY KEY,
    revision_id text NOT NULL REFERENCES hs_reg_revisions (revision_id) ON DELETE CASCADE,
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    regulation_id text NOT NULL,
    article_id text NOT NULL,
    kind text NOT NULL,
    chapter text NOT NULL,
    source_start integer NOT NULL CHECK (source_start >= 0),
    source_end integer NOT NULL CHECK (source_end > source_start),
    content text NOT NULL,
    embedding_text text NOT NULL,
    refs jsonb NOT NULL DEFAULT '[]',
    deleted boolean NOT NULL DEFAULT false,
    embedding vector(384) NOT NULL,
    UNIQUE (revision_id, ordinal)
);

CREATE INDEX IF NOT EXISTS hs_reg_chunks_embedding
    ON hs_reg_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS hs_reg_chunks_content_trgm
    ON hs_reg_chunks USING gin (content gin_trgm_ops);
CREATE INDEX IF NOT EXISTS hs_reg_chunks_article ON hs_reg_chunks (revision_id, article_id);
CREATE INDEX IF NOT EXISTS hs_reg_chunks_regulation ON hs_reg_chunks (regulation_id);
