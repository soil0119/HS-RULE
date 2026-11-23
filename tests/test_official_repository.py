import os
import unittest

import psycopg2

from hsrule.official_repository import OfficialRepository

URL = os.getenv("HSRULE_TEST_DATABASE_URL")


def vec(x, y=0.0):
    return [float(x), float(y)] + [0.0] * 382


def revision(reg="20", rev="20:1", text_hash="h1"):
    return {"revision_id": rev, "regulation_id": reg, "title": "학칙", "official_url": "https://rule.example/x",
            "text_hash": text_hash, "embedding_model": "fake", "chunk_params": {"max_tokens": 128}}


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
        with self.repo.cursor() as cursor:
            cursor.execute("TRUNCATE hs_reg_revisions CASCADE")

    def save(self, rev="20:1", reg="20", n=2, **kw):
        return self.repo.save_revision(revision(reg, rev), [chunk(rev, i, reg=reg) for i in range(n)],
                                       [vec(1, i) for i in range(n)], **kw)

    def test_initialize_is_idempotent_and_creates_indexes(self):
        self.repo.initialize()
        with self.repo.cursor() as cursor:
            cursor.execute("SELECT indexname FROM pg_indexes WHERE tablename IN ('hs_reg_chunks','hs_reg_revisions')")
            names = {row["indexname"] for row in cursor.fetchall()}
        for expected in ("hs_reg_chunks_embedding", "hs_reg_chunks_content_trgm", "hs_reg_revisions_one_current"):
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
