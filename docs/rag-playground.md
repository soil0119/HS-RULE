# RAG·LangChain API Playground (#79)

실제 HS-RULE API를 대상으로 질문, 후속 대화, 한영 답변, 근거, HTTP 상태, 요청 ID, 응답 시간을 확인하는 로컬 웹 도구다. 모델이나 DB를 대신 실행하지 않으며 답변을 만들지 않는다.

## 시작

Docker Desktop 또는 Docker Engine + Compose v2가 필요하다. 저장소 루트에서 실행한다.

```sh
docker compose -f compose.playground.yaml up --build -d
```

브라우저에서 **http://127.0.0.1:8788** 을 연다. 기본 API 대상은 호스트 머신의 `8080` 포트다. 컨테이너는 `host.docker.internal`로 호스트에 접근한다. [Docker 네트워크 설명](https://docs.docker.com/compose/how-tos/networking/).

API가 다른 포트/서버에 있으면 재생성한다.

```sh
HSRULE_API_URL=http://host.docker.internal:8090 \
  docker compose -f compose.playground.yaml up -d --force-recreate

# 다른 팀원의 API 또는 별도 테스트 서버
HSRULE_API_URL=https://api.example.com \
  docker compose -f compose.playground.yaml up -d --force-recreate
```

`HSRULE_API_URL`은 API 루트 주소다. `/query`를 끝에 붙이지 않는다. 인증정보·쿼리 문자열·fragment를 넣을 수 없다. `/api` 같은 API 경로 prefix는 허용한다. 서버는 이 주소의 `/query`, `/health/live`, `/health`만 호출하며 리다이렉트·범용 프록시·자동 재시도를 지원하지 않는다.

`host.docker.internal` 대상에는 현재 FastAPI의 허용 호스트 정책에 맞춰 `Host: localhost[:port]`를 보낸다. Docker Desktop에서는 호스트의 로컬 API를 사용할 수 있다. Linux Engine에서는 호스트 API가 Docker bridge에서 접근 가능한 인터페이스에 바인딩돼 있어야 한다. 원격 공개 설정 없이 개발 머신에서만 사용한다.

API 자체가 컨테이너 DNS 이름에 있고 근거 파일 URL을 브라우저가 해석하지 못하면 브라우저용 주소를 따로 지정한다.

```sh
HSRULE_API_URL=http://host.docker.internal:8090 \
HSRULE_BROWSER_API_URL=http://localhost:8090 \
  docker compose -f compose.playground.yaml up -d --force-recreate
```

`host.docker.internal`의 브라우저용 주소는 기본적으로 `localhost`로 바뀐다. official_html의 절대 공식 URL은 그대로 열린다. PDF의 상대 URL은 브라우저용 API 주소를 기준으로 연다. 관리자용 비공개 파일 인증은 이 도구가 제공하지 않는다.

포트가 사용 중이면 `PLAYGROUND_PORT=8790`을 지정한다. `PLAYGROUND_PORT=0`으로 자동 포트를 사용한 경우 실제 주소는 아래 명령으로 확인한다.

```sh
docker compose -f compose.playground.yaml port playground 8080
docker compose -f compose.playground.yaml ps
docker compose -f compose.playground.yaml down
```

## 테스트 순서

1. **연결 확인:** 서버 실행(`/health/live`)과 서비스 준비(`/health`)를 구분한다. 실행 200·준비 503은 프로세스는 있으나 필수 설정/연결이 부족하다는 뜻이다.
2. **첫 질문:** 한국어 질문을 보내 답변·근거·요청 ID와 JSON을 확인한다. HTTP 200이어도 정상 답변 형식이 아니면 실패로 표시한다.
3. **후속 질문:** 성공한 대화는 이력에 자동 추가된다. “그 요건을 더 자세히 알려줘”처럼 연속 질문을 보내 LangChain의 이력 처리를 확인한다.
4. **영어:** 언어를 en으로 바꾸고 같은 조건을 질문한다. 언어 선택은 API의 답변 언어이며 화면 언어는 한국어다.
5. **근거:** 공식 조항·개정 ID 또는 PDF 페이지·점수와 원문 링크를 확인한다. 점수가 모델 답변의 정확도를 보증하지는 않는다.
6. **결과 보관:** 결과 JSON을 내려받아 설정 변경 전후를 비교한다. 질문·이력·답변이 포함되므로 공유 전에 개인정보를 확인한다.

질문은 2,000자, 이력은 최대 12개(최근 6쌍), 각 메시지는 4,000자다. 실패한 요청은 이력에 추가하지 않는다. 4,000자보다 긴 답변도 자동 추가하지 않고 안내한다. 이력 JSON을 직접 편집할 수 있으며 **새 대화** 또는 새로고침으로 탭 메모리를 비운다. 브라우저 저장소·서버 로그·DB에는 대화를 기록하지 않는다. 다운로드한 파일은 사용자가 별도로 보관한다.

