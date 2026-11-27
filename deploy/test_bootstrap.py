"""Integration check against a disposable DB named hsrule_bootstrap_test only."""
import os
from contextlib import closing
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import psycopg2


@unittest.skipUnless(os.environ.get("HSRULE_BOOTSTRAP_TEST_DSN"), "disposable PostgreSQL required")
class BootstrapCheck(unittest.TestCase):
    def test_bootstrap_grants_row_access_and_preserves_the_existing_password(self):
        dsn = os.environ["HSRULE_BOOTSTRAP_TEST_DSN"]
        with closing(psycopg2.connect(dsn)) as conn, conn.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            self.assertEqual(cursor.fetchone()[0], "hsrule_bootstrap_test")
            cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = 'hsrule_web'")
            self.assertIsNone(cursor.fetchone(), "test requires a fresh, disposable role namespace")
        password = "bootstrap-check-'\\-original"
        replacement = "bootstrap-check-replacement"
        script = Path(__file__).with_name("bootstrap.py")
        with tempfile.TemporaryDirectory() as uploads:
            env = {**os.environ, "DB_ADMIN_URL": dsn, "DB_APP_PASSWORD": password,
                   "UPLOAD_DIR": uploads, "PYTHONPATH": str(script.parent.parent)}
            try:
                missing = subprocess.run([sys.executable, str(script)], env={**env, "DB_APP_PASSWORD": ""},
                                         capture_output=True, text=True)
                self.assertNotEqual(missing.returncode, 0)
                for supplied_password in (password, replacement):
                    result = subprocess.run([sys.executable, str(script)],
                                            env={**env, "DB_APP_PASSWORD": supplied_password},
                                            capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertNotIn(password, result.stdout + result.stderr)
                    self.assertNotIn(replacement, result.stdout + result.stderr)
                with closing(psycopg2.connect(dsn, user="hsrule_web", password=password)) as conn:
                    with conn.cursor() as cursor:
                        cursor.execute("SELECT count(*) FROM web_documents")
                        self.assertEqual(cursor.fetchone()[0], 0)
                        cursor.execute("SELECT has_schema_privilege(current_user, 'public', 'CREATE'), "
                                       "has_table_privilege(current_user, 'web_documents', 'DELETE'), "
                                       "has_table_privilege(current_user, 'web_document_pages', 'INSERT')")
                        self.assertEqual(cursor.fetchone(), (False, True, True))
                with self.assertRaises(psycopg2.OperationalError):
                    psycopg2.connect(dsn, user="hsrule_web", password=replacement)
            finally:
                with closing(psycopg2.connect(dsn)) as conn, conn, conn.cursor() as cursor:
                    cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = 'hsrule_web'")
                    if cursor.fetchone():
                        cursor.execute("DROP OWNED BY hsrule_web")
                        cursor.execute("DROP ROLE hsrule_web")


if __name__ == "__main__":
    unittest.main()
