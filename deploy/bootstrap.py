"""Create the app login once, then apply the existing document-store migrations."""
from contextlib import closing
import os
from pathlib import Path
import sys

import psycopg2
from psycopg2 import sql

from hsrule_web.documents import DocumentStore


def main() -> int:
    names = ("DB_ADMIN_URL", "DB_APP_PASSWORD", "UPLOAD_DIR")
    if any(not os.environ.get(name, "").strip() for name in names):
        print("DB_ADMIN_URL, DB_APP_PASSWORD and UPLOAD_DIR are required.", file=sys.stderr)
        return 1
    try:
        with closing(psycopg2.connect(os.environ["DB_ADMIN_URL"], connect_timeout=5)) as conn:
            with conn, conn.cursor() as cursor:
                # Serialize first-time role creation without changing an existing password.
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", (721086213289,))
                cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", ("hsrule_web",))
                if cursor.fetchone() is None:
                    cursor.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD %s").format(
                        sql.Identifier("hsrule_web")), (os.environ["DB_APP_PASSWORD"],))
                cursor.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        store = DocumentStore(os.environ["DB_ADMIN_URL"], Path(os.environ["UPLOAD_DIR"]))
        store.initialize()
        store.grant_app_role("hsrule_web")
    except Exception:
        # Driver errors may contain connection details; never print them or credentials.
        print("Document-store bootstrap failed; check DB access and upload permissions.", file=sys.stderr)
        return 1
    print("Document store initialized; app role granted. Existing role password preserved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
