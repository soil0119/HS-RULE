# RAG 어댑터 연결 (#9·#10)

## 구성

| 모듈 | 역할 |
|---|---|
| `hsrule/official_chunking.py` | 공식 규정 본문을 조·표·부칙 단위로 토큰 청킹, 원문 범위 검증 (#23) |
| `hsrule/migrations/`, `migrations.py`, `official_repository.py` | 비파괴 migration, pgvector 스키마(부분 hnsw·trigram 인덱스), 개정 단위 원자 저장 (#24) |
| `hsrule/official_indexer.py` | 배치 임베딩 색인, 개정·삭제 재색인, 스냅샷 동기화 (#25) |
| `hsrule/official_retrieval.py` | 조항 번호 + 한국어 키워드 + 벡터 RRF 결합 검색, 권한·개정 필터 (#26) |
| `hsrule/chains.py`, `rag_config.py` | LangChain Retriever·Runnable·대화 이력·한영 체인, LLM(Gemini·Groq) 모델 설정 (#27) |
| `hsrule/answering.py` | JSON 구조화 출력, 후보 라벨 기반 조항 인용, 오류 계약 (#28) |
| `hsrule/conflict.py` | 교직원 문서 충돌 분석, 처리 범위·근거 표시 (#29) |
| `hsrule/adapter.py` | `RagBackend`(`answer`, `analyze_conflict`) 구현과 `create_regulation_adapter` |

## 실행

```sh
docker compose up -d db
export DATABASE_URL=postgresql://postgres:$POSTGRES_PASSWORD@127.0.0.1:5432/hansungrules
python -m hsrule.official_repository --init      # migration 적용 (비파괴)
python -m hsrule.official_indexer snapshot.json   # {"regulations":[{regulation_id, revision_id, title, official_url, text}]}
export GEMINI_API_KEY=... GEMINI_MODEL=<현재 지원 모델>   # 기본 제공자 gemini, 모델 기본값 없음
# Groq를 쓰려면: export LLM_PROVIDER=groq GROQ_API_KEY=... GROQ_MODEL=<현재 지원 모델>  (종료된 Scout 거부)
```

```python
from hsrule.official_repository import OfficialRepository
from hsrule.adapter import create_regulation_adapter

app = create_app(settings, document_store=store, rag_backend=create_regulation_adapter(OfficialRepository(url)))
```

## 웹 API가 맞춰야 할 점 (#19)

- 출처는 페이지가 아니라 `{label, chunk_id, regulation_id, revision_id, title, article_id, url, source_start, source_end, score}`다. `page_start`/`page_end`는 만들지 않는다. 기존 PDF 기준 출처 검증(문서 ID·페이지 범위)은 이 형태를 받도록 바꿔야 한다.
- 충돌 분석은 계약의 `summary`, `conflicts`, `recommendations`에 더해 `scope`(검토한 문단·잘림·후보 없음·버린 지적)를 반환하고, 각 충돌에 `evidence`가 붙는다. `scope.truncated`나 `passages_without_candidates`가 있으면 "충돌 없음"이라고 표시하면 안 된다.
- 오류는 `RagError.code`(`rag_backend_error`, `rag_invalid_response`)를 그대로 502로 매핑한다.

## 조건 확인과 근거 충실도 (#78)

`CitedAnswerer.answer()`는 `answer·found·sources·followups`에 더해 다음 필드를 준다. 어댑터의 웹 API 계약은 아직 네 필드만 넘기므로(#20·#67에서 합의 후 노출), 사용자에게 필요한 내용은 `answer` 본문에도 넣는다.

- `clarification`: `{needed, questions, missing, dropped_personal_questions}`. 규정이 입학년도·과정(학부/대학원/학석사연계)·학적 상태·학과·적용 시점에 따라 달라지는데 사용자가 그 조건을 밝히지 않았을 때 최대 2개의 추가 질문을 만든다. 질문·이력에서 이미 밝힌 조건은 프롬프트에 `[사용자가 밝힌 조건]`으로 넣고 다시 묻지 않는다. 이름·연락처·학번 전체 같은 개인정보를 묻는 질문은 버린다. `missing` 값: `admission_year`, `program`, `student_status`, `department`, `effective_date`.
- `grounding`: `{checked, unsupported, verified, repaired}`. 답변의 수치(학점·평점·기간 등)가 인용 근거나 사용자가 말한 값에 있는지 확인한다. 어긋나면 남은 시간 안에서 한 번만 고쳐 쓰게 하고, 그래도 어긋나거나 고쳐 쓰기가 실패하면 처음 답변에 확인 안내 문장을 붙이고 `verified=false`로 둔다. 조 번호·인용 라벨·목록 번호는 검사하지 않고, 한글 수사(예: '일곱 학기')와 문장 의미는 검사하지 않는다.
- 후속 질문 재작성이 조 번호·입학년도·과정·학적 상태·학과를 잃으면 검색 질문에 다시 붙인다. 영어 `Article 40`은 `제40조`를 덧붙여 조항 일치 검색이 되게 한다.

## 테스트

`HSRULE_TEST_DATABASE_URL`을 일회용 PostgreSQL(pgvector)로 지정하면 DB 테스트까지 실행된다. 지정하지 않으면 해당 테스트는 건너뛴다. 모델·임베딩은 대역을 쓰므로 실제 품질 검증이 아니다(`docs/rag-evaluation.md`). 검색 상한과 근거 없음 기준은 `docs/rag-retrieval.md`, DB 스키마·실행 계획·중복 저장 책임은 `docs/rag-index-db.md`에 있다.
