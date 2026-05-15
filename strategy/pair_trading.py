"""
=============================================================================
strategy/pair_trading.py - 페어 트레이딩 전략
=============================================================================

페어 트레이딩(Pair Trading)이란?
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
두 개의 상관관계가 높은 종목을 동시에 매수/매도하여
시장 방향에 상관없이 수익을 추구하는 시장 중립(Market Neutral) 전략입니다.

핵심 아이디어:
"상관관계가 높은 두 종목의 가격 차이(스프레드)가 벌어지면,
 다시 평균으로 회귀할 것이라고 가정하고 베팅한다."

예시:
  코카콜라 vs 펩시 → 같은 음료 산업, 높은 상관관계
  삼성전자 vs SK하이닉스 → 같은 반도체 산업

  만약 코카콜라가 비정상적으로 많이 올랐다면:
  → 코카콜라 매도(Short) + 펩시 매수(Long)
  → 스프레드가 정상으로 돌아오면 양쪽 모두 청산하여 수익

수학적 기반:
━━━━━━━━━━━━
1. 상관계수(Correlation): 두 종목의 가격 움직임이 얼마나 비슷한지
   - |r| > 0.8 이면 페어로 적합
   
2. 공적분(Cointegration): 두 시계열의 선형 결합이 정상(stationary)인지
   - ADF 검정(Augmented Dickey-Fuller Test)으로 확인
   - p-value < 0.05 이면 공적분 관계 존재
   
3. Z-Score: 스프레드가 평균에서 얼마나 벗어났는지
   - z = (spread - mean) / std
   - |z| > 2.0 이면 진입 신호 (2 표준편차 이탈)
   - |z| < 0.5 이면 청산 신호 (평균 회귀 완료)

장점:
- 시장 방향에 무관한 수익 (Market Neutral)
- 변동성이 높은 시장에서도 안정적
- 분산 효과

단점:
- 상관관계가 깨질 수 있음 (구조적 변화)
- 공매도(Short)가 필요 → 모의매매에서는 가상 처리
- 수수료 2배 (양쪽 모두 거래)

구현 구조:
    PairFinder → 상관관계 높은 페어 후보 탐색
    PairTrader → 스프레드 계산 + Z-Score 기반 매매 신호
=============================================================================
"""

import numpy as np
import pandas as pd
import logging
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass

logger = logging.getLogger("PairTrading")


@dataclass
class PairSignal:
    """
    페어 트레이딩 신호

    속성:
        pair: (종목A, 종목B) 튜플
        z_score: 현재 Z-Score (스프레드 이탈 정도)
        action: "ENTER_LONG_A" / "ENTER_SHORT_A" / "EXIT" / "HOLD"
        spread: 현재 스프레드 값
        correlation: 두 종목의 상관계수
        half_life: 평균 회귀 반감기 (일)
        confidence: 신호 신뢰도 (0~1)
    """
    pair: Tuple[str, str]
    z_score: float
    action: str
    spread: float
    correlation: float
    half_life: float = 0
    confidence: float = 0


