# AI 업무자동화 2강 시작 패키지

다운로드 파일 이름은 **`강의자료.zip`**입니다. 이 ZIP 하나에 **워크스페이스를 설정하는 안내**와
**1강 미참여자용 공통 샘플**이 함께 들어 있습니다. 별도의 샘플 ZIP이나 워크스페이스 예시 ZIP은 받지
않아도 됩니다.

> **이름 안내**: 아래 본문에서 말하는 `업무자동화/`는 이 학생의 경우 프로젝트 루트
> `E:\AI 스터디\만조그룹 2차\`를, `강의자료/`는 지금 이 README가 있는 폴더
> (`업무자동화 강의자료_20260725/`)를 가리킵니다. 실제 폴더 이름은 다르지만 역할은 같습니다.
>
> **이 폴더는 이미 설정이 끝난 상태입니다.** 개인 1강 로드맵은 이 폴더의 `나의_자동화_로드맵.html`이고,
> 내용 변경 없이 프로젝트 루트의 `progress/roadmap.html`로 이미 복사되어 있습니다 (두 파일이 동일한지는
> 아래 "1강 참여자" 절의 명령으로 다시 확인 가능). `skills/`, `context/`, `01-input/` 등 2강 공용 루트
> 구조(아래 "설정 뒤의 공용 루트" 참고)도 이미 만들어져 있으므로, 아래 "에이전트에게 할 한 문장"을 다시
> 실행할 필요는 없습니다. 이 README는 그 설정 과정을 기록해 둔 것이며, 이후 참고용으로 유지합니다.
>
> **현재 작업 세션은 이 폴더 안에서만 작업하도록 범위가 제한되어 있습니다.** 프로젝트 루트 파일(예:
> `progress/roadmap.html`, `design/`)을 실제로 고치려면 루트 폴더를 다시 열어야 합니다.

## 압축을 풀 위치

```text
업무자동화/
└── 강의자료/                    ← 이 ZIP을 여기에 압축 해제
    ├── README.md                ← 지금 읽는 안내
    ├── workspace-scaffold-skill.md
    ├── skill-templates/         ← 앱이 인식하는 SKILL.md 원본 2개
    ├── common-sample-pack/      ← 1강 미참여자용 가상 준비물
    └── report-template/         ← 복습요약 보고서 양식(HTML·PDF)
```

1. 1강 수강생은 기존 `업무자동화/` 안에 `강의자료/` 폴더를 만들고 이 ZIP을 풉니다.
2. 2강부터 참여한 수강생은 새 `업무자동화/` 폴더와 그 안의 `강의자료/` 폴더를 만든 뒤 같은 ZIP을 풉니다.
   `common-sample-pack/`은 이미 함께 들어 있으므로 따로 내려받거나 따로 압축 해제하지 않습니다.
3. Codex 또는 Claude Code에서 **`업무자동화/` 폴더 자체**를 프로젝트로 엽니다. `강의자료/`만 따로 열지
   않습니다.

## 에이전트에게 할 한 문장

아래 문장을 그대로 보냅니다.

```text
현재 열린 업무자동화/ 폴더의 강의자료/README.md와 강의자료/workspace-scaffold-skill.md를 먼저 읽어. semiclass-workspace-scaffold 스킬을 사용할 수 있으면 사용하고, 없으면 강의자료/workspace-scaffold-skill.md의 규칙을 그대로 따라. 개인 1강 로드맵(예: 강의자료/나의_자동화_로드맵.html처럼 강의자료 폴더 안에 있는, 가상 샘플이 아닌 본인 로드맵 HTML)이 있으면 그 파일을 progress/roadmap.html로 내용 변경 없이 복사해. 전혀 없으면 강의자료/common-sample-pack/virtual-lesson-01-roadmap.html을 대신 복사해. 그 외에는 1강 설계도와 로드맵을 바탕으로 업무자동화/ 루트 자체에 2강 구조를 안전하게 보강해줘. 강의자료/report-template/의 복습요약 보고서 양식은 02-reference/에 복사해. 강의자료와 기존 design/·progress/ 파일은 덮어쓰거나 옮기거나 삭제하지 말고, 없는 폴더와 파일만 만들어줘. 새 워크스페이스 루트는 만들지 말고, 실제 커넥터 연결·외부 발송·예약 실행은 하지 마.
```

## 1강 참여자 (개인 로드맵 있음) — 이 학생의 경우

개인 1강 로드맵 파일이 이미 있으면(이 학생은 `나의_자동화_로드맵.html`), 가상 샘플을 쓰지 않고 그
파일을 `progress/roadmap.html`로 그대로 복사합니다. 이미 복사가 끝났다면 두 파일 내용이 같은지만
확인하면 됩니다. **이 폴더(`강의자료/`) 안에서** 다음처럼 비교해서 결과가 없으면(=차이 없음) 정상입니다.

```bash
diff "../progress/roadmap.html" "나의_자동화_로드맵.html"
```

(프로젝트 루트 `업무자동화/`에서 실행한다면 `diff "progress/roadmap.html" "강의자료/나의_자동화_로드맵.html"`.)

## 1강 미참여자만 할 일

개인 1강 로드맵이 전혀 없으면 에이전트가 `common-sample-pack/virtual-lesson-01-roadmap.html`을
`progress/roadmap.html`로 그대로 복사합니다. N-01·N-02와 결과 작성 기준은 각각 `01-input/`과
`02-reference/`의 실습 사본으로 준비합니다. 이 샘플은 수업 연습용이므로 개인 로드맵의 완료 증거로
바꾸지 않습니다.

## 설정 뒤의 공용 루트

`업무자동화/`에는 1강의 `design/`·`progress/`를 유지한 채 `README.md`, `AGENTS.md 또는
CLAUDE.md`, `skills/`, `context/`, `inbox/`, `01-input/`, `02-reference/`, `03-output/`, `workflow/`,
`tests/`, `evidence/`, `knowledge/`가 보강됩니다. 폴더별 역할은 에이전트가 만드는
`02-reference/workspace-file-guide.md`에서 확인합니다. `01-input/`, `02-reference/`, `03-output/`에는
수강생이 직접 작성할 필요가 없도록 각각 사용 안내 README도 함께 만들어집니다.

## 스킬을 인식시키는 마지막 한 번

스캐폴딩은 강의자료/skill-templates/의 검증된 원본을 `skills/`에 복사하고, Codex용 `.agents/skills/`,
Claude Code용 `.claude/skills/`에도 같은 `SKILL.md` 패키지를 복사합니다. 각 파일은 첫 줄부터 YAML
frontmatter(`name`, `description`)를 포함합니다.
만든 직후에는 현재 대화에서 바로 호출하지 말고 `업무자동화/`를 다시 열거나 새 Codex·Claude Code 세션을
시작하세요. 그 다음에는 `$semiclass-input-output-spec-review` 또는 “입·출력 규격 검증을 진행해줘”처럼
요청할 수 있습니다.
