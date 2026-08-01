# 자동화 작업 노트 (feature/trading-day-automation)

이 문서는 `feature/trading-day-automation` 브랜치에서 수행한 작업의 배경, 해석이
필요했던 애매한 규칙에 대한 결정, 검증 결과, 그리고 사람이 반드시 해야 하는
후속 조치를 정리합니다.

---

## 0. 기존 아키텍처 진단 요약 (작업 전 확인된 사실)

이 사이트에는 서로 독립적인 자동화 파이프라인 3개가 있고, 그중 클라우드에서
"확실히" 도는 건 하나뿐입니다.

| 파이프라인 | 실행 주체 | 상태 |
|---|---|---|
| 상승률 top10 (`stock-analysis-data.json`) | **사용자 PC의 Windows 작업 스케줄러** (`scripts/collect_gainers.py`) | PC가 꺼져 있으면 갱신 안 됨 |
| 상승률 top10 (DB, `/api/top-gainers`) | **Vercel Cron** (`vercel.json`, `0 7 * * *` UTC = 매일 16:00 KST) | 클라우드에서 실제로 도는 유일한 크론이지만, 2026-07-12 이후 갱신이 멈춰 있음(원인 미상 - 아래 5번 참고) |
| 마켓 스코프 (`market-scope-data.json`) | **Cowork Scheduled Task**(`market-scope-daily-update`, 로컬 새벽 5시) | Cowork 데스크톱 앱이 그 시간에 열려 있어야 정시 실행됨 |

`.github/workflows` 디렉토리는 이 작업 이전에는 **존재하지 않았습니다** (GitHub
Actions 워크플로우가 아예 없었음). 즉 "GitHub에 뭔가 자동으로 도는 게 있겠지"라는
기대와 달리, 실제로 클라우드에서 매일 확실히 실행되는 건 Vercel Cron 1개뿐이었고,
나머지 두 파이프라인은 각각 "사용자의 PC가 켜져 있는지"와 "Cowork 앱이 열려
있는지"라는, 사이트 운영과 무관한 외부 조건에 암묵적으로 의존하고 있었습니다.
이게 "며칠씩 데이터가 안 올라온다"는 증상의 근본 원인입니다.

이번 작업으로 상승률/마켓스코프 두 파이프라인 모두에 **GitHub Actions 워크플로우**를
추가해, PC나 Cowork 앱 상태와 무관하게 클라우드에서 매일 자동 실행되도록
했습니다 (`.github/workflows/gainers-daily.yml`, `.github/workflows/market-scope-daily.yml`).

---

## 1. Task 1 — 거래일 인식 상승률 자동화

### 1-1. 규칙 해석 (스펙이 애매했던 부분에 대한 결정)

원 요구사항의 4개 규칙 중 2·3번은 실제로는 **하나의 알고리즘**의 특수 사례로
구현했습니다:

> "토요일부터 거꾸로(금요일, 목요일, …) 훑어 내려가며, 연속된 비거래일(휴장
> 평일) 구간을 찾는다. 그 구간의 **가장 이른 날짜**가 '주간 리포트 발행일'이다."

