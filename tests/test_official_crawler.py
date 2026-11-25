"""공식 규정 수집기(#16) 오프라인 회귀.

fixture는 2026-11-25에 rule.hansung.ac.kr 공개 페이지에서 받은 실제 HTML을 요소 단위로 축소한
것이다(마크업 변경 없음). '합성'이라고 적은 사례만 실제 사이트에서 보지 못한 입력을 만들어 쓴다.
네트워크·실제 사이트 호출은 하지 않는다.
"""
import json
import re
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

import requests

from hsrule.official_chunking import chunk_regulation, split_units, verify_chunks
from hsrule_crawler.catalog import (CatalogEntry, Revision, parse_catalog, parse_detail, same_title,
                                    select_revision, split_former_names)
from hsrule_crawler.client import FetchError, OfficialClient
from hsrule_crawler.collect import build_regulation, official_url
from hsrule_crawler.pages import PageError
from hsrule_crawler.parser import parse_content
from tests.fakes import local_tokenizer

FIXTURES = Path(__file__).with_name("fixtures") / "official"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class CatalogTests(unittest.TestCase):
    def test_tree_lists_regulations_with_latest_revision(self):
        entries, skipped = parse_catalog(fixture("tree.html"))
        by_seq = {entry.seq: entry for entry in entries}
        self.assertEqual(sorted(by_seq), [18, 20, 54, 197])
        self.assertEqual(by_seq[20], CatalogEntry(20, "2-1-1", "한성대학교 학칙", 1717, ("제2편 학칙", "제1장 대학")))
        self.assertEqual(by_seq[54].path, ("제4편 부속기관",))
        self.assertEqual(skipped, [])

    def test_abolished_marker_is_status_not_title(self):
        # 실제 목록 표기 '3-1-83 창업R&D센터 규정 [폐지]'(목록 323건 중 35건이 폐지).
        entry = {entry.seq: entry for entry in parse_catalog(fixture("tree.html"))[0]}[197]
        self.assertEqual((entry.code, entry.title, entry.abolished), ("3-1-83", "창업R&D센터 규정", True))
        self.assertEqual(entry.path, ("제3편 학사행정", "제1장 일반행정"))
        self.assertFalse({e.seq: e for e in parse_catalog(fixture("tree.html"))[0]}[20].abolished)
        detail = parse_detail(fixture("detail_197_abolished.html"))  # 상세 표기 '[폐지] 3-1-83 …'
        self.assertEqual((detail["code"], detail["title"], detail["abolished"]), ("3-1-83", "창업R&D센터 규정", True))
        self.assertEqual({r.kind for r in detail["revisions"]}, {"개정"})

    def test_non_page_items_are_reported_not_crawled(self):
        # 합성: 외부 URL 링크(lawType 3) 항목. 실제 목록에서는 아직 보지 못했다.
        original = fixture("tree.html")
        match = re.search(r"var zNodes = (\[.*?\]);", original, re.S)
        nodes = json.loads(match.group(1))
        next(node for node in nodes if node["id"] == 54)["lawType"] = "3"
        html = original.replace(match.group(1), json.dumps(nodes, ensure_ascii=False))
        entries, skipped = parse_catalog(html)
        self.assertNotIn(54, [entry.seq for entry in entries])
        self.assertEqual([item["seq"] for item in skipped], [54])

    def test_former_names_and_spacing_do_not_break_title_matching(self):
        # 실제 목록 표기(SEQ 111, 104)와 본문 표기(SEQ 31 '기 자 재 관 리 규 정').
        self.assertEqual(split_former_names("학술연구비규정 (舊 교원연구장려금 규정, 교내학술연구비규정)"),
                         ("학술연구비규정", ("교원연구장려금 규정", "교내학술연구비규정")))
        self.assertEqual(split_former_names("한성대학교 학칙"), ("한성대학교 학칙", ()))
        self.assertTrue(same_title("학 사 운 영 규 정", "학사운영규정(舊 학사운영내규)"))
        self.assertTrue(same_title("기 자 재 관 리 규 정", "기자재관리규정"))
        self.assertFalse(same_title("학교법인 한성학원 정관", "한성대학교 학칙"))
        self.assertFalse(same_title("", ""))

    def test_error_redirect_is_not_an_empty_catalog(self):
        with self.assertRaises(PageError) as caught:
            parse_catalog(fixture("error_redirect.html"))
        self.assertEqual(caught.exception.reason, "error_page")

    def test_detail_revisions_and_metadata(self):
        detail = parse_detail(fixture("detail_20.html"))
        self.assertEqual((detail["code"], detail["title"], detail["department"]), ("2-1-1", "한성대학교 학칙", "공통"))
        self.assertEqual(detail["revisions"], [Revision(1717, date(2026, 11, 11), "개정"),
                                               Revision(1647, date(2026, 4, 21), "개정"),
                                               Revision(1623, date(2026, 2, 26), "개정")])

    def test_current_revision_excludes_future_effective_dates(self):
        revisions = parse_detail(fixture("detail_20.html"))["revisions"]
        current, upcoming = select_revision(revisions, date(2026, 11, 25))
        self.assertEqual((current.history, upcoming), (1717, []))
        current, upcoming = select_revision(revisions, date(2026, 11, 10))
        self.assertEqual((current.history, [r.history for r in upcoming]), (1647, [1717]))
        self.assertEqual(select_revision(revisions, date(2026, 11, 11))[0].history, 1717)  # 시행일 당일은 시행 중
        with self.assertRaises(PageError) as caught:
            select_revision(revisions, date(2026, 1, 1))
        self.assertEqual(caught.exception.reason, "no_effective_revision")


class ContentParserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = parse_content(fixture("content_20.html"), "한성대학교 학칙")
        cls.text = cls.result["text"]
        cls.articles = cls.result["articles"]

    def test_articles_follow_dom_with_site_ids(self):
        body = [a for a in self.articles if a["kind"] == "article"]
        self.assertEqual([a["label"] for a in body], ["제1조", "제3조", "제5조의4", "제9조", "제17조"])
        self.assertEqual(body[0]["id"], "111075")
        self.assertTrue(all(a["id"].isdigit() for a in self.articles))
        self.assertEqual({a["chapter"] for a in body}, {"제 1 장 총 칙"})

    def test_offsets_cover_each_article_exactly(self):
        for item in self.articles:
            self.assertTrue(self.text[item["start"]:].startswith(item["heading"]), item)
            self.assertLessEqual(item["start"], item["end"])
        for left, right in zip(self.articles, self.articles[1:]):
            self.assertLessEqual(left["end"], right["start"])
        ninth = next(a for a in self.articles if a["label"] == "제9조")
        self.assertIn("2. 제 2학기: 9월 1일부터 다음해 2월 말일까지", self.text[ninth["start"]:ninth["end"]])
        self.assertNotIn("제 17 조", self.text[ninth["start"]:ninth["end"]])

    def test_paragraphs_items_and_tables_keep_lines(self):
        lines = self.text.split("\n")
        self.assertEqual(lines[0], "한성대학교 학칙")
        self.assertIn("1. 제 1학기: 3월 1일부터 8월 31일까지", lines)
        self.assertIn("명칭 | 관련 학과 | 사업종목 | 설치장소", lines)
        self.assertIn("케이키친한상 | 호텔외식 경영학과 | 음식점업 (한식) | 서울특별시 성북구 삼선교로 16길 116", lines)
        self.assertIn("② 학교기업 설치 및 운영에 관한 사항은 규정으로 따로 정한다.", lines)
        self.assertNotIn("조항", lines)  # 조항 표의 캡션·버튼은 본문이 아니다.

    def test_deleted_article_and_addenda(self):
        deleted = [a["label"] for a in self.articles if a["deleted"]]
        self.assertEqual(deleted, ["제17조"])
        addenda = [a for a in self.articles if a["kind"] == "addendum"]
        self.assertEqual([a["label"] for a in addenda], ["부칙#1", "부칙(2026.4.21.공포)", "부칙(2026.11.11.공포)"])
        self.assertIn("본 변경 학칙은 공포한 날부터 시행한다.", self.text[addenda[-1]["start"]:addenda[-1]["end"]])

    def test_attachments_are_metadata_and_listed_once(self):
        self.assertEqual([a["file_id"] for a in self.result["attachments"]], ["4159", "4160"])
        self.assertEqual(self.result["attachments"][0]["name"], "(별표 1)대학별 학부 및 학과 입학정원.hwp")
        self.assertEqual(self.text.count("[별표/서식 파일]"), 1)
        self.assertTrue(self.text.endswith("(별표 2) 대학별 학부(과) 학위종별.hwp"))

    def test_rag_chunker_agrees_with_dom_boundaries(self):
        """sumiiniee의 청커(#23)가 이 텍스트를 같은 조항으로 나누고 검증을 통과한다."""
        self.assertEqual(self.result["warnings"], [])
        units = [u["article_id"] for u in split_units(self.text) if u["kind"] in ("article", "addendum")]
        self.assertEqual(units, [a["label"] for a in self.articles])
        regulation = {"regulation_id": "20", "revision_id": "20:1717", "title": "한성대학교 학칙",
                      "official_url": official_url(20, 1717), "text": self.text}
        tokenizer = local_tokenizer(self.text)
        chunks = chunk_regulation(regulation, tokenizer, 128, 16)
        self.assertEqual(verify_chunks(regulation, chunks, tokenizer, 128), [])

    def test_line_start_cross_reference_is_not_an_article(self):
        result = parse_content(fixture("content_18_reference.html"), "학교법인 한성학원 정관")
        self.assertEqual(len(result["articles"]), 2)
        reference = next(a for a in result["articles"] if "제123조에 따른" in result["text"][a["start"]:a["end"]])
        self.assertTrue(result["text"][reference["start"]:].startswith(reference["heading"]))
        self.assertEqual(result["warnings"], [])

    def test_history_lines_and_untitled_article_frames_are_notes(self):
        # 실제 SEQ 118: 규정명 아래 .history 연혁 줄. 실제 SEQ 66: 제목 없는 조항 틀에 '(2020.4.9. 제정)'.
        cases = (("content_118_history.html", "연계전공 시행세칙", ["2005. 4.12. 제정", "2025.2.27.전부개정"]),
                 ("content_66_untitled.html", "학생부종합전형 운영 규정", ["(2020.4.9. 제정)"]))
        for name, title, notes in cases:
            with self.subTest(name=name):
                result = parse_content(fixture(name), title)
                self.assertEqual(result["history_notes"], notes)
                self.assertEqual(result["warnings"], [])
                self.assertEqual([a["label"] for a in result["articles"]], ["제1조"])
                self.assertEqual(result["text"].split("\n")[1:1 + len(notes)], notes)

    def test_header_like_body_line_is_reported(self):
        # 합성: 청커 정규식이 조항 시작으로 오인하는 본문 줄. DOM 경계는 그대로 유지한다.
        html = fixture("content_20.html").replace(
            "각 대학 학부 또는 학과의 입학정원은","</span></div><div class=\"none level0\"><span>제 7 조(재학연한)에 따라 각 대학 학부 또는 학과의 입학정원은", 1)
        result = parse_content(html, "한성대학교 학칙")
        self.assertEqual([a["label"] for a in result["articles"] if a["kind"] == "article"],
                         ["제1조", "제3조", "제5조의4", "제9조", "제17조"])
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("청커에서 조항 시작으로 인식", result["warnings"][0])

    def test_unknown_block_is_kept_with_warning(self):
        # 합성: 사이트가 새 블록 종류를 추가한 경우. 본문을 버리지 않고 경고로 알린다.
        html = fixture("content_20.html").replace(
            '<div class="addenda">', '<div class="newkind">새 형식 블록 내용</div><div class="addenda">', 1)
        result = parse_content(html, "한성대학교 학칙")
        self.assertIn("새 형식 블록 내용", result["text"].split("\n"))
        self.assertEqual(result["warnings"], ["분류할 수 없는 블록: '새 형식 블록 내용'"])

    def test_rejects_error_empty_shell_login_and_wrong_regulation(self):
        cases = {
            "error_page": (fixture("error_redirect.html"), "한성대학교 학칙"),
            "empty_content": (fixture("content_empty.html"), "한성대학교 학칙"),
            # 사이트는 SEQ가 틀려도 다른 규정을 200으로 준다(실제 확인). 제목 대조로 거부한다.
            "title_mismatch": (fixture("content_20.html"), "학교법인 한성학원 정관"),
            # 합성: 메뉴 화면(본문 영역 없음)과 로그인 화면.
            "not_content": ("<html><body><div class='lnb'>규정정보 상세 HOME</div></body></html>", "한성대학교 학칙"),
            "login_page": ("<html><body><form><input type='password' name='pw'></form></body></html>", "x"),
            "shell": ("<html><body><div id='lawcontent'><div class='lawname'>한성대학교 학칙</div></div></body></html>",
                      "한성대학교 학칙"),
        }
        for reason, (html, title) in cases.items():
            with self.subTest(reason=reason), self.assertRaises(PageError) as caught:
                parse_content(html, title)
            self.assertEqual(caught.exception.reason, reason)


