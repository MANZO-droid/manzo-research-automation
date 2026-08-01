# 만조리서치 Top10 종목 분석 자동화 — 설계도

작성일: 2026-07-25
갱신: 2026-07-25 (실제 코드 확인 후) → 재갱신: 2026-07-25 (`AUTOMATION_NOTES.md`와 `feature/trading-day-automation` 브랜치 커밋 확인 후 — 최초 갱신판이 로컬에서 되돌려져 있던 옛 버전을 기준으로 쓰여 있어 바로잡음)

## 업무
한국 증시 장 마감 기준 당일 주가 상승률 Top10 종목을 리스트업하고, 상승 이유와 차트를 분석해 만조리서치 홈페이지 '당일 상승률 상위 10위' 섹션에 매일 새 데이터로 반영한다. 토요일(또는 연휴 시 정해진 규칙에 따른 첫 휴장일)에는 한 주간 상승률 Top10을 같은 방식으로 같은 섹션에 반영한다.

## 중요: 한 번 발견한 걸 다시 잃어버릴 뻔한 이력

`scripts/collect_gainers.py`, `scripts/collect_market_scope.py`, `api/cron-update-gainers.js`는 한 번
`feature/trading-day-automation` 브랜치에서 휴장일 자동 판별(krx_calendar) 연동까지 끝나 커밋됐었는데,
이후 로컬에서 이 세 파일만 그 연동이 빠진 옛 버전으로 되돌아가 있었습니다(원인 불명 — 사용자도 의도한
게 아니라고 확인). 2026-07-25에 이 되돌림을 발견해 커밋된 상태로 복원했습니다. 아래 내용은 **복원된
정상 상태** 기준입니다. 자세한 배경은 `AUTOMATION_NOTES.md`를 참고하세요.

## 코드로 확인한 실제 구조 (2026-07-25, 복원 후 기준)

실행 중인 자동화는 하나가 아니라 **세 갈래**로 나뉘어 있고, 각 갈래마다 트리거가 이중으로 걸려 있는
곳도 있습니다.

| 갈래 | 트리거 | 데이터 소스 | 저장 위치 |
| --- | --- | --- | --- |
| A. 상승률/거래대금 Top10 + 종목분석 | **GitHub Actions**(`.github/workflows/gainers-daily.yml`, 매일 07:00 UTC=16:00 KST, 인자 없이 실행 → `krx_calendar.get_weekly_report_trigger()`가 개장일 여부·daily/weekly를 자동 판단) — 원래 있던 **Windows 작업 스케줄러**(`scripts/setup_scheduler.ps1`, 평일/토요일 16:00)는 중복 실행 방지를 위해 꺼야 함(아직 안 끔, 확인 필요) | 네이버 증권(상승률·거래대금 크롤링), 네이버 fchart(OHLCV), 네이버 금융 뉴스, **Claude**(`claude-opus-5`, 2026-07-27 Gemini 429 할당량 문제로 교체 — `ANTHROPIC_API_KEY` 필요) | `stock-analysis-data.json` (실행 스크립트 안에서 git add/commit/push까지 자동 실행) |
| B. Kiwoom API → Supabase 랭킹 | **꺼짐(2026-07-25)** — 원래 Vercel Cron(`vercel.json`, 매일 07:00 UTC=16:00 KST)이었으나, 키움 "지정단말기(8050)" 인증 실패로 13일 연속 실패해 크론 등록 자체를 껐음(코드는 유지, 커밋 `bde1de2`) | 키움증권 API(`api/_kiwoom.js`) | Supabase `daily_gainers` 테이블(프론트는 `/api/top-gainers`로 읽음) — 2026-07-12 이후 갱신 없음(원인 확인됨, 재개는 지정단말기 해제 후) |
| C. 마켓 스코프(테마·종목 언급) | **GitHub Actions**(`.github/workflows/market-scope-daily.yml`, 개장일에만 실행) — 원래 있던 **Cowork Scheduled Task**(`market-scope-daily-update`, 로컬 새벽 5시)는 중복 실행 방지를 위해 꺼야 함(확인 필요) | 텔레그램 공개 채널 13곳 크롤링, Gemini(이슈 탐지) | `market-scope-data.json` |

index.html은 이 셋을 각각 `/stock-analysis-data.json`, `/api/top-gainers`, `market-scope-data.json`으로 fetch해서 화면에 채웁니다.

