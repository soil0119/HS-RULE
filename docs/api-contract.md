# 규정 웹 API 계약

## 실행과 설정

Python 3.11에서 웹 의존성을 설치합니다. PostgreSQL에는 기존 테이블과 독립된 `web_documents`와 `web_document_pages`를 초기화합니다. `python -m hsrule_web.documents`는 같은 설정으로 다시 실행할 수 있습니다.

```sh
python -m pip install -r requirements-web.txt
cp .env.example .env
python -m hsrule_web.documents
uvicorn server:app --host 127.0.0.1 --port 8000 --no-proxy-headers
```

| 설정 | 값 |
|---|---|
| `DATABASE_URL` | 일반 PostgreSQL 연결 문자열. 초기화와 보관 API에 필요합니다. |
| `UPLOAD_DIR` | PDF 원본 디렉터리. 기본값은 저장소의 `uploads/`입니다. 여러 워커에서 같은 DB를 사용하면 같은 영속 파일 디렉터리를 사용합니다. |
| `ADMIN_ID`, `ADMIN_PW`, `SECRET_KEY` | 관리자를 명시적으로 설정합니다. 비어 있거나 예시 자격 증명을 사용하면 로그인은 503입니다. `SECRET_KEY`에는 최소 32자의 무작위 문자열을 설정합니다. |
| `PUBLIC_ORIGIN` | 기본값 `https://hs.officeworkkt.com`. 허용 Host는 지정된 출처의 호스트, `localhost`, `127.0.0.1`입니다. CORS는 이 출처와 루프백 개발 출처를 허용합니다. |
| `GROQ_API_KEY` | 외부 RAG 담당자가 사용하는 선택 설정입니다. 웹 서버에 필요한 설정은 위 행에 있습니다. |

`create_app(settings=None, document_store=None, rag_backend=None)`는 설정을 읽고 의존성을 보관합니다. 초기화·연결 확인은 API 호출 또는 초기화 명령에서 수행합니다. 테스트에서 `Settings(allowed_hosts=("testserver",))`로 명시적 Host를 허용할 수 있습니다.

`GET /`, `GET /login-page`, `GET /upload`, `GET /HSU_logo.png`는 의존성 없이 서빙합니다. `GET /health/live`는 `{"ok":true}`를 반환합니다. `GET /health`는 관리자 설정, 문서 저장소 스키마·디렉터리, 주입된 RAG 어댑터가 모두 준비됐을 때 200입니다. 어댑터는 주입 여부를 확인합니다. 준비되지 않았으면 503 `not_ready`입니다.

## 인증과 오류

`POST /login`에 `{"username":"...","password":"..."}`를 보냅니다. 성공 응답은 `{"success":true,"token":"..."}`입니다. 관리자 경로에서는 `Authorization: Bearer <token>`을 보냅니다. HS256 토큰의 발급자는 `hsrule-web`, 대상은 `hsrule-admin`, 주체는 설정한 관리자 ID입니다. 만료는 발급 12시간 후입니다.

로그인 시도는 1분에 연결 피어당 5회, 워커 전체 120회로 제한합니다. POST·DELETE 및 문서 조회·준비 상태 처리는 워커당 동시에 8개까지 받습니다. 제한 응답은 429와 `Retry-After`를 포함합니다. 이 한도는 워커별 메모리에 있습니다. 위 실행 명령은 프록시 헤더 해석을 꺼 연결 피어를 기준으로 삼으므로 프록시를 통하면 피어 한도를 다른 사용자와 공유할 수 있습니다.

API 오류 응답에는 문자열 `detail`, 안정적인 `code`, `request_id`가 있습니다. `X-Request-ID` 응답 헤더에도 같은 ID가 전달됩니다. 대표 코드는 `invalid_input`(422), `authentication_required`·`invalid_token`(401), `document_not_found`(404), `duplicate_document`(409), `request_too_large`·`file_too_large`(413), `rag_not_connected`(503), `rag_invalid_response`·`rag_backend_error`(502)입니다. 클라이언트는 `code`로 메시지를 번역하거나 화면 상태를 바꿀 수 있습니다.

## 문서 보관과 원본 열기

| 요청 | 권한 | 성공 응답 |
|---|---|---|
| `GET /rules` | 공개 | `{chapters:[{chapter,rules}],total}`. 현재 공개 문서는 `업로드 규정` 편에 모입니다. 각 항목은 문서 메타데이터와 `name`·`url`·`code`·`dept`·`uploaded`입니다. `name`은 제목, `url`은 원본 경로입니다. |
| `GET /uploaded-rules` | 관리자 | `{rules:[문서 메타데이터]}`. 공개·비공개 문서를 포함합니다. |
| `POST /upload-regulation` | 관리자 | multipart `file` 및 선택 `title`, `visibility`. 성공 시 201 `{success:true,document}`. |
| `DELETE /uploaded-rules/{document_id}` | 관리자 | `{success:true,document_id}`. |
| `GET /documents/{document_id}` | 공개 또는 관리자 | 문서 메타데이터. 비공개는 관리자 토큰이 필요합니다. |
| `GET /documents/{document_id}/file` | 공개 또는 관리자 | PDF inline 및 바이트 범위 요청. 비공개는 토큰을 붙인 fetch로 읽을 수 있습니다. |

문서 메타데이터는 `id`, `title`, `filename`, `visibility`, `content_hash`(SHA-256), `page_count`, `warnings`, `status:"stored"`, `index_status:"not_connected"`입니다. `id`는 서버가 생성한 32자리 소문자 16진 UUID입니다. 제목은 기본 파일명에서 확장자를 뺀 값이고, 공개 범위는 기본 `private`입니다. `visibility=public`은 공개 목록과 원본 접근을 허용합니다. 제목과 파일명은 표시용 원문으로, 브라우저에서 텍스트로 표시합니다.

