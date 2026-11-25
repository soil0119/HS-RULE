import os
import unittest
from pathlib import Path

from hsrule.official_indexer import index_regulation
from hsrule.official_repository import OfficialRepository
from hsrule.official_retrieval import (MAX_CANDIDATES, MAX_K, HybridRetriever, fuse, query_articles,
                                       query_terms)
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
        self.assertIn("A+", query_terms("A+ 학점 기준"))
        self.assertIn("A⁺", query_terms("A⁺ 학점 기준"))
        self.assertNotIn("은", query_terms("학점은"))

    def test_fuse_prefers_items_found_by_several_retrievers(self):
        a, b, c = ({"id": x, "score": 1.0} for x in "abc")
        fused = fuse([("keyword", 1.0, [a, b]), ("vector", 1.0, [c, b])])
        self.assertEqual(fused[0]["id"], "b")
        self.assertEqual(sorted(fused[0]["found_by"]), ["keyword", "vector"])

    def test_fuse_keeps_score_kinds_and_never_invents_a_similarity(self):
        exact = {"id": "x", "score": None, "score_kind": "exact"}
        lexical = {"id": "x", "score": 3.5, "score_kind": "lexical"}
        vector = {"id": "x", "score": 0.81, "score_kind": "cosine"}
        entry = fuse([("article", 3.0, [exact]), ("keyword", 1.0, [lexical]), ("vector", 1.0, [vector])])[0]
        self.assertEqual(entry["scores"]["article"], {"kind": "exact", "value": None})
        self.assertEqual(entry["scores"]["keyword"], {"kind": "lexical", "value": 3.5})
        self.assertEqual(entry["cosine"], 0.81)
        self.assertNotEqual(entry["fused_score"], 1.0)  # RRF 점수는 유사도가 아니다
        only_exact = fuse([("article", 3.0, [exact])])[0]
        self.assertIsNone(only_exact["cosine"])


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

    def test_limits_are_capped_and_reported(self):
        retriever = HybridRetriever(self.repo, self.emb, k=99, candidates=999)
        self.assertEqual(retriever.k, MAX_K)
        self.assertEqual(retriever.candidates, MAX_CANDIDATES)
        self.assertEqual(retriever.limits["search_timeout_ms"], self.repo.SEARCH_TIMEOUT_MS)
        core = [h for h in retriever.search("학점", k=99) if h["found_by"] != ["reference"]]
        self.assertLessEqual(len(core), MAX_K)
        long_query = "조기졸업 " * 2000
        self.assertTrue(retriever.search(long_query))  # 길이 상한에서 잘리고도 동작한다

    def test_weak_vector_only_candidates_count_as_no_evidence(self):
        strict = HybridRetriever(self.repo, self.emb, k=5, min_cosine=0.99)
        hits = strict.search("학생식당 운영 시간과 메뉴 가격")
        self.assertEqual([h for h in hits if h["found_by"] == ["vector"]], [])
        loose = HybridRetriever(self.repo, self.emb, k=5, min_cosine=0.0)
        self.assertTrue(loose.search("학생식당 운영 시간과 메뉴 가격"))
        exact = strict.search("제40조")
        self.assertTrue(exact)  # 조항 일치는 유사도 기준을 적용하지 않는다

    def test_short_query_and_wrong_regulation_name(self):
        self.assertTrue(any(h["article_id"] == "제40조" for h in self.retriever.search("조기졸업")))
        wrong_name = self.retriever.search("한성대 학사규정 조기졸업 요건")
        self.assertIn("제40조", [h["article_id"] for h in wrong_name])
        grades = self.retriever.search("성적처리지침에서 A+ 기준이 뭔가요?")
        self.assertIn("제32조", [h["article_id"] for h in grades])

    def test_mixing_two_revisions_of_one_regulation_is_rejected(self):
        index_regulation(reg(rev="20:2", text=NEW), self.repo, self.emb)
        try:
            with self.assertRaises(ValueError):
                self.retriever.search("조기졸업", revision_ids=["20:1", "20:2"])
        finally:
            self.repo.delete_revision("20:2")
            index_regulation(reg(), self.repo, self.emb, force=True)

    def test_retriever_vector_query_uses_the_partial_index(self):
        with self.repo.cursor() as cursor:
            cursor.execute("SET LOCAL enable_seqscan = off")
            cursor.execute(f"""
                EXPLAIN SELECT {self.repo._CANDIDATE}, 1 - (c.embedding <=> %s::vector) AS score
                FROM hs_reg_chunks c JOIN hs_reg_revisions r USING (revision_id)
                WHERE c.searchable AND c.visibility = 'public' AND NOT c.deleted
                ORDER BY c.embedding <=> %s::vector LIMIT 20
            """, (str([1.0] + [0.0] * 383), str([1.0] + [0.0] * 383)))
            plan = "\n".join(row["QUERY PLAN"] for row in cursor.fetchall())
        self.assertIn("hs_reg_chunks_embedding", plan)


if __name__ == "__main__":
    unittest.main()
