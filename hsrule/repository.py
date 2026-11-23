import math
import re
from contextlib import closing, contextmanager
from pathlib import Path

import psycopg2
from psycopg2.extras import Json, RealDictCursor, execute_values


class DuplicateDocumentError(ValueError):
    """A document with this PDF content already exists."""


DOCUMENT_COLUMNS = """
    d.id, d.title, d.filename, d.visibility, d.page_count, d.warnings, d.created_at,
    (SELECT count(*) FROM hs_chunks c WHERE c.document_id = d.id) AS chunks
"""
CANDIDATE_COLUMNS = """
    c.id, c.document_id, d.title, c.article, c.content, c.page_start, c.page_end,
    1 - (c.embedding <=> %s::vector) AS score, d.visibility
"""
VISIBLE = "(d.visibility = 'public' OR %s)"


def _vector(values):
    if len(values) != 384 or not all(math.isfinite(v) for v in values) or not any(values):
        raise ValueError("384차원의 유한한 영벡터가 아닌 임베딩이 필요합니다.")
    return str([float(value) for value in values])


class RegulationRepository:
    def __init__(self, database_url: str):
        self.database_url = database_url

    @contextmanager
    def _cursor(self):
        with closing(psycopg2.connect(self.database_url)) as conn, conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                yield cursor

    def initialize(self) -> None:
        with self._cursor() as cursor:
            cursor.execute(Path(__file__).with_name("schema.sql").read_text(encoding="utf-8"))

    def health(self) -> dict:
        with self._cursor() as cursor:
            cursor.execute("""
                SELECT (SELECT count(*) FROM hs_documents) AS document_count,
                       (SELECT count(*) FROM hs_chunks) AS chunk_count
            """)
            status = dict(cursor.fetchone())
        status["ready"] = status["document_count"] > 0 and status["chunk_count"] > 0
        return status

    def save_document(self, document: dict, embeddings: list[list[float]]) -> dict:
        chunks = document["chunks"]
        if not chunks or len(chunks) != len(embeddings):
            raise ValueError("청크와 임베딩의 개수가 일치해야 하며 비어 있을 수 없습니다.")
        doc_id = document["id"]
        rows = []
        for chunk, embedding in zip(chunks, embeddings):
            if chunk.get("document_id", doc_id) != doc_id or not chunk["id"].startswith(doc_id + ":"):
                raise ValueError("청크의 문서 ID가 일치하지 않습니다.")
            rows.append((chunk["id"], doc_id, chunk["ordinal"], chunk["page_start"], chunk["page_end"],
                         chunk["article"], chunk["content"], _vector(embedding)))

        with self._cursor() as cursor:
            cursor.execute("""
                INSERT INTO hs_documents (id, title, filename, visibility, content_hash, page_count, pages, warnings)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (content_hash) DO NOTHING
                RETURNING id, title, filename, visibility, page_count, warnings, created_at
            """, (doc_id, document["title"], document["filename"], document["visibility"],
                  document["content_hash"], document["page_count"], Json(document["pages"]), Json(document["warnings"])))
            saved = cursor.fetchone()
            if saved is None:
                raise DuplicateDocumentError("동일한 PDF가 이미 등록되어 있습니다.")
            execute_values(cursor, """
                INSERT INTO hs_chunks (id, document_id, ordinal, page_start, page_end, article, content, embedding)
                VALUES %s
            """, rows)
            return dict(saved, chunks=len(rows))

    def list_documents(self, include_private: bool = False) -> list[dict]:
        with self._cursor() as cursor:
            cursor.execute(f"""
                SELECT {DOCUMENT_COLUMNS} FROM hs_documents d WHERE {VISIBLE}
                ORDER BY d.created_at DESC, d.id
            """, (include_private,))
            return cursor.fetchall()

    def get_document(self, document_id: str, include_private: bool = False) -> dict | None:
        with self._cursor() as cursor:
            cursor.execute(f"""
                SELECT {DOCUMENT_COLUMNS}, d.pages, d.content_hash FROM hs_documents d
                WHERE d.id = %s AND {VISIBLE}
            """, (document_id, include_private))
            return cursor.fetchone()

    def delete_document(self, document_id: str) -> dict | None:
        with self._cursor() as cursor:
            cursor.execute("DELETE FROM hs_documents WHERE id = %s RETURNING id, title, filename", (document_id,))
            return cursor.fetchone()

    def vector_candidates(self, embedding: list[float], include_private: bool = False, limit: int = 20) -> list[dict]:
        query_vector = _vector(embedding)
        with self._cursor() as cursor:
            cursor.execute(f"""
                SELECT {CANDIDATE_COLUMNS} FROM hs_chunks c JOIN hs_documents d ON d.id = c.document_id
                WHERE {VISIBLE} ORDER BY c.embedding <=> %s::vector, c.id LIMIT %s
            """, (query_vector, include_private, query_vector, max(0, min(limit, 100))))
            return cursor.fetchall()

    def keyword_candidates(self, query: str, embedding: list[float], include_private: bool = False,
                           limit: int = 20) -> list[dict]:
        terms = list(dict.fromkeys(re.findall(r"\w+", query[:1024].lower())))[:8]
        if not terms:
            return []
        with self._cursor() as cursor:
            cursor.execute(f"""
                SELECT {CANDIDATE_COLUMNS} FROM hs_chunks c JOIN hs_documents d ON d.id = c.document_id
                CROSS JOIN LATERAL (
                    SELECT sum((strpos(lower(d.title), term) > 0)::int * 3
                             + (strpos(lower(c.article), term) > 0)::int * 2
                             + (strpos(lower(c.content), term) > 0)::int) AS rank
                    FROM unnest(%s::text[]) AS term
                ) lexical
                WHERE {VISIBLE} AND lexical.rank > 0
                ORDER BY lexical.rank DESC, c.id LIMIT %s
            """, (_vector(embedding), terms, include_private, max(0, min(limit, 100))))
            return cursor.fetchall()
