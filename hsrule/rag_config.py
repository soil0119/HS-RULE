"""RAG 설정 (#27). Groq 모델은 설정으로만 고르고, 종료된 모델은 기본값으로 쓰지 않는다."""
import os
from dataclasses import dataclass

# 종료되었거나 지원 목록에서 빠진 모델. 설정에 있어도 거부한다.
RETIRED_GROQ_MODELS = frozenset({"meta-llama/llama-4-scout-17b-16e-instruct"})


class RagConfigError(ValueError):
    """RAG 설정이 올바르지 않다."""


@dataclass(frozen=True)
class RagSettings:
    groq_api_key: str
    groq_model: str
    retrieval_k: int = 6
    history_limit: int = 12
    temperature: float = 0.0

    @classmethod
    def from_env(cls, env=None) -> "RagSettings":
        env = os.environ if env is None else env
        key, model = env.get("GROQ_API_KEY", "").strip(), env.get("GROQ_MODEL", "").strip()
        if not key:
            raise RagConfigError("GROQ_API_KEY가 설정되지 않았습니다.")
        if not model:
            raise RagConfigError("GROQ_MODEL이 설정되지 않았습니다. 현재 지원 모델을 직접 지정하세요.")
        if model in RETIRED_GROQ_MODELS:
            raise RagConfigError(f"{model}은(는) 종료된 모델입니다. 현재 지원 모델을 지정하세요.")
        return cls(key, model, retrieval_k=int(env.get("RAG_RETRIEVAL_K", "6")))


def create_llm(settings: RagSettings):
    from langchain_groq import ChatGroq

    return ChatGroq(model=settings.groq_model, api_key=settings.groq_api_key,
                    temperature=settings.temperature, max_retries=2, timeout=60)