class PairFinder:
    """
    페어 후보 탐색기

    종목 리스트에서 상관관계가 높은 페어를 자동으로 찾습니다.

    탐색 기준:
    1. 상관계수 |r| > 0.7
    2. 공적분 p-value < 0.05 (가능한 경우)
    3. 평균 회귀 반감기 < 30일
    """

    def __init__(self, min_correlation: float = 0.7):
        """
        Args:
            min_correlation: 최소 상관계수 기준 (기본 0.7)
        """
        self.min_correlation = min_correlation

    def find_pairs(self, price_data: Dict[str, pd.DataFrame],
                   top_n: int = 5) -> List[Dict]:
        """
        페어 후보 탐색

        여러 종목의 가격 데이터에서 상관관계가 높은 페어를 찾습니다.

        Args:
            price_data: {종목코드: DataFrame(Date, Close)} 딕셔너리
            top_n: 상위 N개 페어 반환

        Returns:
            [{"pair": (A, B), "correlation": float, "half_life": float}, ...]
        """
        symbols = list(price_data.keys())
        if len(symbols) < 2:
            logger.warning("페어 탐색: 최소 2개 종목이 필요합니다")
            return []

        # 종가 데이터프레임 구성 (열: 종목, 행: 날짜)
        close_df = pd.DataFrame()
        for sym in symbols:
            df = price_data[sym]
            if df is not None and len(df) > 20:
                close_df[sym] = df["Close"].values[:min(len(df), 252)]

        if close_df.shape[1] < 2:
            return []

        # 상관행렬 계산
        corr_matrix = close_df.corr()

        # 모든 페어의 상관계수 추출
        pairs = []
        done = set()
        for i, sym_a in enumerate(close_df.columns):
            for j, sym_b in enumerate(close_df.columns):
                if i >= j:
                    continue
                key = (sym_a, sym_b)
                if key in done:
                    continue
                done.add(key)

                corr = corr_matrix.loc[sym_a, sym_b]

                # 최소 상관계수 필터
                if abs(corr) < self.min_correlation:
                    continue

                # 평균 회귀 반감기 계산
                spread = close_df[sym_a] - close_df[sym_b] * (
                    close_df[sym_a].mean() / close_df[sym_b].mean()
                )
                half_life = self._calc_half_life(spread.dropna().values)

                pairs.append({
                    "pair": (sym_a, sym_b),
                    "correlation": round(corr, 4),
                    "half_life": round(half_life, 1),
                })

        # 상관계수 절대값 기준 정렬
        pairs.sort(key=lambda p: abs(p["correlation"]), reverse=True)

        logger.info(f"페어 탐색 완료: {len(pairs)}개 후보 발견 (기준: |r| > {self.min_correlation})")
        return pairs[:top_n]

    def _calc_half_life(self, spread: np.ndarray) -> float:
        """
        평균 회귀 반감기 계산

        반감기(Half-Life)란?
        스프레드가 평균에서 벗어난 후, 절반만큼 회복하는 데 걸리는 기간.
        짧을수록 평균 회귀가 빠름 → 페어 트레이딩에 유리

        계산 방법 (Ornstein-Uhlenbeck 모델):
        dS = θ(μ - S)dt + σdW
        → θ = -log(autocorrelation) / dt
        → half_life = -log(2) / log(θ)

        Args:
            spread: 스프레드 시계열

        Returns:
            반감기 (일 단위), 계산 불가 시 999
        """
        if len(spread) < 10:
            return 999.0

        try:
            # 간단한 AR(1) 회귀: spread_t = α + β * spread_{t-1} + ε
            y = spread[1:]
            x = spread[:-1]

            # β = Cov(x,y) / Var(x)
            beta = np.cov(x, y)[0, 1] / np.var(x) if np.var(x) > 0 else 1

            # β가 0~1 사이여야 평균 회귀
            if beta <= 0 or beta >= 1:
                return 999.0

            # 반감기 = -ln(2) / ln(β)
            half_life = -np.log(2) / np.log(abs(beta))
            return max(0.5, min(half_life, 999.0))

        except Exception:
            return 999.0


