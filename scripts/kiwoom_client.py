# -*- coding: utf-8 -*-
"""키움증권 REST API 클라이언트 - 상승률/거래대금 상위 "플랜B" 전용.

2026-10-19 추가(회장님 요청): 네이버가 올해만 세 번(상승률 상위), 한 번(거래대금 상위)
조용히·시끄럽게 막힌 전례가 있어 이미 KRX Open API 대체소스가 붙어 있었는데
(fetch_krx_day_prices/fetch_krx_day_all, collect_gainers.py), 그 소스는 "최신 며칠치가
아직 안 올라와 있을 때가 있다"는 한계가 이미 확인돼 있다(당일 대체로는 못 미더움).
키움 REST API는 네이버처럼 실시간이라 그 공백(당일 장중/장마감 직후)을 메울 수 있다.

대체 순서(collect_gainers.py에서 조립): 네이버(1차) → 키움(2차, 이 모듈) → KRX Open API(3차).

API 스펙 출처: 키움증권 공식 GitHub 저장소(Kiwoom-Securities/Kiwoom-REST-API)의
kiwoom/_data/kiwoom_api_spec.json을 2026-10-19에 직접 받아 확인한 필드명 그대로 사용한다
(추측 없음 - au10001/ka10027/ka10032 세 TR만 이 세션에서 실제로 대조했다).

필요 환경변수: KIWOOM_APPKEY, KIWOOM_SECRETKEY (.env.local 또는 GitHub Secrets).
KIWOOM_HOST를 넣으면 실전 대신 모의투자 도메인(https://mockapi.kiwoom.com)으로 바꿀 수 있다.

⚠ 확인된 제약(키움 개발자센터 안내, 이 프로젝트에서는 미실측): 접근토큰 발급은 사전에
등록한 IP에서만 된다(최대 10개). GitHub Actions 호스티드 러너는 IP가 매번 바뀌므로,
이 모듈을 GitHub Actions에서 그대로 쓰면 토큰 발급 단계(get_access_token)에서
8050류 에러로 막힐 가능성이 있다 - 실제로 막히는지는 이 세션에서 키움 앱키를
받기 전이라 검증하지 못했다. 막히면 로컬/고정 IP 환경에서만 수동 실행하는 용도로
남는다(완전 자동화 불가, 수동 플랜B)."""
import os
import time

import requests

_token_cache = {"token": None, "expires_at": 0.0}


def _host() -> str:
    return os.environ.get("KIWOOM_HOST", "https://api.kiwoom.com")


def get_access_token() -> str:
    """au10001(접근토큰 발급). 토큰은 보통 하루 단위로 발급되므로(응답의 expires_dt가
    보통 다음날 새벽) 23시간만 캐시해 두고, 매 호출마다 새로 받지 않는다."""
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires_at"]:
        return _token_cache["token"]

    appkey = os.environ.get("KIWOOM_APPKEY")
    secretkey = os.environ.get("KIWOOM_SECRETKEY")
    if not appkey or not secretkey:
        raise RuntimeError("KIWOOM_APPKEY/KIWOOM_SECRETKEY가 없습니다 - .env.local에 추가해 주세요.")

    r = requests.post(
        f"{_host()}/oauth2/token",
        headers={"Content-Type": "application/json;charset=UTF-8"},
        json={"grant_type": "client_credentials", "appkey": appkey, "secretkey": secretkey},
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    if data.get("return_code") not in (0, None):
        raise RuntimeError(f"키움 토큰 발급 실패: {data.get('return_msg')}")

    _token_cache["token"] = data["token"]
    _token_cache["expires_at"] = now + 23 * 3600
    return data["token"]


def _call(api_id: str, body: dict) -> dict:
    """공통 호출부(순위정보 TR은 전부 같은 엔드포인트 /api/dostk/rkinfo를 쓰고,
    header의 api-id로만 TR을 구분한다)."""
    token = get_access_token()
    r = requests.post(
        f"{_host()}/api/dostk/rkinfo",
        headers={
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {token}",
            "api-id": api_id,
        },
        json=body,
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    if data.get("return_code") not in (0, None):
        raise RuntimeError(f"키움 {api_id} 호출 실패: {data.get('return_msg')}")
    return data


def fetch_top_gainers(market: str, top_n: int = 40) -> list[dict]:
    """ka10027(전일대비등락률상위요청). market: "KOSPI" 또는 "KOSDAQ".
    collect_gainers.fetch_top_gainers()와 같은 모양(list[dict], 키 ticker/name/close/
    changePct/volume/tradeAmount)으로 맞춰서 그 자리에 그대로 꽂을 수 있게 한다.

    ⚠ 이 TR은 거래대금(trde_prica) 필드를 안 준다 - close*volume으로 근사한다
    (기존 네이버 경로의 '값이 없으면 close*volume 근사' 관행과 동일)."""
    mrkt_tp = {"KOSPI": "001", "KOSDAQ": "101"}[market]
    data = _call("ka10027", {
        "mrkt_tp": mrkt_tp, "sort_tp": "1", "trde_qty_cnd": "0000",
        "stk_cnd": "0", "crd_cnd": "0", "updown_incls": "1",
        "pric_cnd": "0", "trde_prica_cnd": "0", "stex_tp": "3",
    })
    out = []
    for item in data.get("pred_pre_flu_rt_upper", []):
        ticker = item.get("stk_cd", "")
        if not (len(ticker) == 6 and ticker.isdigit()):
            continue
        try:
            close = abs(int(item["cur_prc"]))
            change_pct = float(item["flu_rt"])
            volume = int(item["now_trde_qty"])
        except (KeyError, ValueError):
            continue
        out.append({
            "ticker": ticker, "name": item.get("stk_nm", ""), "close": close,
            "changePct": change_pct, "volume": volume, "tradeAmount": close * volume,
        })
        if len(out) >= top_n:
            break
    return out


def fetch_volume_stocks(top_n: int = 10) -> list[dict]:
    """ka10032(거래대금상위요청). KOSPI+KOSDAQ 각각 호출해 합친 뒤 거래대금으로
    다시 정렬한다(mrkt_tp=000/전체는 이 TR 명세에 없어 시장별로 불러야 함).
    이 TR은 이미 거래대금 기준으로 정렬돼서 오므로, 네이버 플랜B처럼 "전 종목(수천개)을
    받아 재정렬"할 필요가 없다 - 종목당 데이터 자체가 훨씬 가볍다.
    관리종목은 mang_stk_incls=0으로 API 단에서 미리 제외한다(collect_gainers의
    classify_excluded가 ETF/ETN/우선주/리츠까지 추가로 걸러낸다)."""
    merged = []
    for mrkt_tp in ("001", "101"):
        data = _call("ka10032", {"mrkt_tp": mrkt_tp, "mang_stk_incls": "0", "stex_tp": "3"})
        for item in data.get("trde_prica_upper", []):
            ticker = item.get("stk_cd", "")
            if not (len(ticker) == 6 and ticker.isdigit()):
                continue
            try:
                close = abs(int(item["cur_prc"]))
                change_pct = float(item["flu_rt"])
                trade_amount = int(item["trde_prica"]) * 1_000_000  # 명세: 단위 백만원
            except (KeyError, ValueError):
                continue
            merged.append({
                "ticker": ticker, "name": item.get("stk_nm", ""), "close": close,
                "changePct": change_pct, "tradeAmount": trade_amount,
            })
    merged.sort(key=lambda x: x["tradeAmount"], reverse=True)
    return merged[:top_n]
