"""공식 규정 본문의 조항·표·부칙 전처리와 토큰 청킹 (#23).

입력은 크롤러(#16/#17)가 검증해 발행한 공식 규정 본문이다. 페이지 번호는 만들지 않고,
규정/개정 ID·공식 URL·조항 ID·원문 범위(source_start/source_end)만 유지한다.
"""
import hashlib
import json
import re

ARTICLE_HEADER = re.compile(
    r"^[ \t]*제[ \t]*\d+[ \t]*조(?:[ \t]*의[ \t]*\d+)?(?:[ \t]+\d+)?(?=[ \t]*(?:\(|$))", re.MULTILINE
)
CHAPTER_HEADER = re.compile(r"^[ \t]*제[ \t]*\d+[ \t]*[장절](?=[ \t]|$)[^\n]*", re.MULTILINE)
# DOM 범위를 쓸 때는 관(款) 제목도 장·절처럼 다음 조항의 소속 표시로 쓴다(#60, SEQ 18 정관 '제 1 관 임 용').
DOM_SECTION_HEADER = re.compile(r"^[ \t]*제[ \t]*\d+[ \t]*[장절관](?=[ \t]|$)[^\n]*", re.MULTILINE)
DOM_KINDS = ("article", "addendum")
ADDENDUM_HEADER = re.compile(r"^[ \t]*부[ \t]*칙(?:[ \t]*\(([^)\n]*)\))?[ \t]*$", re.MULTILINE)
ATTACHMENT_HEADER = re.compile(r"^[ \t]*\[별표/서식 파일\][ \t]*$", re.MULTILINE)
REFERENCE = re.compile(r"제[ \t]*(\d+)[ \t]*조(?:[ \t]*의[ \t]*(\d+))?")
APPENDIX_REFERENCE = re.compile(r"<?[ \t]*(별표)[ \t]*([0-9]+|[ⅠⅡⅢⅣⅤ])[ \t]*>?")
DELETED = re.compile(r"^[ \t]*[(（]?삭제")
# 삭제 표시(#53). '삭제' 뒤에는 괄호 안 주석(개정 날짜 등)만 허용한다. '삭제 절차'처럼 다른 말이 이어지면 삭제로 보지 않는다.
_OPEN, _CLOSE = "(（<〈《\\[", ")）>〉》\\]"
_ANNOTATION = rf"[{_OPEN}][^{_CLOSE}\n]*[{_CLOSE}]"
STRICT_DELETED = re.compile(
    rf"^[ \t]*(?:[①-⑳][ \t]*)?(?:[{_OPEN}][ \t]*삭제[ \t]*[\d.\s,~\-년월일]*[{_CLOSE}]|삭제)"
    rf"(?:[ \t]*{_ANNOTATION})*[ \t]*$")
ANNOTATION_ONLY = re.compile(rf"^[ \t]*{_ANNOTATION}(?:[ \t]*{_ANNOTATION})*[ \t]*$")
TITLE_AND_TAIL = re.compile(r"^[ \t]*[(（]([^)）\n]*)[)）][ \t]*(.*)$")
# 참조 앞에 붙은 대상 이름(#57). 이름은 참조 바로 앞 한 낱말이다(띄어쓰기로 구분).
_LAW = r"(?:법률|법령|시행령|시행규칙|(?<![방문기수요어편용화해])법)"
_REGULATION = r"(?:학칙|정관|시행세칙|세칙|규정|규칙|지침|내규|요령)"
TARGET_NAME = re.compile(
    rf"(?:^|(?<=[\s(（「『]))(?P<demo>(?:이|본|당|동|같은|위)[ \t]+)?"
    rf"(?P<name>[가-힣A-Za-z0-9·]*?(?P<suffix>{_LAW}|{_REGULATION}))[ \t]*[」』]?[ \t]*(?:의[ \t]*)?$")