## 현재 프로젝트 연결 상태와 한계

- 기본 `server:app`에는 아직 `rag_backend`가 주입되지 않아 `/query`가 `503 rag_not_connected`를 반환한다. 이 도구를 켜는 것만으로 RAG가 연결되지는 않는다.
- **hs.officeworkkit.com은 현재 정적 UI 배포다.** Playground의 API 주소로 지정하면 안 된다. 실제 FastAPI 주소를 사용한다.
- 실제 모델 키·모델 이름은 백엔드에 설정한다. `GROQ_API_KEY`, DB 인증정보, 관리자 계정은 Playground에 전달하지 않는다.
- 실제 모델 연결/시연은 [#76](https://github.com/soil0119/HS-RULE/issues/76), 백엔드 adapter/official_html 계약 정리는 [#20](https://github.com/soil0119/HS-RULE/issues/20)·[#66](https://github.com/soil0119/HS-RULE/issues/66) 의존성이다. 기본 앱 실행과 실제 모델 연결을 구분한다.
- 현재 `/query`는 `question`, `messages`, `language`만 받는다. threshold·k·모델·프롬프트 변경 UI는 제공하지 않는다. [#77](https://github.com/soil0119/HS-RULE/issues/77)·[#78](https://github.com/soil0119/HS-RULE/issues/78)에서 실제 서비스 설정을 바꾼 후 같은 질문으로 비교한다.
- 단일 요청의 종단 응답 시간과 원본 응답을 보여준다. 토큰 비용·단계별 LangChain trace·자동 품질 평가·관리자 충돌 분석은 이 도구의 범위 밖이다. 응답 JSON에 제공되는 메타데이터는 그대로 확인할 수 있다.

## 오류 확인

| 표시 | 확인할 점 |
|---|---|
| `upstream_unreachable` | API 프로세스·포트·Docker 호스트 접근 확인 |
| `upstream_timeout` | health 5초, 질문 125초 제한. 백엔드/모델 처리 시간 확인 |
| HTTP 503 | 미연결 adapter·DB·설정. 원본 `code` 확인 |
| HTTP 429 | 요청 한도·모델 quota. 자동 재시도하지 않으므로 원인 해결 후 수동 전송 |
| HTTP 502 | 모델/검색 오류 또는 official_html API 계약. 요청 ID로 백엔드 조사 |
| `non_json_response`, HTTP 301/302 | 정적 사이트나 잘못된 API 주소/리다이렉트 여부 확인 |

Playground의 `/api/query` 자체는 HTTP 200 envelope 안의 `status`에 **실제 upstream HTTP 상태**를 담는다. `status: null`은 서버에 도달하지 못한 경우다. `elapsed_ms`, `request_id`, `body`를 함께 보관한다. 원본 요청/응답 JSON에 없는 관측값을 만들어내지 않는다.

## 개발 검증

```sh
python3 -m unittest discover -s tools/rag-playground -v
node --check tools/rag-playground/static/app.js
node --test tools/rag-playground/test_ui.mjs
docker compose -f compose.playground.yaml config --quiet
```

unittest는 임시 HTTP upstream을 사용해 실제 요청 전달·오류 보존·고정 경로·출처 차단을 검증한다. 모델 품질 시험이 아니다. 컨테이너는 비관리자 사용자·read-only 파일시스템으로 실행하며 호스트 `127.0.0.1`에만 포트를 공개한다. 별도 DB·모델 다운로드·외부 패키지 설치가 없다. 팀 공유용 공개 배포나 인증 없는 포트포워딩 용도로 사용하지 않는다.

## 2026-11-26 검증 기록

- Python gateway 테스트 9개와 Node UI 회귀 테스트 3개 통과. 응답 상태·JSON·리다이렉트·요청 경계·API prefix 링크·이력 제한을 확인했다.
- Docker Desktop에서 Compose 빌드/실행, 비관리자/read-only, 루프백 8788 게시와 컨테이너 healthy를 확인했다. Linux Engine 실기 검증은 하지 않았다.
- 현재 실제 `server:app`에 연결해 liveness 200 / readiness 503, 질문 `503 rag_not_connected` 및 요청 ID 표시를 브라우저에서 확인했다.
- 별도 임시 HTTP 대역으로 정상 답변·ko/en 후속 이력·official_html/PDF 근거·위험 링크 차단·JSON 다운로드·429 실패·4,001자 답변 이력 제외와 390px 화면의 수평 넘침 없음을 확인했다. 이 검증은 실제 LLM 답변 품질 평가가 아니다.
- 독립 리뷰에서 확인한 API prefix 사용 시 PDF 링크 경로 누락을 수정하고 실제 브라우저 및 Node 회귀 검사로 확인했다.
