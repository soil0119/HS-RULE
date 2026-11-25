import os
import unittest

import psycopg2

from hsrule.migrations import MigrationError, check_non_destructive, migration_files
from hsrule.official_repository import IndexConsistencyError, OfficialRepository, vector_literal

URL = os.getenv("HSRULE_TEST_DATABASE_URL")


def vec(x, y=0.0):
    return [float(x), float(y)] + [0.0] * 382


def revision(reg="20", rev="20:1", text_hash="h1", model="fake", params=None):
    return {"revision_id": rev, "regulation_id": reg, "title": "학칙", "official_url": "https://rule.example/x",
            "text_hash": text_hash, "embedding_model": model,
            "chunk_params": params or {"max_tokens": 128}}


def chunk(rev, ordinal, article="제1조", reg="20", content="본문"):
    return {"id": f"{rev}:{ordinal}", "revision_id": rev, "regulation_id": reg, "ordinal": ordinal,
            "article_id": article, "kind": "article", "chapter": "", "source_start": ordinal * 10,
            "source_end": ordinal * 10 + 5, "content": content, "embedding_text": content,
            "references": [], "deleted": False}


@unittest.skipUnless(URL, "set HSRULE_TEST_DATABASE_URL to a disposable PostgreSQL with pgvector")
class OfficialRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.repo = OfficialRepository(URL)
        self.repo.initialize()
        self.repo.rebuild()

    def save(self, rev="20:1", reg="20", n=2, **kw):
        return self.repo.save_revision(revision(reg, rev), [chunk(rev, i, reg=reg) for i in range(n)],
                                       [vec(1, i) for i in range(n)], **kw)

    def test_migrations_are_non_destructive_and_recorded(self):
        self.assertTrue(migration_files())
        check_non_destructive(migration_files())  # 데이터를 지우는 문장이 있으면 예외
        self.assertEqual(self.repo.initialize(), [])  # 이미 적용된 상태에서는 아무것도 다시 실행하지 않는다
        with self.repo.cursor() as cursor:
            cursor.execute("SELECT name FROM hs_reg_migrations ORDER BY name")
            names = [row["name"] for row in cursor.fetchall()]
        self.assertEqual(names, [path.name for path in migration_files()])

    def test_applied_migration_cannot_be_edited(self):
        name = migration_files()[0].name
        with self.repo.cursor() as cursor:
            cursor.execute("SELECT checksum FROM hs_reg_migrations WHERE name = %s", (name,))
            original = cursor.fetchone()["checksum"]
            cursor.execute("UPDATE hs_reg_migrations SET checksum = 'tampered' WHERE name = %s", (name,))
        self.addCleanup(self._restore_checksum, name, original)
        with self.assertRaises(MigrationError):
            self.repo.initialize()

    def _restore_checksum(self, name, checksum):
        with self.repo.cursor() as cursor:
            cursor.execute("UPDATE hs_reg_migrations SET checksum = %s WHERE name = %s", (checksum, name))

    def test_mixed_embedding_model_or_chunk_params_are_rejected(self):
        self.save("20:1")
        self.save("30:1", reg="30")
        other = revision("40", "40:1", model="other-model")
        with self.assertRaises(IndexConsistencyError):
            self.repo.save_revision(other, [chunk("40:1", 0, reg="40")], [vec(1)])
        mixed = revision("40", "40:1", params={"max_tokens": 64})
        with self.assertRaises(IndexConsistencyError):
            self.repo.save_revision(mixed, [chunk("40:1", 0, reg="40")], [vec(1)])
        self.assertEqual(self.repo.index_meta()["embedding_model"], "fake")
        self.assertIsNone(self.repo.get_revision("40:1"))
        # 재구축 후에는 새 설정으로 색인할 수 있다.
        self.repo.rebuild()
        self.repo.save_revision(other, [chunk("40:1", 0, reg="40")], [vec(1)])
        self.assertEqual(self.repo.index_meta()["embedding_model"], "other-model")

    def test_single_revision_may_change_chunk_params(self):
        self.save("20:1")
        changed = revision("20", "20:1", params={"max_tokens": 64})
        self.repo.save_revision(changed, [chunk("20:1", 0)], [vec(1)])
        self.assertEqual(self.repo.index_meta()["chunk_params"], {"max_tokens": 64})

    def test_initialize_is_idempotent_and_creates_indexes(self):
        self.repo.initialize()
        with self.repo.cursor() as cursor:
            cursor.execute("SELECT indexname FROM pg_indexes WHERE tablename IN ('hs_reg_chunks','hs_reg_revisions')")
            names = {row["indexname"] for row in cursor.fetchall()}
        for expected in ("hs_reg_chunks_embedding", "hs_reg_chunks_search_trgm", "hs_reg_revisions_one_current"):
            self.assertIn(expected, names)

    def test_new_revision_replaces_current_atomically(self):
        self.save("20:1")
        self.save("20:2", n=3)
        self.assertEqual(self.repo.current_revision("20")["revision_id"], "20:2")
        self.assertFalse(self.repo.get_revision("20:1")["is_current"])
        self.assertEqual(len(self.repo.chunks_of("20:2")), 3)
        self.assertTrue(self.repo.health()["ready"])

    def test_resaving_same_revision_replaces_chunks(self):
        self.save("20:1", n=3)
        self.save("20:1", n=1)
        self.assertEqual(len(self.repo.chunks_of("20:1")), 1)
        self.assertEqual(self.repo.get_revision("20:1")["chunk_count"], 1)

    def test_failed_save_keeps_previous_state(self):
        self.save("20:1")
        bad = [chunk("20:2", 0), chunk("20:2", 1)]
        with self.assertRaises(ValueError):
            self.repo.save_revision(revision(rev="20:2"), bad, [vec(1), vec(0)])  # 영벡터
        self.assertEqual(self.repo.current_revision("20")["revision_id"], "20:1")
        self.assertIsNone(self.repo.get_revision("20:2"))
        with self.assertRaises(psycopg2.Error):
            dup = [chunk("20:3", 0), chunk("20:3", 0)]
            self.repo.save_revision(revision(rev="20:3"), dup, [vec(1), vec(1, 1)])
        self.assertEqual(self.repo.current_revision("20")["revision_id"], "20:1")

    def test_rejects_mismatched_ids_and_bad_vectors(self):
        with self.assertRaises(ValueError):
            self.repo.save_revision(revision(), [chunk("other", 0)], [vec(1)])
        with self.assertRaises(ValueError):
            self.repo.save_revision(revision(), [chunk("20:1", 0)], [[1.0] * 10])
        with self.assertRaises(ValueError):
            self.repo.save_revision(revision(), [], [])

    # 벡터 인덱스가 정렬을 제공할 수 있도록 ORDER BY에 거리 외의 키를 두지 않는다.
    SEARCH_SQL = """
        SELECT c.regulation_id FROM hs_reg_chunks c
        WHERE c.searchable AND NOT c.deleted AND (c.visibility = 'public' OR %s)
        ORDER BY c.embedding <=> %s::vector LIMIT %s
    """

    def search(self, embedding, limit=1, include_private=False):
        with self.repo.cursor() as cursor:
            cursor.execute(self.SEARCH_SQL, (include_private, vector_literal(embedding), limit))
            return [row["regulation_id"] for row in cursor.fetchall()]

    def test_search_text_is_normalized_for_keyword_matching(self):
        self.repo.save_revision(revision(), [chunk("20:1", 0, content="A⁺ 학점은 ９５점 이상이다")], [vec(1)])
        with self.repo.cursor() as cursor:
            cursor.execute("SELECT search_text FROM hs_reg_chunks WHERE id = '20:1:0'")
            stored = cursor.fetchone()["search_text"]
        self.assertIn("a+", stored)   # 상첨자 ⁺ -> +
        self.assertIn("95", stored)   # 전각 숫자 -> 반각
        with self.repo.cursor() as cursor:
            cursor.execute("SELECT count(*) AS n FROM hs_reg_chunks WHERE strpos(search_text, 'a+') > 0")
            self.assertEqual(cursor.fetchone()["n"], 1)

    def test_scope_filter_is_applied_before_the_limit(self):
        """비공개·구개정·삭제 조항이 가장 가까운 이웃이어도 LIMIT 안으로 들어오지 않는다."""
        self.save("20:1", n=3)
        private = dict(revision("99", "99:1"), visibility="private")
        self.repo.save_revision(private, [chunk("99:1", 0, reg="99", content="비공개")], [vec(0, 1)])
        self.assertEqual(self.search(vec(0, 1), limit=1), ["20"])  # 질의와 같은 방향인 비공개를 제외
        self.assertEqual(self.search(vec(0, 1), limit=1, include_private=True), ["99"])
        self.repo.save_revision(revision("98", "98:1"), [dict(chunk("98:1", 0, reg="98"), deleted=True)], [vec(0, 1)])
        self.assertNotIn("98", self.search(vec(0, 1), limit=5, include_private=True))
        self.save("20:2", n=1)  # 20:1은 더 이상 현재 개정이 아니다
        self.assertTrue(all(not c["searchable"] for c in self.repo.chunks_of("20:1")))
        self.assertTrue(all(c["searchable"] for c in self.repo.chunks_of("20:2")))

    def test_visibility_change_reaches_the_chunks(self):
        self.save("20:1", n=2)
        self.assertEqual(self.search(vec(1, 0), limit=5), ["20", "20"])
        self.assertEqual(self.repo.set_visibility("20", "private"), 1)
        self.assertEqual(self.search(vec(1, 0), limit=5), [])
        self.assertEqual(len(self.search(vec(1, 0), limit=5, include_private=True)), 2)

    def test_vector_order_by_uses_the_partial_hnsw_index(self):
        """필터와 거리 정렬이 한 테이블에 있어 벡터 인덱스가 선택된다."""
        self.save("20:1", n=3)
        with self.repo.cursor() as cursor:
            cursor.execute("SET LOCAL enable_seqscan = off")
            cursor.execute("EXPLAIN " + self.SEARCH_SQL, (False, vector_literal(vec(1, 0)), 5))
            plan = "\n".join(row["QUERY PLAN"] for row in cursor.fetchall())
        self.assertIn("hs_reg_chunks_embedding", plan)

    def test_delete_revision_and_regulation(self):
        self.save("20:1")
        self.save("20:2")
        self.save("30:1", reg="30")
        self.assertTrue(self.repo.delete_revision("20:1"))
        self.assertFalse(self.repo.delete_revision("20:1"))
        self.assertEqual(self.repo.delete_regulation("20"), 1)
        self.assertIsNone(self.repo.current_revision("20"))
        self.assertEqual(self.repo.current_revision("30")["revision_id"], "30:1")
        with self.repo.cursor() as cursor:
            cursor.execute("SELECT count(*) AS n FROM hs_reg_chunks WHERE regulation_id = '20'")
            self.assertEqual(cursor.fetchone()["n"], 0)


if __name__ == "__main__":
    unittest.main()
