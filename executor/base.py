"""
=============================================================================
executor/base.py - 주문 실행기 추상 베이스 클래스
=============================================================================

모든 주문 실행기(Executor)가 상속받는 인터페이스를 정의합니다.
Alpaca, 한국투자증권, 모의매매 등 브로커별 실행기가 이 인터페이스를 구현합니다.

실행기의 역할:
1. 주문 제출 (시장가/지정가/손절주문)
2. 주문 상태 확인
3. 포지션 조회
4. 계좌 잔고 확인
5. 주문 취소
=============================================================================
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict, List
from dataclasses import dataclass
from enum import Enum
from datetime import datetime


class OrderSide(Enum):
    """주문 방향"""
    BUY = "buy"
    SELL = "sell"


class OrderType(Enum):
    """주문 유형"""
    MARKET = "market"      # 시장가 (즉시 체결, 가격 보장 없음)
    LIMIT = "limit"        # 지정가 (가격 지정, 체결 보장 없음)
    STOP = "stop"          # 스탑 (특정 가격 도달 시 시장가 전환)
    STOP_LIMIT = "stop_limit"  # 스탑 리밋 (스탑 + 지정가)
    # ── 한국 시장 시간외 거래 (KRX 전용) ──
    AFTER_HOURS_CLOSE = "after_hours_close"   # 장후 시간외 종가 (15:40~16:00, 종가로 매매)
    AFTER_HOURS_SINGLE = "after_hours_single"  # 시간외 단일가 (16:00~18:00, 10분 단일가, ±10%)
    PRE_MARKET_CLOSE = "pre_market_close"     # 장전 시간외 종가 (08:30~08:40, 전일 종가로 매매)


class OrderStatus(Enum):
    """주문 상태"""
    PENDING = "pending"        # 대기 중
    SUBMITTED = "submitted"    # 제출됨
    PARTIAL = "partial"        # 부분 체결
    FILLED = "filled"          # 전량 체결
    CANCELLED = "cancelled"    # 취소됨
    REJECTED = "rejected"      # 거부됨
    EXPIRED = "expired"        # 만료됨


@dataclass
class Order:
    """주문 정보"""
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: int
    price: Optional[float] = None       # 지정가 (LIMIT/STOP_LIMIT)
    stop_price: Optional[float] = None  # 스탑 가격 (STOP/STOP_LIMIT)
    order_id: Optional[str] = None      # 브로커 할당 ID
    status: OrderStatus = OrderStatus.PENDING
    filled_price: Optional[float] = None
    filled_quantity: int = 0
    submitted_at: Optional[datetime] = None
    filled_at: Optional[datetime] = None
    strategy: str = ""                  # 어떤 전략이 주문했는지
    decision_json: str = "{}"           # 매매 결정 상세 JSON (앙상블 점수, 모듈 기여도 등)
    avg_price_hint: float = 0.0         # 매도 시 평균매수가 힌트 (실현PnL 정확 계산용)
                                        # caller가 보유 정보로 채워 전달 (executor가 API 재조회 안 하도록)


@dataclass
class Position:
    """보유 포지션 정보"""
    symbol: str
    quantity: int
    avg_price: float             # 평균 매수 단가
    current_price: float = 0.0   # 현재가
    unrealized_pnl: float = 0.0  # 미실현 손익
    market_value: float = 0.0    # 시장가치
    side: str = "long"           # "long" or "short"

    @property
    def unrealized_pnl_pct(self) -> float:
        """미실현 수익률 (%) — avg_price 대비 현재가 변동 비율"""
        if self.avg_price <= 0:
            return 0.0
        return (self.current_price - self.avg_price) / self.avg_price


@dataclass
class AccountInfo:
    """계좌 정보"""
    total_equity: float = 0.0    # 총 자산
    cash: float = 0.0            # 현금
    buying_power: float = 0.0    # 매수 가능 금액
    positions_value: float = 0.0 # 보유 포지션 합계
    daily_pnl: float = 0.0      # 당일 손익
    currency: str = "KRW"


class BaseExecutor(ABC):
    """
    주문 실행기 추상 베이스 클래스

    모든 브로커 실행기는 이 클래스를 상속받아 구현합니다.
    인터페이스가 동일하므로 브로커를 교체해도 코드 변경이 최소화됩니다.
    """

    def __init__(self, name: str, paper: bool = True):
        """
        Parameters:
            name: 실행기 이름 (로깅용)
            paper: True=모의매매, False=실거래
        """
        self.name = name
        self.paper = paper
        self.orders: List[Order] = []

    @property
    def mode(self) -> str:
        """
        DB 태깅용 모드 문자열 ('paper' 또는 'live')

        모든 거래/포지션/자산 기록이 이 값으로 태그되어 대시보드에서
        현재 봇 모드와 일치하는 데이터만 표시됩니다.

        - PaperExecutor: 항상 'paper'
        - KISExecutor/AlpacaExecutor: self.paper에 따라 결정
        - DualExecutor: KIS 모드 기준 (US도 동일 모드로 운영 가정)
        """
        return "paper" if self.paper else "live"

    def positions_query_succeeded(self) -> bool:
        """
        가장 최근 get_positions() 호출이 성공했는지 반환 (BaseExecutor 기본값: True)

        실거래 브로커(KIS/Alpaca)는 이를 override하여 API 실패 추적.
        Paper executor는 in-memory이므로 항상 True.
        """
        return True

    def account_query_succeeded(self) -> bool:
        """가장 최근 get_account() 호출이 성공했는지 반환 (default: True)"""
        return True

    @abstractmethod
    def connect(self) -> bool:
        """브로커 API 연결"""
        pass

    @abstractmethod
    def submit_order(self, order: Order) -> Order:
        """
        주문 제출

        Parameters:
            order: 주문 정보

        Returns:
            업데이트된 Order (order_id, status 포함)
        """
        pass

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """주문 취소"""
        pass

    @abstractmethod
    def get_order_status(self, order_id: str) -> OrderStatus:
        """주문 상태 조회"""
        pass

    @abstractmethod
    def get_positions(self) -> List[Position]:
        """전체 보유 포지션 조회"""
        pass

    @abstractmethod
    def get_account(self) -> AccountInfo:
        """계좌 정보 조회"""
        pass

    def buy_market(self, symbol: str, quantity: int, strategy: str = "",
                   decision_json: str = "{}") -> Order:
        """시장가 매수 (편의 메서드)"""
        order = Order(
            symbol=symbol,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=quantity,
            strategy=strategy,
            decision_json=decision_json,
        )
        return self.submit_order(order)

    def sell_market(self, symbol: str, quantity: int, strategy: str = "",
                    decision_json: str = "{}", avg_price_hint: float = 0.0) -> Order:
        """
        시장가 매도 (편의 메서드)

        avg_price_hint: 매도 직전 평균매수가 (KIS executor가 실현PnL 정확 계산용 사용).
                        caller가 self.get_positions()로 미리 조회한 값을 전달.
                        executor 내부에서 추가 API 호출을 방지하기 위함
                        (KIS의 get_positions() 호출 시 _last_positions_call_ok 플래그
                         오염 → 후속 매수 신호 silent 차단되는 버그 회피).
        """
        order = Order(
            symbol=symbol,
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=quantity,
            strategy=strategy,
            decision_json=decision_json,
            avg_price_hint=avg_price_hint,
        )
        return self.submit_order(order)

    def buy_limit(self, symbol: str, quantity: int, price: float, strategy: str = "") -> Order:
        """지정가 매수"""
        order = Order(
            symbol=symbol,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=quantity,
            price=price,
            strategy=strategy
        )
        return self.submit_order(order)

    def after_hours_close_order(
        self, symbol: str, quantity: int, side: OrderSide,
        strategy: str = "after_hours_close",
    ) -> Order:
        """
        장후 시간외 종가 주문 (15:40~16:00 KST, 당일 종가로 거래)

        한국 시장 전용. 미국/Alpaca는 unsupported로 처리.

        가격 보장: 당일 종가에 자동 체결 (가격 협상 없음, FIFO)
        장점: 정규장 마감 후에도 청산 가능
        단점: 유동성 낮음 (체결 안 될 수 있음)
        """
        order = Order(
            symbol=symbol,
            side=side,
            order_type=OrderType.AFTER_HOURS_CLOSE,
            quantity=quantity,
            strategy=strategy,
        )
        return self.submit_order(order)

    def after_hours_single_order(
        self, symbol: str, quantity: int, side: OrderSide, price: float,
        strategy: str = "after_hours_single",
    ) -> Order:
        """
        시간외 단일가 주문 (16:00~18:00 KST, 10분마다 단일가 매매)

        한국 시장 전용. 반드시 지정가 + 전일 종가 ±10% 범위.

        장점: 정규장 마감 후 4시간 추가 거래 가능
        단점: ±10% 제한, 10분 단위로만 체결 시도
        """
        order = Order(
            symbol=symbol,
            side=side,
            order_type=OrderType.AFTER_HOURS_SINGLE,
            quantity=quantity,
            price=price,
            strategy=strategy,
        )
        return self.submit_order(order)

    def close_position(self, symbol: str, strategy: str = "close_position",
                       decision_json: str = "{}") -> Optional[Order]:
        """특정 종목 전량 청산 (avg_price를 sell_market에 힌트로 전달)"""
        positions = self.get_positions()
        for pos in positions:
            if pos.symbol == symbol and pos.quantity > 0:
                return self.sell_market(
                    symbol, pos.quantity,
                    strategy=strategy,
                    decision_json=decision_json,
                    avg_price_hint=float(pos.avg_price),
                )
        return None

    def close_partial(
        self, symbol: str, ratio: float, strategy: str = "close_partial",
        decision_json: str = "{}",
    ) -> Optional[Order]:
        """
        보유 포지션의 일부 매도 (비중 기반)

        Parameters:
            symbol: 종목코드
            ratio: 매도 비중 (0.0~1.0, 예: 0.5 = 50%)
            strategy: 거래 사유 태그 (예: "take_profit_1")

        Returns:
            매도 주문 (None이면 보유 없음 또는 수량 0)

        주의: 1주 단위 절삭 (소수점 매도 불가)
              잔여 1주가 ratio*qty < 1 보다 많으면 1주 매도, 아니면 None
        """
        if ratio <= 0 or ratio > 1.0:
            return None
        positions = self.get_positions()
        for pos in positions:
            if pos.symbol == symbol and pos.quantity > 0:
                # 1주 단위 절삭 (반올림이 아닌 floor)
                sell_qty = int(pos.quantity * ratio)
                if sell_qty < 1:
                    # 1주 미만이면 ratio가 0.5 이상일 때만 1주 매도
                    if ratio >= 0.5 and pos.quantity >= 1:
                        sell_qty = 1
                    else:
                        return None
                if sell_qty >= pos.quantity:
                    sell_qty = pos.quantity  # 전량보다 많이 잡히지 않도록
                return self.sell_market(
                    symbol, sell_qty,
                    strategy=strategy,
                    decision_json=decision_json,
                    avg_price_hint=float(pos.avg_price),
                )
        return None
