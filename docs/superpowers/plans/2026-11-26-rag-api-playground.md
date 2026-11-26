# RAG API Playground Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans for inline implementation and one final independent review.

**Goal:** Docker로 실행하는 실제 HS-RULE API 질문·대화·근거 테스트 화면.

**Architecture:** Python 표준 라이브러리 웹 서버가 고정된 HS-RULE API의 `/query`와 두 health 경로만 호출한다. 정적 웹 화면은 같은 출처의 서버를 사용하고 모델 키를 받지 않는다.

**Tech Stack:** Python 3.11+, HTML/CSS/JavaScript, Docker Compose, unittest.

**Spec:** 이 문서의 Goal·Architecture·Global Constraints와 사용자 요청: “Rag랑 랭체인 api 연결했을때 테스트해볼 툴같은거 도커로 띄워서 쓸 수 있게”.

## Global Constraints

- 기존 서비스·DB 및 제품 프론트는 변경하지 않는다. 사용자만 PR을 머지한다.
- 질문 2,000자, 메시지 12개/각 4,000자, 언어 ko/en인 현재 API를 그대로 사용한다.
- 답변을 생성하거나 threshold 변경을 흉내 내지 않는다. 연결 오류를 실제 상태로 표시한다.
- 모델 키·질문은 서버 로그나 영구 저장소에 기록하지 않는다. 루프백 포트만 공개한다.

## Review Focus

- API 503/429/502, 비 JSON 응답, timeout을 성공으로 표시하지 않기.
- 외부 출처 POST·임의 프록시 경로·리다이렉트 차단.
- HTML이 포함된 답변·근거 및 위험한 링크를 실행하지 않기.
- 실패한 질문·너무 긴 답변을 대화 이력에 자동 편입하지 않기.
- Docker의 host.docker.internal과 브라우저의 localhost 주소를 구분하기.

### Task 1: 고정 API gateway와 계약 테스트

**Files:** `tools/rag-playground/playground.py`, `tools/rag-playground/test_server.py`.

**Interfaces:** `/api/config`, `/api/health`, `POST /api/query` → `{status, elapsed_ms, request_id, body}`.

- [x] 임시 upstream으로 실제 전달/오류/출처·경로 경계 테스트를 작성하고 실패 확인.
- [x] 고정 대상·응답 제한·timeout·리다이렉트 차단을 구현하고 unittest 통과.
- [x] gateway와 테스트를 커밋.

### Task 2: 웹 화면·Docker·사용법

**Files:** `tools/rag-playground/static/{index.html,app.js,styles.css}`, `tools/rag-playground/{Dockerfile,.dockerignore}`, `compose.playground.yaml`, `docs/rag-playground.md`.

**Interfaces:** Task 1 API envelope를 사용해 실제 상태·답변·근거·JSON을 표시.

- [x] 질문·ko/en·대화 이력·새 대화·연결 확인·JSON 다운로드를 구현.
- [x] 별도 Compose로 비관리자·read-only 컨테이너를 빌드/실행.
- [x] unittest, JS 구문 검사, Docker health, 브라우저 정상/실패/모바일 화면 확인.
- [x] 실모델 미연결 한계와 담당 이슈를 문서화하고 최종 리뷰 후 커밋·PR 게시.

Execution: aa3f5fe gateway 커밋 후 웹 화면·Docker·사용법 구현. API prefix 링크 결함은 별도 리뷰에서 재현 후 수정하고 회귀 테스트를 추가했다. 실모델 연결은 #76/#20/#66 후속 의존성으로 유지한다.
