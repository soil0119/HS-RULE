# HS-RULE 앱/API 구현 범위

## 규정 자료와 담당자

한성대학교 공식 규정관리시스템에서 수집한 공개 규정 본문을 서비스의 주 자료로 사용한다. 수집에서 규정·개정 식별자, 원문 링크, 첨부 상태를 보존한다. 관리자 PDF는 공개 범위를 선택해 보관할 수 있는 추가 자료다. 기존 JSON은 진단용 스냅샷으로 남긴다.

| 범위 | 담당자 | 추적 |
|---|---|---|
| 백엔드, 공식 공개 규정 크롤러, 일반 PostgreSQL | `neicebee` | #7, #16–#22 |
| RAG, LangChain, 청킹·임베딩·벡터 DB, 모델과 검색 평가 | `sumiiniee` | #9–#11 및 하위 이슈 |
| 프론트엔드, PC·모바일과 한국어·영어 전환 | `IVEhades` 예정. 초대 수락 후 GitHub 배정 확인 | #6, #13–#15 |
| 도메인, 실행 문서, 가벼운 CI | #8에서 추적 | 관련 하위 이슈 |

상위 이슈는 #3이다. 초기 구성 #4와 진단 #5를 완료했고, 남은 구현은 #13–#32에 등록했다. PDF 색인 기반 PR #2는 사용자가 main에 병합했다. [상위 이슈 #3](https://github.com/soil0119/HS-RULE/issues/3) · [PR #2](https://github.com/soil0119/HS-RULE/pull/2)

## 현재 게시 범위

- Draft PR #33은 일반 앱/API, 관리자 인증, 선택 PDF 보관, RAG 주입 계약을 담는다. 선택 PDF는 서버가 발급한 ID로 원본과 1기반 페이지를 보관한다. 공개 목록은 공개 PDF만 제공한다. 질의·충돌 분석은 RAG 어댑터 미연결 상태에서 503을 반환한다. [Draft PR #33](https://github.com/soil0119/HS-RULE/pull/33) · [API 계약](api-contract.md)
- 성공한 Local Codex 디자인은 별도 Draft PR에서 보존한다. 공식 자료 수집, UI 서비스 연동과 PC·모바일 검증, RAG/검색 품질 평가, 운영/CI 구현은 각 담당 이슈에 남긴다.

앱은 FastAPI, PostgreSQL 및 정적 HTML/CSS/JS를 사용한다. 일반 문서 보관은 경량 Python 의존성만 사용한다. 기본 공개 출처는 `https://hs.officeworkkt.com`이며 로컬 개발을 허용한다. 실제 DNS·TLS·호스팅과 운영 DB는 #8 작업에서 검증한다.
