"""
=============================================================================
tests/test_market_halt.py - 시장 정지 감지 회귀 테스트
=============================================================================

실거래 안전성 핵심 모듈의 회귀 방지 테스트.

검증 시나리오:
1. KOSPI -8% 폭락 → CB 1단계 발동 (매수+매도 모두 차단)
2. KOSPI -15% → CB 2단계
3. KOSPI -20% → CB 3단계 (당일 매매 종료)
4. KOSPI -6% → 경고 (매수만 차단)
5. KOSPI -5% → 사이드카 (프로그램매매 차단)
6. 정상 시 → 모두 허용
7. 14:50 이후 CB 미발동
=============================================================================
"""

import pytest
import sys
from pathlib import Path
from datetime import datetime, time as dtime
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from strategy.market_halt_detector import (
    MarketHaltDetector, HaltStatus, MarketHaltState, HaltCheckResult
)


class TestCircuitBreakerKR:
    """한국 서킷브레이커 발동 시나리오"""

    def setup_method(self):
        self.detector = MarketHaltDetector(kis_client=None)
        # 시간을 13:00으로 고정 (CB 컷오프 14:50 이전)
        # ★ market_halt_detector는 datetime.now()와 utils.timezones.now_kst()를
        #   둘 다 사용하므로(Phase 11 C4 타임존 수정), 테스트도 두 곳을 모두 mock해야
        #   CB 발동 시간대(is_cb_window) 판정이 고정된다. now_kst를 mock하지 않으면
        #   실제 벽시계에 의존 → 14:50(KST) 이후 실행 시 CB 테스트가 실패한다.
        self.fake_now = datetime(2026, 5, 10, 13, 0, 0)

    def _mock_kospi_pct(self, pct: float):
        """KOSPI 변동률을 모킹"""
        def mock_fetch(symbol):
            return (pct, 2500.0)
        return mock_fetch

    def test_normal_market_allows_all_trading(self):
        """정상 시장: KOSPI +0.5% → 모두 허용"""
        with patch.object(self.detector, '_fetch_index_pct_yfinance',
                          side_effect=self._mock_kospi_pct(0.5)):
            result = self.detector.check(force_refresh=True)
        assert result.can_trade_new is True
        assert result.can_trade_exit is True

    def test_kospi_minus_3_5_triggers_warning(self):
        """KOSPI -3.5% → 경고 (B 정책: 정보성 경고 — 매매는 허용)"""
        with patch.object(self.detector, '_fetch_index_pct_yfinance',
                          side_effect=self._mock_kospi_pct(-3.5)):
            result = self.detector.check(force_refresh=True)
        # B 정책: 경고(-3~-5%)는 차단하지 않고 정보성 경고만 표시
        assert result.can_trade_new is True, "경고는 매수를 차단하지 않음 (B 정책)"
        assert result.can_trade_exit is True
        assert result.kr_state.status == HaltStatus.WARNING

    def test_kospi_minus_5_triggers_sidecar(self):
        """KOSPI -5% → 사이드카 (B 정책: 표시는 하되 개인 주문은 허용)"""
        with patch.object(self.detector, '_fetch_index_pct_yfinance',
                          side_effect=self._mock_kospi_pct(-5.0)):
            with patch('strategy.market_halt_detector.datetime') as mock_dt, \
                 patch('utils.timezones.now_kst', return_value=self.fake_now):
                mock_dt.now.return_value = self.fake_now
                result = self.detector.check(force_refresh=True)
        # B 정책: 사이드카는 프로그램매매 호가만 정지 — 개인 주문은 차단 안 함
        assert result.can_trade_new is True
        assert result.kr_state.status == HaltStatus.HALT_SIDECAR

    def test_kospi_minus_6_5_still_sidecar(self):
        """KOSPI -6.5% → 사이드카 단계 (B 정책: 개인 주문은 허용)"""
        with patch.object(self.detector, '_fetch_index_pct_yfinance',
                          side_effect=self._mock_kospi_pct(-6.5)):
            with patch('strategy.market_halt_detector.datetime') as mock_dt, \
                 patch('utils.timezones.now_kst', return_value=self.fake_now):
                mock_dt.now.return_value = self.fake_now
                result = self.detector.check(force_refresh=True)
        # B 정책: 사이드카는 개인 단일종목 주문을 차단하지 않음
        assert result.can_trade_new is True
        assert result.kr_state.status == HaltStatus.HALT_SIDECAR

    def test_sidecar_expires_after_5min(self):
        """★ 시한제: 사이드카는 5분 후 자동 해제 (지수가 -5% 아래여도)"""
        from datetime import timedelta
        t0 = datetime(2026, 5, 18, 11, 0, 0)
        # 1) 사이드카 발동
        with patch.object(self.detector, '_fetch_index_pct_yfinance',
                          side_effect=self._mock_kospi_pct(-5.5)):
            with patch('strategy.market_halt_detector.datetime') as mock_dt, \
                 patch('utils.timezones.now_kst', return_value=t0):
                mock_dt.now.return_value = t0
                r1 = self.detector.check(force_refresh=True)
        assert r1.kr_state.status == HaltStatus.HALT_SIDECAR
        # 2) 6분 경과 — 지수는 여전히 -5.5%인데 사이드카 시한(5분)은 만료
        t1 = t0 + timedelta(minutes=6)
        with patch.object(self.detector, '_fetch_index_pct_yfinance',
                          side_effect=self._mock_kospi_pct(-5.5)):
            with patch('strategy.market_halt_detector.datetime') as mock_dt, \
                 patch('utils.timezones.now_kst', return_value=t1):
                mock_dt.now.return_value = t1
                r2 = self.detector.check(force_refresh=True)
        # 사이드카 만료 → WARNING으로 강등, 매수 차단 해제
        assert r2.kr_state.status == HaltStatus.WARNING
        assert r2.can_trade_new is True

    def test_kospi_minus_8_triggers_cb_level_1(self):
        """KOSPI -8% → CB 1단계 발동 (전 종목 차단)"""
        with patch.object(self.detector, '_fetch_index_pct_yfinance',
                          side_effect=self._mock_kospi_pct(-8.5)):
            with patch('strategy.market_halt_detector.datetime') as mock_dt, \
                 patch('utils.timezones.now_kst', return_value=self.fake_now):
                mock_dt.now.return_value = self.fake_now
                result = self.detector.check(force_refresh=True)
        assert result.can_trade_new is False
        assert result.can_trade_exit is False, "CB 1단계 시 매도도 차단"
        assert result.kr_state.status == HaltStatus.HALT_CB_1

    def test_kospi_minus_15_triggers_cb_level_2(self):
        """KOSPI -15% → CB 2단계"""
        with patch.object(self.detector, '_fetch_index_pct_yfinance',
                          side_effect=self._mock_kospi_pct(-15.5)):
            with patch('strategy.market_halt_detector.datetime') as mock_dt, \
                 patch('utils.timezones.now_kst', return_value=self.fake_now):
                mock_dt.now.return_value = self.fake_now
                result = self.detector.check(force_refresh=True)
        assert result.can_trade_new is False
        assert result.can_trade_exit is False
        assert result.kr_state.status == HaltStatus.HALT_CB_2

    def test_kospi_minus_20_triggers_cb_level_3(self):
        """KOSPI -20% → CB 3단계 (당일 매매 종료)"""
        with patch.object(self.detector, '_fetch_index_pct_yfinance',
                          side_effect=self._mock_kospi_pct(-21.0)):
            with patch('strategy.market_halt_detector.datetime') as mock_dt, \
                 patch('utils.timezones.now_kst', return_value=self.fake_now):
                mock_dt.now.return_value = self.fake_now
                result = self.detector.check(force_refresh=True)
        assert result.can_trade_new is False
        assert result.can_trade_exit is False
        assert result.kr_state.status == HaltStatus.HALT_CB_3


