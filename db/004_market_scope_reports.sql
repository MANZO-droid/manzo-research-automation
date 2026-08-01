-- ────────────────────────────────────────────────────────────────
-- 마켓 스코프 리포트 저장 테이블 (신규)
-- Supabase 대시보드 > SQL Editor 에 그대로 붙여넣고 [Run] 하세요.
--
-- 배경: 기존에는 market-scope-data.json({current, history[]})에 하드코딩되어
-- 사이트 저장소에 git push 되던 데이터를 이 표로 옮긴다. 날짜별로 한 행씩
-- upsert하고, "current"는 report_date가 가장 최신인 행, "history"는 나머지
-- 행들로 API 레이어(api/market-scope.js)에서 구성한다.
-- ────────────────────────────────────────────────────────────────

create table if not exists public.market_scope_reports (
  report_date    date primary key,
  range_label    text,
  message_count  int,
  channel_count  int,
  items          jsonb not null default '[]'::jsonb,  -- [{rank,name,type,mention,channel,score,articles:[{title,summary,url}]}...]

  updated_at     timestamptz not null default now()
);

create index if not exists market_scope_reports_report_date_idx
  on public.market_scope_reports (report_date desc);

alter table public.market_scope_reports enable row level security;

drop policy if exists "public read market_scope_reports" on public.market_scope_reports;
create policy "public read market_scope_reports"
  on public.market_scope_reports
  for select
  to anon, authenticated
  using (true);
-- 쓰기 정책 없음 → anon key로는 쓰기 불가. GitHub Actions는 service_role key로 씀.
