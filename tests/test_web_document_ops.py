"""문서 저장소 운영 회귀(#21): migration 기준선, 최소 권한 역할, 동시 업로드·삭제, 다운로드 중 삭제, 읽기 전용 점검."""
import asyncio
import hashlib
import json
import os
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, redirect_stdout
from io import StringIO
from pathlib import Path
from threading import Barrier, Event, current_thread
from unittest.mock import patch
from uuid import uuid4

import psycopg2
from psycopg2 import sql
from psycopg2.extensions import connection, make_dsn

from test_web_documents import make_pdf

DSN = os.getenv("HSRULE_WEB_TEST_DSN")
BASELINE = "0001_web_documents.sql"


class HistoryTableNameTests(unittest.TestCase):
    def test_runner_rejects_an_unsafe_history_table_before_touching_the_database(self):
        from hsrule.migrations import MigrationError, apply_migrations

        for name in ("web_migrations; DROP TABLE x", "Web", "", "1abc"):
            with self.subTest(name=name), self.assertRaises(MigrationError):
                apply_migrations(None, history_table=name)


@unittest.skipUnless(DSN, "isolated PostgreSQL DSN not provided")
class DocumentStoreOpsTests(unittest.TestCase):
    def setUp(self):
        self.schema = "web_test_" + uuid4().hex
        self.admin(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.addCleanup(self.admin, sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))
        self.dsn = make_dsn(DSN, options=f"-c search_path={self.schema}")
        self.upload_dir = Path(self.enterContext(tempfile.TemporaryDirectory())) / "uploads"
        self.store = self.make_store()

    def make_store(self, dsn=None):
        from hsrule_web.documents import DocumentStore

        return DocumentStore(dsn or self.dsn, self.upload_dir)

    def admin(self, statement, params=None):
        with closing(psycopg2.connect(DSN)) as conn, conn, conn.cursor() as cur:
            cur.execute(statement, params)

    def query(self, statement, params=None):
        with closing(psycopg2.connect(self.dsn)) as conn, conn, conn.cursor() as cur:
            cur.execute(statement, params)
            return cur.fetchall() if cur.description else None

    def rows(self):
        return (self.query("SELECT * FROM web_documents ORDER BY id"),
                self.query("SELECT * FROM web_document_pages ORDER BY document_id, page_number"))

    def files(self):
        return sorted(path.name for path in self.upload_dir.iterdir())

    # --- migration ---------------------------------------------------------------------------------

    def test_fresh_database_records_the_baseline_once(self):
        self.assertEqual(self.store.initialize(), [BASELINE])
        self.assertEqual(self.store.initialize(), [])
        self.assertEqual(self.query("SELECT name FROM web_migrations"), [(BASELINE,)])
        self.assertTrue(self.store.health())

    def test_pre_migration_database_adopts_the_baseline_without_changing_data(self):
        from hsrule_web.documents import MIGRATIONS_DIR

        # A database created by the inline SCHEMA of PR #33, before versioned migrations existed.
        self.query((MIGRATIONS_DIR / BASELINE).read_text(encoding="utf-8"))
        data = make_pdf("Kept page", "Second page")
        document = self.store.save(data, "kept.pdf", visibility="public")
        before = self.rows()

        self.assertEqual(self.store.initialize(), [BASELINE])
        self.assertEqual(self.rows(), before)
        self.assertEqual(self.files(), [f"{document.id}.pdf"])
        self.assertEqual(self.store.file_path(document.id).read_bytes(), data)

    def test_concurrent_initialize_applies_the_baseline_exactly_once(self):
        workers = 4
        barrier = Barrier(workers)

        def initialize():
            store = self.make_store()
            barrier.wait(timeout=5)
            return store.initialize()

        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = [future.result(timeout=10) for future in [pool.submit(initialize) for _ in range(workers)]]
        self.assertEqual(sorted(results), [[]] * (workers - 1) + [[BASELINE]])
        self.assertEqual(self.query("SELECT name FROM web_migrations"), [(BASELINE,)])

    def test_edited_baseline_is_refused(self):
        from hsrule.migrations import MigrationError

        self.store.initialize()
        self.query("UPDATE web_migrations SET checksum = 'edited' WHERE name = %s", (BASELINE,))
        with self.assertRaises(MigrationError):
            self.store.initialize()

    # --- least-privilege app role -----------------------------------------------------------------

    def create_login_role(self):
        with closing(psycopg2.connect(DSN)) as conn, conn.cursor() as cur:
            cur.execute("SELECT rolsuper OR rolcreaterole FROM pg_roles WHERE rolname = current_user")
            if not cur.fetchone()[0]:
                self.skipTest("test DSN cannot create roles")
        role, password = "web_app_" + uuid4().hex[:12], uuid4().hex
        self.admin(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(role), sql.Literal(password)))
        # Runs after the schema is dropped (cleanups are LIFO), so no privileges remain.
        self.addCleanup(self.admin, sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
        self.addCleanup(self.admin, sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
        return role, make_dsn(self.dsn, user=role, password=password)

    def test_cli_migrates_and_grants_an_app_role_row_access_only(self):
        from hsrule_web.documents import main
        from hsrule_web.errors import APIError

        role, app_dsn = self.create_login_role()
        env = {"DATABASE_URL": self.dsn, "UPLOAD_DIR": str(self.upload_dir)}
        output = StringIO()
        with patch.dict(os.environ, env), redirect_stdout(output):
            main(["--grant-role", role])
            main([])
        self.assertIn(BASELINE, output.getvalue())
        self.assertIn("none (already current)", output.getvalue())

        app = self.make_store(app_dsn)
        self.assertTrue(app.health())
        data = make_pdf("App role page")
        document = app.save(data, "role.pdf", visibility="public")
        self.assertEqual(app.get(document.id, include_pages=True).pages[0].page_number, 1)
        self.assertEqual([item.id for item in app.list()], [document.id])
        self.assertTrue(app.delete(document.id))
        self.assertEqual(self.rows(), ([], []))
        self.assertEqual(self.files(), [])

        # The app role cannot change the schema or read the migration history.
        with self.assertRaises(APIError):
            app.initialize()
        for statement in ("CREATE TABLE web_extra (id int)", "DROP TABLE web_document_pages",
                          "TRUNCATE web_documents", "SELECT * FROM web_migrations"):
            with self.subTest(statement=statement), closing(psycopg2.connect(app_dsn)) as conn:
                with self.assertRaises(psycopg2.errors.InsufficientPrivilege), conn, conn.cursor() as cur:
                    cur.execute(statement)

    def test_granting_a_missing_role_is_refused(self):
        from hsrule_web.errors import APIError

        self.store.initialize()
        with self.assertRaises(APIError) as caught:
            self.store.grant_app_role("web_app_missing_" + uuid4().hex[:8])
        self.assertEqual(caught.exception.status_code, 422)

    # --- concurrent upload / delete ----------------------------------------------------------------

    def test_concurrent_uploads_of_the_same_pdf_store_exactly_one(self):
        from hsrule_web.errors import APIError

        self.store.initialize()
        data, workers = make_pdf("Same bytes from four admins"), 4
        barrier = Barrier(workers)

        def upload(number):
            store = self.make_store()
            barrier.wait(timeout=5)
            try:
                return store.save(data, f"copy-{number}.pdf").id
            except APIError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(upload, range(workers), timeout=20))
        stored = [result for result in results if result != "duplicate_document"]
        self.assertEqual(len(stored), 1, results)
        self.assertEqual([row[0] for row in self.rows()[0]], stored)
        self.assertEqual(self.files(), [f"{stored[0]}.pdf"])

    def test_upload_waits_for_an_uncommitted_duplicate_and_follows_its_outcome(self):
        from hsrule_web.documents import METADATA_COLUMNS
        from hsrule_web.errors import APIError

        self.store.initialize()
        for outcome in ("commit", "rollback"):
            with self.subTest(outcome=outcome):
                data = make_pdf(f"Racing upload {outcome}")
                other_id = uuid4().hex
                with closing(psycopg2.connect(self.dsn)) as other, ThreadPoolExecutor(max_workers=1) as pool:
                    with other.cursor() as cur:
                        cur.execute(f"INSERT INTO web_documents ({METADATA_COLUMNS}) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                                    (other_id, "other", "other.pdf", "private",
                                     hashlib.sha256(data).hexdigest(), 1, []))
                    pending = pool.submit(self.store.save, data, "mine.pdf")
                    self.wait_until_blocked()
                    self.assertFalse(pending.done())
                    getattr(other, outcome)()
                    if outcome == "commit":
                        with self.assertRaises(APIError) as caught:
                            pending.result(timeout=5)
                        self.assertEqual(caught.exception.code, "duplicate_document")
                        self.assertEqual(self.files(), [])
                        self.query("DELETE FROM web_documents WHERE id = %s", (other_id,))
                    else:
                        mine = pending.result(timeout=5)
                        self.assertEqual(self.files(), [f"{mine.id}.pdf"])
                        self.assertTrue(self.store.delete(mine.id))

    def wait_until_blocked(self, timeout=5.0):
        """Wait until another session in this database waits on a lock held by a competing transaction."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.query("SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock' "
                          "AND datname = current_database() AND pid <> pg_backend_pid()")[0][0]:
                return
            time.sleep(0.02)
        self.fail("upload never waited for the competing transaction")

    def paused(self, call, *args, commit=True, **kwargs):
        """Run a store call in a thread, holding it just before its COMMIT until released."""
        reached, release = Event(), Event()
        connect = psycopg2.connect
        deleter = "paused-store-call"

        class PausedCommit(connection):
            def __exit__(self, exc_type, exc, traceback):
                if exc_type is None and current_thread().name.startswith(deleter):
                    reached.set()
                    if not release.wait(timeout=8):
                        raise TimeoutError("test did not release the store call")
                    if not commit:
                        super().__exit__(psycopg2.OperationalError, None, None)  # roll back
                        raise psycopg2.OperationalError("test: transaction rolled back")
                return super().__exit__(exc_type, exc, traceback)

        def connect_with_pause(dsn, **options):
            # Only the first transaction pauses; its recovery connection (if any) behaves normally.
            if current_thread().name.startswith(deleter) and not reached.is_set():
                return connect(dsn, connection_factory=PausedCommit, **options)
            return connect(dsn, **options)

        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix=deleter)
        self.addCleanup(pool.shutdown)
        self.addCleanup(release.set)
        patcher = patch("hsrule_web.documents.psycopg2.connect", side_effect=connect_with_pause)
        patcher.start()
        self.addCleanup(patcher.stop)
        future = pool.submit(call, *args, **kwargs)
        self.assertTrue(reached.wait(timeout=5), "store call never reached its commit")
        return future, release

    def test_reupload_during_delete_waits_and_succeeds_after_the_delete_commits(self):
        self.store.initialize()
        data = make_pdf("Delete then upload again")
        old = self.store.save(data, "old.pdf", visibility="public")
        deleting, release = self.paused(self.store.delete, old.id, commit=True)
        self.assertEqual(self.files(), [f".{old.id}.pdf.deleting"])

        with ThreadPoolExecutor(max_workers=1) as pool:
            upload = pool.submit(self.make_store().save, data, "new.pdf", visibility="public")
            self.wait_until_blocked()
            self.assertFalse(upload.done(), "upload overtook the uncommitted delete")
            release.set()
            self.assertTrue(deleting.result(timeout=5))
            new = upload.result(timeout=5)
        self.assertNotEqual(new.id, old.id)
        self.assertEqual([row[0] for row in self.rows()[0]], [new.id])
        self.assertEqual(self.files(), [f"{new.id}.pdf"])
        self.assertEqual(self.store.file_path(new.id).read_bytes(), data)

    def test_reupload_during_a_failed_delete_is_rejected_and_the_original_is_restored(self):
        from hsrule_web.errors import APIError

        self.store.initialize()
        data = make_pdf("Delete fails, upload again")
        old = self.store.save(data, "old.pdf", visibility="public")
        deleting, release = self.paused(self.store.delete, old.id, commit=False)

        with ThreadPoolExecutor(max_workers=1) as pool:
            upload = pool.submit(self.make_store().save, data, "new.pdf", visibility="public")
            self.wait_until_blocked()
            release.set()
            with self.assertRaises(APIError):
                deleting.result(timeout=5)
            with self.assertRaises(APIError) as caught:
                upload.result(timeout=5)
        self.assertEqual(caught.exception.code, "duplicate_document")
        self.assertEqual([row[0] for row in self.rows()[0]], [old.id])
        self.assertEqual(self.files(), [f"{old.id}.pdf"])
        self.assertEqual(self.store.file_path(old.id).read_bytes(), data)

    # --- download while deleting -------------------------------------------------------------------

    async def download(self, app, path, on_start=None):
        scope = {
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1", "method": "GET", "scheme": "http", "path": path,
            "query_string": b"", "headers": [(b"host", b"testserver")],
            "server": ("testserver", 80), "client": ("127.0.0.1", 1234),
            # A path-send server would reopen the deleted name; the response must ignore it.
            "extensions": {"http.response.pathsend": {}},
        }
        messages = []

        async def receive():
            await asyncio.Future()

        async def send(message):
            messages.append(message)
            if message["type"] == "http.response.start" and on_start is not None:
                await on_start()

        await app(scope, receive, send)
        return messages[0]["status"], b"".join(m.get("body", b"") for m in messages
                                                if m["type"] == "http.response.body")

    def app(self, store):
        from hsrule_web.settings import Settings
        from server import create_app

        return create_app(Settings(allowed_hosts=("testserver",)), document_store=store)

    @unittest.skipIf(sys.platform == "win32", "Windows prevents renaming a PDF while it is open")
    def test_download_in_progress_finishes_when_the_document_is_deleted(self):
        self.store.initialize()
        data = make_pdf("Streaming original", "Page two")
        document = self.store.save(data, "stream.pdf", visibility="public")
        app, path = self.app(self.store), f"/documents/{document.id}/file"

        async def delete_after_headers():
            self.assertTrue(await asyncio.to_thread(self.store.delete, document.id))

        status, body = asyncio.run(self.download(app, path, delete_after_headers))
        self.assertEqual((status, body), (200, data))
        self.assertIsNone(self.store.get(document.id, include_private=True))
        self.assertEqual(self.files(), [])
        self.assertEqual(asyncio.run(self.download(app, path))[0], 404)

    def test_delete_between_metadata_lookup_and_file_open_returns_404(self):
        self.store.initialize()
        document = self.store.save(make_pdf("Removed before open"), "gone.pdf", visibility="public")
        store = self.store

        class DeleteAfterLookup:
            def __getattr__(self, name):
                return getattr(store, name)

            def get(self, document_id, **options):
                found = store.get(document_id, **options)
                store.delete(document_id)
                return found

        status, _ = asyncio.run(self.download(self.app(DeleteAfterLookup()), f"/documents/{document.id}/file"))
        self.assertEqual(status, 404)
        self.assertEqual(self.files(), [])

    # --- read-only storage check -------------------------------------------------------------------

    def check(self, **options):
        from hsrule_web.storage_check import check_storage

        return [(finding.kind, finding.name) for finding in check_storage(self.dsn, self.upload_dir, **options)]

    def snapshot(self):
        contents = {path.name: path.read_bytes() if path.is_file() else None for path in self.upload_dir.iterdir()}
        return self.rows(), contents

    def test_storage_check_reports_every_mismatch_without_changing_anything(self):
        self.store.initialize()
        save = lambda text: self.store.save(make_pdf(text, "page 2"), f"{text}.pdf")  # noqa: E731
        clean, missing, recoverable, stale, tampered, short = (
            save(name) for name in ("clean", "missing", "recoverable", "stale", "tampered", "short"))
        path = self.store.file_path
        pending = lambda document: path(document.id).with_name(f".{document.id}.pdf.deleting")  # noqa: E731

        path(missing.id).unlink()
        path(recoverable.id).replace(pending(recoverable))
        pending(stale).write_bytes(path(stale.id).read_bytes())
        path(tampered.id).write_bytes(b"%PDF-1.4 changed %%EOF")
        self.query("DELETE FROM web_document_pages WHERE document_id = %s AND page_number = 2", (short.id,))
        unregistered, leftover, directory = uuid4().hex, uuid4().hex, uuid4().hex
        (self.upload_dir / f"{unregistered}.pdf").write_bytes(b"orphan")
        (self.upload_dir / f".{leftover}.pdf.deleting").write_bytes(b"orphan backup")
        (self.upload_dir / f"{directory}.pdf").mkdir()
        (self.upload_dir / "notes.txt").write_text("stray")
        before = self.snapshot()

        expected = sorted([
            ("leftover_pending", f".{leftover}.pdf.deleting"),
            ("missing_original", f"{missing.id}.pdf"),
            ("not_regular_file", f"{directory}.pdf"),
            ("page_count_mismatch", f"{short.id}.pdf"),
            ("recoverable_original", f".{recoverable.id}.pdf.deleting"),
            ("stale_pending", f".{stale.id}.pdf.deleting"),
            ("unknown_file", "notes.txt"),
            ("unregistered_file", f"{unregistered}.pdf"),
        ])
        self.assertEqual(self.check(), expected)
        self.assertEqual(self.check(verify_hash=True), sorted(expected + [("hash_mismatch", f"{tampered.id}.pdf")]))
        self.assertEqual(self.snapshot(), before)
        self.assertNotIn(clean.id, str(self.check(verify_hash=True)))

    def test_storage_check_session_cannot_write(self):
        from hsrule_web import storage_check

        self.store.initialize()
        self.store.save(make_pdf("Read only"), "read-only.pdf")
        original = storage_check._check_document

        def try_to_write(cursor, *args):
            with self.assertRaises(psycopg2.errors.ReadOnlySqlTransaction):
                cursor.execute("DELETE FROM web_documents")
            return original(cursor, *args)

        with patch.object(storage_check, "_check_document", try_to_write):
            self.assertEqual(self.check(), [])
        self.assertEqual(len(self.rows()[0]), 1)

    def test_storage_check_waits_for_an_upload_in_progress_instead_of_reporting_it(self):
        from hsrule_web.storage_check import check_storage

        self.store.initialize()
        saving, release = self.paused(self.store.save, make_pdf("In flight"), "flight.pdf", commit=True)
        # The file exists and the row is not committed yet: a naive scan would call it unregistered.
        self.assertEqual(len(self.files()), 1)
        with ThreadPoolExecutor(max_workers=1) as pool:
            checking = pool.submit(check_storage, self.dsn, self.upload_dir)
            time.sleep(0.3)
            self.assertFalse(checking.done(), "check did not wait for the document lock")
            release.set()
            document = saving.result(timeout=5)
            self.assertEqual(checking.result(timeout=5), [])
        self.assertEqual(self.files(), [f"{document.id}.pdf"])

    def test_storage_check_reports_busy_when_a_document_stays_locked(self):
        self.store.initialize()
        saving, release = self.paused(self.store.save, make_pdf("Stuck"), "stuck.pdf", commit=True)
        [name] = self.files()
        self.assertEqual(self.check(lock_timeout=0.2), [("busy", name)])
        release.set()
        saving.result(timeout=5)
        self.assertEqual(self.check(), [])

    def test_storage_check_cli_exit_status_and_json(self):
        from hsrule_web.storage_check import main

        self.store.initialize()
        document = self.store.save(make_pdf("CLI"), "cli.pdf")
        env = {"DATABASE_URL": self.dsn, "UPLOAD_DIR": str(self.upload_dir)}
        with patch.dict(os.environ, env):
            with redirect_stdout(StringIO()) as output:
                self.assertEqual(main([]), 0)
            self.assertIn("OK", output.getvalue())
            self.store.file_path(document.id).unlink()
            with redirect_stdout(StringIO()) as output:
                self.assertEqual(main(["--json"]), 1)
            self.assertEqual([(item["kind"], item["document_id"]) for item in json.loads(output.getvalue())],
                             [("missing_original", document.id)])
        with patch.dict(os.environ, {**env, "UPLOAD_DIR": str(self.upload_dir / "absent")}):
            with redirect_stdout(StringIO()), patch("sys.stderr", StringIO()):
                self.assertEqual(main([]), 2)


if __name__ == "__main__":
    unittest.main()
