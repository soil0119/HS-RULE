# RAG 어댑터 연결 (#9·#10)

## 구성

| 모듈 | 역할 |
|---|---|
| `hsrule/official_chunking.py` | 공식 규정 본문을 조·표·부칙 단위로 토큰 청킹, 원문 범위 검증 (#23) |
| `hsrule/schema_official.sql`, `official_repository.py` | pgvector 스키마(hnsw·trigram 인덱스), 개정 단위 원자 저장 (#24) |
| `hsrule/official_indexer.py` | 배치 임베딩 색인, 개정·삭제 재색인, 스냅샷 동기화 (#25) |
| `hsrule/official_retrieval.py` | 조항 번호 + 한국어 키워드 + 벡터 RRF 결합 검색, 권한·개정 필터 (#26) |
| `hsrule/chains.py`, `rag_config.py` | LangChain Retriever·Runnable·대화 이력·한영 체인, Groq 모델 설정 (#27) |
| `hsrule/answering.py` | JSON 구조화 출력, 후보 라벨 기반 조항 인용, 오류 계약 (#28) |
| `hsrule/conflict.py` | 교직원 문서 충돌 분석, 처리 범위·근거 표시 (#29) |
| `hsrule/adapter.py` | `RagBackend`(`answer`, `analyze_conflict`) 구현과 `create_regulation_adapter` |

## 실행

```sh
docker compose up -d db
export DATABASE_URL=postgresql://postgres:$POSTGRES_PASSWORD@127.0.0.1:5432/hansungrules
python -m hsrule.official_repository --init
python -m hsrule.official_indexer snapshot.json   # {"regulations":[{regulation_id, revision_id, title, official_url, text}]}
export GROQ_API_KEY=... GROQ_MODEL=<현재 지원 모델>   # 기본값 없음, 종료된 Scout 거부
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

## 테스트

`HSRULE_TEST_DATABASE_URL`을 일회용 PostgreSQL(pgvector)로 지정하면 DB 테스트까지 실행된다. 지정하지 않으면 해당 테스트는 건너뛴다. 모델·임베딩은 대역을 쓰므로 실제 품질 검증이 아니다(`docs/rag-evaluation.md`).
