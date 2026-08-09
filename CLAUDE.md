# CLAUDE.md

이 저장소(`E:\AI 스터디\리서치자동화\`)는 만조리서치 사이트에 반영할 데이터를 만들어내는 자동화 파이프라인이며, "AI 업무자동화" 강의의 개인 실습 공간이기도 합니다.

## 두 저장소 구조 (2026-08-01 분리)

원래 사이트 저장소(`만조인베스트 웹페이지 관리` = GitHub `manzo-site`) 안에 `리서치자동화/` 하위 폴더로 있었으나, 완전히 분리된 **독립 git 저장소**가 됐습니다. 형제 폴더 `E:\AI 스터디\만조인베스트 웹페이지 관리\`가 사이트 저장소입니다.

- **2026-08-01(2차) — 결과물을 사이트 저장소에 JSON으로 커밋하던 방식을 폐기하고 Supabase로 직접 저장합니다.** `scripts/collect_gainers.py`는 `daily_gainers`·`volume_stocks` 테이블에, `scripts/collect_market_scope.py`는 `market_scope_reports` 테이블에 REST API(`SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY`)로 upsert합니다. 더 이상 `stock-analysis-data.json`·`market-scope-data.json`을 쓰지 않고, 사이트 저장소를 체크아웃·커밋·푸시할 필요도 없습니다(`SITE_REPO_PAT` 시크릿도 더 이상 필요 없음).
- 이 저장소는 여전히 사이트 저장소를 직접 갖고 있지 않습니다 — `db/*.sql`은 Supabase 대시보드 SQL Editor에 사람이 직접 실행하는 스키마 마이그레이션 파일입니다(스크립트가 DDL을 실행하지 않음).
- 로컬 실행 시 `.env.local`에 `SUPABASE_URL`·`SUPABASE_SERVICE_ROLE_KEY`가 필요합니다(사이트 저장소 `.env.local`과 같은 프로젝트 값).
- 사이트(`../만조인베스트 웹페이지 관리/index.html`, `api/top-gainers.js`, `api/market-scope.js`)는 이 Supabase 테이블을 읽기 전용(anon key)으로 조회해 렌더링합니다. **원칙적으로 사이트 코드는 건드리지 않지만, "하드코딩 제거"처럼 이 저장소의 저장 방식 변경이 사이트의 읽기 코드에도 영향을 주는 경우는 예외**입니다(2026-08-01 Supabase 전환 때 `index.html`·`api/top-gainers.js`·`api/market-scope.js`를 함께 수정함).
- **2026-08-02**: 사이트 쪽에 남아있던 자체 Supabase 쓰기 코드(`api/cron-update-gainers.js`, 키움 API 직접 호출로 `daily_gainers`에 중복으로 쓰던 것)를 완전히 삭제했습니다. 이제 세 테이블 모두 이 저장소(`collect_gainers.py`/`collect_market_scope.py`)만 씁니다 — 사이트는 어떤 경우에도 Supabase에 쓰지 않습니다.

## 작업 원칙

1. 작업 전 `design/automation.yaml`과 `design/roadmap.yaml`을 읽습니다.
2. 샘플·강의 자료(`.automation/archive/`)를 실제 업무 사실로 취급하지 않습니다.
3. 실제 입력(`input/`), 참고자료(`reference/`), 결과를 서로 섞지 않습니다.
4. `input/` 원본을 수정하지 않습니다.
5. 대시보드 파생 파일(`dashboard.html`, `.automation/dashboard.json`, `.automation/file-index.json`)은 직접 꾸며 쓰지 않고, 아래 명령으로 원본에서 다시 만듭니다.

   ```bash
   node .automation/dashboard/refresh-dashboard.mjs .
   ```

6. 중요한 상태 변화는 아래 명령으로 append-only 이벤트 로그에 기록합니다.

   ```bash
   node .automation/dashboard/record-event.mjs . --type=<event-type> --message="<요약>"
   ```

7. 대화에서 자동화 설계나 상태가 바뀌면 `design/automation.yaml`·`design/roadmap.yaml`을 먼저 갱신하고, 응답 전에 대시보드를 다시 만듭니다.
8. 외부 발송·삭제·결제·예약·권한 변경은 직전 명시적 승인 없이는 하지 않습니다. **Supabase에 대한 upsert(daily_gainers/volume_stocks/market_scope_reports)는 이미 승인된 자동화 흐름의 일부이므로 예외입니다** — 단, 무엇을 upsert했는지는 항상 로그로 확인 가능해야 합니다. 스키마 변경(DDL, `db/*.sql`)은 스크립트가 자동 실행하지 않고 사람이 Supabase SQL Editor에서 직접 실행합니다.
9. 증거 없는 로드맵 단계를 완료로 표시하지 않습니다.
10. "입·출력 규격 검증을 진행해줘" 또는 "목 입력을 만들어줘" 요청을 받으면 `.claude/skills/`·`.agents/skills/`의 `semiclass-input-output-spec-review`/`semiclass-mock-input-generator` SKILL.md를 적용합니다.
11. 확정된 규칙은 [reference/policies/confirmed-rules.md](reference/policies/confirmed-rules.md), 아직 확정 전인 질문·후보는 [.automation/intake.json](.automation/intake.json)의 `open_questions`에 있습니다. `design/roadmap.yaml`의 `stages` 순서(design → prepare → first_run → verify → operate)를 따릅니다.

전체 안내는 [README.md](README.md)를 확인하세요.