class FakeResponse:
    def __init__(self, url, text="", status=200):
        self.url, self.text, self.status_code, self.encoding = url, text, status, "utf-8"


class FakeSession:
    def __init__(self, responses):
        self.headers, self.cookies = {}, requests.cookies.RequestsCookieJar()
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class ClientTests(unittest.TestCase):
    def client(self, *responses):
        self.slept, self.now = [], [0.0]
        session = FakeSession(responses)
        client = OfficialClient(session, min_interval=1.0, clock=lambda: self.now[0], sleep=self.slept.append)
        return client, session

    def test_public_request_without_cookies_or_tls_override(self):
        client, session = self.client(FakeResponse("https://rule.hansung.ac.kr/lmxsrv/law/lawDetail.do?SEQ=20", "ok"))
        self.assertEqual(client.detail_html(20), "ok")
        url, kwargs = session.calls[0]
        self.assertEqual(url, "https://rule.hansung.ac.kr/lmxsrv/law/lawDetail.do")
        self.assertEqual(kwargs["params"], {"SEQ": 20})
        self.assertNotIn("verify", kwargs)
        self.assertNotIn("cookies", kwargs)
        self.assertEqual(len(session.cookies), 0)
        self.assertEqual(kwargs["timeout"], (5, 20))

    def test_requests_are_spaced(self):
        ok = "https://rule.hansung.ac.kr/lmxsrv/law/x"
        client, _ = self.client(FakeResponse(ok), FakeResponse(ok), FakeResponse(ok))
        client.get("x", {})
        self.now[0] = 0.25
        client.get("x", {})
        self.now[0] = 5.0
        client.get("x", {})
        self.assertEqual(self.slept, [0.75])

    def test_rejects_error_redirect_foreign_host_status_and_network_errors(self):
        cases = [
            (FakeResponse("https://rule.hansung.ac.kr/errorPage.do"), PageError, "error_page"),
            (FakeResponse("https://evil.example/lmxsrv"), PageError, "foreign_redirect"),
            (FakeResponse("http://rule.hansung.ac.kr/lmxsrv/law/x"), PageError, "foreign_redirect"),
            (FakeResponse("https://rule.hansung.ac.kr/lmxsrv/law/x", status=503), FetchError, None),
            (requests.ConnectionError("down"), FetchError, None),
        ]
        for response, error, reason in cases:
            client, _ = self.client(response)
            with self.subTest(response=getattr(response, "url", response)), self.assertRaises(error) as caught:
                client.get("x", {})
            if reason:
                self.assertEqual(caught.exception.reason, reason)


