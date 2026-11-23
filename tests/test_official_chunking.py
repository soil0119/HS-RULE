import string
import unittest
from pathlib import Path

from tokenizers import Tokenizer, models, pre_tokenizers, processors
from transformers import PreTrainedTokenizerFast

from hsrule.official_chunking import (ChunkingError, chunk_regulation, extract_references,
                                      split_units, verify_chunks)

FIXTURE = Path(__file__).with_name("fixtures") / "hansung_rules_excerpt.txt"
URL = "https://rule.hansung.ac.kr/lmxsrv/law/lawFullContent.do?SEQ=20&SEQ_HISTORY=1623"


def local_tokenizer(text):
    symbols = list(dict.fromkeys("[UNK] [CLS] [SEP]".split() + list(set(text + string.ascii_letters))))
    symbols += ["##" + c for c in symbols if len(c) == 1]
    backend = Tokenizer(models.WordPiece({token: idx for idx, token in enumerate(symbols)}, unk_token="[UNK]"))
    backend.pre_tokenizer = pre_tokenizers.BertPreTokenizer()
    backend.post_processor = processors.TemplateProcessing(
        single="[CLS] $A [SEP]", special_tokens=[("[CLS]", 1), ("[SEP]", 2)])
    return PreTrainedTokenizerFast(tokenizer_object=backend, unk_token="[UNK]")


def regulation(text):
    return {"regulation_id": "20", "revision_id": "20:1623", "title": "한성대학교 학칙",
            "official_url": URL, "text": text}


class OfficialChunkingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = FIXTURE.read_text(encoding="utf-8")
        cls.reg = regulation(cls.text)
        cls.tokenizer = local_tokenizer(cls.text)

    def chunks(self, max_tokens=128, overlap=16):
        return chunk_regulation(self.reg, self.tokenizer, max_tokens, overlap)

    def by_article(self, chunks, article):
        return [c for c in chunks if c["article_id"] == article]

    def test_units_cover_every_character_exactly_once(self):
        units = split_units(self.text)
        self.assertEqual(units[0]["start"], 0)
        self.assertEqual(units[-1]["end"], len(self.text))
        for left, right in zip(units, units[1:]):
            self.assertEqual(left["end"], right["start"])

    def test_article_ids_and_kinds(self):
        ids = [(u["kind"], u["article_id"]) for u in split_units(self.text)]
        for expected in [("preamble", ""), ("article", "제1조"), ("article", "제3조의2"),
                         ("article", "제5조의4"), ("article", "제27조1"), ("attachments", "별표/서식")]:
            self.assertIn(expected, ids)
        self.assertIn(("addendum", "부칙#1"), ids)
        self.assertIn(("addendum", "부칙(2016.2.3공포)"), ids)
        self.assertIn(("addendum", "부칙(2016.2.3공포)제2조"), ids)
        self.assertIn(("addendum", "부칙(2025.4.11.공포)제2조"), ids)
        # 부칙 안의 제2조는 본칙 조 ID와 섞이지 않는다.
        self.assertNotIn(("article", "제2조"), ids)

    def test_no_loss_no_overflow_for_several_limits(self):
        for max_tokens, overlap in ((128, 16), (64, 8), (48, 0), (256, 32)):
            chunks = self.chunks(max_tokens, overlap)
            self.assertEqual(verify_chunks(self.reg, chunks, self.tokenizer, max_tokens), [],
                             (max_tokens, overlap))

    def test_metadata_is_kept_and_no_page_numbers(self):
        for chunk in self.chunks():
            self.assertEqual(chunk["official_url"], URL)
            self.assertEqual(chunk["revision_id"], "20:1623")
            self.assertEqual(chunk["regulation_id"], "20")
            self.assertTrue(chunk["id"].startswith("20:1623:"))
            self.assertNotIn("page_start", chunk)
            self.assertEqual(self.text[chunk["source_start"]:chunk["source_end"]], chunk["content"])

    def test_chapter_heading_is_kept_with_next_article(self):
        chunk = self.by_article(self.chunks(), "제4조")[0]
        self.assertTrue(chunk["content"].startswith("제 2 장 직 제"))
        self.assertEqual(chunk["chapter"], "제 2 장 직 제")
        section = self.by_article(self.chunks(), "제27조1")[0]
        self.assertEqual(section["chapter"], "제 1 절 교육과정")

    def test_table_cells_are_preserved_in_order(self):
        joined = "\n".join(c["content"] for c in self.by_article(self.chunks(48, 0), "제32조"))
        for cell in ("등 급", "A⁺", "95-100", "4.5", "F", "0-59", "0.0"):
            self.assertIn(cell, joined)
        self.assertLess(joined.index("95-100"), joined.index("0-59"))

    def test_long_article_is_split_with_prefix_and_overlap(self):
        chunks = self.by_article(self.chunks(48, 8), "제40조")
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertTrue(chunk["embedding_text"].startswith("[한성대학교 학칙] 제40조"))
        self.assertLess(chunks[1]["source_start"], chunks[0]["source_end"])

    def test_cross_references_are_extracted(self):
        refs = {}
        for chunk in self.chunks(256):
            refs.setdefault(chunk["article_id"], []).extend(chunk["references"])
        self.assertIn("제41조", refs["제40조"])
        self.assertEqual(refs["제3조"], ["별표Ⅰ"])
        self.assertEqual(refs["부칙(2025.4.11.공포)제2조"], ["별표2"])
        self.assertEqual(extract_references("제3조의2 및 제 3 조에 따라", "제3조"), ["제3조의2"])

    def test_deleted_articles_are_kept_and_flagged(self):
        chunk = self.by_article(self.chunks(), "제17조")[0]
        self.assertTrue(chunk["deleted"])
        self.assertIn("삭제 (2005. 4.12)", chunk["content"])
        self.assertFalse(self.by_article(self.chunks(), "제1조")[0]["deleted"])

    def test_verify_detects_loss_and_tampering(self):
        chunks = self.chunks()
        self.assertTrue(verify_chunks(self.reg, chunks[1:], self.tokenizer))
        broken = [dict(chunks[0], content=chunks[0]["content"] + "x")] + chunks[1:]
        self.assertTrue(verify_chunks(self.reg, broken, self.tokenizer))
        self.assertTrue(verify_chunks(self.reg, chunks + [chunks[0]], self.tokenizer))

    def test_invalid_inputs(self):
        with self.assertRaises(ChunkingError):
            chunk_regulation(dict(self.reg, official_url=""), self.tokenizer)
        with self.assertRaises(ChunkingError):
            chunk_regulation(self.reg, self.tokenizer, 16, 16)


if __name__ == "__main__":
    unittest.main()
