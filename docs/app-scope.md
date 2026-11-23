# HS-RULE 앱/API 구현 범위

## 확정 사항

- 상위 작업 #3. 초기 구성 #4와 진단 #5는 완료 기록이다.
- 저희: #7 인증·권한·PDF 문서 보관 API, #6 Local Codex 기반 PC·모바일 전체 UI, #8 hs.officeworkkt.com 설정·실행 문서·가벼운 CI.
- RAG·청킹·임베딩·pgvector 검색·LangChain·AI 생성·평가: sumiiniee의 #9·#10·#11. 기존 초안은 Draft PR #2에 인계하며 새 앱 PR에 포함하지 않는다.
- 사용자 PDF는 아직 없다. 보관과 페이지 추출은 합성 PDF로 검증한다.

## 구현 원칙

기존 FastAPI·PostgreSQL과 정적 HTML/CSS/JS를 유지한다. 일반 문서 메타데이터·페이지 보관은 벡터·모델 없이 동작한다. Postgres 대신 별도 SQLite를 추가하지 않는다. 원본은 uploads/{server_id}.pdf, DB에는 안전한 ID·메타데이터·페이지 텍스트를 저장한다. 관리자 업로드는 private가 기본이고 public은 명시적으로 선택한다.

서버는 RAG 서비스의 명시적 주입 계약만 제공한다. 미연결 질의·충돌 분석은 503으로 안내하며 임의 답변·가짜 임베딩을 만들지 않는다. 모델·DB·관리자 설정 누락이 UI와 liveness를 막지 않는다. 인증과 공개 범위·파일 경계는 실제 API에서 검증한다.

PUBLIC_ORIGIN은 https://hs.officeworkkt.com이며 로컬 개발을 허용한다. 실제 DNS·TLS·호스팅 배포는 별도 접근·대상이 확인되어야 한다. CI는 비밀값·모델 다운로드 없이 일반 앱 문법/단위/API와 가능한 격리 DB 검증을 수행한다.

## PR 순서

1. codex/app-api → main: #7 일반 API·인증·PDF 문서 관리와 RAG 연결 계약.
2. codex/open-design-ui → codex/app-api: #6 생성 UI·안전한 API 연결·PC/모바일 검증.
3. codex/app-ops → codex/open-design-ui: #8 도메인·실행 문서·최소 CI·통합 검증.

각 컴포넌트를 자주 커밋하고 PR에 이슈와 검증/제한을 기록한다. 사용자 머지로 남긴다.
