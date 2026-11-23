"""Document analysis and PDF/DOCX export routes."""

import io
import json
from pathlib import Path, PureWindowsPath
from typing import Annotated
from xml.sax.saxutils import escape
from zipfile import ZipFile

from docx import Document as DocxDocument
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import StreamingResponse
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont, TTFError
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer

from hsrule_web.auth import require_admin
from hsrule_web.contracts import (
    MAX_JSON_BODY, MAX_MULTIPART_BODY, ConflictResponse, ConflictResult, ExportRequest,
    Locale, QuerySource, call_rag, require_rag, validate_text,
)
from hsrule_web.documents import MAX_PDF_BYTES, display_filename, parse_pdf, read_upload
from hsrule_web.errors import APIError

router = APIRouter(tags=["teacher"])


def _pdf_font() -> str:
    for filename in (
        "C:/Windows/Fonts/malgun.ttf",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf",
        "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
        "/System/Library/Fonts/AppleGothic.ttf",
    ):
        if Path(filename).is_file():
            try:
                pdfmetrics.registerFont(TTFont("RegulationKorean", filename))
            except (OSError, TTFError):
                continue
            return "RegulationKorean"
    name = "HYSMyeongJo-Medium"
    pdfmetrics.registerFont(UnicodeCIDFont(name))
    return name


_PDF_FONT = _pdf_font()


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp949"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("Unsupported text encoding")


def _extract_conflict(data: bytes, filename: str) -> str:
    extension = PureWindowsPath(filename).suffix.lower()
    if extension not in (".pdf", ".docx", ".txt", ".json"):
        raise APIError(415, "unsupported_file_type", "PDF, DOCX, TXT 또는 JSON 파일을 선택해 주세요.")
    try:
        if extension == ".pdf":
            pages, _ = parse_pdf(data)
            text = "\n\n".join(page.text for page in pages)
        elif extension == ".docx":
            with ZipFile(io.BytesIO(data)) as archive:
                entries = archive.infolist()
                if len(entries) > 2000 or sum(entry.file_size for entry in entries) > MAX_PDF_BYTES:
                    raise APIError(413, "file_too_large", "압축을 푼 DOCX가 허용 한도를 초과합니다.")
            document = DocxDocument(io.BytesIO(data))
            text = "\n".join(p.text for p in document.paragraphs)
        elif extension == ".json":
            text = json.dumps(json.loads(data.decode("utf-8-sig")), ensure_ascii=False, allow_nan=False)
        else:
            text = _decode_text(data)
        text = validate_text(text).strip()
    except APIError:
        raise
    except Exception:
        raise APIError(422, "invalid_document", "파일에서 텍스트를 읽을 수 없습니다.") from None
    if not text:
        raise APIError(422, "invalid_document", "파일에 분석할 텍스트가 없습니다.")
    if len(text) > 100_000:
        raise APIError(413, "text_too_large", "분석할 텍스트는 최대 100,000자까지 허용합니다.")
    return text


@router.post(
    "/conflict/", response_model=ConflictResponse, dependencies=[Depends(require_admin)],
    max_body_size=MAX_MULTIPART_BODY,
)
def analyze_conflict(
    request: Request,
    file: Annotated[UploadFile, File()],
    language: Annotated[Locale, Form()] = "ko",
):
    backend = require_rag(request.app.state.rag_backend)
    filename = display_filename(file.filename or "")
    text = _extract_conflict(read_upload(file.file), filename)
    result = call_rag(backend.analyze_conflict, ConflictResult, text, filename,
                      include_private=True, language=language)
    return ConflictResponse(**result.model_dump(), filename=filename)


def _paragraph_text(text: str) -> str:
    return escape(text).replace("\n", "<br/>")


def _source_label(source: QuerySource) -> str:
    pages = str(source.page_start)
    if source.page_end != source.page_start:
        pages += f"–{source.page_end}"
    label = f"• {source.title} (p. {pages})"
    if source.score is not None:
        score = f"{source.score:.0%}" if abs(source.score) <= 1 else f"{source.score:g}"
        label += f" · 유사도 {score}"
    return label


