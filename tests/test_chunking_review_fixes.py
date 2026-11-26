"""재검토 이슈 #53·#57 회귀. 합성 입력과 일회용 DB를 쓰며 실제 공식 사이트 수집이 아니다."""
import os
import unittest

from hsrule.official_chunking import _is_deleted, chunk_regulation, extract_references
from hsrule.official_indexer import index_regulation
from hsrule.official_repository import OfficialRepository
from hsrule.official_retrieval import HybridRetriever
from tests.fakes import FakeEmbeddings
from tests.test_official_chunking import local_tokenizer, regulation

URL = os.getenv("HSRULE_TEST_DATABASE_URL")
ARTICLE = {"kind": "article"}
TEXT = (
    "제1장 총칙\n"
    "제8조(수업)\n수업은 「고등교육법」 제8조와 시행령 제2조에 따라 운영한다. 졸업은 연계전공 시행세칙 제37조를 따른다.\n"
    "제17조(삭제)\n"
    "제18조(입학생 등록) 삭제\n"
    "제19조(삭제 절차)\n삭제 절차는 이 조에서 정한다.\n"
    "제20조(복학)\n복학 신청 기한을 정한다. 제19조에 따른다.\n"
    "제37조(졸업)\n학점이 부족한 학생은 졸업할 수 없다. 졸업학점을 정한다.\n"
)


class InlineDeletedTests(unittest.TestCase):  # #53
    def test_inline_next_line_and_parenthesized_forms(self):
        for body in ("제17조(삭제)", "제17조(입학생 등록) 삭제", "제17조(삭제) <2005.4.12>", "제17조 (삭제 2005. 4.12)",
                     "제17조(입학생)\n삭제 (2005. 4.12)"):
            with self.subTest(body=body):
                self.assertTrue(_is_deleted(ARTICLE, body))

    def test_annotation_brackets_and_paragraph_deletions(self):
        for body in ("제17조(삭제) 〈2005.4.12〉", "제17조(삭제) <2005.4.12> <2010.3.1>", "제17조(삭제)\n〈2005.4.12〉",
                     "제17조(입학)\n① 삭제 〈2010.3.1〉\n② 삭제 (2011.1.1)"):
            with self.subTest(body=body):
                self.assertTrue(_is_deleted(ARTICLE, body))
        self.assertFalse(_is_deleted(ARTICLE, "제17조(입학)\n① 삭제\n② 본문은 유지한다"))

    def test_normal_articles_are_not_deleted(self):
        for body in ("제19조(삭제 절차)\n삭제 절차는 이 조에서 정한다.", "제17조(입학생)\n본문", "제17조(삭제)\n그러나 본문이 있다",
                     "제17조(입학생)"):
            with self.subTest(body=body):
                self.assertFalse(_is_deleted(ARTICLE, body))

    def test_chunks_carry_the_flag(self):
        reg = regulation(TEXT)
        chunks = chunk_regulation(reg, local_tokenizer(TEXT), 128, 16)
        flags = {c["article_id"]: c["deleted"] for c in chunks}
        self.assertEqual((flags["제17조"], flags["제18조(입학생등록)"] if "제18조(입학생등록)" in flags else flags["제18조"]),
                         (True, True))
        self.assertFalse(flags["제19조"])
        self.assertFalse(flags["제8조"])


class ReferenceTargetTests(unittest.TestCase):  # #57
    def test_other_regulations_and_external_laws_are_not_local_references(self):
        content = "「고등교육법」 제8조와 시행령 제2조에 따르고, 학칙 제37조를 따른다. 제5조에 따른다."
        self.assertEqual(extract_references(content, "제1조", "연계전공 시행세칙"), ["제5조"])
        self.assertEqual(extract_references("「고등교육법 시행령」 제2조 및 제8조", "제1조", "한성대학교 학칙"), [])

    def test_own_regulation_and_unnamed_references_are_kept(self):
        self.assertEqual(extract_references("이 학칙 제8조 및 제9조, 제10조", "제1조", "한성대학교 학칙"),
                         ["제8조", "제9조", "제10조"])
        self.assertEqual(extract_references("제3조의2 및 제 3 조에 따라", "제3조"), ["제3조의2"])

    def test_demonstrative_self_references_are_kept(self):  # '이 규정'은 현재 규정이다
        self.assertEqual(extract_references("이 규정 제3조 및 제4조", "제1조", "학사운영규정"), ["제3조", "제4조"])
        self.assertEqual(extract_references("본규정 제7조", "제1조", "학사운영규정"), ["제7조"])
        self.assertEqual(extract_references("본 학칙 제8조", "제1조", "한성대학교 학칙"), ["제8조"])
        self.assertEqual(extract_references("학사운영규정 제5조", "제1조", "학사운영규정"), ["제5조"])

    def test_external_and_undetermined_targets_are_not_local(self):
        for content, title in (("같은 법 제9조", "한성대학교 학칙"), ("동법 제10조", "한성대학교 학칙"),
                               ("장학금 지급 규정 제5조", "학사운영규정"), ("대학원학칙 제5조", "한성대학교 학칙"),
                               ("「한성대학교 학칙」 제40조", "연계전공 시행세칙")):
            with self.subTest(content=content):
                self.assertEqual(extract_references(content, "제1조", title), [])
        self.assertEqual(extract_references("산정 방법은 제5조에 따른다", "제1조", "한성대학교 학칙"), ["제5조"])

    def test_chained_references_follow_the_same_target(self):
        self.assertEqual(extract_references("학칙 제37조제2항 및 제38조, 제39조", "제1조", "연계전공 시행세칙"), [])
        self.assertEqual(extract_references("학칙 제37조 및 이 세칙 제5조", "제1조", "연계전공 시행세칙"), ["제5조"])

    def test_addendum_self_header_is_not_a_main_article_reference(self):
        self.assertEqual(extract_references("제1조 이 규정은 공포한 날부터 시행한다.", "부칙#1제1조"), [])
        self.assertEqual(extract_references("제1조 시행", "부칙(2025.4.11.공포)제1조"), [])


@unittest.skipUnless(URL, "set HSRULE_TEST_DATABASE_URL to a disposable PostgreSQL with pgvector")
class PublicSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = OfficialRepository(URL)
        cls.repo.initialize()
        with cls.repo.cursor() as cursor:
            cursor.execute("TRUNCATE hs_reg_revisions CASCADE")
        cls.emb = FakeEmbeddings(TEXT)
        index_regulation(regulation(TEXT), cls.repo, cls.emb)
        cls.retriever = HybridRetriever(cls.repo, cls.emb, k=6)

    def test_inline_deleted_articles_are_excluded_from_public_search(self):
        for query in ("제17조", "제18조 입학생 등록"):
            ids = [h["article_id"] for h in self.retriever.search(query)]
            self.assertNotIn("제17조", ids)
            self.assertFalse([i for i in ids if i.startswith("제18조")])
        self.assertIn("제19조", [h["article_id"] for h in self.retriever.search("제19조 삭제 절차")])

    def test_external_law_article_number_does_not_pull_local_article(self):
        hits = self.retriever.search("제8조 수업 운영")
        self.assertEqual(hits[0]["article_id"], "제8조")
        self.assertNotIn("reference", [f for h in hits for f in h["found_by"] if h["article_id"] == "제37조"])


if __name__ == "__main__":
    unittest.main()
