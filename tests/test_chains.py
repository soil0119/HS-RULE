import unittest

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from hsrule.chains import RegulationChain, RegulationRetriever, format_evidence, label_documents, to_messages
from hsrule.rag_config import RagConfigError, RagSettings


class RecordingLLM(GenericFakeChatModel):
    """들어온 프롬프트를 기록하는 대역 모델. 실제 모델 호출이 아니다."""

    seen: list = []

    def _generate(self, messages, *args, **kwargs):
        self.seen.append(messages)
        return super()._generate(messages, *args, **kwargs)


class StubSearcher:
    def __init__(self):
        self.calls = []

    def search(self, query, *, include_private=False, k=None, regulation_ids=None, revision_ids=None):
        self.calls.append({"query": query, "include_private": include_private, "k": k})
        return [{"id": "20:1:3", "regulation_id": "20", "revision_id": "20:1", "title": "한성대학교 학칙",
                 "article_id": "제40조", "official_url": "https://rule.example/20", "content": "조기졸업 요건 본문",
                 "source_start": 10, "source_end": 20, "found_by": ["keyword"], "fused_score": 0.03}]


def llm(*texts):
    model = RecordingLLM(messages=iter(AIMessage(t) for t in texts))
    model.seen = []
    return model


class ChainTests(unittest.TestCase):
    def test_history_is_converted_and_limited(self):
        history = [{"role": "user", "content": f"q{i}"} for i in range(20)]
        self.assertEqual(len(to_messages(history, 12)), 12)
        self.assertEqual(to_messages(history, 12)[-1].content, "q19")
        self.assertEqual(len(to_messages([{"role": "assistant", "content": "x" * 9000}])[0].content), 4000)
        with self.assertRaises(ValueError):
            to_messages([{"role": "system", "content": "x"}])

    def test_retriever_returns_documents_with_metadata(self):
        docs = RegulationRetriever(searcher=StubSearcher(), k=3).invoke("조기졸업")
        self.assertEqual(docs[0].metadata["article_id"], "제40조")
        labeled = label_documents(docs)
        self.assertEqual(labeled[0].metadata["label"], "S1")
        self.assertIn("[S1] 한성대학교 학칙 제40조", format_evidence(labeled, "ko"))
        self.assertEqual(format_evidence([], "en"), "(no evidence was retrieved)")

    def test_first_question_skips_condense_and_answers_in_korean(self):
        searcher, model = StubSearcher(), llm("제40조에 따라 7학기에 조기졸업할 수 있습니다.")
        result = RegulationChain(model, searcher, k=4).invoke("조기졸업 조건이 뭐야?")
        self.assertEqual(searcher.calls, [{"query": "조기졸업 조건이 뭐야?", "include_private": False, "k": 4}])
        self.assertEqual(len(model.seen), 1)
        self.assertIn("한국어로 답한다", model.seen[0][0].content)
        self.assertIn("조기졸업 요건 본문", model.seen[0][0].content)
        self.assertIn("제40조", result["answer"])
        self.assertEqual(result["docs"][0].metadata["label"], "S1")

    def test_followup_is_condensed_with_history_before_search(self):
        searcher = StubSearcher()
        model = llm("조기졸업 신청 시기", "7학기 중간고사 전에 신청합니다.")
        history = [{"role": "user", "content": "조기졸업 조건이 뭐야?"},
                   {"role": "assistant", "content": "7학기에 가능합니다."}]
        result = RegulationChain(model, searcher).invoke("그럼 언제 신청해?", history, include_private=True)
        self.assertEqual(searcher.calls[0]["query"], "조기졸업 신청 시기")
        self.assertTrue(searcher.calls[0]["include_private"])
        self.assertEqual(result["search_query"], "조기졸업 신청 시기")
        self.assertEqual(len(model.seen), 2)

    def test_english_answer_instruction_and_validation(self):
        model = llm("You can graduate early under Article 40 (제40조).")
        RegulationChain(model, StubSearcher()).invoke("How do I graduate early?", language="en")
        self.assertIn("Answer in English", model.seen[0][0].content)
        chain = RegulationChain(llm("x"), StubSearcher())
        with self.assertRaises(ValueError):
            chain.invoke("질문", language="fr")
        with self.assertRaises(ValueError):
            chain.invoke("  ")


class SettingsTests(unittest.TestCase):
    def test_model_must_be_configured_and_not_retired(self):
        with self.assertRaises(RagConfigError):
            RagSettings.from_env({"GROQ_API_KEY": "k"})
        with self.assertRaises(RagConfigError):
            RagSettings.from_env({"GROQ_MODEL": "openai/gpt-oss-120b"})
        with self.assertRaises(RagConfigError):
            RagSettings.from_env({"GROQ_API_KEY": "k", "GROQ_MODEL": "meta-llama/llama-4-scout-17b-16e-instruct"})
        settings = RagSettings.from_env({"GROQ_API_KEY": "k", "GROQ_MODEL": "openai/gpt-oss-120b"})
        self.assertEqual(settings.groq_model, "openai/gpt-oss-120b")


if __name__ == "__main__":
    unittest.main()
