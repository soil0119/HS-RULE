CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS hs_documents (
    id text PRIMARY KEY,
    title text NOT NULL,
    filename text NOT NULL,
    visibility text NOT NULL CHECK (visibility IN ('public', 'private')),
    content_hash text NOT NULL UNIQUE,
    page_count integer NOT NULL CHECK (page_count > 0),
    pages jsonb NOT NULL,
    warnings jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS hs_chunks (
    id text PRIMARY KEY,
    document_id text NOT NULL REFERENCES hs_documents(id) ON DELETE CASCADE,
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    page_start integer NOT NULL CHECK (page_start > 0),
    page_end integer NOT NULL CHECK (page_end >= page_start),
    article text NOT NULL,
    content text NOT NULL,
    embedding vector(384) NOT NULL,
    UNIQUE (document_id, ordinal)
);
