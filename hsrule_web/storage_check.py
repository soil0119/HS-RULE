"""Read-only consistency check between web_documents and the uploads directory (#21).

It only reports. Each document is re-read while holding the advisory lock that save, delete and
their recovery hold, so an upload or delete in progress is never reported as an inconsistency.
Manual recovery steps for every finding kind are in docs/document-store-ops.md.

    python -m hsrule_web.storage_check [--verify-hash] [--json]

Exit status: 0 consistent, 1 findings reported, 2 the check could not run.
"""
import hashlib
import json
import os
import re
import stat
import sys
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path

import psycopg2

from hsrule_web.documents import document_lock_id

ORIGINAL = re.compile(r"([0-9a-f]{32})\.pdf")
PENDING = re.compile(r"\.([0-9a-f]{32})\.pdf\.deleting")

KINDS = {
    "missing_original": "메타데이터는 있지만 원본 PDF가 없습니다.",
    "recoverable_original": "원본은 없지만 삭제 대기 사본(.deleting)이 남아 있습니다.",
    "unregistered_file": "DB에 등록되지 않은 원본 PDF입니다.",
    "leftover_pending": "삭제가 끝난 문서의 삭제 대기 사본이 남아 있습니다.",
    "stale_pending": "원본과 삭제 대기 사본이 함께 있습니다.",
    "hash_mismatch": "원본 PDF의 SHA-256이 content_hash와 다릅니다.",
    "page_count_mismatch": "저장된 페이지 행 수가 page_count와 다릅니다.",
    "not_regular_file": "문서 파일 이름이지만 일반 파일이 아닙니다.",
    "unknown_file": "저장소 형식에 맞지 않는 파일입니다.",
    "busy": "문서 잠금을 기다리다 시간이 초과되어 점검하지 못했습니다. 다시 실행하세요.",
}


@dataclass(frozen=True)
class Finding:
    kind: str
    name: str
    document_id: str | None = None

    @property
    def message(self) -> str:
        return KINDS[self.kind]


def _entries(upload_dir: Path) -> tuple[set[str], list[Finding]]:
    """Document ids that have any file, plus findings for names outside the storage layout."""
    ids, findings = set(), []
    with os.scandir(upload_dir) as entries:
        for entry in entries:
            match = ORIGINAL.fullmatch(entry.name) or PENDING.fullmatch(entry.name)
            if match:
                ids.add(match.group(1))
            else:
                findings.append(Finding("unknown_file", entry.name))
    return ids, findings


def _file_state(path: Path) -> str:
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return "absent"
    return "file" if stat.S_ISREG(mode) else "other"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _check_document(cursor, upload_dir: Path, document_id: str, verify_hash: bool) -> list[Finding]:
    cursor.execute(
        "SELECT d.content_hash, d.page_count, "
        "(SELECT count(*) FROM web_document_pages p WHERE p.document_id = d.id) "
        "FROM web_documents d WHERE d.id = %s",
        (document_id,),
    )
    row = cursor.fetchone()
    original = upload_dir / f"{document_id}.pdf"
    pending = upload_dir / f".{document_id}.pdf.deleting"
    original_state, pending_state = _file_state(original), _file_state(pending)

    findings = [Finding("not_regular_file", path.name, document_id)
                for path, state in ((original, original_state), (pending, pending_state)) if state == "other"]
    has_original, has_pending = original_state == "file", pending_state == "file"
    if row is None:
        if has_original:
            findings.append(Finding("unregistered_file", original.name, document_id))
        if has_pending:
            findings.append(Finding("leftover_pending", pending.name, document_id))
        return findings

    content_hash, page_count, stored_pages = row
    if stored_pages != page_count:
        findings.append(Finding("page_count_mismatch", original.name, document_id))
    if has_original and has_pending:
        findings.append(Finding("stale_pending", pending.name, document_id))
    elif has_pending:
        findings.append(Finding("recoverable_original", pending.name, document_id))
    elif not has_original and original_state == "absent":
        findings.append(Finding("missing_original", original.name, document_id))
    if verify_hash and has_original and _sha256(original) != content_hash:
        findings.append(Finding("hash_mismatch", original.name, document_id))
    return findings


def check_storage(database_url: str, upload_dir: Path, *, verify_hash: bool = False,
                  lock_timeout: float = 5.0) -> list[Finding]:
    """Compare the catalog with the files without changing either. Raises OSError or psycopg2.Error."""
    upload_dir = Path(upload_dir)
    file_ids, findings = _entries(upload_dir)
    with closing(psycopg2.connect(database_url, connect_timeout=3)) as conn:
        # Read-only autocommit: every statement is its own read-only transaction.
        conn.set_session(readonly=True, autocommit=True)
        with conn.cursor() as cursor:
            cursor.execute("SELECT set_config('lock_timeout', %s, false)", (f"{int(lock_timeout * 1000)}ms",))
            cursor.execute("SELECT id FROM web_documents")
            document_ids = sorted({row[0] for row in cursor.fetchall()} | file_ids)
            for document_id in document_ids:
                lock_id = document_lock_id(document_id)
                try:
                    cursor.execute("SELECT pg_advisory_lock(%s)", (lock_id,))
                except psycopg2.errors.LockNotAvailable:
                    findings.append(Finding("busy", f"{document_id}.pdf", document_id))
                    continue
                try:
                    findings += _check_document(cursor, upload_dir, document_id, verify_hash)
                finally:
                    cursor.execute("SELECT pg_advisory_unlock(%s)", (lock_id,))
    return sorted(findings, key=lambda finding: (finding.kind, finding.name))


def main(argv: list[str] | None = None) -> int:
    import argparse

    from hsrule_web.settings import Settings

    parser = argparse.ArgumentParser(prog="python -m hsrule_web.storage_check",
                                     description="Report DB/uploads mismatches without changing anything.")
    parser.add_argument("--verify-hash", action="store_true", help="also compare every original's SHA-256")
    parser.add_argument("--json", action="store_true", help="print findings as JSON")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    if not settings.database_url:
        print("DATABASE_URL is required.", file=sys.stderr)
        return 2
    try:
        findings = check_storage(settings.database_url, settings.upload_dir, verify_hash=args.verify_hash)
    except (OSError, psycopg2.Error) as error:
        print(f"Storage check could not run: {type(error).__name__}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps([{**asdict(finding), "message": finding.message} for finding in findings],
                         ensure_ascii=False, indent=2))
    elif findings:
        for finding in findings:
            print(f"{finding.kind}\t{finding.name}\t{finding.message}")
    else:
        print(f"OK: {settings.upload_dir} matches the document catalog.")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
