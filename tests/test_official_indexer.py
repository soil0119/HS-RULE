import os
import unittest
from pathlib import Path

from hsrule.official_indexer import (IndexingError, _embed_in_batches, index_regulation, sync_snapshot,
                                     validate_embeddings)
from hsrule.official_repository import OfficialRepository
from tests.fakes import FakeEmbeddings

URL = os.getenv("HSRULE_TEST_DATABASE_URL")
TEXT = (Path(__file__).with_name("fixtures") / "hansung_rules_excerpt.txt").read_text(encoding="utf-8")


def reg(reg_id="20", rev="20:1", text=TEXT, **extra):
    return {"regulation_id": reg_id, "revision_id": rev, "title": "한성대학교 학칙",
            "official_url": f"https://rule.example/{reg_id}", "text": text, **extra}


class PreflightTests(unittest.TestCase):
    """실제 모델 대신 대역으로 사전검증 규칙 자체를 확인한다."""

    def test_accepts_a_model_that_separates_korean(self):
        report = validate_embeddings(FakeEmbeddings(TEXT + " ".join(["조기졸업", "주차", "요금"])), 128)
        self.assertEqual(report["model"], "fake-char-bigram-384")
        self.assertGreater(report["korean_similar"], report["korean_unrelated"])

    def test_rejects_short_input_limit_wrong_dimension_and_flat_model(self):
        emb = FakeEmbeddings(TEXT)
        emb.tokenizer.model_max_length = 64
        with self.assertRaises(IndexingError):
            validate_embeddings(emb, 128)
        emb.tokenizer.model_max_length = 512

        class WrongDim(FakeEmbeddings):
            def embed_documents(self, texts):
                return [[1.0] * 10 for _ in texts]

        with self.assertRaises(IndexingError):
            validate_embeddings(WrongDim(TEXT), 128)

        class Flat(FakeEmbeddings):
            def embed_documents(self, texts):
                return [[1.0] + [0.0] * 383 for _ in texts]

        with self.assertRaises(IndexingError):
            validate_embeddings(Flat(TEXT), 128)

    def test_failed_batch_is_retried_then_raised(self):
        class Flaky(FakeEmbeddings):
            def __init__(self, corpus, failures):
                super().__init__(corpus)
                self.failures = failures

            def embed_documents(self, texts):
                if self.failures > 0:
                    self.failures -= 1
                    raise RuntimeError("일시 오류")
                return super().embed_documents(texts)

        waits = []
        vectors = _embed_in_batches(Flaky(TEXT, 2), ["가", "나", "다"], 1, sleep=waits.append)
        self.assertEqual(len(vectors), 3)
        self.assertEqual(len(waits), 2)
        with self.assertRaises(RuntimeError):
            _embed_in_batches(Flaky(TEXT, 5), ["가"], 1, sleep=waits.append)


@unittest.skipUnless(URL, "set HSRULE_TEST_DATABASE_URL to a disposable PostgreSQL with pgvector")
class IndexerTests(unittest.TestCase):
    def setUp(self):
        self.repo = OfficialRepository(URL)
        self.repo.initialize()
        self.repo.rebuild()
        with self.repo.cursor() as cursor:
            cursor.execute("DELETE FROM hs_reg_index_runs")
        self.emb = FakeEmbeddings(TEXT + "신규 조항 개정 주차 요금 일곱 학기")

    def test_visibility_change_is_applied_without_reembedding(self):
        index_regulation(reg(), self.repo, self.emb)
        calls = len(self.emb.calls)
        result = index_regulation(reg(visibility="private"), self.repo, self.emb)
        self.assertEqual(result["status"], "rescoped")
        self.assertEqual(len(self.emb.calls), calls)  # 다시 임베딩하지 않는다
        self.assertEqual(self.repo.get_revision("20:1")["visibility"], "private")
        self.assertTrue(all(c["visibility"] == "private" for c in self.repo.chunks_of("20:1")))
        self.assertEqual(index_regulation(reg(visibility="private"), self.repo, self.emb)["status"], "unchanged")
        self.assertEqual(index_regulation(reg(), self.repo, self.emb)["status"], "rescoped")

    def test_abolished_regulation_leaves_the_index(self):
        index_regulation(reg(), self.repo, self.emb)
        index_regulation(reg("30", "30:1"), self.repo, self.emb)
        report = sync_snapshot([reg(abolished=True), reg("30", "30:1")], self.repo, self.emb)
        self.assertEqual(report["abolished"], ["20:1"])
        self.assertEqual(self.repo.regulation_ids(), ["30"])
        self.assertEqual(report["removed"], [])  # 스냅샷에 남아 있으므로 누락 삭제가 아니다

    def test_run_state_is_recorded(self):
        report = sync_snapshot([reg()], self.repo, self.emb)
        run = self.repo.last_run()
        self.assertTrue(run["ok"])
        self.assertIsNotNone(run["finished_at"])
        self.assertEqual(run["snapshot_revisions"], ["20:1"])
        self.assertEqual(run["report"]["indexed"], report["indexed"])
        self.assertIsNotNone(run["report"]["preflight"])
        failing = sync_snapshot([dict(reg("30", "30:1"), official_url="")], self.repo, self.emb)
        failed_run = self.repo.last_run()
        self.assertFalse(failed_run["ok"])
        self.assertEqual(len(failed_run["report"]["failed"]), len(failing["failed"]))

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
        report = sync_snapshot([reg("20", "20:2", TEXT + "\n제 68 조 (신규)\n신규.\n")], self.repo, self.emb,
                               complete=True)
        self.assertEqual(report["removed"], ["30"])
        self.assertEqual(report["indexed"], ["20:2"])
        self.assertEqual(self.repo.regulation_ids(), ["20"])
        sync_snapshot([reg("20", "20:3", TEXT + "\n제 69 조 (신규)\n신규.\n")], self.repo, self.emb)
        self.assertEqual([r["revision_id"] for r in self.repo.list_revisions("20")], ["20:2", "20:3"])

    def test_failure_or_empty_snapshot_never_deletes(self):
        sync_snapshot([reg("20", "20:1"), reg("30", "30:1")], self.repo, self.emb)
        self.assertEqual(sync_snapshot([], self.repo, self.emb)["removed"], [])
        bad = dict(reg("20", "20:2"), official_url="")
        report = sync_snapshot([bad], self.repo, self.emb, complete=True)
        self.assertEqual(len(report["failed"]), 1)
        self.assertEqual(report["removed"], [])
        self.assertEqual(self.repo.regulation_ids(), ["20", "30"])
        self.assertEqual(self.repo.current_revision("20")["revision_id"], "20:1")


if __name__ == "__main__":
    unittest.main()
