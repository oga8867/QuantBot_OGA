"""
strategy/factor.py - 팩터 투자 분석 모듈
=============================================================================

4가지 핵심 팩터로 종목의 투자 매력도를 평가합니다.

[팩터 투자란?]
특정 "팩터(요인)"를 가진 종목이 장기적으로 초과수익을 내는 경향이 있다는
학술 연구(Fama-French 등)에 기반한 투자 방법론입니다.

[4가지 팩터]
1. 모멘텀(Momentum): 최근 상승한 종목이 계속 오르는 경향
   - 측정: 3개월/6개월/12개월 수익률
   - 근거: Jegadeesh & Titman (1993)

2. 밸류(Value): 저평가된 종목이 장기적으로 수렴하는 경향
   - 측정: PER, PBR, PSR 등 밸류에이션 지표
   - 근거: Fama & French (1992)

3. 퀄리티(Quality): 재무 건전성이 높은 기업이 안정적 수익
   - 측정: ROE, 부채비율, 이익 안정성
   - 근거: Novy-Marx (2013)

4. 사이즈(Size): 소형주가 대형주보다 높은 수익률
   - 측정: 시가총액
   - 근거: Fama & French (1993) SMB 팩터

사용법:
    from strategy.factor import FactorAnalyzer
    fa = FactorAnalyzer()
    score = fa.analyze(symbol="AAPL")
    # score: ModuleScore(name="factor", score=-0.3~+1.0, ...)
=============================================================================
"""

from dataclasses import dataclass
from typing import Optional, Dict, List

try:
    import yfinance as yf
    YF_AVAILABLE = True
except ImportError:
    YF_AVAILABLE = False


@dataclass
class FactorScores:
    """개별 팩터 점수 모음"""
    momentum: float = 0.0      # -1 ~ +1
    value: float = 0.0         # -1 ~ +1
    quality: float = 0.0       # -1 ~ +1
    size: float = 0.0          # -1 ~ +1
    combined: float = 0.0      # 가중 평균
    reasons: list = None

    def __post_init__(self):
        if self.reasons is None:
            self.reasons = []


