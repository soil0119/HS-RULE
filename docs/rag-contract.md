# RAG 쪽 통합 계약 제안 (#68)

상태: **제안**. 원천 DB·발행·API 담당(@neicebee)과 합의한 뒤 확정한다. 이 문서는 RAG 담당(@sumiiniee)이 지금 코드로 보장하는 것과 정해야 할 것을 나눠 적는다. 아래 "합의 필요" 항목은 확정 전까지 바꿀 수 있다.

## 책임

| 영역 | 담당 | 위치 |
|---|---|---|
| 공식 사이트 수집·검증·스냅샷 발행, 원천 DB(규정·개정·조문 원문, 시행·예정·폐지 상태), 목록·원문 API, 웹 출처 직렬화 | @neicebee | `hsrule_crawler/`, #17·#18·#19·#66·#67 |
| 청킹·임베딩·벡터 색인(`hs_reg_revisions`, `hs_reg_chunks`, `hs_reg_index_meta`, `hs_reg_index_runs`, `hs_reg_migrations`), 검색·답변·충돌 분석, 평가 | @sumiiniee | `hsrule/`, `hsrule/migrations/` |

색인은 원천의 **투영**이다. 정답은 원천 DB(또는 발행된 스냅샷)이고 색인은 언제든 다시 만들 수 있다. 색인 테이블은 원천 테이블을 외래 키로 참조하지 않고, 원문 표시·목록은 웹 API가 원천 DB에서 한다. 같은 원문을 두 곳에 두는 것은 검색용 사본(청크)뿐이다.

## 스냅샷 입력 (수집기 → 색인기)

파일 형식(`python -m hsrule.official_indexer <snapshot.json>`):

```json
{"schema_version": 1, "complete": true, "failures": [], "generated_at": "2026-11-27T00:00:00+00:00",
 "regulations": [{"regulation_id": "20", "revision_id": "20:1717", "title": "한성대학교 학칙",
                  "official_url": "https://rule.hansung.ac.kr/...", "text": "…", "articles": [...]}]}
```

