"""
=============================================================================
executor/alpaca_executor.py - Alpaca 미국 주식 주문 실행기
=============================================================================

Alpaca Markets API를 통해 미국 주식(NYSE/NASDAQ)을 자동 매매합니다.

Alpaca란?
- 미국 주식/ETF 수수료 무료 트레이딩 플랫폼
- REST API + WebSocket 제공 (개발자 친화적)
- 페이퍼 트레이딩(모의매매)을 동일 API로 제공
- 일중 4배, 오버나잇 2배 레버리지 가능
- 2025년 "Best Broker for Algorithmic Trading" 선정

시작 방법:
1. https://alpaca.markets 에서 계정 생성 (���료)
2. 대시보드에서 API Key, Secret Key 발급
3. .env에 ALPACA_API_KEY, ALPACA_SECRET_KEY 저장
4. 먼저 Paper Trading 모드로 테스트!

API 엔드포인트:
- Paper: https://paper-api.alpaca.markets
- Live:  https://api.alpaca.markets
=============================================================================
"""

import os
import logging
from typing import List, Optional
from datetime import datetime
from .base import (
    BaseExecutor, Order, Position, AccountInfo,
    OrderSide, OrderType, OrderStatus
)

logger = logging.getLogger(__name__)

try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import (
        MarketOrderRequest, LimitOrderRequest, StopOrderRequest
    )
    from alpaca.trading.enums import (
        OrderSide as AlpacaSide,
        TimeInForce,
        OrderStatus as AlpacaStatus
    )
    ALPACA_AVAILABLE = True
except ImportError:
    ALPACA_AVAILABLE = False


