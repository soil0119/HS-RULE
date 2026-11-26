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
        self.assertEqual(detail["revisions"], [Revision(1717, date(2026, 11, 11), "개정", date(2026, 11, 11)),
                                               Revision(1647, date(2026, 4, 21), "개정", date(2026, 4, 21)),
                                               Revision(1623, date(2026, 2, 26), "개정", date(2026, 2, 26))])
        self.assertEqual(detail["status_date"], date(2026, 11, 11))

    def test_effective_date_is_chek_future_date_not_the_shown_date(self):
        # 실제 학칙 상세: 1453은 목록에 2025-08-28(showDate)로 보이지만 chekFutureDate는 2025-09-01이다.
        # 같은 개정 본문의 마지막 부칙이 "(2025. 8. 28. 공포) … 2025년 9월 1일부터 시행"이므로
        # showDate는 공포일, chekFutureDate는 시행일이다.
        revision = next(r for r in parse_detail(fixture("detail_20_dates.html"))["revisions"] if r.history == 1453)
        self.assertEqual((revision.promulgated, revision.effective), (date(2025, 8, 28), date(2025, 9, 1)))
        evidence = parse_content(fixture("content_20_1453.html"), "한성대학교 학칙")["text"]
        self.assertIn("부 칙 (2025. 8. 28. 공포)", evidence)
        self.assertIn("2025년 9월 1일부터 시행한다", evidence)

        revisions = parse_detail(fixture("detail_20_dates.html"))["revisions"]
        for today, expected in ((date(2025, 8, 28), 1410), (date(2025, 8, 31), 1410), (date(2025, 9, 1), 1453)):
            with self.subTest(today=today):
                current, upcoming = select_revision(revisions, today)
                self.assertEqual(current.history, expected)
                self.assertEqual(1453 in [r.history for r in upcoming], expected != 1453)

    def test_detail_without_an_effective_date_is_rejected(self):
        # 합성: 시행일(chekFutureDate)이 없는 개정 항목은 시행 여부를 알 수 없다.
        html = fixture("detail_20.html").replace("chekFutureDate('20260421')", "noDate()", 1)
        with self.assertRaises(PageError) as caught:
            parse_detail(html)
        self.assertEqual(caught.exception.reason, "detail_invalid")

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

    # --- #65 본문 노드 보존 -------------------------------------------------------------------------

    def parse(self, html):
        return parse_content(html, "한성대학교 학칙")

    def addendum_text(self, result, label):
        item = next(item for item in result["articles"] if item["label"] == label)
        return result["text"][item["start"]:item["end"]]

    def test_direct_text_in_an_addendum_body_is_kept(self):
        # 합성(리뷰 #65 재현): 부칙 본문의 시행일 문장을 감싸는 태그 없이 직접 텍스트로 둔다.
        sentence = "(시행일) 본 학칙은 1972년 12월 21일부터 시행한다."
        html = fixture("content_20.html").replace(
            '<div id="111181"><div class="none"><span style="0">(시행일) 본 학칙은 1972년 12월 21일부터 시행한다. '
            '</span></div></div>', f'<div id="111181">{sentence}</div>')
        self.assertNotEqual(html, fixture("content_20.html"))
        result = self.parse(html)
        self.assertIn(sentence, self.addendum_text(result, "부칙#1"))
        self.assertEqual(result["warnings"], [])
        # 수집 단계에서 빠지면 청커도 검출할 수 없으므로(리뷰 지적), 청크까지 문장이 이어지는지 본다.
        regulation = {"regulation_id": "20", "revision_id": "20:1717", "title": result["title"],
                      "official_url": official_url(20, 1717), "text": result["text"]}
        tokenizer = local_tokenizer(result["text"])
        chunks = chunk_regulation(regulation, tokenizer, 128, 16)
        self.assertEqual(verify_chunks(regulation, chunks, tokenizer, 128), [])
        self.assertTrue(any(sentence in chunk["content"] for chunk in chunks))

    def test_mixed_inline_block_comment_and_table_nodes_keep_source_order(self):
        # 합성: 직접 텍스트·인라인·블록·<br>·주석·script·중첩 표가 섞인 부칙 본문
        body = ('<div id="111181">앞 문장 <b>굵게</b> 이어짐<!-- 주석 --><script>var x = 1;</script>'
                '<div class="none">블록 문장</div>꼬리 문장<br/>줄바꿈 뒤'
                '<table><caption>표 이름</caption><tr><td>칸1</td><td>칸2 <table><tr><td>안쪽</td></tr></table></td></tr>'
                '</table>표 뒤</div>')
        html = re.sub(r'<div id="111181">.*?</div></div>', body, fixture("content_20.html"), count=1, flags=re.S)
        result = self.parse(html)
        self.assertEqual(self.addendum_text(result, "부칙#1").split("\n"),
                         ["부 칙", "앞 문장 굵게 이어짐", "블록 문장", "꼬리 문장", "줄바꿈 뒤", "표 이름", "칸1 | 칸2 안쪽",
                          "표 뒤"])
        self.assertNotIn("var x", result["text"])
        self.assertNotIn("주석", result["text"])

    def test_empty_addendum_body_and_stray_top_level_content_are_reported(self):
        # 합성: 빈 부칙 본문, 최상위의 직접 텍스트와 div가 아닌 블록. 빈 앵커는 조용히 넘긴다.
        html = re.sub(r'<div id="111181">.*?</div></div>', '<div id="111181"> <!-- 비어 있음 --> </div>',
                      fixture("content_20.html"), count=1, flags=re.S)
        html = html.replace('<div class="article" style="font-size: 10pt;">',
                            '<a name="77"></a>최상위 직접 문장<p>문단 블록</p>'
                            '<div class="article" style="font-size: 10pt;">', 1)
        result = self.parse(html)
        lines = result["text"].split("\n")
        self.assertIn("최상위 직접 문장", lines)
        self.assertIn("문단 블록", lines)
        self.assertEqual(result["warnings"], ["부칙 본문이 비어 있습니다: 부 칙",
                                              "분류할 수 없는 최상위 내용: '최상위 직접 문장'",
                                              "분류할 수 없는 최상위 내용: '문단 블록'"])

    def test_direct_text_in_an_article_body_is_kept(self):
        # 합성: 조항 본문에 태그 없는 텍스트가 섞인 경우
        html = fixture("content_20.html")
        changed = re.sub(r'(<div class="JO"[^>]*>.*?<div class="article".*?</div>)',
                         r'\1조항 직접 문장', html, count=1, flags=re.S)
        self.assertNotEqual(changed, html)
        self.assertIn("조항 직접 문장", self.parse(changed)["text"])

    def test_every_text_node_of_the_real_fixtures_reaches_the_output(self):
        # 실제 fixture 전체: 첨부 목록(이름만 남김)과 조항 제목 표의 화면용 caption('조항') 밖의
        # 모든 텍스트 노드가 출력 text에 들어간다.
        from bs4 import BeautifulSoup, Comment

        for name in ("content_20.html", "content_18_reference.html", "content_118_history.html",
                     "content_66_untitled.html"):
            html = fixture(name)
            root = BeautifulSoup(html, "lxml").select_one("#lawcontent")
            result = parse_content(html, root.select_one(".lawname").get_text(" "))
            compact = re.sub(r"\s+", "", result["text"])
            with self.subTest(name):
                for node in root.find_all(string=True):
                    attachment_list = any("article" in (parent.get("class") or []) and parent.parent is root
                                          for parent in node.parents)
                    heading_caption = node.parent.name == "caption" and node.find_parent(class_="article")
                    if isinstance(node, Comment) or attachment_list or heading_caption or not node.strip():
                        continue
                    self.assertIn(re.sub(r"\s+", "", node), compact)

    # --- #64 첨부 링크 연결 ---------------------------------------------------------------------------

    def attachments_with(self, *items):
        """합성: 첨부 목록을 (이름, [fileDown ID…]) 항목들로 바꾼다. 항목마다 실제 마크업과 같은 표 하나."""
        blocks = []
        for name, ids in items:
            links = "".join(f'<a href="javascript:fileDown(\'{file_id}\', \'attach\');" style="margin: 5px;" '
                            f'title="다운받기"><img alt="다운받기" src="/images/service/sub/ico_down.png"/></a>'
                            for file_id in ids)
            span = f'<span class="attach_file">{name}</span>' if name else ""
            blocks.append('<div class="article" style="padding-left: 10px;"><table><caption>별지서식다운로드</caption>'
                          f'<tr><td>{span}{links}</td></tr></table></div>')
        html = re.sub(r'<div class="article" style="padding-left: 10px;">.*?</table>\s*</div>', "",
                      fixture("content_20.html"), flags=re.S)
        html = html.replace("</div>\n</body>", "".join(blocks) + "</div>\n</body>", 1)
        result = self.parse(html)
        return [(item["name"], item["file_id"]) for item in result["attachments"]], result["warnings"]

    def test_missing_link_never_borrows_another_attachments_id(self):
        full = [("별표1.hwp", ["4159"]), ("별표2.hwp", ["4160"]), ("별표3.hwp", ["4161"])]
        for missing in range(3):
            with self.subTest(missing=missing):
                items = [(name, [] if index == missing else ids) for index, (name, ids) in enumerate(full)]
                found, warnings = self.attachments_with(*items)
                self.assertEqual(found, [(name, "" if index == missing else ids[0])
                                         for index, (name, ids) in enumerate(full)])
                self.assertEqual(warnings, [f"첨부 '{full[missing][0]}': 다운로드 링크가 없습니다"])

    def test_two_names_in_one_cell_and_ambiguous_or_stray_links(self):
        html = fixture("content_20.html").replace(
            '<span class="attach_file">(별표 1)대학별 학부 및 학과 입학정원.hwp</span>',
            '<span class="attach_file">앞 파일.hwp</span><span class="attach_file">(별표 1)대학별 학부 및 학과 입학정원.hwp</span>')
        found = [(item["name"], item["file_id"]) for item in self.parse(html)["attachments"]]
        self.assertEqual(found[:2], [("앞 파일.hwp", ""), ("(별표 1)대학별 학부 및 학과 입학정원.hwp", "4159")])

        found, warnings = self.attachments_with(("둘.hwp", ["1", "2"]), ("", ["3"]), ("정상.hwp", ["4"]))
        self.assertEqual(found, [("둘.hwp", ""), ("정상.hwp", "4")])
        self.assertEqual(warnings, ["첨부 '둘.hwp': 다운로드 링크가 여러 개입니다(1, 2)",
                                    "첨부 이름과 연결되지 않은 다운로드 링크: 3"])

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
    def __init__(self, url, text="", status=200, headers=None):
        self.url, self.text, self.status_code, self.encoding = url, text, status, "utf-8"
        self.headers = headers or {}


