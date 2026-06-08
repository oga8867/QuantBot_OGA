"""
=============================================================================
utils/market_holidays.py - 한국/미국 시장 휴장일 조회 + 캐싱
=============================================================================

봇이 휴장일(공휴일·임시공휴일·KRX 임시휴장 등)을 판단할 때 사용합니다.

데이터 소스 우선순위:
  1) 메모리 캐시 (process 수명)
  2) 디스크 캐시 (data/holidays_{KR|US}_{YYYY}.json, TTL 7일)
  3) pandas_market_calendars (XKRX/XNYS) — 임시공휴일·조기폐장 포함
  4) 네트워크·라이브러리 실패 → 빈 집합 → 호출 측이 주말만 체크 (현재 동작 유지)
     즉 "더 나빠지지 않음" 보장.

휴일 이름은 별도 룩업:
  - KR: 양력 고정 휴일 매핑 + korean_lunar_calendar로 음력 휴일 추정
  - US: 양력 고정 + 변동 휴일 패턴 매칭(MLK·Memorial·Thanksgiving 등)
  - 매칭 실패 → "임시휴장" / "Market Holiday"

캐시 TTL이 7일인 이유: 임시공휴일은 연중 발표될 수 있어서 너무 길면 누락 위험.
=============================================================================
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta
from typing import Dict, Optional, Set

logger = logging.getLogger(__name__)

# ─── 캐시 설정 ────────────────────────────────────────────────────────────
CACHE_DIR = "data"
CACHE_TTL_SEC = 7 * 86400  # 7일 — 임시공휴일 발표 주기 고려

# 메모리 캐시 (process 수명)
_mem_cache: Dict[str, Dict[int, Set[date]]] = {"KR": {}, "US": {}}


def _cache_path(market: str, year: int) -> str:
    return os.path.join(CACHE_DIR, f"holidays_{market}_{year}.json")


def _load_disk_cache(market: str, year: int) -> Optional[Set[date]]:
    """디스크 캐시 로드. 만료/손상이면 None."""
    path = _cache_path(market, year)
    if not os.path.exists(path):
        return None
    try:
        st = os.stat(path)
        if (datetime.now().timestamp() - st.st_mtime) > CACHE_TTL_SEC:
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {date.fromisoformat(d) for d in data.get("dates", [])}
    except Exception as e:
        logger.debug(f"[휴장일] {market} {year} 디스크 캐시 로드 실패: {e}")
        return None


def _save_disk_cache(market: str, year: int, holidays: Set[date]) -> None:
    """디스크 캐시 저장. 실패해도 예외 전파 안 함."""
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        path = _cache_path(market, year)
        data = {
            "market": market,
            "year": year,
            "saved_at": datetime.now().isoformat(),
            "count": len(holidays),
            "dates": sorted([d.isoformat() for d in holidays]),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.debug(f"[휴장일] {market} {year} 디스크 캐시 저장 실패: {e}")


def _fetch_from_mcal(market: str, year: int) -> Optional[Set[date]]:
    """
    pandas_market_calendars로 휴장일 추출.

    XKRX(한국)·XNYS(미국) 캘린더 사용. 임시공휴일·조기폐장 모두 반영됨.
    "전체 평일 - 거래일 집합 = 휴장 평일" 식으로 역산.
    """
    try:
        import pandas as pd
        import pandas_market_calendars as mcal
    except ImportError:
        logger.warning(
            "[휴장일] pandas_market_calendars 미설치 → 폴백 (주말만 체크)"
        )
        return None

    code = {"KR": "XKRX", "US": "XNYS"}.get(market.upper())
    if not code:
        return None

    try:
        cal = mcal.get_calendar(code)
        sched = cal.schedule(
            start_date=f"{year}-01-01",
            end_date=f"{year}-12-31",
        )
        trading = set(sched.index.date)
        all_business = pd.date_range(
            f"{year}-01-01", f"{year}-12-31", freq="B"
        )
        return {d for d in all_business.date if d not in trading}
    except Exception as e:
        logger.warning(f"[휴장일] {market} {year} mcal 조회 실패: {e}")
        return None


def get_holidays(market: str, year: int) -> Set[date]:
    """
    해당 시장의 휴장일 집합 반환 (주말 제외).

    메모리 → 디스크(TTL) → 네트워크(mcal) → 빈 집합 순서.
    실패해도 빈 집합 반환하므로 호출 측에서 안전하게 사용 가능.

    Parameters:
        market: "KR" 또는 "US"
        year:   조회할 연도

    Returns:
        Set[date] — 그 해 평일 중 시장이 닫힌 날짜들의 집합
    """
    market = market.upper()
    if market not in _mem_cache:
        return set()

    cache = _mem_cache[market]
    if year in cache:
        return cache[year]

    disk = _load_disk_cache(market, year)
    if disk is not None:
        cache[year] = disk
        return disk

    fresh = _fetch_from_mcal(market, year)
    if fresh is not None:
        cache[year] = fresh
        _save_disk_cache(market, year, fresh)
        return fresh

    # 폴백: 빈 집합 (호출 측이 주말만 체크하던 기존 동작과 동일)
    cache[year] = set()
    return set()


def get_kr_holidays(year: int) -> Set[date]:
    """한국 시장(KRX) 휴장일 집합."""
    return get_holidays("KR", year)


def get_us_holidays(year: int) -> Set[date]:
    """미국 시장(NYSE) 휴장일 집합."""
    return get_holidays("US", year)


def is_market_holiday(d: date, market: str) -> bool:
    """주말이거나 해당 시장 휴장일이면 True."""
    if d.weekday() >= 5:  # 토(5) / 일(6)
        return True
    return d in get_holidays(market, d.year)


def next_business_day(d: date, market: str) -> date:
    """
    주어진 날짜 다음의 시장 영업일.

    주말·휴장일을 건너뛰며 1년 한도 안에서 탐색 (안전망).
    """
    market = market.upper()
    nd = d + timedelta(days=1)
    for _ in range(366):
        if not is_market_holiday(nd, market):
            return nd
        nd = nd + timedelta(days=1)
    return nd  # 이론상 도달 불가


# ─── 휴일 이름 매핑 (양력 고정 + 음력 추정) ────────────────────────────────

# 한국 양력 고정 공휴일
_KR_FIXED: Dict[tuple, str] = {
    (1, 1): "신정",
    (3, 1): "삼일절",
    (5, 1): "근로자의 날",
    (5, 5): "어린이날",
    (6, 6): "현충일",
    (8, 15): "광복절",
    (10, 3): "개천절",
    (10, 9): "한글날",
    (12, 25): "성탄절",
    (12, 31): "연말 종무일",
}

# 미국 양력 고정 휴일 (관찰 휴일은 별도 처리)
_US_FIXED: Dict[tuple, str] = {
    (1, 1): "New Year's Day",
    (6, 19): "Juneteenth",
    (7, 4): "Independence Day",
    (12, 25): "Christmas",
}


def _kr_match_direct(d: date) -> Optional[str]:
    """
    한국 휴일 직접 매칭 (양력 고정 + 음력 매칭). 대체공휴일 로직 제외.

    이 함수는 _kr_holiday_name에서 두 가지 용도로 쓰인다:
      1) 입력 날짜 자체의 직접 매칭
      2) 직전 1~3일을 거슬러 본 휴일을 찾아 "○○ 대체공휴일" 라벨 생성
    """
    if (d.month, d.day) in _KR_FIXED:
        return _KR_FIXED[(d.month, d.day)]
    try:
        from korean_lunar_calendar import KoreanLunarCalendar
        c = KoreanLunarCalendar()
        c.setSolarDate(d.year, d.month, d.day)
        lm, ld = c.lunarMonth, c.lunarDay
        if lm == 1 and ld == 1:
            return "설날"
        if lm == 12 and ld >= 29:  # 음력 12월 말일 = 설날 전날
            return "설날 연휴"
        if lm == 1 and ld == 2:
            return "설날 연휴"
        if lm == 4 and ld == 8:
            return "부처님오신날"
        if lm == 8 and ld == 15:
            return "추석"
        if lm == 8 and ld in (14, 16):
            return "추석 연휴"
    except Exception:
        pass
    return None


def _kr_holiday_name(d: date) -> str:
    """
    한국 휴일 이름 (직접 매칭 → 대체공휴일 추정 → 폴백).

    대체공휴일 규칙:
      - 어린이날·삼일절·광복절·개천절·한글날·성탄절·부처님오신날이 일요일이면
        → 다음 평일이 대체공휴일
      - 설날/추석 연휴가 일요일과 겹치면 → 다음 평일이 대체공휴일
      - 봇은 직전 1~3일 안에 휴일이 있고 그게 일요일이면(또는 연휴 본일이면)
        "○○ 대체공휴일"로 추정한다.
    """
    name = _kr_match_direct(d)
    if name:
        return name

    # 대체공휴일 추정 — 직전 1~3일을 거슬러 본 휴일 찾기.
    # KRX는 본 공휴일이 토·일에 떨어지면 다음 평일을 휴장으로 처리한다
    # (한국 법령상 일요일 공휴일은 모두 대체, 토요일은 설날·추석·어린이날만이지만
    #  KRX 실제 거래일 데이터를 보면 토요일에 떨어진 다른 공휴일도 휴장으로 잡힘).
    for back in (1, 2, 3):
        prev = d - timedelta(days=back)
        prev_name = _kr_match_direct(prev)
        if not prev_name:
            continue
        # 토·일에 떨어진 공휴일 → 다음 평일 대체공휴일
        # 또는 설날/추석 본일이 직전에 있으면 (연휴 자체가 토/일과 겹친 경우 포함)
        if prev.weekday() >= 5 or prev_name in ("설날", "추석"):
            return f"{prev_name} 대체공휴일"

    # 진짜 매칭 안 되는 휴장 (임시공휴일·KRX 임시휴장 등)
    return "임시휴장"


def _us_holiday_name(d: date) -> str:
    """미국 휴일 이름 추정 (양력 고정 + 변동 패턴)."""
    # 양력 고정
    if (d.month, d.day) in _US_FIXED:
        return _US_FIXED[(d.month, d.day)]

    wd = d.weekday()  # 0=Mon ... 6=Sun

    # 월요일 변동 휴일
    if wd == 0:
        if d.month == 1 and 15 <= d.day <= 21:
            return "MLK Day"
        if d.month == 2 and 15 <= d.day <= 21:
            return "Presidents' Day"
        if d.month == 5 and d.day >= 25:
            return "Memorial Day"
        if d.month == 9 and d.day <= 7:
            return "Labor Day"

    # Thanksgiving: 11월 넷째 목요일
    if wd == 3 and d.month == 11 and 22 <= d.day <= 28:
        return "Thanksgiving"

    # Good Friday: 부활절 직전 금요일 (3~4월)
    if wd == 4 and d.month in (3, 4):
        return "Good Friday"

    # 관찰 휴일 (Observed)
    if d.month == 7 and d.day == 3 and wd == 4:
        return "Independence Day (Observed)"
    if d.month == 12 and d.day == 24 and wd == 4:
        return "Christmas Eve"
    if d.month == 1 and d.day == 2 and wd == 4:
        return "New Year (Observed)"

    return "Market Holiday"


def get_holiday_name(d: date, market: str) -> Optional[str]:
    """
    휴장일이면 이름 반환, 아니면 None.

    주말은 None (별도 표시). 평일 휴장일에만 이름을 돌려준다.
    """
    market = market.upper()
    if d.weekday() >= 5:
        return None
    if d not in get_holidays(market, d.year):
        return None
    if market == "KR":
        return _kr_holiday_name(d)
    if market == "US":
        return _us_holiday_name(d)
    return None
