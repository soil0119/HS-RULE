"""평가셋 검수와 분할 집계 회귀 (#77). 정답 조항 ID가 실제 학칙 발췌본 청크에 있는지 확인한다."""
import json
import unittest
from pathlib import Path

from hsrule.official_chunking import chunk_regulation
from scripts import run_rag_eval as ev
from tests.test_official_chunking import local_tokenizer, regulation

ROOT = Path(__file__).resolve().parent.parent
CASES = [json.loads(line) for line in (ROOT / "eval/regulation_eval.jsonl").read_text(encoding="utf-8").splitlines()
         if line.strip()]
TYPES = {"exact_article", "keyword", "short_query", "wrong_regulation_name", "synonym", "table", "cross_reference",
         "addendum", "followup", "english", "no_evidence", "deleted_regression", "admission_year", "needs_condition",
         "program"}


class EvalSetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        text = (ROOT / "tests/fixtures/hansung_rules_excerpt.txt").read_text(encoding="utf-8")
        cls.articles = {c["article_id"] for c in chunk_regulation(regulation(text), local_tokenizer(text), 128, 16)}

    def test_cases_are_well_formed(self):
        ids = [case["id"] for case in CASES]
        self.assertEqual(len(ids), len(set(ids)), "사례 id가 겹친다")
        for case in CASES:
            with self.subTest(case=case["id"]):
                self.assertIn(case["split"], {"tune", "holdout"})
                self.assertIn(case["type"], TYPES)
                self.assertIn(case["language"], {"ko", "en"})
                self.assertTrue(case["question"].strip())
                if case.get("expected_found") is False:
                    self.assertFalse(case.get("expected_articles"))
                if "expected_clarification" in case:
                    self.assertIsInstance(case["expected_clarification"], bool)
                self.assertLessEqual(set(case.get("required_articles", [])), set(case.get("expected_articles", [])))
                for role in (m["role"] for m in case.get("history", [])):
                    self.assertIn(role, {"user", "assistant"})

    def test_answer_articles_exist_in_the_corpus(self):
        for case in CASES:
            for key in ("expected_articles", "required_articles", "expect_absent_articles"):
                for article in case.get(key, []):
                    with self.subTest(case=case["id"], article=article):
                        self.assertIn(article, self.articles)

    def test_holdout_covers_the_main_kinds(self):
        holdout = [case for case in CASES if case["split"] == "holdout"]
        self.assertGreaterEqual(len(holdout), 10)
        self.assertTrue({"en", "ko"} <= {case["language"] for case in holdout})
        kinds = {case["type"] for case in holdout}
        for kind in ("no_evidence", "admission_year", "needs_condition", "followup", "table", "addendum"):
            self.assertIn(kind, kinds)
        self.assertTrue(any(case.get("expected_clarification") is True for case in holdout))
        self.assertTrue(any(case.get("expected_clarification") is False for case in holdout))


class SplitMetricTests(unittest.TestCase):
    def test_required_articles_must_all_be_found(self):
        a2 = "부칙(2016.2.3공포)제2조"
        self.assertIsNone(ev.case_rank(["제34조", "제33조"], {a2, "제34조"}, [a2]))
        self.assertEqual(ev.case_rank(["제34조", a2], {a2, "제34조"}, [a2]), 2)
        self.assertEqual(ev.case_rank(["제34조", a2], {a2, "제34조"}), 1)

    def test_metrics_are_split(self):
        rows = [{"split": "tune", "rank": 1}, {"split": "tune", "rank": None}, {"split": "holdout", "rank": 2}]
        result = ev.split_summaries(rows, ev.retrieval_metrics)
        self.assertEqual(result["tune"]["recall"]["@1"], 0.5)
        self.assertEqual(result["holdout"]["mrr"], 0.5)

    def test_clarification_and_grounding_summaries(self):
        rows = [{"clarification_asked": True, "expected_clarification": True},
                {"clarification_asked": False, "expected_clarification": True},
                {"clarification_asked": True, "expected_clarification": False},
                {"clarification_asked": False, "expected_clarification": None}]
        result = ev.summarize_clarification(rows)
        self.assertEqual((result["asked_when_needed"], result["unnecessary_rate"]), (0.5, 1.0))
        self.assertEqual(ev.summarize_clarification([{"clarification_asked": None, "expected_clarification": True}]),
                         {"measured": 0, "asked_when_needed": None, "unnecessary_rate": None})
        grounding = ev.summarize_grounding([{"grounding_verified": True, "grounding_repaired": True},
                                            {"grounding_verified": False, "grounding_repaired": False}])
        self.assertEqual((grounding["verified_rate"], grounding["repaired"]), (0.5, 1))


if __name__ == "__main__":
    unittest.main()
