"""
=============================================================================
utils/timezones.py - 시간대 인식 datetime 유틸리티
=============================================================================

⚠️ CRITICAL: 이 봇이 사용하는 모든 시장 시간 판단은 정확한 KST/ET 기준이 필요합니다.

문제: Python `datetime.now()`는 naive datetime을 반환하므로
      서버 시간대가 KST가 아니면 시장 세션 / 쿨다운 / 토큰 만료 비교가 모두 어긋납니다.

해결: 이 모듈의 helper들로 명시적으로 시간대를 지정하여 datetime을 얻습니다.

  - now_kst(): 한국 표준시 기준 현재 시각
  - now_et():  미국 동부시(자동 DST) 기준 현재 시각
  - now_utc(): UTC 기준 현재 시각
  - to_kst(dt): naive datetime을 KST로 해석 (loaded from DB)
  - kst_naive(): tzinfo 없는 KST datetime (.replace(tzinfo=None))

권장 사용법:
  - 모든 시장 시간 비교: now_kst()
  - 모든 미국 시장 비교: now_et()
  - DB 저장/로드: ISO with explicit Z 또는 +09:00 suffix
=============================================================================
"""

from datetime import datetime, timezone, timedelta

# zoneinfo는 Python 3.9+. 이전 버전은 백포트 시도.
try:
    from zoneinfo import ZoneInfo  # Python 3.9+
    KST = ZoneInfo("Asia/Seoul")
    ET = ZoneInfo("America/New_York")
    UTC = timezone.utc
    HAS_ZONEINFO = True
except ImportError:
    try:
        from backports.zoneinfo import ZoneInfo  # Python 3.8
        KST = ZoneInfo("Asia/Seoul")
        ET = ZoneInfo("America/New_York")
        UTC = timezone.utc
        HAS_ZONEINFO = True
    except ImportError:
        # zoneinfo 백포트도 없으면 fixed-offset fallback
        # ⚠️ DST 자동처리 안 됨 (미국 시장 11~3월 1시간 어긋날 수 있음)
        KST = timezone(timedelta(hours=9))
        ET = timezone(timedelta(hours=-5))  # EST 기준 (winter)
        UTC = timezone.utc
        HAS_ZONEINFO = False


def now_kst() -> datetime:
    """현재 한국 표준시 (tz-aware)"""
    return datetime.now(KST)


def now_et() -> datetime:
    """현재 미국 동부시 (tz-aware, DST 자동)"""
    return datetime.now(ET)


def now_utc() -> datetime:
    """현재 UTC (tz-aware)"""
    return datetime.now(UTC)


def kst_naive() -> datetime:
    """KST 기준 시각을 tz 정보 없이 반환 (기존 코드 호환)"""
    return datetime.now(KST).replace(tzinfo=None)


def to_kst(dt: datetime) -> datetime:
    """
    datetime을 KST로 변환

    - tz-aware datetime → KST로 변환
    - naive datetime → 이미 KST라고 가정 (legacy DB 호환)
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=KST)
    return dt.astimezone(KST)


def to_utc(dt: datetime) -> datetime:
    """datetime을 UTC로 변환 (naive면 KST 가정)"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=KST).astimezone(UTC)
    return dt.astimezone(UTC)


def parse_kst_iso(iso_str: str) -> datetime:
    """
    ISO 문자열을 KST datetime으로 파싱

    - "2026-05-15T09:00:00+09:00" → tz 정보 사용
    - "2026-05-15T09:00:00" → KST로 해석
    """
    dt = datetime.fromisoformat(iso_str)
    return to_kst(dt)
