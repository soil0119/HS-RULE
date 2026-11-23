# RAG 평가 (#11)

## 구성

- 평가셋: `eval/regulation_eval.jsonl`. 한성대학교 학칙 공식 본문 발췌(`tests/fixtures/hansung_rules_excerpt.txt`)를 정답 근거로 한 12건이다. 유형은 정확 조항, 조의N, 키워드, 동의어, 표, 교차 참조, 부칙, 후속 질문, 영어 질문, 근거 없음(2), 삭제 조항 회귀다. 정답 규정·개정·조항은 `expected_articles`와 실행 시 색인한 개정 ID로 대조한다.
- 실행기: `scripts/run_rag_eval.py --mode standin|real`. 결과는 `eval/results/<시각>-<모드>.json`에 쌓인다.
- 기록 항목: Recall@1/3/5, MRR(결합·키워드 단독·벡터 단독), 삭제 조항 회귀, 검색 지연 p50/p95. `real` 모드는 추가로 인용 조항 일치율, 개정 일치율, 무근거 답변률, 올바른 거절률, 답변 지연, 오류 수를 기록한다.
- 버전 기록: git 커밋, 코퍼스·평가셋 SHA-256, 임베딩 모델, 토크나이저, 청킹 설정, 프롬프트 코드 SHA-256, Groq 모델 ID.
- 수치는 실행기가 계산한 값만 결과 파일에 적고, 이 문서에는 옮기지 않는다. 측정하지 못한 항목은 결과의 `not_measured`에 사유와 함께 남는다.

## 실제 검증과 대역 검증의 구분

| 항목 | 상태 |
|---|---|
| PostgreSQL 16 + pgvector 스키마·색인·검색·권한/개정/삭제 필터 | 실제 PostgreSQL에서 테스트 통과(`HSRULE_TEST_DATABASE_URL`) |
| 청킹 전문 보존(누락·잘림·한도 초과) | 실제 학칙 발췌 fixture로 통과. 전체 규정 스냅샷은 미검증 |
| 임베딩 | 테스트·`standin`은 문자 2-gram 해시 대역. 실제 `paraphrase-multilingual-MiniLM-L12-v2` 미검증(이 환경에서 모델 다운로드 불가) |
| 답변·인용·충돌 분석 | 대역 모델로 계약·검증 로직만 테스트. 실제 Groq 호출 미검증(`GROQ_API_KEY`·`GROQ_MODEL` 필요) |
| `standin` 평가 결과 | 파이프라인 점검용이며 모델 품질 주장이 아님 |
| 공식 크롤링 스냅샷(#16/#17), 일반 DB(#18) | 미연결. 입력 형식은 `{regulation_id, revision_id, title, official_url, text}`로 가정 |

PDF 초안 PR#2의 합성 입력 테스트는 이 평가의 완료 근거가 아니다.

## 실제 평가 실행

```sh
docker compose up -d db
export DATABASE_URL=postgresql://postgres:$POSTGRES_PASSWORD@127.0.0.1:5432/hansungrules
export GROQ_API_KEY=... GROQ_MODEL=<현재 지원 모델>
python scripts/run_rag_eval.py --mode real --snapshot snapshot.json
```

주의: 실행기는 색인을 비우고 다시 만든다. 일회용 DB에서만 실행하고, 키는 커밋하지 않는다. #17 스냅샷이 나오면 평가셋을 전체 규정(표·부칙·긴 문서·충돌 분석 문서) 기준으로 늘린다.
