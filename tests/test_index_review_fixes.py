"""재검토 이슈 #54·#55·#56 회귀. 일회용 PostgreSQL+pgvector와 대역 임베딩을 쓰며 실제 모델·운영 DB 검증이 아니다."""
import os
import threading
import time
import unittest
from pathlib import Path

import psycopg2

from hsrule.official_indexer import index_regulation, sync_snapshot
from hsrule.official_repository import IndexConsistencyError, OfficialRepository
from hsrule.official_retrieval import HybridRetriever
from tests.fakes import FakeEmbeddings
from tests.test_official_repository import chunk, revision, vec

URL = os.getenv("HSRULE_TEST_DATABASE_URL")
TEXT = (Path(__file__).with_name("fixtures") / "hansung_rules_excerpt.txt").read_text(encoding="utf-8")


def reg(reg_id="20", rev="20:1", title="한성대학교 학칙", url=None, text=TEXT, **extra):
    return {"regulation_id": reg_id, "revision_id": rev, "title": title,
            "official_url": url or f"https://rule.example/{reg_id}", "text": text, **extra}


class CountingEmbeddings(FakeEmbeddings):
    def __init__(self, corpus):
        super().__init__(corpus)
        self.documents = 0

    def embed_documents(self, texts):
        self.documents += len(texts)
        return super().embed_documents(texts)


@unittest.skipUnless(URL, "set HSRULE_TEST_DATABASE_URL to a disposable PostgreSQL with pgvector")
class FirstSaveRaceTests(unittest.TestCase):  # #54
    def setUp(self):
        self.repo = OfficialRepository(URL)
        self.repo.initialize()
        self.repo.rebuild()

    def run_saves(self, models):
        """메타 잠금을 다른 연결이 쥔 채로 두 저장을 시작해, 둘이 빈 메타를 동시에 보는 상황을 만든다."""
        holder = psycopg2.connect(URL)
        holder.autocommit = False
        with holder.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("hs_reg_index_meta",))
        results, threads = {}, []

        def save(reg_id, model):
            try:
                self.repo.save_revision(revision(reg_id, f"{reg_id}:1", model=model),
                                        [chunk(f"{reg_id}:1", 0, reg=reg_id)], [vec(1)])
                results[reg_id] = "ok"
            except IndexConsistencyError:
                results[reg_id] = "rejected"

        for reg_id, model in zip(("20", "30"), models):
            thread = threading.Thread(target=save, args=(reg_id, model))
            thread.start()
            threads.append(thread)
        time.sleep(1.0)
        blocked = not results  # 잠금 아래에서 기다려야 한다
        holder.commit()
        holder.close()
        for thread in threads:
            thread.join(10)
        return blocked, results

    def test_two_first_saves_with_different_models_cannot_both_win(self):
        blocked, results = self.run_saves(("model-A", "model-B"))
        self.assertTrue(blocked, "빈 메타에서도 저장이 색인 전체 잠금을 기다려야 한다")
        self.assertEqual(sorted(results.values()), ["ok", "rejected"])
        stored = {r["regulation_id"]: r["embedding_model"] for r in self.repo.list_revisions()}
        self.assertEqual(len(stored), 1)
        self.assertEqual(self.repo.index_meta()["embedding_model"], next(iter(stored.values())))

    def test_two_first_saves_with_the_same_model_both_succeed(self):
        blocked, results = self.run_saves(("model-A", "model-A"))
        self.assertTrue(blocked)
        self.assertEqual(results, {"20": "ok", "30": "ok"})
        self.assertEqual(self.repo.index_meta()["embedding_model"], "model-A")

    def test_single_first_save(self):
        self.repo.save_revision(revision(), [chunk("20:1", 0)], [vec(1)])
        self.assertEqual(self.repo.index_meta()["embedding_model"], "fake")


