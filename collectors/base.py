"""
=============================================================================
collectors/base.py - 데이터 수집기 추상 베이스 클래스
=============================================================================

모든 수집기(Collector)가 상속받는 기본 인터페이스를 정의합니다.

설계 원칙:
- 각 수집기는 독립적으로 동작해야 합니다 (다른 수집기가 실패해도 영향 없음)
- 모든 외부 API 호출은 try/except로 감싸서 Graceful Degradation을 보장합니다
- 수집 결과는 항상 pandas DataFrame으로 반환합니다
=============================================================================
"""

from abc import ABC, abstractmethod
from typing import Optional
import pandas as pd
import logging

# 로거 설정 - 각 수집기에서 상속받아 사용
logger = logging.getLogger(__name__)


class BaseCollector(ABC):
    """
    데이터 수집기의 추상 베이스 클래스 (Abstract Base Class)

    ABC를 상속받으면 @abstractmethod로 표시된 메서드를 반드시 구현해야 합니다.
    이렇게 하면 모든 수집기가 동일한 인터페이스를 가지게 됩니다.

    사용 예:
        class PriceCollectorUS(BaseCollector):
            def collect(self, symbol):
                ...  # yfinance로 데이터 수집

    왜 이렇게 하나요?
    → 나중에 수집기를 교체하거나 추가할 때 일관된 방식으로 사용할 수 있습니다.
    → 예를 들어 yfinance가 죽으면 다른 소스로 교체하기 쉽습니다.
    """

    def __init__(self, name: str):
        """
        Parameters:
            name: 수집기 이름 (로깅에 사용)
        """
        self.name = name
        self.logger = logging.getLogger(f"collector.{name}")

    @abstractmethod
    def collect(self, symbol: str, **kwargs) -> Optional[pd.DataFrame]:
        """
        데이터를 수집하여 DataFrame으로 반환

        Parameters:
            symbol: 종목 코드 (예: "AAPL", "005930.KS")
            **kwargs: 추가 파라미터 (기간, 간격 등)

        Returns:
            수집된 데이터 DataFrame, 실패 시 None
        """
        pass

    def safe_collect(self, symbol: str, **kwargs) -> Optional[pd.DataFrame]:
        """
        안전한 수집 래퍼 - 예외 발생 시 None 반환 (Graceful Degradation)

        collect()를 직접 호출하는 대신 이 메서드를 사용하면,
        어떤 에러가 발생하더라도 프로그램이 죽지 않고 계속 실행됩니다.

        Parameters:
            symbol: 종목 코드
            **kwargs: 추가 파라미터

        Returns:
            DataFrame 또는 None (실패 시)
        """
        try:
            self.logger.info(f"[{self.name}] '{symbol}' 데이터 수집 시작...")
            data = self.collect(symbol, **kwargs)

            if data is not None and not data.empty:
                self.logger.info(
                    f"[{self.name}] '{symbol}' 수집 완료: {len(data)}행"
                )
                return data
            else:
                self.logger.warning(
                    f"[{self.name}] '{symbol}' 수집 결과가 비어있습니다."
                )
                return None

        except Exception as e:
            # 어떤 에러든 잡아서 로깅만 하고 None 반환
            # → 이 수집기가 실패해도 다른 수집기는 정상 동작
            self.logger.error(
                f"[{self.name}] '{symbol}' 수집 실패: {type(e).__name__}: {e}"
            )
            return None

    def validate_symbol(self, symbol: str) -> bool:
        """
        종목 코드 형식 기본 검증

        Parameters:
            symbol: 검증할 종목 코드

        Returns:
            유효하면 True
        """
        if not symbol or not isinstance(symbol, str):
            return False
        # 최소 1자 이상, 공백 없음
        return len(symbol.strip()) > 0 and " " not in symbol
