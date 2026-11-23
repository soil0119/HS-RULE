from collections import deque
from hmac import compare_digest
from math import ceil
from threading import Lock
from time import monotonic, time

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from hsrule_web.errors import APIError
from hsrule_web.settings import Settings

ISSUER = "hsrule-web"
AUDIENCE = "hsrule-admin"
TOKEN_SECONDS = 12 * 60 * 60
BEARER = HTTPBearer(auto_error=False)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=1, max_length=1024)


class LoginResponse(BaseModel):
    success: bool = True
    token: str


class LoginLimiter:
    """Reserve an attempt before checking credentials to bound simultaneous logins."""

    WINDOW = 60
    PER_PEER = 5
    TOTAL = 120

    def __init__(self):
        # ponytail: each worker keeps a bounded one-minute window.
        self._attempts: deque[tuple[float, str]] = deque()
        self._lock = Lock()

    def reserve(self, peer: str) -> None:
        with self._lock:
            now = monotonic()
            while self._attempts and self._attempts[0][0] <= now - self.WINDOW:
                self._attempts.popleft()
            if len(self._attempts) >= self.TOTAL:
                first = self._attempts[0][0]
            else:
                matching = [started for started, address in self._attempts if address == peer]
                if len(matching) < self.PER_PEER:
                    self._attempts.append((now, peer))
                    return
                first = matching[0]
            raise APIError(429, "login_rate_limited", "로그인 시도 한도를 초과했습니다.",
                           {"Retry-After": str(ceil(first + self.WINDOW - now))})


def issue_token(settings: Settings, login: LoginRequest) -> str:
    username_ok = compare_digest(login.username.encode(), settings.admin_id.encode())
    password_ok = compare_digest(login.password.encode(), settings.admin_pw.encode())
    if not (username_ok and password_ok):
        raise APIError(401, "invalid_credentials", "아이디 또는 비밀번호가 올바르지 않습니다.")
    issued_at = int(time())
    return jwt.encode(
        {"sub": settings.admin_id, "iss": ISSUER, "aud": AUDIENCE,
         "iat": issued_at, "exp": issued_at + TOKEN_SECONDS},
        settings.secret_key, algorithm="HS256",
    )


def optional_admin(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(BEARER),
) -> bool:
    if "authorization" not in request.headers:
        return False
    settings = request.app.state.settings
    if credentials is not None and settings.admin_enabled:
        try:
            claims = jwt.decode(
                credentials.credentials, settings.secret_key, algorithms=["HS256"],
                issuer=ISSUER, audience=AUDIENCE,
                options={"require": ["sub", "iss", "aud", "iat", "exp"], "strict_aud": True},
            )
            if (claims["sub"] == settings.admin_id
                    and type(claims["iat"]) is int and type(claims["exp"]) is int
                    and 0 < claims["exp"] - claims["iat"] <= TOKEN_SECONDS):
                return True
        except (jwt.InvalidTokenError, ValueError, TypeError, OverflowError):
            pass
    raise APIError(401, "invalid_token", "유효한 관리자 토큰이 필요합니다.",
                   {"WWW-Authenticate": "Bearer"})


def require_admin(admin: bool = Depends(optional_admin)) -> None:
    if not admin:
        raise APIError(401, "authentication_required", "관리자 로그인이 필요합니다.",
                       {"WWW-Authenticate": "Bearer"})
