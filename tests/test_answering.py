import os
import unittest
from pathlib import Path

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from hsrule.answering import REFUSAL, CitedAnswerer, RagError, extract_json
from tests.test_chains import StubSearcher


SECRET = "gsk_live_abcdef123456"


class ErrorLLM(GenericFakeChatModel):
    def _generate(self, *args, **kwargs):
        raise RuntimeError(f"provider 401: key={SECRET} body=규정 원문 일부")


def model_with(*texts):
    return GenericFakeChatModel(messages=iter(AIMessage(t) for t in texts))


def answerer(*texts, searcher=None):
    return CitedAnswerer(model_with(*texts), searcher or StubSearcher())


class EmptySearcher:
    def search(self, *args, **kwargs):
        return []


GOOD = '{"found": true, "answer": "제40조에 따라 7학기에 가능합니다.", "citations": ["S1"], "followups": ["신청은?"]}'


class CitationTests(unittest.TestCase):
    def test_valid_answer_builds_sources_from_candidates(self):
        result = answerer(GOOD).answer("조기졸업?")
        self.assertTrue(result["found"])
        self.assertEqual(result["sources"][0]["article_id"], "제40조")
        self.assertEqual(result["sources"][0]["source_kind"], "official_html")
        self.assertNotIn("page_start", result["sources"][0])
        self.assertEqual(result["sources"][0]["score"], 0.8)  # 코사인 유사도만 담는다
        self.assertEqual(result["sources"][0]["url"], "https://rule.example/20")
        self.assertEqual(result["sources"][0]["revision_id"], "20:1")
        self.assertNotIn("page_start", result["sources"][0])
        self.assertEqual(result["followups"], ["신청은?"])

    def test_fenced_and_surrounded_json_is_accepted(self):
        self.assertEqual(extract_json("설명\n```json\n{\"a\": 1}\n```")["a"], 1)
        self.assertTrue(answerer("```json\n" + GOOD + "\n```").answer("q")["found"])

    def test_unknown_and_duplicate_citations(self):
        text = '{"found": true, "answer": "답", "citations": ["S9", "S1", "S1"], "followups": []}'
        result = answerer(text).answer("q")
        self.assertEqual([s["label"] for s in result["sources"]], ["S1"])
        self.assertEqual(result["dropped_citations"], ["S9"])

    def test_found_without_valid_citation_is_rejected(self):
        for text in ('{"found": true, "answer": "답", "citations": [], "followups": []}',
                     '{"found": true, "answer": "답", "citations": ["S7"], "followups": []}'):
            with self.assertRaises(RagError) as ctx:
                answerer(text).answer("q")
            self.assertEqual(ctx.exception.code, "rag_invalid_response")

    def test_not_found_has_no_sources(self):
        text = '{"found": false, "answer": "근거가 없습니다.", "citations": ["S1"], "followups": []}'
        result = answerer(text).answer("학교 식당 메뉴는?")
        self.assertFalse(result["found"])
        self.assertEqual(result["sources"], [])

    def test_no_candidates_refuses_without_calling_model(self):
        model = ErrorLLM(messages=iter([]))
        result = CitedAnswerer(model, EmptySearcher()).answer("질문", language="en")
        self.assertEqual(result["answer"], REFUSAL["en"])
        self.assertFalse(result["found"])
        self.assertEqual(result["reason"], "no_evidence")

    def test_public_query_cannot_cite_private_material(self):
        from tests.test_chains import StubSearcher, hit

        private = StubSearcher([dict(hit(), visibility="private")])
        with self.assertRaises(RagError) as ctx:
            CitedAnswerer(model_with(GOOD), private).answer("비공개 인사 기준")
        self.assertEqual((ctx.exception.code, ctx.exception.reason), ("rag_invalid_response", "scope"))
        admin = CitedAnswerer(model_with(GOOD), private).answer("비공개 인사 기준", include_private=True)
        self.assertTrue(admin["found"])

    def test_error_reasons_are_distinct_and_hide_provider_details(self):
        with self.assertRaises(RagError) as ctx:
            answerer("아님", "{broken").answer("q")
        self.assertEqual(ctx.exception.reason, "json")
        with self.assertRaises(RagError) as ctx:
            CitedAnswerer(ErrorLLM(messages=iter([])), StubSearcher()).answer("q")
        self.assertEqual(ctx.exception.reason, "provider")
        self.assertNotIn(SECRET, str(ctx.exception))
        self.assertNotIn("규정 원문", str(ctx.exception))
        with self.assertRaises(RagError) as ctx:
            answerer('{"found": true, "answer": "답", "citations": ["S9"], "followups": []}').answer("q")
        self.assertEqual(ctx.exception.reason, "citation")

    def test_timeout_is_separate_from_provider_failure(self):
        from hsrule.chains import ChainTimeout

        class Slow(GenericFakeChatModel):
            def _generate(self, *args, **kwargs):
                raise ChainTimeout("느림")

        with self.assertRaises(RagError) as ctx:
            CitedAnswerer(Slow(messages=iter([])), StubSearcher()).answer("q")
        self.assertEqual((ctx.exception.code, ctx.exception.reason), ("rag_backend_error", "timeout"))

    def test_bad_json_is_retried_once_then_rejected(self):
        self.assertTrue(answerer("이건 JSON이 아님", GOOD).answer("q")["found"])
        with self.assertRaises(RagError) as ctx:
            answerer("아님", "{broken").answer("q")
        self.assertEqual(ctx.exception.code, "rag_invalid_response")
        with self.assertRaises(RagError) as ctx:
            answerer('{"found": true, "answer": "", "citations": ["S1"]}', '{"x": 1}').answer("q")
        self.assertEqual(ctx.exception.code, "rag_invalid_response")

    def test_model_failure_maps_to_backend_error(self):
        with self.assertRaises(RagError) as ctx:
            CitedAnswerer(ErrorLLM(messages=iter([])), StubSearcher()).answer("q")
        self.assertEqual(ctx.exception.code, "rag_backend_error")

    def test_search_failure_maps_to_backend_error_but_bad_input_does_not(self):
        class Broken:
            def search(self, *a, **k):
                raise ConnectionError("db down")

        with self.assertRaises(RagError) as ctx:
            CitedAnswerer(ErrorLLM(messages=iter([])), Broken()).answer("q")
        self.assertEqual(ctx.exception.code, "rag_backend_error")
        with self.assertRaises(ValueError):
            answerer(GOOD).answer("   ")

    def test_followups_are_capped(self):
        text = '{"found": true, "answer": "답", "citations": ["S1"], "followups": ["a","b","c","d"]}'
        self.assertEqual(len(answerer(text).answer("q")["followups"]), 3)


