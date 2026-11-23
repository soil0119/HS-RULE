import io
import os
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch

import psycopg2
from psycopg2 import sql
from psycopg2.extensions import make_dsn
from reportlab.pdfgen.canvas import Canvas


def make_pdf(*pages):
    output = io.BytesIO()
    canvas = Canvas(output, invariant=True)
    for text in pages:
        if text:
            canvas.drawString(40, 700, text)
        canvas.showPage()
    canvas.save()
    return output.getvalue()


class PdfTests(unittest.TestCase):
    def test_real_pages_preserve_number_text_and_blank_page_warning(self):
        from hsrule_web.documents import parse_pdf

        pages, warnings = parse_pdf(make_pdf("First source page", "", "Third source page"))
        self.assertEqual([page.page_number for page in pages], [1, 2, 3])
        self.assertIn("First source page", pages[0].text)
        self.assertEqual(pages[1].text, "")
        self.assertIn("Third source page", pages[2].text)
        self.assertEqual(len(warnings), 1)
        self.assertIn("2", warnings[0])

    def test_parser_distinguishes_broken_empty_oversize_and_too_many_pages(self):
        from hsrule_web.documents import parse_pdf
        from hsrule_web.errors import APIError

        for data, status, code in (
            (b"%PDF-1.4\nbroken\n%%EOF", 422, "invalid_pdf"),
            (make_pdf("", ""), 422, "no_pdf_text"),
            (b"0" * (20 * 1024 * 1024 + 1), 413, "file_too_large"),
            (make_pdf("text", *([""] * 1000)), 422, "too_many_pdf_pages"),
        ):
            with self.subTest(code=code), self.assertRaises(APIError) as caught:
                parse_pdf(data)
            self.assertEqual((caught.exception.status_code, caught.exception.code), (status, code))


@unittest.skipUnless(os.getenv("HSRULE_WEB_TEST_DSN"), "isolated PostgreSQL DSN not provided")
class PostgresDocumentTests(unittest.TestCase):
    def setUp(self):
        from hsrule_web.documents import DocumentStore

        dsn = os.environ["HSRULE_WEB_TEST_DSN"]
        schema = "web_test_" + uuid4().hex
        with closing(psycopg2.connect(dsn)) as conn, conn, conn.cursor() as cur:
            cur.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))

        def drop_schema():
            with closing(psycopg2.connect(dsn)) as conn, conn, conn.cursor() as cur:
                cur.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))

        self.addCleanup(drop_schema)
        self.dsn = make_dsn(dsn, options=f"-c search_path={schema}")
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.upload_dir = Path(self.temp) / "uploads"
        self.store = DocumentStore(self.dsn, self.upload_dir)
        self.assertFalse(self.store.health())
        self.store.initialize()
        self.store.initialize()
        self.assertTrue(self.store.health())

    def execute(self, statement):
        with closing(psycopg2.connect(self.dsn)) as conn, conn, conn.cursor() as cur:
            cur.execute(statement)

    def test_visibility_original_path_and_real_page_persistence(self):
        private = self.store.save(make_pdf("Hidden"), "../../outside.pdf")
        data = make_pdf("Visible", "", "Visible end")
        public = self.store.save(data, r"C:\\folder\\public.pdf", title="Public title", visibility="public")

        self.assertEqual(private.filename, "outside.pdf")
        self.assertFalse((Path(self.temp) / "outside.pdf").exists())
        self.assertRegex(private.id, r"^[0-9a-f]{32}$")
        self.assertEqual(self.store.file_path(public.id).read_bytes(), data)
        self.assertEqual(self.store.get(public.id).metadata.title, "Public title")
        document = self.store.get(public.id, include_pages=True)
        self.assertEqual([page.page_number for page in document.pages], [1, 2, 3])
        self.assertIn("Visible end", document.pages[-1].text)
        self.assertIsNone(self.store.get(private.id))
        self.assertEqual(self.store.get(private.id, include_private=True).metadata.id, private.id)
        self.assertEqual([item.id for item in self.store.list()], [public.id])
        self.assertEqual({item.id for item in self.store.list(include_private=True)}, {public.id, private.id})

        self.assertTrue(self.store.delete(public.id))
        self.assertFalse(self.store.delete(public.id))
        self.assertFalse(self.store.file_path(public.id).exists())
        self.assertIsNone(self.store.get(public.id))

    def test_duplicate_and_page_insert_failure_leave_no_new_files_or_rows(self):
        from hsrule_web.errors import APIError

        data = make_pdf("Unique bytes")
        original = self.store.save(data, "original.pdf")
        with self.assertRaises(APIError) as duplicate:
            self.store.save(data, "renamed.pdf", visibility="public")
        self.assertEqual(duplicate.exception.status_code, 409)
        self.assertEqual([item.id for item in self.store.list(include_private=True)], [original.id])
        self.assertEqual(list(self.upload_dir.iterdir()), [self.store.file_path(original.id)])

        self.execute("ALTER TABLE web_document_pages ADD CONSTRAINT reject_page CHECK (page_number < 2)")
        with self.assertRaises(APIError):
            self.store.save(make_pdf("Valid", "Rejected"), "second.pdf")
        self.assertEqual([item.id for item in self.store.list(include_private=True)], [original.id])
        self.assertEqual(list(self.upload_dir.iterdir()), [self.store.file_path(original.id)])

    def test_failed_file_write_removes_partial_file_and_rolls_back_rows(self):
        from hsrule_web.errors import APIError

        with patch("hsrule_web.documents.os.fsync", side_effect=OSError("private path")):
            with self.assertRaises(APIError) as caught:
                self.store.save(make_pdf("File write failed"), "failed.pdf")
        self.assertEqual(caught.exception.code, "document_storage_error")
        self.assertEqual(self.store.list(include_private=True), [])
        self.assertEqual(list(self.upload_dir.iterdir()), [])

    def test_database_delete_failure_restores_original_file(self):
        from hsrule_web.errors import APIError

        data = make_pdf("Retain original")
        document = self.store.save(data, "original.pdf", visibility="public")
        self.execute("CREATE TABLE prevent_delete (id text REFERENCES web_documents(id) DEFERRABLE INITIALLY DEFERRED)")
        with closing(psycopg2.connect(self.dsn)) as conn, conn, conn.cursor() as cur:
            cur.execute("INSERT INTO prevent_delete VALUES (%s)", (document.id,))
        with self.assertRaises(APIError):
            self.store.delete(document.id)
        self.assertEqual(self.store.file_path(document.id).read_bytes(), data)
        self.assertIsNotNone(self.store.get(document.id))
        self.assertEqual(list(self.upload_dir.iterdir()), [self.store.file_path(document.id)])

    def test_filesystem_delete_failure_keeps_original_and_row(self):
        from hsrule_web.errors import APIError

        data = make_pdf("Retain original")
        document = self.store.save(data, "original.pdf", visibility="public")
        with patch("hsrule_web.documents.Path.replace", side_effect=OSError("private path")):
            with self.assertRaises(APIError):
                self.store.delete(document.id)
        self.assertEqual(self.store.file_path(document.id).read_bytes(), data)
        self.assertIsNotNone(self.store.get(document.id))
