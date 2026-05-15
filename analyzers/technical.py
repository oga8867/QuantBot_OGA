"""
=============================================================================
analyzers/technical.py - 기술적 지표 분석기
=============================================================================

주가 데이터에서 기술적 지표(Technical Indicators)를 계산합니다.

기술적 분석이란?
- 과거의 가격/거래량 패턴으로 미래 가격을 예측하려는 분석 방법
- "역사는 반복된다" + "가격은 모든 것을 반영한다"가 기본 가정
- 100% 맞지는 않지만, 확률적 우위(edge)를 제공할 수 있음

여기서 구현하는 지표들:
1. SMA (단순이동평균) - 추세 방향 판단
2. EMA (지수이동평균) - 최근 데이터에 가중치
3. RSI (상대강도지수) - 과매수/과매도 판단
4. MACD - 추세 전환 감지
5. 볼린저 밴드 - 변동성 범위 + 돌파 감지
6. ATR (평균진실범위) - 변동성 측정 + 손절 기준
7. OBV (거래량 균형) - 거래량으로 추세 확인

★ 외부 라이브러리(pandas-ta, ta-lib) 대신 직접 구현하는 이유:
  1. 의존성 최소화 (ta-lib는 C 빌드가 필요해서 설치 까다로움)
  2. 원리를 이해하며 공부할 수 있음
  3. 커스터마이징이 쉬움 (지표 변형, 조합 등)
=============================================================================
"""

import numpy as np
import pandas as pd
from typing import Optional
from dataclasses import dataclass


@dataclass
class TechnicalSignal:
    """
    기술적 분석 결과를 담는 데이터 클래스

    Attributes:
        signal: "BUY", "SELL", "HOLD"
        strength: 신호 강도 (0.0 ~ 1.0)
        reasons: 신호 발생 이유 리스트
    """
    signal: str       # "BUY", "SELL", "HOLD"
    strength: float   # 0.0 ~ 1.0
    reasons: list     # ["RSI 과매도 반등", "MACD 골든크로스", ...]