## 시작 신호
- A(Top10+분석): **GitHub Actions**가 매일 07:00 UTC 호출하고, 실제 발행 여부·daily/weekly 모드는 `krx_calendar.get_weekly_report_trigger()`가 KRX 휴장일 캘린더(`krx-holidays-2026.json`) 기준으로 자동 판단 — 확인된 사실 (`AUTOMATION_NOTES.md` §1, §3에 날짜별 테스트 케이스로 검증됨)
- "토요일과 이어진 앞선 요일이 휴일인 연휴면 평일 다음 첫 휴일 기준" 규칙 — **이미 구현·검증됨**. "토요일 → 그 전 금요일이 휴장이면 금요일 당일 발행, 목·금·토 연속 휴장이면 목요일(연휴 시작일) 발행"으로 해석해 구현. 다만 이 해석은 원 스펙 문장이 다소 모순돼 보여 사람이 판단해 정한 것이라 — **확인 필요**: 이 해석이 실제 의도와 맞는지 한 번 확인
- B(Kiwoom→Supabase): **2026-07-25부로 크론 꺼짐** — 키움 지정단말기 인증 실패로 13일 연속 실패해 `vercel.json`에서 `crons` 항목을 제거함(코드는 유지). 지정단말기 해제 후 재등록 필요
- C(마켓 스코프): GitHub Actions, 개장일에만 — 확인된 사실

## 입력자료
- Top10(A): 네이버 증권 상승률/거래대금 페이지 크롤링, 네이버 fchart(OHLCV 120일) — 확인된 사실
- Top10(B): 키움증권 API — 확인된 사실 (연동은 됐으나 지정단말기 인증 문제로 2026-07-12 이후 호출 안 됨, 2026-07-25부로 크론 자체를 꺼둠)
- 마켓 스코프(C): 텔레그램 공개 채널 13곳(`moneythemestock` 등) 크롤링 — 확인된 사실
- 상승 이유: 종목당 네이버 뉴스 최대 15개 수집 후 **Claude**(`claude-opus-5`) 분석 — **2026-07-27부로 Gemini에서 교체함**(Gemini 무료 할당량이 market-scope(C)와 공유돼 자주 소진됨). **뉴스가 0건이면 분석 자체를 생략**(`riseReason`에 "뉴스를 수집하지 못했습니다" 문구만 남기고 `chartAnalysis`는 빈 값) — 프롬프트 자체는 그대로 유지했으므로 뉴스 없다고 추정해서 지어내는 동작은 여전히 없음. Claude 실제 호출로는 아직 검증 못함(`ANTHROPIC_API_KEY` 미등록 — 확인 필요)
- API 키 보관 위치: `.env.local`(로컬 실행용, Git에 커밋 안 됨) + **GitHub Secrets**(GitHub Actions 실행용) — **확인 완료(2026-07-25)**: `GEMINI_API_KEY`가 Repository secret으로 이미 등록돼 있음(market-scope(C)에서 계속 사용). **확인 필요(2026-07-27 추가)**: `ANTHROPIC_API_KEY`는 아직 `.env.local`·GitHub Secrets 어디에도 없음 — gainers(A)를 실제로 돌리려면 사람이 직접 등록해야 함

## 현재 단계
A(collect_gainers.py, GitHub Actions)는 코드가 완성돼 있고 git push까지 자동 실행됨. B(Kiwoom→Supabase)는 지정단말기 인증 문제로 2026-07-25부로 크론을 꺼둔 상태(재개는 사람이 키움증권에 해제 요청한 뒤). C(마켓 스코프)는 GitHub Actions로 전환됐으나 기존 Cowork 예약 작업과 중복 실행 가능성이 있음.

## AI 역할 — 설계와 실제 코드가 다른 점 (복원 후 기준)

