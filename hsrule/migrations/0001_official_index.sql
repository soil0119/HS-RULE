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
    -- 키워드 검색용 정규화 본문(NFKC + 소문자). 표의 A⁺ 같은 상첨자와 전각 문자를 A+ 형태로 맞춘다.
    -- 인용은 content와 원문 오프셋으로만 하고, 이 열은 검색에만 쓴다.
    search_text text NOT NULL,
    refs jsonb NOT NULL DEFAULT '[]',
    deleted boolean NOT NULL DEFAULT false,
    -- 아래 두 열은 소속 개정의 visibility/is_current를 그대로 복사한다(같은 트랜잭션에서 갱신).
    -- 필터와 거리 정렬을 한 테이블에서 처리해야 pgvector가 벡터 인덱스를 쓸 수 있다.
    visibility text NOT NULL CHECK (visibility IN ('public', 'private')),
    searchable boolean NOT NULL,
    embedding vector(384) NOT NULL,
    UNIQUE (revision_id, ordinal)
);

-- 검색 대상(현재 개정·미삭제)만 담은 부분 인덱스. 권한 필터는 같은 테이블의 열이므로
-- 인덱스 스캔 단계에서 적용되고, LIMIT 이후에 걸러지지 않는다.
CREATE INDEX IF NOT EXISTS hs_reg_chunks_embedding
    ON hs_reg_chunks USING hnsw (embedding vector_cosine_ops) WHERE searchable AND NOT deleted;
CREATE INDEX IF NOT EXISTS hs_reg_chunks_search_trgm
    ON hs_reg_chunks USING gin (search_text gin_trgm_ops) WHERE searchable AND NOT deleted;
CREATE INDEX IF NOT EXISTS hs_reg_chunks_article ON hs_reg_chunks (revision_id, article_id);
CREATE INDEX IF NOT EXISTS hs_reg_chunks_regulation ON hs_reg_chunks (regulation_id);
