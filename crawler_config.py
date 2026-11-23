"""크롤러 공통 세션 설정. 실제 쿠키는 로컬 .env에서만 읽습니다."""

import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"))


def load_cookies() -> dict[str, str]:
    try:
        cookies = json.loads(os.getenv("HANSUNG_COOKIES_JSON", "{}"))
    except json.JSONDecodeError:
        raise ValueError("HANSUNG_COOKIES_JSON은 문자열 값으로 구성된 JSON 객체여야 합니다.") from None
    if not isinstance(cookies, dict) or not all(isinstance(value, str) for value in cookies.values()):
        raise ValueError("HANSUNG_COOKIES_JSON은 문자열 값으로 구성된 JSON 객체여야 합니다.")
    return cookies
