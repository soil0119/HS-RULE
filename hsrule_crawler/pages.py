"""공식 사이트 응답 공통 검사. 사이트는 오류·로그인 화면도 HTTP 200으로 돌려준다."""
import re

from bs4 import BeautifulSoup

OFFICIAL_HOST = "rule.hansung.ac.kr"
BASE_URL = f"https://{OFFICIAL_HOST}"

_ERROR_REDIRECT = re.compile(r"""action\s*=\s*["'][^"']*errorPage\.do""", re.IGNORECASE)
_PASSWORD_INPUT = re.compile(r"""<input[^>]+type\s*=\s*["']?password""", re.IGNORECASE)


class PageError(ValueError):
    """응답이 기대한 공식 페이지가 아니다. reason은 실패 보고에 쓰는 짧은 코드다."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


def normalize_space(value: str) -> str:
    """NBSP·탭·연속 공백을 공백 하나로 줄이고 양끝을 자른다."""
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def service_soup(html: str) -> BeautifulSoup:
    """오류 리다이렉트·로그인 화면을 거부하고 파싱 결과를 돌려준다."""
    if _ERROR_REDIRECT.search(html):
        raise PageError("error_page", "errorPage.do로 이동하는 200 응답")
    if _PASSWORD_INPUT.search(html):
        raise PageError("login_page", "비밀번호 입력이 있는 로그인 화면")
    return BeautifulSoup(html, "lxml")