PDF 한 파일당 최대 20 MiB·1,000페이지이며, 같은 SHA-256의 파일은 409입니다. 손상되거나 모든 페이지에서 추출 가능한 텍스트가 없으면 422입니다. 일부 빈 페이지의 번호와 빈 텍스트는 그대로 저장되며 `warnings`에 페이지 안내가 포함됩니다. 원본은 `UPLOAD_DIR/<id>.pdf`에 기록합니다. DB를 백업할 때 원본 디렉터리도 같은 시점에 보관합니다. 프로세스가 삭제 도중 종료되거나 파일 복구가 실패하면 디렉터리의 `.<id>.pdf.deleting` 파일이 복구 원본입니다. 해당 ID의 DB 행이 있으면 원래 파일명으로 복구할 수 있습니다.

## 질의, 분석 및 내보내기

- `POST /query`는 `{question,messages?,language?}`를 받습니다. 질문은 최대 2,000자입니다. `messages`에는 이번 요청 이전의 `{role:"user"|"assistant",content}`를 시간순으로 최대 12개, 각각 최대 4,000자 보냅니다. `language`는 `ko` 또는 `en`이며 기본은 `ko`입니다.
- 질의 성공 응답은 `{answer,sources,found,followups,dept?,dept_phone?}`입니다. 출처는 `{document_id,page_start,page_end,title,url,chunk_id?,score?}`입니다. 페이지는 1부터 시작합니다. 서버가 공개 저장 PDF와 페이지 범위를 확인하고 제목과 `/documents/{id}/file#page=N`을 생성합니다. 근거를 찾았다고 반환했지만 검증 가능한 출처가 없으면 502입니다.
- `POST /conflict/`는 관리자 전용 multipart `file` 및 선택 `language`를 받습니다. PDF·DOCX·TXT·JSON, 최대 20 MiB, 추출 텍스트 최대 100,000자입니다. `language`는 질의와 같고, 결과는 `{filename,summary,conflicts,recommendations}`입니다. 충돌 항목은 `{regulation,article,issue,severity}`이며 심각도는 `높음`·`보통`·`낮음` 또는 `high`·`medium`·`low`입니다. 어댑터가 없으면 파일 텍스트 추출 전에 503입니다.
- `POST /export/pdf`, `POST /export/docx`는 `{content,title?,question?,sources?}`를 받아 파일을 반환합니다. `sources`에는 질의 응답의 출처 객체를 그대로 보냅니다. PDF는 환경에서 나눔고딕·맑은 고딕·AppleGothic을 찾으면 해당 폰트를 포함합니다. 폰트가 없으면 PDF 뷰어의 한국어 CID 폰트 지원이 필요합니다. PDF 이모지와 특수 문자는 폰트 범위에 따르며 DOCX 글꼴 표시는 클라이언트 환경에 따릅니다.

## RAG 연결 계약

서비스 엔트리포인트는 설정과 저장소를 공유한 뒤 동기식 어댑터를 주입합니다. 어댑터가 응답 언어를 선택하고, 웹 API는 `language`를 검증하여 전달합니다. 공개 질의는 항상 `include_private=False`, 관리자 분석은 `True`입니다. 어댑터도 이 범위를 적용해 본문과 출처가 모두 같은 권한 범위를 사용하도록 합니다.

```python
from hsrule_web.documents import DocumentStore
from hsrule_web.settings import Settings
from server import create_app

settings = Settings.from_env()
store = DocumentStore(settings.database_url, settings.upload_dir)
# Implement create_regulation_adapter in the separate retrieval service.
app = create_app(settings, document_store=store, rag_backend=create_regulation_adapter(store))
```

`hsrule_web.contracts.RagBackend` 인터페이스는 다음 두 메서드를 사용합니다.

```python
def answer(
    question: str, messages: list[dict[str, str]], include_private: bool = False,
    *, language: Literal["ko", "en"] = "ko",
) -> dict: ...

def analyze_conflict(
    text: str, filename: str, include_private: bool = True,
    *, language: Literal["ko", "en"] = "ko",
) -> dict: ...
```

`answer`는 `answer`, `sources`, `found`, `followups` 및 선택 `dept`, `dept_phone`을 반환합니다. 출처 원소는 `document_id`, `page_start`, `page_end` 및 선택 `chunk_id`, `score`입니다. `analyze_conflict`는 `summary`, `conflicts`, `recommendations`를 반환합니다. API가 원본의 표시 파일명을 따로 채웁니다. 색인 생성·상태 관리는 어댑터 소유 작업입니다.

| 저장소 메서드 | 계약 |
|---|---|
| `initialize() -> None` | 원본 디렉터리와 PostgreSQL 일반 테이블을 생성합니다. |
| `health() -> bool` | 테이블 접근 및 원본 디렉터리를 확인합니다. |
| `list(*, include_private=False) -> list[DocumentMetadata]` | 업로드 규정 메타데이터 목록입니다. |
| `get(id, *, include_private=False, include_pages=False) -> SourceDocument \| None` | `metadata`와 `pages`가 있습니다. 페이지를 요청하면 `{page_number,text}`가 번호순으로 반환됩니다. |
| `save(data: bytes, filename: str, *, title=None, visibility="private") -> DocumentMetadata` | PDF 원본과 메타데이터 및 페이지를 보관합니다. |
| `delete(id) -> bool` | 문서와 페이지를 삭제합니다. 문서가 없으면 `False`입니다. |
| `file_path(id) -> Path` | 인증을 거친 요청에서 사용할 내부 파일 경로입니다. |
