"""
=============================================================================
backtest/metrics.py - 성능 지표 계산기
=============================================================================

백테스트 또는 실거래 결과에서 전략의 품질을 평가하는 지표들을 계산합니다.

핵심 성능 지표:
┌───────────────┬─────────────────────────────────────────────────┐
│ 지표           │ 의미                                            │
├───────────────┼─────────────────────────────────────────────────┤
│ CAGR          │ 연평균 복합 수익률 (연환산 수익)                │
│ Sharpe Ratio  │ 위험 대비 수익 효율 (높을수록 좋음)             │
│ Sortino Ratio │ 하방 위험 대비 수익 (Sharpe의 개선 버전)        │
│ Max Drawdown  │ 최대 낙폭 (가장 많이 잃은 비율)                 │
│ Win Rate      │ 승률 (수익 거래 / 전체 거래)                    │
│ Profit Factor │ 총이익 / 총손실 (> 1이면 수익)                  │
│ Calmar Ratio  │ CAGR / |MDD| (MDD 대비 수익 효율)              │
└───────────────┴─────────────────────────────────────────────────┘

이 지표들은 "이 전략이 돈을 벌 수 있는가?"를 판단하는 기준입니다.
하나만 보면 안 되고, 여러 지표를 종합적으로 봐야 합니다.
=============================================================================
"""

import numpy as np
import pandas as pd
from typing import Dict, Optional
from dataclasses import dataclass


@dataclass
class PerformanceMetrics:
    """
    전략 성과 지표 종합

    모든 지표를 한 객체에 담아서 비교/출력하기 쉽게 합니다.
    """
    total_return: float = 0.0       # 누적 수익률
    cagr: float = 0.0               # 연평균 복합 수익률
    sharpe_ratio: float = 0.0       # 샤프 비율
    sortino_ratio: float = 0.0      # 소르티노 비율
    max_drawdown: float = 0.0       # 최대 낙폭
    calmar_ratio: float = 0.0       # 칼마 비율
    win_rate: float = 0.0           # 승률
    profit_factor: float = 0.0      # 수익 팩터
    avg_win: float = 0.0            # 평균 수익 (승리 거래)
    avg_loss: float = 0.0           # 평균 손실 (패배 거래)
    total_trades: int = 0           # 총 거래 횟수
    max_consecutive_losses: int = 0 # 최대 연속 손실
    volatility: float = 0.0         # 연간 변동성
    recovery_time: int = 0          # MDD 회복 기간 (일)

    def to_dict(self) -> Dict:
        """딕셔너리로 변환"""
        return {
            "총 수익률": f"{self.total_return*100:.2f}%",
            "CAGR": f"{self.cagr*100:.2f}%",
            "샤프 비율": f"{self.sharpe_ratio:.2f}",
            "소르티노 비율": f"{self.sortino_ratio:.2f}",
            "최대 낙폭 (MDD)": f"{self.max_drawdown*100:.2f}%",
            "칼마 비율": f"{self.calmar_ratio:.2f}",
            "승률": f"{self.win_rate*100:.1f}%",
            "수익 팩터": f"{self.profit_factor:.2f}",
            "평균 수익": f"{self.avg_win*100:.2f}%",
            "평균 손실": f"{self.avg_loss*100:.2f}%",
            "총 거래 수": self.total_trades,
            "최대 연속 손실": self.max_consecutive_losses,
            "연간 변동성": f"{self.volatility*100:.2f}%",
        }

    def summary(self) -> str:
        """요약 문자열"""
        lines = [f"  {k}: {v}" for k, v in self.to_dict().items()]
        return "=== 성과 지표 ===\n" + "\n".join(lines)


def calculate_returns(equity_curve: pd.Series) -> pd.Series:
    """
    자산 곡선에서 일별 수익률 계산

    Parameters:
        equity_curve: 자산 가치 시계열 (예: 시작 10000 → 종료 12000)

    Returns:
        일별 수익률 시리즈 (예: 0.01 = 1% 상승)
    """
    return equity_curve.pct_change().dropna()


def total_return(equity_curve: pd.Series) -> float:
    """
    누적 수익률

    공식: (최종 자산 - 초기 자산) / 초기 자산

    Parameters:
        equity_curve: 자산 가치 시계열

    Returns:
        누적 수익률 (0.5 = 50% 수익)
    """
    if len(equity_curve) < 2 or equity_curve.iloc[0] == 0:
        return 0.0
    result = (equity_curve.iloc[-1] / equity_curve.iloc[0]) - 1
    return 0.0 if np.isnan(result) else result


