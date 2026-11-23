import asyncio
import io
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from pathlib import Path

from docx import Document
from pdfminer.high_level import extract_text

from test_web_auth import PASSWORD, configured_client
from test_web_documents import make_pdf

PUBLIC = "a" * 32
PRIVATE = "b" * 32
MISSING = "c" * 32


class FixtureStore:
    def __init__(self, directory):
        from hsrule_web.documents import DocumentMetadata, SourceDocument

        self.directory = Path(directory)
        self.documents = {}
        for document_id, visibility, title in ((PUBLIC, "public", "Official title"),
                                                (PRIVATE, "private", "Private title")):
            self.documents[document_id] = SourceDocument(metadata=DocumentMetadata(
                id=document_id, title=title, filename="original.pdf", visibility=visibility,
                content_hash="d" * 64, page_count=2, warnings=[],
            ), pages=[])
            self.file_path(document_id).write_bytes(make_pdf("first", "second"))

    def health(self):
        return True

    def list(self, *, include_private=False):
        return [item.metadata for item in self.documents.values()
                if include_private or item.metadata.visibility == "public"]

    def get(self, document_id, *, include_private=False, include_pages=False):
        result = self.documents.get(document_id)
        if result and (include_private or result.metadata.visibility == "public"):
            return result
        return None

    def file_path(self, document_id):
        return self.directory / (document_id + ".pdf")


class RagFixture:
    def __init__(self):
        self.answer_input = self.conflict_input = None

    def answer(self, question, messages, include_private=False, *, language="ko"):
        self.answer_input = (question, messages, include_private, language)
        return {
            "answer": "일반 답변", "found": True, "followups": ["관련 질문"],
            "sources": [
                {"document_id": PUBLIC, "page_start": 1, "page_end": 2, "score": 0.9,
                 "title": "Untrusted title", "url": "https://example.invalid/"},
            ],
        }

    def analyze_conflict(self, text, filename, include_private=True, *, language="ko"):
        self.conflict_input = (text, filename, include_private, language)
        return {
            "summary": "요약", "conflicts": [
                {"regulation": "규정", "article": "제1조", "issue": "충돌", "severity": "높음"}
            ], "recommendations": "개선", "filename": "ignored",
        }