ATTACHED_DEMO = re.compile(rf"^(?:이|본|당|동)(?:{_LAW}|{_REGULATION})$")
BARE_UNIQUE = ("학칙", "정관")  # 대학에 하나뿐이라 이름만으로 대상이 정해진다
# 연속 참조('학칙 제37조제2항 및 제38조')의 이음말
CHAIN_JOINERS = re.compile(r"^(?:[\s,·ㆍ~\-]|제[ \t]*\d+[ \t]*[항호]|및|와|과|또는|부터|내지)*$")
OWN_ARTICLE = re.compile(r"제\d+조(?:의\d+)?$")

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


def validate_dom_articles(text: str, articles) -> list[dict]:
    """수집기 스냅샷의 DOM 조항 범위(#60)를 검사한다. 잘못되면 ChunkingError로 그 규정만 색인을 거부한다.

    각 항목: id(사이트 조문 ID), kind(article|addendum), label(제3조의2·부칙(2016.2.3.공포) 등), heading,
    start·end(text[start:end]가 그 조문), deleted. 범위는 겹치지 않고 순서대로이며, 범위는 heading으로 시작해야 한다.
    """
    if not isinstance(articles, list) or not articles:
        raise ChunkingError("스냅샷 articles가 비어 있습니다.")
    checked, seen, previous_end = [], set(), 0
    for index, item in enumerate(articles):
        if not isinstance(item, dict):
            raise ChunkingError(f"articles[{index}]가 객체가 아닙니다.")
        source_id, kind, label = str(item.get("id") or ""), item.get("kind"), _compact(str(item.get("label") or ""))
        start, end = item.get("start"), item.get("end")
        where = f"articles[{index}]({source_id or label or '?'})"
        if not source_id or source_id in seen:
            raise ChunkingError(f"{where}: 조문 ID가 없거나 중복입니다.")
        if kind not in DOM_KINDS or not label:
            raise ChunkingError(f"{where}: kind 또는 label이 올바르지 않습니다.")
        if not (isinstance(start, int) and isinstance(end, int) and previous_end <= start < end <= len(text)):
            raise ChunkingError(f"{where}: 범위가 본문 밖이거나 앞 조문과 겹칩니다.")
        heading = _compact(str(item.get("heading") or ""))
        if heading and not _compact(text[start:end]).startswith(heading):
            raise ChunkingError(f"{where}: 범위가 조문 제목({item.get('heading')})으로 시작하지 않습니다.")
        seen.add(source_id)
        previous_end = end
        checked.append({"id": source_id, "kind": kind, "label": label, "start": start, "end": end,
                        "heading": str(item.get("heading") or "").strip(), "deleted": bool(item.get("deleted"))})
    return checked


