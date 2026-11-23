import hashlib
import os
import subprocess
import time
import unittest
from contextlib import closing

import psycopg2


def vector(x, y=0):
    return [float(x), float(y)] + [0.0] * 382


def document(doc_id, visibility="public", body="규정 본문"):
    return {
        "id": doc_id, "title": "학교 규정", "filename": "rules.pdf", "visibility": visibility,
        "page_count": 1, "content_hash": hashlib.sha256(doc_id.encode()).hexdigest(),
        "pages": [{"page_number": 1, "text": body}], "warnings": [],
        "chunks": [{"id": f"{doc_id}:1:0", "document_id": doc_id, "ordinal": 0,
                    "page_start": 1, "page_end": 1, "article": "제1조", "content": body,
                    "embedding_text": body}],
    }


@unittest.skipUnless(os.getenv("HSRULE_TEST_PGVECTOR") == "1", "set HSRULE_TEST_PGVECTOR=1 for a disposable Docker database")
class RepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from hsrule.repository import RegulationRepository

        container = subprocess.check_output([
            "docker", "run", "-d", "--rm", "-p", "127.0.0.1::5432", "--tmpfs",
            "/var/lib/postgresql/data", "-e", "POSTGRES_HOST_AUTH_METHOD=trust", "pgvector/pgvector:pg16",
        ], text=True).strip()
        cls.addClassCleanup(subprocess.run, ["docker", "rm", "-f", container], check=True, stdout=subprocess.DEVNULL)
        port = subprocess.check_output(["docker", "port", container, "5432/tcp"], text=True).strip().split(":")[-1]
        cls.url = f"postgresql://postgres@127.0.0.1:{port}/postgres"
        for attempt in range(100):
            try:
                with closing(psycopg2.connect(cls.url)):
                    break
            except psycopg2.OperationalError:
                if attempt == 99:
                    raise
                time.sleep(0.2)
        cls.repo = RegulationRepository(cls.url)
        cls.repo.initialize()

    def setUp(self):
        with closing(psycopg2.connect(self.url)) as conn, conn, conn.cursor() as cursor:
            cursor.execute("TRUNCATE hs_documents CASCADE")

    def test_empty_and_populated_health_metadata_and_cascade_delete(self):
        self.assertFalse(self.repo.health()["ready"])
        self.repo.save_document(document("private", "private"), [vector(1)])
        status = self.repo.health()
        self.assertTrue(status["ready"])
        self.assertEqual((status["document_count"], status["chunk_count"]), (1, 1))
        self.assertEqual(self.repo.list_documents(), [])
        self.assertIsNone(self.repo.get_document("private"))
        listing, = self.repo.list_documents(include_private=True)
        for key in ["id", "title", "filename", "visibility", "page_count", "warnings", "created_at"]:
            self.assertIn(key, listing)
        self.assertEqual(listing["chunks"], 1)
        self.assertEqual(self.repo.get_document("private", True)["pages"][0]["text"], "규정 본문")
        self.assertEqual(self.repo.delete_document("private")["id"], "private")
        self.assertIsNone(self.repo.delete_document("private"))
        self.assertEqual(self.repo.health()["chunk_count"], 0)

    def test_visibility_before_limit_and_actual_cosines_for_both_searches(self):
        self.repo.save_document(document("private", "private", "규정"), [vector(1)])
        self.repo.save_document(document("public"), [vector(3, 4)])
        for method, args in [(self.repo.vector_candidates, ()), (self.repo.keyword_candidates, ("규정",))]:
            with self.subTest(method=method.__name__):
                public, = method(*args, vector(1), limit=1)
                self.assertEqual(public["document_id"], "public")
                self.assertAlmostEqual(public["score"], 0.6, places=5)
                for key in ["id", "title", "article", "content", "page_start", "page_end", "visibility"]:
                    self.assertIn(key, public)
                self.assertEqual(len(method(*args, vector(1), include_private=True)), 2)
        first, = self.repo.vector_candidates(vector(1), True, limit=1)
        self.assertEqual(first["document_id"], "private")
        self.assertEqual(self.repo.keyword_candidates("무관함", vector(1)), [])

    def test_duplicate_hash_and_failed_chunk_insert_preserve_existing_rows(self):
        from hsrule.repository import DuplicateDocumentError

        self.repo.save_document(document("original"), [vector(1)])
        duplicate = document("duplicate")
        duplicate["content_hash"] = document("original")["content_hash"]
        with self.assertRaises(DuplicateDocumentError):
            self.repo.save_document(duplicate, [vector(1)])
        failing = document("broken")
        failing["chunks"] *= 2
        with self.assertRaises(psycopg2.IntegrityError):
            self.repo.save_document(failing, [vector(1), vector(1)])
        self.assertEqual([d["id"] for d in self.repo.list_documents()], ["original"])
        self.assertEqual(self.repo.health()["chunk_count"], 1)

    def test_invalid_embeddings_and_mismatched_document_ids_are_atomic(self):
        self.repo.save_document(document("existing"), [vector(1)])
        for vectors in ([], [[1]], [vector(0)], [vector(float("nan"))]):
            with self.subTest(vectors=len(vectors)), self.assertRaises(ValueError):
                self.repo.save_document(document("bad"), vectors)
        for field, value in [("document_id", "existing"), ("id", "other:1:0")]:
            bad = document("bad")
            bad["chunks"][0][field] = value
            with self.assertRaises(ValueError):
                self.repo.save_document(bad, [vector(1)])
        self.assertEqual([d["id"] for d in self.repo.list_documents()], ["existing"])
