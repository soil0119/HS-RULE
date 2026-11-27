"""질문 조건 확인·근거 충실도·검색 질문 조건 보존 회귀 (#78). 모델은 대역이며 실제 답변 품질을 측정하지 않는다."""
import json
import unittest

from hsrule.answering import CitedAnswerer
from hsrule.chains import RegulationChain, preserve_terms
from hsrule.conditions import attach_questions, check_grounding, clarification, detect_conditions, numbers_in
from tests.test_chains import StubSearcher, hit, llm

CREDIT_MAIN = hit(1, "제34조", "①본 대학교의 졸업에 필요한 학점은 130학점 이상으로 한다. (2016. 2. 3)")
CREDIT_ADDENDUM = hit(2, "부칙(2016.2.3공포)제2조",
                      "제2조 (졸업학점 변경에 따른 경과조치) ① 2015학년도 이전에 입학한 자의 졸업에 필요한 학점은 140 학점이상으로 한다.")
EARLY = hit(3, "제40조", "① 7학기만에 4학년 전과정을 이수하고 전체 성적 총 평점평균이 4.0이상이며 ... "
                         "② 학·석사연계과정생의 조기졸업인정은 ... 총 평점평균이 3.5이상인자")


def reply(answer, citations, *, found=True, needs=False, questions=(), missing=(), followups=()):
    return json.dumps({"found": found, "answer": answer, "citations": list(citations), "followups": list(followups),
                       "needs_clarification": needs, "clarifying_questions": list(questions),
                       "missing_conditions": list(missing)}, ensure_ascii=False)


class ConditionDetectionTests(unittest.TestCase):
    def test_detects_stated_conditions(self):
        found = detect_conditions(["저는 14학번 학석사연계과정생이고 AI·소프트웨어학과예요. 지금 휴학 중입니다."])
        self.assertEqual(found["admission_year"], ["2014"])
        self.assertEqual(found["program"], ["학석사연계"])
        self.assertEqual(found["department"], ["AI·소프트웨어학과"])
        self.assertIn("student_status", found)
        self.assertEqual(detect_conditions(["2015학년도에 입학했어요"])["admission_year"], ["2015"])
        self.assertEqual(detect_conditions(["I was admitted in 2014."])["admission_year"], ["2014"])

    def test_english_undergraduate_is_not_a_graduate_program(self):
        for text, expected in (("I am an undergraduate student.", ["학부"]),
                               ("I am in an undergraduate program.", ["학부"]),
                               ("I am a graduate student.", ["대학원"]),
                               ("We are graduate students.", ["대학원"]),
                               ("I attend graduate school.", ["대학원"])):
            with self.subTest(text=text):
                self.assertEqual(detect_conditions([text]).get("program", []), expected)

    def test_questions_without_conditions_find_nothing(self):
        self.assertEqual(detect_conditions(["졸업하려면 몇 학점 필요해요?", "무슨 학과든 같나요?"]), {})
        self.assertNotIn("admission_year", detect_conditions(["2026년 3월부터 적용되나요?"]))


class ClarificationTests(unittest.TestCase):
    def test_personal_info_questions_are_dropped(self):
        result = clarification(True, ["이름과 연락처를 알려 주세요.", "몇 학번이세요?"], ["admission_year"], {}, "ko")
        self.assertEqual((result["needed"], result["questions"]), (True, ["몇 학번이세요?"]))
        self.assertEqual(result["dropped_personal_questions"], 1)

    def test_already_stated_condition_is_not_asked_again(self):
        result = clarification(True, ["입학년도가 언제인가요?"], ["admission_year"], {"admission_year": ["2014"]}, "ko")
        self.assertFalse(result["needed"])
        self.assertEqual(result["questions"], [])

    def test_default_question_when_model_gave_none(self):
        result = clarification(True, [], ["program", "unknown"], {}, "en")
        self.assertEqual(result["missing"], ["program"])
        self.assertIn("bachelor's-master's", result["questions"][0])

    def test_questions_are_attached_once(self):
        text = attach_questions("일반 규정은 130학점입니다.", ["몇 학년도에 입학하셨나요?"], "ko")
        self.assertIn("몇 학년도에 입학하셨나요?", text)
        self.assertEqual(attach_questions(text, ["몇 학년도에 입학하셨나요?"], "ko"), text)


