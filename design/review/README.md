# 디자인 검토

| 확인 항목 | 결과 |
| --- | --- |
| 데스크톱 · 1280 × 720 | 질문 화면과 상단 오른쪽 설정 영역을 확인했습니다. [캡처](desktop.jpg) |
| 모바일 · 390 × 844 | 영어 질문 화면과 설정 메뉴를 확인했습니다. [질문 화면](mobile-en.jpg) · [설정 메뉴](mobile-settings.jpg) |
| 저장소 검증 | JavaScript 문법, HTML·CSS의 로컬 자산 참조, SUIT 폰트 해시를 확인했습니다. 생성된 QA 픽스처의 모의 API·DOM 검사 575개가 통과했습니다. |

`git diff --check`에서 SUIT 라이선스 원본 22행의 끝 공백이 검출됐습니다. 라이선스를 원본과 동일하게 보존했으며, 그 파일을 제외한 검사는 통과했습니다.

QA 픽스처는 Node.js 20 이상과 Python 3에서 실행할 수 있습니다.

```sh
cd design/hs-rule-ui
node qa/verify.cjs
```

| 남은 작업 | 확인 사항 |
| --- | --- |
| [#13](https://github.com/soil0119/HS-RULE/issues/13) · 화면 통합 | 390px 영어 화면에서 `History` 버튼의 글자가 좁게 여러 줄로 꺾입니다. 레이아웃 수정 후 실제 브라우저에서 확인합니다. FastAPI를 연결한 로그인·질문·업로드·내보내기 종단 간 검증도 남아 있습니다. |
| [#15](https://github.com/soil0119/HS-RULE/issues/15) · 공식 규정 반영 | 현재 화면 문구와 인용 구조는 PDF 문서·쪽수 기준입니다. 공식 크롤링 결과와 HTML 조문 근거에 맞춘 표시가 필요합니다. |