- 금요일이 정상 개장일이면 → 구간은 토요일 하루뿐 → **토요일**에 발행 (규칙 2 기본 케이스)
- 금요일만 휴장이면 → 구간은 금·토 이틀 → **금요일**에 발행 (규칙 2: "금요일이
  휴일이면 금요일 대신 그 전 개장일에 발행"이 아니라, **"월~목 데이터로 정리해
  금요일 그 자체에 발행"**으로 해석했습니다 - 스펙 원문의 "그 전 개장일에
  발행한다"는 표현과 "정확히: ... 금요일에 발행한다"는 표현이 모순되어 보였는데,
  후자(금요일 당일 발행)를 채택했습니다. 이유: 목요일까지의 데이터를 다음 개장일인
  "그 전 개장일"(즉 목요일 자체)에 발행하는 건 시간 순서상 불가능하고, 실질적으로
  구현 가능한 유일한 해석은 "휴장일인 금요일 그날, 월~목 데이터를 정리해
  발행한다"이기 때문입니다.
- 목·금이 모두 휴장(토요일 포함 3일 연속 휴장)이면 → 구간은 목·금·토 사흘 →
  **목요일**(연휴 시작일)에 발행 (규칙 3)

이 로직은 `lib/krx-calendar.js`의 `getWeeklyReportTrigger()`와
`scripts/krx_calendar.py`의 `get_weekly_report_trigger()`에 동일하게 구현되어
있고, 아래 3번 항목의 테스트로 검증했습니다.

### 1-2. 구현 파일

- `krx-holidays-2026.json` — 2026년 KRX 휴장일 21건 (아래 2번 참고)
- `lib/krx-calendar.js` — `isTradingDay`, `previousTradingDay`, `nextTradingDay`,
  `getWeeklyReportTrigger` (CommonJS, 외부 의존성 없음)
- `scripts/krx_calendar.py` — 동일 로직의 파이썬 버전 (같은 JSON 파일을 읽음)
- `api/cron-update-gainers.js` — 핸들러 시작 시 `isTradingDay(today)`를 확인해
  개장일이 아니면 키움 API를 호출하지 않고 `{ok:true, skipped:true, reason:'not a trading day'}`를
  반환. 주간 발행 트리거 여부는 로그로만 남김 (아래 "범위 밖" 참고).
- `scripts/collect_gainers.py` — `--date`/`--mode`를 모두 생략한 무인 실행(예:
  GitHub Actions)에서는 `get_weekly_report_trigger(오늘)`로 발행 여부와
  daily/weekly 모드를 자동 결정. `--date` 또는 `--mode`를 명시하면 기존 동작(구
  버전 방식) 그대로 유지 - 하위호환.

### 1-3. 범위 밖으로 남긴 것 (명시적 TODO)

`api/cron-update-gainers.js`는 Supabase `daily_gainers` 테이블에 **일별**
데이터만 적재하는 구조입니다. "주간 리포트"라는 개념 자체가 이 DB 스키마에는
없고, `weekly_gainers` 같은 별도 테이블과 그 테이블을 채우는 집계 로직이
필요합니다. 이번 작업에서는:
- 매일 개장일 여부는 **확실히** 구현했습니다(핵심 요구사항).
- 주간 집계를 Supabase 파이프라인에 새로 연결하는 것은 스키마 변경이 필요한
  더 큰 작업이라 TODO로 남겼습니다(파일 내 주석 참고). 대신 주간 리포트는
  기존처럼 `scripts/collect_gainers.py`(JSON 기반)가 전담합니다.

---

## 2. 2026년 KRX 휴장일 데이터 출처

WebSearch로 아래 소스들을 교차 확인해 `krx-holidays-2026.json`을 작성했습니다.
(단순 캘린더 사이트 정보뿐 아니라, 대체공휴일·임시공휴일처럼 그해에만 특별히
발생하는 항목은 국내 뉴스 보도로 재확인했습니다.)

- https://markethours.io/market-holidays/krx (2026년 KRX 17개 정규 휴장일 목록)
- https://www.calendarlabs.com/krx-market-holidays-2026/
- 대체공휴일 4건(3.1절 대체 3/2, 부처님오신날 대체 5/25, 광복절 대체 8/17,
  개천절 대체 10/5) — 확인: https://www.cbci.co.kr/news/articleView.html?idxno=587522 등
  국내 뉴스 다수
- 추석 연휴가 9/24(목)~9/26(토) 3일임(대체공휴일 없음, 연휴에 일요일이
  끼지 않으므로) — 확인: 국내 포털 검색 결과 다수(예: dallyeok.com, kholidayz.com)
- **6월 3일 제9회 전국동시지방선거 임시공휴일 + 증시 휴장** — 확인:
  한국경제(hankyung.com/article/2026052094456), MBC(imnews.imbc.com/news/2026/econo/article/6823907_36932.html),
  국제뉴스(gukjenews.com) 등 2026년 5월 실제 보도. "한국거래소, 6월 3일·7월 17일
  전 시장 휴장"이라는 제목의 기사가 두 날짜를 함께 명시.
- **7월 17일 제헌절 부활(18년 만에 법정공휴일 재지정, 대체공휴일 없음 - 요일이
  금요일이라 겹치는 날 없음)** — 확인: 위와 동일한 한국경제/MBC 기사, 그 외
  다수 국내 매체(예: kgosu.com, daouoffice.com)

**신뢰도 평가**: 위 출처들은 모두 2026년 실제 보도/공지 기반이라 상당히 신뢰할
수 있다고 판단했지만, **한국거래소(KRX) 공식 홈페이지의 "증권시장 휴장일" 공지
원문 자체는 직접 열람하지 못했습니다** (WebFetch로 KRX 공식 사이트 접근 시도는
하지 않음). 따라서:

> ⚠ **`krx-holidays-2026.json`은 사람이 한국거래소(open.krx.co.kr 또는
> global.krx.co.kr) 공식 2026년 휴장일 공지와 최종 대조 확인해 주세요.**
> 특히 아래 항목은 뉴스 보도 기준으로는 확실하지만, 공식 공지의 정확한 표현
> (예: 파생상품시장 야간거래 등 부분 휴장 여부)까지는 확인하지 못했습니다:
> - 2026-06-03 (지방선거 임시공휴일)
> - 2026-07-17 (제헌절 재지정 첫 해)
> - 2026-12-31 (연말 휴장일 - 매년 관행이지만 그해 공지로 재확인 필요)

주말(토요일)에 이미 걸리는 현충일(6/6), 광복절(8/15), 추석 마지막날(9/26),
개천절(10/3)도 목록에 포함해 두었습니다 - `isTradingDay()`는 어차피 주말을
먼저 걸러내므로 로직에는 영향이 없고, 문서화 목적으로만 남겼습니다.

**2027년 이후**: 이 JSON 파일은 2026년 데이터만 담고 있습니다. 매년 초
`krx-holidays-2027.json` 등을 추가로 만들고, `lib/krx-calendar.js`와
`scripts/krx_calendar.py`의 `HOLIDAY_FILES` 배열에 등록해야 다음 해에도
정확히 동작합니다 (등록하지 않으면 주말만 자동 제외되고 평일 휴장일은
누락됩니다 - 조용히 실패하는 부분이니 매년 1월 초 확인 필요).

---

## 3. 거래일 로직 검증 (날짜 케이스 테스트)

`lib/krx-calendar.js`와 `scripts/krx_calendar.py` 양쪽에서 동일한 결과가
나오는 것을 직접 실행해 확인했습니다.

| 날짜 | 설명 | isTradingDay | getWeeklyReportTrigger 결과 |
|---|---|---|---|
| 2026-07-20 (월) | 평상시 평일 개장일 | true | `{shouldRun:true, mode:'daily'}` |
| 2026-01-01 (목) | 신정, 휴장 | false | `{shouldRun:false, mode:'weekly', weekStart:'2025-12-29', weekEnd:'2026-01-02'}` (그 주 트리거는 토요일 1/3) |
| 2026-01-03 (토) | 정상 토요일 (금요일 1/2는 개장) | false | `{shouldRun:true, mode:'weekly', weekEnd:'2026-01-02'}` |
| 2026-07-17 (금) | 제헌절, 금요일 휴장 케이스 | false | `{shouldRun:true, mode:'weekly', weekEnd:'2026-07-16'}` → **금요일 당일 발행** |
| 2026-07-18 (토) | 위 케이스의 다음날 토요일 | false | `{shouldRun:false, ...}` → 이미 금요일에 발행했으므로 토요일엔 스킵(중복 방지 확인) |
| 2026-09-24 (목) | 추석연휴 시작(목·금·토 3일 연속 휴장) | false | `{shouldRun:true, mode:'weekly', weekEnd:'2026-09-23'}` → **목요일(연휴 시작일) 발행** |
| 2026-09-25 (금) | 추석 당일(연휴 중간) | false | `{shouldRun:false, ...}` |
| 2026-09-26 (토) | 추석연휴 마지막(토요일) | false | `{shouldRun:false, ...}` → 목요일에 이미 발행했으므로 스킵 |
| 2026-02-14 (토) | 정상 케이스 재확인 | false | `{shouldRun:true, weekEnd:'2026-02-13'}` |

`previousTradingDay('2026-01-01')` → `2025-12-31`, `nextTradingDay('2026-01-01')`
→ `2026-01-02`, `nextTradingDay('2026-09-23')` → `2026-09-28` (추석 연휴 3일 +
일요일을 건너뜀) 도 기대대로 동작 확인.

(참고: `previousTradingDay('2026-01-01')`이 2025-12-31을 반환하는 건, 이
저장소에 2025년 휴장일 JSON이 없어서입니다 - 실제로 2025-12-31도 KRX 연말
휴장일일 가능성이 높으므로, 2025년 데이터가 필요하면 `krx-holidays-2025.json`을
추가해야 정확해집니다. 지금은 2026년 로직 검증이 목적이라 범위 밖으로 뒀습니다.)

---

## 4. Task 3 — 백필 필요 날짜 (거래대금 volumeStocks 감사 결과)

`scripts/audit_volume_gaps.py`를 만들어 `stock-analysis-data.json`을 감사했습니다.
API 키가 없어 실제 데이터를 새로 수집할 수는 없으므로, 아래는 **현재 커밋된
파일 기준 결과**입니다 (실행: `python scripts/audit_volume_gaps.py`).

```
stock-analysis-data.json 내 날짜 수: 12개
latestDate(파일 기준): 2026-07-16

volumeStocks가 비어있는 날짜: 없음 (현재 파일에 있는 모든 날짜는 거래대금 데이터 보유)
```

즉, **현재 파일에 실제로 존재하는 12개 날짜(2026-07-02~07-16, 평일 10개 +
7/11 주간 리포트)는 모두 `volumeStocks`가 채워져 있어 별도 백필이 필요 없습니다.**

그런데 실제로 보고된 증상("거래대금 표에 데이터가 없다")은 이 감사 결과와는
다른 원인이었습니다 - Task 3에서 고친 근본 버그(§0, index.html
`loadStockAnalysis()`)를 다시 설명하면:
- DB(`/api/top-gainers`)의 `latestDate`가 **2026-07-12**로 멈춰 있는데,
  이 날짜는 애초에 `stock-analysis-data.json`에 항목 자체가 없습니다
  (파일은 7/11 다음이 7/13 - 즉 7/12는 존재하지 않는 날짜).
- 예전 코드는 `dbData.latestDate`를 그대로 `data.latestDate`로 덮어썼기 때문에,
  거래대금 테이블이 "존재하지도 않는 날짜(7/12)"를 렌더링하려다 실패해
  "데이터가 없습니다"가 뜬 것입니다.
- 이번 수정으로 거래대금 섹션은 이제 `latestVolumeDate`(파일 안에서
  `volumeStocks`가 실제로 채워진 마지막 날짜 = 2026-07-16)를 독립적으로
  사용하므로 이 증상은 재발하지 않습니다.

참고로 개장일 기준 파일의 실제 공백도 함께 확인했습니다 (`krx_calendar`로
2026-07-01~07-20 사이 개장일인데 파일에 아예 날짜 항목이 없는 날):
`2026-07-01`(데이터 시작 이전으로 추정), `2026-07-20`(오늘 - 아직 자동화가
돌지 않아 당연히 없음). 7/17은 이번에 확인한 제헌절 휴장일이라 개장일이
아니므로 공백이 아닙니다.

**결론 / 필요 조치**: 지금 당장 백필해야 할 "빈 volumeStocks" 날짜는 없습니다.
다만 실제 운영 데이터(사람이 가진 GEMINI_API_KEY로) 최신 상태에서 다시
`scripts/audit_volume_gaps.py`를 돌려 재확인하는 걸 권장합니다 - 이 저장소에
커밋된 스냅샷과 실제 프로덕션 데이터가 다를 수 있습니다. 공백이 나오면
`python scripts/collect_gainers.py --date YYYY-MM-DD --mode daily`(또는
`weekly`)로 해당 날짜를 재실행해 `enrich_gainers.py`까지 마저 돌리면 됩니다.

---

## 5. Task 5 — 후속 조치 권장 사항 (사람이 해야 할 일)

1. **GitHub Secrets 등록**: 저장소 Settings → Secrets and variables → Actions에서
   `GEMINI_API_KEY`를 등록해야 새로 추가한 두 워크플로우
   (`.github/workflows/gainers-daily.yml`, `.github/workflows/market-scope-daily.yml`)가
   동작합니다. 이 브랜치의 커밋만으로는 시크릿이 채워지지 않습니다.
2. **Vercel Cron 실패 원인 확인**: `/api/top-gainers`가 2026-07-12 이후
   갱신되지 않고 있습니다. Vercel 대시보드 → 해당 프로젝트 → Functions/Cron
   로그에서 `cron-update-gainers`의 최근 실행 기록을 확인해 주세요. 가능성
   높은 원인: 키움 토큰 만료/갱신 실패, Vercel 프로젝트에 `SUPABASE_URL`
   /`SUPABASE_SERVICE_ROLE_KEY` 환경변수 미설정 또는 만료, 키움 API 쿼터 초과
   등. (이 세션에는 Vercel 대시보드 접근 권한이 없어 로그를 직접 볼 수
   없었습니다.)
3. **중복 실행 방지**: GitHub Actions 워크플로우가 정상 작동하는 것을 확인한
   뒤에는 다음 두 가지를 반드시 끄거나 삭제해 주세요 (그대로 두면 같은 날
   두 번 커밋되거나, 서로 다른 시각에 실행되어 데이터가 뒤섞일 수 있습니다):
   - 사용자 PC의 **Windows 작업 스케줄러**에 등록된 `collect_gainers.py` 관련 작업
     (평일 오후 4시, 토요일 오후 4시)
   - Cowork의 **Scheduled Task** `market-scope-daily-update`
4. **`krx-holidays-2026.json` 최종 대조**: 위 2번 항목의 출처 한계 참고 -
   한국거래소 공식 2026년 휴장일 공지 원문과 마지막으로 한 번 더 대조해 주세요.
5. **매년 초 휴장일 데이터 갱신**: `krx-holidays-2027.json`을 매년 추가하고
   `lib/krx-calendar.js` / `scripts/krx_calendar.py`의 `HOLIDAY_FILES`에
   등록하는 작업이 반복적으로 필요합니다 (자동화되어 있지 않음).
6. **`scripts/audit_volume_gaps.py`를 실제 프로덕션 데이터로 재실행**해 진짜
   백필이 필요한 날짜가 있는지 확인 (§4 참고).

---

## 6. 변경 파일 목록 (요약)

- `krx-holidays-2026.json` (신규)
- `lib/krx-calendar.js` (신규)
- `scripts/krx_calendar.py` (신규)
- `scripts/audit_volume_gaps.py` (신규)
- `.github/workflows/gainers-daily.yml` (신규)
- `.github/workflows/market-scope-daily.yml` (신규)
- `api/cron-update-gainers.js` (수정 - 개장일 스킵 로직)
- `scripts/collect_gainers.py` (수정 - 무인 실행 시 자동 daily/weekly 판단)
- `scripts/collect_market_scope.py` (수정 - "오늘" 자동 실행 시 개장일에만 실행)
- `index.html` (수정 - 거래대금 날짜 독립 상태 + 월간 캘린더 UI + 스테일 날짜 버그 수정)
- `AUTOMATION_NOTES.md` (본 문서, 신규)

`vercel.json`은 내용 변경이 필요하지 않아 그대로 두었습니다(유효한 JSON이며,
`functions.maxDuration: 60`도 개장일 스킵 경로가 즉시 반환되므로 여전히
충분합니다). 실제 개장일 판단 로직은 함수 코드(`api/cron-update-gainers.js`)
안에서 처리합니다.
