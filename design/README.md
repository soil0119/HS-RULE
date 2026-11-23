# HS-RULE 프런트엔드 디자인 스냅샷

Local Codex로 완성한 질문 화면, 관리자 로그인, PDF 문서 관리 화면을 [`hs-rule-ui/`](hs-rule-ui/)에 보존했습니다. 상단 오른쪽에서 질문 예시 대상, 한국어·영어 UI, 계정 메뉴에 접근할 수 있습니다. 세 화면 모두 로컬 SUIT Variable 폰트를 로드합니다.

저장소 루트에서 정적 미리보기를 실행합니다.

```sh
python3 -m http.server 8001 --bind 127.0.0.1 --directory design/hs-rule-ui
```

브라우저에서 `http://127.0.0.1:8001`을 엽니다. 이 정적 미리보기에서 API 호출은 실패가 예상되며 화면은 실제 실패 상태를 표시합니다. 모의 API 응답은 선택적으로 실행하는 QA 픽스처 안에서만 사용합니다.

Local Codex가 반환한 원본 미리보기 주소는 `http://127.0.0.1:49567/api/projects/hs-rule-regulation-ui-local/raw/index.html`입니다. 보존 소스의 디자인 규칙과 인계 문서, 당시 검토 화면과 알려진 제한을 함께 볼 수 있습니다. [디자인 규칙](hs-rule-ui/brand-spec.md) · [인계 문서](hs-rule-ui/integration-guide.md) · [검토 결과](review/README.md)

기존 루트 화면과 운영 경로 교체는 [#13](https://github.com/soil0119/HS-RULE/issues/13)에서 진행합니다. 이 디자인은 PDF가 기본 자료였던 시점에 의뢰했습니다. 공식 규정 크롤링 결과와 HTML 조문 인용 반영은 [#15](https://github.com/soil0119/HS-RULE/issues/15)에서 다룹니다.
