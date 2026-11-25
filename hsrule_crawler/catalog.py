"""공식 규정 목록(lawTree)과 상세(lawDetail)의 개정 목록 해석."""
import json
import re
from dataclasses import dataclass
from datetime import date

from .pages import PageError, normalize_space, service_soup

_ZNODES = re.compile(r"var\s+zNodes\s*=\s*(\[.*?\])\s*;\s*\n", re.S)
_CODE_TITLE = re.compile(r"^(\d+(?:-[0-9A-Za-z]+)+)\s+(.+)$")
_SHOW_DATE = re.compile(r"showDate\(\s*'(\d{8})'")
_FORMER_NAMES = re.compile(r"^(.*?)\s*\(\s*舊\s*([^)]*)\)\s*$")  # '학사운영규정(舊 학사운영내규)'
_ABOLISHED = re.compile(r"\s*\[\s*폐지\s*\]\s*")  # 목록은 '… [폐지]', 상세는 '[폐지] 3-1-83 …'

REGULATION_TYPE = "1301"  # 일반 분류 트리 항목. 1302는 게시판형이다.
PAGE_LAW_TYPES = {"1", "2"}  # 1: 일반 규정, 2: 비정형 규정. 3은 외부 URL 링크다.


@dataclass(frozen=True)
class CatalogEntry:
    seq: int
    code: str
    title: str
    latest_history: int
    path: tuple[str, ...]  # 편·장 분류 경로
    former_names: tuple[str, ...] = ()  # 목록 이름에 '(舊 …)'로 적힌 옛 명칭
    abolished: bool = False  # 목록 이름의 '[폐지]' 표기


@dataclass(frozen=True)
class Revision:
    history: int
    effective: date
    kind: str  # 사이트 표기(제정·개정·전부개정 등)


def split_abolished(name: str) -> tuple[str, bool]:
    """'[폐지]' 표기를 떼어 내고 폐지 여부를 돌려준다."""
    stripped = normalize_space(_ABOLISHED.sub(" ", name))
    return stripped, stripped != normalize_space(name)


def split_code_title(name: str) -> tuple[str, str]:
    """'2-1-1 한성대학교 학칙' → ('2-1-1', '한성대학교 학칙'). 코드가 없으면 빈 코드."""
    name = normalize_space(name)
    match = _CODE_TITLE.match(name)
    return (match.group(1), match.group(2)) if match else ("", name)


def split_former_names(title: str) -> tuple[str, tuple[str, ...]]:
    """'학술연구비규정 (舊 교원연구장려금 규정, 교내학술연구비규정)' → 현재 명칭과 옛 명칭들."""
    match = _FORMER_NAMES.match(title)
    if not match:
        return title, ()
    former = tuple(name for name in (normalize_space(part) for part in match.group(2).split(",")) if name)
    return match.group(1), former


def same_title(left: str, right: str) -> bool:
    """공백 차이('학 사 운 영 규 정')와 옛 명칭·폐지 표기를 무시하고 같은 규정명인지 본다."""
    def key(value):
        return re.sub(r"\s+", "", split_former_names(split_abolished(value)[0])[0])
    return bool(key(left)) and key(left) == key(right)


def parse_catalog(html: str) -> tuple[list[CatalogEntry], list[dict]]:
    """규정 트리에서 본문이 있는 규정과, 본문이 없어 제외한 항목(사유 포함)을 돌려준다."""
    service_soup(html)
    match = _ZNODES.search(html)
    if not match:
        raise PageError("catalog_missing", "규정 트리 데이터(zNodes)가 없습니다")
    try:
        nodes = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise PageError("catalog_invalid", str(exc)) from None
    by_id = {node["id"]: node for node in nodes}

    def path_of(node) -> tuple[str, ...]:
        names, parent, seen = [], by_id.get(node.get("pId")), set()
        while parent is not None and parent["id"] not in seen and parent["id"] != node["id"]:
            seen.add(parent["id"])
            if parent.get("pId") == parent["id"] or parent.get("pId") is None:
                break  # 루트("규정")는 경로에 넣지 않는다.
            names.append(normalize_space(parent["name"]))
            parent = by_id.get(parent.get("pId"))
        return tuple(reversed(names))

    entries, skipped = [], []
    for node in nodes:
        if str(node.get("folder")) == "1":
            continue
        name = normalize_space(str(node.get("name", "")))
        if node.get("typeCode") != REGULATION_TYPE or str(node.get("lawType")) not in PAGE_LAW_TYPES:
            skipped.append({"seq": node.get("id"), "name": name,
                            "reason": f"본문 페이지가 아닌 항목(typeCode={node.get('typeCode')}, "
                                      f"lawType={node.get('lawType')})"})
            continue
        if not isinstance(node.get("hseq"), int) or node["hseq"] <= 0:
            skipped.append({"seq": node.get("id"), "name": name, "reason": "개정 ID(hseq)가 없습니다"})
            continue
        unmarked, abolished = split_abolished(name)
        code, title = split_code_title(unmarked)
        title, former = split_former_names(title)
        entries.append(CatalogEntry(int(node["id"]), code, title, node["hseq"], path_of(node), former, abolished))
    seqs = [entry.seq for entry in entries]
    if len(seqs) != len(set(seqs)):
        raise PageError("catalog_invalid", "같은 SEQ가 목록에 여러 번 있습니다")
    return entries, skipped


def parse_detail(html: str) -> dict:
    """규정 상세에서 코드·제목·담당부서와 개정 목록(시행일 포함)을 읽는다."""
    soup = service_soup(html)
    select = soup.select_one("select#histroySeq")  # 사이트 원문 철자(histroy)를 그대로 쓴다.
    if select is None:
        raise PageError("detail_missing", "개정 선택 목록이 없습니다")
    revisions = []
    for option in select.find_all("option"):
        value = option.get("value", "")
        date_match = _SHOW_DATE.search(str(option))
        if not value.isdigit() or not date_match:
            raise PageError("detail_invalid", f"개정 항목을 해석할 수 없습니다: {normalize_space(option.get_text())!r}")
        for script in option.find_all("script"):
            script.decompose()
        raw = date_match.group(1)
        try:
            effective = date(int(raw[:4]), int(raw[4:6]), int(raw[6:]))
        except ValueError:
            raise PageError("detail_invalid", f"시행일 형식 오류: {raw}") from None
        revisions.append(Revision(int(value), effective, split_abolished(option.get_text(" "))[0]))
    if not revisions:
        raise PageError("detail_invalid", "개정 이력이 비어 있습니다")
    stit = soup.select_one(".Stit")
    dtit = soup.select_one(".Dtit")
    unmarked, abolished = split_abolished(stit.get_text(" ") if stit else "")
    code, title = split_code_title(unmarked)
    department = normalize_space(dtit.get_text(" ")) if dtit else ""
    department = re.sub(r"^담당부서\s*:\s*", "", department)
    return {"code": code, "title": title, "department": department, "abolished": abolished,
            "revisions": revisions}


def select_revision(revisions: list[Revision], today: date) -> tuple[Revision, list[Revision]]:
    """오늘 시행 중인 개정과, 아직 시행 전인 개정(시행일 순)을 돌려준다."""
    effective = [revision for revision in revisions if revision.effective <= today]
    if not effective:
        raise PageError("no_effective_revision", f"{today.isoformat()} 기준 시행 중인 개정이 없습니다")
    current = max(effective, key=lambda revision: (revision.effective, revision.history))
    upcoming = sorted((revision for revision in revisions if revision.effective > today),
                      key=lambda revision: (revision.effective, revision.history))
    return current, upcoming
