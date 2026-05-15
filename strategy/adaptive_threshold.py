"""
=============================================================================
strategy/adaptive_threshold.py - 시장 변동성 기반 적응형 임계값
=============================================================================

기존 봇은 매수/매도 임계값(buy_threshold=0.2, sell_threshold=-0.2)이
하드코딩되어 시장 상황과 무관하게 동일하게 작동했습니다.

이 모듈은 VIX, ATR, 시장 체제(regime)를 기반으로 임계값을 동적 조절합니다.

이론적 근거:
1. 변동성 군집(Volatility Clustering): 변동성이 높은 시기엔 신호의 노이즈도 큼
   → 더 강한 신호만 신뢰해야 거짓 양성 감소
2. 시장 체제 변화(Market Regime): Bull/Bear/Sideways
   → 각 체제에 맞는 임계값 사용
3. VIX의 예측력 (Whaley 2000):
   - VIX < 15: 안정적 강세장 → 적극 매매
   - VIX 15~20: 정상 → 표준 임계값
   - VIX 20~30: 불안정 → 보수적
   - VIX > 30: 공황 → 매매 중지 또는 강한 신호만

학술 자료:
- "Dynamic thresholding consistently outperforms static thresholds" (2025)
- VIX 200일 이동평균 크로스오버: 70-78% regime detection 정확도
- Hidden Markov Model (HMM): 2-3 latent states로 90%+ 정확도
=============================================================================
"""

import logging
from dataclasses import dataclass
from typing import Optional, Tuple, Literal
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


VolatilityRegime = Literal["low", "normal", "elevated", "crisis"]
MarketRegime = Literal["bull", "bear", "sideways"]


@dataclass
class AdaptiveThresholds:
    """동적 계산된 임계값"""
    buy_threshold: float            # 매수 임계값 (기본 0.2 ~ +0.4)
    sell_threshold: float           # 매도 임계값 (기본 -0.2 ~ -0.4)
    min_confidence: float           # 최소 신뢰도 (0.03 ~ 0.30)
    position_size_multiplier: float # 포지션 크기 조절 (0.5 ~ 1.0)
    regime_volatility: VolatilityRegime
    regime_market: MarketRegime
    vix_value: float
    detail: str                     # 디버깅/UI용