class TestCircuitBreakerCutoff:
    """CB 발동 시간대 (14:50 이후 미발동)"""

    def test_after_1450_cb_does_not_trigger(self):
        """14:50 이후엔 -8% 떨어져도 CB 발동 안 함 (단, 경고는 가능)"""
        detector = MarketHaltDetector(kis_client=None)
        fake_now = datetime(2026, 5, 10, 15, 0, 0)  # 15:00

        def mock_pct(symbol):
            return (-8.5, 2300.0)

        with patch.object(detector, '_fetch_index_pct_yfinance', side_effect=mock_pct):
            with patch('strategy.market_halt_detector.datetime') as mock_dt, \
                 patch('utils.timezones.now_kst', return_value=fake_now):
                mock_dt.now.return_value = fake_now
                result = detector.check(force_refresh=True)

        # 14:50 이후라 CB 1/2/3 발동 안 함, 경고 단계로 떨어짐
        assert result.kr_state.status != HaltStatus.HALT_CB_1
        # -8%는 WARNING 임계값(-6%)도 넘으므로 WARNING으로는 표시됨
        assert result.kr_state.status == HaltStatus.WARNING


class TestCircuitBreakerUS:
    """미국 서킷브레이커 (S&P 500)"""

    def test_sp500_minus_7_triggers_cb_level_1(self):
        """S&P 500 -7% → CB 1단계"""
        detector = MarketHaltDetector(kis_client=None)

        def mock_pct(symbol):
            if symbol == "^GSPC":
                return (-7.5, 5000.0)
            return (0.0, 0.0)  # KOSPI는 정상

        with patch.object(detector, '_fetch_index_pct_yfinance', side_effect=mock_pct):
            result = detector.check(force_refresh=True)

        assert result.us_state.status == HaltStatus.HALT_CB_1
        assert result.can_trade_new is False
        assert result.can_trade_exit is False


