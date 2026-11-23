import re
from threading import BoundedSemaphore
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, Path, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException

from hsrule_web.auth import LoginLimiter, LoginRequest, LoginResponse, issue_token, optional_admin, require_admin
from hsrule_web.contracts import (
    MAX_JSON_BODY, MAX_MULTIPART_BODY, AdminRulesResponse, DeleteResponse, HealthResponse, QueryRequest,
    QueryResponse, QuerySource, RagAnswer, RagBackend, RuleChapter, RuleItem, RulesResponse, SourceReference,
    UploadResponse, call_rag, invalid_rag_response, require_rag, source_url,
)
from hsrule_web.documents import DOCUMENT_ID_PATTERN, DocumentMetadata, DocumentStore, Visibility, read_upload
from hsrule_web.errors import APIError, error_response
from hsrule_web.settings import BASE_DIR, Settings
from routers.teacher import router as teacher_router

NO_CACHE = {"Cache-Control": "no-store"}
DEV_ORIGIN = re.compile(r"https?://(?:localhost|127\.0\.0\.1)(?::[0-9]{1,5})?")
EXPOSE_HEADERS = ["X-Request-ID", "Content-Disposition", "Content-Range", "Retry-After"]
LOGIN_BODY = 16 * 1024
BODY_LIMITS = {
    "/login": LOGIN_BODY,
    "/query": MAX_JSON_BODY,
    "/export/pdf": MAX_JSON_BODY,
    "/export/docx": MAX_JSON_BODY,
    "/conflict/": MAX_MULTIPART_BODY,
    "/upload-regulation": MAX_MULTIPART_BODY,
}
DocumentId = Annotated[str, Path(pattern=DOCUMENT_ID_PATTERN)]


