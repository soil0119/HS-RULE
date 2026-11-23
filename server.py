from threading import BoundedSemaphore
from uuid import uuid4

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.datastructures import URL
from starlette.exceptions import HTTPException

from hsrule_web.auth import LoginLimiter, LoginRequest, LoginResponse, issue_token, require_admin
from hsrule_web.errors import APIError, error_response
from hsrule_web.settings import BASE_DIR, Settings

NO_CACHE = {"Cache-Control": "no-store"}


def create_app(settings=None, document_store=None, rag_backend=None):
    settings = settings if settings is not None else Settings.from_env()
    app = FastAPI()
    app.state.settings = settings
    app.state.document_store = document_store
    app.state.rag_backend = rag_backend
    login_limiter = LoginLimiter()
    # ponytail: per-worker admission for requests with bodies or DB reads.
    requests_in_flight = BoundedSemaphore(8)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.public_origin],
        allow_origin_regex=r"https?://(?:localhost|127\.0\.0\.1)(?::[0-9]{1,5})?",
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.middleware("http")
    async def request_boundary(request: Request, call_next):
        request.state.request_id = uuid4().hex
        needs_slot = (request.method in ("POST", "DELETE")
                      or request.url.path in ("/health", "/rules", "/uploaded-rules")
                      or request.url.path.startswith("/documents/"))
        acquired = False
        try:
            # Starlette reads the Host header through URL(scope); avoid forwarded host headers.
            host = URL(scope=request.scope).hostname
            if host not in settings.hosts:
                raise APIError(400, "invalid_host", "허용되지 않은 호스트입니다.")
            if needs_slot:
                acquired = requests_in_flight.acquire(blocking=False)
                if not acquired:
                    raise APIError(429, "request_limit", "요청이 많습니다. 잠시 후 다시 시도해 주세요.",
                                   {"Retry-After": "1"})
            response = await call_next(request)
            if request.method == "OPTIONS" and response.status_code == 400:
                response = error_response(request, APIError(
                    400, "cors_not_allowed", "허용되지 않은 교차 출처 요청입니다."
                ))
        except APIError as error:
            response = error_response(request, error)
        except ValueError:
            response = error_response(request, APIError(400, "invalid_host", "허용되지 않은 호스트입니다."))
        except Exception:
            response = error_response(request, APIError(500, "internal_error", "요청을 처리하지 못했습니다."))
        finally:
            if acquired:
                requests_in_flight.release()
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(APIError)
    async def handle_api_error(request: Request, error: APIError):
        return error_response(request, error)

    @app.exception_handler(RequestValidationError)
    async def handle_validation(request: Request, error: RequestValidationError):
        return error_response(request, APIError(422, "invalid_input", "요청 형식 또는 값이 올바르지 않습니다."))

    @app.exception_handler(HTTPException)
    async def handle_http_error(request: Request, error: HTTPException):
        detail = "요청한 항목을 찾을 수 없습니다." if error.status_code == 404 else "요청을 처리하지 못했습니다."
        return error_response(request, APIError(error.status_code, "http_error", detail, error.headers))

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

    @app.get("/health/live")
    def live():
        return {"ok": True}

    @app.get("/health")
    def readiness():
        if document_store is None or not document_store.health() or rag_backend is None or not settings.admin_enabled:
            raise APIError(503, "not_ready", "서비스에 필요한 설정 또는 연결이 준비되지 않았습니다.")
        return {"ok": True}

    @app.post("/login", response_model=LoginResponse)
    def login(data: LoginRequest, request: Request):
        if not settings.admin_enabled:
            raise APIError(503, "admin_not_configured", "관리자 로그인이 설정되지 않았습니다.")
        login_limiter.reserve(request.client.host if request.client else "")
        return LoginResponse(token=issue_token(settings, data))

    @app.get("/uploaded-rules", dependencies=[Depends(require_admin)])
    def uploaded_rules():
        if document_store is None:
            raise APIError(503, "documents_not_configured", "문서 보관소가 설정되지 않았습니다.")
        return {"rules": document_store.list(include_private=True)}

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080, proxy_headers=False)
