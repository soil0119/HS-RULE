"""목록 항목 하나를 검증된 스냅샷 항목으로 만든다.

필수 필드(regulation_id, revision_id, title, official_url, text)는 RAG 색인기
(hsrule.official_indexer)의 입력 계약과 같다. 나머지는 백엔드(#18/#19)와 변경 감지(#17)용이다.
"""
import argparse
import hashlib
import json
import re
import sys
from datetime import date, datetime, timezone
from urllib.parse import urlencode

from .catalog import CatalogEntry, parse_catalog, parse_detail, same_title, select_revision
from .client import LAW_PATH, FetchError, OfficialClient
from .pages import BASE_URL, PageError
from .parser import parse_content


def official_url(seq: int, history: int) -> str:
    """원문 링크는 서버가 공식 호스트로만 만든다."""
    return f"{BASE_URL}{LAW_PATH}/lawFullView.do?{urlencode({'SEQ': seq, 'SEQ_HISTORY': history})}"


# 본문 안 조항 연혁·인쇄 링크(/lmxsrv/…?SEQ=20&SEQ_HISTORY=1717…). 사이트는 SEQ_HISTORY로 본문을 고르고
# 같은 값을 이 링크에 넣으므로, 응답 본문이 어느 개정인지 알려 주는 유일한 표기다.
_IDENTITY = re.compile(r"/lmxsrv/[\w/]+\.do\?SEQ=(\d+)&(?:amp;)?SEQ_HISTORY=(\d+)")
_ADDENDUM_DATE = re.compile(r"(\d{4})\.(\d{1,2})\.(\d{1,2})")


def response_revisions(html: str) -> set[tuple[int, int]]:
    """본문 HTML의 링크가 가리키는 (SEQ, SEQ_HISTORY) 쌍."""
    return {(int(seq), int(history)) for seq, history in _IDENTITY.findall(html)}


def check_revision_identity(html: str, articles: list[dict], seq: int, revision) -> None:
    """요청한 개정의 본문인지 확인한다. 식별 불가·불일치는 색인하지 않고 거부한다.

    1. 링크의 (SEQ, SEQ_HISTORY)가 모두 요청과 같아야 한다. 링크가 하나도 없으면 revision_unidentified.
    2. 본문 부칙의 공포일이 선택한 개정 날짜보다 늦으면 뒤 개정의 본문이다(공포일은 자기 개정 날짜보다
       늦을 수 없다). 날짜를 읽을 수 없는 부칙은 이 검사에서 뺀다.
    """
    found = response_revisions(html)
    if not found:
        raise PageError("revision_unidentified", f"본문에서 개정 식별자를 찾지 못했습니다(요청 {seq}:{revision.history})")
    if found != {(seq, revision.history)}:
        shown = ", ".join(f"{s}:{h}" for s, h in sorted(found))
        raise PageError("revision_mismatch", f"요청 {seq}:{revision.history}, 본문 링크 {shown}")
    for item in articles:
        match = _ADDENDUM_DATE.search(item["label"]) if item["kind"] == "addendum" else None
        if match is None:
            continue
        try:
            promulgated = date(*map(int, match.groups()))
        except ValueError:
            continue
        if promulgated > revision.effective:
            raise PageError("revision_mismatch",
                            f"{revision.effective} 개정 본문에 그 뒤 공포된 부칙이 있습니다: {item['heading']}")


def _revision(revision) -> dict:
    return {"history": revision.history, "effective_date": revision.effective.isoformat(), "kind": revision.kind}


