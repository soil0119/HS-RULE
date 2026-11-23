import hashlib
import io
import os
import tempfile
import string
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
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
        from hsrule.pdf_ingestion import PdfIngestionError, load_pdf

        for data in [b"bad pdf", make_pdf([], []), b"x" * (20 * 1024**2 + 1)]:
            with self.subTest(size=len(data)), self.assertRaises(PdfIngestionError):
                load_pdf(data, "bad.pdf")

    def test_blank_page_is_reported_without_shifting_citations(self):
        from hsrule.pdf_ingestion import load_pdf

        doc = load_pdf(make_pdf(["One"], [], ["Three"]), "display.pdf")
        self.assertEqual(doc["pages"][1], {"page_number": 2, "text": ""})
        self.assertEqual(doc["indexed_pages"], [1, 3])
        self.assertIn("2", " ".join(doc["warnings"]))

    def test_page_limit(self):
        from hsrule.pdf_ingestion import PdfIngestionError, load_pdf

        with self.assertRaises(PdfIngestionError):
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


class FixtureEmbeddings:
    tokenizer = local_tokenizer("First sample")

    def embed_documents(self, texts):
        return [[1.] + [0.] * 383 for _ in texts]


class CLITests(unittest.TestCase):
    def test_cli_import_is_offline_without_settings(self):
        result = subprocess.run(
            [sys.executable, "-c", "import sys; import build_db; assert 'sentence_transformers' not in sys.modules"],
            env={**os.environ, "DATABASE_URL": "", "PYTHON_DOTENV_DISABLED": "1", "HF_HUB_OFFLINE": "1"},
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_storage_uses_issued_id_and_cleans_up_only_new_files(self):
        from hsrule.pdf_ingestion import store_pdf
        from hsrule.repository import DuplicateDocumentError

        class DuplicateOnSecondSave:
            existing = None

            def save_document(self, doc, vectors):
                if self.existing == doc["content_hash"]:
                    raise DuplicateDocumentError("Duplicate PDF")
                self.existing = doc["content_hash"]
                return doc

        pdf = make_pdf(["First sample"])
        repo = DuplicateOnSecondSave()
        with tempfile.TemporaryDirectory() as directory:
            uploads = Path(directory) / "uploads"
            doc = store_pdf(pdf, "../../name.pdf", repo, FixtureEmbeddings(), uploads)
            original = uploads / (doc["id"] + ".pdf")
            self.assertEqual(doc["visibility"], "private")
            self.assertEqual(original.read_bytes(), pdf)
            with self.assertRaises(DuplicateDocumentError):
                store_pdf(pdf, "../../name.pdf", repo, FixtureEmbeddings(), uploads)
            self.assertEqual(list(uploads.iterdir()), [original])
            with patch("hsrule.pdf_ingestion.uuid4", return_value=UUID(doc["id"])):
                with self.assertRaises(FileExistsError):
                    store_pdf(make_pdf(["Other"]), "new.pdf", repo, FixtureEmbeddings(), uploads)
            self.assertEqual(original.read_bytes(), pdf)

    def test_batch_failure_preserves_first_pdf_and_exits_nonzero(self):
        import build_db

        class TemporaryRepository:
            def __init__(self, _url):
                pass

            def initialize(self):
                pass

            def save_document(self, doc, vectors):
                return {"id": doc["id"], "chunks": len(vectors), "visibility": doc["visibility"]}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.pdf"
            source.write_bytes(make_pdf(["First sample"]))
            with patch.object(build_db, "UPLOAD_DIR", root / "uploads"), \
                    patch.object(build_db, "RegulationRepository", TemporaryRepository), \
                    patch.object(build_db, "LocalEmbeddings", FixtureEmbeddings), \
                    patch.dict(os.environ, {"DATABASE_URL": "fixture", "PYTHON_DOTENV_DISABLED": "1"}):
                status = build_db.main([str(source), str(root / "missing.pdf"), "--visibility", "public"])
            self.assertNotEqual(status, 0)
            stored, = (root / "uploads").iterdir()
            self.assertNotEqual(stored.name, source.name)
            self.assertEqual(stored.read_bytes(), source.read_bytes())

    def test_init_only_prepares_schema_without_embedding_model(self):
        import build_db

        with patch.object(build_db, "RegulationRepository") as repository, \
                patch.object(build_db, "LocalEmbeddings", side_effect=AssertionError("model used")), \
                patch.dict(os.environ, {"DATABASE_URL": "fixture", "PYTHON_DOTENV_DISABLED": "1"}):
            self.assertEqual(build_db.main(["--init-only"]), 0)
            repository.return_value.initialize.assert_called_once_with()
            for args in [[], ["--init-only", "anything.pdf"]]:
                with self.subTest(args=args), self.assertRaises(SystemExit) as result:
                    build_db.main(args)
                self.assertNotEqual(result.exception.code, 0)
