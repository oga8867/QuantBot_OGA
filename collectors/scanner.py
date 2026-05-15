"""
=============================================================================
collectors/scanner.py - 시장 스캐너
=============================================================================

감시 종목 리스트를 순회하며 "주목할 만한" 종목을 자동 탐색합니다.

스캐너의 역할:
- 매일 수십~수백 개 종목을 체크할 수 없으니, 자동으로 필터링
- 비정상적인 움직임(거래량 급증, 급등/급락, 지표 극단값)을 감지
- "여기 뭔가 일어나고 있다"는 종목만 뽑아서 상세 분석 대상으로 넘김

6가지 탐지 신호:
1. 거래량 급증 (20일 평균의 2배/3배)
2. RSI 극단값 (< 25 또는 > 80)
3. 급등/급락 (일 5%, 주 10%)
4. 볼린저 밴드 이탈
5. MACD 크로스 (골든/데드)
6. 52주 신고/신저 근접 (5% 이내)
=============================================================================
"""

import pandas as pd
import numpy as np
from typing import List, Dict, Optional
from dataclasses import dataclass, field
from .base import BaseCollector


@dataclass
class ScanResult:
    """
    스캐너가 감지한 신호 결과

    Attributes:
        symbol: 종목 코드
        signals: 감지된 신호 리스트
        priority: 우선순위 (신호 개수 × 강도)
        latest_price: 현재가
        change_pct: 변동률
    """
    symbol: str
    signals: List[str] = field(default_factory=list)
    priority: float = 0.0
    latest_price: float = 0.0
    change_pct: float = 0.0


