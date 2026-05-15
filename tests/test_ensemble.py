"""
=============================================================================
tests/test_ensemble.py - EnsembleStrategy 단위 테스트
=============================================================================

앙상블 전략은 여러 분석 모듈의 점수를 가중 평균하여 최종 신호를 생성합니다.
이 테스트는 가중치 정규화, 신호 결합, 임계값 판단, DataFrame 기반 신호 생성 등을
검증합니다.

테스트 카테고리:
1. 가중치 정규화 (_validate_weights)
2. 신호 결합 (combine)
3. BUY/SELL/HOLD 임계값 판단
4. DataFrame 기반 신호 생성 (generate_signals)
5. 기술적 점수 변환 (technical_score_from_df)
6. 엣지 케이스

실행:
    pytest tests/test_ensemble.py -v
=============================================================================
"""

import pytest
import numpy as np
import pandas as pd
from strategy.ensemble import EnsembleStrategy, ModuleScore, EnsembleSignal


# =============================================================================
# 1. 가중치 정규화 테스트
# =============================================================================

class TestWeightValidation:
    """가중치 합계가 1.0이 아닐 때 자동 정규화되는지 확인"""

    def test_default_weights_sum_to_one(self, ensemble_strategy):
        """
        기본 EnsembleConfig 가중치 합 = 1.0 확인

        technical(0.45) + factor(0.35) + sentiment(0.20) + 나머지(0.0) = 1.0
        """
        total = sum(ensemble_strategy.weights.values())
        assert abs(total - 1.0) < 0.01

    def test_auto_normalization(self):
        """
        합계가 1.0이 아닌 가중치 → 자동 정규화

        입력: {"a": 2, "b": 3} → 합계 5
        정규화: {"a": 0.4, "b": 0.6}
        """
        es = EnsembleStrategy(weights={"a": 2.0, "b": 3.0})
        assert abs(es.weights["a"] - 0.4) < 0.01
        assert abs(es.weights["b"] - 0.6) < 0.01

    def test_already_normalized_unchanged(self):
        """이미 합계 1.0인 가중치는 변경되지 않아야 함"""
        original = {"x": 0.6, "y": 0.4}
        es = EnsembleStrategy(weights=original.copy())
        assert abs(es.weights["x"] - 0.6) < 0.01
        assert abs(es.weights["y"] - 0.4) < 0.01


# =============================================================================
# 2. 신호 결합 (combine) 테스트
# =============================================================================

class TestCombine:
    """combine() 메서드의 점수 결합 로직 검증"""

    def test_single_module_full_confidence(self):
        """
        단일 모듈 (confidence=1.0) → 점수가 그대로 반영

        technical: score=0.5, weight=1.0, confidence=1.0
        → final_score ≈ 0.5
        """
        es = EnsembleStrategy(weights={"technical": 1.0})
        scores = [
            ModuleScore(name="technical", score=0.5, confidence=1.0,
                        reasons=["RSI 과매도"])
        ]
        signal = es.combine(scores)
        assert abs(signal.score - 0.5) < 0.01

    def test_low_confidence_reduces_impact(self):
        """
        낮은 신뢰도 → 점수 영향력 감소

        모듈 A: score=1.0, confidence=0.1 → contribution = 1.0 × 0.5 × 0.1
        모듈 B: score=0.0, confidence=1.0 → contribution = 0
        최종: A의 영향이 크게 줄어야 함
        """
        es = EnsembleStrategy(weights={"a": 0.5, "b": 0.5})
        scores = [
            ModuleScore(name="a", score=1.0, confidence=0.1, reasons=[]),
            ModuleScore(name="b", score=0.0, confidence=1.0, reasons=[]),
        ]
        signal = es.combine(scores)
        # a의 기여: 1.0 * 0.5 * 0.1 = 0.05
        # b의 기여: 0.0 * 0.5 * 1.0 = 0.0
        # total_weight: 0.5*0.1 + 0.5*1.0 = 0.55
        # final: 0.05 / 0.55 ≈ 0.09
        assert signal.score < 0.15  # 신뢰도 낮은 모듈의 영향 감소 확인

    def test_unknown_module_ignored(self):
        """
        가중치에 없는 모듈 → 무시됨

        "unknown" 모듈은 weights에 없으므로 기여도 = 0
        """
        es = EnsembleStrategy(weights={"technical": 1.0})
        scores = [
            ModuleScore(name="technical", score=0.3, confidence=1.0),
            ModuleScore(name="unknown", score=0.9, confidence=1.0),
        ]
        signal = es.combine(scores)
        # unknown은 무시되므로 technical만 반영
        assert abs(signal.score - 0.3) < 0.01

    def test_all_positive_produces_buy(self):
        """
        모든 모듈이 강한 양의 점수 → BUY 신호

        buy_threshold = 0.2 (EnsembleConfig 기본값)
        """
        es = EnsembleStrategy(weights={"a": 0.5, "b": 0.5})
        scores = [
            ModuleScore(name="a", score=0.8, confidence=1.0),
            ModuleScore(name="b", score=0.6, confidence=1.0),
        ]
        signal = es.combine(scores)
        assert signal.action == "BUY"
        assert signal.score > 0.2

    def test_all_negative_produces_sell(self):
        """모든 모듈이 강한 음의 점수 → SELL 신호"""
        es = EnsembleStrategy(weights={"a": 0.5, "b": 0.5})
        scores = [
            ModuleScore(name="a", score=-0.7, confidence=1.0),
            ModuleScore(name="b", score=-0.5, confidence=1.0),
        ]
        signal = es.combine(scores)
        assert signal.action == "SELL"
        assert signal.score < -0.2

    def test_mixed_scores_produce_hold(self):
        """
        상반된 점수 → HOLD 신호

        모듈이 서로 상쇄하면 점수가 임계값 범위 내에 머무름
        """
        es = EnsembleStrategy(weights={"a": 0.5, "b": 0.5})
        scores = [
            ModuleScore(name="a", score=0.1, confidence=1.0),
            ModuleScore(name="b", score=-0.1, confidence=1.0),
        ]
        signal = es.combine(scores)
        assert signal.action == "HOLD"
        assert abs(signal.score) < 0.2

    def test_components_dict_populated(self):
        """combine() 결과에 모듈별 기여도가 components에 기록됨"""
        es = EnsembleStrategy(weights={"a": 0.6, "b": 0.4})
        scores = [
            ModuleScore(name="a", score=0.5, confidence=0.8),
            ModuleScore(name="b", score=-0.3, confidence=1.0),
        ]
        signal = es.combine(scores)
        assert "a" in signal.components
        assert "b" in signal.components

    def test_reasons_limited_to_five(self):
        """combine()은 reasons를 최대 5개까지만 반환"""
        es = EnsembleStrategy(weights={"a": 1.0})
        many_reasons = [f"이유 {i}" for i in range(10)]
        scores = [
            ModuleScore(name="a", score=0.5, confidence=1.0,
                        reasons=many_reasons)
        ]
        signal = es.combine(scores)
        assert len(signal.reasons) <= 5


