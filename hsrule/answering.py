"""구조화 출력·후보 기반 조항 인용·오류 계약 (#28).

모델은 검색된 후보 라벨(S1, S2...)로만 인용할 수 있다. 서버가 후보 메타데이터에서 출처를 만들고,
후보에 없는 인용·근거 없는 답변은 거부한다. 페이지 번호는 만들지 않는다.
"""
import json
import re

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field, ValidationError

from hsrule.chains import ANSWER_SYSTEM, ChainTimeout, RegulationChain, label_documents

REFUSAL = {
    "ko": "제공된 공식 규정에서 이 질문에 해당하는 근거를 찾지 못했습니다. 담당 부서에 문의해 주세요.",
    "en": "I could not find a basis for this question in the official regulations provided. Please contact the relevant office.",
}
JSON_INSTRUCTION = (
    '\n\n출력 형식: 아래 JSON 객체 하나만 출력한다(설명·코드블록 금지).\n'
    '{"found": true|false, "answer": "답변", "citations": ["S1"], "followups": ["후속 질문"]}\n'
    "- found는 [근거]만으로 답할 수 있을 때만 true. false이면 citations는 빈 배열이다.\n"
    "- citations에는 답변에 실제로 사용한 [근거]의 라벨만 넣는다. 없는 라벨을 만들지 않는다.\n"
    "- followups는 최대 3개."
)


class RagError(RuntimeError):
    """웹 API 오류 계약: rag_backend_error(502), rag_invalid_response(502).

    `code`는 웹 API가 그대로 쓰는 계약 코드이고, `reason`은 원인을 구분하는 내부 값
    (timeout·provider·json·scope)이다. provider 예외의 본문은 메시지에 담지 않는다.
    원문·비밀값이 섞여 들어갈 수 있기 때문이다.
    """

    def __init__(self, code: str, message: str, reason: str = ""):
        super().__init__(message)
        self.code = code
        self.reason = reason or code


class ModelAnswer(BaseModel):
    found: bool
    answer: str = Field(min_length=1, max_length=6000)
    citations: list[str] = Field(default_factory=list)
    followups: list[str] = Field(default_factory=list)


def extract_json(text: str) -> dict:
    """모델 출력에서 첫 JSON 객체를 꺼낸다(코드블록·앞뒤 설명을 허용)."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start = text.find("{")
    if start < 0:
        raise ValueError("JSON 객체가 없습니다.")
    value, _ = json.JSONDecoder().raw_decode(text[start:])
    if not isinstance(value, dict):
        raise ValueError("JSON 객체가 아닙니다.")
    return value


def source_of(doc) -> dict:
    """출처를 만든다.

    공식 HTML 규정이므로 조항 ID와 원문 오프셋을 쓰고 페이지는 만들지 않는다(`source_kind`).
    표시용 URL 생성은 웹 API 계약에 맡기고 여기서는 공식 URL만 전달한다.
    `score`는 벡터 검색의 코사인 유사도만 담고, 없으면 None이다(RRF 값을 유사도로 쓰지 않는다).
    """
    meta = doc.metadata
    return {"label": meta["label"], "chunk_id": meta["chunk_id"], "regulation_id": meta["regulation_id"],
            "revision_id": meta["revision_id"], "title": meta["title"], "article_id": meta["article_id"],
            "url": meta["official_url"], "source_kind": "official_html",
            "source_start": meta["source_start"], "source_end": meta["source_end"],
            "score": meta.get("cosine")}


def validate(parsed: ModelAnswer, docs, *, include_private: bool = False) -> tuple[list[str], list[str]]:
    """(유효 인용 라벨, 버린 라벨)을 반환한다. 근거를 찾았다는 답은 유효 인용이 있어야 한다."""
    valid = {doc.metadata["label"] for doc in docs}
    kept = [label for label in dict.fromkeys(parsed.citations) if label in valid]
    dropped = [label for label in dict.fromkeys(parsed.citations) if label not in valid]
    if parsed.found and not kept:
        raise RagError("rag_invalid_response", "근거를 찾았다고 했지만 검색 후보에 해당하는 인용이 없습니다.",
                       "citation")
    if not include_private:  # 검색 단계의 권한 필터를 답변 단계에서 한 번 더 확인한다.
        by_label = {doc.metadata["label"]: doc for doc in docs}
        leaked = [label for label in kept if by_label[label].metadata.get("visibility") != "public"]
        if leaked:
            raise RagError("rag_invalid_response", "공개 질의의 출처에 비공개 자료가 섞였습니다.", "scope")
    return kept, dropped


class CitedAnswerer:
    def __init__(self, llm, searcher, *, k: int = 6, history_limit: int = 12, max_parse_attempts: int = 2):
        self.chain = RegulationChain(llm, searcher, k=k, history_limit=history_limit)
        self.llm, self.max_parse_attempts = llm, max_parse_attempts

    def answer(self, question: str, messages: list[dict] | None = None, *, include_private: bool = False,
               language: str = "ko") -> dict:
        try:
            state = self.chain.prepare(question, messages, include_private=include_private, language=language)
        except ValueError:
            raise
        except ChainTimeout as exc:
            raise RagError("rag_backend_error", "검색 또는 질문 재작성이 시간 안에 끝나지 않았습니다.",
                           "timeout") from exc
        except Exception as exc:
            raise RagError("rag_backend_error", "검색 단계에서 오류가 발생했습니다.", "provider") from exc
        docs = state["docs"]
        if not docs:  # 근거 후보가 없으면 모델을 부르지 않고 거절한다(근거 없음).
            return {"answer": REFUSAL[language], "found": False, "sources": [], "followups": [],
                    "reason": "no_evidence"}
        parsed = self._ask(state)
        kept, dropped = validate(parsed, docs, include_private=include_private)
        if not parsed.found:
            return {"answer": parsed.answer, "found": False, "sources": [], "followups": parsed.followups[:3]}
        by_label = {doc.metadata["label"]: doc for doc in docs}
        return {"answer": parsed.answer, "found": True, "sources": [source_of(by_label[l]) for l in kept],
                "followups": [f.strip() for f in parsed.followups if f.strip()][:3],
                "dropped_citations": dropped}

    def _ask(self, state: dict) -> ModelAnswer:
        system = ANSWER_SYSTEM[state["language"]] + JSON_INSTRUCTION
        messages = self.chain._answer_prompt.format_messages(
            system=system, evidence=state["evidence"], history=state["history"], question=state["question"])
        last_error = None
        for _ in range(self.max_parse_attempts):
            try:
                text = self.llm.invoke(messages).content
            except ChainTimeout as exc:
                raise RagError("rag_backend_error", "답변 모델이 시간 안에 응답하지 않았습니다.", "timeout") from exc
            except TimeoutError as exc:
                raise RagError("rag_backend_error", "답변 모델이 시간 안에 응답하지 않았습니다.", "timeout") from exc
            except Exception as exc:
                # provider 예외 본문에는 원문이나 키가 섞일 수 있으므로 메시지에 담지 않는다.
                raise RagError("rag_backend_error", "답변 모델 호출에 실패했습니다.", "provider") from exc
            try:
                return ModelAnswer.model_validate(extract_json(text if isinstance(text, str) else str(text)))
            except (ValueError, ValidationError) as exc:
                last_error = exc
                messages = messages + [HumanMessage("형식 오류: 지정한 JSON 객체만 다시 출력하라. " + str(exc)[:200])]
        raise RagError("rag_invalid_response", "답변 모델이 올바른 JSON을 반환하지 않았습니다.", "json") from last_error
