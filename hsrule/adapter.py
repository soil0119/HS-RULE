"""웹 API가 주입하는 RagBackend 어댑터 (#10). hsrule_web.contracts.RagBackend 계약을 따른다."""
from hsrule.answering import CitedAnswerer
from hsrule.conflict import ConflictAnalyzer


class RegulationAdapter:
    def __init__(self, llm, searcher, *, k: int = 6, history_limit: int = 12):
        self._answerer = CitedAnswerer(llm, searcher, k=k, history_limit=history_limit)
        self._conflicts = ConflictAnalyzer(llm, searcher)

    def answer(self, question, messages, include_private=False, *, language="ko") -> dict:
        result = self._answerer.answer(question, messages, include_private=include_private, language=language)
        return {key: result[key] for key in ("answer", "sources", "found", "followups")}

    def analyze_conflict(self, text, filename, include_private=True, *, language="ko") -> dict:
        result = self._conflicts.analyze(text, filename, include_private=include_private, language=language)
        payload = {key: result[key] for key in ("summary", "conflicts", "scope")}
        # 웹 API의 ConflictResult.recommendations는 문자열이다(최대 10,000자).
        # 근거 조항을 괄호로 함께 적고, 구조화된 원본은 recommendation_details에 둔다.
        label = "근거" if language == "ko" else "basis"
        payload["recommendations"] = "\n".join(
            f"- {item['text']} ({label}: {item['regulation']} {item['article']})"
            for item in result["recommendations"])[:10_000]
        payload["recommendation_details"] = result["recommendations"]
        return payload


def create_regulation_adapter(repository, embeddings=None, settings=None, llm=None) -> RegulationAdapter:
    """설정(GROQ_MODEL 등)과 저장소로 어댑터를 만든다. 테스트에서는 llm·embeddings를 주입한다."""
    from hsrule.embeddings import LocalEmbeddings
    from hsrule.official_retrieval import HybridRetriever
    from hsrule.rag_config import RagSettings, create_llm

    settings = settings or RagSettings.from_env()
    embeddings = embeddings or LocalEmbeddings()
    return RegulationAdapter(llm or create_llm(settings), HybridRetriever(repository, embeddings, k=settings.retrieval_k),
                             k=settings.retrieval_k, history_limit=settings.history_limit)
