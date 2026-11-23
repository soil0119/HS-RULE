# Task 1 brief — PDF 수집·저장 기반

## 목적과 소유권

저장소 `/Users/sh/Documents/GitHub/HS-RULE`, 브랜치 `codex/pdf-foundation`에서 구현한다. 사용자 최종 입력원은 제공할 PDF다. 원격 규정 재수집은 하지 않는다. 실제 PDF는 아직 없으므로 합성 PDF로 검증한다.

수정 가능: `hsrule/__init__.py`, `hsrule/pdf_ingestion.py`, `hsrule/embeddings.py`, `hsrule/repository.py`, `hsrule/schema.sql`, `build_db.py`, `tests/test_pdf_ingestion.py`, `tests/test_repository.py`, 필요한 합성 fixture. 다른 파일은 수정하지 않는다. 기존 ZIP·크롤러·JSON·사용자 변경을 보존한다.

## 구현 계약

### PDF 및 임베딩

- `load_pdf(data: bytes, filename: str, title: str | None = None, visibility: str = "private", document_id: str | None = None) -> dict`.
- 반환 문서: `id`, `title`, `filename`, `visibility`, `content_hash`, `page_count`, `pages`, `chunks`(초기 빈 목록), `warnings`.
- `pages`: `[{"page_number": 1, "text": "..."}]`이며 빈 페이지도 보존한다. 페이지는 1부터 센다.
- `content_hash`는 원본 바이트 SHA-256, 기본 `id`는 서버 발급 UUID hex. 파일명은 표시용이며 로컬 경로가 되지 않는다.
- 실제 pdfminer 페이지 API로 추출한다. 손상 PDF와 전체 페이지에 텍스트가 없는 PDF는 안전한 명시적 예외로 거부한다. 빈 일부 페이지는 경고로 기록하고 전체/색인 페이지 범위를 반환한다.
- 텍스트의 줄바꿈과 전문은 보존한다. 입력 상한은 기본 20MiB, 최대 1,000페이지로 검증하며 예외를 호출자가 4xx로 구분할 수 있게 한다.
- `chunk_pages(document: dict, tokenizer, max_tokens: int = 128, overlap_tokens: int = 16) -> list[dict]`.
- 청크: `id`, `ordinal`, `page_start`, `page_end`, `article`, `content`, `embedding_text`. ID는 문서 ID·페이지·순번을 포함한다. 청크는 페이지를 넘지 않아도 된다.
- `embedding_text`는 tokenizer의 특수 토큰을 포함한 실제 길이가 `max_tokens` 이하여야 한다. 제목·조항 접두어가 본문 한도를 침범하지 않게 예산을 계산하고 장문 마지막 부분도 보존한다.
- 조항명은 줄 시작의 명시적 조항 헤더만 보조 메타데이터로 사용한다. 문장 속 `제2조에 따른` 교차 참조로 본문을 쪼개지 않는다. 페이지 근거는 항상 존재한다.
- `LocalEmbeddings(model_name: str = "paraphrase-multilingual-MiniLM-L12-v2")` 클래스는 `tokenizer` 속성, `embed_documents(texts: list[str]) -> list[list[float]]`, `embed_query(text: str) -> list[float]`를 제공한다. 384차원, 정규화 임베딩. SentenceTransformer는 첫 실제 사용에서만 로딩한다. 모듈 import에서 다운로드하지 않는다.

### Repository

`RegulationRepository(database_url: str)`의 메서드:

- `initialize() -> None`, `health() -> dict`.
- `save_document(document: dict, embeddings: list[list[float]]) -> dict`.
- `list_documents(include_private: bool = False) -> list[dict]`.
- `get_document(document_id: str, include_private: bool = False) -> dict | None`.
- `delete_document(document_id: str) -> dict | None`.
- `vector_candidates(embedding: list[float], include_private: bool = False, limit: int = 20) -> list[dict]`.
- `keyword_candidates(query: str, embedding: list[float], include_private: bool = False, limit: int = 20) -> list[dict]`.

검색 후보: `id`(청크 ID), `document_id`, `title`, `article`, `content`, `page_start`, `page_end`, `score`(실제 코사인), `visibility`. 문서 목록에는 `id`, `title`, `filename`, `visibility`, `page_count`, `chunks`(개수), `warnings`, `created_at`이 있어야 한다. `get_document`는 추가 메타데이터·페이지를 포함할 수 있다.

