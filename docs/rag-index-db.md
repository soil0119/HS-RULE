# 공식 규정 색인 DB (#24)

## 스키마와 일관성 제약

| 대상 | 규칙 |
|---|---|
| 벡터 차원 | `vector(384)` 고정. `vector_literal`이 384차원·유한값·영벡터 아님을 저장 전에 거부한다. |
| 모델·토크나이저 버전 | 색인 전체가 한 설정만 쓴다. `hs_reg_index_meta`에 `embedding_model`·`embedding_dim`·`chunk_params`를 한 행으로 두고, 다른 설정으로 저장하면 `IndexConsistencyError`다. 바꾸려면 `--rebuild`로 전체를 다시 만든다. |
| 청크 ID | `<revision_id>:<ordinal>`. `(revision_id, ordinal)`에 UNIQUE, `ordinal >= 0`, `source_end > source_start`. |
| 원문 FK | `hs_reg_chunks.revision_id → hs_reg_revisions.revision_id ON DELETE CASCADE`. 개정을 지우면 청크가 함께 지워진다. |
| 개정 혼합 | `hs_reg_revisions_one_current`(부분 UNIQUE)로 규정마다 현재 개정은 최대 하나다. |
| 공개 범위·검색 대상 | `hs_reg_chunks.visibility`·`searchable`은 소속 개정의 `visibility`·`is_current` 복사본이며, 승격·강등·공개 범위 변경과 같은 트랜잭션에서 갱신한다. |

## 권한 필터와 실행 계획

벡터 검색은 필터와 거리 정렬을 모두 `hs_reg_chunks` 한 테이블에서 처리한다. 이렇게 하지 않으면 `hs_reg_revisions`와 조인한 뒤 정렬하게 되어 pgvector가 벡터 인덱스를 쓰지 못하고, 전체 청크를 읽은 뒤 LIMIT을 적용한다.

- 부분 인덱스: `hnsw (embedding vector_cosine_ops) WHERE searchable AND NOT deleted`. 키워드용 trigram 인덱스도 같은 조건의 부분 인덱스이며 `search_text`에 걸려 있다.
- `search_text`는 `content`를 NFKC 정규화하고 소문자로 만든 검색 전용 열이다. 공식 HTML 표의 `A⁺`나 전각 숫자(`９５`)가 질의의 `A+`·`95`와 맞도록 색인 시점에 한 번만 변환한다. 인용과 원문 오프셋은 항상 `content` 기준이다.
- `ORDER BY`에는 거리 식만 둔다. `, c.id` 같은 2차 정렬 키를 붙이면 인덱스가 정렬을 제공하지 못한다. 동순위 결정은 결합 단계(`fuse`)에서 한다.
- 권한·개정·삭제 조건은 인덱스 스캔 단계의 조건이므로 LIMIT 이전에 적용된다. `tests/test_official_repository.py`의 `test_scope_filter_is_applied_before_the_limit`가 비공개·구개정·삭제 청크가 질의와 가장 가까워도 결과에 들어오지 않음을 확인하고, `test_vector_order_by_uses_the_partial_hnsw_index`가 `EXPLAIN`으로 인덱스 선택을 확인한다.
- HNSW는 후필터 방식이므로 걸러지는 행이 많으면 결과 수가 `LIMIT`보다 적을 수 있다. 검색 시 `hnsw.ef_search = 200`을 세션에 설정한다.

## migration

`hsrule/migrations/*.sql`을 번호순으로 적용하고 `hs_reg_migrations`에 이름과 체크섬을 남긴다.

- 추가만 한다. 적용된 파일을 고치면 체크섬 불일치로 거부되므로 새 번호를 추가한다.
- 러너가 `DROP TABLE/COLUMN`, `TRUNCATE`, `DELETE FROM`을 발견하면 적용을 거부한다. 색인을 비우는 일은 `python -m hsrule.official_repository --rebuild`로만 한다.
- `initialize()`는 여러 번 실행해도 안전하고, 적용할 것이 없으면 빈 목록을 반환한다.

## 수집기 DOM 조문 범위 (#60)