def build_regulation(client: OfficialClient, entry: CatalogEntry, today: date, now=None) -> dict:
    """상세에서 시행 중 개정을 고르고, 그 개정의 본문을 받아 검증·해석한다."""
    detail = parse_detail(client.detail_html(entry.seq))
    if not same_title(detail["title"], entry.title):
        raise PageError("title_mismatch", f"목록 {entry.title!r}, 상세 {detail['title']!r}")
    histories = {revision.history for revision in detail["revisions"]}
    if entry.latest_history not in histories:
        raise PageError("revision_mismatch", f"목록의 최신 개정 {entry.latest_history}가 상세 개정 목록에 없습니다")
    if detail["abolished"] != entry.abolished:
        raise PageError("status_mismatch", f"폐지 표기 불일치: 목록 {entry.abolished}, 상세 {detail['abolished']}")
    if entry.abolished:
        # 폐지 규정은 본문을 받지 않는다. 색인기는 abolished 항목을 받으면 해당 규정을 색인에서 지운다.
        latest = next(revision for revision in detail["revisions"] if revision.history == entry.latest_history)
        return {
            "regulation_id": str(entry.seq),
            "revision_id": f"{entry.seq}:{latest.history}",
            "title": entry.title,
            "official_url": official_url(entry.seq, latest.history),
            "text": "",
            "abolished": True,
            "visibility": "public",
            "former_names": list(entry.former_names),
            "code": entry.code or detail["code"],
            "department": detail["department"],
            "path": list(entry.path),
            "revision": _revision(latest),
            "latest_history": entry.latest_history,
            "fetched_at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        }
    current, upcoming = select_revision(detail["revisions"], today)
    html = client.content_html(entry.seq, current.history)
    parsed = parse_content(html, entry.title)
    check_revision_identity(html, parsed["articles"], entry.seq, current)
    text = parsed["text"]
    return {
        "regulation_id": str(entry.seq),
        "revision_id": f"{entry.seq}:{current.history}",
        "title": entry.title,  # 목록 명칭(옛 명칭 제외). 본문 표기는 text 첫 줄에 그대로 있다.
        "official_url": official_url(entry.seq, current.history),
        "text": text,
        "abolished": False,
        "visibility": "public",
        "former_names": list(entry.former_names),
        "history_notes": parsed["history_notes"],
        "code": entry.code or detail["code"],
        "department": detail["department"],
        "path": list(entry.path),
        "revision": _revision(current),
        "latest_history": entry.latest_history,
        "upcoming": [_revision(revision) for revision in upcoming],
        "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "articles": parsed["articles"],
        "attachments": parsed["attachments"],
        "warnings": parsed["warnings"],
        "fetched_at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
    }


def main(argv: list[str] | None = None) -> int:
    """지정한 SEQ만 실제로 받아 해석 결과를 요약한다. 전체 수집·스냅샷 발행은 #17 범위다."""
    parser = argparse.ArgumentParser(prog="python -m hsrule_crawler.collect",
                                     description="공식 규정 표본 수집·해석 확인(전체 수집 아님)")
    parser.add_argument("seq", type=int, nargs="+", help="확인할 규정 SEQ")
    parser.add_argument("--full", action="store_true", help="본문·조항까지 JSON으로 출력")
    args = parser.parse_args(argv)
    client = OfficialClient()
    entries, _ = parse_catalog(client.catalog_html())
    by_seq = {entry.seq: entry for entry in entries}
    failed = 0
    for seq in args.seq:
        entry = by_seq.get(seq)
        if entry is None:
            print(json.dumps({"seq": seq, "error": "not_in_catalog"}, ensure_ascii=False))
            failed += 1
            continue
        try:
            item = build_regulation(client, entry, date.today())
        except (PageError, FetchError) as exc:
            print(json.dumps({"seq": seq, "error": str(exc)}, ensure_ascii=False))
            failed += 1
            continue
        if not args.full:
            item = {key: item.get(key) for key in ("regulation_id", "revision_id", "code", "title", "department",
                                                    "abolished", "revision", "upcoming", "warnings")} | {
                "articles": sum(1 for a in item.get("articles", []) if a["kind"] == "article"),
                "addenda": sum(1 for a in item.get("articles", []) if a["kind"] == "addendum"),
                "attachments": len(item.get("attachments", [])), "chars": len(item["text"])}
        print(json.dumps(item, ensure_ascii=False, indent=None if not args.full else 2))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
