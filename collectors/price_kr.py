"""
=============================================================================
collectors/price_kr.py - 한국 주식 가격 수집기
=============================================================================

pykrx 라이브러리를 사용하여 한국(KOSPI/KOSDAQ) 주가 데이터를 수집합니다.

pykrx란?
- 한국거래소(KRX)에서 직접 데이터를 크롤링하는 라이브러리
- 무료, API 키 불필요
- OHLCV + 시가총액 + 거래대금 제공
- yfinance의 한국 데이터보다 안정적 (직접 KRX에서 가져오므로)

한국 종목코드 규칙:
- KOSPI: 6자리 숫자 (예: "005930" = 삼성전자)
- KOSDAQ: 6자리 숫자 (예: "247540" = 에코프로비엠)
- yfinance에서 쓸 때는 .KS(코스피) 또는 .KQ(코스닥) 붙임
- pykrx에서는 순수 6자리 숫자만 사용

사용 예:
    collector = PriceCollectorKR()
    df = collector.safe_collect("005930")  # 삼성전자
=============================================================================
"""

import pandas as pd
from typing import Optional
from datetime import datetime, timedelta
from .base import BaseCollector

try:
    from pykrx import stock as krx_stock
    PYKRX_AVAILABLE = True
except ImportError:
    PYKRX_AVAILABLE = False

# yfinance를 폴백 데이터 소스로 사용
# pykrx가 KRX API 장애/로그인 실패로 데이터를 못 가져오면
# yfinance로 한국 주식 데이터를 가져옵니다 (.KS/.KQ 심볼 지원)
try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False


