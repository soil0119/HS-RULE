# 공식 규정 검색 (#26)

## 결합 방식

세 검색의 후보를 가중 RRF(Reciprocal Rank Fusion)로 합친다. 순위만 쓰므로 종류가 다른 점수를 한 축에 섞지 않는다.

| 검색 | 가중치 | 점수 종류 | 설명 |
|---|---|---|---|
| 조항 완전 일치 | 3.0 | `exact` | 질의의 조 번호(제40조, 40조, 제3조의2)를 조항 ID로 정규화해 찾는다. 유사도가 없으므로 `value`는 `None`이다. |
| 한국어 키워드 | 1.0 | `lexical` | 어절 끝의 조사·어미를 한 번 떼고 2자 이상 용어(등급 기호 `A`·`A+`는 한 글자도 허용)로 포함 수를 센다. 질의와 본문을 모두 NFKC·소문자로 정규화해 비교하므로 표의 `A⁺`가 `A+` 질의와 맞는다. 유사도가 아니다. |
| 벡터 | 1.0 | `cosine` | `1 - 코사인 거리`. 유사도로 쓸 수 있는 유일한 점수다. |

`fused_score`는 RRF 값이며 유사도가 아니다. 각 후보의 원점수는 `scores[검색이름] = {"kind", "value"}`에, 벡터 유사도는 `cosine`에 따로 담는다. 고정 상수를 유사도 자리에 넣지 않는다.

상위 근거가 인용한 조(교차 참조)는 같은 개정 안에서만 덧붙이고 `found_by: ["reference"]`로 표시한다.

## 권한·개정 필터

- 필터는 `hs_reg_chunks`의 `searchable`·`visibility`·`deleted` 열로 걸며, 부분 인덱스 스캔 단계에서 적용되어 LIMIT 이후로 밀리지 않는다(`docs/rag-index-db.md`).
- 개정을 지정하지 않으면 현재 개정만 검색한다. `revision_ids`로 과거 개정을 직접 지정할 수 있으나, 한 규정의 두 개정을 함께 넘기면 거부한다.
- 공개 질의는 `include_private=False`, 관리자 분석은 `True`다. 권한은 웹 API의 토큰 검증 결과로만 정해지며 UI 선택값을 권한으로 쓰지 않는다.
- 삭제(삭제된 조) 청크는 기본으로 제외하고, 회귀 검증용으로만 `include_deleted`를 쓴다.

## 상한과 근거 없음 기준

| 항목 | 값 | 위치 |
|---|---|---|
| 근거 수 | 기본 6, 최대 `MAX_K` = 12 | `hsrule/official_retrieval.py` |
| 검색별 후보 수 | 기본 20, 최대 `MAX_CANDIDATES` = 50 | 같음 |
| 질의 길이 | `MAX_QUERY_CHARS` = 2,000자 (웹 API 질문 한도와 같다) | 같음 |
| DB 질의 시간 | `SEARCH_TIMEOUT_MS` = 5,000ms (`statement_timeout`) | `hsrule/official_repository.py` |
| HNSW 탐색 폭 | `hnsw.ef_search` = 200 | 같음 |
| 문맥 토큰 | 이력+근거 합계 `MAX_CONTEXT_TOKENS` = 2,048 토큰(임베딩 토크나이저 기준 추정). 이력은 그중 `HISTORY_SHARE` = 절반까지만 쓰고 오래된 메시지부터 버리며, 가장 최근 한 건이 넘치면 잘라 쓴다. 근거는 남은 예산 안에서 하위 근거부터 제외한다. 시스템 지시는 고정, 질문은 2,000자 이하라 요청 전체가 이 범위로 제한된다. 출력은 토큰으로 자르지 않고 답변 길이 검증(6,000자)으로 제한한다. | `hsrule/chains.py` (#27·#50) |
| 요청 시간 | 질문 재작성·답변·형식 재시도가 `MAX_TOTAL_SECONDS` = 120초를 함께 쓴다. 충돌 분석은 300초를 검색·모델·재시도가 함께 쓴다. 남은 시간이 `MIN_CALL_SECONDS` = 1초 미만이면 새 호출을 시작하지 않고, 남은 시간을 호출별 timeout으로 넘긴다. | `hsrule/chains.py`, `hsrule/conflict.py` (#51) |
| 모델 재시도 | provider 자체 재시도는 끄고, 429·5xx·연결 실패만 `MODEL_ATTEMPTS` = 3회 안에서 남은 시간이 허락할 때 다시 시도한다(Retry-After 우선, 없으면 0.5·1초…최대 8초 대기). 시간 초과는 다시 시도하지 않는다. | `hsrule/chains.py`, `hsrule/rag_config.py` (#51) |
| 근거 없음 | 조항 일치·키워드 일치가 없고 코사인 유사도가 `MIN_COSINE` = 0.25 미만이면 후보에서 제외. 남은 후보가 없으면 근거 없음으로 처리하고 모델을 호출하지 않는다. | `hsrule/official_retrieval.py`, `hsrule/answering.py` |

평가셋(`eval/regulation_eval.jsonl`)에 정확 조항·동의어·짧은 질문·부정확한 규정명·후속 질문·근거 없음·삭제 조항 회귀가 들어 있고, `scripts/run_rag_eval.py`가 Recall@k·MRR과 검색 지연 p50/p95를 기록한다. 기준값을 바꾸면 이 표와 평가 결과를 함께 갱신한다.
