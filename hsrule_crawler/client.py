"""공식 규정 사이트 HTTP 클라이언트. 공개 페이지만, 쿠키 주입·TLS 검증 해제 없이 요청한다.

재시도·체크포인트·전체 수집 일정은 #17에서 이 클라이언트 위에 구현한다.
"""
import time
from urllib.parse import urljoin, urlsplit

import requests

from .pages import BASE_URL, OFFICIAL_HOST, PageError

USER_AGENT = "HS-RULE-crawler/1.0 (+https://hs.officeworkkit.com)"
LAW_PATH = "/lmxsrv/law"
MAX_REDIRECTS = 3
REDIRECT_STATUSES = {301, 302, 303, 307, 308}


class FetchError(RuntimeError):
    """네트워크 오류나 200이 아닌 응답. 재시도 여부는 호출 측(#17)이 정한다."""


def _check_official(url: str, page: str) -> None:
    """https://rule.hansung.ac.kr(기본 포트)만 허용한다. 오류 페이지는 요청하지 않고 거부한다."""
    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.hostname != OFFICIAL_HOST or parts.port not in (None, 443)
            or parts.username is not None or parts.password is not None):
        raise PageError("foreign_redirect", f"공식 호스트가 아닌 주소로 이동: {parts.scheme}://{parts.hostname}")
    if "errorPage" in parts.path:
        raise PageError("error_page", f"{page}가 오류 페이지로 이동")


class OfficialClient:
    def __init__(self, session=None, *, timeout=(5, 20), min_interval: float = 1.0,
                 clock=time.monotonic, sleep=time.sleep):
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "ko-KR,ko;q=0.9"})
        self.timeout = timeout
        self.min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._last = None

    def _wait_turn(self):
        if self._last is not None:
            remaining = self.min_interval - (self._clock() - self._last)
            if remaining > 0:
                self._sleep(remaining)
        self._last = self._clock()

    def get(self, page: str, params: dict) -> str:
        """공식 페이지를 받는다. 리다이렉트는 자동으로 따르지 않고, 다음 주소를 요청 전에 검사한다."""
        url, query, visited = f"{BASE_URL}{LAW_PATH}/{page}", params, set()
        for _ in range(MAX_REDIRECTS + 1):
            self._wait_turn()
            try:
                # verify는 넘기지 않는다(requests 기본값: 인증서 검증). 쿠키도 주입하지 않는다.
                response = self.session.get(url, params=query, timeout=self.timeout, allow_redirects=False)
            except requests.RequestException as exc:
                raise FetchError(f"{page} 요청 실패: {type(exc).__name__}") from None
            current = response.url
            _check_official(current, page)
            if response.status_code not in REDIRECT_STATUSES:
                if response.status_code != 200:
                    raise FetchError(f"{page} 응답 {response.status_code}")
                response.encoding = response.encoding or "utf-8"
                return response.text
            location = response.headers.get("Location")
            if not location:
                raise PageError("bad_redirect", f"{page} 응답 {response.status_code}에 Location이 없음")
            visited.add(current)
            url, query = urljoin(current, location), None
            _check_official(url, page)  # 다음 요청을 보내기 전에 막는다
            if url in visited:
                raise PageError("redirect_loop", f"{page} 리다이렉트가 반복됨")
        raise PageError("redirect_loop", f"{page} 리다이렉트가 {MAX_REDIRECTS}회를 넘음")

    def catalog_html(self) -> str:
        return self.get("lawTree.do", {"LAWGROUP": 1, "MODE": "srvlist"})

    def detail_html(self, seq: int) -> str:
        return self.get("lawDetail.do", {"SEQ": seq})

    def content_html(self, seq: int, history: int) -> str:
        return self.get("lawFullContent.do", {"SEQ": seq, "SEQ_HISTORY": history})
