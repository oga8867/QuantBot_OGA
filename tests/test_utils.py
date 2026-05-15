"""
=============================================================================
tests/test_utils.py - 유틸리티 함수 단위 테스트
=============================================================================

utils/market.py의 시장 판별 및 포지션 접근 함수 테스트.

실행:
    pytest tests/test_utils.py -v
=============================================================================
"""

import pytest
from utils.market import (
    detect_market, is_kr_stock, is_us_stock,
    get_currency, get_position_attr
)


# =============================================================================
# 1. detect_market 테스트
# =============================================================================

class TestDetectMarket:

    def test_kospi_stock(self):
        """코스피(.KS) 종목 → KR"""
        assert detect_market("005930.KS") == "KR"
        assert detect_market("000660.KS") == "KR"

    def test_kosdaq_stock(self):
        """코스닥(.KQ) 종목 → KR"""
        assert detect_market("042700.KQ") == "KR"
        assert detect_market("403870.KQ") == "KR"

    def test_us_stock(self):
        """미국 종목 (접미사 없음) → US"""
        assert detect_market("AAPL") == "US"
        assert detect_market("MSFT") == "US"
        assert detect_market("NVDA") == "US"

    def test_etf(self):
        """미국 ETF → US"""
        assert detect_market("SPY") == "US"
        assert detect_market("QQQ") == "US"


# =============================================================================
# 2. is_kr_stock / is_us_stock 테스트
# =============================================================================

class TestMarketChecks:

    def test_is_kr_stock_true(self):
        assert is_kr_stock("005930.KS") is True
        assert is_kr_stock("042700.KQ") is True

    def test_is_kr_stock_false(self):
        assert is_kr_stock("AAPL") is False

    def test_is_us_stock_true(self):
        assert is_us_stock("AAPL") is True
        assert is_us_stock("NVDA") is True

    def test_is_us_stock_false(self):
        assert is_us_stock("005930.KS") is False


# =============================================================================
# 3. get_currency 테스트
# =============================================================================

class TestGetCurrency:

    def test_kr_stock_krw(self):
        """한국 종목 → KRW"""
        assert get_currency("005930.KS") == "KRW"
        assert get_currency("042700.KQ") == "KRW"

    def test_us_stock_usd(self):
        """미국 종목 → USD"""
        assert get_currency("AAPL") == "USD"
        assert get_currency("SPY") == "USD"


# =============================================================================
# 4. get_position_attr 테스트
# =============================================================================

class TestGetPositionAttr:

    def test_dict_access(self):
        """dict 포지션에서 속성 접근"""
        pos = {"symbol": "AAPL", "quantity": 10, "avg_price": 150.0}
        assert get_position_attr(pos, "symbol") == "AAPL"
        assert get_position_attr(pos, "quantity") == 10
        assert get_position_attr(pos, "avg_price") == 150.0

    def test_object_access(self, mock_position):
        """dataclass 포지션에서 속성 접근"""
        assert get_position_attr(mock_position, "symbol") == "AAPL"
        assert get_position_attr(mock_position, "quantity") == 10
        assert get_position_attr(mock_position, "avg_price") == 150.0

    def test_dict_missing_key_default(self):
        """dict에 없는 키 → default 반환"""
        pos = {"symbol": "AAPL"}
        assert get_position_attr(pos, "quantity", 0) == 0
        assert get_position_attr(pos, "missing", "N/A") == "N/A"

    def test_object_missing_attr_default(self):
        """객체에 없는 속성 → default 반환"""
        class Simple:
            symbol = "TEST"
        obj = Simple()
        assert get_position_attr(obj, "quantity", 0) == 0

    def test_default_is_zero(self):
        """default 미지정 시 0"""
        pos = {"symbol": "AAPL"}
        assert get_position_attr(pos, "nonexistent") == 0