수집 스냅샷 항목에 `articles`(사이트 조문 ID·kind·label·heading·start·end·deleted, PR #47 형식)가 있으면 청커는 정규식으로 조항을 다시 나누지 않고 그 범위를 쓴다. 없으면(학칙 발췌본, 이전 형식) 기존 줄 정규식으로 나눈다.

- 범위는 겹치지 않고 순서대로이며 `text[start:end]`가 heading으로 시작해야 한다. 어긋나면 그 규정만 색인을 거부한다(`failed`에 기록, 다른 규정은 계속).
- 첫 조문 앞(규정명·연혁)은 `preamble`, 조문 사이의 장·절·관 제목은 다음 조문 청크 앞부분, 마지막 뒤의 `[별표/서식 파일]`은 `attachments`다. 원문 전체가 빠짐없이 청크에 들어가는지 `verify_chunks`가 확인한다.
- 부칙 범위 안의 `제N조`는 `부칙(…)제N조`로 따로 나눈다. DOM이 삭제로 표시한 조문(범위형 삭제 포함)은 검색에서 제외한다.
- `hs_reg_chunks.source_article_id`(0004)에 사이트 조문 ID를 저장한다. 부칙 안 조는 `<부칙 ID>#<라벨>`이다. 청크 ID(`개정:순번`)는 개정마다 새로 매기므로, 개정이 바뀌어도 같은 조문을 잇는 키는 `(regulation_id, source_article_id)`다. 연혁 줄이 앞에 추가돼도 조문 라벨·조문 ID·내용은 같다.
- 본문이 같아도 조문 ID·범위·삭제 표시가 바뀌면 변경으로 보고 다시 색인한다(`text_hash`에 DOM 구성을 포함).

## 로컬 개발 환경 (DB만)

앱은 Docker로 만들지 않는다. PostgreSQL+pgvector만 띄운다.

```sh
echo "POSTGRES_PASSWORD=$(python -c 'import secrets;print(secrets.token_hex(16))')" >> .env
docker compose up -d db
export DATABASE_URL="postgresql://postgres:$POSTGRES_PASSWORD@127.0.0.1:5432/hansungrules"
python -m hsrule.official_repository --init
HSRULE_TEST_DATABASE_URL="$DATABASE_URL" python -m unittest discover -s tests -t .
```

`compose.yaml`의 `db`는 `pgvector/pgvector:pg16`이며 `vector`·`pg_trgm` 확장은 migration이 만든다. 포트는 `127.0.0.1:5432`로만 노출된다.

## 웹 문서 스키마와 중복 저장 책임

| 저장소 | 소유 | 내용 |
|---|---|---|
| `web_documents`, `web_document_pages` (#7·#18, @neicebee) | 백엔드 | 업로드 PDF 원본·페이지 텍스트·메타데이터. 원본 파일의 단일 출처다. |
| `hs_reg_revisions`, `hs_reg_chunks` (#9, @sumiiniee) | RAG | 크롤링으로 검증된 공식 규정의 개정·조항 청크와 벡터. |
| `hs_documents`, `hs_chunks` (PR#2 초안) | — | PDF 초안 테이블. 공식 입력원으로 쓰지 않으며 이 색인과 독립이다. |

- 규정 본문은 색인 쪽에 청크 단위로만 중복 저장한다. 원문 전체 보관과 제공은 백엔드 책임이고, 색인은 `official_url`과 원문 오프셋(`source_start`/`source_end`)만 들고 있어 원문을 다시 만들지 않는다.
- 공개 범위의 단일 출처는 크롤링 스냅샷이다. 색인은 스냅샷의 `visibility`를 복사하며, 백엔드에서 범위가 바뀌면 `set_visibility`나 재색인으로 반영한다.
- 두 저장소는 같은 PostgreSQL 인스턴스를 쓰되 테이블 접두사(`web_`, `hs_reg_`)로 분리한다. 서로의 테이블을 직접 읽거나 쓰지 않는다.

## PR#2 인수와 실제 검증의 구분

PR#2의 `hs_documents`/`hs_chunks` 스키마와 `vector_candidates`는 코드만 인수했고 이 색인에 쓰지 않는다. 실제 임베딩 모델 검증 범위는 `docs/rag-evaluation.md`에 있다.