class PriceCollectorKR(BaseCollector):
    """
    한국 주식 가격 데이터 수집기

    pykrx를 통해 KRX(한국거래소)에서 직접 OHLCV 데이터를 가져옵니다.
    """

    def __init__(self):
        super().__init__(name="price_kr")

    def _normalize_symbol(self, symbol: str) -> str:
        """
        종목코드 정규화: yfinance 형식(.KS/.KQ)이 들어와도 순수 코드로 변환

        예:
            "005930.KS" → "005930"
            "247540.KQ" → "247540"
            "005930"    → "005930"
        """
        # .KS 또는 .KQ 접미사 제거
        if symbol.endswith(".KS") or symbol.endswith(".KQ"):
            return symbol[:-3]
        return symbol

    def collect(
        self,
        symbol: str,
        period: str = "1y",
        interval: str = "1d"
    ) -> Optional[pd.DataFrame]:
        """
        한국 주가 OHLCV 데이터 수집 (pykrx 우선, yfinance 폴백)

        데이터 소스 우선순위:
        1. pykrx (KRX 직접 크롤링, 가장 정확)
        2. yfinance (Yahoo Finance, pykrx 실패 시 자동 전환)

        pykrx가 KRX 로그인 실패/API 장애로 데이터를 못 가져오면
        yfinance로 자동 폴백하여 데이터를 수집합니다.
        이 덕분에 KRX 인증 없이도 한국 종목 분석이 가능합니다.

        Parameters:
            symbol: 한국 종목 코드
                - "005930" 또는 "005930.KS" (삼성전자)
                - "247540" 또는 "247540.KQ" (에코프로비엠)
            period: 수집 기간 ("3mo", "6mo", "1y", "2y", "5y")
            interval: "1d"만 지원 (pykrx 제약)

        Returns:
            DataFrame with columns: Open, High, Low, Close, Volume
        """
        # 종목코드 정규화 (.KS/.KQ 제거)
        pure_code = self._normalize_symbol(symbol)

        if not pure_code.isdigit() or len(pure_code) != 6:
            self.logger.error(
                f"유효하지 않은 한국 종목코드: {symbol} "
                f"(6자리 숫자여야 합니다. 예: 005930)"
            )
            return None

        # ── 1차: pykrx로 시도 ──
        df = self._collect_pykrx(pure_code, period)

        # ── 2차: pykrx 실패 시 yfinance 폴백 ──
        if df is None or df.empty:
            self.logger.info(
                f"[KR 수집] pykrx 실패 → yfinance 폴백 시도: {symbol}"
            )
            df = self._collect_yfinance(symbol, pure_code, period, interval)

        if df is None or df.empty:
            self.logger.warning(
                f"'{symbol}' 데이터 수집 실패 (pykrx + yfinance 모두 실패)"
            )
            return None

        return df

    def _collect_pykrx(
        self, pure_code: str, period: str
    ) -> Optional[pd.DataFrame]:
        """
        pykrx를 사용한 데이터 수집 (1차 소스)

        KRX(한국거래소)에서 직접 데이터를 가져옵니다.
        가장 정확하지만, KRX API 장애/인증 문제로 실패할 수 있습니다.
        """
        if not PYKRX_AVAILABLE:
            return None

        try:
            end_date = datetime.now()
            period_map = {
                "1mo": timedelta(days=30),
                "3mo": timedelta(days=90),
                "6mo": timedelta(days=180),
                "1y": timedelta(days=365),
                "2y": timedelta(days=730),
                "5y": timedelta(days=1825),
            }
            delta = period_map.get(period, timedelta(days=365))
            start_date = end_date - delta

            start_str = start_date.strftime("%Y%m%d")
            end_str = end_date.strftime("%Y%m%d")

            df = krx_stock.get_market_ohlcv_by_date(
                fromdate=start_str,
                todate=end_str,
                ticker=pure_code
            )

            if df.empty:
                return None

            # 컬럼명을 영문으로 통일
            column_map = {
                "시가": "Open",
                "고가": "High",
                "저가": "Low",
                "종가": "Close",
                "거래량": "Volume",
            }
            df = df.rename(columns=column_map)

            columns_to_keep = ["Open", "High", "Low", "Close", "Volume"]
            df = df[[col for col in columns_to_keep if col in df.columns]]
            df.index.name = "Date"

            self.logger.debug(f"[KR 수집] pykrx 성공: {pure_code} ({len(df)}행)")
            return df

        except Exception as e:
            self.logger.warning(f"[KR 수집] pykrx 에러 ({pure_code}): {e}")
            return None

    def _collect_yfinance(
        self,
        symbol: str,
        pure_code: str,
        period: str,
        interval: str = "1d"
    ) -> Optional[pd.DataFrame]:
        """
        yfinance를 사용한 데이터 수집 (폴백 소스)

        Yahoo Finance에서 한국 주식 데이터를 가져옵니다.
        심볼 형식: "005930.KS" (코스피) 또는 "247540.KQ" (코스닥)

        pykrx보다 약간의 지연이 있을 수 있지만,
        KRX 인증 없이도 데이터를 가져올 수 있어 폴백으로 유용합니다.
        """
        if not YFINANCE_AVAILABLE:
            self.logger.warning("[KR 수집] yfinance 미설치, 폴백 불가")
            return None

        try:
            # yfinance용 심볼 생성 (.KS 또는 .KQ 접미사 필요)
            if symbol.endswith(".KS") or symbol.endswith(".KQ"):
                yf_symbol = symbol
            else:
                yf_symbol = f"{pure_code}.KS"

            ticker = yf.Ticker(yf_symbol)
            df = ticker.history(period=period, interval=interval, auto_adjust=True)

            # .KS로 안 되면 .KQ(코스닥)로 재시도
            if df.empty and not symbol.endswith(".KQ"):
                yf_symbol = f"{pure_code}.KQ"
                ticker = yf.Ticker(yf_symbol)
                df = ticker.history(period=period, interval=interval, auto_adjust=True)

            if df.empty:
                return None

            columns_to_keep = ["Open", "High", "Low", "Close", "Volume"]
            df = df[[col for col in columns_to_keep if col in df.columns]]
            df.index.name = "Date"

            self.logger.info(
                f"[KR 수집] yfinance 폴백 성공: {yf_symbol} ({len(df)}행)"
            )
            return df

        except Exception as e:
            self.logger.warning(f"[KR 수집] yfinance 폴백 에러 ({symbol}): {e}")
            return None

    def get_market_cap(self, symbol: str):
        """
        시가총액 데이터 수집

        시가총액은 기업의 크기를 나타내며, 팩터 분석에서 중요합니다.
        시가총액 = 주가 × 발행주식수
        """
        if not PYKRX_AVAILABLE:
            return None

        pure_code = self._normalize_symbol(symbol)
        end_date = datetime.now()
        start_date = end_date - timedelta(days=30)
        start_str = start_date.strftime("%Y%m%d")
        end_str = end_date.strftime("%Y%m%d")

        try:
            df = krx_stock.get_market_cap_by_date(
                fromdate=start_str,
                todate=end_str,
                ticker=pure_code
            )
            return df if not df.empty else None
        except Exception as e:
            self.logger.warning(f"시가총액 수집 실패 ({symbol}): {e}")
            return None