def dom_units(text: str, articles) -> list[dict]:
    """DOM 조항 범위로 단위를 만든다(#60). 정규식으로 조항 경계를 다시 찾지 않는다.

    - 첫 조문 앞의 규정명·연혁은 preamble, 장·절·관 제목은 바로 다음 조문 단위의 앞부분에 둔다.
    - 조문 사이에 남은 글(장·관 제목 등)도 다음 조문 단위에 붙여 원문을 빠짐없이 덮는다.
    - 부칙 범위 안의 '제N조'는 부칙 라벨을 붙여 따로 나눈다(부칙(…)제2조). 본칙 조와 섞이지 않는다.
    - 마지막 조문 뒤의 [별표/서식 파일]은 attachments 단위다.
    - 단위마다 사이트 조문 ID(source_id)와 DOM 삭제 표시(dom_deleted)를 남긴다.
    """
    items = validate_dom_articles(text, articles)
    units, cursor, chapter = [], 0, ""
    for index, item in enumerate(items):
        gap = text[cursor:item["start"]]
        sections = list(DOM_SECTION_HEADER.finditer(gap))
        if sections:
            chapter = re.sub(r"\s+", " ", sections[-1].group(0)).strip()
        unit_start = cursor
        if index == 0:
            lead = sections[0].start() if sections else len(gap)
            if gap[:lead].strip():
                units.append({"start": 0, "end": lead, "kind": "preamble", "article_id": "", "header": "",
                              "chapter": "", "source_id": "", "dom_deleted": False})
                unit_start = lead
        if item["kind"] == "article":
            units.append({"start": unit_start, "end": item["end"], "kind": "article", "article_id": item["label"],
                          "header": _compact(item["heading"]) or item["label"], "chapter": chapter,
                          "source_id": item["id"], "dom_deleted": item["deleted"]})
        else:
            body = text[item["start"]:item["end"]]
            inner = [m for m in ARTICLE_HEADER.finditer(body) if m.start() > 0]
            edges = [unit_start] + [item["start"] + m.start() for m in inner] + [item["end"]]
            labels = [item["label"]] + [f"{item['label']}{_article_id(m.group(0))}" for m in inner]
            for number, (start, end) in enumerate(zip(edges, edges[1:])):
                units.append({"start": start, "end": end, "kind": "addendum", "article_id": labels[number],
                              "header": labels[number], "chapter": chapter,
                              "source_id": item["id"] if number == 0 else f"{item['id']}#{labels[number]}",
                              "dom_deleted": item["deleted"]})
        cursor = item["end"]
    tail = text[cursor:]
    attachment = ATTACHMENT_HEADER.search(tail)
    attach_start = cursor + attachment.start() if attachment else len(text)
    units[-1]["end"] = attach_start  # 마지막 조문 뒤 남은 글은 마지막 단위에 붙인다.
    if attachment:
        units.append({"start": attach_start, "end": len(text), "kind": "attachments", "article_id": "별표/서식",
                      "header": "[별표/서식 파일]", "chapter": chapter, "source_id": "", "dom_deleted": False})
    return [unit for unit in units if unit["end"] > unit["start"]]


