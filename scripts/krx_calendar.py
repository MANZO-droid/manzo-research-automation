# -*- coding: utf-8 -*-
"""
KRX(한국거래소) 거래일 판단 공용 모듈 (Python)

lib/krx-calendar.js 와 완전히 동일한 로직을 파이썬으로 구현한 버전입니다.
두 파일은 같은 krx-holidays-2026.json을 읽어 사용하며, 규칙 설명은
lib/krx-calendar.js 상단 주석 및 저장소 루트 AUTOMATION_NOTES.md를 참고하세요.

요약:
  1) 평일 + 개장일 → 당일(daily) 모드.
  2) 토요일 → 그 주(월~금) 상승률 top10을 "주간"(weekly) 리포트로 발행.
  3) 그 주 금요일이 휴장일이면 → 발행일이 토요일이 아니라 금요일로 당겨짐.
  4) 토요일 포함 3일 이상 연속 휴장이면 → 그 연휴가 시작되는 첫 휴일에 발행.
  (3, 4는 "토요일부터 거꾸로 훑어 연속된 비거래일 구간의 시작일을 찾는다"는
   동일한 알고리즘의 특수 사례입니다.)
"""
import json
import os
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOLIDAY_FILES = [os.path.join(ROOT, "krx-holidays-2026.json")]


def _load_holidays() -> set:
    holidays = set()
    for path in HOLIDAY_FILES:
        try:
            with open(path, encoding="utf-8") as f:
                holidays.update(json.load(f))
        except (FileNotFoundError, json.JSONDecodeError):
            continue
    return holidays


HOLIDAYS = _load_holidays()


def _parse(date_str: str) -> date:
    return datetime.strptime(date_str, "%Y-%m-%d").date()


def _fmt(d: date) -> str:
    return d.strftime("%Y-%m-%d")


def is_trading_day(date_str: str) -> bool:
    d = _parse(date_str)
    if d.weekday() >= 5:  # 5=토, 6=일
        return False
    if date_str in HOLIDAYS:
        return False
    return True


def previous_trading_day(date_str: str) -> str:
    d = _parse(date_str) - timedelta(days=1)
    while not is_trading_day(_fmt(d)):
        d -= timedelta(days=1)
    return _fmt(d)


def next_trading_day(date_str: str) -> str:
    d = _parse(date_str) + timedelta(days=1)
    while not is_trading_day(_fmt(d)):
        d += timedelta(days=1)
    return _fmt(d)


def get_weekly_report_trigger(date_str: str) -> dict:
    """
    반환: {shouldRun, weekStart, weekEnd, mode} (mode: 'daily' | 'weekly')
    JS 버전(lib/krx-calendar.js)의 getWeeklyReportTrigger와 동일한 로직.
    """
    d = _parse(date_str)
    dow = d.weekday()  # 0=월 ... 5=토, 6=일

    if dow <= 4 and is_trading_day(date_str):
        return {"shouldRun": True, "weekStart": None, "weekEnd": None, "mode": "daily"}

    if dow == 6:  # 일요일
        return {"shouldRun": False, "weekStart": None, "weekEnd": None, "mode": "daily"}

    # 여기 도달: 토요일이거나, 평일인데 휴장일
    monday_offset = 5 if dow == 5 else dow  # 파이썬 weekday: 월=0 이므로 dow 자체가 월요일과의 차이
    monday = d - timedelta(days=monday_offset)
    saturday = monday + timedelta(days=5)

    trigger = saturday
    cur = saturday - timedelta(days=1)  # 금요일부터 시작
    while cur >= monday and not is_trading_day(_fmt(cur)):
        trigger = cur
        cur -= timedelta(days=1)

    week_end_candidate = trigger - timedelta(days=1)
    week_end = (
        _fmt(week_end_candidate)
        if week_end_candidate >= monday and is_trading_day(_fmt(week_end_candidate))
        else None
    )

    should_run = (d == trigger) and (week_end is not None)
    return {
        "shouldRun": should_run,
        "weekStart": _fmt(monday),
        "weekEnd": week_end,
        "mode": "weekly",
    }
