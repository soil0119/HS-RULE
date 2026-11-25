"""공식 규정 본문(lawFullContent) DOM 파서.

조항 경계는 텍스트 정규식이 아니라 DOM(.JO/.article, .addenda+본문 블록)으로 정한다. 본문 줄이
"제123조에 따른 …"처럼 시작해도 새 조항으로 나누지 않는다. 출력 text는 RAG 청커
(hsrule.official_chunking)가 읽는 줄 형식을 따르고, DOM 기준 조항 범위를 articles에 함께 남긴다.
"""
import re

from bs4 import NavigableString, Tag

from .catalog import same_title
from .pages import PageError, normalize_space, service_soup

ATTACHMENT_HEADER = "[별표/서식 파일]"  # 청커의 별표 목록 경계 표기와 같다.
_FILE_DOWN = re.compile(r"fileDown\(\s*'(\d+)'\s*,\s*'attach'\s*\)")
_DELETED = re.compile(r"^[(（]?삭제")
_ADDENDUM = re.compile(r"^부\s*칙(?:\s*\(([^)]*)\))?$")
HEADING_CLASSES = ("chapter", "section", "subsection")  # 장·절·관
_HEADING_SELECTOR = ", ".join(f".{name}" for name in HEADING_CLASSES)


def _classes(tag: Tag) -> list[str]:
    return tag.get("class") or []


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value)


def article_label(heading: str) -> str:
    """'제 3 조의 2(계약…)' → '제3조의2'. 괄호 제목 앞까지를 공백 없이 쓴다."""
    return _compact(heading.split("(", 1)[0])


def _table_lines(table: Tag) -> list[str]:
    """표는 행마다 한 줄로, 칸은 ' | '로 잇는다. 빈 칸은 순서를 지키려 빈 문자열로 둔다."""
    lines = []
    for row in table.find_all("tr"):
        if row.find_parent("table") is not table:
            continue  # 표 안의 표는 해당 칸의 텍스트로 이미 포함된다.
        cells = [normalize_space(cell.get_text(" ")) for cell in row.find_all(["td", "th"], recursive=False)]
        if any(cells):
            lines.append(" | ".join(cells))
    return lines


def _block_lines(element: Tag) -> list[str]:
    """블록 하나를 줄 목록으로 바꾼다. 표가 섞여 있으면 표 앞뒤 텍스트와 표 행을 순서대로 낸다."""
    if element.name == "table":
        return _table_lines(element)
    if element.find("table") is None:
        text = normalize_space(element.get_text(" "))
        return [text] if text else []
    lines, pending = [], []

    def flush():
        text = normalize_space(" ".join(pending))
        if text:
            lines.append(text)
        pending.clear()

    for child in element.children:
        if isinstance(child, NavigableString):
            pending.append(str(child))
        elif isinstance(child, Tag):
            if child.name == "table" or child.find("table") is not None:
                flush()
                lines.extend(_block_lines(child))
            else:
                pending.append(child.get_text(" "))
    flush()
    return lines


class _Builder:
    """줄을 이어 붙이며 각 조항의 원문 범위(start/end)를 기록한다."""

    def __init__(self):
        self.lines: list[str] = []
        self.length = 0
        self.articles: list[dict] = []

    def add(self, line: str) -> int:
        start = self.length + (1 if self.lines else 0)
        self.lines.append(line)
        self.length = start + len(line)
        return start

    def open_article(self, item: dict, heading: str):
        self.close_article()
        item["start"] = self.add(heading)
        item["end"] = None
        self.articles.append(item)

    def close_article(self):
        if self.articles and self.articles[-1]["end"] is None:
            self.articles[-1]["end"] = self.length

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def _attachments(block: Tag) -> list[dict]:
    found = []
    for span in block.select(".attach_file"):
        name = normalize_space(span.get_text(" "))
        link = span.find_next("a", href=_FILE_DOWN)
        file_id = _FILE_DOWN.search(link["href"]).group(1) if link else ""
        if name:
            found.append({"name": name, "file_id": file_id})
    return found