def create_app(
    settings: Settings | None = None,
    document_store: DocumentStore | None = None,
    rag_backend: RagBackend | None = None,
) -> FastAPI:
    if settings is None:
        settings = Settings.from_env()
    if document_store is None and settings.database_url:
        document_store = DocumentStore(settings.database_url, settings.upload_dir)
    allowed_hosts = settings.hosts
    public_origin = settings.origin
    app = FastAPI(form_limits={"max_files": 1, "max_fields": 2, "max_part_size": 4096})
    app.state.settings = settings
    app.state.document_store = document_store
    app.state.rag_backend = rag_backend
    login_limiter = LoginLimiter()
    # ponytail: per-worker admission for requests with bodies or DB reads.
    requests_in_flight = BoundedSemaphore(8)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[public_origin],
        allow_origin_regex=DEV_ORIGIN.pattern,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Range"],
        expose_headers=EXPOSE_HEADERS,
    )
    app.include_router(teacher_router)

    @app.middleware("http")
    async def request_boundary(request: Request, call_next):
        request.state.request_id = uuid4().hex
        acquired = False
        try:
            try:
                host = request.url.hostname
            except ValueError:
                host = None
            if host not in allowed_hosts:
                raise APIError(400, "invalid_host", "허용되지 않은 호스트입니다.")
            path = request.url.path
            if (request.method in ("POST", "DELETE") or path in ("/health", "/rules", "/uploaded-rules")
                    or path.startswith("/documents/")):
                acquired = requests_in_flight.acquire(blocking=False)
                if not acquired:
                    raise APIError(429, "request_limit", "요청이 많습니다. 잠시 후 다시 시도해 주세요.",
                                   {"Retry-After": "1"})
            # Route body limits also count streamed requests without Content-Length.
            limit = BODY_LIMITS.get(path) if request.method == "POST" else None
            length = request.headers.get("content-length")
            if limit and length is not None:
                if not length.isascii() or not length.isdecimal() or len(length) > 12:
                    raise APIError(400, "invalid_input", "본문 길이 형식이 올바르지 않습니다.")
                if int(length) > limit:
                    raise body_too_large()
            response = await call_next(request)
            if (request.method == "OPTIONS" and "access-control-request-method" in request.headers
                    and response.status_code == 400):
                response = error_response(request, APIError(
                    400, "cors_not_allowed", "허용되지 않은 교차 출처 요청입니다."
                ))
            elif (path.startswith("/documents/") and path.endswith("/file") and "range" in request.headers
                    and response.status_code in (400, 416)):
                range_headers = {"Content-Range": response.headers["Content-Range"]} \
                    if "Content-Range" in response.headers else None
                response = error_response(request, APIError(
                    response.status_code, "invalid_range", "PDF 바이트 범위가 올바르지 않습니다.", range_headers
                ))
        except APIError as error:
            response = error_response(request, error)
        except Exception:
            response = error_response(request, APIError(500, "internal_error", "요청을 처리하지 못했습니다."))
        finally:
            if acquired:
                requests_in_flight.release()
        # Middleware-generated errors still need the allowed-origin headers for browsers.
        origin = request.headers.get("origin")
        if (response.status_code >= 400 and "access-control-request-method" not in request.headers and origin
                and (origin == public_origin or DEV_ORIGIN.fullmatch(origin))):
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Expose-Headers"] = ", ".join(EXPOSE_HEADERS)
            if "Vary" not in response.headers:
                response.headers.add_vary_header("Origin")
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(APIError)
    async def handle_api_error(request: Request, error: APIError):
        return error_response(request, error)

    @app.exception_handler(RequestValidationError)
    async def handle_validation(request: Request, error: RequestValidationError):
        return error_response(request, APIError(422, "invalid_input", "요청 형식 또는 값이 올바르지 않습니다."))

    @app.exception_handler(HTTPException)
    async def handle_http_error(request: Request, error: HTTPException):
        if error.status_code == 413:
            return error_response(request, body_too_large())
        detail = "요청한 항목을 찾을 수 없습니다." if error.status_code == 404 else "요청을 처리하지 못했습니다."
        return error_response(request, APIError(error.status_code, "http_error", detail, error.headers))

    def get_store() -> DocumentStore:
        if document_store is None:
            raise APIError(503, "documents_not_configured", "문서 보관소가 설정되지 않았습니다.")
        return document_store

    def find_document(document_id: str, admin: bool) -> DocumentMetadata:
        document = get_store().get(document_id, include_private=admin)
        if document is None or (not admin and document.metadata.visibility != "public"):
            raise document_not_found()
        return document.metadata

    def static_file(filename: str):
        path = BASE_DIR / filename
        if not path.is_file():
            raise APIError(404, "file_not_found", "요청한 파일을 찾을 수 없습니다.")
        return FileResponse(path, headers=NO_CACHE)

    @app.get("/")
    def root():
        return static_file("index.html")

    @app.get("/login-page")
    def login_page():
        return static_file("login.html")

    @app.get("/upload")
    def upload_page():
        return static_file("upload.html")

    @app.get("/HSU_logo.png")
    def logo():
        return static_file("HSU_logo.png")

    @app.get("/health/live", response_model=HealthResponse)
    def live():
        return HealthResponse()

    @app.get("/health", response_model=HealthResponse)
    def readiness():
        if (not settings.admin_enabled or rag_backend is None or document_store is None
                or not document_store.health()):
            raise APIError(503, "not_ready", "서비스에 필요한 설정 또는 연결이 준비되지 않았습니다.")
        return HealthResponse()

    @app.post("/login", response_model=LoginResponse, max_body_size=LOGIN_BODY)
    def login(data: LoginRequest, request: Request):
        if not settings.admin_enabled:
            raise APIError(503, "admin_not_configured", "관리자 로그인이 설정되지 않았습니다.")
        login_limiter.reserve(request.client.host if request.client else "")
        return LoginResponse(token=issue_token(settings, data))

    @app.get("/rules", response_model=RulesResponse)
    def rules():
        public_documents = get_store().list()
        entries = [
            RuleItem(**metadata.model_dump(), name=metadata.title, url=source_url(metadata.id))
            for metadata in public_documents if metadata.visibility == "public"
        ]
        chapters = [RuleChapter(chapter="업로드 규정", rules=entries)] if entries else []
        return RulesResponse(chapters=chapters, total=len(entries))

    @app.get("/uploaded-rules", response_model=AdminRulesResponse, dependencies=[Depends(require_admin)])
    def uploaded_rules():
        return AdminRulesResponse(rules=get_store().list(include_private=True))

    @app.post("/upload-regulation", response_model=UploadResponse, status_code=201,
              dependencies=[Depends(require_admin)], max_body_size=MAX_MULTIPART_BODY)
    def upload_document(
        file: Annotated[UploadFile, File()],
        title: Annotated[str | None, Form(max_length=200)] = None,
        visibility: Annotated[Visibility, Form()] = "private",
    ):
        metadata = get_store().save(read_upload(file.file), file.filename or "", title=title, visibility=visibility)
        return UploadResponse(document=metadata)

    @app.delete("/uploaded-rules/{document_id}", response_model=DeleteResponse,
                dependencies=[Depends(require_admin)])
    def delete_document(document_id: DocumentId):
        if not get_store().delete(document_id):
            raise document_not_found()
        return DeleteResponse(document_id=document_id)

    @app.get("/documents/{document_id}", response_model=DocumentMetadata)
    def document_metadata(document_id: DocumentId, admin: bool = Depends(optional_admin)):
        return find_document(document_id, admin)

    @app.get("/documents/{document_id}/file", response_class=FileResponse)
    def document_file(document_id: DocumentId, admin: bool = Depends(optional_admin)):
        metadata = find_document(document_id, admin)
        path = get_store().file_path(document_id)
        if not path.is_file():
            raise document_not_found()
        return FileResponse(path, media_type="application/pdf", filename=metadata.filename,
                            content_disposition_type="inline", headers={"X-Content-Type-Options": "nosniff"})

    @app.post("/query", response_model=QueryResponse, response_model_exclude_none=True, max_body_size=MAX_JSON_BODY)
    def query(data: QueryRequest):
        backend = require_rag(rag_backend)
        store = get_store()
        result = call_rag(backend.answer, RagAnswer, data.question.strip(),
                          [message.model_dump() for message in data.messages],
                          include_private=False, language=data.language)
        sources = verified_sources(store, result.sources)
        if result.found and not sources:
            raise invalid_rag_response()
        return QueryResponse(**result.model_dump(exclude={"sources"}), sources=sources)

    return app


def verified_sources(store: DocumentStore, references: list[object]) -> list[QuerySource]:
    sources = []
    metadata_cache: dict[str, DocumentMetadata | None] = {}
    for item in references:
        try:
            reference = SourceReference.model_validate(item)
        except ValidationError:
            continue
        document_id = reference.document_id
        if document_id not in metadata_cache:
            document = store.get(document_id)
            metadata = document.metadata if document is not None else None
            if metadata is not None and (metadata.visibility != "public" or not store.file_path(document_id).is_file()):
                metadata = None
            metadata_cache[document_id] = metadata
        metadata = metadata_cache[document_id]
        if metadata is not None and reference.page_end <= metadata.page_count:
            sources.append(QuerySource(**reference.model_dump(), title=metadata.title,
                                       url=source_url(document_id, reference.page_start)))
    return sources


def body_too_large():
    return APIError(413, "request_too_large", "요청 본문 크기가 허용 한도를 초과했습니다.")


def document_not_found():
    return APIError(404, "document_not_found", "해당 문서를 찾을 수 없습니다.")


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080, proxy_headers=False)
