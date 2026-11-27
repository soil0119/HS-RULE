"""공식 규정 색인 저장소 (#24). 개정 단위로 원자적으로 저장·교체·삭제한다."""
import math
import sys
import unicodedata
from contextlib import closing, contextmanager

import psycopg2
from psycopg2.extras import Json, RealDictCursor, execute_values

from hsrule.migrations import apply_migrations

EMBEDDING_DIM = 384
REVISION_COLUMNS = ("revision_id, regulation_id, title, official_url, text_hash, visibility, is_current, "
                    "embedding_model, chunk_params, chunk_count, indexed_at")


def normalize_search_text(text: str) -> str:
    """키워드 검색용 정규화. NFKC로 상첨자·전각 문자를 보통 문자로 바꾸고 소문자로 만든다."""
    return unicodedata.normalize("NFKC", text).casefold()


def vector_literal(values) -> str:
    if len(values) != EMBEDDING_DIM or not all(math.isfinite(v) for v in values) or not any(values):
        raise ValueError(f"{EMBEDDING_DIM}차원의 유한한 영벡터가 아닌 임베딩이 필요합니다.")
    return str([float(value) for value in values])


class IndexConsistencyError(ValueError):
    """색인에 이미 다른 임베딩 모델·차원·청킹 설정의 벡터가 있다."""