class AlpacaExecutor(BaseExecutor):
    """
    Alpaca API 기반 미국 주식 주문 실행기

    사용법:
        executor = AlpacaExecutor(paper=True)  # 모의매매
        executor.connect()

        # 시장가 매수
        order = executor.buy_market("AAPL", 10)

        # 포지션 확��
        positions = executor.get_positions()

        # 계좌 정보
        account = executor.get_account()
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        paper: bool = True
    ):
        """
        Parameters:
            api_key: Alpaca API 키 (None이면 환경변수에서 로드)
            secret_key: Alpaca Secret 키
            paper: True=모의매매, False=실거래 (주의!)
        """
        super().__init__(name="alpaca", paper=paper)

        self.api_key = api_key or os.environ.get("ALPACA_API_KEY")
        self.secret_key = secret_key or os.environ.get("ALPACA_SECRET_KEY")
        self.client = None

    def connect(self) -> bool:
        """
        Alpaca API 연결

        Returns:
            연결 성공 여부
        """
        if not ALPACA_AVAILABLE:
            logger.info("[Alpaca] alpaca-py 미설치: pip install alpaca-py")
            return False

        if not self.api_key or not self.secret_key:
            logger.info("[Alpaca] API 키가 설정되지 않았습니다. .env 파일을 확인하세요.")
            return False

        try:
            self.client = TradingClient(
                api_key=self.api_key,
                secret_key=self.secret_key,
                paper=self.paper  # True=모의, False=실거래
            )
            # 연결 테스트 (계좌 조회)
            account = self.client.get_account()
            mode = "PAPER" if self.paper else "LIVE"
            logger.info(f"[Alpaca] {mode} 모드 연결 성공")
            logger.info(f"         자산: ${float(account.equity):,.2f}")
            return True
        except Exception as e:
            logger.error(f"[Alpaca] 연결 실패: {e}")
            return False

    def submit_order(self, order: Order) -> Order:
        """
        Alpaca에 주문 제출

        Parameters:
            order: 주문 정보

        Returns:
            체결 정보가 업데이트된 Order
        """
        if not self.client:
            order.status = OrderStatus.REJECTED
            return order

        try:
            # 주문 방향 변환
            side = AlpacaSide.BUY if order.side == OrderSide.BUY else AlpacaSide.SELL

            # 주문 유형별 요청 생성
            if order.order_type == OrderType.MARKET:
                request = MarketOrderRequest(
                    symbol=order.symbol,
                    qty=order.quantity,
                    side=side,
                    time_in_force=TimeInForce.DAY
                )
            elif order.order_type == OrderType.LIMIT:
                request = LimitOrderRequest(
                    symbol=order.symbol,
                    qty=order.quantity,
                    side=side,
                    limit_price=order.price,
                    time_in_force=TimeInForce.DAY
                )
            elif order.order_type == OrderType.STOP:
                request = StopOrderRequest(
                    symbol=order.symbol,
                    qty=order.quantity,
                    side=side,
                    stop_price=order.stop_price,
                    time_in_force=TimeInForce.DAY
                )
            else:
                order.status = OrderStatus.REJECTED
                return order

            # 주문 제출
            alpaca_order = self.client.submit_order(request)

            # 결과 매핑
            order.order_id = str(alpaca_order.id)
            order.submitted_at = datetime.now()
            order.status = self._map_status(alpaca_order.status)

            if alpaca_order.filled_avg_price:
                order.filled_price = float(alpaca_order.filled_avg_price)
            if alpaca_order.filled_qty:
                order.filled_quantity = int(alpaca_order.filled_qty)

            self.orders.append(order)
            return order

        except Exception as e:
            logger.error(f"[Alpaca] 주문 실패: {e}")
            order.status = OrderStatus.REJECTED
            return order

    def cancel_order(self, order_id: str) -> bool:
        """주문 취소"""
        if not self.client:
            return False
        try:
            self.client.cancel_order_by_id(order_id)
            return True
        except Exception as e:
            logger.error(f"[Alpaca] 취�� 실패: {e}")
            return False

    def get_order_status(self, order_id: str) -> OrderStatus:
        """주��� 상태 조��"""
        if not self.client:
            return OrderStatus.REJECTED
        try:
            alpaca_order = self.client.get_order_by_id(order_id)
            return self._map_status(alpaca_order.status)
        except Exception:
            return OrderStatus.REJECTED

    def get_positions(self) -> List[Position]:
        """전체 보유 포지션 조��"""
        if not self.client:
            return []

        try:
            alpaca_positions = self.client.get_all_positions()
            positions = []

            for p in alpaca_positions:
                positions.append(Position(
                    symbol=p.symbol,
                    quantity=int(p.qty),
                    avg_price=float(p.avg_entry_price),
                    current_price=float(p.current_price),
                    unrealized_pnl=float(p.unrealized_pl),
                    market_value=float(p.market_value),
                    side="long" if p.side == "long" else "short",
                ))

            return positions
        except Exception as e:
            logger.error(f"[Alpaca] 포지션 조회 ���패: {e}")
            return []

    def get_account(self) -> AccountInfo:
        """계좌 정보 조회"""
        if not self.client:
            return AccountInfo()

        try:
            account = self.client.get_account()
            return AccountInfo(
                total_equity=float(account.equity),
                cash=float(account.cash),
                buying_power=float(account.buying_power),
                positions_value=float(account.long_market_value),
                daily_pnl=float(account.equity) - float(account.last_equity),
                currency="USD",
            )
        except Exception as e:
            logger.error(f"[Alpaca] ��좌 조회 실패: {e}")
            return AccountInfo()

    def _map_status(self, alpaca_status) -> OrderStatus:
        """Alpaca 상태 → 내부 상태 변환"""
        status_map = {
            "new": OrderStatus.SUBMITTED,
            "partially_filled": OrderStatus.PARTIAL,
            "filled": OrderStatus.FILLED,
            "done_for_day": OrderStatus.FILLED,
            "canceled": OrderStatus.CANCELLED,
            "expired": OrderStatus.EXPIRED,
            "rejected": OrderStatus.REJECTED,
            "pending_new": OrderStatus.PENDING,
            "accepted": OrderStatus.SUBMITTED,
        }
        return status_map.get(str(alpaca_status).lower(), OrderStatus.PENDING)
