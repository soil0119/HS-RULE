-- 기준선: PR #33의 DocumentStore.SCHEMA와 같은 문장이다.
-- 이미 테이블이 있는 DB에서는 아무것도 바꾸지 않고 이력(web_migrations)에만 기록된다.
CREATE TABLE IF NOT EXISTS web_documents (
    id text PRIMARY KEY CHECK (id ~ '^[0-9a-f]{32}$'),
    title text NOT NULL,
    filename text NOT NULL,
    visibility text NOT NULL CHECK (visibility IN ('private', 'public')),
    content_hash text NOT NULL CONSTRAINT web_documents_content_hash_unique UNIQUE,
    page_count integer NOT NULL CHECK (page_count BETWEEN 1 AND 1000),
    warnings text[] NOT NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS web_document_pages (
    document_id text NOT NULL REFERENCES web_documents(id) ON DELETE CASCADE,
    page_number integer NOT NULL CHECK (page_number BETWEEN 1 AND 1000),
    text text NOT NULL,
    PRIMARY KEY (document_id, page_number)
);
