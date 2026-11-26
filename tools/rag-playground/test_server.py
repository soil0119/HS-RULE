"""Run: python3 -m unittest discover -s tools/rag-playground -v"""
import json
import threading
import unittest
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from playground import make_server


class Upstream(BaseHTTPRequestHandler):
    calls = []
    status = 200
    reply = {"answer": "학칙 제33조를 확인하세요.", "found": True, "sources": []}
    redirect = False
    raw = None

    def do_POST(self):
        self.calls.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
        self.respond()

    def do_GET(self):
        self.calls.append((self.path, None))
        self.respond()

    def respond(self):
        self.send_response(302 if self.redirect else self.status)
        if self.redirect:
            self.send_header("Location", "/private")
        self.send_header("X-Request-ID", "upstream-trace")
        self.end_headers()
        self.wfile.write(self.raw if self.raw is not None else json.dumps(self.reply, ensure_ascii=False).encode())

    def log_message(self, *args):
        pass


class PlaygroundTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        cls.gateway = make_server(("127.0.0.1", 0), f"http://127.0.0.1:{cls.upstream.server_port}")
        for server in (cls.upstream, cls.gateway):
            threading.Thread(target=server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        for server in (cls.gateway, cls.upstream):
            server.shutdown()
            server.server_close()

    def setUp(self):
        Upstream.calls.clear()
        Upstream.status = 200
        Upstream.redirect = False
        Upstream.raw = None
        Upstream.reply = {"answer": "학칙 제33조를 확인하세요.", "found": True, "sources": []}

    def request(self, method, path, data=None, headers=None):
        conn = HTTPConnection("127.0.0.1", self.gateway.server_port, timeout=4)
        body = json.dumps(data, ensure_ascii=False).encode() if data is not None else None
        request_headers = {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{self.gateway.server_port}"}
        request_headers.update(headers or {})
        conn.request(method, path, body, request_headers)
        response = conn.getresponse()
        raw = response.read()
        result = (response.status, json.loads(raw))
        conn.close()
        return result

    def test_query_forwards_exact_question_history_and_language(self):
        data = {"question": "휴학은?", "messages": [{"role": "user", "content": "장학금"}], "language": "en"}
        status, result = self.request("POST", "/api/query", data)
        self.assertEqual((status, result["status"]), (200, 200))
        self.assertEqual(Upstream.calls, [("/query", data)])
        self.assertEqual(result["body"]["answer"], Upstream.reply["answer"])
        self.assertEqual(result["request_id"], "upstream-trace")
        self.assertGreaterEqual(result["elapsed_ms"], 0)

    def test_health_reports_liveness_and_readiness_separately(self):
        status, result = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(set(result), {"live", "ready"})
        self.assertEqual([c[0] for c in Upstream.calls], ["/health/live", "/health"])

    def test_api_errors_keep_original_status_and_json(self):
        for code in (429, 502, 503):
            with self.subTest(code=code):
                Upstream.status = code
                Upstream.reply = {"code": "rag_not_connected", "request_id": "trace-123"}
                status, result = self.request("POST", "/api/query", {"question": "test", "messages": [], "language": "ko"})
                self.assertEqual((status, result["status"]), (200, code))
                self.assertEqual(result["body"], Upstream.reply)

    def test_cross_origin_and_arbitrary_routes_never_call_upstream(self):
        status, _ = self.request("POST", "/api/query", {"question": "test"}, {"Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        status, _ = self.request("GET", "/api/proxy?url=http://internal")
        self.assertEqual(status, 404)
        status, _ = self.request("GET", "/api/config", headers={"Host": "evil.example"})
        self.assertEqual(status, 403)
        self.assertEqual(Upstream.calls, [])

    def test_invalid_request_and_unsupported_tuning_never_call_upstream(self):
        for data in ({"question": " "}, {"question": "x" * 2001}, {"question": "ok", "threshold": 0.7},
                     {"question": "ok", "messages": [{"role": "system", "content": "no"}]},
                     {"question": "ok", "messages": [{"role": "user", "content": "x" * 4001}]},
                     {"question": "ok", "language": "ja"}):
            status, _ = self.request("POST", "/api/query", data)
            self.assertEqual(status, 400)
        self.assertEqual(Upstream.calls, [])

    def test_redirects_are_not_followed(self):
        Upstream.redirect = True
        _, result = self.request("POST", "/api/query", {"question": "ok"})
        self.assertEqual(result["status"], 302)
        self.assertEqual([c[0] for c in Upstream.calls], ["/query"])

    def test_non_json_and_oversized_responses_are_visible_errors(self):
        Upstream.raw = b"<html>Wrong API target</html>"
        _, result = self.request("POST", "/api/query", {"question": "ok"})
        self.assertEqual(result["body"]["code"], "non_json_response")
        self.assertEqual(result["body"]["raw"], Upstream.raw.decode())
        Upstream.raw = b"x" * (2 * 1024 * 1024 + 1)
        _, result = self.request("POST", "/api/query", {"question": "ok"})
        self.assertEqual(result["body"]["code"], "response_too_large")

    def test_oversized_request_and_malformed_json_do_not_reach_upstream(self):
        status, _ = self.request("POST", "/api/query", {"question": "x" * (256 * 1024)})
        self.assertEqual(status, 413)
        conn = HTTPConnection("127.0.0.1", self.gateway.server_port, timeout=4)
        conn.request("POST", "/api/query", b'{', {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{self.gateway.server_port}"})
        self.assertEqual(conn.getresponse().status, 400)
        conn.close()
        self.assertEqual(Upstream.calls, [])

    def test_target_rejects_embedded_credentials_and_non_http(self):
        for url in ("file:///etc/passwd", "https://user:secret@example.com", "http://example.com/?key=secret"):
            with self.assertRaises(ValueError):
                make_server(("127.0.0.1", 0), url)


if __name__ == "__main__":
    unittest.main()
