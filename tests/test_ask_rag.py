"""로컬 질문 도구(#76) 회귀. 모델·검색은 대역이며 실제 provider 호출이 아니다."""
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from hsrule.answering import CitedAnswerer, RagError
from hsrule.chains import RegulationChain, is_rate_limited, new_usage
from scripts import ask_rag
from tests.test_answer_flow import CREDIT_ADDENDUM, CREDIT_MAIN, reply
from tests.test_chains import StubSearcher, llm
from tests.test_langchain_review_fixes import FakeClock, StatusError, scripted


class UsageModel(GenericFakeChatModel):
    """provider가 알려 주는 토큰 사용량(usage_metadata)을 흉내 낸다."""

    def _generate(self, messages, *args, **kwargs):
        result = super()._generate(messages, *args, **kwargs)
        result.generations[0].message.usage_metadata = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}
        return result


class UsageTests(unittest.TestCase):
    def test_calls_retries_and_tokens_are_recorded(self):
        model = UsageModel(messages=iter([AIMessage("재작성"), AIMessage(reply("130학점입니다(제34조).", ["S1"]))]))
        answerer = CitedAnswerer(model, StubSearcher([CREDIT_MAIN]))
        result = answerer.answer("그럼 졸업은?", [{"role": "user", "content": "학점 질문"}])
        self.assertEqual(result["usage"], {"calls": 2, "retries": 0, "input_tokens": 200, "output_tokens": 40})

    def test_retry_is_counted(self):
        clock = FakeClock()
        model = scripted(clock, ("raise", StatusError(503), 1), ("ok", reply("130학점입니다(제34조).", ["S1"]), 1))
        answerer = CitedAnswerer(model, StubSearcher([CREDIT_MAIN]))
        answerer.chain.clock, answerer.chain.sleep = clock, lambda s: None
        result = answerer.answer("졸업학점은?")
        self.assertEqual((result["usage"]["calls"], result["usage"]["retries"]), (2, 1))

    def test_rate_limit_is_reported_separately(self):
        self.assertTrue(is_rate_limited(StatusError(429)))
        self.assertFalse(is_rate_limited(StatusError(503)))
        clock = FakeClock()
        model = scripted(clock, *[("raise", StatusError(429), 0)] * 3)
        answerer = CitedAnswerer(model, StubSearcher([CREDIT_MAIN]))
        answerer.chain.clock, answerer.chain.sleep = clock, lambda s: None
        with self.assertRaises(RagError) as ctx:
            answerer.answer("졸업학점은?")
        self.assertEqual(ctx.exception.reason, "rate_limit")

    def test_state_starts_with_empty_usage(self):
        state = RegulationChain(llm("x"), StubSearcher())._new_state("질문", None, False, "ko")
        self.assertEqual(state["usage"], new_usage())


