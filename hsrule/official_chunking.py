"""공식 규정 본문의 조항·표·부칙 전처리와 토큰 청킹 (#23).

입력은 크롤러(#16/#17)가 검증해 발행한 공식 규정 본문이다. 페이지 번호는 만들지 않고,
규정/개정 ID·공식 URL·조항 ID·원문 범위(source_start/source_end)만 유지한다.
"""
import hashlib
import re

ARTICLE_HEADER = re.compile(
    r"^[ \t]*제[ \t]*\d+[ \t]*조(?:[ \t]*의[ \t]*\d+)?(?:[ \t]+\d+)?(?=[ \t]*(?:\(|$))", re.MULTILINE
)
CHAPTER_HEADER = re.compile(r"^[ \t]*제[ \t]*\d+[ \t]*[장절](?=[ \t]|$)[^\n]*", re.MULTILINE)
ADDENDUM_HEADER = re.compile(r"^[ \t]*부[ \t]*칙(?:[ \t]*\(([^)\n]*)\))?[ \t]*$", re.MULTILINE)
ATTACHMENT_HEADER = re.compile(r"^[ \t]*\[별표/서식 파일\][ \t]*$", re.MULTILINE)
REFERENCE = re.compile(r"제[ \t]*(\d+)[ \t]*조(?:[ \t]*의[ \t]*(\d+))?")
APPENDIX_REFERENCE = re.compile(r"<?[ \t]*(별표)[ \t]*([0-9]+|[ⅠⅡⅢⅣⅤ])[ \t]*>?")
DELETED = re.compile(r"^[ \t]*[(（]?삭제")


class ChunkingError(ValueError):
    """공식 규정 본문을 청킹할 수 없다."""


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _article_id(header: str) -> str:
    return _compact(header)


def _boundaries(text: str) -> list[tuple[int, str, re.Match]]:
    found = []
    for kind, pattern in (("article", ARTICLE_HEADER), ("chapter", CHAPTER_HEADER),
                          ("addendum", ADDENDUM_HEADER), ("attachments", ATTACHMENT_HEADER)):
        found.extend((match.start(), kind, match) for match in pattern.finditer(text))
    found.sort(key=lambda item: item[0])
    unique = []
    for item in found:  # 같은 줄에서 겹치는 패턴은 먼저 잡힌 것만 사용한다.
        if unique and item[0] == unique[-1][0]:
            continue
        unique.append(item)
    return unique


def split_units(text: str) -> list[dict]:
    """본문을 조항/부칙/별표 목록 단위로 나눈다. 모든 문자가 정확히 한 단위에 속한다."""
    units, pending_start, chapter = [], None, ""
    addendum_label, addendum_count = "", 0

    def close(end):
        if units and units[-1]["end"] is None:
            units[-1]["end"] = end

    for start, kind, match in _boundaries(text):
        if kind == "chapter":
            close(start)
            if pending_start is None:
                pending_start = start
            chapter = re.sub(r"\s+", " ", match.group(0)).strip()
            continue
        unit_start = start if pending_start is None else pending_start
        close(unit_start)
        pending_start = None
        if kind == "addendum":
            addendum_count += 1
            date = _compact(match.group(1) or "")
            addendum_label = f"부칙({date})" if date else f"부칙#{addendum_count}"
            units.append({"start": unit_start, "end": None, "kind": "addendum", "article_id": addendum_label,
                          "header": match.group(0).strip(), "chapter": chapter})
        elif kind == "attachments":
            addendum_label = ""
            units.append({"start": unit_start, "end": None, "kind": "attachments", "article_id": "별표/서식",
                          "header": "[별표/서식 파일]", "chapter": chapter})
        else:
            article = _article_id(match.group(0))
            if addendum_label:  # 부칙 안의 조는 본칙 조와 구분한다.
                article = f"{addendum_label}{article}"
            units.append({"start": unit_start, "end": None, "kind": "addendum" if addendum_label else "article",
                          "article_id": article, "header": re.sub(r"\s+", " ", match.group(0)).strip(),
                          "chapter": chapter})
    close(len(text))
    if pending_start is not None:  # 조항 없이 끝나는 장/절 제목도 버리지 않는다.
        units.append({"start": pending_start, "end": len(text), "kind": "heading", "article_id": chapter,
                      "header": chapter, "chapter": chapter})
    first = units[0]["start"] if units else len(text)
    if text[:first].strip():
        units.insert(0, {"start": 0, "end": first, "kind": "preamble", "article_id": "", "header": "", "chapter": ""})
    elif units:
        units[0]["start"] = 0  # 앞쪽 공백도 첫 단위에 포함해 범위를 끊김 없이 유지한다.
    return units


def extract_references(content: str, own_article: str = "") -> list[str]:
    """조 교차 참조(제N조, 제N조의M)와 별표 참조를 정규화해 등장 순서대로 반환한다."""
    refs = []
    for match in REFERENCE.finditer(content):
        ref = f"제{match.group(1)}조" + (f"의{match.group(2)}" if match.group(2) else "")
        refs.append(ref)
    for match in APPENDIX_REFERENCE.finditer(content):
        refs.append(f"별표{match.group(2)}")
    own = own_article.split(")")[-1]
    return [ref for ref in dict.fromkeys(refs) if ref != own]