def parse_content(html: str, expected_title: str) -> dict:
    """본문 HTML을 조항형 자료로 바꾼다. 요청한 규정이 아니거나 본문이 없으면 PageError."""
    soup = service_soup(html)
    root = soup.select_one("#lawcontent")
    if root is None:
        raise PageError("not_content", "#lawcontent가 없습니다(메뉴·목록 화면)")
    name_tag = root.select_one(".lawname")
    title = normalize_space(name_tag.get_text(" ")) if name_tag else ""
    if not title:
        raise PageError("empty_content", "규정명이 없는 빈 본문")
    # 사이트는 SEQ가 틀려도 SEQ_HISTORY만 보고 다른 규정을 200으로 돌려준다. 제목으로 대조한다.
    if not same_title(title, expected_title):
        raise PageError("title_mismatch", f"요청 {expected_title!r}, 응답 {title!r}")

    out = _Builder()
    out.add(title)
    chapter, warnings, attachments, history_notes = "", [], [], []
    pending_addendum = None
    addendum_count = 0

    for block in root.find_all(recursive=False):
        if not isinstance(block, Tag) or block.name != "div":
            continue
        classes = _classes(block)
        if "lawname" in classes:
            continue
        if pending_addendum is not None:  # 부칙 제목 다음 블록이 그 부칙의 본문이다.
            item, pending_addendum = pending_addendum, None
            if not classes and block.get("id"):
                item["id"] = block["id"]
                for child in block.find_all(recursive=False):
                    for line in _block_lines(child):
                        out.add(line)
                continue
            warnings.append(f"부칙 본문 블록이 없습니다: {item['heading']}")
        if "history" in classes:  # 규정명 아래 제정·개정 연혁 줄('2005. 4.12. 제정')
            note = normalize_space(block.get_text(" "))
            if note:
                out.close_article()
                history_notes.append(note)
                out.add(note)
            continue
        if any(name in classes for name in HEADING_CLASSES):  # JO 밖에 놓인 장·절·관 제목
            out.close_article()
            heading = normalize_space(block.get_text(" "))
            if "chapter" in classes:
                chapter = heading
            if heading:
                out.add(heading)
            continue
        if "JO" in classes:
            chapter_tag = block.select_one(_HEADING_SELECTOR)
            heading_cell = block.select_one(".article td:nth-of-type(2)")
            heading = normalize_space(heading_cell.get_text(" ")) if heading_cell is not None else ""
            if heading_cell is not None and not heading:
                # 제목 없는 조항 틀에 연혁 메모('(2020.4.9. 제정)')만 담긴 경우. 조항이 아니다.
                out.close_article()
                for child in block.find_all(recursive=False):
                    if "article" in _classes(child):
                        continue
                    for line in _block_lines(child):
                        history_notes.append(line)
                        out.add(line)
                continue
            if heading:
                out.open_article({"id": block.get("id", ""), "kind": "article", "label": article_label(heading),
                                  "heading": heading, "chapter": chapter}, heading)
                body = []
                for child in block.find_all(recursive=False):
                    if "article" in _classes(child):
                        continue
                    body.extend(_block_lines(child))
                for line in body:
                    out.add(line)
                # 본문이 모두 "삭제"이거나, 본문 없이 제목에 삭제를 적은 범위 조문("제95조의4 ~ … (삭제 …)")
                out.articles[-1]["deleted"] = (all(_DELETED.match(line) for line in body) if body
                                               else "삭제" in heading)
            elif chapter_tag is not None:
                out.close_article()
                heading = normalize_space(chapter_tag.get_text(" "))
                if "chapter" in _classes(chapter_tag):
                    chapter = heading
                out.add(heading)
            elif normalize_space(block.get_text(" ")):
                warnings.append(f"분류할 수 없는 조문 블록: {normalize_space(block.get_text(' '))[:40]!r}")
                out.add(normalize_space(block.get_text(" ")))
            continue
        if "addenda" in classes:
            heading = normalize_space(block.get_text(" "))
            match = _ADDENDUM.match(heading)
            if not match:
                raise PageError("parse_error", f"부칙 제목을 해석할 수 없습니다: {heading!r}")
            addendum_count += 1
            date = _compact(match.group(1) or "")
            label = f"부칙({date})" if date else f"부칙#{addendum_count}"
            pending_addendum = {"id": "", "kind": "addendum", "label": label, "heading": heading,
                                "chapter": "", "deleted": False}
            out.open_article(pending_addendum, heading)
            continue
        if "article" in classes:  # 문서 끝의 별표·서식 첨부 목록
            attachments.extend(_attachments(block))
            continue
        unknown = normalize_space(block.get_text(" "))
        if unknown:  # 모르는 블록도 버리지 않는다. 경고와 함께 본문에 남긴다.
            warnings.append(f"분류할 수 없는 블록: {unknown[:40]!r}")
            out.add(unknown)
    out.close_article()
    if pending_addendum is not None:
        warnings.append(f"부칙 본문 블록이 없습니다: {pending_addendum['heading']}")

    if attachments:
        out.add(ATTACHMENT_HEADER)
        for attachment in attachments:
            out.add(attachment["name"])
    if not out.articles:
        raise PageError("shell", "조문·부칙이 하나도 없는 본문")
    text = out.text
    warnings.extend(boundary_warnings(text, out.articles))
    return {"title": title, "text": text, "articles": out.articles, "attachments": attachments,
            "history_notes": history_notes, "warnings": warnings}


def boundary_warnings(text: str, articles: list[dict]) -> list[str]:
    """RAG 청커의 줄 정규식이 DOM 조항 경계와 다르게 나누는 위치를 찾는다.

    청커는 텍스트만 보므로, 본문 줄이 '제5조(…)'처럼 시작하면 새 조항으로 오인한다. 부칙 안의 조는
    청커도 부칙 조로 다루므로 본칙 조항만 비교한다.
    """
    from hsrule.official_chunking import ARTICLE_HEADER

    addenda = [(item["start"], item["end"]) for item in articles if item["kind"] == "addendum"]
    dom_starts = {item["start"] for item in articles if item["kind"] == "article"}
    found = set()
    for match in ARTICLE_HEADER.finditer(text):
        line_start = text.rfind("\n", 0, match.start()) + 1
        if any(start <= line_start < end for start, end in addenda):
            continue
        found.add(line_start)
    warnings = []
    for offset in sorted(found - dom_starts):
        line = text[offset:text.find("\n", offset) if "\n" in text[offset:] else len(text)]
        warnings.append(f"본문 줄이 청커에서 조항 시작으로 인식됩니다(offset {offset}): {line[:40]!r}")
    for offset in sorted(dom_starts - found):
        line = text[offset:text.find("\n", offset) if "\n" in text[offset:] else len(text)]
        warnings.append(f"DOM 조항 제목을 청커가 인식하지 못합니다(offset {offset}): {line[:40]!r}")
    return warnings
