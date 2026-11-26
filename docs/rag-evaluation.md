# RAG 평가 (#11)

## 구성

- 검색·답변 평가셋: `eval/regulation_eval.jsonl` 17건. 한성대학교 학칙 공식 본문 발췌(`tests/fixtures/hansung_rules_excerpt.txt`)가 정답 근거다. 유형은 정확 조항(2), 키워드, 짧은 질문(2), 부정확한 규정명(2), 동의어, 표, 교차 참조, 부칙, 후속 질문, 영어 질문(2), 근거 없음(2), 삭제 조항 회귀다. 정답 규정·개정·조항·공식 URL은 `expected_articles`, 실행 시 색인한 개정 ID, 각 근거의 `official_url`로 대조한다.
- 충돌 분석 평가셋: `eval/conflict_cases.jsonl` 5건(충돌 2, 규정 준수 1, 무관 1, 긴 문서 1). #29의 분석 품질 평가를 여기서 측정한다.
- 실행기: `scripts/run_rag_eval.py --mode standin|real`. 결과는 `eval/results/<시각>-<모드>.json`에 쌓인다.
- 기록 항목: Recall@1/3/5, MRR(결합·키워드 단독·벡터 단독), 삭제 조항 회귀, 검색 지연 p50/p95, 검색 상한(`retrieval_limits`), 회귀 점검(`regressions`), 충돌 분석 후보 재현율. `real` 모드는 추가로 인용 조항·청크·개정·URL 일치, 무근거 답변률, 올바른 거절률, 답변 지연, 오류 수, 충돌 지적과 정답 비교, 권고의 근거 유무를 기록한다.
- 회귀 점검(`regressions`)은 모델 없이 결정적으로 돌아가며 공개/비공개 누출, 새 개정 제공과 구개정 차단, 삭제 조항 제외, 색인 실패 거부, 후보에 없는 인용 거부를 확인한다.
- `standin` 모드는 측정할 수 없는 사례(후속 질문·영어 질의)를 `skipped`에 사유와 함께 남기고 지표에서 제외한다. 실패로 기록하지 않는다.
- 버전 기록: git 커밋, 코퍼스·평가셋 SHA-256, 임베딩 모델, 토크나이저, 청킹 설정, 프롬프트 코드 SHA-256, Groq 모델 ID.
- `expected_articles`는 인용으로 허용하는 조항 목록이고, 선택 항목 `required_articles`는 반드시 함께 인용해야 하는 조항이다. 허용 목록 밖 조항을 인용하면 인용 일치가 아니다. 사례 하나의 모델 오류·시간 초과는 그 사례의 `error`와 `seconds`로 남고 나머지 사례는 계속 평가한다. 요약의 `denominators`와 `errors`로 집계 분모를 확인한다.
- 수치는 실행기가 계산한 값만 결과 파일에 적고, 이 문서에는 옮기지 않는다. 측정하지 못한 항목은 결과의 `not_measured`에 사유와 함께 남는다.

## 실제 검증과 대역 검증의 구분

| 항목 | 상태 |
|---|---|
| PostgreSQL 16 + pgvector 스키마·색인·검색·권한/개정/삭제 필터 | 실제 PostgreSQL에서 테스트 통과(`HSRULE_TEST_DATABASE_URL`) |
| 청킹 전문 보존(누락·잘림·한도 초과) | 실제 학칙 발췌 fixture로 통과. 전체 규정 스냅샷은 미검증 |
| 임베딩 | 테스트·`standin`은 문자 2-gram 해시 대역. 실제 `paraphrase-multilingual-MiniLM-L12-v2` 미검증(이 환경에서 모델 다운로드 불가) |
| 답변·인용·충돌 분석 | 대역 모델로 계약·검증 로직만 테스트. 실제 LLM 호출 미검증(`GEMINI_API_KEY`·`GEMINI_MODEL` 또는 `GROQ_*` 필요) |
| 임베딩 모델 사전검증(길이·차원·한국어 구분) | 규칙은 대역으로 검증. 실제 모델에 대한 실행은 `real` 모드 첫 색인에서 이뤄지며 아직 미실행 |
| `standin` 평가 결과 | 파이프라인 점검용이며 모델 품질 주장이 아님 |
| 공식 크롤링 스냅샷(#16/#17), 일반 DB(#18) | 미연결. 입력 형식은 `{regulation_id, revision_id, title, official_url, text}`로 가정 |

PDF 초안 PR#2의 합성 입력 테스트는 이 평가의 완료 근거가 아니다.

## 실제 평가 실행

```sh
docker compose up -d db
export DATABASE_URL=postgresql://postgres:$POSTGRES_PASSWORD@127.0.0.1:5432/hansungrules
export GEMINI_API_KEY=... GEMINI_MODEL=<현재 지원 모델>   # Groq는 LLM_PROVIDER=groq GROQ_API_KEY GROQ_MODEL
python scripts/run_rag_eval.py --mode real --snapshot snapshot.json
```

주의: 실행기는 색인을 비우고 다시 만든다. 일회용 DB에서만 실행하고, 키는 커밋하지 않는다. #17 스냅샷이 나오면 평가셋을 전체 규정(표·부칙·긴 문서·충돌 분석 문서) 기준으로 늘린다.
