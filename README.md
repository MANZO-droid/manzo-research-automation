# 업무자동화 학습 공간 — 만조리서치 Top10 종목 분석 자동화

이 저장소(`E:\AI 스터디\리서치자동화\`)는 만조리서치 사이트에 들어갈 데이터를 만들어내는 파이프라인이자, "AI 업무자동화" 강의의 개인 실습 공간입니다. **독립된 git 저장소**이며, 사이트 자체는 형제 폴더 `E:\AI 스터디\만조인베스트 웹페이지 관리\`(GitHub `MANZO-droid/manzo-site`)의 별도 저장소에 있습니다.

이 저장소는 결과물을 파일로 갖지 않습니다 — GitHub Actions가 실행될 때 Supabase(daily_gainers·volume_stocks·market_scope_reports 테이블)에 직접 저장합니다(2026-08-01부로 사이트 저장소에 JSON을 커밋하던 방식 폐기, 자세한 내용은 [CLAUDE.md](CLAUDE.md) 참고). **2026-08-02부로 세 테이블 모두 이 저장소만 씁니다** — 사이트 쪽에 남아있던 자체 쓰기 코드(키움 API 직접 호출)는 중복 요인이라 삭제했습니다.

**아래 상대 경로는 모두 이 저장소 안 기준입니다.**

## 이 자동화가 줄이는 일
한국 증시 마감 기준 당일(평일) 또는 주간(토요일·연휴) 상승률 Top10 종목을 자동으로 뽑아 상승 이유·차트를 분석하고, 홈페이지 '당일 상승률 상위 10위' 섹션에 매일 새 데이터로 반영하는 일.

## 시작 조건
GitHub Actions(`.github/workflows/gainers-daily.yml`)가 매일 07:00 UTC(=16:00 KST)에 호출하고, `krx_calendar.get_weekly_report_trigger()`가 오늘 발행 여부와 daily/weekly 모드를 자동 판단합니다.

## 입력
네이버 증권 상승률/거래대금 크롤링, 네이버 fchart OHLCV(120일), 종목당 네이버 뉴스 최대 15개. 실제 예시 1건은 [input/manzo-real-2026-07-13-049080.txt](input/manzo-real-2026-07-13-049080.txt).

## 처리
`design/automation.yaml`의 `process` 8단계(발행 여부 판단 → Top10 선정 → 거래대금 상위 선정 → 기술적 지표 계산 → 뉴스 수집 → 상승 이유/차트 분석 → 결과 저장 → 게시) — 실제로는 `scripts/collect_gainers.py`가 담당합니다.

## 결과
Supabase 프로젝트(사이트 저장소 `.env.local`의 `SUPABASE_URL`과 동일)의 `daily_gainers`·`volume_stocks`·`market_scope_reports` 테이블. 사이트(`../만조인베스트 웹페이지 관리/`)의 `api/top-gainers.js`·`api/market-scope.js`가 이 테이블을 읽어 `index.html`에 그립니다. 스키마는 [db/](db/) 참고. 출력 규격은 [reference/policies/manzo-output-contract.md](reference/policies/manzo-output-contract.md) 참고.

## 사람이 확인할 곳
최종 게시(배포) 이후 사후 검토 — 상승 이유 분석의 사실관계, 분량, 종목 필터링 누락·오류. Supabase upsert가 스크립트 안에서 조건 없이 자동 실행되므로 사전에 막는 지점은 현재 없습니다(확인 필요).

## 현재 로드맵 단계
`design`(설계, 진행 중) — 자세한 근거와 단계별 증거는 [design/roadmap.yaml](design/roadmap.yaml) 참고.

## 다음 행동
관리종목·정리매매 제외 필터링을 `get_daily_top10()`에 추가(KRX 계정 발급 후 `classify_excluded()`에 조건 추가).

## 미결 질문
아직 확정되지 않은 질문은 [.automation/intake.json](.automation/intake.json)의 `open_questions`에 있습니다.

## 대시보드
[dashboard.html](dashboard.html)을 더블클릭하면 위 정보를 화면으로 볼 수 있습니다. 파일이 바뀐 뒤에는 이 저장소 루트에서 아래 명령으로 다시 만듭니다.

```bash
node .automation/dashboard/refresh-dashboard.mjs .
```

## 실행

`.env.local`에 `SUPABASE_URL`·`SUPABASE_SERVICE_ROLE_KEY`가 필요합니다(사이트 저장소 `.env.local`과 같은 값). 로컬·GitHub Actions 모두 동일하게 Supabase REST API로 직접 씁니다 — 더 이상 사이트 저장소를 체크아웃할 필요가 없습니다.

```bash
python scripts/collect_gainers.py          # daily_gainers·volume_stocks에 upsert
python scripts/collect_market_scope.py     # market_scope_reports에 upsert
```

## 스킬
- `.claude/skills/semiclass-input-output-spec-review/SKILL.md`, `.agents/skills/semiclass-input-output-spec-review/SKILL.md` — "입·출력 규격 검증을 진행해줘"
- `.claude/skills/semiclass-mock-input-generator/SKILL.md`, `.agents/skills/semiclass-mock-input-generator/SKILL.md` — "목 입력을 만들어줘" / "테스트 입력 만들어줘"

## 지난 2강 구형 구조(01-input/, 02-reference/, 03-output/, context/, inbox/, evidence/, knowledge/, progress/, workflow/, tests/, 강의자료 등)
2026-08-01에 `.automation/archive/2026-08-01-lesson02-compaction/legacy-root/`로 숨김 보관했습니다. 그 안의 실제 자료(실제 입력 1건, 실제 출력 규격, 실제 회귀 기록)는 위 `input/`, `reference/policies/`, `design/`으로 이미 옮겨졌고, 강의 공통 가상 샘플(N-01·N-02·N-03·E-01·E-02 등)은 정보 손실 없이 그대로 보관만 되어 있습니다.
