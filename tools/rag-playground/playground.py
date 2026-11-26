"""Local-only HS-RULE API playground. No model keys or application dependencies."""
import json
import os
from pathlib import Path
import re
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import BoundedSemaphore
from time import perf_counter
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

MAX_BODY = 256 * 1024
MAX_RESPONSE = 2 * 1024 * 1024
INVALID_TEXT = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")
STATIC = Path(__file__).parent / "static"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def valid_base_url(url):
    parsed = urlsplit(url)
    if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment or parsed.port == 0
            or any(c.isspace() or ord(c) < 32 for c in url)):
        raise ValueError("API URL must be http(s), without credentials, query, or fragment")
    return parsed


def validate_query(data):
    def text(value, limit):
        return isinstance(value, str) and 0 < len(value) <= limit and value.strip() and not INVALID_TEXT.search(value)

    if not isinstance(data, dict) or set(data) - {"question", "messages", "language"}:
        raise ValueError("허용 항목: question, messages, language. 튜닝 항목은 현재 API가 지원하지 않습니다.")
    if not text(data.get("question"), 2000) or data.get("language", "ko") not in ("ko", "en"):
        raise ValueError("질문은 1~2,000자, 언어는 ko/en이어야 합니다.")
    messages = data.get("messages", [])
    if not isinstance(messages, list) or len(messages) > 12:
        raise ValueError("대화 이력은 최대 12개입니다.")
    for message in messages:
        if (not isinstance(message, dict) or set(message) != {"role", "content"}
                or message["role"] not in ("user", "assistant") or not text(message["content"], 4000)):
            raise ValueError("이력은 user/assistant, 본문 1~4,000자 형식이어야 합니다.")
    return data


def make_server(address, api_url, browser_api_url=None):
    target = valid_base_url(api_url)
    api_url = api_url.rstrip("/")
    # Docker reaches the host via its special DNS name; the API admits localhost.
    docker_host = target.hostname == "host.docker.internal"
    local_authority = "localhost" + (f":{target.port}" if target.port else "")
    public_url = browser_api_url or urlunsplit(target._replace(netloc=local_authority if docker_host else target.netloc))
    valid_base_url(public_url)
    opener = build_opener(ProxyHandler({}), NoRedirect())
    slots = BoundedSemaphore(4)

    def call_api(path, data=None):
        started = perf_counter()
        if not slots.acquire(blocking=False):
            return {"status": None, "elapsed_ms": 0, "request_id": None,
                    "body": {"code": "playground_busy", "detail": "동시 요청 상한은 4개입니다."}}
        try:
            headers = {"Accept": "application/json", "Content-Type": "application/json"}
            if docker_host:
                headers["Host"] = local_authority
            request = Request(api_url + path, data=json.dumps(data, ensure_ascii=False).encode() if data is not None else None,
                              headers=headers)
            try:
                response = opener.open(request, timeout=125 if data is not None else 5)
            except HTTPError as error:
                response = error
            with response:
                raw = response.read(MAX_RESPONSE + 1)
                if len(raw) > MAX_RESPONSE:
                    body = {"code": "response_too_large", "detail": "응답이 2 MiB 제한을 초과했습니다."}
                else:
                    try:
                        body = json.loads(raw)
                    except (ValueError, UnicodeError):
                        body = {"code": "non_json_response", "detail": "API가 JSON을 반환하지 않았습니다.",
                                "raw": raw.decode("utf-8", errors="replace")}
                return {"status": response.status, "elapsed_ms": round((perf_counter() - started) * 1000),
                        "request_id": response.headers.get("X-Request-ID"), "body": body}
        except (URLError, OSError, ValueError) as error:
            timed_out = isinstance(error, (TimeoutError, socket.timeout)) or isinstance(getattr(error, "reason", None), TimeoutError)
            return {"status": None, "elapsed_ms": round((perf_counter() - started) * 1000), "request_id": None,
                    "body": {"code": "upstream_timeout" if timed_out else "upstream_unreachable",
                             "detail": "API 응답 시간이 초과됐습니다." if timed_out else "API에 연결할 수 없습니다. 실행 상태와 주소를 확인하세요."}}
        finally:
            slots.release()

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def send(self, status, body, content_type="application/json; charset=utf-8"):
            raw = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(raw)

        def admitted(self, post=False):
            authority = self.headers.get("Host", "")
            try:
                host = urlsplit("//" + authority)
                valid = (host.hostname in ("localhost", "127.0.0.1", "::1") and host.netloc == authority
                         and host.username is None and host.port != 0)
            except ValueError:
                valid = False
            if not valid:
                self.send(403, {"error": "로컬 주소로 접속하세요."})
                return False
            if post and (self.headers.get("Origin") != f"http://{authority}"
                         or self.headers.get("Sec-Fetch-Site") == "cross-site"):
                self.send(403, {"error": "같은 출처의 테스트 화면에서 요청하세요."})
                return False
            return True

        def do_GET(self):
            if not self.admitted():
                return
            if self.path == "/healthz":
                self.send(200, {"ok": True})
            elif self.path == "/api/config":
                self.send(200, {"api_url": api_url, "browser_api_url": public_url.rstrip("/")})
            elif self.path == "/api/health":
                self.send(200, {"live": call_api("/health/live"), "ready": call_api("/health")})
            elif self.path in ("/", "/index.html", "/app.js", "/styles.css"):
                name = "index.html" if self.path == "/" else self.path[1:]
                content_type = {"index.html": "text/html; charset=utf-8", "app.js": "text/javascript; charset=utf-8",
                                "styles.css": "text/css; charset=utf-8"}[name]
                self.send(200, (STATIC / name).read_bytes(), content_type)
            else:
                self.send(404, {"error": "지원하지 않는 경로입니다."})

        def do_POST(self):
            if not self.admitted(post=True):
                return
            if self.path != "/api/query":
                self.send(404, {"error": "지원하지 않는 경로입니다."})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_BODY:
                    self.send(413, {"error": "본문 크기는 1~256 KiB이어야 합니다."})
                    return
                if (self.headers.get_content_type() != "application/json" or self.headers.get("Transfer-Encoding")):
                    self.send(400, {"error": "Content-Length가 있는 JSON 요청만 지원합니다."})
                    return
                data = validate_query(json.loads(self.rfile.read(length)))
            except (ValueError, UnicodeError, RecursionError):
                self.send(400, {"error": "질문·언어·대화 이력 JSON 형식을 확인하세요. 미지원 튜닝 항목은 전달할 수 없습니다."})
                return
            self.send(200, call_api("/query", data))

        def log_message(self, *args):
            pass  # Questions, history, and upstream responses stay out of logs.

    return ThreadingHTTPServer(address, Handler)


if __name__ == "__main__":
    server = make_server(("0.0.0.0", int(os.environ.get("PORT", "8080"))),
                         os.environ.get("HSRULE_API_URL", "http://host.docker.internal:8080"),
                         os.environ.get("HSRULE_BROWSER_API_URL") or None)
    print("HS-RULE API Playground ready on port", server.server_port, flush=True)
    server.serve_forever()
