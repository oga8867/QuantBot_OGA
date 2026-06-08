"""
=============================================================================
strategy/sleeves.py - 전략 슬리브 신호 계산 (자본 쿼터 모드용)
=============================================================================

CapitalAllocator(자본 쿼터)와 함께 쓰는 '전략 슬리브'들의 신호를 계산합니다.

설계 원칙:
- 순수 함수 — 라이브 루프와 분리되어 단위 테스트가 쉬움.
- 각 슬리브는 (signal, score)를 반환:
    signal: "BUY" / "SELL" / "HOLD"
    score : -1.0 ~ +1.0 (부호 통일 — 양수=매수매력, 음수=매도매력)
- 입력은 run_bot이 이미 계산해 둔 값을 재사용 (중복 분석 방지):
    df_analyzed     : 지표 계산된 가격 DataFrame
    factor_combined : FactorAnalyzer.combined (-1~+1) 또는 None
    sentiment_score : 뉴스 평균 감성 (-1~+1) 또는 None
    technical_signal: TechnicalSignal (analyzer.generate_signal 결과) 또는 None
    ensemble_signal : EnsembleSignal (ensemble.combine 결과) 또는 None

1종목 1슬리브 정책: 한 종목은 한 슬리브만 보유. 같은 종목에 여러 슬리브가
BUY를 내면 CapitalAllocator.pick_strategy()가 점수+가용자본으로 하나만 선택.
=============================================================================
"""

from __future__ import annotations

import logging
from typing import Dict, Optional, Tuple

# 모멘텀/평균회귀 시그널 헬퍼는 capital_allocator에 이미 구현됨 — 재사용
from strategy.capital_allocator import momentum_signal, mean_reversion_signal

logger = logging.getLogger(__name__)


# 사용 가능한 전략 슬리브 (key → 한글 표시명)
AVAILABLE_SLEEVES: Dict[str, str] = {
    "momentum": "모멘텀(추세추종)",
    "mean_reversion": "평균회귀",
    "factor": "팩터",
    "technical": "기술적",
    "sentiment": "감성",
    "ensemble": "앙상블",
}

# 슬리브별 매수/매도 임계값 (점수 기준)
_FACTOR_BUY, _FACTOR_SELL = 0.30, -0.30
_SENTIMENT_BUY, _SENTIMENT_SELL = 0.30, -0.30


def _clip(v: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def compute_sleeve_signal(
    sleeve: str,
    *,
    df_analyzed=None,
    factor_combined: Optional[float] = None,
    sentiment_score: Optional[float] = None,
    technical_signal=None,
    ensemble_signal=None,
) -> Tuple[str, float]:
    """
    단일 슬리브의 (signal, score)를 계산.

    데이터 부족/미제공 시 ("HOLD", 0.0)을 반환하여 안전.
    """
    try:
        if sleeve == "momentum":
            sig = momentum_signal(df_analyzed)
            # 점수: 최근 20일 수익률을 -1~+1로 클립 (방향 부호 통일)
            score = 0.0
            if df_analyzed is not None and len(df_analyzed) > 20:
                close = df_analyzed["Close"]
                mom = float(close.iloc[-1] / close.iloc[-20] - 1)
                score = _clip(mom * 5.0)  # ±20% → ±1.0
            if sig == "SELL":
                score = -abs(score) if score != 0 else -0.5
            elif sig == "BUY":
                score = abs(score) if score != 0 else 0.5
            else:
                score = 0.0
            return sig, score

        if sleeve == "mean_reversion":
            sig = mean_reversion_signal(df_analyzed)
            # 점수: RSI가 중앙(50)에서 얼마나 벗어났는지 → 반대 방향 매력
            score = 0.0
            if df_analyzed is not None and "RSI" in df_analyzed.columns:
                rsi = float(df_analyzed["RSI"].iloc[-1])
                # RSI 낮을수록 매수매력(+), 높을수록 매도매력(-)
                score = _clip((50.0 - rsi) / 50.0)
            if sig == "HOLD":
                score = 0.0
            return sig, score

        if sleeve == "factor":
            if factor_combined is None:
                return "HOLD", 0.0
            fc = _clip(float(factor_combined))
            if fc >= _FACTOR_BUY:
                return "BUY", fc
            if fc <= _FACTOR_SELL:
                return "SELL", fc
            return "HOLD", fc

        if sleeve == "sentiment":
            if sentiment_score is None:
                return "HOLD", 0.0
            ss = _clip(float(sentiment_score))
            if ss >= _SENTIMENT_BUY:
                return "BUY", ss
            if ss <= _SENTIMENT_SELL:
                return "SELL", ss
            return "HOLD", ss

        if sleeve == "technical":
            if technical_signal is None:
                return "HOLD", 0.0
            sig = technical_signal.signal
            strength = float(getattr(technical_signal, "strength", 0.0))
            if sig == "BUY":
                return "BUY", _clip(strength)
            if sig == "SELL":
                return "SELL", -_clip(strength)
            return "HOLD", 0.0

        if sleeve == "ensemble":
            if ensemble_signal is None:
                return "HOLD", 0.0
            return ensemble_signal.action, _clip(float(ensemble_signal.score))

    except Exception as e:
        logger.debug(f"[슬리브] {sleeve} 신호 계산 실패 → HOLD: {e}")
        return "HOLD", 0.0

    # 알 수 없는 슬리브
    return "HOLD", 0.0


def compute_all_sleeve_signals(
    active_sleeves,
    **kwargs,
) -> Dict[str, Tuple[str, float]]:
    """
    활성 슬리브 전부의 신호를 한 번에 계산.

    Parameters:
        active_sleeves: 활성 슬리브 key 리스트 (예: ["momentum", "factor"])
        **kwargs: compute_sleeve_signal에 그대로 전달

    Returns:
        {sleeve: (signal, score)}
    """
    out = {}
    for s in active_sleeves:
        if s in AVAILABLE_SLEEVES:
            out[s] = compute_sleeve_signal(s, **kwargs)
    return out


def sanitize_weights(raw) -> Dict[str, float]:
    """
    사용자 입력 슬리브 비중을 안전한 {sleeve: fraction}으로 정규화.

    - AVAILABLE_SLEEVES에 없는 키 제외
    - 음수/비정상 제외
    - 값이 1.0 초과면 백분율로 간주해 /100 (예: 50 → 0.5)
    - 합계가 0이면 빈 dict (호출 측이 ensemble로 폴백)
    - 합계 1.0으로 정규화하지 않고 원값 유지 (정규화는 CapitalAllocator 담당)
    """
    if not isinstance(raw, dict):
        return {}
    out = {}
    for k, v in raw.items():
        if k not in AVAILABLE_SLEEVES:
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if fv <= 0:
            continue
        if fv > 1.0:
            fv = fv / 100.0
        out[k] = fv
    return out