def cagr(equity_curve: pd.Series, trading_days: int = 252) -> float:
    """
    CAGR (Compound Annual Growth Rate, 연평균 복합 수익률)

    단순 수익률과 달리, 복리 효과를 반영한 연간 수익률입니다.
    "매년 평균 몇 % 복리로 성장했는가?"에 대한 답.

    공식: CAGR = (최종/초기)^(252/거래일수) - 1
          252 = 1년 영업일 수

    예: 2년간 50% 수익 → CAGR = 1.5^(1/2) - 1 = 22.5%
        (매년 22.5%씩 복리로 2년 → 50% 수익)

    Parameters:
        equity_curve: 자산 가치 시계열
        trading_days: 연간 거래일 수 (미국 252, 한국 250)

    Returns:
        CAGR (0.1 = 연 10%)
    """
    if len(equity_curve) < 2 or equity_curve.iloc[0] <= 0:
        return 0.0

    total = equity_curve.iloc[-1] / equity_curve.iloc[0]
    n_days = len(equity_curve)

    if total <= 0 or n_days <= 0:
        return 0.0

    # 연환산: (총수익비)^(252/전체일수) - 1
    result = total ** (trading_days / n_days) - 1
    return 0.0 if np.isnan(result) else result


def sharpe_ratio(
    returns: pd.Series,
    risk_free_rate: float = 0.03,
    trading_days: int = 252
) -> float:
    """
    Sharpe Ratio (샤프 비율)

    "리스크 1단위당 초과수익이 얼마인가?"를 측정합니다.
    높을수록 효율적으로 돈을 벌고 있다는 뜻.

    공식: Sharpe = (연간수익률 - 무위험수익률) / 연간변동성

    해석:
    - < 0: 무위험 자산보다 못함 (쓸모없는 전략)
    - 0~1: 보통
    - 1~2: 좋음
    - > 2: 매우 우수 (헤지펀드 수준)
    - > 3: 의심스러움 (과적합 가능성)

    Parameters:
        returns: 일별 수익률 시리즈
        risk_free_rate: 무위험 수익률 (연간, 기본 3%)
        trading_days: 연간 거래일

    Returns:
        샤프 비율
    """
    if returns.empty or returns.std() == 0:
        return 0.0

    # 연간화
    annual_return = returns.mean() * trading_days
    annual_vol = returns.std() * np.sqrt(trading_days)

    if annual_vol == 0:
        return 0.0

    return (annual_return - risk_free_rate) / annual_vol


def sortino_ratio(
    returns: pd.Series,
    risk_free_rate: float = 0.03,
    trading_days: int = 252
) -> float:
    """
    Sortino Ratio (소르티노 비율)

    Sharpe 비율의 개선 버전. 변동성 대신 "하방 변동성"만 사용합니다.
    상승 변동성은 좋은 것이므로, 하락할 때의 변동만 리스크로 봅니다.

    공식: Sortino = (연간수익률 - 무위험) / 하방변동성

    Sharpe vs Sortino:
    - Sharpe: 상승/하락 모두 리스크로 간주 → 보수적
    - Sortino: 하락만 리스크 → 더 공정한 평가

    Parameters:
        returns: 일별 수익률
        risk_free_rate: 무위험 수익률
        trading_days: 연간 거래일

    Returns:
        소르티노 비율
    """
    if returns.empty:
        return 0.0

    annual_return = returns.mean() * trading_days

    # 하방 변동성: 음수 수익률만으로 표준편차 계산
    downside = returns[returns < 0]
    if downside.empty:
        return float("inf")  # 손실이 한번도 없으면 무한대

    downside_vol = downside.std() * np.sqrt(trading_days)

    if downside_vol == 0:
        return 0.0

    return (annual_return - risk_free_rate) / downside_vol


def max_drawdown(equity_curve: pd.Series) -> float:
    """
    Maximum Drawdown (최대 낙폭)

    고점에서 저점까지 최대 얼마나 떨어졌는지를 측정합니다.
    "최악의 경우 얼마를 잃을 수 있는가?"에 대한 답.

    공식:
        Peak = 현재까지의 최고점 (누적 최대값)
        Drawdown = (현재 - Peak) / Peak
        MDD = min(모든 Drawdown)

    예: 자산이 100 → 150 → 90으로 변했다면
        MDD = (90 - 150) / 150 = -40%

    Parameters:
        equity_curve: 자산 가치 시계열

    Returns:
        MDD (음수, 예: -0.15 = -15%)
    """
    if len(equity_curve) < 2:
        return 0.0

    # 누적 최고점 (running maximum)
    peak = equity_curve.cummax()

    # 각 시점에서의 낙폭
    drawdown = (equity_curve - peak) / peak

    return drawdown.min()


