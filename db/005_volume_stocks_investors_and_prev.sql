-- ────────────────────────────────────────────────────────────────
-- volume_stocks 확장: 개인/기관/외국인 순매수 + 전일 대비(순위·가격·거래대금)
-- Supabase 대시보드 > SQL Editor 에 그대로 붙여넣고 [Run] 하세요.
--
-- 배경: 사이트(index.html renderVolumeTable)는 원래 s.investors(개인/기관/
-- 외국인 순매수), s.prevRank(전일 순위), s.priceChange(전일 대비 가격),
-- s.prevTradeAmount(전일 거래대금)를 렌더링하도록 만들어져 있었는데,
-- 2026-08-01 Supabase 전환 때 이 컬럼들을 스키마에서 빠뜨려서 화면에
-- 안 보이는 회귀가 발생했다(회장님이 발견). db/003에 컬럼을 추가한다.
-- ────────────────────────────────────────────────────────────────

alter table public.volume_stocks
  add column if not exists investors jsonb,          -- {individual, institution, foreign} (원 단위, 근사값)
  add column if not exists prev_rank int,             -- 전일 같은 종목의 순위 (신규 진입이면 null)
  add column if not exists price_change numeric,      -- 전일 대비 가격(원, 부호 포함)
  add column if not exists prev_trade_amount numeric; -- 전일 거래대금(원)

comment on column public.volume_stocks.investors is
  '{individual, institution, foreign} 순매수 금액(원, 근사값). institution/foreign은 네이버 frgn.naver의 순매매량(주) × 종가로 근사, individual은 -(institution+foreign) 역산값.';
comment on column public.volume_stocks.prev_rank is
  '전일 거래대금 상위 순위. scripts/collect_gainers.py가 upsert 직전 Supabase에서 전 거래일 데이터를 조회해 계산.';