class StubClient:
    def __init__(self, detail, content):
        self.detail, self.content, self.requested = detail, content, []

    def detail_html(self, seq):
        self.requested.append(("detail", seq))
        return self.detail

    def content_html(self, seq, history):
        self.requested.append(("content", seq, history))
        return self.content


class BuildRegulationTests(unittest.TestCase):
    ENTRY = CatalogEntry(20, "2-1-1", "한성대학교 학칙", 1717, ("제2편 학칙", "제1장 대학"))
    NOW = datetime(2026, 11, 25, 5, 0, tzinfo=timezone.utc)

    def test_snapshot_item_matches_indexer_contract(self):
        stub = StubClient(fixture("detail_20.html"), fixture("content_20.html"))
        item = build_regulation(stub, self.ENTRY, date(2026, 11, 25), now=self.NOW)
        self.assertEqual(stub.requested, [("detail", 20), ("content", 20, 1717)])
        self.assertEqual((item["regulation_id"], item["revision_id"], item["title"]), ("20", "20:1717", "한성대학교 학칙"))
        self.assertEqual(item["official_url"],
                         "https://rule.hansung.ac.kr/lmxsrv/law/lawFullView.do?SEQ=20&SEQ_HISTORY=1717")
        self.assertEqual((item["code"], item["department"], item["visibility"]), ("2-1-1", "공통", "public"))
        self.assertEqual(item["revision"], {"history": 1717, "effective_date": "2026-11-11", "kind": "개정"})
        self.assertEqual(len(item["content_hash"]), 64)
        self.assertEqual(item["fetched_at"], "2026-11-25T05:00:00+00:00")
        json.dumps(item, ensure_ascii=False)  # 스냅샷 JSON으로 그대로 직렬화된다.

    def test_future_revision_is_recorded_not_indexed(self):
        # 시행일 이전 날짜로 보면 1717은 시행예정이다. 실제 1647 본문 대신 같은 fixture를 응답으로 쓴다.
        stub = StubClient(fixture("detail_20.html"), fixture("content_20.html"))
        item = build_regulation(stub, self.ENTRY, date(2026, 11, 10), now=self.NOW)
        self.assertEqual(stub.requested[-1], ("content", 20, 1647))
        self.assertEqual(item["revision_id"], "20:1647")
        self.assertEqual(item["upcoming"], [{"history": 1717, "effective_date": "2026-11-11", "kind": "개정"}])

    def test_abolished_regulation_is_marked_without_fetching_text(self):
        entry = CatalogEntry(197, "3-1-83", "창업R&D센터 규정", 928, ("제3편 학사행정", "제1장 일반행정"), (), True)
        stub = StubClient(fixture("detail_197_abolished.html"), "본문을 요청하면 안 된다")
        item = build_regulation(stub, entry, date(2026, 11, 25), now=self.NOW)
        self.assertEqual(stub.requested, [("detail", 197)])
        self.assertEqual((item["regulation_id"], item["revision_id"], item["abolished"], item["text"]),
                         ("197", "197:928", True, ""))
        self.assertEqual(item["revision"], {"history": 928, "effective_date": "2021-10-21", "kind": "개정"})

    def test_catalog_and_detail_must_agree(self):
        mismatched = [
            (CatalogEntry(20, "2-1-1", "한성대학교 학칙", 9999, ()), "detail_20.html", "revision_mismatch"),
            (CatalogEntry(20, "1-0-1", "학교법인 한성학원 정관", 1717, ()), "detail_20.html", "title_mismatch"),
            (CatalogEntry(197, "3-1-83", "창업R&D센터 규정", 928, ()), "detail_197_abolished.html", "status_mismatch"),
        ]
        for entry, detail, reason in mismatched:
            stub = StubClient(fixture(detail), fixture("content_20.html"))
            with self.subTest(reason=reason), self.assertRaises(PageError) as caught:
                build_regulation(stub, entry, date(2026, 11, 25))
            self.assertEqual(caught.exception.reason, reason)
            self.assertNotIn("content", [call[0] for call in stub.requested])


if __name__ == "__main__":
    unittest.main()