| 설계(로드맵 1강) | 실제 코드(`collect_gainers.py`) |
| --- | --- |
| 우선주·관리종목·ETF·정리매매 종목 자동 제외 | **부분 구현(2026-07-26)** — `classify_excluded()`를 추가해 `get_daily_top10()`/`get_weekly_top10()`에 적용. 우선주(종목명 패턴)·ETF(네이버 ETF 목록 API)·ETN(종목명에 "ETN" 포함)은 실제 라이브 크롤링으로 검증됨(2026-07-24 실행에서 우선주 3건·ETF 5건·ETN 25건·개별종목 선물연계 파생상품 1건 실제로 제외됨). **관리종목·정리매매는 여전히 미구현** — 공식 KRX 관리종목현황 API가 로그인(KRX Data Marketplace 계정, `KRX_ID`/`KRX_PW`)을 요구해서 막힘. 회장님이 직접 계정을 만들기로 결정(2026-07-26) — 계정 발급 후 `classify_excluded()`에 조건 추가 필요 |
| 필터링 후 Top10 10개 미만이면 중단 | **미구현** — 몇 개가 모이든 그대로 진행 |
| 상승 이유/차트 분량 250자 이상 | 실제 프롬프트 기준(2026-07-27부로 Claude가 실행, 프롬프트 문구는 그대로)은 riseReason 200자 이상, chartAnalysis 150자 이상 (250자 기준과 다름) |
| 게시까지 자동, 사후 검토 | 확인된 사실 — `git_push()`가 스크립트 안에서 조건 없이 자동 실행됨. 사람이 막는 지점(게이트)은 코드에 없음 |
| Gemini 토큰 소진 시 중단 | **다르게 동작** — 429 오류 시 재시도(대기 시간은 오류 메시지 파싱, 최대 누적 300초). 그래도 실패하면 중단이 아니라 빈 문자열로 계속 진행 |
| `financials`(재무 정보) | 코드는 항상 빈 값(`{}`)으로 저장하지만, 실제 `stock-analysis-data.json`(예: 2026-07-16자)에는 매출·영업이익 등 실제 수치가 채워져 있음 — **확인 필요**: 누가/어떤 절차로 이 값을 채우는지 |
| `chartAnalysis`의 수치 근거 | **수정함(2026-07-25)** — 원래 `analyze_stock()`의 Gemini 프롬프트에 실제 `technicals` 값이 전달되지 않아, 전체 120건 중 크로스 언급 10건 중 7건이 실제 `technicals.cross`와 다르게 서술됨(예: 실제 데드크로스인데 골든크로스로 서술). 프롬프트에 실제 수치를 전달하고 "크로스 없음이면 지어내지 말라"는 지시를 추가함. 실제 Gemini 재실행으로는 아직 검증 못함 — `tests/manzo-fixes-2026-07-25.md` 참고 |
| 뉴스 `summary` 필드 | **수정함(2026-07-25)** — 원인은 네이버가 `news_read.naver` 주소를 JS 리다이렉트로 바꿔서 실제 기사 페이지(`n.news.naver.com/mnews/article/...`)를 못 읽고 있었기 때문. `fetch_article_summary()`가 이 리다이렉트를 따라가도록 고쳐 실제 URL로 재검증 완료(빈 문자열 → 실제 기사 186자) |
| 기사 있는데도 근거 없는 사실 서술 | **완화(2026-07-25)** — 실제 사례(기가레인 2026-07-13)에서 수집된 기사 5건에 없는 "조회공시" 언급이 riseReason에 등장. 프롬프트에 "기사에 없는 사건(공시 종류·계약 상대방·날짜·금액)은 지어내지 말라"는 지시를 추가. (뉴스 0건일 때 지어내는 문제는 위 "입력자료" 항목대로 애초에 분석을 생략하므로 복원 후에는 해당 없음) |

## 필요 도구
확인된 사실:
- 네이버 증권/뉴스 크롤링 (`requests` + `BeautifulSoup`)
- Claude API (`anthropic`, 모델 `claude-opus-5`) — gainers(A)의 상승 이유·차트분석용, 2026-07-27부로 Gemini에서 교체
- Gemini API (`google-genai`, 모델 `gemini-2.0-flash`) — market-scope(C)에서 계속 사용 중
- 키움증권 API 연동 코드 존재 (`api/_kiwoom.js`)
- 거래일 판단: `lib/krx-calendar.js`(JS) + `scripts/krx_calendar.py`(Python), 둘 다 `krx-holidays-2026.json`을 읽음
- 스케줄 실행: **GitHub Actions**(A, C) + Vercel Cron(B)
- 작업 폴더: `E:\AI 스터디\만조그룹 2차`

