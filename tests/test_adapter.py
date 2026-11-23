import json
import unittest

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from hsrule.adapter import RegulationAdapter
from tests.test_conflict import FOUND, Searcher
from tests.test_answering import GOOD


class AdapterTests(unittest.TestCase):
    def test_answer_and_conflict_follow_backend_contract(self):
        model = GenericFakeChatModel(messages=iter([AIMessage(GOOD), AIMessage(json.dumps(FOUND))]))
        adapter = RegulationAdapter(model, Searcher())
        answer = adapter.answer("조기졸업?", [], False, language="ko")
        self.assertEqual(set(answer), {"answer", "sources", "found", "followups"})
        self.assertEqual(answer["sources"][0]["article_id"], "제40조")
        self.assertEqual(answer["sources"][0]["source_kind"], "official_html")
        conflict = adapter.analyze_conflict("조기졸업은 5학기에 가능하다.", "a.txt", True, language="ko")
        self.assertEqual(set(conflict), {"summary", "conflicts", "recommendations", "scope",
                                         "recommendation_details"})
        self.assertEqual(conflict["conflicts"][0]["severity"], "높음")
        self.assertEqual(conflict["recommendations"],
                         "- 제40조에 맞게 7학기로 수정 (근거: 한성대학교 학칙 제40조)")
        self.assertEqual(conflict["recommendation_details"][0]["article"], "제40조")


if __name__ == "__main__":
    unittest.main()