| 항목 | 지금 보장하는 것 |
|---|---|
| 필수 필드 | `regulation_id`, `revision_id`, `title`, `official_url`, `text`. 선택: `articles`, `abolished`, `visibility`(public/private, 기본 public) |
| ID | `regulation_id` = 사이트 SEQ(문자열), `revision_id` = `SEQ:SEQ_HISTORY`, 조문 ID = 사이트 조문 ID(`articles[].id`). 부칙 안 조는 `<부칙 ID>#<라벨>` |
| 본문·해시 | 수집기의 `text`가 기준 본문이다(줄 형식은 `docs/official-crawler.md`). 색인 변경 감지는 `sha256(text)`, `articles`가 있으면 `sha256(text + DOM 구성)` |
| DOM 범위 | `articles[].start/end`는 `text`의 파이썬 문자 위치(코드 포인트)다. 겹치지 않고 순서대로이며 heading으로 시작해야 한다. 어기면 그 규정만 색인 거부(#60) |
| 상태 | `text`는 오늘 시행 중인 개정만 담는다. `upcoming`·`scheduled_abolition`은 색인하지 않는다. `abolished: true`면 그 규정을 색인에서 지운다. DOM `deleted`와 본문 삭제 표시는 검색 제외 |
| 완전성 | `schema_version: 1`, `complete: true`, `failures`가 빈 스냅샷만 "목록 전체"로 본다. 그때만 스냅샷에 없는 규정을 색인에서 지운다. 그래도 현재 색인의 20%(최소 3건)를 넘게 지워야 하면 지우지 않고 `removal_skipped`에 후보를 남긴다. 이전 형식(버전 없음)·부분 수집은 누락 삭제를 하지 않는다 |
| 재시작 | 같은 스냅샷을 다시 실행하면 바뀐 규정만 다시 임베딩한다. 중단된 실행은 `hs_reg_index_runs`에 `ok=false`와 `aborted`로 남는다(#56) |

## 출처·충돌 분석 출력 (어댑터 → 웹 API)

고정 예시: `tests/fixtures/contracts/rag_answer_official_html.json`, `tests/fixtures/contracts/rag_conflict_result.json`. 어댑터 출력의 키나 형식이 바뀌면 `tests/test_contract_examples.py`가 실패한다.

- 답변: `{answer, found, sources, followups}`. 출처 항목은 `{label, chunk_id, regulation_id, revision_id, title, article_id, url, source_kind: "official_html", source_start, source_end, score}`이고 **페이지가 없다**. `score`는 벡터 코사인 유사도이며 없으면 null이다(RRF 점수를 유사도로 쓰지 않는다).
- `chunk_id`(`개정:순번`)는 개정마다 새로 매기는 값이라 저장·링크 키로 쓰지 않는다. 개정이 바뀌어도 같은 조문을 잇는 키는 `(regulation_id, source_article_id)`이고 색인에는 저장한다(#60). 출처에 넣는 것은 아래 합의 항목이다.
- 보조 PDF 문서의 출처는 기존 페이지 기반 형식을 유지하고 `source_kind`로 구분한다. 웹 API는 두 형식을 모두 받아야 한다(#66).
- 충돌 분석: `{summary, conflicts[].evidence, scope, recommendations(문자열), recommendation_details}`. `scope.truncated`, `stopped_for_time`, `passages_without_candidates`가 있거나 `passages_analyzed < passages_total`이면 "충돌 없음"으로 표시하지 않는다(#67·#70).
- 오류: `RagError.code`(`rag_backend_error`, `rag_invalid_response`)는 502, `reason`(timeout·rate_limit·provider·json)은 로그·진단용 내부 값이다.

## 합의 필요 (@neicebee)

1. 원천 DB의 조문 ID를 사이트 조문 ID 그대로 쓸지. 그대로 쓰면 색인의 `source_article_id`와 바로 이어진다.
2. #17 발행 스냅샷에 `schema_version`·`complete`·`failures`를 넣을지. 넣지 않으면 색인기는 누락 규정을 지우지 않는다.
3. 시행 예정 개정을 색인할지(지금은 하지 않음). 한다면 검색에서 현재/예정을 구분하는 필터가 필요하다.
4. 출처에 `source_article_id`를 추가하고 웹 `SourceReference`를 공식 HTML·PDF 두 형식으로 바꾸는 것(#66).
5. DOM 오프셋 단위(파이썬 문자 위치)를 프론트(#69)까지 그대로 쓸지. JavaScript 문자열은 UTF-16 단위라 이모지 등에서 어긋날 수 있다.

## 관리자 화면 설정 후보 (#75 인계)

지금은 모두 코드 상수나 서버 환경변수이고, 실행 중에 바꾸는 설정 API는 없다. 설정 저장·적용 API(@neicebee)가 생기기 전에는 **읽기 전용 표시**를 권한다. 값은 #77 기준선 결과로 바꿀 수 있다.

| 항목 | 위치 | 형식·범위 | 기본값 | 바꾸면 | 비고 |
|---|---|---|---|---|---|
| 근거 수 k | `RAG_RETRIEVAL_K` | 정수 1~12 | 6 | 서버 재시작 | 재색인 불필요 |
| 검색별 후보 수 | `HybridRetriever(candidates)` | 정수 1~50 | 20 | 코드 변경 | 재색인 불필요 |
| 조항/키워드/벡터 가중치 | `HybridRetriever(weights)` | 0 이상 실수 | 3 / 1 / 1 | 코드 변경 | RRF 순위 결합 가중치. 유사도가 아니다 |
| 벡터 단독 후보 최소 코사인 | `MIN_COSINE` | 0~1 | 0.25 | 코드 변경 | 키워드·조항 일치 후보에는 적용하지 않는다. "올리면 품질 향상"이 아니다 |
| 문맥 상한 / 이력 비율 | `MAX_CONTEXT_TOKENS` / `HISTORY_SHARE` | 정수 / 0~1 | 2048 / 0.5 | 코드 변경 | 토큰 수는 임베딩 토크나이저 기준 추정 |
| 질의 시간 / 충돌 분석 시간 | `MAX_TOTAL_SECONDS` | 초 | 120 / 300 | 코드 변경 | 재시도 포함 전체 시간 |
| LLM 제공자·모델 | `LLM_PROVIDER`, `GEMINI_MODEL`·`GROQ_MODEL` | 지원 모델 ID | 없음(필수) | 서버 재시작 | 키는 화면·URL·로그에 노출하지 않는다 |
| 청크 길이 / 겹침 | 색인 `max_tokens` / `overlap_tokens` | 토큰 | 128 / 16 | **재색인 필요** | 관리자 화면에서 바꾸지 않기를 권한다 |
| 임베딩 모델 | `LocalEmbeddings` | 384차원 모델 | paraphrase-multilingual-MiniLM-L12-v2 | **새 색인 필요** | 다른 모델 벡터를 한 색인에 섞을 수 없다 |
