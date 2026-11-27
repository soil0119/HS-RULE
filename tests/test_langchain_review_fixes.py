"""재검토 이슈 #49·#50·#51·#52 회귀. 모델·시계는 대역이며 실제 provider 호출이 아니다."""
import importlib.util
import json
import types
import unittest
from unittest import mock

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from hsrule.answering import CitedAnswerer, RagError
from hsrule.chains import (DeadlineReached, RegulationChain, fit_history, invoke_within, is_retryable_error,
                           is_timeout_error, to_messages)
from hsrule.conflict import ConflictAnalyzer
from tests.test_chains import StubSearcher, llm
from tests.test_conflict import DOC, FOUND, Searcher

GOOD = '{"found": true, "answer": "제40조에 따라 가능합니다.", "citations": ["S1"], "followups": []}'


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class TickingLLM(GenericFakeChatModel):
    """호출 때마다 가짜 시계를 앞당기는 대역 모델."""

    clock: object = None
    step: float = 0.0
    starts: list = []

    def _generate(self, messages, *args, **kwargs):
        self.starts.append(self.clock.now)
        self.clock.now += self.step
        return super()._generate(messages, *args, **kwargs)


def ticking(clock, step, *texts):
    model = TickingLLM(messages=iter(AIMessage(t) for t in texts), clock=clock, step=step)
    model.starts = []
    return model


class FollowUpTests(unittest.TestCase):  # #49
    HISTORY = [{"role": "user", "content": "조기졸업은 몇 학기에 가능한가요?"},
               {"role": "assistant", "content": "제40조에 따라 7학기에 가능합니다."}]

    def test_follow_up_runs_through_rewrite_and_citation(self):
        for language in ("ko", "en"):
            with self.subTest(language=language):
                answerer = CitedAnswerer(llm("조기졸업 신청 방법", GOOD), StubSearcher())
                result = answerer.answer("신청은 어떻게 하나요?", self.HISTORY, language=language)
                self.assertTrue(result["found"])
                self.assertEqual(result["sources"][0]["article_id"], "제40조")

    def test_repeated_follow_ups_keep_working(self):
        searcher = StubSearcher()
        answerer = CitedAnswerer(llm("재작성1", GOOD, "재작성2", GOOD), searcher)
        history = list(self.HISTORY)
        for question in ("신청은?", "기간은?"):
            result = answerer.answer(question, history)
            self.assertTrue(result["found"])
            history += [{"role": "user", "content": question}, {"role": "assistant", "content": result["answer"]}]
        self.assertEqual([c["query"] for c in searcher.calls], ["재작성1", "재작성2"])

    def test_prepare_and_invoke_share_the_state(self):
        chain = RegulationChain(llm("x"), StubSearcher(), clock=FakeClock())
        state = chain._new_state("질문", self.HISTORY, False, "ko")
        for key in ("started_at", "dropped_for_context", "evidence_budget", "history"):
            self.assertIn(key, state)
        with self.assertRaises(ValueError):
            chain._new_state(" ", None, False, "ko")
        with self.assertRaises(ValueError):
            chain._new_state("질문", None, False, "fr")