class GroundingCheckTests(unittest.TestCase):
    def test_numbers_are_normalized_and_references_ignored(self):
        self.assertEqual(numbers_in("평점 4.0 이상, 130학점 (제34조, [S1])\n2. 신청한다", answer=True), {"4", "130"})

    def test_supported_and_unsupported_figures(self):
        evidence = [CREDIT_MAIN["content"], CREDIT_ADDENDUM["content"]]
        ok = check_grounding("2015학년도 이전 입학생은 140학점, 이후는 130학점입니다(제34조).", evidence, [])
        self.assertEqual((ok["verified"], ok["checked"]), (True, 3))
        bad = check_grounding("졸업에는 120학점이 필요합니다.", evidence, [])
        self.assertEqual(bad["unsupported"], ["120"])
        user = check_grounding("2014학번이시면 140학점입니다.", evidence, ["저는 2014학번이에요"])
        self.assertTrue(user["verified"])  # 사용자가 밝힌 입학년도는 근거 없이도 쓸 수 있다

    def test_user_question_does_not_ground_a_regulation_figure(self):
        result = check_grounding("졸업에는 150학점이 필요합니다(제34조).", [CREDIT_MAIN["content"]],
                                 ["저는 2014학번이에요", "졸업학점 150학점 맞나요?"])
        self.assertEqual(result["unsupported"], ["150"])
        self.assertFalse(result["verified"])

    def test_explicit_user_years_remain_allowed(self):
        for answer, user in (("2014학번이시면 130학점입니다.", "저는 14학번이에요"),
                             ("2026년 기준으로 130학점입니다.", "2026년 기준으로 알려 주세요")):
            with self.subTest(user=user):
                self.assertTrue(check_grounding(answer, [CREDIT_MAIN["content"]], [user])["verified"])


