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

---

## 7. 2026-08-01: 저장소 분리 + 상승 이유 분석을 Claude → Groq로 교체

- 회장님 요청으로 `manzo-site` 저장소 안의 `리서치자동화/`를 완전히 분리해
  이 독립 저장소(`E:\AI 스터디\리서치자동화\`)로 만들었습니다. `.github/workflows/`도
  같이 옮겨왔고, 결과 JSON은 여전히 사이트 저장소에 쓰므로 크로스 저장소
  체크아웃·푸시 방식으로 워크플로를 다시 짰습니다(`SITE_REPO_PATH` 환경변수,
  `SITE_REPO_PAT` 시크릿).
- 회장님이 "무과금으로 하려고 해서 Anthropic API는 설정을 안 했다"고 밝혀,
  `collect_gainers.py`의 `analyze_stock()`을 Claude(`claude-opus-5`, 유료)에서
  Groq(`llama-3.3-70b-versatile`, 무료, 카드 불필요)로 교체했습니다.
- 애초에 Claude로 바꾼 이유가 "Gemini 무료 할당량을 market-scope와 나눠 쓰다
  소진됨"이었는데, Groq는 market-scope가 쓰는 Gemini와 할당량이 완전히
  분리돼 있어 같은 문제가 재발하지 않습니다. Groq 무료 티어는 모델에 따라
  하루 1,000~14,400회 수준으로, 이 파이프라인이 쓰는 하루 10~20회보다 충분히
  여유롭습니다(2026-08-01 기준 — Gemini처럼 예고 없이 깎일 수 있어 시점을
  못박아 둡니다).
- **검증한 것**: python 컴파일, `groq` SDK import·생성자 시그니처(`max_retries`
  존재), `chat.completions.create`가 `max_tokens` 파라미터를 받는지 실제
  라이브러리에서 확인. **검증 못한 것**: 실제 Groq API를 호출해 한국어
  `riseReason`/`chartAnalysis` 품질을 확인하는 것 — 다음 실제 실행(수동
  `workflow_dispatch` 또는 다음 예약 실행) 때 사람이 결과물을 확인해야 합니다.

---

## 8. 2026-08-01: GitHub Actions 동작 검증 + JSON 하드코딩 → Supabase 직접 저장 전환

### 8-1. GitHub Actions 동작 검증 결과

`https://api.github.com/repos/MANZO-droid/manzo-research-automation`으로 직접
확인(저장소가 public이라 인증 없이 조회 가능, `gh` CLI는 이 환경에 설치돼
있지 않아 REST API로 대체):

- 두 워크플로(`gainers-daily.yml`, `market-scope-daily.yml`) 모두 **등록되고
  활성화(active)** 상태 확인.
- `gainers-daily`: 오늘(2026-08-01) 실행 이력 2건 확인 — 1차는 실패(Groq 전환
  전 코드), 2차(Groq 전환 후, `bc7a8fd`)는 **성공**. `SITE_REPO_PAT`·
  `GROQ_API_KEY` 시크릿이 이미 등록돼 있고 크로스 저장소 푸시까지 실제로
  동작한다는 뜻(사이트 저장소 커밋 `2198f7e` 확인).
- `market-scope-daily`: 실행 이력 **0건** — 크론(20:00 UTC)이 아직 한 번도
  도래하지 않았고 수동 실행도 안 됐기 때문으로 보임(워크플로 자체는
  active). `GEMINI_API_KEY` 시크릿이 실제로 등록돼 있는지는 **아직
  미검증** — 사람이 "Run workflow"(workflow_dispatch)로 한 번 수동 실행해
  확인하는 걸 권장합니다.
- 사이트 저장소의 `market-scope-data.json` 최신 커밋(`7977a23`,
  2026-08-01)은 이 GH Actions 워크플로가 아니라 이전 파이프라인(Cowork
  Scheduled Task 등)에서 온 것으로 추정됩니다(워크플로 실행 이력 0건과
  모순되지 않음 - 저장소 분리 이전 마지막 로컬/Cowork 실행 결과).

### 8-2. 하드코딩 JSON → Supabase 직접 저장 전환 (회장님 지시)

"리서치 액션이 완료돼서 등록하는 리포트 자료는 더 이상 만조사이트에
하드코딩하지 말고 Supabase 데이터베이스에 저장해달라"는 지시에 따라, 두
파이프라인 모두 **사이트 저장소에 JSON을 git push하던 방식을 완전히
폐기**하고 Supabase에 직접 upsert하도록 바꿨습니다.

**발견한 기존 자산**: 사이트 저장소에 이미 `daily_gainers` Supabase
테이블과 이를 읽는 `api/top-gainers.js`(공개 anon key 사용, RLS로 쓰기
차단)가 존재했습니다(원래는 별도의 키움 API 기반 Vercel Cron
`api/cron-update-gainers.js` 전용이었고, 2026-07-12 이후 갱신이 멈춘
상태였음 - §0 참고). 이 표의 컬럼 구성(`ohlcv`, `technicals`, `news`,
`rise_reason`, `chart_analysis`)이 `collect_gainers.py`가 생성하는 데이터와
거의 그대로 일치해, 새 표를 만드는 대신 이 표를 확장해 재사용했습니다.

**변경 파일**:
- `db/002_daily_gainers_weekly_and_comments.sql`(신규) — `daily_gainers`에
  `report_type`(`daily`/`weekly`)·`week_start`·`week_end` 컬럼 추가, 유일키를
  `(trade_date, rank)`에서 `(trade_date, rank, report_type)`으로 교체(주간
  리포트도 같은 표에 저장하기 위함). `rise_reason`/`chart_analysis`/`news`가
  이제 자동 생성됨을 코멘트로 갱신.
- `db/003_volume_stocks.sql`(신규) — 거래대금 상위 10위 전용 표(신규,
  RLS: 공개 읽기·서버만 쓰기).
- `db/004_market_scope_reports.sql`(신규) — 마켓 스코프 리포트 표(신규,
  날짜별 1행 upsert, RLS: 공개 읽기·서버만 쓰기).
  ⚠ **이 세 SQL 파일은 사람이 Supabase 대시보드 SQL Editor에서 직접
  실행해야 적용됩니다** — 스크립트가 DDL을 자동 실행하지 않습니다.
- `scripts/collect_gainers.py` — JSON 파일 읽기/쓰기 + `git_push()` 제거,
  `supabase_upsert()`로 `daily_gainers`·`volume_stocks`에 upsert하도록 교체.
- `scripts/collect_market_scope.py` — 동일하게 JSON + `git_push()` 제거,
  `market_scope_reports`에 upsert.
- `.github/workflows/gainers-daily.yml`,
  `.github/workflows/market-scope-daily.yml` — 사이트 저장소 체크아웃·커밋·
  푸시 스텝 전부 제거(더 이상 필요 없음). 대신 `SUPABASE_URL`·
  `SUPABASE_SERVICE_ROLE_KEY`를 시크릿 env로 추가. **`SITE_REPO_PAT`는 더
  이상 필요하지 않습니다**(비활성화하거나 삭제해도 됨 - 사람 확인 필요,
  다른 용도로 쓰고 있지 않은지 먼저 확인할 것).
- 사이트 저장소 `api/top-gainers.js` — `volume_stocks` 테이블도 함께
  조회해 `dates[날짜].volumeStocks`로 병합 반환하도록 확장(기존에는
  gainers만 DB에서 읽고 volumeStocks는 JSON 폴백이었음).
- 사이트 저장소 `api/market-scope.js`(신규) — `market_scope_reports`를
  읽어 `{current, history}` 형태로 반환(기존 JSON 파일과 같은 응답 모양).
- 사이트 저장소 `api/cron-update-gainers.js` — `daily_gainers` 유일키 변경에
  맞춰 `on_conflict` 파라미터와 upsert payload에 `report_type: 'daily'`
  추가(그대로 두면 유일키 불일치로 upsert가 깨짐 - db/002 마이그레이션과
  함께 적용해야 함).
- 사이트 저장소 `index.html` — `loadStockAnalysis()`가 이제
  `/stock-analysis-data.json` 폴백 없이 `/api/top-gainers` 하나만 호출.
  마켓 스코프 로더도 `market-scope-data.json` 대신 `/api/market-scope` 호출.

**사이트 저장소의 기존 `stock-analysis-data.json`·`market-scope-data.json`
파일 자체는 삭제하지 않고 그대로 남겨뒀습니다**(과거 기록 보관 목적, 이제
어떤 코드도 읽지 않음 - 필요 없다고 판단되면 사람이 삭제해도 안전합니다).

**검증한 것**: 양쪽 저장소 모든 변경 파일 `python -m py_compile` /
`node --check` 통과. GitHub Actions 워크플로 등록 상태를 REST API로 확인.
기존 `api/top-gainers.js`가 이미 실사용 중인 Supabase 프로젝트·anon key를
그대로 재사용해 새 자격증명이 필요 없음을 확인.

**검증 못한 것 (사람이 해야 할 일)**:
1. 위 `db/002~004` SQL 3개를 Supabase 대시보드 SQL Editor에서 순서대로
   실행 — 아직 실행 안 됨(이 세션에는 Supabase 대시보드 접근 권한 없음).
2. 이 저장소(`manzo-research-automation`)의 GitHub Secrets에
   `SUPABASE_URL`·`SUPABASE_SERVICE_ROLE_KEY` 등록 — 사이트 저장소
   `.env.local`에 있는 값과 동일해야 함(현재 미등록으로 추정, 등록 안 하면
   다음 자동 실행이 실패함).
3. SQL 마이그레이션 적용 후 `workflow_dispatch`로 두 워크플로 모두 수동
   1회 실행해 실제로 Supabase에 행이 쌓이는지, 사이트가 그 데이터를
   렌더링하는지 브라우저로 최종 확인.
4. `market-scope-daily` 워크플로가 `GEMINI_API_KEY` 시크릿 부재로 실패하지
   않는지 확인(§8-1 참고, 아직 한 번도 실행된 적 없어 미검증).
5. `api/cron-update-gainers.js`를 구동하는 Vercel Cron이 여전히 필요한지
   판단 — 이제 `collect_gainers.py` 파이프라인이 더 풍부한 데이터(뉴스·
   상승이유·차트분석 포함)로 같은 표를 채우므로, 중복 실행 방지 차원에서
   Vercel Cron을 끄는 걸 권장합니다(AUTOMATION_NOTES §5-3과 같은 이유).

### 8-3. 종단 검증 완료 (2026-08-01, 같은 세션)

`gh` CLI(GitHub 로그인 완료)와 Supabase CLI(Personal Access Token으로 인증)를
이 환경에 연동해, 위 8-2의 "검증 못한 것" 1~3번까지 사람 개입 없이 전부
완료했습니다.

1. `db/002~004_*.sql`을 `supabase db query --linked --file`로 순서대로 실행 →
   `information_schema`로 세 테이블·컬럼 존재 확인.
2. `gh secret set`으로 `SUPABASE_URL`·`SUPABASE_SERVICE_ROLE_KEY`를 이
   저장소 GitHub Secrets에 등록(사이트 저장소 `.env.local`과 동일 값 사용).
3. 코드를 커밋·푸시(리서치자동화 `aba32f7`, 사이트 `4e91028`) 후
   `gh workflow run`으로 `gainers-daily` 재실행 → **성공, `daily_gainers`에
   2026-08-01 weekly 리포트 7종목이 `rise_reason` 채워진 채로 실제 upsert됨**
   (Groq 분석까지 정상 동작 확인). `market-scope-daily`는 2026-08-01이
   KRX 개장일이 아니라 정상 스킵되므로, 대신 로컬에서
   `python scripts/collect_market_scope.py --date 2026-07-31`로 백필 실행 →
   `market_scope_reports`에 385개 메시지·15개 항목 upsert 확인.
4. 사이트(Vercel)의 `/api/top-gainers`·`/api/market-scope`를 실제로 호출해
   최신 데이터가 그대로 내려오는 것까지 확인(`latestDate: 2026-08-01`,
   gainers 7건·volumeStocks 10건 / market-scope `report_date: 2026-07-31`,
   15건).

### 8-6. 근본 버그 수정: 주간 상승률이 '오늘'을 5제곱 복리 계산하던 문제

8/1 표시를 7/16과 맞춰달라는 요청을 처리하다가 `get_weekly_top10()`의 심각한
버그를 발견했습니다. 이 함수는 "그 주 5거래일을 각각 조회"한다고 되어
있었지만, 실제로는 매 반복마다 네이버의 **실시간(현재) 상승률 페이지**만
다시 긁고 있었습니다 - 이 페이지는 과거 날짜 조회가 불가능한 실시간 전용
페이지라서, 결과적으로 "오늘 하루치 등락률을 거래일 수만큼 거듭제곱"하는
것과 동일해졌습니다. 실제로 8/1 주간 리포트의 두산 +271.29%는
`1.30^5 - 1 = 2.71293`(하루 +30%를 5제곱한 값)과 정확히 일치해 확인했습니다.
top_n을 40→100으로 늘려도 "그 주 실제로 다른 종목이 올랐던 것"은 반영되지
않으므로 해결이 안 됐고(같은 종목 목록만 반복 조회), 재시도 중 Groq 무료
할당량까지 소진돼 실패했습니다(안전 장치로 기존 데이터는 보존됨).

**근본 수정**: `db/006_raw_top_candidates.sql`(신규) + `save_raw_candidates()`로
평일 `run_daily()` 실행마다 그날의 원본 후보(KOSPI+KOSDAQ 상위 100씩)를
저장하고, `get_weekly_top10()`은 이제 실시간 재조회 대신
`fetch_weekly_candidates_from_db()`로 그 주 실제 저장된 일별 등락률을 읽어
복리 계산합니다. `save_raw_candidates`/`fetch_weekly_candidates_from_db`를
직접 호출해 정상 동작(단일 날짜 조회 시 복리 없이 그날 값 그대로 반환)까지
확인했습니다.

**중요 - 과거 주는 소급 불가**: 이 주(7/27~7/31)를 포함해 이 커밋 이전
주간들은 raw_top_candidates에 데이터가 없어 정확히 재계산할 방법이
없습니다(네이버가 과거 날짜 조회를 지원하지 않아 원본 자체가 세상에
안 남아있음). 8/1의 "7개, 부정확한 %" 리포트는 그대로 두고, **다음 주간
발행일부터** 정확한 값이 나옵니다. 회장님께 이 사실을 명시적으로 안내하고
진행 방식(다음 개장일 당일 데이터로 대체 vs 매일 저장해 다음 주부터 정확한
주간 집계)을 여쭤본 뒤, 후자로 결정해 구현했습니다.

**남은 사람 확인 사항은 이제 딱 하나**: `market-scope-daily` 워크플로가
실제 평일 크론(20:00 UTC)으로 자동 실행될 때 `GEMINI_API_KEY` 시크릿이
정상 동작하는지 — 로컬 백필로 스크립트 로직 자체는 검증됐지만, GitHub
Actions 환경에서 아직 자연 발화(크론)로 실행된 적은 없습니다.

### 8-4. 회귀 발견 및 수정: 과거 JSON 데이터가 Supabase로 안 옮겨져 있던 문제

§8-2에서 "앞으로 새로 쓰는 데이터"만 Supabase로 보내도록 파이프라인을
바꾸고 사이트의 JSON 폴백을 제거했는데, **사이트 저장소에 이미 커밋돼
있던 과거 날짜들(가장 최근 것 빼고)을 Supabase로 옮기는 걸 빠뜨렸습니다.**
그 결과 전환 직후 사이트에서 날짜 탭이 2026-08-01·2026-07-12(옛 키움
파이프라인이 채워둔 것) 두 개만 남는 회귀가 있었습니다. 회장님이 "사이트에
있던 데이터는 왜 안 들어가 있지?"라고 지적해 주셔서 바로 발견·수정했습니다.

- `scripts/backfill_json_to_supabase.py`(신규, 1회성) — `stock-analysis-data.json`의
  14개 날짜(gainers 127행 + volumeStocks 140행)와 `market-scope-data.json`의
  31개 날짜를 읽어 각각 `daily_gainers`/`volume_stocks`/`market_scope_reports`에
  upsert. 이미 있는 (trade_date, rank, report_type)/(report_date)는 덮어써도
  안전(재실행 가능한 멱등 스크립트).
- 실행 후 실측 확인: `/api/top-gainers`에 날짜 15개(과거 14개 + 오늘) 모두
  gainers·volumeStocks 정상 반환, `/api/market-scope`에 current + history
  29개(최근 30개 제한 내에서 정상 - 가장 오래된 06-22 1개만 이 제한 밖으로
  빠짐, API의 `limit=30` 설계상 의도된 동작).

### 8-5. 회귀 2차 발견 및 수정: 거래대금 표의 순매수·전일 순위/대비 누락

§8-2에서 `volume_stocks` 테이블을 설계할 때 옛 JSON의 `rank/ticker/name/
close/changePct/tradeAmount/naverUrl`만 옮기고, `investors`(개인/기관/외국인
순매수)·`prevRank`(전일 순위)·`priceChange`(전일비)·`prevTradeAmount`(전일
거래대금) 4개 필드를 스키마·백필·API 어디에도 넣지 않아, 사이트의 거래대금
표에서 이 값들이 전부 사라지는 회귀가 있었습니다. 회장님이 "개인/기관/
외국인 순매수 테이블을 만들어 놓았는데 안 보인다"고 지적해 발견했습니다.

- `db/005_volume_stocks_investors_and_prev.sql`(신규) — 4개 컬럼 추가.
- `scripts/backfill_json_to_supabase.py` — 옛 JSON에 있던 값(투자자별
  순매수는 110/140행, prevRank는 27/140행, priceChange는 30/140행에만
  원래도 존재)을 함께 이관하도록 수정 후 재실행.
- `scripts/collect_gainers.py`의 `fetch_volume_stocks()` — **이제부터는
  스크립트가 직접 값을 채웁니다**(과거엔 어떤 스크립트도 이 필드를 만들지
  않았고 수동/별도 경로로 채워진 것으로 추정됨):
  - `priceChange`: 네이버 거래대금 페이지의 전일비 셀(`em.bu_pup`/`bu_pdn`
    클래스로 부호 판별) 파싱.
  - `investors`: 네이버 `frgn.naver` 페이지의 최신 거래일 기관·외국인
    순매매량(주) × 종가로 근사 금액 계산, 개인은 -(기관+외국인) 역산
    (사이트 UI의 "※ 개인 순매수는 기관+외국인 순매수의 역산값입니다"
    안내문과 동일한 방식으로 맞춤).
  - `prevRank`/`prevTradeAmount`: Supabase에서 직전 거래일 `volume_stocks`를
    조회해 같은 티커의 순위·거래대금을 비교.
- 사이트 `api/top-gainers.js` — 4개 필드를 응답에 포함하도록 `toVolumeCard`
  수정.
- **검증한 것**: `fetch_investor_netbuy`/`fetch_prev_volume_stocks`를
  실제로 호출해 정상 값 반환 확인, 백필 후 `/api/top-gainers` 실제 응답에
  `investors` 값이 채워져 나오는 것 확인(2026-07-16 삼성전자 기준).
  **검증 못한 것**: `investors` 근사 계산식(주식수 × 종가)의 실제 정확도 -
  사이트 UI 안내문의 근사 방식을 그대로 따랐을 뿐 별도 검증 소스는
  없습니다. 다음 실제 자동 실행 때 값이 상식적인 범위인지 사람이 한 번
  확인해 주세요.
