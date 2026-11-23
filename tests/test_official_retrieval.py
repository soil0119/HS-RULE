import os
import unittest
from pathlib import Path

from hsrule.official_indexer import index_regulation
from hsrule.official_repository import OfficialRepository
from hsrule.official_retrieval import HybridRetriever, fuse, query_articles, query_terms
from tests.fakes import FakeEmbeddings

URL = os.getenv("HSRULE_TEST_DATABASE_URL")
TEXT = (Path(__file__).with_name("fixtures") / "hansung_rules_excerpt.txt").read_text(encoding="utf-8")
NEW = TEXT.replace("조기졸업을 할 수 있다. (2022.1.27.) 단,", "조기졸업을 할 수 있다. 개정됨 단,")


def reg(reg_id="20", rev="20:1", text=TEXT, visibility="public"):
    return {"regulation_id": reg_id, "revision_id": rev, "title": "한성대학교 학칙", "visibility": visibility,
            "official_url": f"https://rule.example/{reg_id}", "text": text}


class QueryParsingTests(unittest.TestCase):
    def test_articles_and_terms(self):
        self.assertEqual(query_articles("제 32 조와 제3조의2, 40조는?"), ["제32조", "제3조의2", "제40조"])
        self.assertEqual(query_articles("휴학은 어떻게 하나요"), [])
        self.assertIn("조기졸업", query_terms("조기졸업은 어떻게 신청하나요?"))
        self.assertNotIn("어떻게", query_terms("조기졸업은 어떻게 신청하나요?"))

    def test_fuse_prefers_items_found_by_several_retrievers(self):
        a, b, c = ({"id": x, "score": 1.0} for x in "abc")
        fused = fuse([("keyword", 1.0, [a, b]), ("vector", 1.0, [c, b])])
        self.assertEqual(fused[0]["id"], "b")
        self.assertEqual(sorted(fused[0]["found_by"]), ["keyword", "vector"])


@unittest.skipUnless(URL, "set HSRULE_TEST_DATABASE_URL to a disposable PostgreSQL with pgvector")
class RetrievalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = OfficialRepository(URL)
        cls.repo.initialize()
        with cls.repo.cursor() as cursor:
            cursor.execute("TRUNCATE hs_reg_revisions CASCADE")
        cls.emb = FakeEmbeddings(TEXT)
        index_regulation(reg(), cls.repo, cls.emb)
        index_regulation(reg("99", "99:1", "내부 인사 규정\n제 1 조 (비공개)\n비공개 인사 기준을 정한다.\n",
                             visibility="private"), cls.repo, cls.emb)
        cls.retriever = HybridRetriever(cls.repo, cls.emb, k=5)

    def ids(self, hits):
        return [h["article_id"] for h in hits]

    def test_exact_article_comes_first(self):
        hits = self.retriever.search("제32조 성적분류는?")
        self.assertEqual(hits[0]["article_id"], "제32조")
        self.assertIn("article", hits[0]["found_by"])
        self.assertEqual(hits[0]["official_url"], "https://rule.example/20")
        self.assertEqual(hits[0]["revision_id"], "20:1")

    def test_keyword_finds_synonym_free_term(self):
        hits = self.retriever.search("조기졸업 신청은 언제까지 해야 하나요")
        self.assertIn("제40조", self.ids(hits)[:3])

    def test_cross_reference_expansion(self):
        hits = self.retriever.search("제3조 입학정원")
        self.assertEqual(hits[0]["article_id"], "제3조")
        retriever = HybridRetriever(self.repo, self.emb, k=2)
        hits = retriever.search("조기졸업 학석사연계과정 요건")
        self.assertTrue(any(h["found_by"] == ["reference"] for h in hits) or "제40조" in self.ids(hits))

    def test_private_and_deleted_are_filtered(self):
        self.assertNotIn("99", {h["regulation_id"] for h in self.retriever.search("비공개 인사 기준")})
        with_private = self.retriever.search("비공개 인사 기준", include_private=True)
        self.assertEqual(with_private[0]["regulation_id"], "99")
        self.assertNotIn("제17조", self.ids(self.retriever.search("제17조 입학생 등록 및 구비서류")))
        deleted = self.repo.article_candidates(["제17조"], include_deleted=True)
        self.assertTrue(deleted and deleted[0]["deleted"])

    def test_revision_filter_current_vs_explicit(self):
        index_regulation(reg(rev="20:2", text=NEW), self.repo, self.emb)
        try:
            current = self.retriever.search("제40조 조기졸업")
            self.assertEqual({h["revision_id"] for h in current}, {"20:2"})
            old = self.retriever.search("제40조 조기졸업", revision_ids=["20:1"])
            self.assertEqual({h["revision_id"] for h in old}, {"20:1"})
            self.assertTrue(all(h["is_current"] is False for h in old))
        finally:
            self.repo.delete_revision("20:2")
            index_regulation(reg(), self.repo, self.emb, force=True)

    def test_empty_query_and_limit(self):
        self.assertEqual(self.retriever.search("   "), [])
        self.assertLessEqual(len(self.retriever.search("학점", k=3)), 3 + 3)


if __name__ == "__main__":
    unittest.main()
