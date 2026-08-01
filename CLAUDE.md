# CLAUDE.md

이 저장소(`E:\AI 스터디\리서치자동화\`)는 만조리서치 사이트에 반영할 데이터를 만들어내는 자동화 파이프라인이며, "AI 업무자동화" 강의의 개인 실습 공간이기도 합니다.

## 두 저장소 구조 (2026-08-01 분리)

원래 사이트 저장소(`만조그룹 2차` = GitHub `manzo-site`) 안에 `리서치자동화/` 하위 폴더로 있었으나, 완전히 분리된 **독립 git 저장소**가 됐습니다. 형제 폴더 `E:\AI 스터디\만조그룹 2차\`가 사이트 저장소입니다.

- 이 저장소는 결과 JSON을 **갖고 있지 않습니다.** `scripts/*.py`가 사이트 저장소의 `stock-analysis-data.json`·`market-scope-data.json`을 직접 쓰고 `git add/commit/push`합니다.
- 로컬 실행 시 스크립트는 `../만조그룹 2차`를 형제 폴더로 자동 가정합니다.
- GitHub Actions([.github/workflows/](.github/workflows/))는 사이트 저장소를 `site-repo/`로 추가 체크아웃하고, `SITE_REPO_PATH` 환경변수로 위치를 스크립트에 넘깁니다. 이 크로스 저장소 체크아웃·푸시에는 사이트 저장소에 쓰기 권한이 있는 PAT가 필요하며, 이 저장소의 GitHub Secrets에 `SITE_REPO_PAT`라는 이름으로 등록돼 있어야 합니다.
- 이 저장소를 고칠 때 사이트 코드(`index.html` 등)는 건드리지 않습니다 — 그건 사이트 저장소의 몫입니다.

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
8. 외부 발송·삭제·결제·예약·권한 변경은 직전 명시적 승인 없이는 하지 않습니다. **사이트 저장소로의 크로스 푸시는 이미 승인된 자동화 흐름의 일부이므로 예외입니다** — 단, `git_push()`가 실제로 무엇을 커밋하는지는 항상 로그로 확인 가능해야 합니다.
9. 증거 없는 로드맵 단계를 완료로 표시하지 않습니다.
10. "입·출력 규격 검증을 진행해줘" 또는 "목 입력을 만들어줘" 요청을 받으면 `.claude/skills/`·`.agents/skills/`의 `semiclass-input-output-spec-review`/`semiclass-mock-input-generator` SKILL.md를 적용합니다.
11. 확정된 규칙은 [reference/policies/confirmed-rules.md](reference/policies/confirmed-rules.md), 아직 확정 전인 질문·후보는 [.automation/intake.json](.automation/intake.json)의 `open_questions`에 있습니다. `design/roadmap.yaml`의 `stages` 순서(design → prepare → first_run → verify → operate)를 따릅니다.

전체 안내는 [README.md](README.md)를 확인하세요.
