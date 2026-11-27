"""한성대학교 공식 규정 공개 수집기 (#16).

공식 규정관리시스템(rule.hansung.ac.kr)의 공개 페이지만 요청한다. 로그인 쿠키·세션 주입,
TLS 검증 해제는 사용하지 않는다. 출력은 RAG 색인기(hsrule.official_indexer)의 입력 계약을 따른다.
"""
