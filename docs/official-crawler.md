# 공식 규정 수집기 (#16)

`hsrule_crawler/`는 한성대학교 규정관리시스템(`rule.hansung.ac.kr`)의 **공개 페이지만** 읽어 규정 목록·시행 개정·조항형 본문을 만든다. 로그인 쿠키 주입, 로그인 우회, TLS 검증 해제는 사용하지 않는다. 기존 `crawler.py`, `update_crawler.py`(쿠키 기반, SEQ 1~1000 순회)는 이 패키지로 대체할 예정이고, 전체 수집 CLI는 #17에서 만든다.

## 사이트 구조 (2026-11-25 확인)

| 요청 | 용도 | 주의 |
|---|---|---|
| `lawTree.do?LAWGROUP=1&MODE=srvlist` | `zNodes` JSON. 규정 323건의 SEQ·코드·제목·최신 개정(`hseq`)·분류 경로 | 이름의 `[폐지]`(35건)와 `(舊 …)`(29건)은 표기다. 제목에서 분리한다. |
| `lawDetail.do?SEQ=` | 개정 목록(`select#histroySeq`)·시행일(`showDate('YYYYMMDD')`)·담당부서(`.Dtit`) | 최신 개정이 시행 전일 수 있다(`[시행예정]`). |
| `lawFullContent.do?SEQ=&SEQ_HISTORY=` | 본문 `#lawcontent` | **SEQ를 무시하고 SEQ_HISTORY만 본다.** 없는 SEQ도 다른 규정을 200으로 준다. |
| LAWGROUP 2(지침)·4(기타자료), `robots.txt` | — | `errorPage.do`로 보내는 HTTP 200 껍데기. 공개 범위가 아니다. |

본문 DOM: `.lawname` → `.history`(연혁 줄) → `.JO`(장·절 제목 / `.article` 조문 / 빈 블록) 와 `.subsection`(관) → `.addenda`(부칙 제목) + `div#<id>`(부칙 본문) 쌍 → `.article`(별표·서식 파일 목록, `fileDown('<id>','attach')`). 조문과 부칙 본문에는 사이트 고유 ID가 있다.

## 검증·거부 규칙

| 사유 코드 | 조건 |
|---|---|
| `error_page` | `errorPage.do` 리다이렉트 폼이 있거나, 리다이렉트 목적지가 오류 페이지(요청하지 않음) |
| `login_page` | 비밀번호 입력이 있는 화면 |
| `not_content` / `empty_content` / `shell` | 본문 영역 없음 / 규정명 없음 / 조문·부칙이 하나도 없음 |
| `title_mismatch` | 목록·상세·본문의 규정명이 다름(공백·`舊`·`[폐지]` 표기는 무시) |
| `revision_mismatch` / `status_mismatch` | 목록의 최신 개정이 상세에 없음 / 폐지 표기가 목록과 상세에서 다름 |
| `no_effective_revision` | 오늘 기준 시행 중인 개정이 없음 |
| `foreign_redirect` | 리다이렉트 목적지가 `https://rule.hansung.ac.kr`(기본 포트, 사용자 정보 없음)가 아님. **목적지를 요청하기 전에** 거부한다 |
| `redirect_loop` / `bad_redirect` | 같은 주소로 되돌아오거나 3회를 넘는 리다이렉트 / `Location` 없는 3xx |

리다이렉트는 자동으로 따르지 않는다(`allow_redirects=False`). 각 `Location`을 검사한 뒤 같은 호스트일 때만 요청 간격을 지켜 최대 3회 따른다. TLS 인증서 검증은 requests 기본값을 그대로 쓴다. 네트워크 오류와 200이 아닌 응답은 `FetchError`다. 재시도와 실패 집계는 #17이 맡는다.

## 출력 계약 (스냅샷 항목)

필수 필드는 RAG 색인기 `hsrule.official_indexer`의 입력과 같다. 나머지 필드는 #17(변경 감지)과 #18/#19(저장·API)에서 쓴다.

```json
{
  "regulation_id": "20", "revision_id": "20:1717", "title": "한성대학교 학칙",
  "official_url": "https://rule.hansung.ac.kr/lmxsrv/law/lawFullView.do?SEQ=20&SEQ_HISTORY=1717",
  "text": "한성대학교 학칙\n제 1 장 총 칙\n제 1 조 (목적)\n…", "abolished": false, "visibility": "public",
  "code": "2-1-1", "department": "공통", "path": ["제2편 학칙", "제1장 대학"], "former_names": [],
  "revision": {"history": 1717, "effective_date": "2026-11-11", "kind": "개정"},
  "latest_history": 1717, "upcoming": [], "history_notes": [],
  "content_hash": "<sha256(text)>",
  "articles": [{"id": "111075", "kind": "article", "label": "제1조", "heading": "제 1 조 (목적)",
                "chapter": "제 1 장 총 칙", "start": 9, "end": 87, "deleted": false}],
  "attachments": [{"name": "(별표 1)대학별 학부 및 학과 입학정원.hwp", "file_id": "4159"}],
  "warnings": [], "fetched_at": "2026-11-25T05:00:00+00:00"
}
```