class WebAPITests(unittest.TestCase):
    def setUp(self):
        self.store = FixtureStore(self.enterContext(tempfile.TemporaryDirectory()))
        self.rag = RagFixture()
        self.client = configured_client(document_store=self.store, rag_backend=self.rag)
        self.auth = self.auth_header(self.client)

    def auth_header(self, client):
        result = client.post("/login", json={"username": "test-admin", "password": PASSWORD})
        self.assertEqual(result.status_code, 200, result.text)
        return {"Authorization": "Bearer " + result.json()["token"]}

    def assert_error(self, response, status, code=None):
        self.assertEqual(response.status_code, status, response.text)
        error = response.json()
        self.assertIsInstance(error["detail"], str)
        self.assertEqual(error["request_id"], response.headers["X-Request-ID"])
        self.assertEqual(set(error), {"code", "detail", "request_id"})
        if code:
            self.assertEqual(error["code"], code)

    def test_public_list_metadata_file_and_private_admin_access(self):
        listing = self.client.get("/rules").json()
        self.assertEqual(listing["total"], 1)
        self.assertEqual(listing["chapters"][0]["rules"][0]["id"], PUBLIC)
        self.assertEqual(len(self.client.get("/uploaded-rules", headers=self.auth).json()["rules"]), 2)

        for path in (f"/documents/{PRIVATE}", f"/documents/{PRIVATE}/file"):
            self.assert_error(self.client.get(path), 404, "document_not_found")
            self.assertEqual(self.client.get(path, headers=self.auth).status_code, 200)
        self.assertEqual(self.client.get(f"/documents/{PUBLIC}").json()["title"], "Official title")
        response = self.client.get(f"/documents/{PUBLIC}/file", headers={"Range": "bytes=0-4"})
        self.assertEqual(response.status_code, 206, response.text)
        self.assertEqual(response.content, b"%PDF-")
        multiple = self.client.get(f"/documents/{PUBLIC}/file", headers={"Range": "bytes=0-4,8-9"})
        self.assertEqual(multiple.status_code, 206, multiple.text)
        self.assertIn("multipart/byteranges", multiple.headers["content-type"])
        self.assertIn(b"%PDF-", multiple.content)
        invalid_range = self.client.get(f"/documents/{PUBLIC}/file", headers={"Range": "bytes=999999-"})
        self.assert_error(invalid_range, 416, "invalid_range")
        self.assertTrue(invalid_range.headers["content-range"].startswith("bytes */"))
        self.assertIn("inline", response.headers["Content-Disposition"])
        self.assert_error(self.client.get(f"/documents/{MISSING}/file"), 404, "document_not_found")
        self.store.file_path(PUBLIC).unlink()
        self.assert_error(self.client.get(f"/documents/{PUBLIC}/file"), 404, "document_not_found")

    def test_admin_guards_prevent_unauthorized_upload_delete_and_conflict(self):
        file = {"file": ("x.pdf", make_pdf("text"), "application/pdf")}
        self.assert_error(self.client.post("/upload-regulation", files=file), 401)
        self.assert_error(self.client.delete(f"/uploaded-rules/{PUBLIC}"), 401)
        self.assert_error(self.client.get("/uploaded-rules"), 401)
        self.assert_error(self.client.post("/conflict/", files=file), 401)

    def test_healthy_dependencies_and_missing_rag_are_distinct(self):
        self.assertEqual(self.client.get("/health").json(), {"ok": True})
        client = configured_client(document_store=self.store)
        self.assert_error(client.get("/health"), 503, "not_ready")
        self.assert_error(client.post("/query", json={"question": "질문"}), 503, "rag_not_connected")
        self.assert_error(client.post("/conflict/", headers=self.auth_header(client),
                                      files={"file": ("broken.pdf", b"not a PDF")}),
                          503, "rag_not_connected")

    def test_query_preserves_prior_assistant_and_sanitizes_sources(self):
        previous = [{"role": "user", "content": "초기 질문"},
                    {"role": "assistant", "content": "이전 답변"}]
        response = self.client.post("/query", headers=self.auth,
                                    json={"question": "후속 질문", "messages": previous})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.rag.answer_input, ("후속 질문", previous, False, "ko"))
        result = response.json()
        self.assertTrue(result["found"])
        self.assertEqual(result["followups"], ["관련 질문"])
        self.assertEqual(result["sources"], [
            {"document_id": PUBLIC, "page_start": 1, "page_end": 2, "score": 0.9,
             "title": "Official title", "url": f"/documents/{PUBLIC}/file#page=1"},
        ])

    def test_validated_language_is_passed_to_both_rag_methods(self):
        response = self.client.post("/query", json={"question": "English question", "language": "en"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.rag.answer_input, ("English question", [], False, "en"))
        response = self.client.post("/conflict/", headers=self.auth, data={"language": "en"},
                                    files={"file": ("x.txt", "English draft")})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.rag.conflict_input, ("English draft", "x.txt", True, "en"))
        self.assert_error(self.client.post("/query", json={"question": "x", "language": "fr"}),
                          422, "invalid_input")
        self.assert_error(self.client.post("/conflict/", headers=self.auth, data={"language": "fr"},
                                           files={"file": ("x.txt", "Text")}), 422, "invalid_input")

    def test_invalid_query_and_export_payloads_are_422(self):
        for data in (
            {"question": " "}, {"question": "x" * 2001},
            {"question": "x", "messages": [{"role": "system", "content": "test"}]},
            {"question": "x", "messages": [{"role": "assistant"}]},
            {"question": "x", "messages": [{"role": "assistant", "content": "x" * 4001}]},
            {"question": "x", "messages": [{"role": "user", "content": "x"}] * 13},
        ):
            with self.subTest(data=str(data)[:100]):
                self.assert_error(self.client.post("/query", json=data), 422, "invalid_input")
        for fmt in ("pdf", "docx"):
            self.assert_error(self.client.post(f"/export/{fmt}", json={"content": "x", "sources": [{}]}),
                              422, "invalid_input")
            self.assert_error(self.client.post(f"/export/{fmt}", json={"content": "x\u0000y"}),
                              422, "invalid_input")

    def test_export_literal_markup_to_real_pdf_and_docx(self):
        source = self.client.post("/query", json={"question": "x"}).json()["sources"][0]
        document = {"content": "plain <tag> & text", "title": "한성대학교 규정",
                    "question": "a < b", "sources": [source]}
        pdf = self.client.post("/export/pdf", json=document)
        self.assertEqual(pdf.status_code, 200, pdf.text)
        exported_pdf = extract_text(io.BytesIO(pdf.content))
        self.assertIn("plain <tag> & text", exported_pdf)
        self.assertIn("한성대학교 규정", exported_pdf)
        docx = self.client.post("/export/docx", json=document)
        self.assertEqual(docx.status_code, 200, docx.text)
        exported_docx = "\n".join(p.text for p in Document(io.BytesIO(docx.content)).paragraphs)
        self.assertIn("plain <tag> & text", exported_docx)
        self.assertIn("한성대학교 규정", exported_docx)

    def test_conflict_passes_real_extracted_text_to_injected_adapter(self):
        response = self.client.post("/conflict/", headers=self.auth,
                                    files={"file": ("../real.txt", "검증 문서")})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["filename"], "real.txt")
        self.assertEqual(self.rag.conflict_input, ("검증 문서", "real.txt", True, "ko"))
        for filename, data, status, code in (
            ("x.exe", b"data", 415, "unsupported_file_type"),
            ("x.docx", b"not a zip", 422, "invalid_document"),
            ("x.json", b"broken json", 422, "invalid_document"),
            ("x.txt", b"\x00binary", 422, "invalid_document"),
        ):
            with self.subTest(filename=filename):
                self.assert_error(self.client.post("/conflict/", headers=self.auth,
                                                   files={"file": (filename, data)}), status, code)

    def test_backend_failure_does_not_expose_exception_or_model_payload(self):
        class FailingRag:
            def answer(self, question, messages, include_private=False, *, language="ko"):
                raise RuntimeError("sensitive backend exception")

        client = configured_client(document_store=self.store, rag_backend=FailingRag())
        response = client.post("/query", json={"question": "question"})
        self.assert_error(response, 502, "rag_backend_error")
        self.assertNotIn("sensitive", response.text)

    def test_query_rejects_entire_output_with_disallowed_sources(self):
        payload = RagFixture().answer("question", [])
        public = payload["sources"][0]
        for document_id, page_start, page_end, has_public, found in (
            (PRIVATE, 1, 1, True, True), (MISSING, 1, 1, True, True),
            (PRIVATE, 1, 1, False, False), (MISSING, 1, 1, False, False),
            (PUBLIC, 0, 1, True, True), (PUBLIC, 2, 1, True, True), (PUBLIC, 3, 3, True, True),
        ):
            with self.subTest(document_id=document_id, page_start=page_start, page_end=page_end,
                              has_public=has_public, found=found):
                class MixedRag:
                    def answer(self, question, messages, include_private=False, *, language="ko"):
                        invalid = {"document_id": document_id, "page_start": page_start, "page_end": page_end}
                        return {"answer": "sensitive answer", "found": found,
                                "followups": ["sensitive followup"],
                                "sources": [public, invalid] if has_public else [invalid]}

                client = configured_client(document_store=self.store, rag_backend=MixedRag())
                response = client.post("/query", json={"question": "question"})
                self.assert_error(response, 502, "rag_invalid_response")
                self.assertNotIn("sensitive", response.text)

    def test_expensive_routes_have_a_shared_in_flight_ceiling(self):
        started = Barrier(9)
        release = Event()

        class BlockingRag:
            def answer(self, question, messages, include_private=False, *, language="ko"):
                started.wait(timeout=10)
                release.wait(timeout=10)
                return {"answer": "No matches", "found": False, "followups": [], "sources": []}

        client = configured_client(document_store=self.store, rag_backend=BlockingRag())
        with ThreadPoolExecutor(max_workers=8) as executor:
            pending = [executor.submit(client.post, "/query", json={"question": "x"}) for _ in range(8)]
            try:
                started.wait(timeout=10)
                response = client.post("/query", json={"question": "one more"},
                                       headers={"Origin": "http://localhost:5173"})
                self.assert_error(response, 429, "request_limit")
                self.assertEqual(response.headers["access-control-allow-origin"], "http://localhost:5173")
                self.assertEqual(client.get("/health/live").status_code, 200)
            finally:
                release.set()
            self.assertEqual([future.result().status_code for future in pending], [200] * 8)

    def test_multipart_and_chunked_oversize_are_bounded(self):
        body = b"x" * (21 * 1024 * 1024)
        response = self.client.post("/upload-regulation", headers=self.auth,
                                    files={"file": ("huge.pdf", body)})
        self.assert_error(response, 413)
        response = self.client.post("/query", headers={"Content-Type": "application/json"},
                                    content=iter([b"x" * 600000, b"x" * 600000]))
        self.assert_error(response, 413)

class WebStreamingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from hsrule_web.settings import Settings
        from server import create_app

        self.store = FixtureStore(self.enterContext(tempfile.TemporaryDirectory()))
        self.app = create_app(Settings(allowed_hosts=("testserver",)), document_store=self.store)

    async def get(self, path, on_start=None, *, extensions=None):
        scope = {
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1", "method": "GET", "scheme": "http", "path": path,
            "query_string": b"", "headers": [(b"host", b"testserver")],
            "server": ("testserver", 80), "client": ("127.0.0.1", 1234),
            "extensions": extensions or {},
        }
        messages = []

        async def receive():
            await asyncio.Future()

        async def send(message):
            messages.append(message)
            if message["type"] == "http.response.start" and on_start is not None:
                await on_start()

        await self.app(scope, receive, send)
        return messages

    async def test_expensive_request_slots_remain_reserved_until_body_finishes(self):
        all_started = asyncio.Event()
        finish = asyncio.Event()
        count = 0

        async def on_start():
            nonlocal count
            count += 1
            if count == 8:
                all_started.set()
            await finish.wait()

        requests = [asyncio.create_task(self.get(f"/documents/{PUBLIC}/file", on_start)) for _ in range(8)]
        try:
            await asyncio.wait_for(all_started.wait(), timeout=5)
            self.assertEqual((await self.get("/rules"))[0]["status"], 429)
            self.assertEqual((await self.get("/health/live"))[0]["status"], 200)
        finally:
            finish.set()
            results = await asyncio.gather(*requests)
        self.assertEqual([result[0]["status"] for result in results], [200] * 8)
        self.assertEqual((await self.get("/rules"))[0]["status"], 200)

    @unittest.skipIf(sys.platform == "win32", "Windows prevents unlink while the PDF is open")
    async def test_open_pdf_remains_readable_when_catalog_file_is_removed_after_response_starts(self):
        source = self.store.file_path(PUBLIC)
        original = source.read_bytes()

        async def remove_original():
            source.unlink()

        messages = await self.get(f"/documents/{PUBLIC}/file", remove_original,
                                  extensions={"http.response.pathsend": {}})
        self.assertEqual(messages[0]["status"], 200)
        self.assertFalse(source.exists())
        self.assertEqual(b"".join(msg["body"] for msg in messages if msg["type"] == "http.response.body"),
                         original)