@router.post("/export/pdf", max_body_size=MAX_JSON_BODY)
def export_pdf(req: ExportRequest):
    buf = io.BytesIO()
    fn = _PDF_FONT

    def ps(name, size, color="#1A2340", sa=6, leading=None):
        return ParagraphStyle(
            name, fontName=fn, fontSize=size, spaceAfter=sa,
            textColor=colors.HexColor(color),
            leading=leading or size * 1.65
        )

    doc = SimpleDocTemplate(buf, pagesize=A4,
        leftMargin=2.2*cm, rightMargin=2.2*cm,
        topMargin=2.5*cm, bottomMargin=2*cm)

    story = []

    # 헤더
    story.append(Paragraph("한성대학교 규정 마스터 AI", ps("meta", 9, "#6B7A99", sa=4)))
    story.append(Paragraph(_paragraph_text(req.title or "규정 답변 리포트"), ps("title", 16, "#003087", sa=10)))
    story.append(HRFlowable(width="100%", thickness=2, color=colors.HexColor("#003087"), spaceAfter=14))

    # 질문
    if req.question:
        story.append(Paragraph("질문", ps("qlabel", 10, "#003087", sa=4)))
        story.append(Paragraph(_paragraph_text(req.question), ps("qtxt", 11, "#1A2340", sa=12)))

    # 답변
    story.append(Paragraph("답변", ps("alabel", 10, "#003087", sa=6)))
    for line in (req.content or "").split("\n"):
        stripped = line.strip()
        if not stripped:
            story.append(Spacer(1, 4))
            continue
        # 출처 라인은 색 달리
        if stripped.startswith("(출처"):
            story.append(Paragraph(_paragraph_text(stripped), ps("src", 9, "#0050B3", sa=3)))
        else:
            story.append(Paragraph(_paragraph_text(stripped), ps("body", 11, "#1A2340", sa=4)))

    # 참조 조항
    if req.sources:
        story.append(Spacer(1, 10))
        story.append(HRFlowable(width="100%", thickness=0.5,
                                color=colors.HexColor("#D1DCF0"), spaceAfter=8))
        story.append(Paragraph("참조 조항", ps("srclabel", 10, "#003087", sa=6)))
        for source in req.sources:
            story.append(Paragraph(_paragraph_text(_source_label(source)), ps("srcitem", 9, "#6B7A99", sa=3)))

    doc.build(story)
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="hansung_report.pdf"'})


# ── POST /export/docx ─────────────────────────────────────────────
def _set_font(run, name="맑은 고딕", size=None):
    """run에 한글 폰트 직접 지정"""
    run.font.name = name
    run._element.rPr.rFonts.set(qn("w:eastAsia"), name)
    if size:
        run.font.size = Pt(size)

def _add_para(doc, text, font="맑은 고딕", size=10.5, bold=False, color=None, style=None):
    p = doc.add_paragraph(style=style)
    run = p.add_run(text)
    _set_font(run, font, size)
    run.bold = bold
    if color:
        run.font.color.rgb = RGBColor(*color)
    return p

@router.post("/export/docx", max_body_size=MAX_JSON_BODY)
def export_docx(req: ExportRequest):
    doc = DocxDocument()

    # 기본 Normal 스타일에 한글 폰트 적용
    normal = doc.styles["Normal"]
    normal.font.name = "맑은 고딕"
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "맑은 고딕")
    normal.font.size = Pt(10.5)

    # 제목
    h = doc.add_heading(req.title or "규정 답변 리포트", level=1)
    if h.runs:
        _set_font(h.runs[0], "맑은 고딕", 16)
        h.runs[0].font.color.rgb = RGBColor(0, 48, 135)

    _add_para(doc, "한성대학교 규정 마스터 AI", size=9, color=(107, 122, 153))
    doc.add_paragraph()

    # 질문
    if req.question:
        _add_para(doc, "[ 질문 ]", size=11, bold=True, color=(0, 48, 135))
        _add_para(doc, req.question, size=11)
        doc.add_paragraph()

    # 답변
    _add_para(doc, "[ 답변 ]", size=11, bold=True, color=(0, 48, 135))
    for line in (req.content or "").split("\n"):
        stripped = line.strip()
        if not stripped:
            doc.add_paragraph()
            continue
        color = (0, 80, 179) if stripped.startswith("(출처") else None
        size  = 9 if stripped.startswith("(출처") else 11
        _add_para(doc, stripped, size=size, color=color)

    # 참조 조항
    if req.sources:
        doc.add_paragraph()
        _add_para(doc, "[ 참조 조항 ]", size=11, bold=True, color=(0, 48, 135))
        for source in req.sources:
            _add_para(doc, _source_label(source), size=9, color=(107, 122, 153))

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return StreamingResponse(buf,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": 'attachment; filename="hansung_report.docx"'})
