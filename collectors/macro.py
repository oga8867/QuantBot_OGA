"""
=============================================================================
collectors/macro.py - 거시경제 데이터 수집기
=============================================================================

FRED(Federal Reserve Economic Data)에서 미국 거시경제 지표를 수집합니다.

FRED란?
- 미국 연방준비은행(세인트루이스 연준)이 운영하는 무료 경제 데이터 서비스
- 80만 개 이상의 시계열 데이터 보유
- 무료 API 키 발급: https://fred.stlouisfed.org/docs/api/api_key.html

왜 거시경제 데이터가 필요한가?
- 주식 시장은 거시경제에 크게 영향을 받음
- 금리 인상 → 성장주 하락, 금리 인하 → 성장주 상승
- VIX(공포지수) > 30이면 시장 패닉 → 리스크 축소 필요
- 장단기 금리차(T10Y2Y) < 0이면 경기침체 선행지표

수집하는 핵심 지표:
| 코드      | 이름             | 의미                           |
|-----------|------------------|--------------------------------|
| GDP       | 미국 GDP          | 경제 규모 (분기별)             |
| CPIAUCSL  | 소비자물가지수    | 인플레이션 측정                |
| FEDFUNDS  | 연방기금금리      | 미국 기준금리                  |
| T10Y2Y    | 장단기 금리차     | < 0이면 경기침체 경고!         |
| VIXCLS    | VIX 공포지수      | > 30이면 극도의 공포           |
| DGS10     | 10년 국채 수익률  | 장기 금리 (할인율 역할)        |
| UNRATE    | 실업률            | 고용 시장 건전성               |
=============================================================================
"""

import pandas as pd
from typing import Optional, Dict
from datetime import datetime, timedelta
from .base import BaseCollector

try:
    from fredapi import Fred
    FRED_AVAILABLE = True
except ImportError:
    FRED_AVAILABLE = False


# FRED 시계열 코드 매핑
FRED_SERIES = {
    "GDP": "GDP",           # 미국 GDP (분기별)
    "CPI": "CPIAUCSL",      # 소비자물가지수
    "FEDFUNDS": "FEDFUNDS", # 연방기금금리 (기준금리)
    "T10Y2Y": "T10Y2Y",     # 장단기 금리 스프레드
    "VIX": "VIXCLS",        # VIX 공포지수
    "DGS10": "DGS10",       # 10년 국채 수익률
    "UNRATE": "UNRATE",     # 실업률
    "M2SL": "M2SL",         # M2 통화량 (유동성)
}


