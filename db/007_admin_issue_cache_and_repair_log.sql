-- 2026-09-29: 소스 장애 대비 + 자동 수리 로그
--
-- admin_issue_cache: KIND 관리종목 현황 조회가 실패(네트워크 차단·페이지 구조
-- 변경 등)했을 때 쓸 마지막 성공 목록. GitHub Actions는 매 실행이 새 컨테이너라
-- 로컬 파일 캐시가 못 버티므로 Supabase에 저장한다(collect_gainers.py의
-- _save_admin_issue_cache/_load_admin_issue_cache 참고). 단일 행(id=1)만 쓴다.
--
-- repair_log: repair_issues.py가 이슈를 감지하고 자동 수리를 시도한 기록.
-- 판단이 필요해 자동 수리하지 않은 항목도 action='alert_only'로 여기 남는다
-- (site 저장소 admin.html이 최근 로그를 읽기 전용으로 보여준다).

create table if not exists admin_issue_cache (
  id int primary key default 1,
  data jsonb not null,
  fetched_at timestamptz not null
);

create table if not exists repair_log (
  id bigserial primary key,
  checked_at timestamptz not null default now(),
  pipeline text not null,          -- 'gainers' | 'volume' | 'marketScope'
  trade_date date,
  issue text not null,             -- 사람이 읽을 설명
  action text not null,            -- 'backfill_rerun' | 'rule_violation_fix' | 'analysis_retry' | 'alert_only'
  detail jsonb,                    -- 조치 전/후 값 등 세부
  success boolean not null,
  created_at timestamptz not null default now()
);

create index if not exists repair_log_created_at_idx on repair_log (created_at desc);
