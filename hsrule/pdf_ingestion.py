import hashlib
import io
import re
from pathlib import PureWindowsPath
from uuid import uuid4

from pdfminer.high_level import extract_pages
from pdfminer.layout import LAParams, LTContainer, LTTextContainer

MAX_PDF_BYTES = 20 * 1024 * 1024
MAX_PDF_PAGES = 1000
ARTICLE_HEADER = re.compile(
    r"^[ \t]*(제[ \t]*\d+[ \t]*조(?:의[ \t]*\d+)?"
    r"(?:[ \t]*\([^()\n]+\)|(?=[ \t]*$)))", re.MULTILINE
)


class PDFValidationError(ValueError):
    """The supplied PDF cannot be indexed."""


def _text_blocks(layout):
    for element in layout:
        if isinstance(element, LTTextContainer):
            yield element.get_text()
        elif isinstance(element, LTContainer):
            yield from _text_blocks(element)


def load_pdf(data: bytes, filename: str, title: str | None = None,
             visibility: str = "private", document_id: str | None = None) -> dict:
    if visibility not in ("private", "public"):
        raise PDFValidationError("공개 범위가 올바르지 않습니다.")
    if len(data) > MAX_PDF_BYTES:
        raise PDFValidationError("PDF는 최대 20 MiB까지 허용합니다.")
    if b"%PDF-" not in data[:1024] or b"%%EOF" not in data[-1024:]:
        raise PDFValidationError("손상된 PDF이거나 PDF 형식이 아닙니다.")

    pages, indexed_pages, warnings = [], [], []
    try:
        layouts = extract_pages(io.BytesIO(data), laparams=LAParams(all_texts=True))
        for number, layout in enumerate(layouts, 1):
            if number > MAX_PDF_PAGES:
                raise PDFValidationError("PDF는 최대 1,000페이지까지 허용합니다.")
            text = "".join(_text_blocks(layout)).rstrip("\n")
            pages.append({"page_number": number, "text": text})
            if text.strip():
                indexed_pages.append(number)
            else:
                warnings.append(f"{number}페이지에서 텍스트를 추출하지 못했습니다.")
    except PDFValidationError:
        raise
    except Exception as exc:
        raise PDFValidationError("PDF를 읽을 수 없습니다.") from exc
    if not indexed_pages:
        raise PDFValidationError("PDF에 추출 가능한 텍스트가 없습니다.")

    name = PureWindowsPath(filename).name or "document.pdf"
    return {
        "id": document_id or uuid4().hex, "title": title or PureWindowsPath(name).stem,
        "filename": name, "visibility": visibility,
        "content_hash": hashlib.sha256(data).hexdigest(), "page_count": len(pages),
        "pages": pages, "indexed_pages": indexed_pages, "chunks": [], "warnings": warnings,
    }


def _sections(text, article):
    start = 0
    for header in ARTICLE_HEADER.finditer(text):
        if text[start:header.start()].strip():
            yield text[start:header.start()], article
        article = header.group(1)
        start = header.start()
    if text[start:].strip():
        yield text[start:], article


def _windows(section, title, article, tokenizer, max_tokens, overlap_tokens):
    # Use source offsets, since decoding subwords can change Korean text and whitespace.
    offsets = tokenizer(section, add_special_tokens=False, return_offsets_mapping=True)["offset_mapping"]
    if not offsets:
        return
    prefix = ""
    for label in (f"[{title}] {article}".strip(), article):
        if label and len(tokenizer.encode(label + "\n")) <= max_tokens // 2:
            prefix = label + "\n"
            break
    budget = max_tokens - len(tokenizer.encode(prefix))
    if budget < 1:
        raise ValueError("max_tokens가 본문 한 토큰을 담기에 부족합니다.")
    start = 0
    while start < len(offsets):
        end = min(start + budget, len(offsets))
        while end > start:
            left = offsets[start][0] if start else 0
            right = offsets[end][0] if end < len(offsets) else len(section)
            content = section[left:right].strip()
            if len(tokenizer.encode(prefix + content)) <= max_tokens:
                break
            end -= 1
        else:
            raise ValueError("본문을 지정한 토큰 한도 안에서 나눌 수 없습니다.")
        if content:
            yield content, prefix + content
        if end == len(offsets):
            break
        start = max(start + 1, end - overlap_tokens)


def chunk_pages(document: dict, tokenizer, max_tokens: int = 128, overlap_tokens: int = 16) -> list[dict]:
    if not 0 <= overlap_tokens < max_tokens:
        raise ValueError("overlap_tokens는 0 이상이고 max_tokens보다 작아야 합니다.")
    chunks = []
    article = ""
    for page in document["pages"]:
        number = page["page_number"]
        for section, article in _sections(page["text"], article):
            for content, embedding_text in _windows(section, document["title"], article, tokenizer,
                                                    max_tokens, overlap_tokens):
                ordinal = len(chunks)
                chunks.append({
                    "id": f"{document['id']}:{number}:{ordinal}", "ordinal": ordinal,
                    "document_id": document["id"], "page_start": number, "page_end": number,
                    "article": article, "content": content, "embedding_text": embedding_text,
                })
    return chunks
