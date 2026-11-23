# HS-RULE 프런트엔드 UI

Local Codex로 만든 화면에 사용자 피드백을 반영한 현재 UI를 [`hs-rule-ui/`](hs-rule-ui/)에서 관리합니다. 상단 오른쪽에서 질문 예시 대상, 한국어·영어 토글, 계정 메뉴에 접근할 수 있습니다. 빈 질문창은 예시 문장을 쓰고 지우며 순환하고, 포커스·입력 중에는 멈춥니다. 세 화면 모두 로컬 SUIT Variable 폰트를 로드합니다.

공개 주소는 [hs.officeworkkit.com](https://hs.officeworkkit.com/)입니다. 이번 배포는 UI만 포함하며 실제 규정 수집·질문 API는 후속 연결 대상입니다. [배포와 검증 기록](../docs/deployment.md)

저장소 루트에서 정적 미리보기를 실행합니다.

```sh
python3 -m http.server 8001 --bind 127.0.0.1 --directory design/hs-rule-ui
```

브라우저에서 `http://127.0.0.1:8001/index.html`을 엽니다. 경로에 `index.html`을 포함하면 로그인과 문서 관리 링크가 정적 HTML 파일로 연결됩니다. 이 정적 미리보기에서 API 호출은 실패가 예상되며 화면은 실제 실패 상태를 표시합니다. 모의 API 응답은 선택적으로 실행하는 QA 픽스처 안에서만 사용합니다.

Local Codex가 반환했던 원본 미리보기 주소는 `http://127.0.0.1:49567/api/projects/hs-rule-regulation-ui-local/raw/index.html`입니다. 현재 소스에는 이후 수정이 포함됩니다. 디자인 규칙과 인계 문서, 초기 검토 화면을 함께 볼 수 있습니다. [디자인 규칙](hs-rule-ui/brand-spec.md) · [인계 문서](hs-rule-ui/integration-guide.md) · [초기 검토 결과](review/README.md)

`archive/`와 `qa/viewport-review.html`은 저장소와 공개 배포에서 제외했습니다.

규정의 기본 자료는 학교 공식 규정 사이트를 크롤링한 원문입니다. 공개 안내와 예시는 이 기준으로 수정했습니다. 실제 수집 데이터·HTML 조문 인용 연결은 [#15](https://github.com/soil0119/HS-RULE/issues/15)와 [#19](https://github.com/soil0119/HS-RULE/issues/19), 기존 FastAPI 화면 연결은 [#13](https://github.com/soil0119/HS-RULE/issues/13)에서 진행합니다. 관리자 PDF 업로드는 별도 보조자료 기능입니다.
