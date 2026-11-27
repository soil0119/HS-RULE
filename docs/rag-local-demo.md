# RAG 로컬 질문 도구 (#76)

웹 API(#20·#66)가 연결되기 전에도 우리 RAG·LangChain 코드를 실제 임베딩·실제 모델로 시험하는 방법이다. 질문을 입력하면 실제 DB 검색 → 모델 답변 → 근거 조항·공식 URL, 추가 질문, 수치 근거 검사, 모델 호출 수·토큰·지연을 한 번에 보여 준다. 웹 화면 시험은 #80의 Playground가 #20·#66 이후 맡는다.

## 준비 (Windows Git Bash 기준)

```sh
# 1) 일회용 DB. .env에 POSTGRES_PASSWORD를 넣고 실행한다. 운영 DB는 쓰지 않는다.
docker compose up -d --wait db

# 2) 패키지(langchain-google-genai 포함)
python -m pip install -r requirements.txt

# 3) 설정: 저장소 최상위 .env에 아래 줄을 넣는다(.env는 .gitignore에 있어 커밋되지 않는다).
#    키는 .env에만 두고 코드·커밋·채팅·로그에 넣지 않는다. 도구가 .env를 자동으로 읽는다.
#    POSTGRES_PASSWORD=<기존 값>
#    DATABASE_URL=postgresql://postgres:<POSTGRES_PASSWORD>@127.0.0.1:5432/hansungrules
#    LLM_PROVIDER=gemini
#    GEMINI_API_KEY=<AI Studio 키>
#    GEMINI_MODEL=gemini-3.5-flash
```

- AI Studio가 새로 발급하는 `AQ.`로 시작하는 키도 그대로 쓴다. 키는 `x-goog-api-key` 헤더로 Gemini API에 보낸다.
- `GOOGLE_GENAI_USE_VERTEXAI`가 켜져 있으면 키가 Vertex AI로 가서 거부되므로, 도구가 시작하지 않고 알린다.
- 모델 ID는 AI Studio에서 무료 티어로 표시되는지 확인한다. Groq를 쓰려면 `LLM_PROVIDER=groq GROQ_API_KEY=… GROQ_MODEL=…`.

## 실행

```sh
python scripts/ask_rag.py --ping                                    # 키·모델 연결만 확인(호출 1회)
python scripts/ask_rag.py --index --log eval/results/demo.jsonl     # 색인 + 대화형 질문
python scripts/ask_rag.py -q "졸업하려면 몇 학점 필요해요?"          # 한 번만
python scripts/ask_rag.py --language en -q "What does Article 34 say?"
```

- 첫 실행은 임베딩 모델(`paraphrase-multilingual-MiniLM-L12-v2`)을 내려받는다. `--index`나 `--check-embeddings`를 주면 입력 길이·384차원·한국어 구분 사전검증 결과를 먼저 출력하고, 통과하지 못하면 멈춘다.
- `--snapshot`이 없으면 학칙 발췌본(표본)만 색인하며 출력에 그렇게 표시한다. 수집 스냅샷(#17)이 생기면 `--snapshot 파일`로 바꾼다. 다시 실행해도 바뀐 규정만 다시 임베딩한다.
- 대화 명령: `/new` 새 대화, `/en`·`/ko` 답변 언어, `/private` 비공개 규정 포함 전환, `/quit` 종료.
- 시작할 때 작은 호출 1회로 키·모델을 확인하고, 실패하면 DB·임베딩을 건드리기 전에 원인(401·403 키, 404 모델, 429 한도, 시간 초과)을 알려 준다. `--no-ping`으로 끌 수 있다.
- 오류는 원인별로 나온다: `timeout`(시간 초과), `rate_limit`(무료 한도 초과 429), `provider`(키·모델 ID·네트워크), `json`(형식). 키·모델 설정 누락은 시작할 때 바로 알린다.

## 시연 순서 (#76 완료 기준)

1. 첫 질문: `졸업하려면 몇 학점 필요해요?` → 조건별 규정(130/140학점)과 입학년도 추가 질문
2. 후속 질문: `저는 2014학번이에요` → 부칙 경과조치(140학점) 근거
3. 근거 없음: `통학버스 시간표는 어떻게 되나요?` → 거절
4. 영어: `/en` 후 `I was admitted in 2014. How many credits do I need to graduate?`

`--log` 파일에 질문·답변·근거·호출 수·토큰·지연·모델 ID·임베딩 모델·데이터·커밋이 한 줄씩 남는다. 이 파일을 #76에 첨부하면 실제 연결 기록이 된다. 무료 한도는 질문 수가 아니라 호출 수 기준이다. 질문 하나에 보통 1회, 후속 질문은 재작성 1회 추가, 형식·수치 고쳐 쓰기나 429·5xx 재시도가 있으면 더 늘어난다.
