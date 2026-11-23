import hashlib
import io
import string
import subprocess
import sys
import unittest
from pathlib import Path
from uuid import UUID

from reportlab.pdfgen import canvas
from tokenizers import Tokenizer, models, pre_tokenizers, processors
from transformers import PreTrainedTokenizerFast


def make_pdf(*pages):
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer)
    for lines in pages:
        for index, line in enumerate(lines):
            pdf.drawString(40, 760 - 16 * index, line)
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def local_tokenizer(text):
    symbols = list(dict.fromkeys("[UNK] [CLS] [SEP]".split() + list(set(text + string.ascii_letters))))
    symbols += ["##" + c for c in symbols if len(c) == 1]
    vocab = {token: idx for idx, token in enumerate(symbols)}
    backend = Tokenizer(models.WordPiece(vocab, unk_token="[UNK]"))
    backend.pre_tokenizer = pre_tokenizers.BertPreTokenizer()
    backend.post_processor = processors.TemplateProcessing(
        single="[CLS] $A [SEP]", special_tokens=[("[CLS]", 1), ("[SEP]", 2)]
    )
    return PreTrainedTokenizerFast(tokenizer_object=backend, unk_token="[UNK]")


class PDFTests(unittest.TestCase):
    def test_pdf_pages_keep_numbers_lines_and_hash(self):
        from hsrule.pdf_ingestion import load_pdf

        data = make_pdf(["First line", "Second line"], ["Last page"])
        doc = load_pdf(data, "../display.pdf")
        self.assertEqual(UUID(doc["id"]).hex, doc["id"])
        self.assertEqual(doc["filename"], "display.pdf")
        self.assertEqual(doc["visibility"], "private")
        self.assertEqual(doc["content_hash"], hashlib.sha256(data).hexdigest())
        self.assertEqual(doc["page_count"], 2)
        self.assertEqual(doc["chunks"], [])
        self.assertEqual(doc["warnings"], [])
        self.assertEqual([p["page_number"] for p in doc["pages"]], [1, 2])
        self.assertEqual(doc["pages"][0]["text"].splitlines(), ["First line", "Second line"])
        self.assertEqual(doc["pages"][1]["text"].strip(), "Last page")

    def test_invalid_empty_and_oversized_inputs(self):
        from hsrule.pdf_ingestion import PDFValidationError, load_pdf

        for data in [b"bad pdf", make_pdf([], []), b"x" * (20 * 1024**2 + 1)]:
            with self.subTest(size=len(data)), self.assertRaises(PDFValidationError):
                load_pdf(data, "bad.pdf")

    def test_blank_page_is_reported_without_shifting_citations(self):
        from hsrule.pdf_ingestion import load_pdf

        doc = load_pdf(make_pdf(["One"], [], ["Three"]), "display.pdf")
        self.assertEqual(doc["pages"][1], {"page_number": 2, "text": ""})
        self.assertEqual(doc["indexed_pages"], [1, 3])
        self.assertIn("2", " ".join(doc["warnings"]))

    def test_page_limit(self):
        from hsrule.pdf_ingestion import PDFValidationError, load_pdf

        with self.assertRaises(PDFValidationError):
            load_pdf(make_pdf(*([[]] * 1000), ["Too late"]), "long.pdf")

    def test_long_korean_text_and_cross_reference_have_cited_token_bounded_chunks(self):
        from hsrule.pdf_ingestion import chunk_pages

        heading = "제1조(목적)"
        text = heading + "\n" + "학교는 제2조에 따른 내용을 보존한다. " * 80 + "마지막 문장"
        title = "긴 제목 " * 150
        tokenizer = local_tokenizer(text + title)
        doc = dict(id="abc", title=title, pages=[
            dict(page_number=1, text=text),
            dict(page_number=2, text="제3조(시행)\n효력 발생"),
        ])
        chunks = chunk_pages(doc, tokenizer)
        self.assertGreater(len(chunks), 3)
        self.assertEqual(len({c["id"] for c in chunks}), len(chunks))
        self.assertEqual(len({c["ordinal"] for c in chunks}), len(chunks))
        first_page = [c for c in chunks if c["page_start"] == 1]
        self.assertEqual({c["article"] for c in first_page}, {heading})
        self.assertTrue(first_page[-1]["content"].endswith("마지막 문장"))
        self.assertEqual(chunks[-1]["article"], "제3조(시행)")
        for c in chunks:
            self.assertEqual(c["page_start"], c["page_end"])
            self.assertIn("abc", c["id"])
            self.assertIn(c["content"], c["embedding_text"])
            self.assertLessEqual(len(tokenizer.encode(c["embedding_text"])), 128)

    def test_embedding_module_and_constructor_are_lazy(self):
        script = (
            "import sys; from hsrule.embeddings import LocalEmbeddings; "
            "LocalEmbeddings(); assert 'sentence_transformers' not in sys.modules"
        )
        subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1], check=True)