class RecordingAdapter(requests.adapters.BaseAdapter):
    """네트워크 없는 transport. 실제로 전송된 URL을 모두 기록한다."""

    def __init__(self, routes):
        super().__init__()
        self.routes, self.sent = routes, []

    def send(self, request, **kwargs):
        self.sent.append((request.url, kwargs.get("verify")))
        status, headers, body = self.routes[request.url]
        response = requests.Response()
        response.status_code, response.url, response.request = status, request.url, request
        response.headers.update(headers)
        response._content, response.encoding = body.encode("utf-8"), "utf-8"
        return response

    def close(self):
        pass


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
        self.assertIs(kwargs["allow_redirects"], False)

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

    def transport(self, routes):
        session = requests.Session()
        adapter = RecordingAdapter(routes)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        self.slept = []
        return OfficialClient(session, min_interval=1.0, sleep=self.slept.append), adapter

    def test_redirect_targets_are_checked_before_they_are_requested(self):
        start = "https://rule.hansung.ac.kr/lmxsrv/law/x"
        forbidden = {
            "foreign host": ("https://foreign.example/lmxsrv/law/x", "foreign_redirect"),
            "plain http": ("http://rule.hansung.ac.kr/lmxsrv/law/x", "foreign_redirect"),
            "other port": ("https://rule.hansung.ac.kr:8443/lmxsrv/law/x", "foreign_redirect"),
            "userinfo": ("https://user@rule.hansung.ac.kr/lmxsrv/law/x", "foreign_redirect"),
            "look-alike host": ("https://rule.hansung.ac.kr.evil.example/x", "foreign_redirect"),
            "error page": ("https://rule.hansung.ac.kr/errorPage.do", "error_page"),
        }
        for name, (target, reason) in forbidden.items():
            with self.subTest(name):
                client, adapter = self.transport({
                    start: (302, {"Location": target}, ""),
                    target: (200, {}, "must never be sent"),
                })
                with self.assertRaises(PageError) as caught:
                    client.get("x", {})
                self.assertEqual(caught.exception.reason, reason)
                self.assertEqual([url for url, _ in adapter.sent], [start])

    def test_loops_missing_location_and_too_many_hops_are_refused(self):
        base = "https://rule.hansung.ac.kr/lmxsrv/law/"
        cases = {
            "loop": ({base + "x": (302, {"Location": "y"}, ""), base + "y": (301, {"Location": "x"}, "")},
                     "redirect_loop", [base + "x", base + "y"]),
            "self": ({base + "x": (307, {"Location": base + "x"}, "")}, "redirect_loop", [base + "x"]),
            "no location": ({base + "x": (302, {}, "")}, "bad_redirect", [base + "x"]),
            "too many": ({base + "x": (302, {"Location": "a"}, ""), base + "a": (302, {"Location": "b"}, ""),
                          base + "b": (302, {"Location": "c"}, ""), base + "c": (302, {"Location": "d"}, ""),
                          base + "d": (200, {}, "too far")},
                         "redirect_loop", [base + "x", base + "a", base + "b", base + "c"]),
        }
        for name, (routes, reason, sent) in cases.items():
            with self.subTest(name):
                client, adapter = self.transport(routes)
                with self.assertRaises(PageError) as caught:
                    client.get("x", {})
                self.assertEqual(caught.exception.reason, reason)
                self.assertEqual([url for url, _ in adapter.sent], sent)

    def test_same_host_redirect_is_followed_with_tls_verification_and_spacing(self):
        start = "https://rule.hansung.ac.kr/lmxsrv/law/lawDetail.do?SEQ=20"
        moved = "https://rule.hansung.ac.kr/lmxsrv/law/lawDetail2.do?SEQ=20"
        client, adapter = self.transport({
            start: (302, {"Location": "/lmxsrv/law/lawDetail2.do?SEQ=20"}, ""),
            moved: (200, {}, "본문"),
        })
        self.assertEqual(client.get("lawDetail.do", {"SEQ": 20}), "본문")
        self.assertEqual(adapter.sent, [(start, True), (moved, True)])
        self.assertEqual(len(self.slept), 1)  # 리다이렉트 요청도 요청 간격을 지킨다


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
        self.assertEqual(item["revision"], {"history": 1717, "effective_date": "2026-11-11",
                                            "promulgation_date": "2026-11-11", "kind": "개정"})
        self.assertEqual(len(item["content_hash"]), 64)
        self.assertEqual(item["fetched_at"], "2026-11-25T05:00:00+00:00")
        json.dumps(item, ensure_ascii=False)  # 스냅샷 JSON으로 그대로 직렬화된다.

    def test_future_revision_is_recorded_not_indexed(self):
        # 11/10 기준 1717(11/11)은 시행예정이다. 실제 1647 본문(2026-04-21 개정)을 응답으로 쓴다.
        stub = StubClient(fixture("detail_20.html"), fixture("content_20_1647.html"))
        item = build_regulation(stub, self.ENTRY, date(2026, 11, 10), now=self.NOW)
        self.assertEqual(stub.requested[-1], ("content", 20, 1647))
        self.assertEqual(item["revision_id"], "20:1647")
        self.assertEqual(item["upcoming"], [{"history": 1717, "effective_date": "2026-11-11",
                                             "promulgation_date": "2026-11-11", "kind": "개정"}])
        self.assertNotIn("2026. 11. 11. 공포", item["text"])
        self.assertEqual([a["label"] for a in item["articles"] if a["kind"] == "addendum"],
                         ["부칙#1", "부칙(2026.4.21.공포)"])

    def test_body_of_another_revision_is_rejected(self):
        today = date(2026, 11, 10)  # 시행 중 개정은 1647
        later = fixture("content_20.html")  # 1717 본문
        own = fixture("content_20_1647.html")
        cases = {
            # 리뷰 #61 재현: 1647을 요청했는데 1717 본문(11/11 부칙 포함)이 온 경우
            "later revision body": (later, "revision_mismatch"),
            # 합성: 링크가 하나도 없어 어느 개정인지 알 수 없는 본문
            "no identifier": (re.sub(r"SEQ=20&amp;SEQ_HISTORY=1647", "", own), "revision_unidentified"),
            # 합성: 링크가 두 개정을 섞어 가리킴
            "mixed identifiers": (own.replace("SEQ_HISTORY=1647", "SEQ_HISTORY=1623", 1), "revision_mismatch"),
            # 합성: 다른 SEQ를 가리킴(사이트는 요청 SEQ를 링크에 그대로 넣는다)
            "other regulation": (own.replace("SEQ=20&amp;", "SEQ=99&amp;"), "revision_mismatch"),
            # 합성: 링크는 1647인데 본문에 1647(4/21) 뒤에 공포된 부칙이 있음
            "addendum after revision date": (own.replace(
                "</div>\n</body>",
                '<div class="addenda">부   칙 (2026. 11. 11. 공포)</div><div id="9"><div class="none">시행</div></div>'
                "</div>\n</body>", 1), "revision_mismatch"),
        }
        self.assertEqual(build_regulation(StubClient(fixture("detail_20.html"), own), self.ENTRY, today,
                                          now=self.NOW)["revision_id"], "20:1647")
        for name, (content, reason) in cases.items():
            self.assertNotEqual(content, own, f"합성 변형이 적용되지 않았습니다: {name}")
            with self.subTest(name), self.assertRaises(PageError) as caught:
                build_regulation(StubClient(fixture("detail_20.html"), content), self.ENTRY, today, now=self.NOW)
            self.assertEqual(caught.exception.reason, reason)

    def test_abolished_regulation_is_marked_without_fetching_text(self):
        entry = CatalogEntry(197, "3-1-83", "창업R&D센터 규정", 928, ("제3편 학사행정", "제1장 일반행정"), (), True)
        stub = StubClient(fixture("detail_197_abolished.html"), "본문을 요청하면 안 된다")
        item = build_regulation(stub, entry, date(2026, 11, 25), now=self.NOW)
        self.assertEqual(stub.requested, [("detail", 197)])
        self.assertEqual((item["regulation_id"], item["revision_id"], item["abolished"], item["text"]),
                         ("197", "197:928", True, ""))
        # 실제 197: 폐지 기록 928은 2021-10-21 공포, 2021-08-23 시행(소급). 시행일이 효력일이다.
        self.assertEqual(item["revision"], {"history": 928, "effective_date": "2021-08-23",
                                            "promulgation_date": "2021-10-21", "kind": "개정"})

    ABOLISHED = CatalogEntry(197, "3-1-83", "창업R&D센터 규정", 928, ("제3편 학사행정", "제1장 일반행정"), (), True)

    def build_abolished(self, detail, today, content=None):
        stub = StubClient(detail, content or fixture("content_197_310.html"))
        return build_regulation(stub, self.ABOLISHED, today, now=self.NOW), stub

    def test_abolition_takes_effect_on_its_effective_date(self):
        detail = fixture("detail_197_abolished.html")
        before, stub = self.build_abolished(detail, date(2021, 8, 22))
        self.assertEqual((before["abolished"], before["revision_id"]), (False, "197:310"))
        self.assertEqual(stub.requested, [("detail", 197), ("content", 197, 310)])
        self.assertIn("창업R&D센터", before["text"])
        self.assertEqual(before["scheduled_abolition"]["effective_date"], "2021-08-23")
        self.assertEqual([r["history"] for r in before["upcoming"]], [928])
        for today in (date(2021, 8, 23), date(2021, 10, 21), date(2026, 11, 25)):
            with self.subTest(today=today):
                item, stub = self.build_abolished(detail, today)
                self.assertEqual((item["abolished"], item["revision_id"], item["text"]), (True, "197:928", ""))
                self.assertEqual(stub.requested, [("detail", 197)])

    def test_future_abolition_keeps_the_regulation_indexed(self):
        # 합성(리뷰 #62 재현): 폐지 기록의 날짜를 2026-12-01로 옮긴다. 11/25에는 310이 시행 중이다.
        detail = (fixture("detail_197_abolished.html").replace("'20210823'", "'20261201'")
                  .replace("'20211021'", "'20261201'"))
        self.assertNotEqual(detail, fixture("detail_197_abolished.html"))
        item, _ = self.build_abolished(detail, date(2026, 11, 25))
        self.assertEqual((item["abolished"], item["revision_id"]), (False, "197:310"))
        self.assertEqual(item["scheduled_abolition"]["history"], 928)
        self.assertTrue(item["text"])
        on_day, _ = self.build_abolished(detail, date(2026, 12, 1))
        self.assertTrue(on_day["abolished"])

    def test_future_amendment_and_abolition_mixed(self):
        # 합성: 310(과거) → 900 개정(2026-12-01 시행) → 928 폐지(2027-03-01 시행)
        detail = fixture("detail_197_abolished.html").replace("'20210823'", "'20270301'").replace(
            "'20211021'", "'20261215'")
        amendment = ('<option value="900"><script type="text/javascript">showDate(\'20261110\', \'2\');</script>개정'
                     '<script type="text/javascript">if(chekFutureDate(\'20261201\')){document.write("[시행예정]");}'
                     '</script>[폐지]</option>')
        detail = detail.replace('<option value="310">', amendment + '<option value="310">', 1)
        self.assertIn('value="900"', detail)
        cases = {
            date(2026, 11, 25): (False, "197:310", [900, 928]),
            date(2026, 12, 1): (False, "197:900", [928]),
            date(2027, 2, 28): (False, "197:900", [928]),
            date(2027, 3, 1): (True, "197:928", None),
        }
        for today, (abolished, revision_id, upcoming) in cases.items():
            with self.subTest(today=today):
                content = fixture("content_197_310.html")
                if revision_id == "197:900":
                    content = content.replace("SEQ_HISTORY=310", "SEQ_HISTORY=900")
                item, _ = self.build_abolished(detail, today, content)
                self.assertEqual((item["abolished"], item["revision_id"]), (abolished, revision_id))
                if upcoming is not None:
                    self.assertEqual([r["history"] for r in item["upcoming"]], upcoming)
                    self.assertEqual(item["scheduled_abolition"]["effective_date"], "2027-03-01")

    def test_title_status_date_must_match_the_latest_revision(self):
        # 합성: 제목 옆 [시행예정] 날짜와 최신 개정 시행일이 다르면 상세를 믿지 않는다.
        detail = fixture("detail_197_abolished.html")
        changed = detail[::-1].replace("'32801202'", "'10901202'", 1)[::-1]  # 마지막(제목 옆) 날짜만 바꾼다
        self.assertNotEqual(changed, detail)
        with self.assertRaises(PageError) as caught:
            self.build_abolished(changed, date(2026, 11, 25))
        self.assertEqual(caught.exception.reason, "detail_invalid")

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
