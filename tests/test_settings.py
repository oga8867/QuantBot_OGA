"""
=============================================================================
tests/test_settings.py - Settings / Config 단위 테스트
=============================================================================

config/settings.py에 정의된 모든 설정 데이터클래스의 기본값,
유효성 검증, 상호 의존성을 테스트합니다.

테스트 카테고리:
1. CapitalConfig - 자본금 설정
2. RiskConfig - 리스크 파라미터
3. DashboardConfig - 대시보드 매직넘버
4. EnsembleConfig - 앙상블 가중치 + validate()
5. Settings - 종합 설정 클래스
6. 상수 리스트 - US_WATCHLIST, KR_WATCHLIST, SECTOR_UNIVERSE

실행:
    pytest tests/test_settings.py -v
=============================================================================
"""

import pytest
from config.settings import (
    CapitalConfig, RiskConfig, DataConfig, TechnicalConfig,
    ScannerConfig, DashboardConfig, EnsembleConfig, Settings,
    US_WATCHLIST, KR_WATCHLIST, SECTOR_UNIVERSE, AVAILABLE_SECTORS,
)


# =============================================================================
# 1. CapitalConfig 테스트
# =============================================================================

class TestCapitalConfig:
    """자본금 설정 데이터클래스 검증"""

    def test_default_values(self):
        """기본값: 1000만원(KRW), available_cash = total_capital"""
        cfg = CapitalConfig()
        assert cfg.total_capital == 10_000_000
        assert cfg.currency == "KRW"
        assert cfg.available_cash == cfg.total_capital

    def test_post_init_fills_available_cash(self):
        """
        available_cash를 지정하지 않으면 __post_init__에서 total_capital로 채움
        """
        cfg = CapitalConfig(total_capital=50_000)
        assert cfg.available_cash == 50_000

    def test_explicit_available_cash(self):
        """available_cash를 명시적으로 지정하면 그 값 유지"""
        cfg = CapitalConfig(total_capital=100_000, available_cash=80_000)
        assert cfg.available_cash == 80_000

    def test_usd_currency(self):
        """USD 통화 설정"""
        cfg = CapitalConfig(total_capital=10_000, currency="USD")
        assert cfg.currency == "USD"


# =============================================================================
# 2. RiskConfig 테스트
# =============================================================================

class TestRiskConfig:
    """리스크 설정 기본값 및 범위 확인"""

    def test_default_values(self):
        """기본 리스크 파라미터 값 확인"""
        cfg = RiskConfig()
        assert cfg.risk_per_trade == 0.02
        assert cfg.max_position_size == 0.10
        assert cfg.max_daily_loss == 0.03
        assert cfg.max_drawdown == 0.15
        assert cfg.max_positions == 10
        assert cfg.sizing_method == "kelly"
        assert cfg.kelly_fraction == 0.5

    def test_risk_reward_ratio(self):
        """리스크/보상 비율 기본값 확인"""
        cfg = RiskConfig()
        assert cfg.risk_reward_ratio == 2.0

    def test_custom_risk_values(self):
        """커스텀 리스크 설정"""
        cfg = RiskConfig(
            risk_per_trade=0.01,
            max_daily_loss=0.05,
            max_drawdown=0.10,
            sizing_method="fixed_ratio"
        )
        assert cfg.risk_per_trade == 0.01
        assert cfg.max_daily_loss == 0.05
        assert cfg.sizing_method == "fixed_ratio"


# =============================================================================
# 3. DashboardConfig 테스트
# =============================================================================

class TestDashboardConfig:
    """대시보드 매직넘버 중앙 관리 확인"""

    def test_default_values(self):
        """모든 기본값이 합리적인 범위인지 확인"""
        cfg = DashboardConfig()
        assert cfg.scanner_cache_ttl == 300        # 5분
        assert cfg.scanner_top_results == 15
        assert cfg.scanner_per_market == 10
        assert cfg.activity_log_max == 50
        assert cfg.signals_log_max == 50
        assert cfg.equity_snapshot_interval == 300  # 5분
        assert cfg.broadcast_interval == 2
        assert cfg.risk_free_rate == 0.035

    def test_positive_intervals(self):
        """시간 간격 값들이 양수인지 확인"""
        cfg = DashboardConfig()
        assert cfg.scanner_cache_ttl > 0
        assert cfg.equity_snapshot_interval > 0
        assert cfg.broadcast_interval > 0

    def test_risk_free_rate_reasonable(self):
        """무위험 수익률이 합리적 범위 (0% ~ 20%)"""
        cfg = DashboardConfig()
        assert 0.0 <= cfg.risk_free_rate <= 0.20


# =============================================================================
# 4. EnsembleConfig 테스트
# =============================================================================

class TestEnsembleConfig:
    """앙상블 가중치 및 validate() 메서드 검증"""

    def test_default_weights_sum_to_one(self):
        """
        기본 가중치 합계 = 1.0

        technical(0.45) + factor(0.35) + sentiment(0.20)
        + time_series(0) + monte_carlo(0) + ml_prediction(0) = 1.0
        """
        cfg = EnsembleConfig()
        total = (cfg.technical + cfg.factor + cfg.time_series +
                 cfg.monte_carlo + cfg.ml_prediction + cfg.sentiment)
        assert abs(total - 1.0) < 0.001

    def test_validate_returns_true_for_default(self):
        """기본 설정은 validate() 통과"""
        cfg = EnsembleConfig()
        assert cfg.validate() is True

    def test_validate_returns_false_for_bad_weights(self):
        """합계 ≠ 1.0인 가중치는 validate() 실패"""
        cfg = EnsembleConfig(technical=0.5, factor=0.5, sentiment=0.5)
        assert cfg.validate() is False

    def test_threshold_defaults(self):
        """매매 임계값 기본값 확인"""
        cfg = EnsembleConfig()
        assert cfg.buy_threshold == 0.2
        assert cfg.sell_threshold == -0.2

    def test_inactive_modules_zero_weight(self):
        """미구현 모듈은 0 가중치"""
        cfg = EnsembleConfig()
        assert cfg.time_series == 0.0
        assert cfg.monte_carlo == 0.0
        assert cfg.ml_prediction == 0.0