class AnswerFlowTests(unittest.TestCase):
    def answerer(self, *outputs, hits=(CREDIT_MAIN, CREDIT_ADDENDUM)):
        model = llm(*outputs)
        return CitedAnswerer(model, StubSearcher(list(hits))), model

    def test_missing_admission_year_asks_and_keeps_general_rule(self):
        answerer, model = self.answerer(reply(
            "2016학년도 이후 입학생은 130학점(제34조), 2015학년도 이전 입학생은 140학점(부칙 제2조)입니다.",
            ["S1", "S2"], needs=True, questions=["몇 학년도에 입학하셨나요?"], missing=["admission_year"]))
        result = answerer.answer("졸업하려면 몇 학점 필요해요?")
        self.assertTrue(result["found"])
        self.assertEqual(result["clarification"]["missing"], ["admission_year"])
        self.assertIn("몇 학년도에 입학하셨나요?", result["answer"])
        self.assertTrue(result["grounding"]["verified"])
        self.assertIn("needs_clarification", model.seen[0][0].content)  # 프롬프트에 조건 확인 규칙이 들어간다

    def test_stated_condition_reaches_the_prompt_and_is_not_asked(self):
        answerer, model = self.answerer(reply(
            "2015학년도 이전 입학생이므로 140학점이 필요합니다(부칙 제2조).", ["S2"],
            needs=True, questions=["입학년도가 언제인가요?"], missing=["admission_year"]))
        result = answerer.answer("저는 2014학번인데 졸업학점이 몇이에요?")
        self.assertIn("[사용자가 밝힌 조건] 입학년도: 2014", model.seen[0][0].content)
        self.assertFalse(result["clarification"]["needed"])
        self.assertNotIn("입학년도가 언제인가요?", result["answer"])

    def test_unsupported_figure_is_repaired_once(self):
        answerer, model = self.answerer(reply("졸업에는 120학점이 필요합니다(제34조).", ["S1"]),
                                        reply("졸업에는 130학점 이상이 필요합니다(제34조).", ["S1"]))
        result = answerer.answer("졸업학점은?")
        self.assertEqual(result["grounding"], {"checked": 1, "unsupported": [], "verified": True, "repaired": True})
        self.assertIn("130학점", result["answer"])
        self.assertIn("120", model.seen[1][-1].content)  # 고쳐 쓰기 요청에 어긋난 수치를 알린다
        self.assertEqual(len(model.seen), 2)

    def test_incorrect_figure_from_user_question_is_repaired(self):
        answerer, _ = self.answerer(reply("졸업에는 150학점이 필요합니다(제34조).", ["S1"]),
                                    reply("졸업에는 130학점 이상이 필요합니다(제34조).", ["S1"]),
                                    hits=(CREDIT_MAIN,))
        result = answerer.answer("졸업학점 150학점 맞나요?")
        self.assertTrue(result["grounding"]["repaired"])
        self.assertIn("130학점", result["answer"])
        self.assertNotIn("150학점", result["answer"])

    def test_figure_still_unsupported_after_repair_is_flagged(self):
        answerer, _ = self.answerer(reply("졸업에는 120학점이 필요합니다(제34조).", ["S1"]),
                                    reply("졸업에는 125학점이 필요합니다(제34조).", ["S1"]))
        result = answerer.answer("졸업학점은?")
        self.assertFalse(result["grounding"]["verified"])
        self.assertEqual(result["grounding"]["unsupported"], ["120"])
        self.assertIn("120학점", result["answer"])
        self.assertIn("인용한 조항에서 확인되지 않았습니다", result["answer"])

    def test_failed_repair_keeps_the_first_answer_with_notice(self):
        answerer, _ = self.answerer(reply("졸업에는 120학점이 필요합니다(제34조).", ["S1"]), "JSON 아님", "여전히 아님")
        result = answerer.answer("졸업학점은?")
        self.assertTrue(result["found"])
        self.assertFalse(result["grounding"]["verified"])

    def test_grounded_answer_needs_no_extra_call(self):
        answerer, model = self.answerer(reply("조기졸업은 7학기, 평점 4.0 이상입니다(제40조).", ["S1"]), hits=(EARLY,))
        result = answerer.answer("조기졸업 조건은?")
        self.assertTrue(result["grounding"]["verified"])
        self.assertEqual(len(model.seen), 1)

    def test_english_notice(self):
        answerer, _ = self.answerer(reply("You need 120 credits (제34조).", ["S1"]),
                                    reply("You need 125 credits (제34조).", ["S1"]))
        result = answerer.answer("How many credits do I need?", language="en")
        self.assertIn("could not be found in the cited articles", result["answer"])


class SearchQueryPreservationTests(unittest.TestCase):
    def test_lost_conditions_are_appended(self):
        self.assertEqual(preserve_terms("저는 2014학번이에요", "졸업학점 기준"), "졸업학점 기준 2014학년도 입학")
        query = preserve_terms("학석사연계과정생 제40조 기준은?", "조기졸업 평점 기준")
        self.assertIn("제40조", query)
        self.assertIn("학석사연계", query)
        self.assertEqual(preserve_terms("제40조 조기졸업", "제40조 조기졸업 요건"), "제40조 조기졸업 요건")

    def test_english_article_reference_gets_korean_form(self):
        self.assertEqual(preserve_terms("What does Article 40 say?", "What does Article 40 say?"),
                         "What does Article 40 say? 제40조")

    def test_followup_condition_survives_rewrite(self):
        searcher = StubSearcher([CREDIT_MAIN])
        model = llm("졸업학점 기준", "답")
        history = [{"role": "user", "content": "졸업학점이 몇이에요?"},
                   {"role": "assistant", "content": "130학점 이상입니다."}]
        RegulationChain(model, searcher).invoke("저는 2014학번이에요", history)
        self.assertEqual(searcher.calls[0]["query"], "졸업학점 기준 2014학년도 입학")

    def test_first_question_without_conditions_is_unchanged(self):
        searcher = StubSearcher()
        RegulationChain(llm("답"), searcher).invoke("조기졸업 조건이 뭐야?")
        self.assertEqual(searcher.calls[0]["query"], "조기졸업 조건이 뭐야?")


if __name__ == "__main__":
    unittest.main()