@unittest.skipUnless(URL, "set HSRULE_TEST_DATABASE_URL to a disposable PostgreSQL with pgvector")
class MetadataCorrectionTests(unittest.TestCase):  # #55
    def setUp(self):
        self.repo = OfficialRepository(URL)
        self.repo.initialize()
        self.repo.rebuild()
        self.emb = CountingEmbeddings(TEXT + "개정 규정")
        index_regulation(reg(), self.repo, self.emb)
        self.initial_documents = self.emb.documents

    def search_hit(self, query="제32조 성적분류"):
        return HybridRetriever(self.repo, self.emb, k=3).search(query)[0]

    def test_url_only_change_corrects_source_without_reembedding(self):
        result = index_regulation(reg(url="https://rule.example/new-20"), self.repo, self.emb)
        self.assertEqual((result["status"], result["updated"]), ("rescoped", ["official_url"]))
        self.assertEqual(self.emb.documents, self.initial_documents)
        self.assertEqual(self.search_hit()["official_url"], "https://rule.example/new-20")

    def test_title_change_reindexes_and_reaches_search_results(self):
        result = index_regulation(reg(title="한성대학교 학칙(개정)"), self.repo, self.emb)
        self.assertEqual(result["status"], "indexed")
        self.assertGreater(self.emb.documents, self.initial_documents)
        self.assertEqual(self.search_hit()["title"], "한성대학교 학칙(개정)")

    def test_identical_input_is_still_unchanged(self):
        self.assertEqual(index_regulation(reg(), self.repo, self.emb)["status"], "unchanged")

    def test_failed_title_reindex_keeps_the_previous_index(self):
        class Broken(CountingEmbeddings):
            def embed_documents(self, texts):
                raise RuntimeError("임베딩 실패")

        with self.assertRaises(RuntimeError):
            index_regulation(reg(title="바뀐 제목"), self.repo, Broken(TEXT), attempts=1, sleep=lambda _: None)
        self.assertEqual(self.repo.get_revision("20:1")["title"], "한성대학교 학칙")
        self.assertEqual(self.search_hit()["title"], "한성대학교 학칙")


@unittest.skipUnless(URL, "set HSRULE_TEST_DATABASE_URL to a disposable PostgreSQL with pgvector")
class AbortedRunTests(unittest.TestCase):  # #56
    def setUp(self):
        self.repo = OfficialRepository(URL)
        self.repo.initialize()
        self.repo.rebuild()
        with self.repo.cursor() as cursor:
            cursor.execute("DELETE FROM hs_reg_index_runs")
        self.emb = FakeEmbeddings(TEXT + "개정 규정")

    def test_pruning_failure_is_not_recorded_as_success(self):
        class PruneFails(OfficialRepository):
            def prune_revisions(self, *args, **kwargs):
                raise RuntimeError("정리 실패")

        failing = PruneFails(URL)
        with self.assertRaises(RuntimeError):
            sync_snapshot([reg()], failing, self.emb)
        run = self.repo.last_run()
        self.assertFalse(run["ok"])
        self.assertIsNotNone(run["finished_at"])
        self.assertEqual(run["report"]["aborted"]["error_type"], "RuntimeError")
        self.assertIn("20:1", run["report"]["indexed"])  # 이미 색인한 개정은 되돌리지 않는다(이전 색인 보존)

    def test_retry_after_abort_continues_and_succeeds(self):
        class DeleteFails(OfficialRepository):
            def delete_regulation(self, regulation_id):
                raise RuntimeError("삭제 실패")

        sync_snapshot([reg("30", "30:1")], self.repo, self.emb)
        with self.assertRaises(RuntimeError):
            sync_snapshot([reg()], DeleteFails(URL), self.emb)  # 스냅샷에서 빠진 규정 30의 삭제가 실패한다
        self.assertFalse(self.repo.last_run()["ok"])
        self.assertIsNotNone(self.repo.get_revision("30:1"))  # 삭제 실패 시 이전 색인은 그대로다
        report = sync_snapshot([reg()], self.repo, self.emb)
        self.assertEqual((report["unchanged"], report["removed"]), (["20:1"], ["30"]))
        self.assertTrue(self.repo.last_run()["ok"])

    def test_report_save_failure_does_not_hide_the_original_error(self):
        class BothFail(OfficialRepository):
            def prune_revisions(self, *args, **kwargs):
                raise RuntimeError("정리 실패")

            def finish_run(self, *args, **kwargs):
                raise ConnectionError("보고 저장 실패")

        with self.assertRaises(RuntimeError) as ctx:
            sync_snapshot([reg()], BothFail(URL), self.emb)
        self.assertEqual(str(ctx.exception), "정리 실패")
        self.assertIsNone(self.repo.last_run()["finished_at"])  # 마감되지 않은 실행으로 남는다

    def test_report_save_failure_after_success_is_raised(self):
        class SaveFails(OfficialRepository):
            def finish_run(self, *args, **kwargs):
                raise ConnectionError("보고 저장 실패")

        with self.assertRaises(ConnectionError):
            sync_snapshot([reg()], SaveFails(URL), self.emb)
        self.assertIsNone(self.repo.last_run()["finished_at"])

    def test_normal_run_has_no_abort_marker(self):
        report = sync_snapshot([reg()], self.repo, self.emb)
        self.assertNotIn("aborted", report)
        self.assertTrue(self.repo.last_run()["ok"])


if __name__ == "__main__":
    unittest.main()
