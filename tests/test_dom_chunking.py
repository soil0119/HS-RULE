"""수집기 DOM 조항 범위를 청킹에 반영 (#60). 입력은 PR #47 스냅샷 형식을 본뜬 합성 본문이다."""
import copy
import os
import unittest

from hsrule.official_chunking import ChunkingError, chunk_regulation, split_units, verify_chunks
from hsrule.official_indexer import IndexingError, index_regulation, sync_snapshot
from hsrule.official_repository import OfficialRepository
from hsrule.official_retrieval import HybridRetriever
from tests.fakes import FakeEmbeddings
from tests.test_official_chunking import local_tokenizer

URL = os.getenv("HSRULE_TEST_DATABASE_URL")
LINES = [
    "연계전공 운영 규정", "2005. 4.12. 제정", "2025.2.27.전부개정",
    "제 1 장 총 칙",
    "제 1 조 (목적)", "본 규정은 연계전공 운영에 필요한 사항을 정한다.",
    "제 2 조 (정의)", "이 규정에서 연계전공이란 둘 이상의 학과가 함께 운영하는 전공을 말한다.",
    "제 16 조 2(교과목의 분반) 교과목 주관 학과는 분반을 정할 수 있다.",
    "제 2 장 운 영", "제 1 관 임 용",
    "제 3 조 (임용)", "연계전공 담당 교원의 임용은 이 규정 제2조에 따른다.",
    "제 95조의 4 ~ 제 95조의 10 (삭제 2021. 8. 12.)",
    "제 9 조 (학기)", "연계전공 학기는 2학기로 한다.",
    "부 칙 (2016. 2. 3 공포)", "제1조 (시행일) 이 규정은 2016년 3월 1일부터 시행한다.",
    "제2조 (경과조치) 2015학년도 이전 입학생은 종전 규정에 따른다.",
    "[별표/서식 파일]", "(별표 1) 연계전공 목록.hwp",
]
TEXT = "\n".join(LINES)
DOM = [  # (id, kind, label, 시작 줄, 끝 줄(미포함), deleted)
    ("900001", "article", "제1조", 4, 6, False),
    ("900002", "article", "제2조", 6, 9, False),
    ("900003", "article", "제3조", 11, 13, False),
    ("900004", "article", "제95조의4~제95조의10", 13, 14, True),
    ("900005", "article", "제9조", 14, 16, False),
    ("900006", "addendum", "부칙(2016.2.3공포)", 16, 19, False),
]


def offsets(lines):
    starts, position = [], 0
    for line in lines:
        starts.append(position)
        position += len(line) + 1
    return starts


def snapshot(lines=LINES, dom=DOM, **extra):
    text, starts = "\n".join(lines), offsets(lines)
    shift = len(lines) - len(LINES)
    articles = []
    for source_id, kind, label, first, last, deleted in dom:
        first, last = first + shift, last + shift
        end = starts[last] - 1 if last < len(lines) else len(text)
        articles.append({"id": source_id, "kind": kind, "label": label, "heading": lines[first].split("(")[0].strip(),
                         "start": starts[first], "end": end, "deleted": deleted})
    return {"regulation_id": "54", "revision_id": "54:100", "title": "연계전공 운영 규정",
            "official_url": "https://rule.example/54", "text": text, "articles": articles, **extra}


