import hashlib
import io
import logging
import os
import re
from contextlib import closing, contextmanager, suppress
from pathlib import Path, PureWindowsPath
from typing import BinaryIO, Literal
from uuid import uuid4

import psycopg2
from pdfminer.high_level import extract_pages
from pdfminer.layout import LAParams, LTContainer, LTTextContainer
from pydantic import BaseModel
from psycopg2.extras import execute_values

from hsrule_web.errors import APIError

MAX_PDF_BYTES = 20 * 1024 * 1024
MAX_PDF_PAGES = 1000
DOCUMENT_ID_PATTERN = r"^[0-9a-f]{32}$"
Visibility = Literal["private", "public"]
LOGGER = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS web_documents (
    id text PRIMARY KEY CHECK (id ~ '^[0-9a-f]{32}$'),
    title text NOT NULL,
    filename text NOT NULL,
    visibility text NOT NULL CHECK (visibility IN ('private', 'public')),
    content_hash text NOT NULL CONSTRAINT web_documents_content_hash_unique UNIQUE,
    page_count integer NOT NULL CHECK (page_count BETWEEN 1 AND 1000),
    warnings text[] NOT NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS web_document_pages (
    document_id text NOT NULL REFERENCES web_documents(id) ON DELETE CASCADE,
    page_number integer NOT NULL CHECK (page_number BETWEEN 1 AND 1000),
    text text NOT NULL,
    PRIMARY KEY (document_id, page_number)
);
"""
METADATA_COLUMNS = "id, title, filename, visibility, content_hash, page_count, warnings"


class DocumentMetadata(BaseModel):
    id: str
    title: str
    filename: str
    visibility: Visibility
    content_hash: str
    page_count: int
    warnings: list[str]
    status: Literal["stored"] = "stored"
    index_status: Literal["not_connected"] = "not_connected"


class DocumentPage(BaseModel):
    page_number: int
    text: str


class SourceDocument(BaseModel):
    metadata: DocumentMetadata
    pages: list[DocumentPage]


def read_upload(file: BinaryIO) -> bytes:
    file.seek(0)
    data = file.read(MAX_PDF_BYTES + 1)
    check_byte_limit(data)
    return data


def check_byte_limit(data: bytes) -> None:
    if len(data) > MAX_PDF_BYTES:
        raise APIError(413, "file_too_large", "파일은 최대 20 MiB까지 허용합니다.")


def display_filename(filename: str) -> str:
    name = PureWindowsPath(filename).name
    return "".join(char for char in name if char.isprintable())[:255]


def _text_blocks(layout):
    for element in layout:
        if isinstance(element, LTTextContainer):
            yield element.get_text()
        elif isinstance(element, LTContainer):
            yield from _text_blocks(element)


def parse_pdf(data: bytes) -> tuple[list[DocumentPage], list[str]]:
    check_byte_limit(data)
    if b"%PDF-" not in data[:1024] or b"%%EOF" not in data[-1024:]:
        raise APIError(422, "invalid_pdf", "손상되었거나 PDF 형식이 아닌 파일입니다.")

    pages, warnings = [], []
    try:
        for number, layout in enumerate(extract_pages(io.BytesIO(data), laparams=LAParams(all_texts=True)), 1):
            if number > MAX_PDF_PAGES:
                raise APIError(422, "too_many_pdf_pages", "PDF는 최대 1,000페이지까지 허용합니다.")
            text = "".join(_text_blocks(layout)).replace("\x00", "")
            pages.append(DocumentPage(page_number=number, text=text))
            if not text.strip():
                warnings.append(f"{number}페이지에서 텍스트를 추출하지 못했습니다.")
    except APIError:
        raise
    except Exception:
        raise APIError(422, "invalid_pdf", "PDF를 읽을 수 없습니다.") from None
    if pages and len(warnings) == len(pages):
        raise APIError(422, "no_pdf_text", "PDF에 추출 가능한 텍스트가 없습니다.")
    if not pages:
        raise APIError(422, "invalid_pdf", "PDF 페이지를 읽을 수 없습니다.")
    return pages, warnings


def _metadata(row) -> DocumentMetadata:
    return DocumentMetadata(**dict(zip(METADATA_COLUMNS.split(", "), row)))


class DocumentStore:
    """Owns a PostgreSQL document catalog and its corresponding local PDF files."""

    def __init__(self, database_url: str, upload_dir: Path):
        self.database_url = database_url
        self.upload_dir = Path(upload_dir)

    @contextmanager
    def _transaction(self):
        try:
            with closing(psycopg2.connect(self.database_url, connect_timeout=3)) as conn:
                with conn, conn.cursor() as cursor:
                    yield cursor
        except psycopg2.errors.UniqueViolation as error:
            if error.diag.constraint_name == "web_documents_content_hash_unique":
                raise APIError(409, "duplicate_document", "같은 PDF가 이미 보관되어 있습니다.") from None
            raise self._storage_error() from None
        except psycopg2.OperationalError:
            raise APIError(503, "documents_unavailable", "문서 보관소에 연결할 수 없습니다.") from None
        except psycopg2.Error:
            raise self._storage_error() from None

    @staticmethod
    def _storage_error() -> APIError:
        return APIError(500, "document_storage_error", "문서 보관소 작업에 실패했습니다.")

    def initialize(self) -> None:
        try:
            self.upload_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            with self._transaction() as cursor:
                cursor.execute(SCHEMA)
        except OSError:
            raise self._storage_error() from None

    def health(self) -> bool:
        if not self.upload_dir.is_dir() or not os.access(self.upload_dir, os.R_OK | os.W_OK | os.X_OK):
            return False
        try:
            with self._transaction() as cursor:
                cursor.execute(f"SELECT {METADATA_COLUMNS} FROM web_documents LIMIT 0")
                cursor.execute("SELECT document_id, page_number, text FROM web_document_pages LIMIT 0")
        except APIError:
            return False
        return True

    def list(self, *, include_private: bool = False) -> list[DocumentMetadata]:
        with self._transaction() as cursor:
            cursor.execute(
                f"SELECT {METADATA_COLUMNS} FROM web_documents "
                "WHERE (%s OR visibility = 'public') ORDER BY created_at DESC, id",
                (include_private,),
            )
            return [_metadata(row) for row in cursor.fetchall()]

    def get(self, document_id: str, *, include_private: bool = False,
            include_pages: bool = False) -> SourceDocument | None:
        with self._transaction() as cursor:
            lock = " FOR SHARE" if include_pages else ""
            cursor.execute(
                f"SELECT {METADATA_COLUMNS} FROM web_documents "
                f"WHERE id = %s AND (%s OR visibility = 'public'){lock}",
                (document_id, include_private),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            pages = []
            if include_pages:
                cursor.execute(
                    "SELECT page_number, text FROM web_document_pages WHERE document_id = %s ORDER BY page_number",
                    (document_id,),
                )
                pages = [DocumentPage(page_number=number, text=text) for number, text in cursor.fetchall()]
            return SourceDocument(metadata=_metadata(row), pages=pages)

    def file_path(self, document_id: str) -> Path:
        if not re.fullmatch(DOCUMENT_ID_PATTERN, document_id):
            raise APIError(422, "invalid_input", "문서 ID 형식이 올바르지 않습니다.")
        return self.upload_dir / f"{document_id}.pdf"

    def save(self, data: bytes, filename: str, *, title: str | None = None,
             visibility: Visibility = "private") -> DocumentMetadata:
        name = display_filename(filename)
        if PureWindowsPath(name).suffix.lower() != ".pdf":
            raise APIError(415, "unsupported_file_type", "PDF 파일만 보관할 수 있습니다.")
        if visibility not in ("private", "public"):
            raise APIError(422, "invalid_input", "문서 공개 범위가 올바르지 않습니다.")
        cleaned_title = "".join(char for char in (title or "").strip() if char.isprintable())
        if len(cleaned_title) > 200:
            raise APIError(422, "invalid_input", "제목은 최대 200자까지 허용합니다.")
        pages, warnings = parse_pdf(data)
        metadata = DocumentMetadata(
            id=uuid4().hex,
            title=cleaned_title or PureWindowsPath(name).stem[:200],
            filename=name, visibility=visibility, content_hash=hashlib.sha256(data).hexdigest(),
            page_count=len(pages), warnings=warnings,
        )
        path = self.file_path(metadata.id)
        created = False
        try:
            with self._transaction() as cursor:
                cursor.execute(
                    f"INSERT INTO web_documents ({METADATA_COLUMNS}) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (metadata.id, metadata.title, metadata.filename, metadata.visibility, metadata.content_hash,
                     metadata.page_count, metadata.warnings),
                )
                execute_values(
                    cursor, "INSERT INTO web_document_pages (document_id, page_number, text) VALUES %s",
                    [(metadata.id, page.page_number, page.text) for page in pages],
                )
                self.upload_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
                with path.open("xb") as output:
                    created = True
                    output.write(data)
                    output.flush()
                    os.fsync(output.fileno())
        except (APIError, OSError) as error:
            if created:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    LOGGER.error("Could not clean up a failed PDF insert for %s", metadata.id)
            if isinstance(error, APIError):
                raise
            raise self._storage_error() from None
        return metadata

    def delete(self, document_id: str) -> bool:
        path = self.file_path(document_id)
        pending = path.with_name(f".{path.name}.deleting")
        try:
            with self._transaction() as cursor:
                cursor.execute("SELECT id FROM web_documents WHERE id = %s FOR UPDATE", (document_id,))
                if cursor.fetchone() is None:
                    return False
                # A hidden backup preserves the bytes until the database commits.
                if path.exists():
                    path.replace(pending)
                cursor.execute("DELETE FROM web_documents WHERE id = %s", (document_id,))
        except (APIError, OSError) as error:
            if pending.exists() and not path.exists():
                with suppress(OSError):
                    pending.replace(path)
            if isinstance(error, APIError):
                raise
            raise self._storage_error() from None
        try:
            pending.unlink(missing_ok=True)
        except OSError:
            LOGGER.error("PDF deletion committed, but its recovery file still exists for %s", document_id)
        return True


def main() -> None:
    from hsrule_web.settings import Settings

    settings = Settings.from_env()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL is required to initialize the document store.")
    try:
        DocumentStore(settings.database_url, settings.upload_dir).initialize()
    except APIError as error:
        raise SystemExit(error.detail) from None
    print("Document store initialized.")


if __name__ == "__main__":
    main()