class HistoryBudgetTests(unittest.TestCase):  # #50
    def test_long_history_is_cut_before_rewrite_and_evidence_survives(self):
        history = [{"role": "user" if i % 2 == 0 else "assistant", "content": "가" * 4000} for i in range(12)]
        model = llm("조기졸업 요건", GOOD)
        chain = RegulationChain(model, StubSearcher(), token_counter=len)
        state = chain.prepare("조기졸업 요건은?", history)
        history_chars = sum(len(str(m.content)) for m in state["history"])
        self.assertLessEqual(history_chars, 2048 // 2)
        self.assertTrue(state["docs"])  # 긴 이력 때문에 근거가 전부 사라지지 않는다
        rewrite_prompt = model.seen[0]
        self.assertLess(sum(len(str(m.content)) for m in rewrite_prompt), 3500)  # 48,000자가 전달되지 않는다

    def test_fit_history_keeps_recent_messages_in_order(self):
        messages = to_messages([{"role": "user", "content": "a" * 100}, {"role": "assistant", "content": "b" * 100},
                                {"role": "user", "content": "c" * 100}])
        kept, used = fit_history(messages, 250, len)
        self.assertEqual([m.content[0] for m in kept], ["b", "c"])
        self.assertEqual(used, 200)
        kept, used = fit_history(messages, 60, len)  # 최근 한 건도 넘치면 잘라서 남긴다
        self.assertEqual((len(kept), kept[0].content[0], used <= 60), (1, "c", True))
        self.assertEqual(fit_history(messages, 0, len)[0], [])

    def test_history_share_is_capped_and_long_question_keeps_evidence(self):
        chain = RegulationChain(llm("x"), StubSearcher(), token_counter=len)
        history = [{"role": "user", "content": "가" * 4000}] * 12
        state = chain._new_state("질문", history, False, "ko")
        self.assertLessEqual(sum(len(str(m.content)) for m in state["history"]), chain.max_context_tokens // 2)
        self.assertGreaterEqual(state["evidence_budget"], chain.max_context_tokens // 2)
        long_question = chain._new_state("가" * 2000, None, False, "ko")
        self.assertEqual(long_question["evidence_budget"], chain.max_context_tokens)  # 긴 질문도 근거를 밀어내지 않는다
        self.assertEqual(len(long_question["question"]), 2000)


class SharedDeadlineTests(unittest.TestCase):  # #51
    def test_answer_retry_after_deadline_is_not_started(self):
        clock = FakeClock()
        model = ticking(clock, 121, "형식 오류 출력", GOOD)
        answerer = CitedAnswerer(model, StubSearcher())
        answerer.chain.clock = clock
        with self.assertRaises(RagError) as ctx:
            answerer.answer("조기졸업?")
        self.assertEqual(ctx.exception.reason, "timeout")
        self.assertEqual(model.starts, [0])  # 0초 호출만 있고 121초 재시도는 시작하지 않는다

    def test_rewrite_and_answer_share_one_deadline(self):
        clock = FakeClock()
        model = ticking(clock, 121, "재작성", GOOD)
        answerer = CitedAnswerer(model, StubSearcher())
        answerer.chain.clock = clock
        history = [{"role": "user", "content": "이전 질문"}]
        with self.assertRaises(RagError) as ctx:
            answerer.answer("후속 질문", history)
        self.assertEqual(ctx.exception.reason, "timeout")
        self.assertEqual(model.starts, [0])  # 재작성이 마감을 넘기면 답변 모델은 부르지 않는다

    def test_conflict_stops_when_slow_searches_use_the_deadline(self):
        clock = FakeClock()

        class SlowSearcher(Searcher):
            def search(self, *args, **kwargs):
                clock.now += 151
                return super().search(*args, **kwargs)

        model = ticking(clock, 0, json.dumps(FOUND, ensure_ascii=False))
        doc = "\n\n".join(f"{i}번째 지침은 5학기에 조기졸업을 허용한다." for i in range(3))
        analyzer = ConflictAnalyzer(model, SlowSearcher(), max_total_seconds=300, clock=clock)
        result = analyzer.analyze(doc, "지침.txt")
        scope = result["scope"]
        self.assertEqual(model.starts, [])  # 마감(300초)을 넘긴 뒤 모델을 새로 호출하지 않는다
        self.assertTrue(scope["stopped_for_time"] and scope["truncated"])
        self.assertEqual((scope["passages_analyzed"], scope["batches_analyzed"]), (0, 0))
        self.assertIn("시간 한도", result["summary"])

    def test_conflict_parse_retry_stops_at_deadline(self):
        clock = FakeClock()
        model = ticking(clock, 200, "형식 오류", json.dumps(FOUND, ensure_ascii=False))
        analyzer = ConflictAnalyzer(model, Searcher(), max_total_seconds=120, clock=clock)
        result = analyzer.analyze(DOC, "지침.txt")  # 형식 재시도가 마감 뒤라 시작되지 않고 시간 중단으로 남는다
        self.assertEqual(model.starts, [0])
        self.assertTrue(result["scope"]["stopped_for_time"] and result["scope"]["truncated"])
        self.assertEqual((result["conflicts"], result["scope"]["batches_analyzed"]), ([], 0))


class SummaryConsistencyTests(unittest.TestCase):  # #52
    def test_rejected_finding_does_not_leave_its_summary(self):
        bad = {"summary": "문서 전체가 제40조와 충돌한다",
               "conflicts": [{"passage": "P999", "candidate": "C1", "issue": "없는 문단", "severity": "high"}],
               "recommendations": []}
        analyzer = ConflictAnalyzer(GenericFakeChatModel(messages=iter([AIMessage(json.dumps(bad, ensure_ascii=False))])),
                                    Searcher())
        result = analyzer.analyze(DOC, "지침.txt")
        self.assertEqual(result["conflicts"], [])
        self.assertEqual(result["scope"]["dropped_findings"], 1)
        self.assertNotIn("문서 전체가 제40조와 충돌한다", result["summary"])
        self.assertIn("0건", result["summary"])

    def test_summary_comes_from_accepted_findings(self):
        model = GenericFakeChatModel(messages=iter([AIMessage(json.dumps(
            dict(FOUND, summary="검증 전 단정: 모든 조항이 충돌", ), ensure_ascii=False))]))
        result = ConflictAnalyzer(model, Searcher()).analyze(DOC, "지침.txt")
        self.assertEqual(len(result["conflicts"]), 1)
        self.assertNotIn("검증 전 단정", result["summary"])
        self.assertIn("충돌 가능성 1건", result["summary"])
        self.assertIn("제40조", result["summary"])
        self.assertIn(result["conflicts"][0]["issue"], result["summary"])


class ReadTimeout(Exception):
    """httpx.ReadTimeout처럼 TimeoutError를 상속하지 않는 provider 시간 초과 예외 대역."""


class StatusError(Exception):
    def __init__(self, status, retry_after=None):
        super().__init__(f"status {status}")
        self.status_code = status
        self.response = types.SimpleNamespace(
            headers={} if retry_after is None else {"retry-after": str(retry_after)})


class ScriptedLLM(GenericFakeChatModel):
    """호출마다 (동작, 값, 걸린 시간)을 따르고, 받은 호출별 timeout을 기록하는 대역 모델."""

    clock: object = None
    script: list = []
    calls: list = []

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        action, value, seconds = self.script.pop(0)
        self.calls.append({"at": self.clock.now, "timeout": kwargs.get("timeout")})
        self.clock.now += seconds
        if action == "raise":
            raise value
        return ChatResult(generations=[ChatGeneration(message=AIMessage(value))])


def scripted(clock, *script):
    return ScriptedLLM(messages=iter([]), clock=clock, script=list(script), calls=[])


class ProviderRetryTests(unittest.TestCase):  # #51 provider timeout·재시도 예산
    def test_timeout_and_retry_classification(self):
        self.assertTrue(is_timeout_error(ReadTimeout()))
        wrapped = RuntimeError("wrapper")
        wrapped.__cause__ = ReadTimeout()
        self.assertTrue(is_timeout_error(wrapped))
        self.assertTrue(is_retryable_error(StatusError(429)))
        self.assertTrue(is_retryable_error(StatusError(503)))
        self.assertFalse(is_retryable_error(StatusError(400)))
        self.assertFalse(is_retryable_error(ReadTimeout()))

    def test_each_attempt_gets_the_remaining_time_as_timeout(self):
        clock, waits = FakeClock(), []
        model = scripted(clock, ("raise", StatusError(429), 10), ("ok", "답", 5))
        text = invoke_within(model, "q", lambda: 120 - clock.now, sleep=lambda s: (waits.append(s), setattr(clock, "now", clock.now + s)))
        self.assertEqual(text, "답")
        self.assertEqual([c["timeout"] for c in model.calls], [120, 120 - 10 - 0.5])
        self.assertEqual(waits, [0.5])

    def test_retry_after_beyond_the_deadline_is_not_waited(self):
        clock = FakeClock()
        model = scripted(clock, ("raise", StatusError(429, retry_after=30), 100), ("ok", "답", 1))
        with self.assertRaises(StatusError):
            invoke_within(model, "q", lambda: 120 - clock.now, sleep=lambda s: self.fail("기다리면 안 된다"))
        self.assertEqual(len(model.calls), 1)

    def test_non_retryable_error_is_raised_at_once(self):
        clock = FakeClock()
        model = scripted(clock, ("raise", StatusError(400), 1))
        with self.assertRaises(StatusError):
            invoke_within(model, "q", lambda: 120 - clock.now, sleep=lambda s: None)
        self.assertEqual(len(model.calls), 1)

    def test_no_call_starts_with_less_than_a_second_left(self):
        clock = FakeClock()
        clock.now = 119.5
        model = scripted(clock, ("ok", "답", 0))
        with self.assertRaises(DeadlineReached):
            invoke_within(model, "q", lambda: 120 - clock.now)
        self.assertEqual(model.calls, [])

    def test_provider_timeout_is_reported_as_timeout(self):
        clock = FakeClock()
        answerer = CitedAnswerer(scripted(clock, ("raise", ReadTimeout(), 120)), StubSearcher())
        answerer.chain.clock = clock
        with self.assertRaises(RagError) as ctx:
            answerer.answer("조기졸업?")
        self.assertEqual((ctx.exception.code, ctx.exception.reason), ("rag_backend_error", "timeout"))

    def test_transient_answer_error_is_retried_within_the_deadline(self):
        clock = FakeClock()
        answerer = CitedAnswerer(scripted(clock, ("raise", StatusError(503), 2), ("ok", GOOD, 2)), StubSearcher())
        answerer.chain.clock, answerer.chain.sleep = clock, lambda s: None
        self.assertTrue(answerer.answer("조기졸업?")["found"])


class ConflictDeadlineScopeTests(unittest.TestCase):  # #51 미검토 범위 보존
    DOC = "\n\n".join(["5학기 조기졸업을 허용한다." * 20] * 3)

    def test_batch_stopped_at_the_deadline_is_not_counted_as_reviewed(self):
        clock = FakeClock()
        model = scripted(clock, ("ok", json.dumps(FOUND, ensure_ascii=False), 50),
                         ("raise", ReadTimeout(), 70))
        analyzer = ConflictAnalyzer(model, Searcher(), batch_chars=400, max_total_seconds=120, clock=clock)
        result = analyzer.analyze(self.DOC, "지침.txt")
        scope = result["scope"]
        self.assertEqual(len(result["conflicts"]), 1)  # 앞 배치의 결과는 남는다
        self.assertEqual((scope["batches_analyzed"], scope["passages_analyzed"]), (1, 1))
        self.assertTrue(scope["stopped_for_time"] and scope["truncated"])
        self.assertLess(scope["characters_analyzed"], scope["characters_total"])
        self.assertEqual(model.calls[1]["timeout"], 70)  # 남은 시간만 넘겼다
        self.assertIn("문단 3개 중 1개를 검토", result["summary"])

    def test_timeout_well_before_the_deadline_is_still_an_error(self):
        clock = FakeClock()
        analyzer = ConflictAnalyzer(scripted(clock, ("raise", ReadTimeout(), 5)), Searcher(), clock=clock)
        with self.assertRaises(RagError) as ctx:
            analyzer.analyze(DOC, "지침.txt")
        self.assertEqual(ctx.exception.reason, "timeout")


@unittest.skipUnless(importlib.util.find_spec("langchain_google_genai") and importlib.util.find_spec("langchain_groq"),
                     "requirements.txt의 LLM 패키지 필요")
class ProviderPlumbingTests(unittest.TestCase):
    """실제 클라이언트 객체에 호출별 timeout이 전달되고 provider 자체 재시도가 꺼졌는지 확인한다(네트워크 호출 없음)."""

    class Stop(Exception):
        pass

    def test_groq_receives_timeout_without_internal_retries(self):
        from hsrule.rag_config import RagSettings, create_llm

        model = create_llm(RagSettings("groq", "k", "openai/gpt-oss-120b"))
        self.assertEqual(model.client._client.max_retries, 0)
        with mock.patch.object(model.client, "create", side_effect=self.Stop) as create:
            with self.assertRaises(self.Stop):
                model.bind(timeout=7.5).invoke("q")
        self.assertEqual(create.call_args.kwargs["timeout"], 7.5)

    def test_gemini_receives_timeout_without_internal_retries(self):
        from hsrule.rag_config import RagSettings, create_llm

        model = create_llm(RagSettings("gemini", "k", "gemini-3.5-flash"))
        with mock.patch.object(model.client.models, "generate_content", side_effect=self.Stop) as generate:
            with self.assertRaises(self.Stop):
                model.bind(timeout=7.5).invoke("q")
        options = generate.call_args.kwargs["config"].http_options
        self.assertEqual((options.timeout, options.retry_options.attempts), (7500, 1))


if __name__ == "__main__":
    unittest.main()
