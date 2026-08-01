# 만조리서치 자동화 — 실제 코드 수정 기록 (2026-07-25)

`manzo-regression-2026-07-13.md`에서 발견한 3가지 문제를 전체 데이터(120개 종목 항목)로
범위를 넓혀 재확인한 뒤, `scripts/collect_gainers.py`를 실제로 고쳤습니다.

## 전체 데이터 재확인 결과 (수정 전 기준)

| 문제 | 범위 | 확인 방법 |
| --- | --- | --- |
| ① `chartAnalysis`의 크로스 서술이 실제 `technicals.cross`와 다름 | 크로스를 언급한 10건 중 7건 불일치. 육일씨엔에쓰(2026-07-13)는 실제 `데드크로스`인데 `골든크로스`로 서술(정반대) | `data['dates'][*]['gainers'][*]`를 전수 스캔해 언급된 크로스 종류와 `technicals.cross`를 대조 |
| ② 뉴스 0건인데 매우 구체적인 사실을 단정적으로 서술 | 케이피엠테크(2026-07-06 등)가 "추정"이라는 표현 없이 "7월 3일 공시한 225억원 규모 제3자배정 유상증자(텔콘RF제약 대상)"처럼 날짜·금액·상대방 회사명을 지어냄. 같은 유형 최소 5건 | `riseReason`에서 뉴스 제목에 없는 특정 사건 키워드(유상증자·무상증자·조회공시·공매도 등)를 검색, 해당 건의 `news` 개수 대조 |
| ③ 뉴스 `summary`가 항상 빈 문자열 | 확인한 사례 전부 | `finance.naver.com/item/news_read.naver` 실제 URL을 직접 요청 → `<SCRIPT>top.location.href=...</SCRIPT>` JS 리다이렉트만 반환됨을 확인 |

## 적용한 수정 (`scripts/collect_gainers.py`)

1. **`fetch_article_summary()`**: JS 리다이렉트(`top.location.href=...`)를 정규식으로 찾아 실제 기사 주소(`n.news.naver.com/mnews/article/...`)로 재요청하도록 수정. 선택자에 `#dic_area` 추가. 실제 URL로 재검증 완료 — 이전엔 빈 문자열이었던 것이 실제 기사 본문(186자)을 가져옴.
2. **`analyze_stock()`**: `technicals` 인자를 새로 받아 프롬프트에 ma5/ma20/ma60/ma120·거래량비율·추세·크로스 여부 등 실제 계산값을 그대로 제공. "크로스 없음"이면 골든/데드크로스를 지어내지 말라고 명시. `run_daily`/`run_weekly`의 호출부에도 `technicals=g["technicals"]` 전달하도록 수정.
3. **`analyze_stock()` 프롬프트**: 뉴스가 있는 경우/없는 경우 모두 "기사(또는 확인된 사실)에 없는 날짜·금액·공시 종류·계약 상대방을 지어내지 말라"는 문구를 추가.

## 검증한 것 / 검증하지 못한 것

- **검증함**: 문법 오류 없음(`python -m py_compile`), 실제 저장된 뉴스 URL로 ①·③ 수정 후 `fetch_article_summary()`가 실제 본문을 가져오는지, `analyze_stock()`이 만드는 프롬프트에 실제 기술적 지표와 새 지시문이 정확히 들어가는지 — 이상 모두 Gemini를 호출하지 않고(가짜 응답 함수로 대체) 확인.
- **검증 못함**: 새 프롬프트로 실제 Gemini를 호출했을 때 riseReason·chartAnalysis 결과가 실제로 더 정확해지는지는, Gemini 토큰을 쓰고 결과를 `stock-analysis-data.json`에 반영·**자동 git push**까지 실행하는 실제 파이프라인 실행이 필요해 이번에는 실행하지 않았습니다.

## 확인 필요 (다음 실제 실행 때 사람이 볼 것)

- 다음 GitHub Actions 실행(또는 수동 실행 `python scripts/collect_gainers.py --mode daily`) 이후 새로 생성된 `riseReason`/`chartAnalysis`가 위 세 문제 없이 나오는지 실제로 대조

## 뒤늦게 발견한 것: 위 수정을 처음엔 잘못된 기준 파일에 적용했었음

위 3가지 수정을 처음 적용한 뒤, `scripts/collect_gainers.py`가 원래
`feature/trading-day-automation` 브랜치에서 `krx_calendar`(휴장일 자동 판별) 연동까지 끝난 채
커밋됐다가, 로컬에서만 그 연동이 빠진 옛 버전으로 되돌아가 있었다는 걸 발견했습니다(사용자 확인 결과
의도한 되돌림이 아니었음). 이 옛 버전에는 원래 없던 "뉴스 0건이면 Gemini가 추정해서 지어내기" 동작이
섞여 있었는데, 그게 바로 위 ②(케이피엠테크 사례)의 진짜 원인이었습니다.

그래서:
1. `git checkout HEAD -- scripts/collect_gainers.py scripts/collect_market_scope.py api/cron-update-gainers.js`로 커밋된 정상 버전(krx_calendar 연동 포함)을 복원
2. 위 3가지 수정(①②③)을 복원된 정상 버전 위에 다시 적용
3. 정상 버전에는 애초에 "뉴스 0건 → 추정 지어내기" 동작 자체가 없어서(뉴스 0건이면 분석을 생략), ②는 프롬프트 지시 추가와 별개로 **구조적으로 이미 발생할 수 없는 문제**였다는 걸 확인함

자세한 배경은 `AUTOMATION_NOTES.md`, 정정된 트리거 구조는 `design/automation-blueprint.md` 참고.