class PairTrader:
    """
    페어 트레이딩 실행기

    선택된 페어의 스프레드를 모니터링하고
    Z-Score 기반으로 매매 신호를 생성합니다.

    매매 규칙:
    ━━━━━━━━━━
    Z-Score > +2.0  → 스프레드 과대 → A 매도 + B 매수
    Z-Score < -2.0  → 스프레드 과소 → A 매수 + B 매도
    |Z-Score| < 0.5 → 평균 회귀 완료 → 포지션 청산
    |Z-Score| > 3.0 → 손절 (스프레드 발산 위험)
    """

    def __init__(self, entry_z: float = 2.0, exit_z: float = 0.5,
                 stop_z: float = 3.0, lookback: int = 60):
        """
        Args:
            entry_z: 진입 Z-Score 기준 (기본 2.0 = 2 표준편차)
            exit_z: 청산 Z-Score 기준 (기본 0.5)
            stop_z: 손절 Z-Score 기준 (기본 3.0)
            lookback: 이동 평균/표준편차 계산 기간 (기본 60일)
        """
        self.entry_z = entry_z
        self.exit_z = exit_z
        self.stop_z = stop_z
        self.lookback = lookback

    def generate_signal(self, prices_a: pd.Series,
                        prices_b: pd.Series,
                        pair: Tuple[str, str]) -> PairSignal:
        """
        페어 트레이딩 신호 생성

        두 종목의 가격 데이터로 스프레드를 계산하고
        Z-Score 기반 매매 신호를 반환합니다.

        Args:
            prices_a: 종목 A의 종가 시리즈
            prices_b: 종목 B의 종가 시리즈
            pair: (종목A 코드, 종목B 코드)

        Returns:
            PairSignal 객체
        """
        if len(prices_a) < self.lookback or len(prices_b) < self.lookback:
            return PairSignal(
                pair=pair, z_score=0, action="HOLD",
                spread=0, correlation=0, confidence=0
            )

        # 1. 상관계수 계산
        correlation = prices_a.corr(prices_b)

        # 2. 헤지 비율 계산 (최소자승법)
        # β = Cov(A, B) / Var(B) → A ≈ β * B + α
        hedge_ratio = np.cov(prices_a, prices_b)[0, 1] / np.var(prices_b)

        # 3. 스프레드 계산: spread = A - β * B
        spread = prices_a - hedge_ratio * prices_b

        # 4. Z-Score 계산 (이동 평균/표준편차 기반)
        spread_recent = spread.iloc[-self.lookback:]
        mean = spread_recent.mean()
        std = spread_recent.std()

        if std == 0:
            return PairSignal(
                pair=pair, z_score=0, action="HOLD",
                spread=float(spread.iloc[-1]), correlation=correlation,
                confidence=0
            )

        z_score = (spread.iloc[-1] - mean) / std

        # 5. 매매 신호 판단
        action = "HOLD"
        confidence = 0.0

        if abs(z_score) > self.stop_z:
            # 손절: 스프레드가 3 표준편차 이상 벗어남 → 구조 변화 의심
            action = "EXIT"
            confidence = 0.9

        elif z_score > self.entry_z:
            # 스프레드 과대: A가 B 대비 비쌈
            # → A 매도(Short) + B 매수(Long)
            action = "ENTER_SHORT_A"
            confidence = min(abs(z_score) / 3.0, 1.0)

        elif z_score < -self.entry_z:
            # 스프레드 과소: A가 B 대비 쌈
            # → A 매수(Long) + B 매도(Short)
            action = "ENTER_LONG_A"
            confidence = min(abs(z_score) / 3.0, 1.0)

        elif abs(z_score) < self.exit_z:
            # 평균 회귀 완료 → 기존 포지션 청산
            action = "EXIT"
            confidence = 0.7

        # 반감기 계산
        half_life_calc = PairFinder()._calc_half_life(spread_recent.values)

        return PairSignal(
            pair=pair,
            z_score=round(float(z_score), 3),
            action=action,
            spread=round(float(spread.iloc[-1]), 4),
            correlation=round(float(correlation), 4),
            half_life=round(half_life_calc, 1),
            confidence=round(confidence, 3)
        )

    def get_spread_stats(self, prices_a: pd.Series,
                         prices_b: pd.Series) -> Dict:
        """
        스프레드 통계 (대시보드 표시용)

        Args:
            prices_a, prices_b: 두 종목의 종가 시리즈

        Returns:
            {mean, std, current, z_score, min, max, percentile}
        """
        if len(prices_a) < 20 or len(prices_b) < 20:
            return {}

        hedge_ratio = np.cov(prices_a, prices_b)[0, 1] / np.var(prices_b)
        spread = prices_a - hedge_ratio * prices_b

        recent = spread.iloc[-self.lookback:]
        mean = recent.mean()
        std = recent.std()
        current = float(spread.iloc[-1])
        z = (current - mean) / std if std > 0 else 0

        return {
            "mean": round(float(mean), 4),
            "std": round(float(std), 4),
            "current": round(current, 4),
            "z_score": round(float(z), 3),
            "min": round(float(recent.min()), 4),
            "max": round(float(recent.max()), 4),
            "hedge_ratio": round(float(hedge_ratio), 4),
        }
