-- ────────────────────────────────────────────────────────────────
-- 일별 원본 상승률 후보 저장 테이블 (신규)
-- Supabase 대시보드 > SQL Editor 에 그대로 붙여넣고 [Run] 하세요.
--
-- 배경: get_weekly_top10()이 "그 주 5거래일"을 반복문으로 돌면서도 실제로는
-- 매번 네이버의 실시간(현재) 상승률 페이지만 다시 긁고 있었다 - 이 페이지는
-- 과거 날짜를 조회할 방법이 없는 실시간 전용 페이지라서, 결과적으로 "오늘
-- 하루치 등락률을 5제곱 복리 계산"하는 버그가 됐다(2026-08-01 발견,
-- 두산 +271.29% = 1.30^5-1 과 정확히 일치하는 것으로 확인).
--
-- 이 표는 평일 자동 실행 때마다 그날의 원본 후보(KOSPI+KOSDAQ 상위 100씩)를
-- 저장해서, 토요일 주간 발행일에는 실시간 재조회 대신 이 표에서 그 주
-- 5거래일의 "진짜" 일별 등락률을 읽어 복리 계산하도록 바꾼다.
-- ────────────────────────────────────────────────────────────────

create table if not exists public.raw_top_candidates (
  id           bigint generated always as identity primary key,
  trade_date   date    not null,
  market       text    not null check (market in ('kospi', 'kosdaq')),
  ticker       text    not null,
  name         text    not null,
  close        numeric,
  change_pct   numeric,
  trade_amount numeric,

  updated_at   timestamptz not null default now(),

  unique (trade_date, ticker)
);

create index if not exists raw_top_candidates_trade_date_idx
  on public.raw_top_candidates (trade_date desc);

alter table public.raw_top_candidates enable row level security;

drop policy if exists "public read raw_top_candidates" on public.raw_top_candidates;
create policy "public read raw_top_candidates"
  on public.raw_top_candidates
  for select
  to anon, authenticated
  using (true);
-- 쓰기 정책 없음 → anon key로는 쓰기 불가. GitHub Actions는 service_role key로 씀.