# =============================================================================
# 3. 빈 입력 / 엣지 케이스
# =============================================================================

class TestEdgeCases:
    """경계값 및 특수 상황 테스트"""

    def test_empty_module_scores(self):
        """빈 ModuleScore 리스트 → 점수 0, HOLD"""
        es = EnsembleStrategy(weights={"a": 1.0})
        signal = es.combine([])
        assert signal.score == 0.0
        assert signal.action == "HOLD"

    def test_zero_confidence_all_modules(self):
        """모든 모듈의 신뢰도가 0 → total_weight=0 → 점수 0"""
        es = EnsembleStrategy(weights={"a": 0.5, "b": 0.5})
        scores = [
            ModuleScore(name="a", score=0.9, confidence=0.0),
            ModuleScore(name="b", score=-0.8, confidence=0.0),
        ]
        signal = es.combine(scores)
        assert signal.score == 0.0

    def test_ensemble_signal_dataclass(self):
        """EnsembleSignal 데이터클래스 기본값 확인"""
        sig = EnsembleSignal(action="HOLD", score=0.0, confidence=0.0)
        assert sig.components == {}
        assert sig.reasons == []

    def test_module_score_dataclass(self):
        """ModuleScore 데이터클래스 기본값 확인"""
        ms = ModuleScore(name="test", score=0.5, confidence=0.8)
        assert ms.reasons == []


# =============================================================================
# 4. generate_signals (DataFrame 기반) 테스트
# =============================================================================

class TestGenerateSignals:
    """generate_signals()의 일별 신호 생성 로직 검증"""

    def _make_df(self, n=100):
        """테스트용 OHLCV DataFrame 생성"""
        dates = pd.date_range("2024-01-01", periods=n, freq="B")
        np.random.seed(42)
        close = 100 + np.cumsum(np.random.randn(n) * 0.5)
        return pd.DataFrame({
            "Open": close - 0.5,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Volume": np.random.randint(1000, 10000, n),
        }, index=dates)

    def test_all_bullish_scores_produce_buy_signals(self):
        """
        모든 모듈이 강한 양의 점수 → 매수 신호(1) 다수 생성
        """
        df = self._make_df()
        es = EnsembleStrategy(weights={"a": 0.5, "b": 0.5})

        module_scores = {
            "a": pd.Series(0.5, index=df.index),
            "b": pd.Series(0.5, index=df.index),
        }
        signals = es.generate_signals(df, module_scores)

        # 가중 합산: 0.5*0.5 + 0.5*0.5 = 0.5 > buy_threshold(0.2)
        assert (signals == 1).all()

    def test_all_bearish_scores_produce_sell_signals(self):
        """모든 모듈이 강한 음의 점수 → 매도 신호(-1)"""
        df = self._make_df()
        es = EnsembleStrategy(weights={"a": 0.5, "b": 0.5})

        module_scores = {
            "a": pd.Series(-0.5, index=df.index),
            "b": pd.Series(-0.5, index=df.index),
        }
        signals = es.generate_signals(df, module_scores)
        assert (signals == -1).all()

    def test_neutral_scores_produce_hold_signals(self):
        """중립 점수 → HOLD 신호(0)"""
        df = self._make_df()
        es = EnsembleStrategy(weights={"a": 0.5, "b": 0.5})

        module_scores = {
            "a": pd.Series(0.0, index=df.index),
            "b": pd.Series(0.0, index=df.index),
        }
        signals = es.generate_signals(df, module_scores)
        assert (signals == 0).all()

    def test_missing_module_filled_with_zero(self):
        """
        weights에 있지만 scores에 없는 모듈 → 0으로 처리됨

        weights: {"a": 0.5, "b": 0.5}
        scores: {"a": 0.8} (b 누락)
        → b는 기여 없음, a만 반영: 0.8 * 0.5 = 0.4 > 0.2 → BUY
        """
        df = self._make_df()
        es = EnsembleStrategy(weights={"a": 0.5, "b": 0.5})

        module_scores = {
            "a": pd.Series(0.8, index=df.index),
            # "b" 누락 → weight > 0이지만 scores에 없으므로 건너뜀
        }
        signals = es.generate_signals(df, module_scores)
        # a의 기여만: 0.8 * 0.5 = 0.4 > 0.2 → 매수 신호
        assert (signals == 1).all()


