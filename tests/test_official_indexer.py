import os
import unittest
from pathlib import Path

from hsrule.official_indexer import IndexingError, index_regulation, sync_snapshot
from hsrule.official_repository import OfficialRepository
from tests.fakes import FakeEmbeddings

URL = os.getenv("HSRULE_TEST_DATABASE_URL")
TEXT = (Path(__file__).with_name("fixtures") / "hansung_rules_excerpt.txt").read_text(encoding="utf-8")


def reg(reg_id="20", rev="20:1", text=TEXT):
    return {"regulation_id": reg_id, "revision_id": rev, "title": "한성대학교 학칙",
            "official_url": f"https://rule.example/{reg_id}", "text": text}


@unittest.skipUnless(URL, "set HSRULE_TEST_DATABASE_URL to a disposable PostgreSQL with pgvector")
class IndexerTests(unittest.TestCase):
    def setUp(self):
        self.repo = OfficialRepository(URL)
        self.repo.initialize()
        with self.repo.cursor() as cursor:
            cursor.execute("TRUNCATE hs_reg_revisions CASCADE")
        self.emb = FakeEmbeddings(TEXT + "신규 조항 개정")

    def test_index_then_unchanged_then_forced(self):
        first = index_regulation(reg(), self.repo, self.emb, batch_size=5)
        self.assertEqual(first["status"], "indexed")
        self.assertGreater(first["chunks"], 5)
        self.assertTrue(all(size <= 5 for size in self.emb.calls))
        self.assertEqual(sum(self.emb.calls), first["chunks"])
        calls = len(self.emb.calls)
        self.assertEqual(index_regulation(reg(), self.repo, self.emb, batch_size=5)["status"], "unchanged")
        self.assertEqual(len(self.emb.calls), calls)
        self.assertEqual(index_regulation(reg(), self.repo, self.emb, batch_size=5, force=True)["status"], "indexed")
        self.assertEqual(index_regulation(reg(), self.repo, self.emb, max_tokens=64)["status"], "indexed")

    def test_new_revision_becomes_current_and_old_is_kept(self):
        index_regulation(reg(rev="20:1"), self.repo, self.emb)
        index_regulation(reg(rev="20:2", text=TEXT + "\n제 68 조 (신규)\n신규 조항을 둔다.\n"), self.repo, self.emb)
        self.assertEqual(self.repo.current_revision("20")["revision_id"], "20:2")
        self.assertIsNotNone(self.repo.get_revision("20:1"))
        self.assertTrue(any(c["article_id"] == "제68조" for c in self.repo.chunks_of("20:2")))

    def test_embedding_failure_keeps_previous_index(self):
        index_regulation(reg(rev="20:1"), self.repo, self.emb)
        self.emb.fail_on = "신규 조항"
        with self.assertRaises(RuntimeError):
            index_regulation(reg(rev="20:2", text=TEXT + "\n제 68 조 (신규)\n신규 조항을 둔다.\n"), self.repo, self.emb)
        self.assertEqual(self.repo.current_revision("20")["revision_id"], "20:1")
        self.assertIsNone(self.repo.get_revision("20:2"))

    def test_invalid_input_is_rejected(self):
        with self.assertRaises(IndexingError):
            index_regulation(dict(reg(), official_url=""), self.repo, self.emb)
        self.assertEqual(self.repo.list_revisions(), [])

    def test_snapshot_sync_removes_missing_and_prunes_old(self):
        sync_snapshot([reg("20", "20:1"), reg("30", "30:1")], self.repo, self.emb)
        report = sync_snapshot([reg("20", "20:2", TEXT + "\n제 68 조 (신규)\n신규.\n")], self.repo, self.emb)
        self.assertEqual(report["removed"], ["30"])
        self.assertEqual(report["indexed"], ["20:2"])
        self.assertEqual(self.repo.regulation_ids(), ["20"])
        sync_snapshot([reg("20", "20:3", TEXT + "\n제 69 조 (신규)\n신규.\n")], self.repo, self.emb)
        self.assertEqual([r["revision_id"] for r in self.repo.list_revisions("20")], ["20:2", "20:3"])

    def test_failure_or_empty_snapshot_never_deletes(self):
        sync_snapshot([reg("20", "20:1"), reg("30", "30:1")], self.repo, self.emb)
        self.assertEqual(sync_snapshot([], self.repo, self.emb)["removed"], [])
        bad = dict(reg("20", "20:2"), official_url="")
        report = sync_snapshot([bad], self.repo, self.emb)
        self.assertEqual(len(report["failed"]), 1)
        self.assertEqual(report["removed"], [])
        self.assertEqual(self.repo.regulation_ids(), ["20", "30"])
        self.assertEqual(self.repo.current_revision("20")["revision_id"], "20:1")


if __name__ == "__main__":
    unittest.main()
