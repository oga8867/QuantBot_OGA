"""
=============================================================================
backtest/engine.py - 백테스트 엔진
=============================================================================

과거 데이터로 전략을 시뮬레이션하여 성과를 검증합니다.

백테스트(Backtest)란?
- "만약 과거에 이 전략으로 매매했다면 어떤 결과가 나왔을까?"
- 과거 데이터로 전략의 유효성을 검증하는 시뮬레이션
- 실제 돈을 넣기 전에 반드시 거쳐야 할 단계

★ 백테스트의 함정 (주의사항):
1. 과적합(Overfitting): 과거 데이터에 맞춰서 튜닝하면 미래에 안 먹힘
2. 생존자 편향: 상장폐지된 종목을 제외하면 성과가 부풀려짐
3. 슬리피지 미반영: 실제로는 주문 가격과 체결 가격이 다름
4. 거래비용 무시: 수수료, 세금을 빼면 수익이 크게 줄어듦
5. 미래 정보 사용(Look-ahead bias): 그 시점에 없는 정보를 사용

이 엔진은:
- 벡터 연산(pandas) 기반으로 빠른 시뮬레이션
- 거래비용(수수료 + 슬리피지) 반영
- Walk-Forward 검증 지원 (시계열 교차검증)
=============================================================================
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Callable
from dataclasses import dataclass, field
from .metrics import calculate_all_metrics, PerformanceMetrics


@dataclass
class BacktestConfig:
    """
    백테스트 설정

    Attributes:
        initial_capital: 초기 자본금
        commission: 수수료율 (편도, 예: 0.001 = 0.1%)
        slippage: 슬리피지 (체결 가격 불이익, 예: 0.001 = 0.1%)
        position_size: 1회 투입 비율 (0.0~1.0)
    """
    initial_capital: float = 10_000_000   # 초기 자본
    commission: float = 0.001             # 수수료 0.1% (편도)
    slippage: float = 0.0005              # 슬리피지 0.05%
    position_size: float = 1.0            # 투입 비율 (1.0 = 전액)
    allow_short: bool = False             # 공매도 허용 여부


@dataclass
class BacktestResult:
    """
    백테스트 결과

    Attributes:
        equity_curve: 자산 가치 시계열
        trades: 개별 거래 기록 DataFrame
        metrics: 성과 지표
        signals_df: 신호 + 포지션 DataFrame
    """
    equity_curve: pd.Series = None
    trades: pd.DataFrame = None
    metrics: PerformanceMetrics = None
    signals_df: pd.DataFrame = None
    config: BacktestConfig = None


class BacktestEngine:
    """
    벡터 기반 백테스트 엔진

    사용법:
        engine = BacktestEngine(config)
        result = engine.run(price_data, signals)

    또는 전략 함수를 직접 전달:
        result = engine.run_strategy(price_data, my_strategy_func)
    """

    def __init__(self, config: Optional[BacktestConfig] = None):
        self.config = config or BacktestConfig()

    def run(
        self,
        df: pd.DataFrame,
        signals: pd.Series
    ) -> BacktestResult:
        """
        신호 시리즈로 백테스트 실행

        Parameters:
            df: OHLCV DataFrame (Close 컬럼 필수)
            signals: 매매 신호 시리즈
                - 1 = 매수 (롱 포지션 진입)
                - 0 = 포지션 없음 (청산)
                - -1 = 매도 (숏, allow_short일 때만)

        Returns:
            BacktestResult 객체
        """
        close = df["Close"].copy()
        capital = self.config.initial_capital

        # 신호를 price index에 맞추기
        signals = signals.reindex(close.index).fillna(0)

        # ─── 포지션 변화 감지 (거래 발생 시점) ────────────────────────
        # position_change: 0→1이면 매수, 1→0이면 매도
        position_change = signals.diff().fillna(0)

        # ─── 일별 수익률 계산 (포지션 있을 때만) ──────────────────────
        daily_returns = close.pct_change().fillna(0)

        # 포지션이 있는 날만 수익률 반영
        strategy_returns = daily_returns * signals.shift(1)  # 전일 신호로 오늘 수익

        # ─── 거래비용 차감 ────────────────────────────────────────────
        # 포지션 변경 시마다 수수료 + 슬리피지 발생
        cost_per_trade = self.config.commission + self.config.slippage
        trade_costs = position_change.abs() * cost_per_trade

        # 수익률에서 비용 차감
        net_returns = strategy_returns - trade_costs

        # ─── 자산 곡선(Equity Curve) 생성 ─────────────────────────────
        # 복리 수익: (1 + r1) * (1 + r2) * ... * (1 + rn) * 초기자본
        equity_curve = capital * (1 + net_returns).cumprod()

        # ─── 개별 거래 기록 추출 ──────────────────────────────────────
        trades = self._extract_trades(close, signals, position_change)

        # ─── 성과 지표 계산 ───────────────────────────────────────────
        trade_returns = trades["return"] if not trades.empty else pd.Series(dtype=float)
        metrics = calculate_all_metrics(equity_curve, trade_returns)

        # 결과 조립
        signals_df = pd.DataFrame({
            "Close": close,
            "Signal": signals,
            "Position_Change": position_change,
            "Daily_Return": strategy_returns,
            "Net_Return": net_returns,
            "Equity": equity_curve,
        })

        return BacktestResult(
            equity_curve=equity_curve,
            trades=trades,
            metrics=metrics,
            signals_df=signals_df,
            config=self.config,
        )

    def run_strategy(
        self,
        df: pd.DataFrame,
        strategy_func: Callable[[pd.DataFrame], pd.Series]
    ) -> BacktestResult:
        """
        전략 함수를 직접 전달하여 백테스트

        Parameters:
            df: OHLCV + 지표 DataFrame
            strategy_func: DataFrame을 받아 신호 Series를 반환하는 함수
                예: def my_strategy(df) -> pd.Series of {1, 0, -1}

        Returns:
            BacktestResult
        """
        signals = strategy_func(df)
        return self.run(df, signals)

    def _extract_trades(
        self,
        close: pd.Series,
        signals: pd.Series,
        position_change: pd.Series
    ) -> pd.DataFrame:
        """
        포지션 변화에서 개별 거래 기록 추출

        Returns:
            DataFrame: entry_date, exit_date, entry_price, exit_price,
                      return, holding_days, side
        """
        trades = []
        in_position = False
        entry_date = None
        entry_price = None

        for date, change in position_change.items():
            if change > 0 and not in_position:
                # 매수 진입
                in_position = True
                entry_date = date
                entry_price = close[date]
            elif change < 0 and in_position:
                # 매도 청산
                in_position = False
                exit_price = close[date]
                trade_return = (exit_price / entry_price) - 1

                # 거래 비용 차감
                trade_return -= 2 * (self.config.commission + self.config.slippage)

                trades.append({
                    "entry_date": entry_date,
                    "exit_date": date,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "return": trade_return,
                    "holding_days": (date - entry_date).days,
                    "side": "LONG",
                })

        if not trades:
            return pd.DataFrame(columns=[
                "entry_date", "exit_date", "entry_price",
                "exit_price", "return", "holding_days", "side"
            ])

        return pd.DataFrame(trades)

    def walk_forward(
        self,
        df: pd.DataFrame,
        strategy_func: Callable,
        train_size: int = 252,
        test_size: int = 63,
        step_size: int = 21
    ) -> List[BacktestResult]:
        """
        Walk-Forward 검증 (시계열 교차검증)

        일반 교차검증은 미래 데이터가 학습에 포함될 수 있어서
        시계열에는 사용하면 안 됩니다.
        Walk-Forward는 항상 과거 → 미래 방향으로만 검증합니다.

        방식:
        [========학습========][==테스트==]
                    [========학습========][==테스트==]
                                [========학습========][==테스트==]

        Parameters:
            df: 전체 데이터
            strategy_func: 학습 데이터로 전략을 만들고 신호를 반환하는 함수
            train_size: 학습 기간 (일)
            test_size: 테스트 기간 (일)
            step_size: 이동 간격 (일)

        Returns:
            각 윈도우의 BacktestResult 리스트
        """
        results = []
        n = len(df)

        start = 0
        while start + train_size + test_size <= n:
            # 학습/테스트 분할
            train_end = start + train_size
            test_end = train_end + test_size

            train_data = df.iloc[start:train_end]
            test_data = df.iloc[train_end:test_end]

            # 전략 함수에 학습 데이터 전달 → 테스트 데이터에 적용
            try:
                signals = strategy_func(train_data, test_data)
                result = self.run(test_data, signals)
                results.append(result)
            except Exception as e:
                pass  # 윈도우 실패 시 건너뜀

            start += step_size

        return results
