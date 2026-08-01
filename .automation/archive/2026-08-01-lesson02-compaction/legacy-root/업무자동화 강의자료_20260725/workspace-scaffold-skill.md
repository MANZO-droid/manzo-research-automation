# 한 번에 보강하는 `업무자동화/` 워크스페이스 설정 프롬프트

**슬라이드 6의 실습에서 바로** 사용합니다. 모든 수강생은 강의 자료의 **2강 시작 패키지 ZIP 하나만**
`업무자동화/강의자료/`에 압축 해제합니다. 1강 수강생은 기존 `업무자동화/`을 쓰고, 2강부터 참여한
수강생은 새 `업무자동화/강의자료/`를 만듭니다. 공통 실습 샘플은 시작 패키지 안에 이미 있습니다. 파일 작업 가능한 에이전트에서
**`업무자동화/` 폴더 자체**를 열고 아래 프롬프트를 한 번 붙여 넣습니다. 수강생은 README, AGENTS,
폴더를 직접 만들거나 채우지 않습니다.

```text
지금 열려 있는 업무자동화/ 폴더에서 1강 설계도와 로드맵을 찾아 읽고, 2강 구조를 **같은 루트에 한 번에 보강해줘.**
계획만 보여 주거나 README 내용을 나에게 질문하지 말고, 강의자료/README.md와
강의자료/workspace-scaffold-skill.md를 먼저 읽은 뒤 아래의 없는 폴더와 파일을 실제로 작성해.

[출발 자료]
- 개인 자료가 있으면 design/automation-blueprint.md와 progress/roadmap.md, progress/roadmap.html
  또는 ROADMAP.md, ROADMAP.html 중 있는 모든 로드맵을 읽어.
- 개인 1강 로드맵 파일(`progress/roadmap.md`, `progress/roadmap.html`, `ROADMAP.md`, `ROADMAP.html`)이
  전혀 없으면 강의자료/common-sample-pack/virtual-lesson-01-roadmap.html을
  progress/roadmap.html로 **그대로 복사**해. virtual-lesson-01-roadmap.md와
  강의자료/common-sample-pack/의 N-01·N-02·output-contract.txt도 읽어. 이 경우 가상 실습이라고 표시하고,
  개인 로드맵 완료 증거로 만들지 마.
- 새 업무를 고르지 말고, 1강 로드맵에서 이미 정한 다음 최소 단위 하나만 2강 V0로 가져와.

[만들 위치]
- 업무자동화/는 1~4강 공용 루트다. series-workspace/, meeting-automation-sample/처럼 새 루트를 만들지 말고,
  현재 루트에 없는 구조만 보강해.
- 강의자료/, 기존 README.md, design/, progress/의 파일을 덮어쓰거나 이동하거나 삭제하지 마. 같은 파일을
  바꿔야 하면 충돌 목록과 최소 수정안만 보고하고 멈춰. 새 폴더에서 파일을 만들 권한이 없으면 만들었다고
  말하지 말고 그 사실만 보고해.

[한 번에 만들 파일]
[업무자동화]/
├── 강의자료/README.md
├── README.md
├── AGENTS.md 또는 CLAUDE.md
├── skills/                                      ← 사람이 읽는 스킬 정본
│   ├── semiclass-input-output-spec-review/SKILL.md
│   └── semiclass-mock-input-generator/SKILL.md
├── .agents/skills/                               ← Codex가 읽는 투영본
│   ├── semiclass-input-output-spec-review/SKILL.md
│   └── semiclass-mock-input-generator/SKILL.md
├── .claude/skills/                               ← Claude Code가 읽는 투영본
│   ├── semiclass-input-output-spec-review/SKILL.md
│   └── semiclass-mock-input-generator/SKILL.md
├── design/automation-blueprint.md
├── progress/roadmap.md
├── progress/roadmap.html
├── context/README.md
├── inbox/lesson-02.md
├── 01-input/
│   └── README.md
├── 02-reference/
│   ├── README.md
│   ├── workspace-file-guide.md
│   ├── 복습요약-보고서-양식.html
│   └── 복습요약-보고서-양식.pdf
├── 03-output/
│   └── README.md
├── workflow/WORKFLOW.md
├── tests/lesson-02-results.md
├── evidence/
└── knowledge/

[작성 규칙]
- README.md에는 설계도에서 확인된 업무, 시작 조건, 입력, 결과, 사람 검토, 다음 최소 단위를 쉬운
  말로 적어. 비어 있는 사실은 확인 필요로 남겨.
- AGENTS.md 또는 CLAUDE.md에는 이 폴더가 수강생의 학습용 자동화 공간임을 적어. 어려운 말 대신
  비유와 작은 예시를 쓰고, 한 번에 다음 행동 하나만 안내하며, progress/roadmap.md 순서를 따르고,
  증거 없이 완료 처리하지 않도록 적어. 또한 `입·출력 규격 검증을 진행해줘` 또는 `목 입력을 만들어줘`라는
  요청을 받으면 아래의 등록된 프로젝트 스킬을 먼저 적용하고, 스킬이 없으면 일반 답변으로 대신 진행하지 말고
  누락 경로를 알려 주도록 적어.
- 아래 두 스킬은 단순 `.md` 작업 지침이 아니다. 강의자료/skill-templates/ 안의 **YAML frontmatter가 첫 줄의
  `---`로 시작하는 `SKILL.md` 원본**을 각각 그대로 사용해. `name`과 `description`은 반드시 YAML frontmatter
  안에 넣어. 먼저 `skills/`에 정본을 만들고, 각 정본과 **내용이 완전히 같은 파일**을 `.agents/skills/`와
  `.claude/skills/`에도 복사해. `agents/openai.yaml`은 만들지 않아도 된다. 새 업무 규칙은 지어내지 말고,
  개인 설계도에 없는 파일 형식·도구·값은 `확인 필요`로 적어.
  1. `skills/semiclass-input-output-spec-review/SKILL.md`, `.agents/skills/semiclass-input-output-spec-review/SKILL.md`,
     `.claude/skills/semiclass-input-output-spec-review/SKILL.md`: 강의자료/skill-templates/semiclass-input-output-spec-review/SKILL.md를
     세 위치에 내용 변경 없이 복사해. 첫 부분은 아래와 같아야 한다.
     ~~~yaml
     ---
     name: semiclass-input-output-spec-review
     description: 1강 로드맵을 바탕으로 자동화 워크플로우의 입·출력 규격, 예외, 중복 입력 처리와 사람 검토 지점을 점검한다. "입·출력 규격 검증", "입출력 규격 검토", "워크플로우 규격을 확인" 요청에 사용한다.
     ---
     ~~~
     그 아래에는 design·roadmap·WORKFLOW·현재 입력·승인된 기준·이전 시험을
     읽고, `트리거 → 허용 입력 → 처리 경계 → 결과물 → 사람 확인 → 완료 증거` 한 줄, 입력 규격 표,
     출력 규격 표, 불일치, 실제 예외 최대 4개를 만든다. 가장 중요한 미결 예외 하나만
     `허용 규칙 추가 / 정보 요청 / 실행 중단 / 사람 검토 대기` 중 무엇으로 할지 묻는다. 같은 원본이
     이미 처리됐으면 중단하고 새 결과·초안·외부 행동·정상 완료 기록을 만들지 않는다. 승인 전에는
     워크플로우를 고치지 않고, 승인 뒤 최소 수정안과 다음 시험 케이스만 제안한다.
  2. `skills/semiclass-mock-input-generator/SKILL.md`, `.agents/skills/semiclass-mock-input-generator/SKILL.md`,
     `.claude/skills/semiclass-mock-input-generator/SKILL.md`: 강의자료/skill-templates/semiclass-mock-input-generator/SKILL.md를
     세 위치에 내용 변경 없이 복사해. 첫 부분은 아래와 같아야 한다.
     ~~~yaml
     ---
     name: semiclass-mock-input-generator
     description: 승인된 자동화 워크플로우의 규격으로 정상·변형·누락·중복 목 입력을 만들고, 입·출력 규격 검증으로 결과 확인을 이어 간다. "목 입력 생성", "테스트 입력 만들어줘", "여러 입력으로 시험" 요청에 사용한다.
     ---
     ~~~
     그 아래에는 승인된 입·출력 규격을 읽고 N-01(정상), N-02(형식 변형),
     E-01(누락·형식 오류), E-02(N-01과 같은 원본의 두 번째 제출)를 현재 워크플로우의 입력 형식으로
     만든다. 각 케이스에 ID, 파일 또는 행 위치, 시험 목적, 적용 규격, 기대 행동, 확인할 출력 의무,
     금지 행동을 기록한다. 모든 값은 가상 값으로 바꾸고, E-02에서는 새 결과·Slack 초안·외부 행동·정상
     완료 기록을 만들지 않는다. 처리한 실제 결과는 입·출력 규격 검증으로 다시 확인하라고 안내한다.
- design/automation-blueprint.md에는 1강 설계도의 확인된 사실을 옮겨. 개인 로드맵이 있으면
  progress/roadmap.md와 progress/roadmap.html에는 같은 단계·상태·증거 경로·사람 확인 지점을 적어.
  개인 로드맵이 전혀 없어 공통 HTML을 복사한 경우에는 그 HTML을 재생성하거나 수정하지 말고,
  가상 실습임을 README.md와 workflow/WORKFLOW.md에만 표시해. 이전 형식의 개인 로드맵에서 증거 없는 완료는
  확인 필요로 둬.
- `01-input/README.md`에는 사람이 직접 넣는 원본 입력만 두고 원본을 고치지 않는다고 적어. `02-reference/README.md`에는
  승인된 기준·양식·참조 자료만 두며, 보고서 양식 HTML은 복사해 채우는 원본이고 PDF는 모양을 확인하는 기준이라고 적어.
  `03-output/README.md`에는 실행마다 만든 보고서 HTML·PDF·ZIP·발송 초안만 두고, 실제 외부 발송은 하지 않는다고 적어.
- 강의자료/report-template/복습요약-보고서-양식.html과 `.pdf`를 **내용을 바꾸지 않고** `02-reference/`로
  복사해. 이 양식은 1강 수료 예시에 넣지 않는다.
- `02-reference/workspace-file-guide.md`를 만들어. 이 문서는 **현재 1강 설계도와 로드맵에서 확인된 사실만
  근거로**, 각 폴더에 어떤 예시 파일이 어떤 상황에 생기는지 표로 안내해야 해. 표 열은
  `폴더 | 파일 이름 또는 이름 규칙 | 생기는 상황 | 무엇을 담는가 | 설계도·로드맵 근거 | 미확정 항목`으로 해.
  최소한 다음 상황을 빠뜨리지 마: 설계도·로드맵을 옮길 때, 승인된 입력·출력 기준을 둘 때, 목 입력 생성
  뒤, 실제 입력을 실행한 뒤, 중복·누락 입력에서 중단했을 때, 사람이 검토한 뒤, 외부 도구 연결을 대기할 때.
  파일 확장자·실제 이름·도구가 설계도에 없으면 지어내지 말고 `확인 필요`로 남겨. 이 안내서는 실제
  입력이나 결과를 대신 만들지 않는다.
- 공통 샘플을 쓰면 강의자료/common-sample-pack/의 N-01·N-02를 01-input/으로, output-contract.txt를
  02-reference/로 복사해. 강의자료/의 원본은 보존해. 개인 경로에서 익명 정상 입력·승인된 기준이 아직 없으면
  각 폴더의 안내에 확인 필요로 남겨.
- 01-input/에는 서로 다른 정상 입력 2건의 위치를, 02-reference/에는 승인된 입력·출력 기준·보고서 양식과 위 안내서를,
  03-output/에는 결과 초안 위치를, tests/lesson-02-results.md에는 정상·예외·중복 시험의 비교와 수정,
  사람 검토를 기록할 자리를 만들어.
- workflow/WORKFLOW.md에는 시작 조건, 입력, 처리 순서, 출력 규격, 사람 검토, 중단 조건을 작성해.
  공통 샘플을 쓰는 경우 당일 Top10 원시 자료에서 Top10 정리·상승 이유 요약·게시 초안까지 만드는
  예시 흐름을 적되, 규격화와 비교를 위한 가상 실습이라고 표시해. 개인 자동화에서는 1강 설계도의 입력과
  표준 산출물 정의를 우선해.
- context/README.md에는 현재 적용 중인 확정 규칙과 참조 위치만, inbox/lesson-02.md에는 오늘 새로 확인할
  규칙·예외·질문 후보를 근거와 함께 적을 빈 표를 만들어.
- 실제 커넥터 연결이나 외부 쓰기는 하지 마. Slack·Gmail·Google Drive·Notion·스프레드시트 중 필요한
  도착지는 후보로만 기록해. Slack을 실제 발송 대상으로 고르면 chat:write, Slack 워크스페이스 ID,
  수업용 샌드박스 채널 ID, 최종 Slack 메시지 본문, 명시적 승인이 모두 있을 때만 별도 요청에서 한 건을
  다룰 수 있으며, 지금은 빠진 조건을 발송 대기로 기록해.
- 외부 발송, 삭제, 결제, 계정 권한 변경, 실제 커넥터 연결, 예약 실행은 하지 마.

마지막에는 만든 폴더, 생성한 핵심 파일, 확인 필요 항목, 2강에서 시험할 다음 최소 단위 하나만 짧게 보고해.
그리고 아래 네 가지를 경로와 함께 확인해.
1. `skills/`, `.agents/skills/`, `.claude/skills/`의 입·출력 규격 검증 SKILL.md 세 파일이 같은지
2. `skills/`, `.agents/skills/`, `.claude/skills/`의 목 입력 생성 SKILL.md 세 파일이 같은지
3. 각 SKILL.md가 `---`로 시작하고 `name`, `description` YAML frontmatter를 가지는지
4. `02-reference/workspace-file-guide.md`에서 각 폴더의 예시 파일이 생기는 상황을 안내했는지
```

## 완료 기준

- 1강 설계도와 로드맵을 읽어 업무자동화/ 루트의 핵심 구조가 한 번에 보강됐다.
- 수강생이 README나 지침 파일을 직접 작성하지 않았다.
- Codex는 `.agents/skills/`, Claude Code는 `.claude/skills/`에 같은 이름의 SKILL.md가 있어 두 스킬을
  발견할 수 있다. **생성 직후에는 현재 대화를 계속 쓰지 말고 업무자동화/ 폴더를 다시 열거나 새 Codex·Claude Code
  세션을 시작한 뒤** `입·출력 규격 검증을 진행해줘`처럼 요청한다.
- `02-reference/workspace-file-guide.md`에서 정상·예외·중복 시험과 실제 실행 뒤 각 폴더에 생길 파일의
  상황을 설명할 수 있다.
- 정상 입력 2건, 승인된 기준, 출력 위치, tests/lesson-02-results.md의 위치를 설명할 수 있다.
- 선택한 연결의 실제 실행 조건이 없으면 연결 대기 이유를 기록할 수 있다.
