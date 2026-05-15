"""
=============================================================================
strategy/ensemble.py - 앙상블 전략 엔진
=============================================================================

여러 분석 모듈의 결과를 가중치로 결합하여 최종 매매 신호를 생성합니다.

앙상블(Ensemble)이란?
- 여러 모델/방법의 예측을 종합하여 최종 결정을 내리는 기법
- 단일 모델보다 안정적이고 과적합에 강함
- 날씨 예보가 여러 모델을 종합하는 것과 같은 원리

왜 앙상블을 쓰는가?
- RSI만 보면: 강한 상승장에서 계속 "과매수"라고 매도 신호를 줌
- MACD만 보면: 횡보장에서 잦은 거짓 신호 발생
- 여러 지표를 종합하면 → 잘못된 신호가 서로 상쇄됨

가중치 결정 방법:
1. 동일 가중치 (시작점으로 좋음)
2. 백테스트 성능 기반 (Phase 2에서 Optuna로 최적화)
3. 동적 가중치 (최근 성능이 좋은 모듈에 더 큰 가중치)
=============================================================================
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field


@dataclass
class ModuleScore:
    """
    개별 분석 모듈의 출력

    Attributes:
        name: 모듈 이름 (예: "technical", "factor")
        score: 점수 (-1.0 ~ +1.0)
            - +1.0 = 강력 매수
            -  0.0 = 중립
            - -1.0 = 강력 매도
        confidence: 신뢰도 (0.0 ~ 1.0)
        reasons: 근거 리스트
    """
    name: str
    score: float         # -1.0 ~ +1.0
    confidence: float    # 0.0 ~ 1.0
    reasons: List[str] = field(default_factory=list)


@dataclass
class EnsembleSignal:
    """
    앙상블 최종 신호

    Attributes:
        action: "BUY", "SELL", "HOLD"
        score: 종합 점수 (-1.0 ~ +1.0)
        confidence: 종합 신뢰도
        components: 각 모듈별 기여도
    """
    action: str
    score: float
    confidence: float
    components: Dict[str, float] = field(default_factory=dict)
    reasons: List[str] = field(default_factory=list)


class EnsembleStrategy:
    """
    앙상블 전략 엔진

    여러 분석 모듈의 점수를 가중 평균하여 최종 신호를 생성합니다.

    사용법:
        ensemble = EnsembleStrategy(weights)
        signal = ensemble.combine(module_scores)

    또는 DataFrame 기반:
        signals = ensemble.generate_signals(df, analyzers)
    """

    def __init__(self, weights: Optional[Dict[str, float]] = None,
                 ensemble_config=None):
        """
        Parameters:
            weights: 모듈별 가중치 딕셔너리
                예: {"technical": 0.25, "factor": 0.20, ...}
                합계가 1.0이어야 함. None이면 ensemble_config 또는 기본값 사용.
            ensemble_config: EnsembleConfig 인스턴스 (모듈 enable/disable 반영)
                None이면 기본 설정 사용. weights보다 우선순위 낮음.
        """
        if weights is None:
            from config.settings import EnsembleConfig
            config = ensemble_config if ensemble_config else EnsembleConfig()
            # ── 비활성 모듈은 자동 제외 + 가중치 정규화 ──
            # get_effective_weights()가 활성 모듈만 합계 1.0으로 정규화하여 반환
            effective = config.get_effective_weights()
            # 모든 모듈명에 대해 weights 채우기 (없는 모듈은 0)
            weights = {
                "technical": effective.get("technical", 0.0),
                "factor": effective.get("factor", 0.0),
                "time_series": effective.get("time_series", 0.0),
                "monte_carlo": effective.get("monte_carlo", 0.0),
                "ml_prediction": effective.get("ml_prediction", 0.0),
                "sentiment": effective.get("sentiment", 0.0),
            }

        self.weights = weights
        self._validate_weights()

        # ── 매매 임계값: EnsembleConfig에서 읽기 ──
        # 하드코딩 대신 설정에서 읽어서 대시보드/CLI에서 조정 가능
        from config.settings import EnsembleConfig
        ensemble_cfg = ensemble_config if ensemble_config else EnsembleConfig()
        self.buy_threshold = ensemble_cfg.buy_threshold
        self.sell_threshold = ensemble_cfg.sell_threshold

    def _validate_weights(self):
        """가중치 합계 검증"""
        total = sum(self.weights.values())
        if abs(total - 1.0) > 0.01:
            # 자동 정규화
            for key in self.weights:
                self.weights[key] /= total

    def combine(self, module_scores: List[ModuleScore]) -> EnsembleSignal:
        """
        여러 모듈의 점수를 결합하여 최종 신호 생성

        결합 방식:
        1. 각 모듈 점수 × 가중치 × 신뢰도
        2. 합산하여 종합 점수 계산
        3. 임계값과 비교하여 BUY/SELL/HOLD 결정

        Parameters:
            module_scores: ModuleScore 리스트

        Returns:
            EnsembleSignal
        """
        weighted_sum = 0.0
        total_weight = 0.0
        components = {}
        all_reasons = []

        for ms in module_scores:
            weight = self.weights.get(ms.name, 0.0)
            if weight == 0:
                continue

            # 가중 점수 = 점수 × 가중치 × 신뢰도
            # 신뢰도가 낮은 모듈은 영향력 감소
            contribution = ms.score * weight * ms.confidence
            weighted_sum += contribution
            total_weight += weight * ms.confidence

            components[ms.name] = contribution
            all_reasons.extend(ms.reasons)

        # 정규화 (신뢰도 반영한 가중 평균)
        final_score = weighted_sum / total_weight if total_weight > 0 else 0.0

        # 종합 신뢰도: 사용 가능한 모듈이 많을수록 높음
        n_active = sum(1 for ms in module_scores
                       if self.weights.get(ms.name, 0) > 0)
        n_total = len([w for w in self.weights.values() if w > 0])
        coverage = n_active / n_total if n_total > 0 else 0

        confidence = min(abs(final_score), 1.0) * coverage

        # 최종 액션 결정
        if final_score > self.buy_threshold:
            action = "BUY"
        elif final_score < self.sell_threshold:
            action = "SELL"
        else:
            action = "HOLD"

        return EnsembleSignal(
            action=action,
            score=final_score,
            confidence=confidence,
            components=components,
            reasons=all_reasons[:5],  # 상위 5개 이유만
        )

    def generate_signals(
        self,
        df: pd.DataFrame,
        module_scores_series: Dict[str, pd.Series]
    ) -> pd.Series:
        """
        DataFrame 전체에 대해 일별 앙상블 신호 생성 (백테스트용)

        Parameters:
            df: OHLCV + 지표 DataFrame
            module_scores_series: {모듈명: 일별 점수 Series} 딕셔너리
                각 Series는 -1~+1 범위의 값

        Returns:
            신호 Series (1=매수 보유, 0=포지션 없음, -1=매도 보유)
        """
        # 가중 합산
        weighted_score = pd.Series(0.0, index=df.index)

        for name, scores in module_scores_series.items():
            weight = self.weights.get(name, 0.0)
            if weight > 0:
                aligned = scores.reindex(df.index).fillna(0)
                weighted_score += aligned * weight

        # 신호 생성
        signals = pd.Series(0, index=df.index)
        signals[weighted_score > self.buy_threshold] = 1
        signals[weighted_score < self.sell_threshold] = -1

        return signals

    def technical_score_from_df(self, df: pd.DataFrame) -> pd.Series:
        """
        기술적 지표 DataFrame에서 일별 점수 시리즈 생성

        RSI, MACD, 이동평균 정렬을 종합하여 -1~+1 점수를 매김

        Parameters:
            df: TechnicalAnalyzer.calculate_all() 결과

        Returns:
            일별 기술적 점수 (-1~+1)
        """
        score = pd.Series(0.0, index=df.index)

        # RSI 기반 점수
        if "RSI" in df.columns:
            rsi = df["RSI"]
            # RSI 50 기준으로 선형 점수화: 30→+0.3, 70→-0.3
            rsi_score = -(rsi - 50) / 50  # 50이면 0, 30이면 +0.4, 70이면 -0.4
            rsi_score = rsi_score.clip(-0.5, 0.5)
            score += rsi_score * 0.3

        # MACD 히스토그램 방향
        if "MACD_Hist" in df.columns:
            hist = df["MACD_Hist"]
            # 히스토그램 부호 + 변화 방향
            hist_norm = hist / hist.rolling(20).std().replace(0, 1)
            hist_score = hist_norm.clip(-1, 1) * 0.3
            score += hist_score

        # 이동평균 정배열/역배열
        if all(col in df.columns for col in ["SMA_20", "SMA_50"]):
            # 종가 > SMA20 > SMA50 → 상승 정배열 (+)
            ma_bull = ((df["Close"] > df["SMA_20"]) &
                       (df["SMA_20"] > df["SMA_50"])).astype(float) * 0.2
            ma_bear = ((df["Close"] < df["SMA_20"]) &
                       (df["SMA_20"] < df["SMA_50"])).astype(float) * -0.2
            score += ma_bull + ma_bear

        # 볼린저 %B
        if "BB_PctB" in df.columns:
            pctb = df["BB_PctB"]
            bb_score = -(pctb - 0.5)  # 0.5=중립, 0=매수, 1=매도
            bb_score = bb_score.clip(-0.3, 0.3) * 0.2
            score += bb_score

        return score.clip(-1, 1)


def classify_position_type(
    module_scores: List[ModuleScore],
    ensemble: EnsembleSignal,
    enabled_types: Optional[dict] = None,
) -> Optional[dict]:
    """
    매수 신호의 특성을 분석하여 포지션 유형을 분류합니다.

    분류 기준:
    ─────────────────────────────────────────────────────────
    단타 (Short-term, 1~3일):
      - 기술적 분석이 지배적 (technical 기여도가 가장 높음)
      - 기술적 신뢰도 > 0.5
      - 팩터/펀더멘탈 신호 약함
      → 타이트한 손절 (ATR × 1.5), 낮은 R:R (1.5)

    장기 (Long-term, 1개월+):
      - 팩터/펀더멘탈 분석이 지배적
      - 팩터 점수 > 0.3 (저평가 + 양호 재무)
      → 넓은 손절 (ATR × 3.0), 높은 R:R (3.0)

    스윙 (Swing, 1~4주):
      - 위 두 조건에 해당하지 않는 혼합 신호
      → 표준 손절 (ATR × 2.0), 표준 R:R (2.0)
    ─────────────────────────────────────────────────────────

    Parameters:
        module_scores: 각 분석 모듈의 점수 리스트
        ensemble: 앙상블 통합 신호
        enabled_types: 활성화된 포지션 유형 dict
            예: {"short": True, "swing": True, "long": False}
            None이면 모두 활성화로 간주.
            지정된 유형이 비활성화면 다음 우선순위로 폴백,
            모두 비활성이면 None 반환 (매수 차단).

    Returns:
        dict 또는 None
            - dict: 포지션 유형 정보 (position_type, atr_stop_multiplier 등)
            - None: 모든 유형이 비활성화되어 분류 불가 → 매수 차단
    """
    # 기본값: 모두 활성
    if enabled_types is None:
        enabled_types = {"short": True, "swing": True, "long": True}

    # 모두 비활성이면 None 반환 (매수 차단)
    if not any(enabled_types.values()):
        return None

    components = ensemble.components  # {name: contribution}

    # 모듈별 점수/신뢰도 맵 구축
    score_map = {}
    confidence_map = {}
    for ms in module_scores:
        score_map[ms.name] = ms.score
        confidence_map[ms.name] = ms.confidence

    tech_contribution = abs(components.get("technical", 0))
    factor_contribution = abs(components.get("factor", 0))
    tech_score = score_map.get("technical", 0)
    tech_confidence = confidence_map.get("technical", 0)
    factor_score = score_map.get("factor", 0)
    factor_confidence = confidence_map.get("factor", 0)

    # ── 후보 결정 (활성 여부 체크 후 fallback) ──
    # 1차 후보 결정 (원래 로직)
    primary_type = None
    primary_dict = None

    # 단타 후보: 기술적 분석 지배
    if (tech_contribution > factor_contribution
            and tech_confidence > 0.5
            and abs(factor_score) < 0.25):
        primary_type = "short"
        primary_dict = {
            "position_type": "단타",
            "position_type_en": "short",
            "holding_period": "1~3일",
            "holding_period_en": "1-3 days",
            "atr_stop_multiplier": 1.5,
            "rr_ratio": 1.5,
            "classification_reason": (
                f"기술적 신호 지배적 (기여도 {tech_contribution:.2f} > "
                f"팩터 {factor_contribution:.2f})"
            ),
        }
    # 장기 후보: 팩터/펀더멘탈 지배
    elif (factor_contribution > tech_contribution
            and factor_score > 0.3
            and factor_confidence > 0.4):
        primary_type = "long"
        primary_dict = {
            "position_type": "장기",
            "position_type_en": "long",
            "holding_period": "1개월+",
            "holding_period_en": "1+ months",
            "atr_stop_multiplier": 3.0,
            "rr_ratio": 3.0,
            "classification_reason": (
                f"펀더멘탈 신호 지배적 (팩터 점수 {factor_score:.2f}, "
                f"기여도 {factor_contribution:.2f})"
            ),
        }
    # 스윙 후보: 혼합
    else:
        primary_type = "swing"
        primary_dict = {
            "position_type": "스윙",
            "position_type_en": "swing",
            "holding_period": "1~4주",
            "holding_period_en": "1-4 weeks",
            "atr_stop_multiplier": 2.0,
            "rr_ratio": 2.0,
            "classification_reason": (
                f"혼합 신호 (기술 기여 {tech_contribution:.2f}, "
                f"팩터 기여 {factor_contribution:.2f})"
            ),
        }

    # ── 활성 여부 확인 + fallback ──
    # 분류된 유형이 활성이면 그대로 사용
    if enabled_types.get(primary_type, True):
        return primary_dict

    # 비활성이면 fallback 순서: 스윙(혼합) > 장기 > 단타
    # 스윙이 가장 보수적/기본값이라 fallback 1순위
    fallback_order = ["swing", "long", "short"]
    fallback_dicts = {
        "swing": {
            "position_type": "스윙",
            "position_type_en": "swing",
            "holding_period": "1~4주",
            "holding_period_en": "1-4 weeks",
            "atr_stop_multiplier": 2.0,
            "rr_ratio": 2.0,
            "classification_reason": (
                f"[fallback] {primary_dict['position_type']} 비활성 → 스윙으로 대체"
            ),
        },
        "long": {
            "position_type": "장기",
            "position_type_en": "long",
            "holding_period": "1개월+",
            "holding_period_en": "1+ months",
            "atr_stop_multiplier": 3.0,
            "rr_ratio": 3.0,
            "classification_reason": (
                f"[fallback] {primary_dict['position_type']} 비활성 → 장기로 대체"
            ),
        },
        "short": {
            "position_type": "단타",
            "position_type_en": "short",
            "holding_period": "1~3일",
            "holding_period_en": "1-3 days",
            "atr_stop_multiplier": 1.5,
            "rr_ratio": 1.5,
            "classification_reason": (
                f"[fallback] {primary_dict['position_type']} 비활성 → 단타로 대체"
            ),
        },
    }
    for fb in fallback_order:
        if fb == primary_type:
            continue  # 이미 시도함
        if enabled_types.get(fb, False):
            return fallback_dicts[fb]

    # 모든 유형 비활성 (매수 차단)
    return None