class MacroCollector(BaseCollector):
    """
    FRED 거시경제 데이터 수집기

    API 키가 없으면 자동으로 건너뜁니다 (Graceful Degradation).
    """

    def __init__(self, api_key: Optional[str] = None):
        """
        Parameters:
            api_key: FRED API 키 (None이면 환경변수 FRED_API_KEY에서 로드)
        """
        super().__init__(name="macro")
        self.api_key = api_key

        if self.api_key is None:
            import os
            self.api_key = os.environ.get("FRED_API_KEY")

        self.fred = None
        if FRED_AVAILABLE and self.api_key:
            try:
                self.fred = Fred(api_key=self.api_key)
            except Exception as e:
                self.logger.warning(f"FRED 초기화 실패: {e}")

    def collect(self, symbol: str, **kwargs) -> Optional[pd.DataFrame]:
        """
        단일 FRED 시계열 수집

        Parameters:
            symbol: FRED 시계열 코드 (예: "VIXCLS", "FEDFUNDS")
                    또는 별칭 (예: "VIX", "GDP")
            **kwargs:
                period: 수집 기간 (기본 "2y")

        Returns:
            DataFrame (Date 인덱스, Value 컬럼)
        """
        if not self.fred:
            self.logger.warning("FRED API 사용 불가 (키 미설정 또는 fredapi 미설치)")
            return None

        # 별칭을 실제 코드로 변환
        series_id = FRED_SERIES.get(symbol.upper(), symbol)

        # 기간 설정
        period = kwargs.get("period", "2y")
        period_days = {"1y": 365, "2y": 730, "5y": 1825, "10y": 3650}
        days = period_days.get(period, 730)

        start_date = datetime.now() - timedelta(days=days)

        # FRED에서 데이터 가져오기
        data = self.fred.get_series(
            series_id,
            observation_start=start_date
        )

        if data is None or data.empty:
            return None

        # Series → DataFrame 변환
        df = pd.DataFrame({"Value": data})
        df.index.name = "Date"

        return df

    def collect_all(self, period: str = "2y") -> Dict[str, pd.DataFrame]:
        """
        모든 핵심 거시경제 지표를 한번에 수집

        Parameters:
            period: 수집 기간

        Returns:
            딕셔너리: {"VIX": DataFrame, "FEDFUNDS": DataFrame, ...}
            수집 실패한 항목은 포함되지 않음
        """
        results = {}

        for name, series_id in FRED_SERIES.items():
            df = self.safe_collect(series_id, period=period)
            if df is not None:
                results[name] = df

        self.logger.info(
            f"거시경제 데이터 수집 완료: {len(results)}/{len(FRED_SERIES)}개 성공"
        )

        return results

    def get_market_regime(self) -> Dict[str, str]:
        """
        현재 시장 환경(레짐)을 거시경제 지표로 판단

        레짐(Regime)이란?
        - 시장이 현재 어떤 상태에 있는지를 분류하는 것
        - 예: "위험 회피", "정상", "과열" 등
        - 레짐에 따라 전략의 공격성을 조절해야 함

        Returns:
            {"regime": "risk_on/risk_off/neutral",
             "vix_level": "low/normal/high/extreme",
             "rate_trend": "rising/falling/stable",
             "yield_curve": "normal/flat/inverted",
             "signals": [...]}
        """
        if not self.fred:
            return {"regime": "unknown", "signals": ["FRED 데이터 없음"]}

        signals = []
        regime_score = 0  # 양수=risk_on, 음수=risk_off

        # VIX 체크
        vix_data = self.safe_collect("VIXCLS", period="1mo")
        vix_level = "unknown"
        if vix_data is not None and not vix_data.empty:
            vix = vix_data["Value"].iloc[-1]
            if vix < 15:
                vix_level = "low"
                regime_score += 1
                signals.append(f"VIX 매우 낮음 ({vix:.1f}) - 낙관적 시장")
            elif vix < 20:
                vix_level = "normal"
                signals.append(f"VIX 정상 ({vix:.1f})")
            elif vix < 30:
                vix_level = "high"
                regime_score -= 1
                signals.append(f"VIX 높음 ({vix:.1f}) - 불안정")
            else:
                vix_level = "extreme"
                regime_score -= 2
                signals.append(f"VIX 극단적 ({vix:.1f}) - 패닉 상태!")

        # 장단기 금리차 체크
        spread_data = self.safe_collect("T10Y2Y", period="1mo")
        yield_curve = "unknown"
        if spread_data is not None and not spread_data.empty:
            spread = spread_data["Value"].iloc[-1]
            if spread > 0.5:
                yield_curve = "normal"
                regime_score += 1
                signals.append(f"수익률 곡선 정상 ({spread:.2f}%)")
            elif spread > 0:
                yield_curve = "flat"
                signals.append(f"수익률 곡선 평탄화 ({spread:.2f}%) - 주의")
            else:
                yield_curve = "inverted"
                regime_score -= 2
                signals.append(f"수익률 곡선 역전 ({spread:.2f}%) - 침체 경고!")

        # 최종 레짐 판단
        if regime_score >= 2:
            regime = "risk_on"
        elif regime_score <= -2:
            regime = "risk_off"
        else:
            regime = "neutral"

        return {
            "regime": regime,
            "vix_level": vix_level,
            "yield_curve": yield_curve,
            "regime_score": regime_score,
            "signals": signals,
        }
