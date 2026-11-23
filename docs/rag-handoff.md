# RAG 담당자 인계

사용자가 RAG·LangChain을 다른 담당자에게 맡기도록 범위를 변경했다. 아래 GitHub 담당자 지정은 확인 완료다.

| 범위 | 담당 이슈 | 상태 |
|---|---|---|
| PDF 색인·pgvector 기반 검토 | [#9](https://github.com/soil0119/HS-RULE/issues/9) | @sumiiniee, 인계 초안 리뷰 필요 |
| LangChain 검색·답변·충돌 분석 | [#10](https://github.com/soil0119/HS-RULE/issues/10) | @sumiiniee, 미구현 |
| 실제 PDF·모델 평가 | [#11](https://github.com/soil0119/HS-RULE/issues/11) | @sumiiniee, 실제 입력 미제공 |

## 보존한 초안

Draft PR #2 / `codex/pdf-foundation`에 페이지 추출·토큰 청킹·지연 임베딩, 트랜잭션 문서/청크 저장·공개 범위·벡터 후보 조회, PDF CLI와 초기화가 있다. 기능 커밋은 f571f15, 481d0db, 6af9e1f다.

기록된 검증은 합성 PDF·로컬 WordPiece 토크나이저, 임시 PostgreSQL/pgvector를 포함한 unittest 17개다. 기본 MiniLM과 사용자 실제 PDF·Groq는 미검증이다. 독립 리뷰와 머지는 완료하지 않았다. 실제 PDF가 없는 상태에서 원격 수집이나 원본 ZIP의 쿠키를 사용하지 않았다.

`load_pdf`는 페이지 텍스트와 메타데이터만 추출하므로 일반 문서 관리에서 재사용할 수 있다. `store_pdf`는 임베딩까지 결합하므로 일반 앱 API가 이 함수를 그대로 호출하지 않는다. 문서·청크 스키마는 후속 앱 문서 원본 저장 계약과 합의가 필요하다.

## 다른 담당자의 작업과 경계

일반 앱/API·인증·파일 보관은 #7, UI는 #6, 도메인·실행 구성은 #8에서 별도 main 기반 PR로 진행한다. RAG 코드를 이 브랜치에서 추가 구현하거나 앱 PR에 자동으로 포함하지 않는다. 질의·충돌 분석 API는 담당자가 구현할 서비스의 입출력 계약만 제공하며, 미연결 상태를 정상 검색으로 가장하지 않는다. 실제 연결 계약은 #7의 구현 PR에서 문서화한다.

사용자가 선택한 규정 입력은 제공 PDF다. 기존 JSON 315건은 조항 본문이 없는 메뉴 화면이므로 지식 원본으로 사용하지 않는다. 사용자 PDF·키·모델 캐시를 Git에 올리지 않는다.
