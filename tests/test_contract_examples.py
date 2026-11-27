"""공유 계약 예시(#68)가 실제 어댑터 출력과 같은 모양인지 확인한다. 값이 아니라 키·형식을 비교한다.

tests/fixtures/contracts/*.json은 웹 API(#66·#67)·프론트(#69·#70)와 공유하는 고정 예시다. 어댑터 출력의 키나
형식을 바꾸면 이 테스트가 실패하므로, 예시와 docs/rag-contract.md를 함께 고치고 팀에 알린다.
"""
import json
import unittest
from pathlib import Path

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from hsrule.adapter import RegulationAdapter
from tests.test_answering import GOOD
from tests.test_conflict import FOUND, Searcher

FIXTURES = Path(__file__).with_name("fixtures") / "contracts"


def shape_problems(expected, actual, path="$"):
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [f"{path}: 객체가 아님"]
        problems = [f"{path}: 키 차이 {sorted(set(expected) ^ set(actual))}"] if set(expected) != set(actual) else []
        for key in set(expected) & set(actual):
            problems += shape_problems(expected[key], actual[key], f"{path}.{key}")
        return problems
    if isinstance(expected, list):
        if not isinstance(actual, list):
            return [f"{path}: 목록이 아님"]
        return [p for item in actual[:3] for p in shape_problems(expected[0], item, f"{path}[]")] if expected else []
    if expected is None:  # 예시에서 null인 값(score 등)은 숫자일 수도 있다.
        return [] if actual is None or isinstance(actual, (int, float)) else [f"{path}: null/숫자가 아님"]
    if isinstance(expected, bool) or isinstance(actual, bool):
        return [] if type(expected) is type(actual) else [f"{path}: 형식 {type(actual).__name__}"]
    if isinstance(expected, (int, float)):
        return [] if isinstance(actual, (int, float)) else [f"{path}: 숫자가 아님"]
    return [] if isinstance(actual, type(expected)) else [f"{path}: 형식 {type(actual).__name__}"]


class ContractExampleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        model = GenericFakeChatModel(messages=iter([AIMessage(GOOD), AIMessage(json.dumps(FOUND, ensure_ascii=False))]))
        adapter = RegulationAdapter(model, Searcher())
        cls.answer = adapter.answer("조기졸업 요건은?", [], False, language="ko")
        cls.conflict = adapter.analyze_conflict("본 지침은 5학기에 조기졸업을 허용한다.", "학과지침.txt", True, language="ko")

    def load(self, name):
        return json.loads((FIXTURES / name).read_text(encoding="utf-8"))

    def test_answer_matches_the_shared_example(self):
        example = self.load("rag_answer_official_html.json")
        self.assertEqual(shape_problems(example, self.answer), [])
        self.assertEqual(example["sources"][0]["source_kind"], "official_html")
        self.assertNotIn("page_start", example["sources"][0])  # 공식 HTML 출처에는 페이지가 없다

    def test_conflict_matches_the_shared_example(self):
        example = self.load("rag_conflict_result.json")
        self.assertEqual(shape_problems(example, self.conflict), [])
        for key in ("passages_analyzed", "passages_total", "truncated", "stopped_for_time", "dropped_findings"):
            self.assertIn(key, example["scope"])

    def test_shape_check_detects_drift(self):
        example = self.load("rag_answer_official_html.json")
        drifted = json.loads(json.dumps(self.answer))
        drifted["sources"][0]["page_start"] = 1
        del drifted["followups"]
        self.assertEqual(len(shape_problems(example, drifted)), 2)


if __name__ == "__main__":
    unittest.main()