# =============================================================================
# 5. Settings 종합 테스트
# =============================================================================

class TestSettings:
    """최상위 Settings 클래스 통합 검증"""

    def test_default_settings(self, default_settings):
        """기본 Settings 객체의 하위 설정 타입 확인"""
        s = default_settings
        assert isinstance(s.capital, CapitalConfig)
        assert isinstance(s.risk, RiskConfig)
        assert isinstance(s.data, DataConfig)
        assert isinstance(s.technical, TechnicalConfig)
        assert isinstance(s.scanner, ScannerConfig)
        assert isinstance(s.ensemble, EnsembleConfig)
        assert isinstance(s.dashboard, DashboardConfig)

    def test_summary_contains_key_info(self, default_settings):
        """summary()에 주요 정보(자본금, 리스크, 낙폭 등) 포함"""
        summary = default_settings.summary()
        assert "자본금" in summary
        assert "리스크" in summary
        assert "낙폭" in summary

    def test_custom_settings(self):
        """커스텀 하위 설정으로 Settings 생성"""
        s = Settings(
            capital=CapitalConfig(total_capital=50_000, currency="USD"),
            risk=RiskConfig(risk_per_trade=0.01)
        )
        assert s.capital.total_capital == 50_000
        assert s.capital.currency == "USD"
        assert s.risk.risk_per_trade == 0.01
        # 나머지는 기본값
        assert s.data.lookback_days == 252


# =============================================================================
# 6. DataConfig / TechnicalConfig / ScannerConfig 기본값 테스트
# =============================================================================

class TestOtherConfigs:
    """나머지 설정 데이터클래스 기본값 확인"""

    def test_data_config_defaults(self):
        """DataConfig 기본값"""
        cfg = DataConfig()
        assert cfg.lookback_days == 252
        assert cfg.cache_ttl == 3600
        assert cfg.request_delay == 0.5

    def test_technical_config_defaults(self):
        """TechnicalConfig: 표준 기술적 분석 파라미터"""
        cfg = TechnicalConfig()
        assert cfg.rsi_period == 14
        assert cfg.rsi_oversold == 30
        assert cfg.rsi_overbought == 70
        assert cfg.macd_fast == 12
        assert cfg.macd_slow == 26
        assert cfg.bb_period == 20
        assert cfg.bb_std == 2.0

    def test_scanner_config_defaults(self):
        """ScannerConfig: 시장 스캐너 기준값"""
        cfg = ScannerConfig()
        assert cfg.volume_surge_moderate == 2.0
        assert cfg.volume_surge_extreme == 3.0
        assert cfg.price_change_daily == 0.05


# =============================================================================
# 7. 상수 리스트 검증
# =============================================================================

class TestWatchlists:
    """종목 리스트 및 섹터 유니버스 기본 무결성 확인"""

    def test_us_watchlist_not_empty(self):
        """미국 감시 종목 리스트가 비어있지 않아야 함"""
        assert len(US_WATCHLIST) > 0

    def test_kr_watchlist_not_empty(self):
        """한국 감시 종목 리스트가 비어있지 않아야 함"""
        assert len(KR_WATCHLIST) > 0

    def test_kr_stocks_have_suffix(self):
        """한국 종목은 .KS 또는 .KQ 접미사를 가져야 함"""
        for symbol in KR_WATCHLIST:
            assert symbol.endswith((".KS", ".KQ")), \
                f"{symbol}은 .KS 또는 .KQ 접미사가 없습니다"

    def test_us_stocks_no_suffix(self):
        """미국 종목은 .KS/.KQ 접미사가 없어야 함"""
        for symbol in US_WATCHLIST:
            assert not symbol.endswith((".KS", ".KQ")), \
                f"{symbol}은 한국 종목 접미사를 가지고 있습니다"

    def test_sector_universe_has_stocks(self):
        """모든 섹터에 종목이 1개 이상 있어야 함"""
        for sector_key, sector_data in SECTOR_UNIVERSE.items():
            assert "stocks" in sector_data, \
                f"{sector_key} 섹터에 'stocks' 키가 없습니다"
            assert len(sector_data["stocks"]) > 0, \
                f"{sector_key} 섹터에 종목이 없습니다"

    def test_available_sectors_matches_universe(self):
        """AVAILABLE_SECTORS == SECTOR_UNIVERSE.keys()"""
        assert set(AVAILABLE_SECTORS) == set(SECTOR_UNIVERSE.keys())

    def test_sector_has_required_fields(self):
        """각 섹터에 name_ko, name_en, icon, stocks 필드 존재"""
        required_fields = ["name_ko", "name_en", "icon", "stocks"]
        for sector_key, sector_data in SECTOR_UNIVERSE.items():
            for field in required_fields:
                assert field in sector_data, \
                    f"{sector_key} 섹터에 '{field}' 필드가 없습니다"
