# HS-RULE · 문서 도구 시각 시스템

사용자가 지정한 전면 재설계: 흰색과 중립 회색, 거의 검정인 글자, 학술적 파랑 하나를 사용하는 간결한 대학 PDF 문서 도구.

```css
:root {
  --bg: oklch(1 0 0);
  --surface: oklch(0.975 0 0);
  --fg: oklch(0.21 0.004 260);
  --muted: oklch(0.48 0.008 260);
  --border: oklch(0.875 0.004 260);
  --accent: oklch(0.48 0.17 260);
  --font-display: "SUIT Variable", sans-serif;
  --font-body: "SUIT Variable", sans-serif;
  --font-mono: "SUIT Variable", monospace;
}
```

- 공식 SUIT Variable 100–900을 `fonts/SUIT-Variable.woff2`에서 실제 로드한다. 한글과 영문에 동일하게 적용한다. 출처: https://github.com/sun-typeface/SUIT (OFL-1.1, `fonts/OFL-1.1.txt`).
- 64px 상단 바에 워드마크·주요 탐색·오른쪽 설정을 배치한다. 휴대폰에는 탐색 탭과 설정 메뉴를 사용한다.
- 26px 화면 제목, 18px 구역 제목, 16px 본문, 13–14px 메타데이터를 사용한다. 영문 대문자 홍보 문구·환영 슬로건은 없다.
- 화면은 행·표·구분선으로 조직한다. 둥근 카드·과도한 빈 공간·장식용 지표를 사용하지 않는다.
- 질문 이력은 필요한 순간 여는 패널이다. PDF 목록과 문서 관리는 화면 너비를 사용한다. 파란색은 현재 탐색 위치와 주요 작업에 제한한다.
