-- ────────────────────────────────────────────────────────────────
-- 당일 상승률 상위 10위 저장 테이블
-- Supabase 대시보드 > SQL Editor 에 그대로 붙여넣고 [Run] 하세요.
-- (표가 들어있는 공책을 하나 새로 만드는 작업입니다. 기존 데이터는 건드리지 않습니다.)
-- ────────────────────────────────────────────────────────────────

create table if not exists public.daily_gainers (
  id            bigint generated always as identity primary key,
  trade_date    date    not null,            -- 기준일 (예: 2026-07-11)
  rank          int     not null,            -- 순위 1~10

  -- ↓ 키움 ka10027 / 일봉차트로 매일 자동 채우는 필드
  ticker        text    not null,            -- 종목코드 (예: 043590)
  name          text    not null,            -- 종목명
  close         numeric,                     -- 현재가(종가)
  change_pct    numeric,                     -- 전일대비 등락률(%)
  trade_amount  numeric,                     -- 거래대금(원)
  ohlcv         jsonb,                       -- 일봉 배열 [{date,open,high,low,close,volume}...]
  technicals    jsonb,                       -- {ma5,ma20,ma60,volumeAvg20,currentClose}

  -- ↓ 자동화하지 않는 필드 (기존처럼 수동/AI로 채움 — 자동 갱신이 덮어쓰지 않음)
  financials    jsonb,                       -- {period,revenue,operatingProfit,...}
  news          jsonb,                       -- [{title,link}...]
  rise_reason   text,                        -- 상승 이유 분석글
  chart_analysis text,                       -- 차트 분석글

  updated_at    timestamptz not null default now(),

  unique (trade_date, rank)                  -- 하루 안에서 순위는 유일
);

create index if not exists daily_gainers_trade_date_idx
  on public.daily_gainers (trade_date desc);

-- 보안(RLS): 이 표는 사이트에 공개되는 데이터이므로 "읽기"는 누구나 허용,
-- "쓰기"는 service_role(서버 전용 열쇠)만 가능하게 둡니다.
alter table public.daily_gainers enable row level security;

drop policy if exists "public read daily_gainers" on public.daily_gainers;
create policy "public read daily_gainers"
  on public.daily_gainers
  for select
  to anon, authenticated
  using (true);
-- 쓰기 정책은 만들지 않음 → anon key로는 쓰기 불가.
-- Vercel 서버는 service_role key를 써서 RLS를 우회해 기록합니다.