class OfficialRepository:
    def __init__(self, database_url: str):
        self.database_url = database_url

    @contextmanager
    def cursor(self):
        with closing(psycopg2.connect(self.database_url)) as conn, conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                yield cursor

    def initialize(self) -> list[str]:
        """적용되지 않은 migration만 실행한다. 여러 번 실행해도 데이터를 지우지 않는다."""
        with self.cursor() as cursor:
            return apply_migrations(cursor)

    def index_meta(self) -> dict | None:
        with self.cursor() as cursor:
            cursor.execute("SELECT embedding_model, embedding_dim, chunk_params FROM hs_reg_index_meta")
            return cursor.fetchone()

    def _enforce_meta(self, cursor, revision: dict) -> None:
        """색인 전체의 모델·차원·청킹 설정이 섞이지 않게 한다.

        다른 설정으로 색인하려면 이 개정 외에 남은 개정이 없어야 한다(= 사실상 재구축).
        """
        incoming = (revision["embedding_model"], EMBEDDING_DIM, revision["chunk_params"])
        cursor.execute("SELECT embedding_model, embedding_dim, chunk_params FROM hs_reg_index_meta FOR UPDATE")
        current = cursor.fetchone()
        if current is None:
            # 메타 행이 아직 없으면 FOR UPDATE가 잠글 행이 없다(#54). 색인 전체에 하나뿐인 잠금을 잡고,
            # 기다리는 동안 다른 저장이 메타를 만들었을 수 있으므로 잠금 아래에서 다시 읽는다.
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("hs_reg_index_meta",))
            cursor.execute("SELECT embedding_model, embedding_dim, chunk_params FROM hs_reg_index_meta FOR UPDATE")
            current = cursor.fetchone()
        if current is not None:
            if (current["embedding_model"], current["embedding_dim"], current["chunk_params"]) == incoming:
                return
            cursor.execute("SELECT count(*) AS others FROM hs_reg_revisions WHERE revision_id <> %s",
                           (revision["revision_id"],))
            if cursor.fetchone()["others"] > 0:
                raise IndexConsistencyError(
                    f"색인은 {current['embedding_model']}·{current['chunk_params']} 기준입니다. "
                    "다른 모델이나 청킹 설정으로 색인하려면 전체를 재구축하세요.")
        cursor.execute("""
            INSERT INTO hs_reg_index_meta (id, embedding_model, embedding_dim, chunk_params)
            VALUES (true, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET embedding_model = EXCLUDED.embedding_model,
                embedding_dim = EXCLUDED.embedding_dim, chunk_params = EXCLUDED.chunk_params,
                updated_at = now()
        """, (incoming[0], incoming[1], Json(incoming[2])))

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
        visibility = revision.get("visibility", "public")
        if visibility not in ("public", "private"):
            raise ValueError("visibility는 public 또는 private이어야 합니다.")
        rows = []
        for chunk, vector in zip(chunks, vectors):
            if chunk["revision_id"] != revision["revision_id"] or chunk["regulation_id"] != revision["regulation_id"]:
                raise ValueError("청크의 규정/개정 ID가 일치하지 않습니다.")
            rows.append((chunk["id"], chunk["revision_id"], chunk["ordinal"], chunk["regulation_id"],
                         chunk["article_id"], chunk["kind"], chunk["chapter"], chunk["source_start"],
                         chunk["source_end"], chunk["content"], chunk["embedding_text"],
                         normalize_search_text(chunk["content"]),
                         Json(chunk["references"]), chunk["deleted"], visibility, make_current,
                         vector_literal(vector), chunk.get("source_id", "")))
        with self.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (revision["regulation_id"],))
            self._enforce_meta(cursor, revision)
            cursor.execute("DELETE FROM hs_reg_revisions WHERE revision_id = %s", (revision["revision_id"],))
            if make_current:
                cursor.execute("UPDATE hs_reg_revisions SET is_current = false "
                               "WHERE regulation_id = %s AND is_current RETURNING revision_id",
                               (revision["regulation_id"],))
                demoted = [row["revision_id"] for row in cursor.fetchall()]
                if demoted:  # 내려간 개정의 청크는 검색 대상에서 빠진다.
                    cursor.execute("UPDATE hs_reg_chunks SET searchable = false WHERE revision_id = ANY(%s)",
                                   (demoted,))
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
                    source_start, source_end, content, embedding_text, search_text, refs, deleted, visibility,
                    searchable, embedding, source_article_id)
                VALUES %s
            """, rows, template="(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::vector,%s)")
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

    # ---- 색인 실행 기록 (#25) ----
    def start_run(self, embedding_model: str, revision_ids: list[str]) -> int:
        with self.cursor() as cursor:
            cursor.execute("INSERT INTO hs_reg_index_runs (embedding_model, snapshot_revisions) "
                           "VALUES (%s, %s) RETURNING id", (embedding_model, Json(revision_ids)))
            return cursor.fetchone()["id"]

    def finish_run(self, run_id: int, report: dict, ok: bool) -> None:
        with self.cursor() as cursor:
            cursor.execute("UPDATE hs_reg_index_runs SET finished_at = now(), report = %s, ok = %s WHERE id = %s",
                           (Json(report), ok, run_id))

    def last_run(self) -> dict | None:
        with self.cursor() as cursor:
            cursor.execute("SELECT id, started_at, finished_at, embedding_model, snapshot_revisions, report, ok "
                           "FROM hs_reg_index_runs ORDER BY started_at DESC, id DESC LIMIT 1")
            return cursor.fetchone()
    # ---- 검색 (#26) ----
    # 후보는 항상 (1) 현재 개정 또는 지정한 개정, (2) 권한 범위, (3) 삭제 조항 제외 조건을 거친다.
    # 조건은 모두 hs_reg_chunks의 열이므로 부분 인덱스 스캔 단계에서 적용되고 LIMIT 이후로 밀리지 않는다.
    _CANDIDATE = """
        c.id, c.revision_id, c.regulation_id, r.title, r.official_url, c.article_id, c.kind, c.chapter,
        c.content, c.source_start, c.source_end, c.refs, c.deleted, c.visibility, r.is_current,
        c.source_article_id
    """
    SEARCH_TIMEOUT_MS = 5_000
    EF_SEARCH = 200

    @staticmethod
    def _scope(include_private, regulation_ids, revision_ids, include_deleted):
        clauses, params = [], []
        if revision_ids:
            revision_ids = list(dict.fromkeys(revision_ids))
            clauses.append("c.revision_id = ANY(%s)")
            params.append(revision_ids)
        else:
            clauses.append("c.searchable")
        if regulation_ids:
            clauses.append("c.regulation_id = ANY(%s)")
            params.append(list(regulation_ids))
        if not include_private:
            clauses.append("c.visibility = 'public'")
        if not include_deleted:
            clauses.append("NOT c.deleted")
        return " AND ".join(clauses), params

    def _guard_revisions(self, revision_ids) -> None:
        """한 규정의 두 개정을 함께 검색하지 않는다. 같은 조항이 개정별로 중복 근거가 된다."""
        if not revision_ids:
            return
        with self.cursor() as cursor:
            cursor.execute("""
                SELECT regulation_id, count(*) AS revisions FROM hs_reg_revisions
                WHERE revision_id = ANY(%s) GROUP BY regulation_id HAVING count(*) > 1
            """, (list(dict.fromkeys(revision_ids)),))
            mixed = [row["regulation_id"] for row in cursor.fetchall()]
        if mixed:
            raise ValueError(f"한 규정의 여러 개정을 함께 검색할 수 없습니다: {', '.join(mixed)}")

    @contextmanager
    def _search_cursor(self):
        with self.cursor() as cursor:
            cursor.execute("SET LOCAL statement_timeout = %s", (self.SEARCH_TIMEOUT_MS,))
            yield cursor

    @staticmethod
    def _ordered(rows: list[dict]) -> list[dict]:
        # 벡터 질의는 인덱스가 정렬을 제공해야 하므로 SQL에서 2차 키를 쓰지 않고 여기서 동순위를 정한다.
        return sorted(rows, key=lambda row: (-float(row["score"]), row["id"]))

    def vector_candidates(self, embedding, *, limit=20, include_private=False, regulation_ids=None,
                          revision_ids=None, include_deleted=False) -> list[dict]:
        """코사인 유사도 상위 후보. score는 1 - 코사인 거리이며 score_kind는 cosine이다."""
        self._guard_revisions(revision_ids)
        where, params = self._scope(include_private, regulation_ids, revision_ids, include_deleted)
        literal = vector_literal(embedding)
        with self._search_cursor() as cursor:
            cursor.execute("SET LOCAL hnsw.ef_search = %s", (self.EF_SEARCH,))
            cursor.execute(f"""
                SELECT {self._CANDIDATE}, 1 - (c.embedding <=> %s::vector) AS score
                FROM hs_reg_chunks c JOIN hs_reg_revisions r USING (revision_id)
                WHERE {where} ORDER BY c.embedding <=> %s::vector LIMIT %s
            """, [literal, *params, literal, max(0, min(limit, 100))])
            rows = [dict(row, score_kind="cosine") for row in cursor.fetchall()]
        return self._ordered(rows)

    def keyword_candidates(self, terms: list[str], *, limit=20, include_private=False, regulation_ids=None,
                           revision_ids=None, include_deleted=False) -> list[dict]:
        """용어 포함 수 기반 후보. score는 유사도가 아니라 어휘 일치 점수이며 score_kind는 lexical이다."""
        terms = [normalize_search_text(t) for t in dict.fromkeys(terms) if t][:12]
        if not terms:
            return []
        self._guard_revisions(revision_ids)
        where, params = self._scope(include_private, regulation_ids, revision_ids, include_deleted)
        with self._search_cursor() as cursor:
            cursor.execute(f"""
                SELECT {self._CANDIDATE}, lexical.matched::float + lexical.hits::float / 1000 AS score
                FROM hs_reg_chunks c JOIN hs_reg_revisions r USING (revision_id)
                CROSS JOIN LATERAL (
                    SELECT sum((strpos(c.search_text, term) > 0)::int
                               + (strpos(lower(c.article_id), term) > 0)::int) AS matched,
                           sum((length(c.search_text) - length(replace(c.search_text, term, '')))
                               / length(term)) AS hits
                    FROM unnest(%s::text[]) AS term
                ) lexical
                WHERE {where} AND lexical.matched > 0
                ORDER BY score DESC, c.id LIMIT %s
            """, [terms, *params, max(0, min(limit, 100))])
            return [dict(row, score_kind="lexical") for row in cursor.fetchall()]

    def article_candidates(self, article_ids: list[str], *, limit=20, include_private=False,
                           regulation_ids=None, revision_ids=None, include_deleted=False) -> list[dict]:
        """조항 ID 완전 일치 후보. 유사도가 아니므로 score는 없고 score_kind는 exact다."""
        if not article_ids:
            return []
        self._guard_revisions(revision_ids)
        where, params = self._scope(include_private, regulation_ids, revision_ids, include_deleted)
        with self._search_cursor() as cursor:
            cursor.execute(f"""
                SELECT {self._CANDIDATE}
                FROM hs_reg_chunks c JOIN hs_reg_revisions r USING (revision_id)
                WHERE {where} AND c.article_id = ANY(%s)
                ORDER BY c.revision_id, c.ordinal LIMIT %s
            """, [*params, list(article_ids), max(0, min(limit, 100))])
            return [dict(row, score=None, score_kind="exact") for row in cursor.fetchall()]

    def chunks_of(self, revision_id: str) -> list[dict]:
        with self.cursor() as cursor:
            cursor.execute("SELECT id, ordinal, article_id, kind, source_start, source_end, content, "
                           "visibility, searchable, deleted, source_article_id "
                           "FROM hs_reg_chunks WHERE revision_id = %s ORDER BY ordinal", (revision_id,))
            return cursor.fetchall()

    def set_official_url(self, revision_id: str, official_url: str) -> int:
        """본문이 같은 개정의 공식 URL만 교정한다. 청크는 개정 행의 URL을 조인해 쓰므로 검색 결과에 바로 반영된다."""
        with self.cursor() as cursor:
            cursor.execute("UPDATE hs_reg_revisions SET official_url = %s WHERE revision_id = %s",
                           (official_url, revision_id))
            return cursor.rowcount

    def set_visibility(self, regulation_id: str, visibility: str) -> int:
        """규정의 공개 범위를 바꾼다. 청크까지 같은 트랜잭션에서 갱신해 검색에 즉시 반영된다."""
        if visibility not in ("public", "private"):
            raise ValueError("visibility는 public 또는 private이어야 합니다.")
        with self.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (regulation_id,))
            cursor.execute("UPDATE hs_reg_revisions SET visibility = %s WHERE regulation_id = %s",
                           (visibility, regulation_id))
            changed = cursor.rowcount
            cursor.execute("UPDATE hs_reg_chunks SET visibility = %s WHERE regulation_id = %s",
                           (visibility, regulation_id))
            return changed


    def rebuild(self) -> None:
        """색인을 비운다. 모델이나 청킹 설정을 바꿀 때만 명시적으로 호출한다(migration에는 넣지 않는다)."""
        with self.cursor() as cursor:
            cursor.execute("TRUNCATE hs_reg_revisions CASCADE")
            cursor.execute("DELETE FROM hs_reg_index_meta")


if __name__ == "__main__":
    import os

    url = os.environ.get("DATABASE_URL")
    command = sys.argv[1:]
    if not url or command not in (["--init"], ["--rebuild"]):
        sys.exit("사용법: DATABASE_URL=... python -m hsrule.official_repository --init | --rebuild")
    repository = OfficialRepository(url)
    applied = repository.initialize()
    print("적용한 migration:", ", ".join(applied) or "없음 (이미 최신)")
    if command == ["--rebuild"]:
        repository.rebuild()
        print("색인을 비웠습니다. 전체를 다시 색인하세요.")
