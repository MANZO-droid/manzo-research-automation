-- ────────────────────────────────────────────────────────────────
-- 거래대금 상위 10위 저장 테이블 (신규)
-- Supabase 대시보드 > SQL Editor 에 그대로 붙여넣고 [Run] 하세요.
--
-- 배경: 기존에는 stock-analysis-data.json의 dates[날짜].volumeStocks[]에
-- 하드코딩되어 사이트 저장소에 git push 되던 데이터를 이 표로 옮긴다.
-- scripts/collect_gainers.py의 fetch_volume_stocks() 결과를 그대로 upsert한다.
-- ────────────────────────────────────────────────────────────────

create table if not exists public.volume_stocks (
  id            bigint generated always as identity primary key,
  trade_date    date    not null,
  rank          int     not null,

  ticker        text    not null,
  name          text    not null,
  close         numeric,
  change_pct    numeric,
  trade_amount  numeric,
  naver_url     text,

  updated_at    timestamptz not null default now(),

  unique (trade_date, rank)
);

create index if not exists volume_stocks_trade_date_idx
  on public.volume_stocks (trade_date desc);

alter table public.volume_stocks enable row level security;

drop policy if exists "public read volume_stocks" on public.volume_stocks;
create policy "public read volume_stocks"
  on public.volume_stocks
  for select
  to anon, authenticated
  using (true);
-- 쓰기 정책 없음 → anon key로는 쓰기 불가. GitHub Actions는 service_role key로 씀.
