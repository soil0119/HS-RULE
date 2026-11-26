"""재검토 이슈 #58·#59 회귀. 모델·검색기는 대역이며 실제 모델 품질을 측정한 결과가 아니다."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from hsrule.answering import RagError
from scripts import run_rag_eval as ev
from tests.test_chains import StubSearcher
from tests.test_conflict import FOUND

URL = os.getenv("HSRULE_TEST_DATABASE_URL")
EVAL_SET = Path(__file__).resolve().parent.parent / "eval" / "regulation_eval.jsonl"


class CitationJudgementTests(unittest.TestCase):  # #59
    def test_unrelated_article_is_a_failure(self):
        self.assertFalse(ev.citation_matches(["제32조"], ["제34조"]))
        self.assertFalse(ev.citation_matches(["제32조", "제34조"], ["제34조"]))  # 정답 목록 밖 인용이 섞이면 실패
        self.assertTrue(ev.citation_matches(["제34조"], ["제34조"]))
        self.assertFalse(ev.citation_matches([], ["제34조"]))
        self.assertFalse(ev.citation_matches(["제34조"], None))

    def test_alternatives_and_required_evidence_are_separate(self):
        expected, required = ["제40조", "제41조"], ["제40조"]
        self.assertTrue(ev.citation_matches(["제40조"], expected, required))
        self.assertTrue(ev.citation_matches(["제40조", "제41조"], expected, required))
        self.assertFalse(ev.citation_matches(["제41조"], expected, required))  # 필요한 근거를 빠뜨림

    def test_graduation_credit_case_expects_article_34_only(self):
        cases = {c["id"]: c for c in map(json.loads, EVAL_SET.read_text(encoding="utf-8").splitlines() if EVAL_SET.exists() else [])}
        self.assertEqual(cases["short-credit"]["expected_articles"], ["제34조"])

    def test_retrieval_rank_ignores_grade_articles_for_credit_question(self):
        self.assertIsNone(ev.first_rank(["제32조", "제33조"], {"제34조"}))
        self.assertEqual(ev.first_rank(["제32조", "제34조"], {"제34조"}), 2)


class FlakyAnswerer:
    """둘째 사례에서만 RagError를 내는 대역."""

    def __init__(self, fail_on=(1,), reason="timeout"):
        self.calls, self.fail_on, self.reason = 0, set(fail_on), reason

    def answer(self, question, history=None, *, language="ko", **kwargs):
        index = self.calls
        self.calls += 1
        if index in self.fail_on:
            raise RagError("rag_backend_error", "시간 초과", self.reason)
        source = {"label": "S1", "chunk_id": "20:1:3", "regulation_id": "20", "revision_id": "20:1",
                  "article_id": "제34조", "url": "https://rule.example/20"}
        return {"answer": "130학점", "found": True, "sources": [source], "followups": []}


CASES = [
    {"id": "a", "type": "short_query", "language": "ko", "question": "졸업학점", "expected_articles": ["제34조"]},
    {"id": "b", "type": "short_query", "language": "ko", "question": "조기졸업", "expected_articles": ["제34조"]},
    {"id": "c", "type": "absent", "language": "ko", "question": "주차 요금", "expected_articles": [],
     "expected_found": False},
]


class AnswerEvaluationTests(unittest.TestCase):  # #58
    def test_error_in_one_case_does_not_stop_the_rest(self):
        result = ev.evaluate_answers(CASES, FlakyAnswerer(fail_on=(1,)), [{"revision_id": "20:1"}])
        rows = {r["id"]: r for r in result["cases"]}
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows["b"]["error"], {"code": "rag_backend_error", "reason": "timeout"})
        self.assertIsNone(rows["b"]["found"])
        self.assertTrue(rows["a"]["citation_match"])
        self.assertIn("seconds", rows["b"])

    def test_summary_denominators_count_errors_explicitly(self):
        summary = ev.evaluate_answers(CASES, FlakyAnswerer(fail_on=(1,)), [{"revision_id": "20:1"}])["summary"]
        self.assertEqual((summary["cases"], summary["completed"], summary["errors"]), (3, 2, 1))
        self.assertEqual(summary["error_reasons"], ["rag_backend_error/timeout"])
        self.assertEqual(summary["denominators"], {"answerable": 2, "answerable_completed": 1,
                                                   "unanswerable": 1, "unanswerable_completed": 1})
        self.assertEqual(summary["citation_match_rate"], 0.5)  # 오류 사례는 성공으로 세지 않는다(분모 2)
        self.assertEqual(summary["unsupported_answer_rate"], 1.0)  # 모델이 무근거 질문에도 답한 사례 1/1
        self.assertEqual(summary["correct_refusal_rate"], 0.0)

    def test_unexpected_error_is_recorded_without_its_message(self):
        class Broken(FlakyAnswerer):
            def answer(self, question, history=None, **kwargs):
                if self.calls == 0:
                    self.calls += 1
                    raise KeyError("key=gsk_secret 본문")
                return super().answer(question, history, **kwargs)

        result = ev.evaluate_answers(CASES, Broken(fail_on=()), [{"revision_id": "20:1"}])
        first = result["cases"][0]
        self.assertEqual(first["error"], {"code": "unexpected_error", "reason": "KeyError"})
        self.assertNotIn("gsk_secret", json.dumps(result, ensure_ascii=False))
        self.assertEqual(result["summary"]["completed"], 2)

    def test_all_errors_leave_rates_undefined_not_zero_division(self):
        summary = ev.evaluate_answers(CASES, FlakyAnswerer(fail_on=(0, 1, 2), reason="json"),
                                      [{"revision_id": "20:1"}])["summary"]
        self.assertEqual((summary["completed"], summary["errors"]), (0, 3))
        self.assertIsNone(summary["revision_match_rate"])
        self.assertIsNone(summary["unsupported_answer_rate"])
        self.assertEqual(summary["citation_match_rate"], 0.0)


class SequencedLLM(GenericFakeChatModel):
    """첫 호출은 timeout, 이후는 정해진 JSON을 반환한다."""

    calls: int = 0

    def _generate(self, messages, *args, **kwargs):
        self.calls += 1
        if self.calls == 1:
            raise TimeoutError("시간 초과")
        return super()._generate(messages, *args, **kwargs)


class ConflictEvaluationTests(unittest.TestCase):  # #58
    def test_timeout_in_one_conflict_case_keeps_the_others(self):
        cases = [{"id": f"c{i}", "type": "conflict", "language": "ko", "filename": "지침.txt",
                  "document": "본 지침은 5학기에 조기졸업을 허용한다.", "expected_articles": ["제40조"]} for i in range(2)]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.jsonl"
            path.write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in cases), encoding="utf-8")
            llm = SequencedLLM(messages=iter([AIMessage(json.dumps(FOUND, ensure_ascii=False))]))
            result = ev.conflict_evaluation(path, StubSearcher(), llm)
        self.assertEqual((result["cases"], result["analyzed"], result["errors"]), (2, 1, 1))
        first, second = result["per_case"]
        self.assertEqual(first["error"]["reason"], "timeout")
        self.assertNotIn("error", second)
        self.assertEqual(second["expected_found"], ["제40조"])
        self.assertIn("candidate_recall", first)  # 모델 오류와 무관한 후보 재현율은 보존한다

    def test_without_model_nothing_is_counted_as_analyzed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.jsonl"
            path.write_text(json.dumps({"id": "x", "type": "conflict", "language": "ko", "filename": "a.txt",
                                        "document": "문서", "expected_articles": ["제40조"]}), encoding="utf-8")
            result = ev.conflict_evaluation(path, StubSearcher(), None)
        self.assertEqual((result["analyzed"], result["errors"]), (0, 0))


@unittest.skipUnless(URL, "set HSRULE_TEST_DATABASE_URL to a disposable PostgreSQL with pgvector")
class PartialResultTests(unittest.TestCase):  # #58
    def test_unexpected_conflict_failure_still_saves_the_result_with_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            argv = ["run_rag_eval.py", "--mode", "standin", "--out", directory]
            with mock.patch.dict(os.environ, {"DATABASE_URL": URL}), mock.patch.object(sys, "argv", argv), \
                    mock.patch.object(ev, "conflict_evaluation", side_effect=RuntimeError("예상 밖 오류")):
                ev.main()
            saved = sorted(Path(directory).glob("*-standin.json"))
            self.assertEqual(len(saved), 1)
            result = json.loads(saved[0].read_text(encoding="utf-8"))
        self.assertEqual(result["errors"], [{"stage": "conflicts", "error": "RuntimeError"}])
        self.assertTrue(result["retrieval"]["hybrid"]["cases"] > 0)  # 이미 계산한 검색 결과는 보존된다
        self.assertEqual(result["conflicts"]["errors"], 1)


if __name__ == "__main__":
    unittest.main()