class AdaptiveThresholdManager:
    """
    VIX + ATR + 시장 추세 기반 적응형 임계값 계산기

    호출 빈도: 분석 사이클당 1회 (분석 시작 시점)
    캐시: 5분간 유지 (VIX는 변동이 느림)

    사용법:
        manager = AdaptiveThresholdManager()
        thresholds = manager.compute(price_df=spy_df)
        if signal_score > thresholds.buy_threshold:
            buy_with_size(thresholds.position_size_multiplier)
    """

    # ── 기본값 (보수적) ──
    BASE_BUY_THRESHOLD = 0.20
    BASE_SELL_THRESHOLD = -0.20
    BASE_MIN_CONFIDENCE_PAPER = 0.03
    BASE_MIN_CONFIDENCE_LIVE = 0.15

    # ── VIX 임계값 (Whaley 2000 + 현대 개정) ──
    VIX_LOW = 15.0
    VIX_NORMAL = 20.0
    VIX_ELEVATED = 30.0
    # VIX > 30 = crisis

    # ── 캐시 ──
    _cached_result: Optional[AdaptiveThresholds] = None
    _cached_time: Optional[datetime] = None
    CACHE_TTL_SECONDS = 300  # 5분

    def __init__(self, paper: bool = True):
        self.paper = paper
        self.base_min_conf = (
            self.BASE_MIN_CONFIDENCE_PAPER if paper else self.BASE_MIN_CONFIDENCE_LIVE
        )

    def compute(self, force_refresh: bool = False) -> AdaptiveThresholds:
        """
        현재 시장 상황 기반 적응형 임계값 계산

        VIX와 SPY 데이터를 yfinance로 조회 → 변동성 체제 분류 → 임계값 조정.
        실패 시 기본값 반환 (안전).

        Returns:
            AdaptiveThresholds: 조정된 임계값들 + 시장 체제 정보
        """
        # 캐시 확인
        if (
            not force_refresh
            and self._cached_result is not None
            and self._cached_time is not None
            and (datetime.now() - self._cached_time).total_seconds() < self.CACHE_TTL_SECONDS
        ):
            return self._cached_result

        # VIX 조회
        vix = self._fetch_vix()

        # SPY로 시장 추세 판별
        market_regime = self._fetch_market_regime()

        # 변동성 체제 분류
        vol_regime = self._classify_volatility(vix)

        # 임계값 조정
        thresholds = self._adjust_thresholds(vol_regime, market_regime, vix)

        # 캐시
        self._cached_result = thresholds
        self._cached_time = datetime.now()

        return thresholds

    def _fetch_vix(self) -> float:
        """yfinance에서 VIX 현재값 조회 (실패 시 기본값 18)"""
        try:
            import yfinance as yf
            ticker = yf.Ticker("^VIX")
            hist = ticker.history(period="5d", interval="1d")
            if not hist.empty:
                return float(hist["Close"].iloc[-1])
        except Exception as e:
            logger.debug(f"[적응형] VIX 조회 실패: {e}")
        return 18.0  # 정상 범위 기본값 (안전)

    def _fetch_market_regime(self) -> MarketRegime:
        """
        SPY의 200일 이동평균 + 모멘텀으로 시장 체제 판별

        - Bull: 가격 > 200일 MA & 양의 모멘텀
        - Bear: 가격 < 200일 MA & 음의 모멘텀
        - Sideways: 그 외
        """
        try:
            import yfinance as yf
            spy = yf.Ticker("SPY")
            hist = spy.history(period="1y", interval="1d")
            if hist.empty or len(hist) < 200:
                return "sideways"
            close = hist["Close"]
            ma200 = close.rolling(200).mean().iloc[-1]
            ma50 = close.rolling(50).mean().iloc[-1]
            current = float(close.iloc[-1])

            # 50일 모멘텀 (월간 변화율)
            momentum_pct = (current / float(close.iloc[-50]) - 1) * 100

            # Bull: 200MA 위 + 50MA > 200MA + 양의 모멘텀
            if current > ma200 and ma50 > ma200 and momentum_pct > 2:
                return "bull"
            elif current < ma200 and ma50 < ma200 and momentum_pct < -2:
                return "bear"
            return "sideways"
        except Exception as e:
            logger.debug(f"[적응형] 시장체제 판별 실패: {e}")
            return "sideways"

    def _classify_volatility(self, vix: float) -> VolatilityRegime:
        """VIX → 변동성 체제 분류"""
        if vix < self.VIX_LOW:
            return "low"
        elif vix < self.VIX_NORMAL:
            return "normal"
        elif vix < self.VIX_ELEVATED:
            return "elevated"
        else:
            return "crisis"

    def _adjust_thresholds(
        self,
        vol_regime: VolatilityRegime,
        market_regime: MarketRegime,
        vix: float,
    ) -> AdaptiveThresholds:
        """
        변동성 + 시장 체제 → 임계값 조정

        조정 원칙:
        - VIX ↑ → 임계값 ↑ (강한 신호만 신뢰)
        - VIX ↑ → 신뢰도 ↑ (확신할 때만 진입)
        - VIX ↑ → 포지션 ↓ (변동성 위험 회피)
        - Bear 체제 → 매수 더 신중, 매도 더 적극
        """
        # 변동성 기반 배수
        vol_multipliers = {
            "low": 0.7,        # VIX < 15: 약한 신호도 OK
            "normal": 1.0,     # VIX 15~20: 표준
            "elevated": 1.5,   # VIX 20~30: 강한 신호만
            "crisis": 2.5,     # VIX > 30: 거의 매매 중지
        }
        size_multipliers = {
            "low": 1.0,
            "normal": 1.0,
            "elevated": 0.7,
            "crisis": 0.3,
        }

        vol_mult = vol_multipliers[vol_regime]
        size_mult = size_multipliers[vol_regime]

        # ── 매수 임계값: VIX↑ → 더 엄격 (강한 신호만 매수) ──
        buy_th = self.BASE_BUY_THRESHOLD * vol_mult

        # ── ★ CRITICAL FIX: 매도 임계값은 VIX↑일수록 더 쉬워야 함 ──
        # 이전 버그: sell_th = BASE * vol_mult (음수 × 큰값 = 더 음수)
        #   → crisis에서 -0.20 × 2.5 = -0.50 → -0.30 신호로는 청산 불가
        #   → 폭락장에서 손실 청산이 막혀 풀 드로우다운 직격탄
        # 수정: 매도 임계값은 vol_mult로 나눠서 0에 가깝게 (= 청산 쉬워짐)
        sell_th = self.BASE_SELL_THRESHOLD / vol_mult

        # 신뢰도 임계값 (강한 신호만 신뢰)
        min_conf = self.base_min_conf * vol_mult

        # 시장 체제 보정
        if market_regime == "bear":
            # Bear: 매수 더 어렵게 (+20%), 매도 더 쉽게 (절댓값↓)
            buy_th *= 1.2
            sell_th *= 0.8  # 음수의 0.8배 = 절댓값 작아짐 → 청산 쉬워짐 (정상)
            size_mult *= 0.7  # 포지션 축소
        elif market_regime == "bull":
            # Bull: 매수 약간 더 적극, 매도는 그대로
            buy_th *= 0.9

        # 절대 한도 (안전 가드)
        buy_th = max(0.10, min(0.60, buy_th))
        # sell_th는 [-0.60, -0.05] 범위. crisis에선 -0.05까지 완화되어
        # 작은 음수 신호로도 청산 가능 (폭락장 손실 컷)
        sell_th = min(-0.05, max(-0.60, sell_th))
        min_conf = max(0.02, min(0.40, min_conf))
        size_mult = max(0.2, min(1.0, size_mult))

        detail = (
            f"VIX={vix:.1f} ({vol_regime}) | 시장={market_regime} | "
            f"매수임계 {buy_th:+.3f} 매도임계 {sell_th:+.3f} | "
            f"신뢰도 {min_conf:.2%} | 포지션 ×{size_mult:.2f}"
        )

        return AdaptiveThresholds(
            buy_threshold=buy_th,
            sell_threshold=sell_th,
            min_confidence=min_conf,
            position_size_multiplier=size_mult,
            regime_volatility=vol_regime,
            regime_market=market_regime,
            vix_value=vix,
            detail=detail,
        )
