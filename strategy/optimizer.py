"""
=============================================================================
strategy/optimizer.py - 전략 파라미터 최적화기
=============================================================================

Optuna를 사용하여 전략 파라미터(이동평균 기간, RSI 임계값, 앙상블 가중치 등)를
자동으로 최적화합니다.

Optuna란?
- 하이퍼파라미터 최적화 프레임워크 (베이지안 최적화 기반)
- 무작위 탐색보다 훨씬 효율적으로 최적 파라미터를 찾음
- 중간에 성능이 나쁜 시도는 자동으로 중단 (Pruning)
- pip install optuna

최적화할 때 주의사항:
1. 과적합 방지: 학습 기간과 검증 기간을 반드시 분리
2. 파라미터 범위: 너무 넓으면 시간 낭비, 너무 좁으면 최적 못 찾음
3. 목적 함수: Sharpe Ratio 또는 Calmar Ratio 권장 (단순 수익률은 위험)
4. 반복 횟수: 최소 100회 이상 권장 (50회 미만은 불충분)
=============================================================================
"""

import numpy as np
import pandas as pd
from typing import Dict, Optional, Callable, Any
from dataclasses import dataclass

try:
    import optuna
    # Optuna 로그 레벨 조정 (너무 많이 출력되므로)
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    OPTUNA_AVAILABLE = True
except ImportError:
    OPTUNA_AVAILABLE = False

from backtest.engine import BacktestEngine, BacktestConfig


@dataclass
class OptimizationResult:
    """최적화 결과"""
    best_params: Dict[str, Any]       # 최적 파라미터
    best_value: float                  # 최적 목적 함수 값
    n_trials: int                      # 시도 횟수
    study_name: str = ""              # 연구 이름
    all_trials: list = None           # 전체 시도 기록