class TechnicalAnalyzer:
    """
    기술적 지표 계산 및 매매 신호 생성기

    사용법:
        analyzer = TechnicalAnalyzer(config)
        df_with_indicators = analyzer.calculate_all(price_df)
        signal = analyzer.generate_signal(df_with_indicators)
    """

    def __init__(self, config=None):
        """
        Parameters:
            config: TechnicalConfig 객체 (None이면 기본값 사용)
        """
        # config가 없으면 기본값으로 생성
        if config is None:
            from config.settings import TechnicalConfig
            config = TechnicalConfig()

        self.config = config

    # =========================================================================
    # 이동평균 (Moving Averages)
    # =========================================================================

    def sma(self, series: pd.Series, period: int) -> pd.Series:
        """
        SMA (Simple Moving Average, 단순이동평균)

        최근 N일간의 종가를 단순 평균한 값.
        추세 방향을 부드럽게 보여줍니다.

        공식: SMA = (P1 + P2 + ... + Pn) / n

        해석:
        - 가격이 SMA 위에 있으면 → 상승 추세
        - 가격이 SMA 아래로 내려가면 → 하락 전환 가능
        - 단기 SMA가 장기 SMA를 상향 돌파 → 골든크로스 (매수 신호)
        - 단기 SMA가 장기 SMA를 하향 돌파 → 데드크로스 (매도 신호)

        Parameters:
            series: 주가 시계열 (보통 종가)
            period: 이동평균 기간 (일)

        Returns:
            SMA 시계열
        """
        return series.rolling(window=period).mean()

    def ema(self, series: pd.Series, period: int) -> pd.Series:
        """
        EMA (Exponential Moving Average, 지수이동평균)

        최근 데이터에 더 높은 가중치를 부여하는 이동평균.
        SMA보다 가격 변화에 더 빨리 반응합니다.

        공식: EMA_today = Price_today × k + EMA_yesterday × (1-k)
              k = 2 / (period + 1)  ← 평활 계수(smoothing factor)

        SMA vs EMA:
        - SMA: 모든 날에 동일한 가중치 → 느리지만 안정적
        - EMA: 최근 날에 높은 가중치 → 빠르지만 노이즈에 민감

        Parameters:
            series: 주가 시계열
            period: EMA 기간

        Returns:
            EMA 시계열
        """
        return series.ewm(span=period, adjust=False).mean()

    # =========================================================================
    # RSI (Relative Strength Index, 상대강도지수)
    # =========================================================================

    def rsi(self, series: pd.Series, period: int = None) -> pd.Series:
        """
        RSI (Relative Strength Index, 상대강도지수)

        일정 기간 동안 상승한 날의 평균 상승폭 vs 하락한 날의 평균 하락폭을
        비교하여 0~100 사이의 값으로 변환합니다.

        공식:
            RS = 평균 상승폭 / 평균 하락폭
            RSI = 100 - (100 / (1 + RS))

        해석:
        - RSI > 70: 과매수 (많이 올라서 쉬어갈 수 있음) → 매도 관심
        - RSI < 30: 과매도 (많이 떨어져서 반등 가능) → 매수 관심
        - RSI 50 근처: 중립

        주의사항:
        - 강한 추세장에서는 RSI가 오랫동안 70+ 또는 30- 유지 가능
        - RSI 단독보다는 다른 지표와 함께 사용하는 것이 좋음

        Parameters:
            series: 주가 시계열 (종가)
            period: RSI 기간 (기본 14일)

        Returns:
            RSI 시계열 (0~100)
        """
        if period is None:
            period = self.config.rsi_period

        # 일일 변화량 계산
        delta = series.diff()

        # 상승분과 하락분 분리
        # .where(조건, 0): 조건이 True인 곳은 원래 값, False인 곳은 0
        gain = delta.where(delta > 0, 0)  # 상승한 날만 (나머지 0)
        loss = (-delta.where(delta < 0, 0))  # 하락한 날만 (양수로 변환)

        # Wilder's Smoothing Method (EMA와 유사하지만 약간 다름)
        # 처음 N일은 단순평균, 이후는 지수이동평균 방식
        avg_gain = gain.rolling(window=period).mean()
        avg_loss = loss.rolling(window=period).mean()

        # RS (Relative Strength) 계산
        # avg_loss가 0이면 나눗셈 에러 방지
        rs = avg_gain / avg_loss.replace(0, np.nan)

        # RSI 계산
        rsi = 100 - (100 / (1 + rs))

        return rsi

    # =========================================================================
    # MACD (Moving Average Convergence Divergence)
    # =========================================================================

    def macd(self, series: pd.Series) -> tuple:
        """
        MACD (이동평균 수렴확산)

        단기 EMA와 장기 EMA의 차이로 추세의 방향과 강도를 측정합니다.

        구성 요소:
        - MACD Line = EMA(12) - EMA(26)  ← 두 이동평균의 차이
        - Signal Line = MACD의 EMA(9)    ← MACD를 다시 평활화
        - Histogram = MACD - Signal       ← 차이의 차이 (가속도 개념)

        해석:
        - MACD가 Signal을 상향 돌파 → 골든크로스 (매수 신호)
        - MACD가 Signal을 하향 돌파 → 데드크로스 (매도 신호)
        - Histogram이 0 위에서 커지면 → 상승 모멘텀 강화
        - Histogram이 0 아래에서 커지면 → 하락 모멘텀 강화

        Parameters:
            series: 주가 시계열 (종가)

        Returns:
            (macd_line, signal_line, histogram) 튜플
        """
        fast = self.config.macd_fast    # 12
        slow = self.config.macd_slow    # 26
        signal = self.config.macd_signal  # 9

        # MACD Line = 빠른 EMA - 느린 EMA
        ema_fast = self.ema(series, fast)
        ema_slow = self.ema(series, slow)
        macd_line = ema_fast - ema_slow

        # Signal Line = MACD의 EMA
        signal_line = self.ema(macd_line, signal)

        # Histogram = MACD - Signal (막대그래프로 표시됨)
        histogram = macd_line - signal_line

        return macd_line, signal_line, histogram

    # =========================================================================
    # 볼린저 밴드 (Bollinger Bands)
    # =========================================================================

    def bollinger_bands(self, series: pd.Series) -> tuple:
        """
        볼린저 밴드 (Bollinger Bands)

        이동평균을 중심으로 표준편차의 N배만큼 상/하한 밴드를 그립니다.
        가격의 정상적인 변동 범위를 시각화합니다.

        구성:
        - 중심선 = SMA(20)
        - 상단 밴드 = SMA(20) + 2×표준편차
        - 하단 밴드 = SMA(20) - 2×표준편차

        통계적 의미:
        - 2σ 범위 안에 가격이 있을 확률 ≈ 95%
        - 밴드 밖으로 나가면 "비정상적" 상황

        해석:
        - 가격이 상단 밴드 돌파 → 과매수 또는 강한 상승 추세
        - 가격이 하단 밴드 돌파 → 과매도 또는 강한 하락 추세
        - 밴드 폭이 좁아지면(스퀴즈) → 큰 움직임 예고
        - %B = (가격 - 하단) / (상단 - 하단): 0~1 사이, 밴드 내 위치

        Parameters:
            series: 주가 시계열 (종가)

        Returns:
            (upper_band, middle_band, lower_band) 튜플
        """
        period = self.config.bb_period  # 20
        std_dev = self.config.bb_std    # 2.0

        # 중심선 (SMA)
        middle = self.sma(series, period)

        # 표준편차 계산
        std = series.rolling(window=period).std()

        # 상단/하단 밴드
        upper = middle + (std_dev * std)
        lower = middle - (std_dev * std)

        return upper, middle, lower

    # =========================================================================
    # ATR (Average True Range, 평균진실범위)
    # =========================================================================

    def atr(self, df: pd.DataFrame, period: int = None) -> pd.Series:
        """
        ATR (Average True Range, 평균진실범위)

        하루 동안의 "진짜" 변동폭을 측정합니다.
        갭(전일 종가와 당일 시가의 차이)까지 반영하므로
        단순히 (고가-저가)보다 더 정확한 변동성 지표입니다.

        True Range = max(
            고가 - 저가,            ← 당일 내 변동
            |고가 - 전일종가|,      ← 갭 상승 반영
            |저가 - 전일종가|       ← 갭 하락 반영
        )

        ATR = True Range의 N일 이동평균

        활용:
        - 손절선 설정: 현재가 - 2×ATR (평균 변동의 2배 밖에서 손절)
        - 포지션 사이징: ATR이 크면 적게, 작으면 많이 투자
        - 변동성 비교: ATR/가격 = 상대적 변동성

        Parameters:
            df: OHLCV DataFrame (High, Low, Close 컬럼 필요)
            period: ATR 기간 (기본 14일)

        Returns:
            ATR 시계열
        """
        if period is None:
            period = self.config.atr_period

        high = df["High"]
        low = df["Low"]
        close = df["Close"]

        # 전일 종가
        prev_close = close.shift(1)

        # True Range: 세 가지 값 중 최대값
        tr1 = high - low                   # 당일 내 변동
        tr2 = (high - prev_close).abs()    # 갭 상승 포함
        tr3 = (low - prev_close).abs()     # 갭 하락 포함

        # 세 값 중 최대값이 True Range
        true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        # ATR = True Range의 이동평균
        atr = true_range.rolling(window=period).mean()

        return atr

    # =========================================================================
    # OBV (On-Balance Volume, 거래량 균형)
    # =========================================================================

    def obv(self, df: pd.DataFrame) -> pd.Series:
        """
        OBV (On-Balance Volume, 거래량 균형)

        가격이 오른 날의 거래량은 더하고, 내린 날의 거래량은 빼서
        누적합을 구합니다. 거래량으로 매수/매도 압력을 측정합니다.

        로직:
        - 오늘 종가 > 어제 종가 → OBV += 오늘 거래량 (매수세)
        - 오늘 종가 < 어제 종가 → OBV -= 오늘 거래량 (매도세)
        - 변동 없음 → OBV 변화 없음

        해석:
        - 가격 상승 + OBV 상승 → 건강한 상승 (거래량이 뒷받침)
        - 가격 상승 + OBV 하락 → 다이버전스 (상승 지속 어려울 수 있음)
        - 가격 하락 + OBV 상승 → 바닥 다지기 가능

        Parameters:
            df: OHLCV DataFrame

        Returns:
            OBV 시계열
        """
        close = df["Close"]
        volume = df["Volume"]

        # 가격 변화 방향: +1(상승), -1(하락), 0(변동없음)
        direction = np.sign(close.diff())

        # OBV = (방향 × 거래량)의 누적합
        obv = (direction * volume).cumsum()

        return obv

    # =========================================================================
    # 종합 계산 + 신호 생성
    # =========================================================================

    def calculate_all(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        모든 기술적 지표를 한번에 계산하여 DataFrame에 추가

        Parameters:
            df: OHLCV DataFrame (Open, High, Low, Close, Volume)

        Returns:
            기술적 지표가 추가된 DataFrame
        """
        result = df.copy()
        close = result["Close"]

        # 이동평균
        result["SMA_20"] = self.sma(close, self.config.sma_short)
        result["SMA_50"] = self.sma(close, self.config.sma_long)
        result["SMA_200"] = self.sma(close, self.config.sma_trend)
        result["EMA_12"] = self.ema(close, 12)
        result["EMA_26"] = self.ema(close, 26)

        # RSI
        result["RSI"] = self.rsi(close)

        # MACD
        macd_line, signal_line, histogram = self.macd(close)
        result["MACD"] = macd_line
        result["MACD_Signal"] = signal_line
        result["MACD_Hist"] = histogram

        # 볼린저 밴드
        upper, middle, lower = self.bollinger_bands(close)
        result["BB_Upper"] = upper
        result["BB_Middle"] = middle
        result["BB_Lower"] = lower
        # %B: 밴드 내 위치 (0=하단, 1=상단)
        result["BB_PctB"] = (close - lower) / (upper - lower)

        # ATR
        result["ATR"] = self.atr(result)

        # OBV
        result["OBV"] = self.obv(result)

        # 거래량 비율 (20일 평균 대비)
        result["Volume_Ratio"] = (
            result["Volume"] / result["Volume"].rolling(20).mean()
        )

        return result

    def generate_signal(self, df: pd.DataFrame) -> TechnicalSignal:
        """
        기술적 지표를 종합하여 매매 신호 생성

        최신 데이터(마지막 행)를 기준으로 여러 조건을 체크하고,
        매수/매도 점수를 합산하여 최종 신호를 결정합니다.

        Parameters:
            df: calculate_all()로 지표가 추가된 DataFrame

        Returns:
            TechnicalSignal (signal, strength, reasons)
        """
        if df.empty or len(df) < 50:
            return TechnicalSignal("HOLD", 0.0, ["데이터 부족"])

        # 최신 데이터 (가장 마지막 행)
        latest = df.iloc[-1]
        prev = df.iloc[-2]  # 전일 (크로스오버 판단용)

        buy_score = 0.0   # 매수 점수 합산
        sell_score = 0.0  # 매도 점수 합산
        reasons = []

        # ─── 1. RSI 판단 ──────────────────────────────────────────────
        rsi_val = latest.get("RSI", 50)
        if rsi_val < self.config.rsi_oversold:
            buy_score += 0.25
            reasons.append(f"RSI 과매도 ({rsi_val:.1f})")
        elif rsi_val > self.config.rsi_overbought:
            sell_score += 0.25
            reasons.append(f"RSI 과매수 ({rsi_val:.1f})")

        # ─── 2. MACD 크로스오버 ───────────────────────────────────────
        macd_now = latest.get("MACD", 0)
        signal_now = latest.get("MACD_Signal", 0)
        macd_prev = prev.get("MACD", 0)
        signal_prev = prev.get("MACD_Signal", 0)

        # 골든크로스: MACD가 Signal을 아래에서 위로 돌파
        if macd_prev <= signal_prev and macd_now > signal_now:
            buy_score += 0.30
            reasons.append("MACD 골든크로스")
        # 데드크로스: MACD가 Signal을 위에서 아래로 돌파
        elif macd_prev >= signal_prev and macd_now < signal_now:
            sell_score += 0.30
            reasons.append("MACD 데드크로스")

        # ─── 3. 이동평균 추세 ─────────────────────────────────────────
        close = latest["Close"]
        sma_20 = latest.get("SMA_20", close)
        sma_50 = latest.get("SMA_50", close)

        if close > sma_20 > sma_50:
            buy_score += 0.15
            reasons.append("가격 > SMA20 > SMA50 (상승 정배열)")
        elif close < sma_20 < sma_50:
            sell_score += 0.15
            reasons.append("가격 < SMA20 < SMA50 (하락 역배열)")

        # ─── 4. 볼린저 밴드 ───────────────────────────────────────────
        bb_pctb = latest.get("BB_PctB", 0.5)
        if bb_pctb < 0:
            buy_score += 0.15
            reasons.append(f"볼린저 하단 돌파 (%B={bb_pctb:.2f})")
        elif bb_pctb > 1:
            sell_score += 0.15
            reasons.append(f"볼린저 상단 돌파 (%B={bb_pctb:.2f})")

        # ─── 5. 거래량 확인 ───────────────────────────────────────────
        vol_ratio = latest.get("Volume_Ratio", 1.0)
        if vol_ratio > 2.0:
            # 거래량 급증 시 현재 방향 신호 강화
            if buy_score > sell_score:
                buy_score += 0.15
                reasons.append(f"거래량 급증 ({vol_ratio:.1f}x) - 매수 확인")
            elif sell_score > buy_score:
                sell_score += 0.15
                reasons.append(f"거래량 급증 ({vol_ratio:.1f}x) - 매도 확인")

        # ─── 최종 신호 결정 ───────────────────────────────────────────
        net_score = buy_score - sell_score

        if net_score > 0.3:
            return TechnicalSignal("BUY", min(buy_score, 1.0), reasons)
        elif net_score < -0.3:
            return TechnicalSignal("SELL", min(sell_score, 1.0), reasons)
        else:
            if not reasons:
                reasons.append("뚜렷한 신호 없음")
            return TechnicalSignal("HOLD", 1.0 - abs(net_score), reasons)
