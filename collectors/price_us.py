"""
=============================================================================
collectors/price_us.py - 미국 주식 가격 수집기
=============================================================================

yfinance 라이브러리를 사용하여 미국(NYSE/NASDAQ) 주가 데이터를 수집합니다.

yfinance란?
- Yahoo Finance의 비공식 Python 래퍼
- 무료, API 키 불필요
- OHLCV(시가/고가/저가/종가/거래량) + 재무제표 + 실적 등 제공
- 단점: 가끔 데이터가 빠지거나 지연될 수 있음 (무료이므로)

사용 예:
    collector = PriceCollectorUS()
    df = collector.safe_collect("AAPL", period="1y")
=============================================================================
"""

import pandas as pd
from typing import Optional
from .base import BaseCollector

try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False


class PriceCollectorUS(BaseCollector):
    """
    미국 주식 가격 데이터 수집기

    yfinance를 통해 OHLCV 데이터를 가져옵니다.
    추가로 기업 정보, 재무제표, 실적발표일 등도 수집할 수 있습니다.
    """

    def __init__(self):
        super().__init__(name="price_us")

    def collect(
        self,
        symbol: str,
        period: str = "1y",
        interval: str = "1d"
    ) -> Optional[pd.DataFrame]:
        """
        미국 주가 OHLCV 데이터 수집

        Parameters:
            symbol: 미국 종목 코드 (예: "AAPL", "MSFT", "SPY")
            period: 수집 기간
                - "1mo" = 1개월, "3mo" = 3개월
                - "6mo" = 6개월, "1y" = 1년
                - "2y" = 2년, "5y" = 5년, "max" = 전체
            interval: 데이터 간격
                - "1d" = 일봉, "1wk" = 주봉, "1mo" = 월봉
                - "1h" = 시간봉 (최근 730일만 가능)

        Returns:
            DataFrame with columns: Open, High, Low, Close, Volume
            인덱스는 DatetimeIndex
        """
        if not YFINANCE_AVAILABLE:
            self.logger.error("yfinance가 설치되지 않았습니다: pip install yfinance")
            return None

        if not self.validate_symbol(symbol):
            self.logger.error(f"유효하지 않은 종목 코드: {symbol}")
            return None

        # yfinance Ticker 객체 생성
        ticker = yf.Ticker(symbol)

        # 주가 데이터 다운로드
        # auto_adjust=True: 수정 종가(액면분할, 배당 반영)로 자동 조정
        df = ticker.history(period=period, interval=interval, auto_adjust=True)

        if df.empty:
            self.logger.warning(f"'{symbol}' 데이터가 비어있습니다. 종목코드를 확인하세요.")
            return None

        # 컬럼명 정리 (yfinance는 이미 영문 컬럼명 사용)
        # 필요한 컬럼만 선택 (Dividends, Stock Splits 등 제외)
        columns_to_keep = ["Open", "High", "Low", "Close", "Volume"]
        df = df[[col for col in columns_to_keep if col in df.columns]]

        # 인덱스 이름 통일
        df.index.name = "Date"

        return df

    def get_info(self, symbol: str) -> Optional[dict]:
        """
        기업 기본 정보 수집

        반환되는 주요 정보:
        - shortName: 기업명
        - sector: 섹터
        - industry: 산업
        - marketCap: 시가총액
        - trailingPE: PER (주가수익비율)
        - priceToBook: PBR (주가순자산비율)
        - forwardEps: 예상 EPS
        - dividendYield: 배당수익률
        - longBusinessSummary: 사업 설명 (영문)

        Parameters:
            symbol: 미국 종목 코드

        Returns:
            기업 정보 딕셔너리, 실패 시 None
        """
        if not YFINANCE_AVAILABLE:
            return None

        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info

            if not info or "symbol" not in info:
                return None

            return info
        except Exception as e:
            self.logger.error(f"기업정보 수집 실패 ({symbol}): {e}")
            return None

    def get_financials(self, symbol: str) -> Optional[dict]:
        """
        재무제표 데이터 수집

        Parameters:
            symbol: 종목 코드

        Returns:
            딕셔너리: {
                "income_stmt": 손익계산서 DataFrame,
                "balance_sheet": 대차대조표 DataFrame,
                "cash_flow": 현금흐름표 DataFrame
            }
        """
        if not YFINANCE_AVAILABLE:
            return None

        try:
            ticker = yf.Ticker(symbol)
            return {
                "income_stmt": ticker.income_stmt,
                "balance_sheet": ticker.balance_sheet,
                "cash_flow": ticker.cash_flow,
            }
        except Exception as e:
            self.logger.error(f"재무제표 수집 실패 ({symbol}): {e}")
            return None
