"""공식 규정 색인 저장소 (#24). 개정 단위로 원자적으로 저장·교체·삭제한다."""
import math
import sys
from contextlib import closing, contextmanager
from pathlib import Path

import psycopg2
from psycopg2.extras import Json, RealDictCursor, execute_values

EMBEDDING_DIM = 384
REVISION_COLUMNS = ("revision_id, regulation_id, title, official_url, text_hash, visibility, is_current, "
                    "embedding_model, chunk_params, chunk_count, indexed_at")


def vector_literal(values) -> str:
    if len(values) != EMBEDDING_DIM or not all(math.isfinite(v) for v in values) or not any(values):
        raise ValueError(f"{EMBEDDING_DIM}차원의 유한한 영벡터가 아닌 임베딩이 필요합니다.")
    return str([float(value) for value in values])


class OfficialRepository:
    def __init__(self, database_url: str):
        self.database_url = database_url

    @contextmanager
    def cursor(self):
        with closing(psycopg2.connect(self.database_url)) as conn, conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                yield cursor

    def initialize(self) -> None:
        with self.cursor() as cursor:
            cursor.execute(Path(__file__).with_name("schema_official.sql").read_text(encoding="utf-8"))

    def health(self) -> dict:
        with self.cursor() as cursor:
            cursor.execute("""
                SELECT (SELECT count(*) FROM hs_reg_revisions WHERE is_current) AS current_revisions,
                       (SELECT count(*) FROM hs_reg_chunks c JOIN hs_reg_revisions r USING (revision_id)
                         WHERE r.is_current) AS current_chunks
            """)
            status = dict(cursor.fetchone())
        status["ready"] = status["current_revisions"] > 0 and status["current_chunks"] > 0
        return status

    def get_revision(self, revision_id: str) -> dict | None:
        with self.cursor() as cursor:
            cursor.execute(f"SELECT {REVISION_COLUMNS} FROM hs_reg_revisions WHERE revision_id = %s", (revision_id,))
            return cursor.fetchone()

    def current_revision(self, regulation_id: str) -> dict | None:
        with self.cursor() as cursor:
            cursor.execute(f"SELECT {REVISION_COLUMNS} FROM hs_reg_revisions "
                           "WHERE regulation_id = %s AND is_current", (regulation_id,))
            return cursor.fetchone()

    def list_revisions(self, regulation_id: str | None = None) -> list[dict]:
        with self.cursor() as cursor:
            cursor.execute(f"""
                SELECT {REVISION_COLUMNS} FROM hs_reg_revisions
                WHERE %s::text IS NULL OR regulation_id = %s ORDER BY regulation_id, indexed_at, revision_id
            """, (regulation_id, regulation_id))
            return cursor.fetchall()

    def save_revision(self, revision: dict, chunks: list[dict], vectors: list[list[float]],
                      *, make_current: bool = True) -> dict:
        """한 트랜잭션에서 개정과 청크를 저장한다. 같은 revision_id가 있으면 통째로 교체한다.

        make_current이면 같은 규정의 이전 현재 개정을 내리고 이 개정을 현재로 올린다.
        중간에 실패하면 이전 상태가 그대로 남는다.
        """
        if not chunks or len(chunks) != len(vectors):
            raise ValueError("청크와 임베딩의 개수가 일치해야 하며 비어 있을 수 없습니다.")
        rows = []
        for chunk, vector in zip(chunks, vectors):
            if chunk["revision_id"] != revision["revision_id"] or chunk["regulation_id"] != revision["regulation_id"]:
                raise ValueError("청크의 규정/개정 ID가 일치하지 않습니다.")
            rows.append((chunk["id"], chunk["revision_id"], chunk["ordinal"], chunk["regulation_id"],
                         chunk["article_id"], chunk["kind"], chunk["chapter"], chunk["source_start"],
                         chunk["source_end"], chunk["content"], chunk["embedding_text"],
                         Json(chunk["references"]), chunk["deleted"], vector_literal(vector)))
        with self.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (revision["regulation_id"],))
            cursor.execute("DELETE FROM hs_reg_revisions WHERE revision_id = %s", (revision["revision_id"],))
            if make_current:
                cursor.execute("UPDATE hs_reg_revisions SET is_current = false "
                               "WHERE regulation_id = %s AND is_current", (revision["regulation_id"],))
            cursor.execute(f"""
                INSERT INTO hs_reg_revisions (revision_id, regulation_id, title, official_url, text_hash,
                    visibility, is_current, embedding_model, chunk_params, chunk_count)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING {REVISION_COLUMNS}
            """, (revision["revision_id"], revision["regulation_id"], revision["title"], revision["official_url"],
                  revision["text_hash"], revision.get("visibility", "public"), make_current,
                  revision["embedding_model"], Json(revision["chunk_params"]), len(rows)))
            saved = cursor.fetchone()
            execute_values(cursor, """
                INSERT INTO hs_reg_chunks (id, revision_id, ordinal, regulation_id, article_id, kind, chapter,
                    source_start, source_end, content, embedding_text, refs, deleted, embedding)
                VALUES %s
            """, rows, template="(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::vector)")
            return saved

    def delete_revision(self, revision_id: str) -> bool:
        with self.cursor() as cursor:
            cursor.execute("DELETE FROM hs_reg_revisions WHERE revision_id = %s", (revision_id,))
            return cursor.rowcount > 0

    def delete_regulation(self, regulation_id: str) -> int:
        """규정 전체(모든 개정)를 색인에서 제거하고 지운 개정 수를 반환한다."""
        with self.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (regulation_id,))
            cursor.execute("DELETE FROM hs_reg_revisions WHERE regulation_id = %s", (regulation_id,))
            return cursor.rowcount

    def prune_revisions(self, regulation_id: str, keep_previous: int = 1) -> int:
        """현재 개정과 가장 최근 이전 개정 keep_previous개만 남기고 오래된 개정을 지운다."""
        with self.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (regulation_id,))
            cursor.execute("""
                DELETE FROM hs_reg_revisions WHERE regulation_id = %s AND NOT is_current AND revision_id NOT IN (
                    SELECT revision_id FROM hs_reg_revisions WHERE regulation_id = %s AND NOT is_current
                    ORDER BY indexed_at DESC, revision_id DESC LIMIT %s)
            """, (regulation_id, regulation_id, max(0, keep_previous)))
            return cursor.rowcount

    def regulation_ids(self) -> list[str]:
        with self.cursor() as cursor:
            cursor.execute("SELECT DISTINCT regulation_id FROM hs_reg_revisions ORDER BY regulation_id")
            return [row["regulation_id"] for row in cursor.fetchall()]

    def chunks_of(self, revision_id: str) -> list[dict]:
        with self.cursor() as cursor:
            cursor.execute("SELECT id, ordinal, article_id, kind, source_start, source_end, content "
                           "FROM hs_reg_chunks WHERE revision_id = %s ORDER BY ordinal", (revision_id,))
            return cursor.fetchall()


if __name__ == "__main__":
    import os

    url = os.environ.get("DATABASE_URL")
    if not url or sys.argv[1:] != ["--init"]:
        sys.exit("사용법: DATABASE_URL=... python -m hsrule.official_repository --init")
    OfficialRepository(url).initialize()
    print("공식 규정 색인 스키마를 초기화했습니다.")
