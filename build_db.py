import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from hsrule.embeddings import LocalEmbeddings
from hsrule.pdf_ingestion import MAX_PDF_BYTES, PdfIngestionError, store_pdf
from hsrule.repository import DuplicateDocumentError, RegulationRepository

PROJECT_ROOT = Path(__file__).resolve().parent
UPLOAD_DIR = PROJECT_ROOT / "uploads"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="규정 PDF를 페이지별로 색인합니다.")
    parser.add_argument("files", metavar="PDF", type=Path, nargs="*")
    parser.add_argument("--visibility", choices=["private", "public"], default="private")
    parser.add_argument("--init-only", action="store_true", help="빈 데이터베이스의 스키마를 준비합니다.")
    args = parser.parse_args(argv)
    if bool(args.files) == args.init_only:
        parser.error("PDF 경로를 지정하거나 --init-only를 사용하세요.")

    load_dotenv(PROJECT_ROOT / ".env")
    url = os.getenv("DATABASE_URL")
    if not url:
        print("DATABASE_URL을 설정하세요.", file=sys.stderr)
        return 1
    repo = RegulationRepository(url)
    try:
        repo.initialize()
    except Exception:
        print("데이터베이스 스키마를 준비할 수 없습니다.", file=sys.stderr)
        return 1
    if args.init_only:
        print("데이터베이스 스키마를 준비했습니다.")
        return 0

    embeddings = LocalEmbeddings()
    failed = False
    for source in args.files:
        try:
            with source.open("rb") as file:
                data = file.read(MAX_PDF_BYTES + 1)
            saved = store_pdf(data, source.name, repo, embeddings, UPLOAD_DIR, visibility=args.visibility)
            print(f"{source.name} → {saved['id']} ({saved['chunks']}개 청크, {saved['visibility']})")
        except (PdfIngestionError, DuplicateDocumentError, OSError) as exc:
            print(f"{source.name}: {exc}", file=sys.stderr)
            failed = True
        except Exception:
            print(f"{source.name}: 색인 중 오류가 발생했습니다.", file=sys.stderr)
            failed = True
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