class TestCaching:
    """캐시 동작 검증"""

    def test_cache_returns_same_result_within_ttl(self):
        """캐시 TTL 내에는 같은 결과 반환 (지수 재조회 안 함)"""
        detector = MarketHaltDetector(kis_client=None)
        call_count = 0

        def mock_pct(symbol):
            nonlocal call_count
            call_count += 1
            return (0.5, 2500.0)

        with patch.object(detector, '_fetch_index_pct_yfinance', side_effect=mock_pct):
            r1 = detector.check(force_refresh=True)
            initial_calls = call_count
            r2 = detector.check(force_refresh=False)  # 캐시 사용
            r3 = detector.check(force_refresh=False)

        assert r1 is r2 is r3, "캐시 내에서는 같은 객체 반환"
        # 첫 호출에서 KR + US 두 번 호출, 이후는 캐시
        assert call_count == initial_calls


class TestStatusSummary:
    """대시보드 API용 요약 반환 검증"""

    def test_summary_includes_required_fields(self):
        """대시보드가 필요한 모든 필드를 포함"""
        detector = MarketHaltDetector(kis_client=None)

        def mock_pct(symbol):
            return (0.5, 2500.0) if symbol == "^KS11" else (1.0, 5000.0)

        with patch.object(detector, '_fetch_index_pct_yfinance', side_effect=mock_pct):
            detector.check(force_refresh=True)

        summary = detector.get_status_summary()
        # 필수 필드
        assert "can_trade_new" in summary
        assert "can_trade_exit" in summary
        assert "kr_status" in summary
        assert "kr_pct" in summary
        assert "us_status" in summary
        assert "us_pct" in summary
        assert "message" in summary

    def test_summary_when_not_checked(self):
        """체크하지 않은 상태에서도 안전한 기본값 반환"""
        detector = MarketHaltDetector(kis_client=None)
        summary = detector.get_status_summary()
        assert summary["can_trade_new"] is True  # 안전 디폴트
        assert summary["can_trade_exit"] is True
        assert summary["checked"] is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
