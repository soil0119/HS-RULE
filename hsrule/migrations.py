"""비파괴 migration 러너 (#24).

규칙: migration 파일은 추가만 한다. 적용된 파일은 수정하지 않고 새 번호로 또 하나 만든다.
데이터를 지우는 문장(DROP TABLE/COLUMN, TRUNCATE, DELETE)은 migration에 넣지 않는다.
러너가 그런 문장을 발견하면 적용을 거부한다. 색인 재구축은 별도 CLI(--rebuild)로만 한다.
"""
import re
from pathlib import Path

from psycopg2 import sql

MIGRATIONS_DIR = Path(__file__).with_name("migrations")
HISTORY_TABLE = re.compile(r"[a-z_][a-z0-9_]{0,62}")
DESTRUCTIVE = re.compile(r"\b(?:DROP\s+(?:TABLE|COLUMN|DATABASE|SCHEMA)|TRUNCATE|DELETE\s+FROM)\b", re.IGNORECASE)


class MigrationError(RuntimeError):
    """migration을 적용할 수 없다."""


def _strip_comments(sql: str) -> str:
    return re.sub(r"/\*.*?\*/", " ", re.sub(r"--[^\n]*", " ", sql), flags=re.DOTALL)


def migration_files(directory: Path | None = None) -> list[Path]:
    return sorted((directory or MIGRATIONS_DIR).glob("[0-9][0-9][0-9][0-9]_*.sql"))


def check_non_destructive(paths: list[Path]) -> None:
    for path in paths:
        found = DESTRUCTIVE.search(_strip_comments(path.read_text(encoding="utf-8")))
        if found:
            raise MigrationError(f"{path.name}에 데이터를 지우는 문장이 있습니다: {found.group(0)}")


def apply_migrations(cursor, directory: Path | None = None,
                     history_table: str = "hs_reg_migrations") -> list[str]:
    """적용되지 않은 migration만 순서대로 실행하고 적용한 파일 이름을 반환한다.

    history_table은 migration 묶음마다 따로 둔다(공식 색인 hs_reg_migrations, 웹 문서 web_migrations).
    cursor는 dict 행(RealDictCursor)을 반환해야 한다.
    """
    if not HISTORY_TABLE.fullmatch(history_table):
        raise MigrationError(f"이력 테이블 이름이 올바르지 않습니다: {history_table!r}")
    table = sql.Identifier(history_table)
    paths = migration_files(directory)
    check_non_destructive(paths)
    cursor.execute(sql.SQL("""
        CREATE TABLE IF NOT EXISTS {} (
            name text PRIMARY KEY,
            checksum text NOT NULL,
            applied_at timestamptz NOT NULL DEFAULT now()
        )
    """).format(table))
    cursor.execute(sql.SQL("SELECT name, checksum FROM {}").format(table))
    applied_before = {row["name"]: row["checksum"] for row in cursor.fetchall()}
    applied = []
    for path in paths:
        import hashlib

        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        if path.name in applied_before:
            if applied_before[path.name] != checksum:
                raise MigrationError(f"{path.name}이 적용 후 수정되었습니다. 새 번호의 migration을 추가하세요.")
            continue
        cursor.execute(path.read_text(encoding="utf-8"))
        cursor.execute(sql.SQL("INSERT INTO {} (name, checksum) VALUES (%s, %s)").format(table),
                       (path.name, checksum))
        applied.append(path.name)
    return applied