class MarketScanner(BaseCollector):
    """
    시장 스캐너 - 주목할 종목 자동 탐색

    감시 종목 리스트를 순회하며 6가지 신호를 체크합니다.
    """

    def __init__(self, config=None):
        """
        Parameters:
            config: ScannerConfig 객체
        """
        super().__init__(name="scanner")

        if config is None:
            from config.settings import ScannerConfig
            config = ScannerConfig()
        self.config = config

    def collect(self, symbol: str, **kwargs) -> Optional[pd.DataFrame]:
        """BaseCollector 인터페이스 구현 (scan_symbol의 래퍼)"""
        # 스캐너는 DataFrame 대신 ScanResult를 반환하므로
        # 이 메서드는 직접 사용하지 않음
        return None

    def scan_symbol(self, symbol: str, df: pd.DataFrame) -> ScanResult:
        """
        단일 종목에 대해 6가지 신호 체크

        Parameters:
            symbol: 종목 코드
            df: 기술적 지표가 계산된 OHLCV DataFrame
                (TechnicalAnalyzer.calculate_all() 결과)

        Returns:
            ScanResult 객체
        """
        result = ScanResult(symbol=symbol)

        if df is None or df.empty or len(df) < 20:
            return result

        latest = df.iloc[-1]
        result.latest_price = latest["Close"]

        # 전일 대비 변동률
        if len(df) > 1:
            prev_close = df["Close"].iloc[-2]
            result.change_pct = (latest["Close"] - prev_close) / prev_close * 100

        # ─── 1. 거래량 급증 ─────────────────────────────────────────
        if "Volume_Ratio" in df.columns:
            vol_ratio = latest["Volume_Ratio"]
            if vol_ratio >= self.config.volume_surge_extreme:
                result.signals.append(
                    f"거래량 극단 급증 ({vol_ratio:.1f}x)")
                result.priority += 3.0
            elif vol_ratio >= self.config.volume_surge_moderate:
                result.signals.append(
                    f"거래량 급증 ({vol_ratio:.1f}x)")
                result.priority += 1.5

        # ─── 2. RSI 극단값 ──────────────────────────────────────────
        if "RSI" in df.columns:
            rsi = latest["RSI"]
            if not pd.isna(rsi):
                if rsi < 25:
                    result.signals.append(f"RSI 극단적 과매도 ({rsi:.1f})")
                    result.priority += 2.0
                elif rsi > 80:
                    result.signals.append(f"RSI 극단적 과매수 ({rsi:.1f})")
                    result.priority += 2.0

        # ─── 3. 급등/급락 ───────────────────────────────────────────
        daily_change = abs(result.change_pct) / 100
        if daily_change >= self.config.price_change_daily:
            direction = "급등" if result.change_pct > 0 else "급락"
            result.signals.append(
                f"일일 {direction} ({result.change_pct:+.1f}%)")
            result.priority += 2.5

        # 5일간 변동
        if len(df) >= 5:
            price_5d_ago = df["Close"].iloc[-5]
            change_5d = (latest["Close"] - price_5d_ago) / price_5d_ago
            if abs(change_5d) >= self.config.price_change_weekly:
                direction = "급등" if change_5d > 0 else "급락"
                result.signals.append(
                    f"5일 {direction} ({change_5d*100:+.1f}%)")
                result.priority += 1.5

        # ─── 4. 볼린저 밴드 이탈 ────────────────────────────────────
        if "BB_PctB" in df.columns:
            pctb = latest["BB_PctB"]
            if not pd.isna(pctb):
                if pctb > 1.0:
                    result.signals.append(
                        f"볼린저 상단 돌파 (%B={pctb:.2f})")
                    result.priority += 1.5
                elif pctb < 0.0:
                    result.signals.append(
                        f"볼린저 하단 돌파 (%B={pctb:.2f})")
                    result.priority += 1.5

        # ─── 5. MACD 크로스 ─────────────────────────────────────────
        if "MACD" in df.columns and "MACD_Signal" in df.columns and len(df) > 1:
            macd_now = latest["MACD"]
            signal_now = latest["MACD_Signal"]
            macd_prev = df["MACD"].iloc[-2]
            signal_prev = df["MACD_Signal"].iloc[-2]

            if not any(pd.isna([macd_now, signal_now, macd_prev, signal_prev])):
                if macd_prev <= signal_prev and macd_now > signal_now:
                    result.signals.append("MACD 골든크로스")
                    result.priority += 2.0
                elif macd_prev >= signal_prev and macd_now < signal_now:
                    result.signals.append("MACD 데드크로스")
                    result.priority += 2.0

        # ─── 6. 52주 신고/신저 근접 ─────────────────────────────────
        if len(df) >= 252:  # 1년 데이터 필요
            high_52w = df["High"].tail(252).max()
            low_52w = df["Low"].tail(252).min()
            price = latest["Close"]

            # 52주 고점 대비 현재가 위치
            proximity_high = (high_52w - price) / high_52w
            proximity_low = (price - low_52w) / low_52w if low_52w > 0 else 1

            if proximity_high <= self.config.high_low_proximity:
                result.signals.append(
                    f"52주 신고가 근접 ({proximity_high*100:.1f}% 이내)")
                result.priority += 1.5
            elif proximity_low <= self.config.high_low_proximity:
                result.signals.append(
                    f"52주 신저가 근접 ({proximity_low*100:.1f}% 이내)")
                result.priority += 1.5

        return result

    def scan_watchlist(
        self,
        watchlist: List[str],
        analyzed_data: Dict[str, pd.DataFrame]
    ) -> List[ScanResult]:
        """
        감시 종목 리스트 전체를 스캔하고 우선순위로 정렬

        Parameters:
            watchlist: 종목 코드 리스트
            analyzed_data: {종목코드: 분석된 DataFrame} 딕셔너리

        Returns:
            우선순위 내림차순 정렬된 ScanResult 리스트
            (신호가 있는 종목만 포함)
        """
        results = []

        for symbol in watchlist:
            df = analyzed_data.get(symbol)
            if df is not None:
                scan = self.scan_symbol(symbol, df)
                if scan.signals:  # 신호가 있는 경우만
                    results.append(scan)

        # 우선순위 내림차순 정렬
        results.sort(key=lambda x: x.priority, reverse=True)

        return results
