"""
=============================================================================
strategy/capital_allocator.py - 다중 전략 자본 분할 (Risk Parity)
=============================================================================

기존 봇은 자본 100%를 단일 앙상블 신호로 운용했습니다.
이 모듈은 자본을 여러 전략에 나눠 운용하여 분산 효과를 얻습니다.

기본 분할 (Risk Parity 기반):
- 모멘텀(Momentum)        : 50% — 추세 추종, 장기 우상향에 강함
- 평균회귀(Mean Reversion) : 30% — 횡보장에서 효과적
- 팩터(Factor)            : 20% — 가치/품질 점수 기반

학술적 근거:
- DeMiguel et al. (2009) "Optimal versus Naive Diversification"
  → 1/N 단순 분산이 마코위츠 평균-분산 최적화보다 out-of-sample 우월
- AQR "Understanding Risk Parity" (2012)
  → 각 자산이 동일 위험 기여도 → 샤프비 최대화
- Two Sigma 멀티 전략 모델 → 600+ 박사급이 운용하는 표준
- Bridgewater All Weather → 4개 경제 국면별 자산 배분

Risk Parity 산식:
    weight_i = (1/sigma_i) / sum(1/sigma_j)
    여기서 sigma_i = 전략 i의 변동성

장점:
- 단일 전략 의존 금지: 한 전략이 부진해도 다른 전략이 보완
- 시장 체제별로 잘하는 전략이 자동 보완
- 자본 효율성: 전체 자본의 일부만 위험에 노출

주의사항:
- 거래비용 ↑ (다중 신호 = 다중 매매)
- 같은 종목에 다른 신호가 충돌할 수 있음 → conflict 해결 필요
=============================================================================
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Literal
from datetime import datetime

logger = logging.getLogger(__name__)


StrategyName = Literal["momentum", "mean_reversion", "factor", "ensemble"]


@dataclass
class StrategyAllocation:
    """단일 전략의 자본 할당 정보"""
    name: StrategyName
    weight: float                  # 자본 비중 (0.0 ~ 1.0)
    capital: float                 # 할당된 자본 (KRW)
    used: float = 0.0              # 사용 중인 자본 (포지션 평가액)
    realized_pnl: float = 0.0      # 누적 실현 손익
    trade_count: int = 0
    win_count: int = 0

    @property
    def available(self) -> float:
        """사용 가능한 잔여 자본"""
        return max(0, self.capital - self.used)

    @property
    def utilization(self) -> float:
        """자본 활용률 (0~1)"""
        return self.used / self.capital if self.capital > 0 else 0


class CapitalAllocator:
    """
    다중 전략 자본 분할 관리자

    각 전략이 독립적으로 신호를 생성 → CapitalAllocator가 자본 할당 → 매매 실행

    사용법:
        alloc = CapitalAllocator(total_capital=10_000_000)
        alloc.set_weights({"momentum": 0.5, "mean_reversion": 0.3, "factor": 0.2})

        # 매수 결정 시 어느 전략이 진입할지 선택
        strategy = alloc.pick_strategy(symbol, signals)
        if strategy:
            shares = alloc.calculate_shares(strategy, price)
            execute_buy(symbol, shares, strategy=strategy)

    Risk Parity 모드:
        alloc.use_risk_parity(volatilities)  # {"momentum": 0.20, ...}
        # weights = {"momentum": 0.X, ...} 자동 계산
    """

    DEFAULT_WEIGHTS = {
        "momentum": 0.50,
        "mean_reversion": 0.30,
        "factor": 0.20,
    }

    def __init__(self, total_capital: float = 10_000_000):
        self.total_capital = total_capital
        self.allocations: Dict[StrategyName, StrategyAllocation] = {}
        self.set_weights(self.DEFAULT_WEIGHTS)

    def set_weights(self, weights: Dict[str, float]):
        """
        전략별 자본 비중 설정

        Parameters:
            weights: {"momentum": 0.5, ...} 형식 (합계 1.0)
        """
        total = sum(weights.values())
        if abs(total - 1.0) > 0.01:
            logger.warning(
                f"[자본분할] 가중치 합계 {total:.3f} ≠ 1.0 → 자동 정규화"
            )
            weights = {k: v / total for k, v in weights.items()}

        # 기존 사용량은 유지하면서 capital만 재계산
        existing_used = {k: a.used for k, a in self.allocations.items()}
        existing_pnl = {k: a.realized_pnl for k, a in self.allocations.items()}
        existing_trades = {k: (a.trade_count, a.win_count) for k, a in self.allocations.items()}

        self.allocations = {}
        for strat, w in weights.items():
            self.allocations[strat] = StrategyAllocation(
                name=strat,
                weight=w,
                capital=self.total_capital * w,
                used=existing_used.get(strat, 0),
                realized_pnl=existing_pnl.get(strat, 0),
                trade_count=existing_trades.get(strat, (0, 0))[0],
                win_count=existing_trades.get(strat, (0, 0))[1],
            )

        logger.info(
            f"[자본분할] 설정: " +
            ", ".join(
                f"{s}={a.weight:.0%}({a.capital:,.0f}원)"
                for s, a in self.allocations.items()
            )
        )

    def use_risk_parity(self, volatilities: Dict[str, float]):
        """
        Risk Parity 기반 자동 가중치 계산

        weight_i = (1/sigma_i) / sum(1/sigma_j)

        Parameters:
            volatilities: {"momentum": 0.20, "mean_reversion": 0.15, ...}
                          연환산 변동성 (일별 std × sqrt(252))
        """
        if not volatilities:
            return

        # 역변동성 가중치
        inv_vols = {k: 1.0 / max(v, 0.01) for k, v in volatilities.items()}
        total_inv = sum(inv_vols.values())
        weights = {k: iv / total_inv for k, iv in inv_vols.items()}

        logger.info(
            f"[Risk Parity] 변동성 기반 자동 분할: " +
            ", ".join(f"{k}={w:.1%}" for k, w in weights.items())
        )
        self.set_weights(weights)

    def pick_strategy(
        self,
        symbol: str,
        strategy_signals: Dict[str, str],
        strategy_scores: Optional[Dict[str, float]] = None,
    ) -> Optional[str]:
        """
        같은 종목에 여러 전략이 신호를 보낼 때 어떤 전략이 매수할지 결정

        규칙:
        1. BUY 신호를 보낸 전략 중에서만 선택
        2. 각 전략의 사용 가능 자본 + 점수를 종합 점수화
        3. 가장 높은 점수의 전략 선택 (없으면 None)

        Parameters:
            symbol: 종목코드
            strategy_signals: {"momentum": "BUY", "mean_reversion": "HOLD", ...}
            strategy_scores: {"momentum": 0.4, ...} 신호 점수 (옵션)

        Returns:
            선택된 전략명 또는 None (모두 HOLD/매수 불가)
        """
        candidates = []
        for strat, signal in strategy_signals.items():
            if signal != "BUY":
                continue
            alloc = self.allocations.get(strat)
            if not alloc or alloc.available <= 0:
                continue
            score = strategy_scores.get(strat, 0.5) if strategy_scores else 0.5
            # 종합 점수 = 신호 점수 × 자본 활용 여유
            combined = score * (1 - alloc.utilization)
            candidates.append((strat, combined, alloc.available))

        if not candidates:
            return None

        # 점수 + 가용 자본 가중치
        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates[0][0]

    def calculate_shares(
        self, strategy: str, price: float, position_pct: float = 0.10
    ) -> int:
        """
        전략의 가용 자본 내에서 매수할 주식 수 계산

        Parameters:
            strategy: 전략명
            price: 현재가
            position_pct: 전략 자본 중 이번 매수에 투입할 비율 (기본 10%)

        Returns:
            매수 주식 수 (정수, 1주 단위)
        """
        alloc = self.allocations.get(strategy)
        if not alloc or alloc.available <= 0 or price <= 0:
            return 0
        # 1회 진입 금액 = 가용 자본 × position_pct
        per_trade = alloc.available * position_pct
        return int(per_trade / price)

    def reserve(self, strategy: str, amount: float):
        """매수 시 자본 사용량 증가"""
        alloc = self.allocations.get(strategy)
        if alloc:
            alloc.used += amount

    def release(self, strategy: str, amount: float, pnl: float = 0.0, win: bool = False):
        """매도 시 자본 반환 + 손익 기록"""
        alloc = self.allocations.get(strategy)
        if alloc:
            alloc.used = max(0, alloc.used - amount)
            alloc.realized_pnl += pnl
            alloc.trade_count += 1
            if win:
                alloc.win_count += 1

    def get_summary(self) -> Dict:
        """전체 분할 현황 요약 (대시보드/리포트용)"""
        return {
            "total_capital": self.total_capital,
            "strategies": [
                {
                    "name": a.name,
                    "weight": a.weight,
                    "capital": a.capital,
                    "used": a.used,
                    "available": a.available,
                    "utilization_pct": a.utilization * 100,
                    "realized_pnl": a.realized_pnl,
                    "trade_count": a.trade_count,
                    "win_rate_pct": (a.win_count / a.trade_count * 100) if a.trade_count > 0 else 0,
                }
                for a in self.allocations.values()
            ],
        }


# ── 모멘텀 전략 시그널 헬퍼 ──
def momentum_signal(df, ma_short: int = 20, ma_long: int = 50) -> str:
    """
    모멘텀 추세 추종 신호

    BUY: 단기 MA > 장기 MA + 상승 가속
    SELL: 단기 MA < 장기 MA
    HOLD: 그 외
    """
    if df is None or len(df) < ma_long + 5:
        return "HOLD"
    close = df["Close"]
    ma_s = close.rolling(ma_short).mean().iloc[-1]
    ma_l = close.rolling(ma_long).mean().iloc[-1]
    momentum = (close.iloc[-1] / close.iloc[-20] - 1) if len(close) > 20 else 0

    if ma_s > ma_l * 1.01 and momentum > 0.02:
        return "BUY"
    elif ma_s < ma_l * 0.99:
        return "SELL"
    return "HOLD"


# ── 평균회귀 전략 시그널 헬퍼 ──
def mean_reversion_signal(df, rsi_buy: float = 30, rsi_sell: float = 70) -> str:
    """
    평균회귀 신호 (RSI 과매수/과매도)

    BUY: RSI < 30 (과매도 → 반등 기대)
    SELL: RSI > 70 (과매수 → 조정 기대)
    HOLD: 그 외
    """
    if df is None or "RSI" not in df.columns or len(df) < 1:
        return "HOLD"
    rsi = float(df["RSI"].iloc[-1])
    if rsi < rsi_buy:
        return "BUY"
    elif rsi > rsi_sell:
        return "SELL"
    return "HOLD"
