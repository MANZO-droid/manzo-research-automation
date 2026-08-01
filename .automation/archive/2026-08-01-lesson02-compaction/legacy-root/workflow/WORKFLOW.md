# WORKFLOW (2강 공통 실습 기준)

이 문서는 지금 시험 중인 **공통 샘플(가상 Top10 상승 종목 자동화)** 의 흐름을 적습니다. 만조리서치 Top10 자동화의 실제 워크플로우는 아래 "개인 자동화" 절을 확인 필요 상태로 남겨 두고, 설계도(`design/automation-blueprint.md`)의 입력·결과물 정의를 우선합니다.

## 가상 실습 흐름 (표시: 규격화·비교 연습용, 실제 만조리서치 완료 증거 아님)

1. **시작 조건**: 새 대화에 `02-reference/output-contract.txt`와 `01-input/`의 정상 입력 **한 건**만 첨부.
2. **입력**: 당일 상승률 상위 종목 원시 자료(N-01 또는 N-02). 자료 밖 정보·도구·연결은 쓰지 않음.
3. **처리 순서**: `강의자료/common-sample-pack/repetition-test-prompt.txt`의 지시를 그대로 사용해 한 줄 요약 → Top10 정리(필터링) → 상승 이유 요약 → 확인 필요 → 게시 초안 → 사람 검토·게시 확인 순서로 작성.
4. **출력 규격**: `output-contract.txt`의 여섯 heading을 정확한 순서·표기로 사용. Top10 정리는 순위/종목명/종목코드/등락률/거래량/최종 포함 여부 표.
5. **사람 검토**: 결과를 `expected-results.txt`와 비교하고, 형식·필터링·분량·근거·빈칸·경계를 사람이 확인한 뒤에만 `03-output/`에 결과 파일로 저장.
6. **중단 조건**: 같은 원본이 다시 들어오면(중복 입력) 새 결과·초안·외부 행동·정상 완료 기록을 만들지 않고 중단.
7. **필수 정보 누락·형식 오류(E-01) 처리**: 종목코드·구분(우선주/관리종목/ETF/정리매매)이 빠지거나 핵심 정보가 불명확해도 결과 생성 자체는 중단하지 않음. 빠지거나 알 수 없는 항목은 모두 `확인 필요`로 표시하고, 나머지 채울 수 있는 항목만으로 여섯 heading 결과를 끝까지 작성.
8. **게시(배포)**: `강의자료/common-sample-pack/publish-checklist.txt`의 모든 항목(필터링·분량·근거·확인 필요·민감정보 노출 여부)이 확인된 경우에만 사람이 게시 여부를 결정. 이 연습에서는 실제 `git push`나 배포를 실행하지 않고, 하나라도 확인되지 않으면 `게시 보류` 이유를 기록.

## 개인 자동화 흐름 (만조리서치 Top10) — 코드 기준 확인 (2026-07-25, krx_calendar 연동 복원 후)

실제로는 `scripts/collect_gainers.py` 하나의 파이프라인(A)이 이 흐름을 담당합니다. 이 파일은 한 번
`feature/trading-day-automation` 브랜치에서 휴장일 자동 판별 연동까지 끝난 채 커밋됐다가, 로컬에서만
그 연동이 빠진 옛 버전으로 되돌아가 있던 걸 2026-07-25에 발견해 복원했습니다(`AUTOMATION_NOTES.md`
참고). 아래는 복원된 정상 상태 기준입니다.

- **시작 조건**: **GitHub Actions**(`.github/workflows/gainers-daily.yml`)가 매일 07:00 UTC(=16:00 KST)에 인자 없이 호출 → `scripts/krx_calendar.py`의 `get_weekly_report_trigger()`가 `krx-holidays-2026.json` 기준으로 오늘 발행 여부와 daily/weekly 모드를 자동 결정 (확인된 사실). 기존 Windows 작업 스케줄러(`scripts/setup_scheduler.ps1`, 평일/토요일 16:00 고정)는 아직 안 꺼져 있어 중복 실행 위험 있음 — 확인 필요
- **입력**: 네이버 증권 상승률/거래대금 크롤링, 네이버 fchart OHLCV(120일), 종목당 네이버 뉴스 최대 15개 (확인된 사실 — `scripts/collect_gainers.py`)
- **처리 순서** (확인된 사실):
  1. 무인 실행(인자 없음)이면 `get_weekly_report_trigger()`로 오늘이 발행일인지·daily/weekly인지 자동 판단, 발행일이 아니면 그냥 종료
  2. `get_daily_top10()`(또는 주간은 `get_weekly_top10()`)로 KOSPI+KOSDAQ 상승률 상위 10 선정 — **우선주·관리종목·ETF·정리매매 제외 필터링은 아직 코드에 없음(확인 필요, krx_calendar 복원과는 별개 문제)**
  3. `fetch_volume_stocks()`로 거래대금 상위 10 선정
  4. 종목별로 OHLCV 120일 수집 → 최근 60일만 저장, 기술적 지표(`calc_technicals`) 계산
  5. 종목별 뉴스 최대 15개 수집(`fetch_stock_news`), 상위 5개만 저장. **뉴스가 0건이면 분석 자체를 생략**(riseReason에 안내 문구만, chartAnalysis는 빈 값 — 뉴스 없다고 지어내지 않음)
  6. 뉴스가 있으면 Gemini로 `riseReason`(200자 이상)·`chartAnalysis`(150자 이상, 실제 계산된 technicals 수치를 근거로) 생성
  7. `stock-analysis-data.json`에 날짜별로 저장(upsert, 기존 날짜 안 지워짐)
  8. `git_push()`가 스크립트 안에서 add→commit→push까지 자동 실행 (git push 실패해도 오류 메시지만 출력하고 스크립트는 "완료"로 끝남)
- **출력 규격**: `stock-analysis-data.json`의 `dates[날짜].gainers[]`(rank/ticker/name/close/changePct/volume/tradeAmount/w52High/w52Low/technicals/financials/naverUrl/ohlcv/news/riseReason/chartAnalysis)와 `volumeStocks[]` — 확인된 사실 (실제 파일 구조로 확정)
- **사람 검토**: 게시(git push) 후 사후 검토 — 다만 코드에는 사후 검토를 유도하는 알림 장치가 없어, 사용자가 스스로 사이트를 확인해야 함 (확인 필요: 실패/완료 알림 채널)
- **중단 조건**: 설계 의도는 "제미나이 토큰 소진, API 오류, 필터링 후 Top10 10개 미만, 새 권한 설정 필요"이지만, 실제 코드는 Gemini 429 시 재시도 후 실패해도 빈 값으로 계속 진행하고, Top10이 10개 미만이어도 중단하지 않음 — **설계와 코드 중 어느 쪽을 기준으로 삼을지 확인 필요**
