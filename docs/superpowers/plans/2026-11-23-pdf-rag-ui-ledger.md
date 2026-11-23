# 구현·리뷰 기록

- 사용자 선택: PDF 규정 입력, PC·모바일 전체 UI, LangChain RAG·핵심 API 실제 구현, `hs.officeworkkt.com`, 잦은 커밋과 분리 PR.
- OpenDesign Cloud는 잔액 부족으로 종료. 사용자가 Local Codex 전환을 명시적으로 선택했으며 해당 UI 생성은 진행 중이다.
- 진단: 제공 JSON은 본문 없는 화면 315건. 실제 사용자 PDF·Groq 키 미제공.
- Task 1: pending.
- Task 2: pending.
- Task 3: pending.

- 사용자 범위 변경: RAG·LangChain은 sumiiniee의 #9/#10/#11로 인계. 기존 3개 기능 커밋과 17개 검증을 Draft PR #2에 보존. 독립 리뷰·머지 미완료.
- 저희 실행 이슈: #6 UI, #7 일반 API·문서 관리, #8 도메인·검증. 후속 작업은 별도 main 기반 브랜치에서 진행.
