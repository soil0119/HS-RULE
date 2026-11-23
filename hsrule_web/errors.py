from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse


class APIError(HTTPException):
    def __init__(self, status: int, code: str, detail: str, headers: dict[str, str] | None = None):
        super().__init__(status, detail, headers)
        self.code = code


def error_response(request: Request, error: APIError) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        content={"detail": error.detail, "code": error.code, "request_id": request.state.request_id},
        headers=error.headers,
    )