class FactorAnalyzer:
    """
    팩터 투자 분석기

    yfinance에서 종목 정보를 가져와 4가지 팩터 점수를 계산합니다.
    각 팩터는 -1(매우 부정) ~ +1(매우 긍정) 범위입니다.
    """

    # 팩터별 가중치 (합계 1.0)
    WEIGHTS = {
        "momentum": 0.30,  # 모멘텀 가장 높은 비중 (단기 트레이딩에 유효)
        "value": 0.25,     # 밸류
        "quality": 0.25,   # 퀄리티
        "size": 0.20,      # 사이즈
    }

    def analyze(self, symbol: str, df=None) -> FactorScores:
        """
        종목의 팩터 점수를 계산합니다.

        Parameters:
            symbol: 종목 코드 (예: "AAPL", "005930.KS")
            df: 가격 DataFrame (있으면 모멘텀 계산에 사용)

        Returns:
            FactorScores: 4가지 팩터 + 종합 점수
        """
        scores = FactorScores()

        # 모멘텀 (가격 데이터 기반)
        scores.momentum = self._calc_momentum(symbol, df)

        # yfinance 정보 기반 팩터
        info = self._get_info(symbol)
        if info:
            scores.value = self._calc_value(info)
            scores.quality = self._calc_quality(info)
            scores.size = self._calc_size(info)

        # 종합 점수 (가중 평균)
        scores.combined = (
            scores.momentum * self.WEIGHTS["momentum"]
            + scores.value * self.WEIGHTS["value"]
            + scores.quality * self.WEIGHTS["quality"]
            + scores.size * self.WEIGHTS["size"]
        )

        # 근거 생성
        scores.reasons = self._build_reasons(scores)

        return scores

    def _get_info(self, symbol: str) -> Optional[Dict]:
        """yfinance에서 종목 기본 정보 조회"""
        if not YF_AVAILABLE:
            return None
        try:
            ticker = yf.Ticker(symbol)
            return ticker.info
        except Exception:
            return None

    def _calc_momentum(self, symbol: str, df=None) -> float:
        """
        모멘텀 팩터 계산

        [계산 방법]
        - 1개월/3개월/6개월 수익률을 구함
        - 가중 평균: 최근일수록 높은 가중치
        - 결과를 -1~+1로 정규화

        모멘텀이 높으면 (+) → 상승 추세
        모멘텀이 낮으면 (-) → 하락 추세
        """
        try:
            if df is not None and len(df) >= 20:
                close = df["Close"]
            elif YF_AVAILABLE:
                ticker = yf.Ticker(symbol)
                hist = ticker.history(period="6mo")
                if hist.empty:
                    return 0.0
                close = hist["Close"]
            else:
                return 0.0

            if len(close) < 20:
                return 0.0

            # 기간별 수익률
            ret_1m = (close.iloc[-1] / close.iloc[-21] - 1) if len(close) >= 21 else 0
            ret_3m = (close.iloc[-1] / close.iloc[-63] - 1) if len(close) >= 63 else ret_1m
            ret_6m = (close.iloc[-1] / close.iloc[-126] - 1) if len(close) >= 126 else ret_3m

            # 가중 평균 (최근 > 과거)
            mom_raw = ret_1m * 0.5 + ret_3m * 0.3 + ret_6m * 0.2

            # -1 ~ +1로 클리핑 (±50% 이상은 극단값)
            return max(min(mom_raw * 2, 1.0), -1.0)

        except Exception:
            return 0.0

    def _calc_value(self, info: Dict) -> float:
        """
        밸류 팩터 계산

        [핵심 지표]
        - PER (주가수익비율): 낮을수록 저평가
          업종 평균 15 기준, 10 이하면 매력적
        - PBR (주가순자산비율): 낮을수록 저평가
          1.0 이하면 순자산 이하 거래 (매우 저평가)
        - Forward PER: 미래 실적 기준 PER

        점수: 저평가일수록 높은 점수 (+)
        """
        scores = []

        # PER 점수
        per = info.get("trailingPE") or info.get("forwardPE")
        if per and per > 0:
            if per < 10:
                scores.append(0.8)   # 매우 저평가
            elif per < 15:
                scores.append(0.4)   # 저평가
            elif per < 25:
                scores.append(0.0)   # 적정
            elif per < 40:
                scores.append(-0.3)  # 고평가
            else:
                scores.append(-0.6)  # 매우 고평가

        # PBR 점수
        pbr = info.get("priceToBook")
        if pbr and pbr > 0:
            if pbr < 1.0:
                scores.append(0.8)
            elif pbr < 2.0:
                scores.append(0.3)
            elif pbr < 5.0:
                scores.append(-0.1)
            else:
                scores.append(-0.5)

        # PSR (주가매출비율)
        psr = info.get("priceToSalesTrailing12Months")
        if psr and psr > 0:
            if psr < 1.0:
                scores.append(0.6)
            elif psr < 3.0:
                scores.append(0.2)
            elif psr < 10.0:
                scores.append(-0.2)
            else:
                scores.append(-0.5)

        return sum(scores) / len(scores) if scores else 0.0

    def _calc_quality(self, info: Dict) -> float:
        """
        퀄리티 팩터 계산

        [핵심 지표]
        - ROE (자기자본이익률): 높을수록 수익성 좋음 (15%+ 우수)
        - 영업이익률: 높을수록 경쟁력 (20%+ 우수)
        - 부채비율: 낮을수록 안전 (0.5 이하 건전)

        점수: 재무 건전할수록 높은 점수 (+)
        """
        scores = []

        # ROE
        roe = info.get("returnOnEquity")
        if roe is not None:
            if roe > 0.25:
                scores.append(0.8)
            elif roe > 0.15:
                scores.append(0.5)
            elif roe > 0.08:
                scores.append(0.1)
            elif roe > 0:
                scores.append(-0.2)
            else:
                scores.append(-0.6)  # 적자

        # 영업이익률
        margin = info.get("operatingMargins")
        if margin is not None:
            if margin > 0.30:
                scores.append(0.8)
            elif margin > 0.15:
                scores.append(0.4)
            elif margin > 0.05:
                scores.append(0.0)
            elif margin > 0:
                scores.append(-0.3)
            else:
                scores.append(-0.7)

        # 부채비율 (Debt/Equity)
        de = info.get("debtToEquity")
        if de is not None:
            de_ratio = de / 100  # yfinance는 %로 줌
            if de_ratio < 0.3:
                scores.append(0.6)
            elif de_ratio < 0.7:
                scores.append(0.2)
            elif de_ratio < 1.5:
                scores.append(-0.2)
            else:
                scores.append(-0.6)

        return sum(scores) / len(scores) if scores else 0.0

    def _calc_size(self, info: Dict) -> float:
        """
        사이즈 팩터 계산

        [개념]
        소형주 프리미엄: 시가총액이 작은 기업이
        장기적으로 더 높은 수익률을 제공하는 경향

        시총 기준 (USD):
        - 마이크로캡 (<300M): +0.8
        - 소형 (300M~2B): +0.4
        - 중형 (2B~10B): +0.1
        - 대형 (10B~100B): -0.1
        - 메가캡 (>100B): -0.3
        """
        mcap = info.get("marketCap", 0)
        if mcap <= 0:
            return 0.0

        mcap_b = mcap / 1e9  # 십억 달러 단위

        if mcap_b < 0.3:
            return 0.8
        elif mcap_b < 2:
            return 0.4
        elif mcap_b < 10:
            return 0.1
        elif mcap_b < 100:
            return -0.1
        else:
            return -0.3

    def _build_reasons(self, scores: FactorScores) -> List[str]:
        """팩터 분석 근거 텍스트 생성"""
        reasons = []

        if scores.momentum > 0.3:
            reasons.append(f"강한 상승 모멘텀 ({scores.momentum:.2f})")
        elif scores.momentum < -0.3:
            reasons.append(f"하락 모멘텀 ({scores.momentum:.2f})")

        if scores.value > 0.3:
            reasons.append(f"저평가 매력 ({scores.value:.2f})")
        elif scores.value < -0.3:
            reasons.append(f"고평가 주의 ({scores.value:.2f})")

        if scores.quality > 0.3:
            reasons.append(f"우수한 재무 ({scores.quality:.2f})")
        elif scores.quality < -0.3:
            reasons.append(f"재무 취약 ({scores.quality:.2f})")

        if scores.size > 0.3:
            reasons.append(f"소형주 프리미엄 ({scores.size:.2f})")

        if not reasons:
            reasons.append(f"팩터 중립 (종합 {scores.combined:.2f})")

        return reasons
