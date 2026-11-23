import re
from typing import Annotated, Any, Literal, Protocol, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, FiniteFloat, ValidationError, model_validator

from hsrule_web.documents import DOCUMENT_ID_PATTERN, DocumentMetadata, MAX_PDF_BYTES
from hsrule_web.errors import APIError

Locale = Literal["ko", "en"]
MAX_JSON_BODY = 1024 * 1024
MAX_MULTIPART_BODY = MAX_PDF_BYTES + 64 * 1024
INVALID_TEXT = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


def validate_text(value: str) -> str:
    if INVALID_TEXT.search(value):
        raise ValueError("Invalid text character")
    return value


def non_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("Text must not be blank")
    return value


DocumentId = Annotated[str, Field(pattern=DOCUMENT_ID_PATTERN)]
Text = Annotated[str, AfterValidator(validate_text)]
Question = Annotated[Text, Field(min_length=1, max_length=2000), AfterValidator(non_blank)]


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Message(InputModel):
    role: Literal["user", "assistant"]
    content: Annotated[Text, Field(min_length=1, max_length=4000), AfterValidator(non_blank)]


class QueryRequest(InputModel):
    question: Question
    messages: list[Message] = Field(default_factory=list, max_length=12)
    language: Locale = "ko"


class SourceReference(BaseModel):
    document_id: DocumentId
    page_start: int = Field(strict=True, ge=1, le=1000)
    page_end: int = Field(strict=True, ge=1, le=1000)
    chunk_id: Text | None = Field(default=None, min_length=1, max_length=256)
    score: FiniteFloat | None = Field(default=None, strict=True)

    @model_validator(mode="after")
    def check_range(self) -> Self:
        if self.page_end < self.page_start:
            raise ValueError("Invalid page range")
        return self


def source_url(document_id: str, page: int | None = None) -> str:
    url = f"/documents/{document_id}/file"
    return f"{url}#page={page}" if page is not None else url


class QuerySource(SourceReference):
    model_config = ConfigDict(extra="forbid")

    title: Text = Field(min_length=1, max_length=200)
    url: str = Field(max_length=100)

    @model_validator(mode="after")
    def check_url(self) -> Self:
        if self.url != source_url(self.document_id, self.page_start):
            raise ValueError("Invalid source URL")
        return self


class AnswerFields(BaseModel):
    answer: Text = Field(min_length=1, max_length=30_000)
    found: bool = Field(strict=True)
    followups: list[Annotated[Text, Field(min_length=1, max_length=2000)]] = Field(max_length=12)
    dept: Text | None = Field(default=None, max_length=200)
    dept_phone: Text | None = Field(default=None, max_length=100)


class RagAnswer(AnswerFields):
    sources: list[SourceReference] = Field(max_length=50)


class QueryResponse(AnswerFields):
    sources: list[QuerySource]


class RuleItem(DocumentMetadata):
    name: str
    url: str
    code: str = ""
    dept: str = ""
    uploaded: bool = True


class RuleChapter(BaseModel):
    chapter: str
    rules: list[RuleItem]


class RulesResponse(BaseModel):
    chapters: list[RuleChapter]
    total: int


class AdminRulesResponse(BaseModel):
    rules: list[DocumentMetadata]


class UploadResponse(BaseModel):
    success: Literal[True] = True
    document: DocumentMetadata


class DeleteResponse(BaseModel):
    success: Literal[True] = True
    document_id: DocumentId


class HealthResponse(BaseModel):
    ok: Literal[True] = True


class ConflictItem(BaseModel):
    regulation: Text = Field(max_length=200)
    article: Text = Field(max_length=200)
    issue: Text = Field(max_length=4000)
    severity: Literal["높음", "보통", "낮음", "high", "medium", "low"]


class ConflictResult(BaseModel):
    summary: Text = Field(max_length=10_000)
    conflicts: list[ConflictItem] = Field(max_length=50)
    recommendations: Text = Field(max_length=10_000)


class ConflictResponse(ConflictResult):
    filename: str


class ExportRequest(InputModel):
    title: Text = Field(default="규정 답변 리포트", max_length=200)
    content: Annotated[Text, Field(min_length=1, max_length=30_000), AfterValidator(non_blank)]
    sources: list[QuerySource] = Field(default_factory=list, max_length=50)
    question: Text = Field(default="", max_length=2000)


class RagBackend(Protocol):
    def answer(self, question: str, messages: list[dict[str, str]], include_private: bool = False,
               *, language: Locale = "ko") -> dict[str, Any]: ...

    def analyze_conflict(self, text: str, filename: str, include_private: bool = True,
                         *, language: Locale = "ko") -> dict[str, Any]: ...


def require_rag(backend: RagBackend | None) -> RagBackend:
    if backend is None:
        raise APIError(503, "rag_not_connected", "규정 검색 및 분석 서비스가 연결되지 않았습니다.")
    return backend


def invalid_rag_response() -> APIError:
    return APIError(502, "rag_invalid_response", "검색 또는 분석 결과의 형식이 올바르지 않습니다.")


def call_rag(method, result_type, *args, **kwargs):
    try:
        payload = method(*args, **kwargs)
    except Exception:
        raise APIError(502, "rag_backend_error", "검색 또는 분석 서비스에 문제가 발생했습니다.") from None
    try:
        return result_type.model_validate(payload)
    except ValidationError:
        raise invalid_rag_response() from None