URL = os.getenv("HSRULE_TEST_DATABASE_URL")


@unittest.skipUnless(URL, "set HSRULE_TEST_DATABASE_URL to a disposable PostgreSQL with pgvector")
class EndToEndWithDatabaseTests(unittest.TestCase):
    """실제 PostgreSQL 검색 + 대역 모델. 실제 LLM 품질 검증이 아니다."""

    def test_citation_maps_to_real_indexed_article(self):
        from hsrule.official_indexer import index_regulation
        from hsrule.official_repository import OfficialRepository
        from hsrule.official_retrieval import HybridRetriever
        from tests.fakes import FakeEmbeddings

        text = (Path(__file__).with_name("fixtures") / "hansung_rules_excerpt.txt").read_text(encoding="utf-8")
        repo, emb = OfficialRepository(URL), FakeEmbeddings(text)
        repo.initialize()
        with repo.cursor() as cursor:
            cursor.execute("TRUNCATE hs_reg_revisions CASCADE")
        index_regulation({"regulation_id": "20", "revision_id": "20:1", "title": "한성대학교 학칙",
                          "official_url": "https://rule.example/20", "text": text}, repo, emb)
        result = answerer(GOOD, searcher=HybridRetriever(repo, emb)).answer("제40조 조기졸업 조건")
        source = result["sources"][0]
        self.assertEqual(source["article_id"], "제40조")
        self.assertIn("조기졸업", text[source["source_start"]:source["source_end"]])


if __name__ == "__main__":
    unittest.main()