- 새 테이블 `hs_documents`, `hs_chunks`와 pgvector 384차원 열을 사용한다. 기존 `rule_chunks`는 읽거나 삭제하지 않는다.
- 원본 페이지 전문과 경고는 문서 메타데이터에 JSONB 등으로 보존한다. 원본 PDF 바이트는 DB에 넣지 않는다.
- 내용 해시 중복을 DB 고유 제약으로 처리한다. `DuplicateDocumentError` 등 명시적 예외를 공개한다.
- 문서·청크는 단일 트랜잭션으로 저장한다. 차원·개수·문서 ID 불일치·DB 삽입 실패에서 기존 자료가 남아야 한다. 연결·커서를 반드시 닫는다.
- 공개 범위 필터를 SQL의 LIMIT 이전에 적용한다. 기본은 public만, `include_private=True`에서 둘 다 조회한다.
- 벡터 후보는 실제 거리 순서, 키워드 후보는 파라미터 바인딩된 제목·조항·본문 검색과 결정적인 어휘 순서다. 키워드 후보에도 전달된 쿼리 임베딩으로 실제 코사인을 계산한다. 고정 0.85/0.7은 사용하지 않는다. 키워드와 후보 수는 제한한다.
- `health()`는 DB 연결만 되는 빈 색인을 준비 완료로 보고하지 않으며 문서·청크 수를 제공한다.

### PDF CLI

`build_db.py`를 PDF 경로 입력 CLI로 바꾼다. `python build_db.py file1.pdf file2.pdf --visibility public`을 지원한다. 기본 visibility는 private이고 DB URL은 로컬 `.env`/환경변수에서 읽는다.

- 원본은 프로젝트 `uploads/{id}.pdf`에 보관한다. 실제 파일명을 저장 경로에 결합하지 않는다.
- 파일 저장·DB 저장 실패에서 새로 만든 파일만 정리하고 기존 파일·문서는 유지한다. 중복은 명확하게 알린다.
- 원격 수집, 사용자 `.env` 생성, 실제 키 사용, 모델 API 호출을 하지 않는다. CLI를 실제 사용자 PDF 없이 실행하지 않는다. 임베딩은 CLI 실제 실행 시에만 사용한다.
- `DROP TABLE`과 부분 배치 실패 후 성공 종료를 제거한다. 오류 종료 코드는 0이 아니어야 한다.

## 필수 테스트와 실행

표준 `unittest`로 테스트를 먼저 작성하고 결함/미구현으로 실패하는 것을 확인한 뒤 구현한다.

1. 실제 생성한 2페이지 PDF의 텍스트와 페이지 번호·내용 해시 보존.
2. 깨진 PDF·전체 무텍스트 PDF·과도한 크기 거부, 일부 빈 페이지 경고.
3. 한국어 교차 참조·장문 마지막 문장 보존, 청크 ID 고유성과 128토큰 한도. 실제 토크나이저 검증이 없으면 대역 검증임을 보고한다.
4. 공개/비공개 필터, 중복, 임베딩 개수/차원, 삽입 실패 롤백과 기존 문서 보존.
5. CLI 실패는 비정상 종료, 원래 파일명으로 저장 경로를 만들지 않음, 모듈 import는 외부 연결/다운로드 없음.

전체: `.venv/bin/python -m unittest discover -s tests -v`와 지정 Python compile, `git diff --check`.
실제 DB 검증이 필요하면 **신규 임시** pgvector 컨테이너만 localhost 임의 포트·운영 볼륨 없이 사용하고 종료 후 제거한다. 기존 컨테이너를 변경하지 않는다. 운영 DB·외부 Groq·사용자 PDF·원본 쿠키 사용 금지.

## 커밋·보고

파서/청커, Repository, CLI 등 테스트가 통과하는 컴포넌트 단위로 지정 파일만 자주 커밋한다. 다른 파일의 변경을 되돌리지 않는다. push·PR·머지는 부모가 한다. 하위 에이전트를 만들지 않는다.

보고서: `docs/superpowers/plans/task-1-pdf-foundation-report.md`. DONE / DONE_WITH_CONCERNS / NEEDS_CONTEXT / BLOCKED 중 상태, 변경·커밋 SHA·정확한 테스트 명령과 결과·실제/대역 검증 구분·남은 제한을 기록한다. 부모에게는 상태·커밋·테스트 요약·우려만 반환한다.
