"""공식 규정 사이트 HTTP 클라이언트. 공개 페이지만, 쿠키 주입·TLS 검증 해제 없이 요청한다.

재시도·체크포인트·전체 수집 일정은 #17에서 이 클라이언트 위에 구현한다.
"""
import time
from urllib.parse import urlsplit

import requests

from .pages import BASE_URL, OFFICIAL_HOST, PageError

USER_AGENT = "HS-RULE-crawler/1.0 (+https://hs.officeworkkit.com)"
LAW_PATH = "/lmxsrv/law"


class FetchError(RuntimeError):
    """네트워크 오류나 200이 아닌 응답. 재시도 여부는 호출 측(#17)이 정한다."""


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
        url = f"{BASE_URL}{LAW_PATH}/{page}"
        self._wait_turn()
        try:
            # verify는 넘기지 않는다(requests 기본값: 인증서 검증). 쿠키도 주입하지 않는다.
            response = self.session.get(url, params=params, timeout=self.timeout, allow_redirects=True)
        except requests.RequestException as exc:
            raise FetchError(f"{page} 요청 실패: {type(exc).__name__}") from None
        final = urlsplit(response.url)
        if final.scheme != "https" or final.hostname != OFFICIAL_HOST:
            raise PageError("foreign_redirect", f"공식 호스트가 아닌 주소로 이동: {final.hostname}")
        if "errorPage" in final.path:
            raise PageError("error_page", f"{page}가 오류 페이지로 이동")
        if response.status_code != 200:
            raise FetchError(f"{page} 응답 {response.status_code}")
        response.encoding = response.encoding or "utf-8"
        return response.text

    def catalog_html(self) -> str:
        return self.get("lawTree.do", {"LAWGROUP": 1, "MODE": "srvlist"})

    def detail_html(self, seq: int) -> str:
        return self.get("lawDetail.do", {"SEQ": seq})

    def content_html(self, seq: int, history: int) -> str:
        return self.get("lawFullContent.do", {"SEQ": seq, "SEQ_HISTORY": history})