# =============================================================================
# 5. technical_score_from_df 테스트
# =============================================================================

class TestTechnicalScoreFromDf:
    """기술적 지표 DataFrame → 점수 시리즈 변환 검증"""

    def _make_technical_df(self, n=100):
        """기술적 지표가 포함된 테스트 DataFrame"""
        dates = pd.date_range("2024-01-01", periods=n, freq="B")
        np.random.seed(42)
        close = 100 + np.cumsum(np.random.randn(n) * 0.5)

        df = pd.DataFrame({
            "Open": close - 0.5,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Volume": np.random.randint(1000, 10000, n),
            "RSI": np.random.uniform(20, 80, n),
            "MACD_Hist": np.random.randn(n) * 0.5,
            "SMA_20": close - 1,
            "SMA_50": close - 3,
            "BB_PctB": np.random.uniform(0, 1, n),
        }, index=dates)
        return df

    def test_output_range_clipped(self, ensemble_strategy):
        """기술적 점수가 -1 ~ +1 범위로 클리핑됨"""
        df = self._make_technical_df()
        scores = ensemble_strategy.technical_score_from_df(df)

        assert scores.min() >= -1.0
        assert scores.max() <= 1.0

    def test_output_length_matches_input(self, ensemble_strategy):
        """출력 Series 길이 == 입력 DataFrame 행 수"""
        df = self._make_technical_df()
        scores = ensemble_strategy.technical_score_from_df(df)
        assert len(scores) == len(df)

    def test_missing_columns_handled(self, ensemble_strategy):
        """
        일부 기술 지표 컬럼 누락 → 해당 부분만 건너뛰고 동작

        RSI만 있고 MACD, SMA 등이 없어도 에러 없이 점수 생성
        """
        dates = pd.date_range("2024-01-01", periods=50, freq="B")
        df = pd.DataFrame({
            "Close": np.random.uniform(90, 110, 50),
            "RSI": np.random.uniform(25, 75, 50),
        }, index=dates)

        scores = ensemble_strategy.technical_score_from_df(df)
        assert len(scores) == 50
        assert scores.min() >= -1.0
        assert scores.max() <= 1.0

    def test_empty_df_returns_zeros(self, ensemble_strategy):
        """빈 DataFrame → 빈 점수 시리즈 (에러 없이)"""
        df = pd.DataFrame(columns=["Close", "RSI", "MACD_Hist"])
        scores = ensemble_strategy.technical_score_from_df(df)
        assert len(scores) == 0

    def test_rsi_oversold_gives_positive_score(self, ensemble_strategy):
        """
        RSI < 30 (과매도) → 양의 점수 기여

        RSI 점수 공식: -(rsi - 50) / 50
        RSI=25: -(25-50)/50 = 0.5, clipped to 0.5, × 0.3 = 0.15
        """
        dates = pd.date_range("2024-01-01", periods=10, freq="B")
        df = pd.DataFrame({
            "Close": [100] * 10,
            "RSI": [25] * 10,  # 과매도
        }, index=dates)

        scores = ensemble_strategy.technical_score_from_df(df)
        # RSI만 있으므로: -(25-50)/50 = 0.5, clip[-0.5, 0.5], × 0.3 = 0.15
        assert (scores > 0).all()

    def test_rsi_overbought_gives_negative_score(self, ensemble_strategy):
        """RSI > 70 (과매수) → 음의 점수 기여"""
        dates = pd.date_range("2024-01-01", periods=10, freq="B")
        df = pd.DataFrame({
            "Close": [100] * 10,
            "RSI": [80] * 10,  # 과매수
        }, index=dates)

        scores = ensemble_strategy.technical_score_from_df(df)
        assert (scores < 0).all()