def dom_fingerprint(text: str, articles) -> str:
    """본문과 DOM 조문 범위·ID·삭제 표시를 함께 담은 해시. 본문이 같아도 조문 구성이 바뀌면 다시 색인한다."""
    items = validate_dom_articles(text, articles)
    canonical = json.dumps([[i["id"], i["kind"], i["label"], i["start"], i["end"], i["deleted"]] for i in items],
                           ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256((text + "\0" + canonical).encode("utf-8")).hexdigest()


def _reference_target(window: str, own_title: str) -> str:
    """참조 바로 앞 낱말로 대상을 정한다: local(이름 없음), self(현재 규정), other(다른 규정·법률·미확정).

    '이 규정'·'본 학칙'처럼 지시어가 붙으면 현재 규정이다. 법률 이름(「고등교육법」, 같은 법, 동법)은 외부다.
    '학칙'·'정관'은 이름만으로 정해지므로 현재 규정 제목과 비교한다. '○○ 규정'처럼 앞 낱말 없이 '규정'만 보이는
    경우는 어느 규정인지 확정할 수 없으므로 다른 대상으로 보고 로컬 조문으로 확장하지 않는다.
    """
    match = TARGET_NAME.search(window)
    if not match:
        return "local"
    name, is_law = match.group("name"), bool(re.fullmatch(_LAW, match.group("suffix")))
    if match.group("demo") or ATTACHED_DEMO.match(name):
        return "other" if is_law else "self"
    if is_law:
        return "other"
    title = re.sub(r"[\s「」『』]", "", own_title)
    if name in BARE_UNIQUE:
        return "self" if title.endswith(name) else "other"
    if name == match.group("suffix"):  # '규정'·'세칙'만 보이면 어느 규정인지 모른다
        return "other"
    return "self" if title and (title == name or title.endswith(name)) else "other"


def extract_references(content: str, own_article: str = "", own_title: str = "") -> list[str]:
    """조 교차 참조(제N조, 제N조의M)와 별표 참조를 정규화해 등장 순서대로 반환한다.

    다른 규정·외부 법률 이름이 붙은 참조(「고등교육법」 제8조, 학칙 제37조 등)와 대상을 확정할 수 없는 참조는
    현재 규정의 같은 번호 조항과 다를 수 있으므로 제외한다(#57). 이름이 없는 참조와 현재 규정을 가리키는
    참조('이 규정 제3조', 현재 규정 제목)만 남긴다.
    자기 조항(부칙 자기 헤더 포함)은 제외한다.
    """
    refs, previous = [], None  # previous: (끝 위치, 대상)
    for match in REFERENCE.finditer(content):
        line_start = content.rfind("\n", 0, match.start()) + 1
        target = _reference_target(content[max(line_start, match.start() - 60):match.start()], own_title)
        if previous and CHAIN_JOINERS.match(content[previous[0]:match.start()]):
            target = previous[1]  # '학칙 제37조제2항 및 제38조'처럼 이어지는 참조는 같은 대상을 따른다.
        previous = (match.end(), target)
        if target == "other":
            continue
        refs.append(f"제{match.group(1)}조" + (f"의{match.group(2)}" if match.group(2) else ""))
    for match in APPENDIX_REFERENCE.finditer(content):
        refs.append(f"별표{match.group(2)}")
    own_match = OWN_ARTICLE.search(own_article.split(")")[-1])
    own = own_match.group(0) if own_match else own_article.split(")")[-1]
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
    """조 머리글 줄 안의 삭제 표시와 다음 줄들의 삭제 표시를 함께 본다(#53).

    '제17조(삭제)', '제17조(입학생 등록) 삭제', 머리글 다음 줄의 '삭제 (2005. 4.12)'를 삭제로 판정한다.
    삭제 표시 뒤에 본문이 이어지면 삭제로 보지 않는다.
    """
    if unit["kind"] != "article":
        return False
    lines = body.splitlines()
    for index, line in enumerate(lines):
        header = ARTICLE_HEADER.match(line)
        if not header:
            continue
        rest = [item for item in lines[index + 1:] if item.strip()]
        inline = line[header.end():]
        parts = TITLE_AND_TAIL.match(inline)
        title, tail = (parts.group(1), parts.group(2).strip()) if parts else ("", inline.strip())
        inline_deleted = bool((title and STRICT_DELETED.match(f"({title.strip()})")) or (tail and STRICT_DELETED.match(tail)))
        if inline_deleted:  # 머리글에서 삭제됐으면 뒤 줄은 삭제 표시나 개정 주석만 허용한다.
            return all(STRICT_DELETED.match(item) or ANNOTATION_ONLY.match(item) for item in rest)
        return bool(rest) and all(STRICT_DELETED.match(item) for item in rest)
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
    articles = regulation.get("articles")
    if articles:  # 수집기 스냅샷: DOM 조항 범위를 그대로 쓴다(#60).
        units, text_hash = dom_units(text, articles), dom_fingerprint(text, articles)
    else:  # 조항 범위가 없는 입력(발췌본·이전 스냅샷): 줄 정규식으로 나눈다.
        units, text_hash = split_units(text), hashlib.sha256(text.encode("utf-8")).hexdigest()
    chunks = []
    for unit in units:
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
                "references": extract_references(content, unit["article_id"], regulation["title"]),
                "deleted": bool(unit.get("dom_deleted")) or _is_deleted(unit, text[unit["start"]:unit["end"]]),
                "source_id": unit.get("source_id", ""),
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
    if regulation.get("articles"):  # DOM 범위를 썼다면 모든 조문이 청크로 남고 범위를 넘지 않아야 한다.
        items = validate_dom_articles(text, regulation["articles"])
        by_source = {}
        for chunk in chunks:
            by_source.setdefault(chunk.get("source_id", ""), []).append(chunk)
        for item in items:
            own = by_source.get(item["id"], [])
            if not own:
                problems.append(f"DOM 조문 {item['id']}({item['label']})의 청크가 없습니다.")
            elif any(c["source_end"] > item["end"] for c in own):
                problems.append(f"DOM 조문 {item['id']}({item['label']})의 청크가 범위를 넘었습니다.")
            elif item["deleted"] and not all(c["deleted"] for c in own):
                problems.append(f"DOM에서 삭제된 조문 {item['id']}({item['label']})이 검색 대상으로 남았습니다.")
    return problems
