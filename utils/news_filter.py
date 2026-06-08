"""
=============================================================================
utils/news_filter.py - 뉴스 날짜 필터링 헬퍼
=============================================================================

RSS 피드의 published 필드를 datetime으로 파싱하고,
지정한 기간 이내의 뉴스만 통과시키는 헬퍼.

두 NewsCollector(`collectors/news.py`, `analyzers/news_llm.py`)에서
공통으로 사용하여 코드 중복을 줄인다.

기본 동작:
- max_age_days <= 0  → 필터 비활성 (모든 뉴스 통과 — 하위 호환)
- published 파싱 실패 → True 반환 (안전 폴백: 너무 엄격히 잘라내지 않음)
- 그 외           → (현재 UTC - published) <= max_age_days
=============================================================================
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional


def parse_published_to_dt(
    published_str: str = "",
    published_parsed=None,
) -> Optional[datetime]:
    """
    RSS의 published 정보를 UTC tz-aware datetime으로 변환.

    Parameters:
        published_str:    "Wed, 21 May 2026 14:30:00 GMT" 같은 RFC 822 문자열
        published_parsed: feedparser의 time.struct_time (선호, 더 정확)

    Returns:
        datetime (tz-aware, UTC). 파싱 실패 시 None.
    """
    # 1) feedparser의 published_parsed가 가장 신뢰성 높음
    if published_parsed is not None:
        try:
            return datetime(*published_parsed[:6], tzinfo=timezone.utc)
        except (TypeError, ValueError):
            pass

    # 2) 폴백: RFC 822 문자열 파싱 (Google News RSS의 <pubDate> 형식)
    if not published_str:
        return None
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(published_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError, AttributeError):
        return None


def is_within_days(
    max_age_days: int,
    published_str: str = "",
    published_parsed=None,
) -> bool:
    """
    뉴스가 max_age_days 이내에 발행됐는지.

    - max_age_days <= 0 → True (필터 비활성)
    - 파싱 실패        → True (안전 폴백: 너무 엄격히 잘라내지 않음)
    - 그 외           → (현재 - published) <= max_age_days
    """
    if max_age_days <= 0:
        return True
    dt = parse_published_to_dt(published_str, published_parsed)
    if dt is None:
        return True  # 파싱 실패 = 보수적으로 포함
    now = datetime.now(timezone.utc)
    return (now - dt) <= timedelta(days=max_age_days)