class StrategyOptimizer:
    """
    Optuna 기반 전략 파라미터 최적화기

    사용법:
        optimizer = StrategyOptimizer(df_train, df_test)

        # 기술적 지표 파라미터 최적화
        result = optimizer.optimize_technical(n_trials=100)

        # 앙상블 가중치 최적화
        result = optimizer.optimize_ensemble_weights(n_trials=200)
    """

    def __init__(
        self,
        train_data: pd.DataFrame,
        test_data: Optional[pd.DataFrame] = None,
        initial_capital: float = 10_000_000,
        objective_metric: str = "sharpe"
    ):
        """
        Parameters:
            train_data: 학습용 데이터 (파라미터 탐색에 사용)
            test_data: 검증용 데이터 (과적합 확인용, 최적화에 사용 안 함)
            initial_capital: 백테스트 초기 자본
            objective_metric: 최적화 목표
                - "sharpe": 샤프 비율 최대화 (권장)
                - "calmar": 칼마 비율 (MDD 대비 수익)
                - "return": 총 수익률 (과적합 주의!)
                - "sortino": 소르티노 비율
        """
        self.train_data = train_data
        self.test_data = test_data
        self.initial_capital = initial_capital
        self.objective_metric = objective_metric

    def optimize_technical(self, n_trials: int = 100) -> OptimizationResult:
        """
        기술적 지표 파라미터 최적화

        최적화 대상:
        - RSI 기간 (7~21)
        - RSI 과매수/과매도 기준 (60~80, 20~40)
        - MACD fast/slow/signal 기간
        - 볼린저 밴드 기간 및 표준편차 배수

        Parameters:
            n_trials: 최적화 시도 횟수 (최소 50 권장)

        Returns:
            OptimizationResult
        """
        if not OPTUNA_AVAILABLE:
            raise ImportError("optuna 미설치: pip install optuna")

        def objective(trial):
            # 파라미터 탐색 공간 정의
            rsi_period = trial.suggest_int("rsi_period", 7, 21)
            rsi_oversold = trial.suggest_int("rsi_oversold", 20, 40)
            rsi_overbought = trial.suggest_int("rsi_overbought", 60, 80)
            sma_short = trial.suggest_int("sma_short", 10, 30)
            sma_long = trial.suggest_int("sma_long", 40, 100)

            # 제약 조건: sma_short < sma_long
            if sma_short >= sma_long:
                return float("-inf")

            # 전략 실행
            from analyzers.technical import TechnicalAnalyzer
            from config.settings import TechnicalConfig

            config = TechnicalConfig(
                rsi_period=rsi_period,
                rsi_oversold=rsi_oversold,
                rsi_overbought=rsi_overbought,
                sma_short=sma_short,
                sma_long=sma_long,
            )

            analyzer = TechnicalAnalyzer(config)
            df_analyzed = analyzer.calculate_all(self.train_data)

            # ★ null-check: 데이터 수집 실패 시 크래시 방지
            if df_analyzed is None or df_analyzed.empty:
                return float("-inf")

            signal = analyzer.generate_signal(df_analyzed)

            # 간단한 신호 시리즈 생성 (RSI + MA 기반)
            signals = self._generate_simple_signals(df_analyzed, config)

            # 백테스트 실행
            engine = BacktestEngine(BacktestConfig(
                initial_capital=self.initial_capital
            ))
            result = engine.run(df_analyzed, signals)

            # 목적 함수 값 반환
            return self._get_objective_value(result.metrics)

        # Optuna 스터디 생성 및 실행
        study = optuna.create_study(
            direction="maximize",
            study_name="technical_optimization"
        )
        study.optimize(objective, n_trials=n_trials)

        return OptimizationResult(
            best_params=study.best_params,
            best_value=study.best_value,
            n_trials=n_trials,
            study_name="technical_optimization",
        )

    def optimize_ensemble_weights(self, n_trials: int = 200) -> OptimizationResult:
        """
        앙상블 가중치 최적화

        각 분석 모듈의 가중치를 최적화합니다.
        합계가 1.0이 되도록 Dirichlet 분포 기반으로 탐색합니다.

        Parameters:
            n_trials: 최적화 시도 횟수

        Returns:
            OptimizationResult
        """
        if not OPTUNA_AVAILABLE:
            raise ImportError("optuna 미설치: pip install optuna")

        def objective(trial):
            # 가중치 탐색 (0.05~0.40 범위, 합계 1.0으로 정규화)
            w_technical = trial.suggest_float("w_technical", 0.05, 0.40)
            w_factor = trial.suggest_float("w_factor", 0.05, 0.35)
            w_timeseries = trial.suggest_float("w_timeseries", 0.05, 0.35)
            w_monte = trial.suggest_float("w_monte", 0.05, 0.30)
            w_ml = trial.suggest_float("w_ml", 0.0, 0.25)
            w_sentiment = trial.suggest_float("w_sentiment", 0.0, 0.20)

            # 정규화 (합계 1.0)
            total = w_technical + w_factor + w_timeseries + w_monte + w_ml + w_sentiment
            weights = {
                "technical": w_technical / total,
                "factor": w_factor / total,
                "time_series": w_timeseries / total,
                "monte_carlo": w_monte / total,
                "ml_prediction": w_ml / total,
                "sentiment": w_sentiment / total,
            }

            # 앙상블 전략으로 신호 생성
            from strategy.ensemble import EnsembleStrategy
            ensemble = EnsembleStrategy(weights)

            # 기술적 점수만으로 테스트 (다른 모듈은 Phase 2에서 추가)
            tech_scores = ensemble.technical_score_from_df(self.train_data)
            module_scores = {"technical": tech_scores}

            signals = ensemble.generate_signals(self.train_data, module_scores)

            # 백테스트
            engine = BacktestEngine(BacktestConfig(
                initial_capital=self.initial_capital
            ))
            result = engine.run(self.train_data, signals)

            return self._get_objective_value(result.metrics)

        study = optuna.create_study(
            direction="maximize",
            study_name="ensemble_weights"
        )
        study.optimize(objective, n_trials=n_trials)

        return OptimizationResult(
            best_params=study.best_params,
            best_value=study.best_value,
            n_trials=n_trials,
            study_name="ensemble_weights",
        )

    def _generate_simple_signals(
        self, df: pd.DataFrame, config
    ) -> pd.Series:
        """RSI + 이동평균 기반 간단한 신호 생성"""
        signals = pd.Series(0, index=df.index)

        if "RSI" in df.columns and "SMA_20" in df.columns and "SMA_50" in df.columns:
            # 매수: RSI 과매도에서 반등 + 단기MA > 장기MA
            buy_cond = (
                (df["RSI"] < config.rsi_oversold + 10) &
                (df["Close"] > df["SMA_20"])
            )
            # 매도: RSI 과매수 또는 단기MA < 장기MA
            sell_cond = (
                (df["RSI"] > config.rsi_overbought) |
                (df["Close"] < df["SMA_50"])
            )

            signals[buy_cond] = 1
            signals[sell_cond] = 0

        return signals

    def _get_objective_value(self, metrics) -> float:
        """목적 함수 값 추출"""
        if self.objective_metric == "sharpe":
            return metrics.sharpe_ratio
        elif self.objective_metric == "calmar":
            return metrics.calmar_ratio
        elif self.objective_metric == "return":
            return metrics.total_return
        elif self.objective_metric == "sortino":
            return metrics.sortino_ratio
        else:
            return metrics.sharpe_ratio