- **시행 개정**: 오늘 시행 중인 개정만 `text`로 담는다. 시행 전 개정은 `upcoming`에 기록만 한다(사용자 결정).
- **폐지 규정**: 본문을 받지 않고 `abolished: true`, `text: ""`로 낸다. 색인기는 이 항목을 받으면 해당 규정을 색인에서 지운다.
- **text 형식**: 줄 단위. 규정명, 연혁 줄, `제 N 장 …`, `제 N 조 (제목)`, 항·호 한 줄씩, 표는 한 행을 `칸 | 칸` 한 줄로, `부 칙 (날짜)`, 마지막에 `[별표/서식 파일]`과 파일명 순서다. #23 청커의 경계 표기와 같다.
- **articles**: DOM 기준 조항 범위다. `text[start:end]`가 해당 조문·부칙이다. 본문 줄이 `제123조에 따른 …`처럼 시작해도 조항으로 나누지 않는다.
- **노드 보존 규칙**: 조문·부칙 본문과 `#lawcontent` 최상위는 자식 노드를 원문 순서대로 읽는다. 태그 없는 직접 텍스트와 인라인 태그(`span`·`b` 등)는 이어서 한 줄로, 블록 태그는 따로 한 줄로, `<br>`은 줄바꿈으로 다룬다. 본문 표의 `caption`은 표의 첫 줄로 남긴다. 읽지 않는 것은 주석·선언, `script`·`style`·`noscript`·`template`, 텍스트 없는 최상위 앵커(`<a name>`, 실제 페이지에 다수), 화면용 caption(조항 제목 표의 `조항`, 첨부 표의 `별지서식다운로드`)뿐이다. 그 밖에 위치를 모르는 텍스트(최상위 직접 텍스트, div가 아닌 최상위 블록, 모르는 클래스)는 본문에 남기고 경고한다. 빈 부칙 본문도 경고한다.
- **attachments**: 첨부 이름(`span.attach_file`)마다 그 이름부터 다음 이름 직전까지의 형제 노드에서만 `fileDown` 링크를 찾는다. 링크가 없거나 둘 이상이면 다른 파일과 잇지 않고 `file_id: ""`와 경고를 남긴다. 이름 없이 남은 링크도 경고한다.
- **warnings**: 위 보존·첨부 경고와, #23 청커의 줄 정규식이 DOM 경계와 다르게 나누는 위치다. 수집 실패가 아니라 확인 대상이다.

## RAG 담당(@sumiiniee) 확인 요청

실제 표본에서 청커와 DOM 경계가 어긋난 사례다.

| 규정 | 내용 | 청커 동작 |
|---|---|---|
| SEQ 104 학사운영규정 | 본문 줄 `제 16 조 2(교과목의 분반) 교과목 주관 학과/전공/트랙…` | 새 조항으로 나눔 |
| SEQ 18 정관 | 범위형 삭제 조문 `제 95조의 4 ~ 제 95조의 10 (삭제 2021. 8. 12.)` | 조항으로 인식하지 못함 |
| SEQ 18 정관 | `제 1 관 임 용`(관 단위 제목) | 장·절만 인식해서 앞 조항 본문에 포함 |

청커가 `articles` 범위를 직접 쓰면 이 차이가 없어진다. 입력 계약 변경은 협의 후 진행한다.

## 검증 범위

- **오프라인 회귀**: `tests/test_official_crawler.py` 25개. fixture(`tests/fixtures/official/`)는 2026-11-25에 받은 실제 HTML을 요소 단위로 잘라 낸 것이다(마크업 변경 없음). 테스트 안에서 "합성"이라고 적은 사례만 직접 만든 입력이다. #23 청커(`chunk_regulation`, `verify_chunks`)와의 통합은 테스트 대역 토크나이저로 확인한다.
- **실제 사이트 표본**: 목록 1회와 규정 26건(무작위 23건 포함)을 1초 간격으로 수집했다. 25건 성공(폐지 5건 포함), 1건은 의도된 거부(SEQ 253: 목록 "…부설 연구원…", 본문 "…부설 연구기관…"으로 명칭이 달라 수동 확인 필요)였다. 경고는 위 표의 2건이다.
- **미검증**: 323건 전체 수집, 재시도·체크포인트, 스냅샷 발행(#17). DB 저장(#18). 실제 임베딩 모델 색인.

표본 확인 명령(전체 수집이 아니다):

```sh
python -m hsrule_crawler.collect 20 18 54          # 요약
python -m hsrule_crawler.collect 20 --full         # 본문·조항 포함 JSON
```
