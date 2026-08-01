-- ────────────────────────────────────────────────────────────────
-- daily_gainers 확장: 주간 리포트(report_type='weekly') 지원 +
-- rise_reason/chart_analysis/news가 이제 자동 생성됨을 반영하는 코멘트 갱신.
-- Supabase 대시보드 > SQL Editor 에 그대로 붙여넣고 [Run] 하세요.
-- (기존 데이터는 건드리지 않습니다 — 컬럼 추가 + 제약조건 재설정만 합니다.)
--
-- 배경: scripts/collect_gainers.py(2026-08-01 이후)는 당일(daily)뿐 아니라
-- 토요일·연휴 시작일에는 주간(weekly) Top10도 계산합니다. 원래 이 표는
-- daily 전용(trade_date, rank)이 유일키였는데, 주간 리포트도 같은 표에
-- 저장하려면 report_type으로 구분해야 유일키 충돌이 나지 않습니다.
-- ────────────────────────────────────────────────────────────────

alter table public.daily_gainers
  add column if not exists report_type text not null default 'daily',
  add column if not exists week_start date,
  add column if not exists week_end date;

alter table public.daily_gainers
  drop constraint if exists daily_gainers_report_type_check;
alter table public.daily_gainers
  add constraint daily_gainers_report_type_check
  check (report_type in ('daily', 'weekly'));

-- 기존 유일키(trade_date, rank)를 (trade_date, rank, report_type)으로 교체.
-- 주간 리포트는 trade_date에 "발행일"(토요일/연휴 시작일)을 넣고
-- week_start~week_end에 실제 집계 구간을 남긴다.
alter table public.daily_gainers
  drop constraint if exists daily_gainers_trade_date_rank_key;
alter table public.daily_gainers
  add constraint daily_gainers_trade_date_rank_report_type_key
  unique (trade_date, rank, report_type);

comment on column public.daily_gainers.rise_reason is
  '상승 이유 분석글 — 2026-08-01부터 scripts/collect_gainers.py가 Groq로 자동 생성해 씀. 더 이상 수동 전용 필드 아님.';
comment on column public.daily_gainers.chart_analysis is
  '차트 분석글 — 2026-08-01부터 scripts/collect_gainers.py가 Groq로 자동 생성해 씀. 더 이상 수동 전용 필드 아님.';
comment on column public.daily_gainers.news is
  '수집된 뉴스 [{title,summary,url}...] — scripts/collect_gainers.py가 자동으로 채움.';
comment on column public.daily_gainers.financials is
  '재무 정보 — 아직 자동화되지 않음(항상 빈 객체). 필요 시 사람이 채워야 함.';
