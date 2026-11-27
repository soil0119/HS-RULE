"""RAG 설정 (#27). LLM 제공자(Gemini·Groq)와 모델은 설정으로만 고르고, 종료된 모델은 기본값으로 쓰지 않는다.

기본 제공자는 Groq다. Gemini 무료 등급은 프로젝트별로 자격이 갈려(시연 당시 billing 요구) 확실히 무료라고
보장할 수 없는 반면, Groq는 가입만 하면 바로 무료 한도를 쓸 수 있다."""
import os
import re
from dataclasses import dataclass

PROVIDERS = ("gemini", "groq")
DEFAULT_PROVIDER = "groq"
# 종료되었거나 지원 목록에서 빠진 모델. 설정에 있어도 거부한다.
RETIRED_GROQ_MODELS = frozenset({"meta-llama/llama-4-scout-17b-16e-instruct"})
RETIRED_GEMINI_MODELS = frozenset({"gemini-2.0-flash", "gemini-2.0-flash-lite"})
_ENV_NAMES = {"gemini": ("GEMINI_API_KEY", "GEMINI_MODEL"), "groq": ("GROQ_API_KEY", "GROQ_MODEL")}
_RETIRED = {"gemini": RETIRED_GEMINI_MODELS, "groq": RETIRED_GROQ_MODELS}


class RagConfigError(ValueError):
    """RAG 설정이 올바르지 않다."""


@dataclass(frozen=True)
class RagSettings:
    provider: str
    api_key: str
    model: str
    retrieval_k: int = 6
    history_limit: int = 12
    temperature: float = 0.0

    @classmethod
    def from_env(cls, env=None) -> "RagSettings":
        env = os.environ if env is None else env
        provider = (env.get("LLM_PROVIDER", "").strip() or DEFAULT_PROVIDER).lower()
        if provider not in PROVIDERS:
            raise RagConfigError(f"LLM_PROVIDER는 {', '.join(PROVIDERS)} 중 하나여야 합니다.")
        key_name, model_name = _ENV_NAMES[provider]
        key, model = env.get(key_name, "").strip(), env.get(model_name, "").strip()
        if not key:
            raise RagConfigError(f"{key_name}가 설정되지 않았습니다.")
        if not model:
            raise RagConfigError(f"{model_name}이 설정되지 않았습니다. 현재 지원 모델을 직접 지정하세요.")
        if model in _RETIRED[provider]:
            raise RagConfigError(f"{model}은(는) 종료된 모델입니다. 현재 지원 모델을 지정하세요.")
        raw_k = env.get("RAG_RETRIEVAL_K", "").strip() or "6"
        if not raw_k.isdigit() or not 1 <= int(raw_k) <= 12:
            raise RagConfigError("RAG_RETRIEVAL_K는 1~12 사이의 정수여야 합니다.")
        return cls(provider, key, model, retrieval_k=int(raw_k))


def _gemini_generation(model: str) -> int | None:
    match = re.match(r"(?:models/)?gemini-(\d+)", model)
    return int(match.group(1)) if match else None


def create_llm(settings: RagSettings):
    if settings.provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        # provider 자체 재시도는 끈다(attempts=1). 재시도는 요청 마감을 아는 hsrule.chains.invoke_within이 맡는다(#51).
        options = {"model": settings.model, "google_api_key": settings.api_key, "max_retries": 1, "timeout": 60}
        generation = _gemini_generation(settings.model)
        # Gemini 3 이후 모델은 Google이 기본 temperature 유지를 권고한다(낮추면 반복·품질 저하 가능).
        # 이 경우 값을 넘기지 않아 라이브러리 기본값을 따른다.
        if generation is not None and generation < 3:
            options["temperature"] = settings.temperature
        return ChatGoogleGenerativeAI(**options)
    from langchain_groq import ChatGroq

    return ChatGroq(model=settings.model, api_key=settings.api_key,
                    temperature=settings.temperature, max_retries=0, timeout=60)


def message_text(content) -> str:
    """모델 응답 content를 문자열로 만든다. Gemini는 content를 블록 목록으로 줄 수 있다."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type", "text") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    return str(content)
