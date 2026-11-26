# HS-RULE

2026 한성대학교 AX 프론티어 공모전을 위한 규정 검색 AI 웹 서비스입니다.
한성대학교 규정관리시스템의 규정을 수집하고, 관련 조항을 검색해 답변과 근거를 제공합니다.

현재 공개 UI: **[hs.officeworkkit.com](https://hs.officeworkkit.com)**. 새 UI만 Cloudflare에 배포했으며, 질문·로그인·문서 처리 API와 RAG는 후속 연결 대상입니다. [배포·재배포 안내](docs/deployment.md)를 참고하세요.

첨부 프로젝트 `Hansung_AX-update_crawler.zip`을 기반으로 구성했습니다.
FastAPI·HTML 화면, Gemini(또는 Groq) 답변 생성, 로컬 문장 임베딩, PostgreSQL/pgvector를 사용합니다.

## 로컬 실행

Python 3.11과 Docker Compose를 사용합니다. 아래 명령은 프로젝트 루트에서 실행합니다.

```sh
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Windows PowerShell에서는 가상환경 활성화에 `.venv\Scripts\Activate.ps1`, 파일 복사에
`Copy-Item .env.example .env`를 사용합니다.

`.env`에서 다음 값을 설정합니다.

| 변수 | 용도 |
|---|---|
| `LLM_PROVIDER` | RAG 답변 LLM 제공자(`gemini` 기본, `groq`). |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | Gemini 사용 시 필요한 API 키와 모델명. |
| `GROQ_API_KEY`, `GROQ_MODEL` | `LLM_PROVIDER=groq`일 때만 필요합니다. |
| `DATABASE_URL` | 로컬 PostgreSQL 연결 주소 |
| `POSTGRES_PASSWORD` | Compose DB 비밀번호. `DATABASE_URL`의 비밀번호와 일치시킵니다. |
| `ADMIN_ID`, `ADMIN_PW` | 규정 업로드용 관리자 계정. 예시 비밀번호를 교체합니다. |
| `SECRET_KEY` | JWT 서명 키. 아래 명령으로 생성한 값을 넣습니다. |
| `HANSUNG_COOKIES_JSON` | 재수집 시 필요한 본인 브라우저 세션 쿠키 JSON |

```sh
python -c "import secrets; print(secrets.token_hex(32))"
docker compose up -d --wait db
python build_db.py
uvicorn server:app --host 127.0.0.1 --port 8000 --reload
```

브라우저에서 [로컬 서비스](http://127.0.0.1:8000)에 접속합니다.
규정 업로드는 `/login-page`에서 `.env`에 설정한 계정으로 로그인한 뒤 사용합니다.
`/health`의 `ok: true`와 `chunks`로 DB 연결 및 저장 상태를 확인합니다.

- `hansung_rules.json`에는 원본에 포함된 규정 315건이 있습니다. 첫 실행 때 재수집할 필요는 없습니다.
- 임베딩 모델 `paraphrase-multilingual-MiniLM-L12-v2`는 처음 실행할 때 다운로드됩니다.
- `build_db.py`는 **기존 `rule_chunks` 테이블을 삭제하고 다시 만듭니다.** 업로드 규정이 있는 DB에서는 백업 후 실행합니다.
- LLM API(Gemini·Groq)를 호출하는 검색·충돌 분석에는 유효한 API 키와 해당 계정의 모델 접근 권한이 필요합니다.
- DB는 로컬 `127.0.0.1:5432`에 바인딩합니다. 이미 해당 포트를 쓰고 있다면 Compose 포트와 `DATABASE_URL`을 함께 변경합니다.

## 프로젝트 구성

| 경로 | 역할 |
|---|---|
| `server.py` | 검색·로그인·규정 업로드 API와 HTML 서빙 |
| `routers/teacher.py` | 문서 충돌 분석, PDF·DOCX 내보내기 |
| `index.html`, `login.html`, `upload.html` | 검색·로그인·업로드 화면 |
| `crawler.py` | 규정 본문 수집 |
| `update_crawler.py` | PDF·DOCX·HWP·HWPX 첨부파일을 포함한 규정 수집 |
| `crawler_config.py` | 환경변수 기반 크롤러 쿠키 설정 |
| `patch_codes.py` | 수집한 규정에 규정 코드 추가 |
| `build_db.py` | 조항별 분할·임베딩·벡터 DB 구축 |
| `hansung_rules.json` | 원본 규정 스냅샷 |
| `check_yonga.py` | DB에서 연가 관련 조항을 조회하는 수동 확인 도구 |
| `compose.yaml` | 로컬 PostgreSQL/pgvector와 영속 볼륨 |
| `tests/` | 크롤러 환경 설정 회귀 검사 |

## 규정 재수집

규정관리시스템에 접근할 권한이 있는 본인 브라우저의 최신 쿠키를 `.env`에 설정합니다.
실제 쿠키는 소스코드나 커밋에 넣지 않습니다.

```dotenv
HANSUNG_COOKIES_JSON='{"JSESSIONID":"your-session-value"}'
```

```sh
# 본문만 수집할 때
python crawler.py
# 또는 첨부파일까지 수집할 때
python update_crawler.py
python patch_codes.py
```

두 크롤러 모두 기존 JSON에 있는 SEQ는 건너뜁니다. 기존 규정의 본문·첨부파일을 다시
수집하려면 JSON을 별도로 백업하고 해당 항목을 제거하거나 새 JSON으로 시작합니다.
DB 반영은 수집 결과를 확인한 뒤 `python build_db.py`로 진행합니다.
HWP 추출에는 설치된 `pyhwp`의 `hwp5txt` 명령을 사용합니다.
교직원 웹 업로드는 PDF·DOCX·TXT·JSON을 지원하며, HWP·HWPX는 크롤러에서만 처리합니다.

## 검증

```sh
python -m unittest discover -s tests -v
python -m compileall -q server.py routers crawler.py update_crawler.py crawler_config.py build_db.py patch_codes.py check_yonga.py
hwp5txt --help
```

위 검사는 API 키·DB 연결 없이 실행할 수 있습니다. 실제 검색·임베딩·문서 분석은
로컬 DB와 모델, Groq 키를 준비한 뒤 별도로 확인해야 합니다.
PDF 한글 출력에는 서버의 한글 TTF 폰트가 필요합니다. 원본 코드에서 Windows 맑은 고딕,
Linux 나눔고딕, macOS AppleGothic 경로를 탐색합니다.

## 데이터 보관

`.env`, 가상환경, 업로드 파일은 Git에서 제외합니다. `docker compose down`으로 DB를
중지해도 볼륨은 남습니다. `docker compose down -v`는 DB 볼륨까지 삭제하므로 주의합니다.