class DomUnitTests(unittest.TestCase):
    def chunks(self, item=None):
        item = item or snapshot()
        return chunk_regulation(item, local_tokenizer(item["text"]), 128, 16)

    def test_regex_splitting_had_the_reported_mismatches(self):
        ids = [u["article_id"] for u in split_units(TEXT)]
        self.assertIn("제16조2", ids)  # 본문 줄을 새 조항으로 나눔 (SEQ 104)
        self.assertNotIn("제95조의4~제95조의10", ids)  # 범위형 삭제를 조항으로 인식하지 못함 (SEQ 18)

    def test_dom_ranges_drive_the_units(self):
        chunks = self.chunks()
        ids = list(dict.fromkeys(c["article_id"] for c in chunks))
        self.assertEqual(ids, ["", "제1조", "제2조", "제3조", "제95조의4~제95조의10", "제9조", "부칙(2016.2.3공포)",
                               "부칙(2016.2.3공포)제1조", "부칙(2016.2.3공포)제2조", "별표/서식"])
        second = "".join(c["content"] for c in chunks if c["article_id"] == "제2조")
        self.assertIn("제 16 조 2(교과목의 분반)", second)  # 본문 줄은 제2조에 남는다

    def test_headings_between_articles_are_kept_with_the_next_article(self):
        third = [c for c in self.chunks() if c["article_id"] == "제3조"]
        self.assertTrue(third[0]["content"].startswith("제 2 장 운 영\n제 1 관 임 용"))
        self.assertEqual(third[0]["chapter"], "제 1 관 임 용")

    def test_dom_deleted_range_is_excluded_and_others_are_not(self):
        flags = {c["article_id"]: c["deleted"] for c in self.chunks()}
        self.assertTrue(flags["제95조의4~제95조의10"])
        self.assertFalse(flags["제9조"])
        self.assertFalse(flags["제3조"])

    def test_every_character_is_covered_and_verified(self):
        item = snapshot()
        self.assertEqual(verify_chunks(item, self.chunks(item), local_tokenizer(item["text"]), 128), [])

    def test_source_ids_are_kept(self):
        sources = {c["article_id"]: c["source_id"] for c in self.chunks()}
        self.assertEqual(sources["제1조"], "900001")
        self.assertEqual(sources["부칙(2016.2.3공포)"], "900006")
        self.assertEqual(sources["부칙(2016.2.3공포)제2조"], "900006#부칙(2016.2.3공포)제2조")
        self.assertEqual((sources[""], sources["별표/서식"]), ("", ""))

    def test_history_line_added_in_front_keeps_article_identity(self):
        before = {(c["article_id"], c["source_id"], c["content"]) for c in self.chunks()}
        lines = LINES[:3] + ["2026.1.1.일부개정"] + LINES[3:]
        after_chunks = self.chunks(snapshot(lines))
        after = {(c["article_id"], c["source_id"], c["content"]) for c in after_chunks}
        self.assertEqual({x for x in before if x[0]}, {x for x in after if x[0]})  # 조문은 같은 ID·같은 내용
        self.assertIn("2026.1.1.일부개정", next(c["content"] for c in after_chunks if c["article_id"] == ""))

    def test_same_text_with_different_dom_ids_changes_the_hash(self):
        changed = snapshot()
        changed["articles"][0]["id"] = "999999"
        self.assertNotEqual(self.chunks()[0]["text_hash"], self.chunks(changed)[0]["text_hash"])

    def test_invalid_dom_ranges_are_rejected(self):
        def broken(change):
            item = snapshot()
            change(item["articles"])
            return item

        cases = {
            "overlap": lambda a: a[1].update(start=a[0]["start"] + 3),
            "heading": lambda a: a[2].update(heading="제 4 조"),
            "missing id": lambda a: a[0].update(id=""),
            "duplicate id": lambda a: a[1].update(id=a[0]["id"]),
            "out of range": lambda a: a[-1].update(end=10_000),
            "kind": lambda a: a[0].update(kind="table"),
        }
        for name, change in cases.items():
            with self.subTest(name=name), self.assertRaises(ChunkingError):
                self.chunks(broken(change))
        with self.assertRaises(ChunkingError):
            self.chunks(snapshot(dom=[]) | {"articles": [{"id": "x"}]})


@unittest.skipUnless(URL, "set HSRULE_TEST_DATABASE_URL to a disposable PostgreSQL with pgvector")
class DomIndexTests(unittest.TestCase):
    def setUp(self):
        self.repo = OfficialRepository(URL)
        self.repo.initialize()
        self.repo.rebuild()
        self.emb = FakeEmbeddings(TEXT + " 2026.1.1.일부개정")

    def test_source_article_id_is_stored_and_returned(self):
        index_regulation(snapshot(), self.repo, self.emb)
        stored = {row["article_id"]: row["source_article_id"] for row in self.repo.chunks_of("54:100")}
        self.assertEqual(stored["제3조"], "900003")
        hits = HybridRetriever(self.repo, self.emb, k=3).search("연계전공 담당 교원의 임용")
        self.assertEqual((hits[0]["article_id"], hits[0]["source_article_id"]), ("제3조", "900003"))

    def test_dom_deleted_range_is_not_searchable(self):
        index_regulation(snapshot(), self.repo, self.emb)
        ids = [h["article_id"] for h in HybridRetriever(self.repo, self.emb, k=6).search("삭제 제95조의4")]
        self.assertNotIn("제95조의4~제95조의10", ids)

    def test_dom_change_with_same_text_is_reindexed(self):
        self.assertEqual(index_regulation(snapshot(), self.repo, self.emb)["status"], "indexed")
        self.assertEqual(index_regulation(snapshot(), self.repo, self.emb)["status"], "unchanged")
        changed = snapshot()
        changed["articles"][2]["id"] = "900333"
        self.assertEqual(index_regulation(changed, self.repo, self.emb)["status"], "indexed")
        stored = {row["article_id"]: row["source_article_id"] for row in self.repo.chunks_of("54:100")}
        self.assertEqual(stored["제3조"], "900333")

    def test_invalid_snapshot_fails_only_that_regulation(self):
        bad = snapshot(regulation_id="55", revision_id="55:1")
        bad["articles"][0]["end"] = 10_000
        with self.assertRaises(IndexingError):
            index_regulation(copy.deepcopy(bad), self.repo, self.emb)
        report = sync_snapshot([snapshot(), bad], self.repo, self.emb)
        self.assertEqual(report["indexed"], ["54:100"])
        self.assertEqual([f["revision_id"] for f in report["failed"]], ["55:1"])


if __name__ == "__main__":
    unittest.main()