def max_drawdown_duration(equity_curve: pd.Series) -> int:
    """
    MDD 회복 기간: 고점에서 다시 고점으로 돌아오는 데 걸린 최대 기간

    Parameters:
        equity_curve: 자산 가치 시계열

    Returns:
        최대 회복 기간 (영업일 수)
    """
    if len(equity_curve) < 2:
        return 0

    peak = equity_curve.cummax()
    in_drawdown = equity_curve < peak

    # 연속된 drawdown 구간의 길이 계산
    max_duration = 0
    current_duration = 0

    for is_dd in in_drawdown:
        if is_dd:
            current_duration += 1
            max_duration = max(max_duration, current_duration)
        else:
            current_duration = 0

    return max_duration


def win_rate(trade_returns: pd.Series) -> float:
    """
    승률: 수익 거래의 비율

    Parameters:
        trade_returns: 각 거래의 수익률 시리즈

    Returns:
        승률 (0~1, 예: 0.6 = 60%)
    """
    if trade_returns.empty:
        return 0.0
    return (trade_returns > 0).mean()


def profit_factor(trade_returns: pd.Series) -> float:
    """
    Profit Factor (수익 팩터)

    총 수익(이긴 거래 합) / 총 손실(진 거래 합)
    1보다 크면 전체적으로 수익, 1보다 작으면 손실.

    해석:
    - < 1.0: 손실 전략
    - 1.0~1.5: 약한 수익
    - 1.5~2.0: 좋은 전략
    - > 2.0: 매우 우수 (또는 과적합 의심)

    Parameters:
        trade_returns: 각 거래의 수익률

    Returns:
        수익 팩터 (0이면 이익 없음)
    """
    gains = trade_returns[trade_returns > 0].sum()
    losses = abs(trade_returns[trade_returns < 0].sum())

    if losses == 0:
        return float("inf") if gains > 0 else 0.0

    return gains / losses


def max_consecutive_losses(trade_returns: pd.Series) -> int:
    """
    최대 연속 손실 횟수

    연속으로 몇 번까지 질 수 있는지를 보여줍니다.
    심리적 인내력과 자본 관리에 중요한 지표.

    Parameters:
        trade_returns: 각 거래의 수익률

    Returns:
        최대 연속 손실 수
    """
    if trade_returns.empty:
        return 0

    max_streak = 0
    current_streak = 0

    for ret in trade_returns:
        if ret < 0:
            current_streak += 1
            max_streak = max(max_streak, current_streak)
        else:
            current_streak = 0

    return max_streak


def calculate_all_metrics(
    equity_curve: pd.Series,
    trade_returns: Optional[pd.Series] = None,
    risk_free_rate: float = 0.03
) -> PerformanceMetrics:
    """
    모든 성과 지표를 한번에 계산

    Parameters:
        equity_curve: 자산 가치 시계열
        trade_returns: 개별 거래 수익률 (없으면 일별 수익률 사용)
        risk_free_rate: 무위험 수익률

    Returns:
        PerformanceMetrics 객체
    """
    returns = calculate_returns(equity_curve)
    trades = trade_returns if trade_returns is not None else returns

    mdd = max_drawdown(equity_curve)
    annual_ret = cagr(equity_curve)

    metrics = PerformanceMetrics(
        total_return=total_return(equity_curve),
        cagr=annual_ret,
        sharpe_ratio=sharpe_ratio(returns, risk_free_rate),
        sortino_ratio=sortino_ratio(returns, risk_free_rate),
        max_drawdown=mdd,
        calmar_ratio=annual_ret / abs(mdd) if mdd != 0 else 0.0,
        win_rate=win_rate(trades),
        profit_factor=profit_factor(trades),
        avg_win=trades[trades > 0].mean() if (trades > 0).any() else 0.0,
        avg_loss=trades[trades < 0].mean() if (trades < 0).any() else 0.0,
        total_trades=len(trades),
        max_consecutive_losses=max_consecutive_losses(trades),
        volatility=returns.std() * np.sqrt(252) if not returns.empty else 0.0,
        recovery_time=max_drawdown_duration(equity_curve),
    )

    return metrics