class SessionTests(unittest.TestCase):
    def test_multi_turn_session_with_commands_and_log(self):
        searcher = StubSearcher([CREDIT_MAIN, CREDIT_ADDENDUM])
        answerer = CitedAnswerer(llm(
            reply("2016학년도 이후 입학생은 130학점(제34조), 2015학년도 이전은 140학점(부칙 제2조)입니다.", ["S1", "S2"],
                  needs=True, questions=["몇 학년도에 입학하셨나요?"], missing=["admission_year"]),
            "졸업학점 기준",
            reply("2015학년도 이전 입학생이므로 140학점입니다(부칙 제2조).", ["S2"]),
            reply("You need 130 credits (제34조).", ["S1"])), searcher)
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            log = os.path.join(directory, "log.jsonl")
            history = ask_rag.run_session(answerer, ["졸업학점은?", "저는 2014학번이에요", "/new", "/en",
                                                     "How many credits?", "/quit", "무시됨"],
                                          out, log=log, meta={"model": "fake"})
            records = [json.loads(line) for line in open(log, encoding="utf-8")]
        text = out.getvalue()
        self.assertIn("몇 학년도에 입학하셨나요?", text)
        self.assertIn("추가 질문 필요: admission_year", text)
        self.assertIn("[S2] 한성대학교 학칙 부칙(2016.2.3공포)제2조", text)
        self.assertIn("모델 호출", text)
        self.assertEqual(searcher.calls[1]["query"], "졸업학점 기준 2014학년도 입학")  # 후속 질문의 조건 유지
        self.assertEqual([r["language"] for r in records], ["ko", "ko", "en"])
        self.assertEqual([r["history_turns"] for r in records], [0, 1, 0])  # /new 뒤에는 이력이 비어 있다
        self.assertEqual(records[0]["model"], "fake")
        self.assertEqual(len(history), 2)

    def test_errors_do_not_end_the_session(self):
        class Failing:
            calls = 0

            def answer(self, *args, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise RagError("rag_backend_error", "x", "rate_limit")
                return {"answer": "답", "found": False, "sources": [], "followups": [], "reason": "no_evidence"}

        out = io.StringIO()
        ask_rag.run_session(Failing(), ["첫 질문", "둘째 질문"], out)
        self.assertIn("오류(rate_limit): x", out.getvalue())
        self.assertIn("근거를 찾지 못함 (no_evidence)", out.getvalue())

    def test_missing_configuration_exits_with_a_clear_message(self):
        with mock.patch.dict(os.environ, {"DATABASE_URL": ""}, clear=False), mock.patch("sys.stderr"):
            self.assertEqual(ask_rag.main(["-q", "x", "--env-file", "/nonexistent"]), 2)
        env = {"DATABASE_URL": "postgresql://x", "GROQ_API_KEY": "", "GROQ_MODEL": "", "LLM_PROVIDER": ""}
        with mock.patch.dict(os.environ, env, clear=False), mock.patch("sys.stderr", new=io.StringIO()) as err:
            self.assertEqual(ask_rag.main(["-q", "x", "--env-file", "/nonexistent"]), 2)
        self.assertIn("GROQ_API_KEY", err.getvalue())

    def test_env_file_fills_only_missing_values_without_printing_them(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, ".env")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("HSRULE_T_A=from-file\nHSRULE_T_B=secret-value\n# 주석\nHSRULE_T_C=\n")
            with mock.patch.dict(os.environ, {"HSRULE_T_A": "exported"}):
                loaded = ask_rag.load_env_file(path)
                self.assertEqual((os.environ["HSRULE_T_A"], os.environ["HSRULE_T_B"]), ("exported", "secret-value"))
                self.assertNotIn("HSRULE_T_C", os.environ)
        self.assertEqual(loaded, ["HSRULE_T_B"])
        self.assertEqual(ask_rag.load_env_file("/nonexistent/.env"), [])

    def test_ping_explains_common_failures(self):
        clock = FakeClock()
        self.assertEqual(ask_rag.ping(llm("OK")), (True, "모델 연결 정상"))
        for status, words in ((401, "키 인증 실패"), (403, "키 인증 실패"), (404, "GEMINI_MODEL"), (429, "한도"),
                              (400, "GEMINI_MODEL")):
            with self.subTest(status=status):
                ok, message = ask_rag.ping(scripted(clock, ("raise", StatusError(status), 0)))
                self.assertFalse(ok)
                self.assertIn(words, message)

    def test_ping_names_the_groq_settings(self):
        clock = FakeClock()
        ok, message = ask_rag.ping(scripted(clock, ("raise", StatusError(401), 0)), "groq")
        self.assertFalse(ok)
        self.assertIn("GROQ_API_KEY", message)
        self.assertIn("console.groq.com", message)

    def test_ping_only_mode_does_not_need_a_database(self):
        env = {"DATABASE_URL": "", "LLM_PROVIDER": "gemini", "GEMINI_API_KEY": "k", "GEMINI_MODEL": "gemini-3.5-flash"}
        with mock.patch.dict(os.environ, env), mock.patch("hsrule.rag_config.create_llm", lambda s: llm("OK")), \
                mock.patch("sys.stdout", new=io.StringIO()) as out:
            self.assertEqual(ask_rag.main(["--ping", "--env-file", "/nonexistent"]), 0)
        self.assertIn("gemini/gemini-3.5-flash: 모델 연결 정상", out.getvalue())
        failing = scripted(FakeClock(), ("raise", StatusError(401), 0))
        with mock.patch.dict(os.environ, env), mock.patch("hsrule.rag_config.create_llm", lambda s: failing), \
                mock.patch("sys.stdout", new=io.StringIO()):
            self.assertEqual(ask_rag.main(["-q", "x", "--env-file", "/nonexistent"]), 3)  # 연결 실패면 DB 전에 멈춘다

    def test_gemini_uses_the_developer_api_with_key_header(self):
        from hsrule.rag_config import RagSettings, create_llm

        from hsrule.rag_config import RagConfigError

        with self.assertRaises(RagConfigError):  # 이 변수가 켜져 있으면 키가 Vertex AI로 가므로 시작하지 않는다
            RagSettings.from_env({"GEMINI_API_KEY": "k", "GEMINI_MODEL": "m", "GOOGLE_GENAI_USE_VERTEXAI": "true"})
        with mock.patch.dict(os.environ, {"GOOGLE_GENAI_USE_VERTEXAI": ""}):
            client = create_llm(RagSettings("gemini", "AQ.offline-format-check", "gemini-3.5-flash")).client._api_client
        headers = client._http_options.headers or {}
        self.assertFalse(client.vertexai)
        self.assertIn("generativelanguage.googleapis.com", client._http_options.base_url)
        self.assertEqual(headers.get("x-goog-api-key"), "AQ.offline-format-check")
        self.assertNotIn("Authorization", headers)  # AQ. 키를 Bearer로 보내면 401이 난다

    def test_snapshot_loader_accepts_json_and_jsonl(self):
        item = {"regulation_id": "1", "revision_id": "1:1", "title": "t", "official_url": "https://x", "text": "a"}
        with tempfile.TemporaryDirectory() as directory:
            whole, lines = os.path.join(directory, "a.json"), os.path.join(directory, "b.jsonl")
            open(whole, "w", encoding="utf-8").write(json.dumps({"regulations": [item]}))
            open(lines, "w", encoding="utf-8").write(json.dumps(item) + "\n")
            self.assertEqual(ask_rag.load_regulations(whole), [item])
            self.assertEqual(ask_rag.load_regulations(lines), [item])
        self.assertEqual(ask_rag.load_regulations(None)[0]["regulation_id"], "20")


class GroqStatusError(Exception):
    """groq.APIStatusError처럼 status_code와 body를 가진 대역."""

    def __init__(self, status, code):
        super().__init__("본문에 조직 ID 같은 값이 있을 수 있음 org_secret")
        self.status_code, self.body = status, {"error": {"code": code, "type": "tokens"}}


class DemoFindingsTests(unittest.TestCase):
    """#76 실제 시연(Groq)에서 드러난 문제의 회귀."""

    def test_clarifying_answer_keeps_its_evidence(self):
        model = llm(json.dumps({"found": False, "answer": "2016학년도 이후 입학생은 130학점(제34조), 이전은 140학점입니다.",
                                "citations": ["S1", "S2"], "followups": [], "needs_clarification": True,
                                "clarifying_questions": ["몇 학년도에 입학하셨나요?"],
                                "missing_conditions": ["admission_year"]}, ensure_ascii=False))
        result = CitedAnswerer(model, StubSearcher([CREDIT_MAIN, CREDIT_ADDENDUM])).answer("졸업학점은?")
        self.assertTrue(result["found"])
        self.assertEqual([s["article_id"] for s in result["sources"]], ["제34조", "부칙(2016.2.3공포)제2조"])
        self.assertTrue(result["clarification"]["needed"])
        self.assertTrue(result["grounding"]["verified"])
        self.assertIn("found는 true", model.seen[0][0].content)

    def test_groq_413_tpm_is_a_rate_limit_and_detail_hides_the_message(self):
        from hsrule.chains import provider_detail

        error = GroqStatusError(413, "rate_limit_exceeded")
        self.assertTrue(is_rate_limited(error))
        self.assertFalse(is_rate_limited(GroqStatusError(413, "request_too_large")))
        self.assertEqual(provider_detail(error), "HTTP 413, rate_limit_exceeded")
        self.assertNotIn("org_secret", provider_detail(error))

    def test_session_prints_provider_status(self):
        clock = FakeClock()
        answerer = CitedAnswerer(scripted(clock, ("raise", GroqStatusError(400, "invalid_request_error"), 0)),
                                 StubSearcher([CREDIT_MAIN]))
        answerer.chain.clock = clock
        out = io.StringIO()
        ask_rag.run_session(answerer, ["졸업학점은?"], out)
        self.assertIn("오류(provider, HTTP 400, invalid_request_error)", out.getvalue())
        self.assertNotIn("org_secret", out.getvalue())

    def test_groq_output_parse_failure_is_retried(self):
        from hsrule.chains import is_generation_failure, is_retryable_error

        failure = GroqStatusError(400, "output_parse_failed")
        self.assertTrue(is_generation_failure(failure))
        self.assertTrue(is_retryable_error(failure))
        self.assertFalse(is_retryable_error(GroqStatusError(400, "invalid_request_error")))
        clock = FakeClock()
        good = json.dumps({"found": True, "answer": "130학점(제34조).", "citations": ["S1"], "followups": []},
                          ensure_ascii=False)
        answerer = CitedAnswerer(scripted(clock, ("raise", failure, 0), ("reply", good, 0)), StubSearcher([CREDIT_MAIN]))
        answerer.chain.clock = clock
        result = answerer.answer("졸업학점은?")
        self.assertTrue(result["found"])
        self.assertEqual(result["usage"]["retries"], 1)

    def test_repeated_output_parse_failure_is_a_format_error(self):
        clock = FakeClock()
        failure = GroqStatusError(400, "output_parse_failed")
        answerer = CitedAnswerer(scripted(clock, *[("raise", failure, 0)] * 3), StubSearcher([CREDIT_MAIN]))
        answerer.chain.clock = clock
        out = io.StringIO()
        ask_rag.run_session(answerer, ["졸업학점은?"], out)
        self.assertIn("오류(json, HTTP 400, output_parse_failed)", out.getvalue())
        self.assertIn("한 번 더", out.getvalue())

    def test_generation_retry_changes_temperature_and_debug_shows_output(self):
        clock = FakeClock()
        failure = GroqStatusError(400, "output_parse_failed")
        failure.body["error"]["failed_generation"] = "We need to answer in English.\n<|channel|>broken"
        model = scripted(clock, *[("raise", failure, 0)] * 3)
        answerer = CitedAnswerer(model, StubSearcher([CREDIT_MAIN]))
        answerer.chain.clock = clock
        out = io.StringIO()
        ask_rag.run_session(answerer, ["졸업학점은?"], out, debug=True)
        self.assertEqual([call["temperature"] for call in model.calls], [None, 0.7, 0.7])
        self.assertIn("답변 모델의 출력 형식 오류", out.getvalue())
        self.assertIn("[debug] 모델 원출력 앞부분: 'We need to answer in English. <|channel|>broken'", out.getvalue())
        quiet = io.StringIO()
        model.script, model.calls = [("raise", failure, 0)] * 3, []
        ask_rag.run_session(answerer, ["졸업학점은?"], quiet)
        self.assertNotIn("[debug]", quiet.getvalue())

    def test_db_hints(self):
        url = "postgresql://postgres:x@127.0.0.1:5432/hansungrules"
        self.assertIn("URL 인코딩", ask_rag.db_hint("postgresql://postgres:a/b@127.0.0.1/x",
                                                    Exception('could not translate host name "postgres"')))
        self.assertIn("docker compose", ask_rag.db_hint(url, Exception("Connection refused")))
        self.assertIn("비밀번호", ask_rag.db_hint(url, Exception('password authentication failed for user "postgres"')))


URL = os.getenv("HSRULE_TEST_DATABASE_URL")


@unittest.skipUnless(URL, "set HSRULE_TEST_DATABASE_URL to a disposable PostgreSQL with pgvector")
class EndToEndTests(unittest.TestCase):
    """실제 DB 색인·검색을 거쳐 main()이 동작하는지 본다(임베딩·모델만 대역)."""

    def test_mismatched_index_explains_rebuild_and_rebuild_fixes_it(self):
        from pathlib import Path

        from hsrule.official_repository import OfficialRepository
        from tests.fakes import FakeEmbeddings

        corpus = (Path(ask_rag.ROOT) / "tests/fixtures/hansung_rules_excerpt.txt").read_text(encoding="utf-8")
        repository = OfficialRepository(URL)
        repository.initialize()
        repository.rebuild()
        other = FakeEmbeddings(corpus)
        other.model_name = "another-model"
        from hsrule.official_indexer import index_regulation
        index_regulation(dict(ask_rag.DEFAULT_REGULATION, regulation_id="99", revision_id="99:1", text=corpus),
                         repository, other)
        env = {"DATABASE_URL": URL, "LLM_PROVIDER": "gemini", "GEMINI_API_KEY": "k", "GEMINI_MODEL": "gemini-3.5-flash"}
        patches = (mock.patch.dict(os.environ, env),
                   mock.patch("hsrule.embeddings.LocalEmbeddings", lambda: FakeEmbeddings(corpus)),
                   mock.patch("hsrule.official_indexer.validate_embeddings", lambda e: {"model": "fake"}),
                   mock.patch("hsrule.rag_config.create_llm", lambda settings: llm(reply("130학점(제34조).", ["S1"]))))
        with patches[0], patches[1], patches[2], patches[3], mock.patch("sys.stderr", new=io.StringIO()) as err, \
                mock.patch("sys.stdout", new=io.StringIO()):
            self.assertEqual(ask_rag.main(["--index", "--no-ping", "--env-file", "/nonexistent", "-q", "x"]), 5)
        self.assertIn("--rebuild", err.getvalue())
        with patches[0], patches[1], patches[2], patches[3], mock.patch("sys.stdout", new=io.StringIO()) as out:
            self.assertEqual(ask_rag.main(["--rebuild", "--no-ping", "--env-file", "/nonexistent", "-q", "졸업학점"]), 0)
        self.assertIn("RAG 색인을 비웠습니다", out.getvalue())
        self.assertIn("색인 20 한성대학교 학칙: indexed", out.getvalue())

    def test_unreachable_db_gets_a_hint(self):
        env = {"DATABASE_URL": "postgresql://postgres:x@127.0.0.1:1/none", "LLM_PROVIDER": "gemini",
               "GEMINI_API_KEY": "k", "GEMINI_MODEL": "gemini-3.5-flash"}
        from tests.fakes import FakeEmbeddings

        with mock.patch.dict(os.environ, env), mock.patch("hsrule.embeddings.LocalEmbeddings", lambda: FakeEmbeddings("x")), \
                mock.patch("hsrule.rag_config.create_llm", lambda settings: llm("OK")), \
                mock.patch("sys.stderr", new=io.StringIO()) as err, mock.patch("sys.stdout", new=io.StringIO()):
            self.assertEqual(ask_rag.main(["--no-ping", "--env-file", "/nonexistent", "-q", "x"]), 4)
        self.assertIn("DB에 연결하지 못했습니다", err.getvalue())

    def test_index_and_ask_once(self):
        from pathlib import Path

        from hsrule.official_repository import OfficialRepository
        from tests.fakes import FakeEmbeddings

        corpus = (Path(ask_rag.ROOT) / "tests/fixtures/hansung_rules_excerpt.txt").read_text(encoding="utf-8")
        repository = OfficialRepository(URL)
        repository.initialize()
        repository.rebuild()
        model = llm(reply("2016학년도 이후 입학생은 130학점 이상입니다(제34조).", ["S1"]))
        env = {"DATABASE_URL": URL, "LLM_PROVIDER": "gemini", "GEMINI_API_KEY": "k", "GEMINI_MODEL": "gemini-3.5-flash"}
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, env), \
                mock.patch("hsrule.embeddings.LocalEmbeddings", lambda: FakeEmbeddings(corpus)), \
                mock.patch("hsrule.official_indexer.validate_embeddings", lambda e: {"model": "fake"}), \
                mock.patch("hsrule.rag_config.create_llm", lambda settings: model), \
                mock.patch("sys.stdout", new=io.StringIO()) as out:
            log = os.path.join(directory, "log.jsonl")
            self.assertEqual(ask_rag.main(["--index", "--no-ping", "--env-file", "/nonexistent",
                                           "-q", "졸업학점은 몇 학점인가요?", "--log", log]), 0)
            record = json.loads(open(log, encoding="utf-8").read())
        text = out.getvalue()
        self.assertIn("색인 20 한성대학교 학칙: indexed", text)
        self.assertIn("학칙 발췌본(표본, 실제 전체 규정 아님)", text)
        self.assertIn("130학점", text)
        self.assertEqual(record["result"]["sources"][0]["article_id"], "제34조")
        self.assertEqual((record["provider"], record["model"]), ("gemini", "gemini-3.5-flash"))


if __name__ == "__main__":
    unittest.main()
