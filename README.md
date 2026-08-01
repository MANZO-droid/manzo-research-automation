# 업무자동화 학습 공간 — 만조리서치 Top10 종목 분석 자동화

이 저장소(`E:\AI 스터디\리서치자동화\`)는 만조리서치 사이트에 들어갈 데이터를 만들어내는 파이프라인이자, "AI 업무자동화" 강의의 개인 실습 공간입니다. **독립된 git 저장소**이며, 사이트 자체는 형제 폴더 `E:\AI 스터디\만조그룹 2차\`(GitHub `MANZO-droid/manzo-site`)의 별도 저장소에 있습니다.

이 저장소는 결과 JSON을 직접 갖지 않습니다 — GitHub Actions가 실행될 때 사이트 저장소를 추가로 체크아웃해 그 안에 씁니다(자세한 내용은 [CLAUDE.md](CLAUDE.md) 참고).

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
사이트 저장소(`../만조그룹 2차/`, GitHub `manzo-site`)의 `stock-analysis-data.json`(`dates[날짜].gainers[]` — 상승률·거래대금 두 섹션)과 `market-scope-data.json`(마켓 스코프). 사이트의 `index.html`이 이 파일들을 읽어 그립니다. 출력 규격은 [reference/policies/manzo-output-contract.md](reference/policies/manzo-output-contract.md) 참고.

## 사람이 확인할 곳
최종 게시(배포) 이후 사후 검토 — 상승 이유 분석의 사실관계, 분량, 종목 필터링 누락·오류. `git_push()`가 스크립트 안에서 조건 없이 자동 실행되므로 사전에 막는 지점은 현재 없습니다(확인 필요).

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

로컬에서 사람이 직접 실행할 때는 `../만조그룹 2차` 형제 폴더를 자동으로 찾습니다. GitHub Actions에서는 사이트 저장소를 `site-repo/`에 별도 체크아웃하고 `SITE_REPO_PATH` 환경변수로 위치를 알려줍니다.

```bash
python scripts/collect_gainers.py          # 로컬: ../만조그룹 2차 에 씀
SITE_REPO_PATH=/path/to/site python scripts/collect_gainers.py   # 경로를 직접 지정
```

## 스킬
- `.claude/skills/semiclass-input-output-spec-review/SKILL.md`, `.agents/skills/semiclass-input-output-spec-review/SKILL.md` — "입·출력 규격 검증을 진행해줘"
- `.claude/skills/semiclass-mock-input-generator/SKILL.md`, `.agents/skills/semiclass-mock-input-generator/SKILL.md` — "목 입력을 만들어줘" / "테스트 입력 만들어줘"

## 지난 2강 구형 구조(01-input/, 02-reference/, 03-output/, context/, inbox/, evidence/, knowledge/, progress/, workflow/, tests/, 강의자료 등)
2026-08-01에 `.automation/archive/2026-08-01-lesson02-compaction/legacy-root/`로 숨김 보관했습니다. 그 안의 실제 자료(실제 입력 1건, 실제 출력 규격, 실제 회귀 기록)는 위 `input/`, `reference/policies/`, `design/`으로 이미 옮겨졌고, 강의 공통 가상 샘플(N-01·N-02·N-03·E-01·E-02 등)은 정보 손실 없이 그대로 보관만 되어 있습니다.