확인 필요 (`AUTOMATION_NOTES.md` §5 "사람이 해야 할 일" 그대로 옮김):
1. ~~GitHub Secrets에 `GEMINI_API_KEY` 등록 여부 확인~~ — **확인 완료(2026-07-25)**: GitHub 저장소 Settings → Secrets and variables → Actions에 `GEMINI_API_KEY`가 Repository secret으로 등록돼 있음(등록일 기준 5일 전, 즉 이 저장소 작업보다 먼저 등록됨). GitHub Actions 실행 자체가 이 이유로 실패하지는 않을 것으로 확인
2. ~~Vercel Cron(B)이 2026-07-12부터 멈춘 원인 확인~~ — **원인 확인 완료(2026-07-25)**: 커밋 `bde1de2`(같은 날, origin/main에 이미 푸시·배포됨)에서 확인. 키움 "지정단말기(8050)" 인증 실패로 13일 연속 크론이 실패하고 있었고, 문제가 풀릴 때까지 `vercel.json`의 `crons` 항목 자체를 껐음(코드는 남겨둠). **다음 조치**: 사람이 키움증권(1544-9000)에 지정단말기(내PC지정) 서비스 해제를 요청 → 해제되면 `vercel.json`에 `crons` 항목을 다시 추가해 재개
3. GitHub Actions 정상 작동 확인 후 **Windows 작업 스케줄러**와 **Cowork Scheduled Task(market-scope-daily-update)**를 꺼서 중복 실행 방지
4. `krx-holidays-2026.json`을 한국거래소(KRX) 공식 2026년 휴장일 공지와 최종 대조
5. 매년 초 `krx-holidays-2027.json` 등 다음 해 휴장일 파일 추가 필요(자동화 안 돼 있음, 안 하면 평일 휴장일이 조용히 누락됨)
6. `scripts/audit_volume_gaps.py`를 실제 프로덕션 데이터로 재실행해 `volumeStocks` 백필 필요 날짜 확인
7. **(2026-07-27 추가)** `ANTHROPIC_API_KEY`를 `.env.local`(로컬)과 GitHub Secrets(Actions)에 등록 — 아직 안 됨. 등록 전까지 gainers(A)는 "[오류] ANTHROPIC_API_KEY가 없습니다"로 즉시 중단됨. 등록 후 실제 Claude 호출 1회로 재검증 필요
- 뉴스 크롤링 대상 사이트의 이용약관/저작권 관련 확인 여부

## 사람 검토
확인된 사실(설계 의도): 검토 시점은 최종 게시(배포) 이후 사후 검토, 검토 항목은 상승 이유 분석의 사실관계·분량·종목 필터링 누락.

확인 필요: 코드에는 이 사후 검토를 강제하거나 알려주는 장치(알림 등)가 없음. 지금은 사용자가 스스로 사이트를 확인하러 가야 함 — 실패 알림 채널을 정할지 결정 필요(로드맵에도 "아직 미정"으로 남아있음).

제안: 뉴스 콘텐츠의 출처 표기·저작권 이슈도 사후 검토 항목에 포함할지는 별도 검토 권장.

## 결과물
확인된 사실: 일간·주간 결과 모두 `stock-analysis-data.json`의 같은 `dates[날짜]` 구조에 들어가고(주간은 `type: "weekly"` 필드로 구분), index.html의 같은 '당일 상승률 상위 10위' 섹션에서 렌더링됨. 날짜별로 upsert되므로 이전 날짜 데이터는 덮어써지지 않고 쌓임.

## 성공 기준
설계 의도(확인된 사실):
- 성공: 사람 개입·수정 없이 10개 종목 정리가 완벽하게 게시까지 완료
- 중단(사람 개입 필요) 조건: 제미나이 토큰 소진, API 오류, 필터링 후 종목 10개 미만, 권한 설정이 필요한 경우

확인 필요: 위 중단 조건 중 "제미나이 토큰 소진"과 "필터링 후 10개 미만"은 실제 코드 동작과 다름(위 표 참고) — 설계대로 코드를 고칠지, 아니면 지금 코드 동작에 맞게 성공 기준을 다시 쓸지 결정 필요.

## 다음 최소 단위
Top10 필터링 로직(우선주·관리종목·ETF·정리매매 제외)을 `get_daily_top10()`에 실제로 추가하는 것이 아직 안 끝난 가장 작은 단위 작업. 그 전에 위 "필요 도구 — 확인 필요" 1~3번(GitHub Secrets 등록, Vercel Cron 원인, 중복 스케줄 끄기)부터 먼저 정리하는 걸 권장.

---
※ 이 문서는 설계도이며, 위 "코드로 확인한 실제 구조" 절 이외의 실제 실행은 각 스크립트/함수 파일에서 이뤄진다. 더 자세한 배경·검증 근거는 `AUTOMATION_NOTES.md`를 참고할 것.
