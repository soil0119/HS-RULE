"""Back up and restore web document rows and their original PDFs at one recovery point (#21).

    python -m hsrule_web.backup create BACKUP_DIR
    python -m hsrule_web.backup restore BACKUP_DIR

Both use DATABASE_URL (the schema owner) and UPLOAD_DIR. `create` holds an EXCLUSIVE table lock on
web_documents while it copies rows and files: reads continue, but uploads, deletes and row locks wait,
so the rows and PDFs in the backup describe the same moment. `restore` only fills an empty catalog and
an empty upload directory, verifies every copied byte against the manifest, and never overwrites.
Rehearsal record and manual steps: docs/document-store-ops.md.
"""
import hashlib
import json
import os
import shutil
import sys
from contextlib import closing
from pathlib import Path

import psycopg2
from psycopg2 import sql
from psycopg2.extras import RealDictCursor

from hsrule.migrations import apply_migrations, migration_files
from hsrule_web.documents import MIGRATION_HISTORY, MIGRATION_LOCK, MIGRATIONS_DIR

FORMAT = 1
MANIFEST = "manifest.json"
TABLES = ("web_documents", "web_document_pages")  # web_migrations is rebuilt by the migrations


class BackupError(RuntimeError):
    """The backup or restore was refused; nothing was overwritten."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _fsync_copy(source: Path, target: Path) -> None:
    with source.open("rb") as reader, target.open("xb") as writer:
        shutil.copyfileobj(reader, writer, 1024 * 1024)
        writer.flush()
        os.fsync(writer.fileno())


def _current_migrations() -> list[list[str]]:
    return [[path.name, hashlib.sha256(path.read_bytes()).hexdigest()] for path in migration_files(MIGRATIONS_DIR)]


def create_backup(database_url: str, upload_dir: Path, backup_dir: Path, *, lock_timeout: float = 10.0) -> dict:
    upload_dir, backup_dir = Path(upload_dir), Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=False, mode=0o700)  # never mix with an older backup
    try:
        (backup_dir / "uploads").mkdir(mode=0o700)
        with closing(psycopg2.connect(database_url, connect_timeout=3)) as conn, conn.cursor() as cursor:
            cursor.execute("SELECT set_config('lock_timeout', %s, true)", (f"{int(lock_timeout * 1000)}ms",))
            cursor.execute("LOCK TABLE web_documents IN EXCLUSIVE MODE")
            cursor.execute("SELECT now(), current_setting('server_version')")
            recovery_point, server_version = cursor.fetchone()
            cursor.execute(sql.SQL("SELECT name, checksum FROM {} ORDER BY name").format(
                sql.Identifier(MIGRATION_HISTORY)))
            migrations = [list(row) for row in cursor.fetchall()]
            tables = {}
            for table in TABLES:
                target = backup_dir / f"{table}.copy"
                with target.open("xb") as output:
                    cursor.copy_expert(sql.SQL("COPY {} TO STDOUT").format(sql.Identifier(table)), output)
                    output.flush()
                    os.fsync(output.fileno())
                cursor.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table)))
                tables[table] = {"rows": cursor.fetchone()[0], "sha256": _sha256(target)}
            cursor.execute("SELECT id, content_hash FROM web_documents ORDER BY id")
            files = {}
            for document_id, content_hash in cursor.fetchall():
                name = f"{document_id}.pdf"
                try:
                    _fsync_copy(upload_dir / name, backup_dir / "uploads" / name)
                except FileNotFoundError:
                    raise BackupError(f"원본이 없는 문서가 있습니다: {name}. storage_check로 먼저 확인하세요.") from None
                digest = _sha256(backup_dir / "uploads" / name)
                if digest != content_hash:
                    raise BackupError(f"원본 해시가 DB와 다릅니다: {name}. storage_check --verify-hash로 확인하세요.")
                files[name] = digest
            conn.rollback()  # releases the lock; nothing was written
        manifest = {
            "format": FORMAT, "recovery_point": recovery_point.isoformat(), "server_version": server_version,
            "migrations": migrations, "tables": tables, "files": files,
        }
        # The manifest is written last: a directory without it is an incomplete backup.
        with (backup_dir / MANIFEST).open("x", encoding="utf-8") as output:
            json.dump(manifest, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
        return manifest
    except BaseException:
        shutil.rmtree(backup_dir, ignore_errors=True)
        raise


def verify_backup(backup_dir: Path) -> dict:
    """Check the manifest and every copied byte. Returns the manifest."""
    backup_dir = Path(backup_dir)
    try:
        manifest = json.loads((backup_dir / MANIFEST).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise BackupError("manifest.json이 없습니다. 완료되지 않은 백업입니다.") from None
    if manifest.get("format") != FORMAT:
        raise BackupError(f"지원하지 않는 백업 형식입니다: {manifest.get('format')!r}")
    expected = {f"{table}.copy": info["sha256"] for table, info in manifest["tables"].items()}
    expected |= {f"uploads/{name}": digest for name, digest in manifest["files"].items()}
    for relative, digest in expected.items():
        path = backup_dir / relative
        if not path.is_file() or _sha256(path) != digest:
            raise BackupError(f"백업 파일이 없거나 손상되었습니다: {relative}")
    extra = {path.name for path in (backup_dir / "uploads").iterdir()} - set(manifest["files"])
    if extra:
        raise BackupError(f"manifest에 없는 백업 파일이 있습니다: {sorted(extra)}")
    return manifest


def restore_backup(backup_dir: Path, database_url: str, upload_dir: Path) -> dict:
    backup_dir, upload_dir = Path(backup_dir), Path(upload_dir)
    manifest = verify_backup(backup_dir)
    if manifest["migrations"] != _current_migrations():
        raise BackupError("백업의 migration 이력이 현재 코드와 다릅니다. 백업을 만든 버전의 코드로 복원하세요.")
    if upload_dir.exists() and any(upload_dir.iterdir()):
        raise BackupError(f"복원 대상 업로드 폴더가 비어 있지 않습니다: {upload_dir}")

    copied = []
    with closing(psycopg2.connect(database_url, connect_timeout=3)) as conn:
        try:
            with conn, conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", (MIGRATION_LOCK,))
                apply_migrations(cursor, MIGRATIONS_DIR, history_table=MIGRATION_HISTORY)
                cursor.execute("LOCK TABLE web_documents IN EXCLUSIVE MODE")
                cursor.execute("SELECT EXISTS (SELECT 1 FROM web_documents) AS used")
                if cursor.fetchone()["used"]:
                    raise BackupError("복원 대상 DB의 web_documents가 비어 있지 않습니다.")
                for table in TABLES:
                    with (backup_dir / f"{table}.copy").open("rb") as source:
                        cursor.copy_expert(sql.SQL("COPY {} FROM STDIN").format(sql.Identifier(table)), source)
                    cursor.execute(sql.SQL("SELECT count(*) AS rows FROM {}").format(sql.Identifier(table)))
                    if cursor.fetchone()["rows"] != manifest["tables"][table]["rows"]:
                        raise BackupError(f"{table} 행 수가 manifest와 다릅니다.")
                upload_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
                for name in manifest["files"]:
                    _fsync_copy(backup_dir / "uploads" / name, upload_dir / name)
                    copied.append(upload_dir / name)
                # COMMIT happens here; files are already durable, matching how save orders writes.
        except BaseException:
            # The target directory was empty, so every file in it is ours to remove.
            for path in copied:
                path.unlink(missing_ok=True)
            raise
    return manifest


def main(argv: list[str] | None = None) -> int:
    import argparse

    from hsrule_web.settings import Settings
    from hsrule_web.storage_check import check_storage

    parser = argparse.ArgumentParser(prog="python -m hsrule_web.backup",
                                     description="Back up or restore web documents (DATABASE_URL, UPLOAD_DIR).")
    parser.add_argument("action", choices=("create", "restore", "verify"))
    parser.add_argument("backup_dir", type=Path)
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    if args.action != "verify" and not settings.database_url:
        print("DATABASE_URL is required.", file=sys.stderr)
        return 2
    try:
        if args.action == "create":
            manifest = create_backup(settings.database_url, settings.upload_dir, args.backup_dir)
        elif args.action == "verify":
            manifest = verify_backup(args.backup_dir)
        else:
            manifest = restore_backup(args.backup_dir, settings.database_url, settings.upload_dir)
    except (BackupError, OSError, psycopg2.Error) as error:
        print(f"{args.action} refused: {error}", file=sys.stderr)
        return 2
    counts = ", ".join(f"{table} {info['rows']}" for table, info in manifest["tables"].items())
    print(f"{args.action}: recovery point {manifest['recovery_point']} ({counts}, files {len(manifest['files'])})")
    if args.action == "restore":
        findings = check_storage(settings.database_url, settings.upload_dir, verify_hash=True)
        for finding in findings:
            print(f"{finding.kind}\t{finding.name}\t{finding.message}")
        print("storage_check --verify-hash:", "OK" if not findings else f"{len(findings)} findings")
        return 1 if findings else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