def _prefix(title: str, unit: dict, tokenizer, max_tokens: int) -> str:
    labels = []
    if unit["kind"] in ("article", "addendum"):
        labels.append(f"[{title}] {unit['article_id']} {unit['header']}".strip())
        labels.append(f"[{title}] {unit['article_id']}")
    else:
        labels.append(f"[{title}] {unit['article_id']}".strip())
    labels.append(f"[{title}]")
    for label in labels:
        if len(tokenizer.encode(label + "\n")) <= max_tokens // 2:
            return label + "\n"
    return ""


def _tight(text: str, left: int, right: int) -> tuple[int, int]:
    while left < right and text[left].isspace():
        left += 1
    while right > left and text[right - 1].isspace():
        right -= 1
    return left, right


def _windows(text, unit, prefix, tokenizer, max_tokens, overlap_tokens):
    section_left, section_right = _tight(text, unit["start"], unit["end"])
    if section_left == section_right:
        return
    section = text[section_left:section_right]
    offsets = tokenizer(section, add_special_tokens=False, return_offsets_mapping=True)["offset_mapping"]
    if not offsets:
        return
    budget = max_tokens - len(tokenizer.encode(prefix))
    if budget < 1:
        raise ChunkingError("max_tokens가 본문 한 토큰을 담기에 부족합니다.")
    start = 0
    while start < len(offsets):
        end = min(start + budget, len(offsets))
        while end > start:
            left = offsets[start][0] if start else 0
            right = offsets[end][0] if end < len(offsets) else len(section)
            if end < len(offsets):  # 줄/항 경계가 있으면 창의 뒤쪽 절반에서 그 경계로 맞춘다.
                for candidate in range(end, start + max(1, (end - start) // 2), -1):
                    cut = offsets[candidate][0]
                    if section[cut - 1:cut] == "\n":
                        right, end = cut, candidate
                        break
            tight_left, tight_right = _tight(section, left, right)
            if len(tokenizer.encode(prefix + section[tight_left:tight_right])) <= max_tokens:
                break
            end -= 1
        else:
            raise ChunkingError("본문을 지정한 토큰 한도 안에서 나눌 수 없습니다.")
        if tight_left < tight_right:
            yield section_left + tight_left, section_left + tight_right
        if end == len(offsets):
            break
        start = max(start + 1, end - overlap_tokens)


def _is_deleted(unit: dict, body: str) -> bool:
    if unit["kind"] != "article":
        return False
    lines = body.splitlines()
    for index, line in enumerate(lines):
        if ARTICLE_HEADER.match(line):
            rest = [item for item in lines[index + 1:] if item.strip()]
            return bool(rest) and all(DELETED.match(item) for item in rest)
    return False


def chunk_regulation(regulation: dict, tokenizer, max_tokens: int = 128, overlap_tokens: int = 16) -> list[dict]:
    """검증된 공식 규정 본문 하나를 청크 목록으로 만든다.

    regulation: regulation_id, revision_id, title, official_url, text
    """
    if not 0 <= overlap_tokens < max_tokens:
        raise ChunkingError("overlap_tokens는 0 이상이고 max_tokens보다 작아야 합니다.")
    for key in ("regulation_id", "revision_id", "title", "official_url", "text"):
        if not regulation.get(key):
            raise ChunkingError(f"공식 규정 입력에 {key}가 없습니다.")
    text = regulation["text"]
    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    chunks = []
    for unit in split_units(text):
        prefix = _prefix(regulation["title"], unit, tokenizer, max_tokens)
        for source_start, source_end in _windows(text, unit, prefix, tokenizer, max_tokens, overlap_tokens):
            content = text[source_start:source_end]
            ordinal = len(chunks)
            body = text[unit["start"]:unit["end"]]
            chunks.append({
                "id": f"{regulation['revision_id']}:{ordinal}", "ordinal": ordinal,
                "regulation_id": regulation["regulation_id"], "revision_id": regulation["revision_id"],
                "official_url": regulation["official_url"], "text_hash": text_hash,
                "kind": unit["kind"], "article_id": unit["article_id"], "chapter": unit["chapter"],
                "unit_start": unit["start"], "unit_end": unit["end"],
                "source_start": source_start, "source_end": source_end,
                "content": content, "embedding_text": prefix + content,
                "references": extract_references(content, unit["article_id"]),
                "deleted": _is_deleted(unit, text[unit["start"]:unit["end"]]),
            })
    return chunks


def verify_chunks(regulation: dict, chunks: list[dict], tokenizer, max_tokens: int = 128) -> list[str]:
    """누락·중복·잘림·한도 초과를 찾는다. 문제가 없으면 빈 목록을 반환한다."""
    text, problems = regulation["text"], []
    covered = [False] * len(text)
    ids = set()
    for chunk in chunks:
        if chunk["id"] in ids:
            problems.append(f"중복 청크 ID: {chunk['id']}")
        ids.add(chunk["id"])
        start, end = chunk["source_start"], chunk["source_end"]
        if text[start:end] != chunk["content"]:
            problems.append(f"원문 범위와 내용이 다릅니다: {chunk['id']}")
        if not chunk["unit_start"] <= start < end <= chunk["unit_end"]:
            problems.append(f"청크가 조항 경계를 넘었습니다: {chunk['id']}")
        if len(tokenizer.encode(chunk["embedding_text"])) > max_tokens:
            problems.append(f"토큰 한도 초과: {chunk['id']}")
        for index in range(start, end):
            covered[index] = True
    missing = [i for i, char in enumerate(text) if not char.isspace() and not covered[i]]
    if missing:
        problems.append(f"원문 {len(missing)}자가 어떤 청크에도 포함되지 않았습니다 (첫 위치 {missing[0]}).")
    return problems
