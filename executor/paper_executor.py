"""
=============================================================================
executor/paper_executor.py - 모의매매 실행기
=============================================================================

실제 돈을 사용하지 않고 매매를 시뮬레이션합니다.
실거래 전에 반드시 30일 이상 모의매매로 검증해야 합니다.

모의매매의 목적:
1. 전략 로직 검증 (버그 없는지)
2. 주문 실행 흐름 테스트
3. 리스크 관리 규칙 동작 확인
4. 심리적 준비 (실전과 같은 환경 경험)

실거래와의 차이:
- 슬리피지 없음 (항상 원하는 가격에 체결됨)
- 유동성 제약 없음 (얼마든 살 수 있음)
- 시장 충격 없음 (큰 주문이 가격에 영향 안 줌)
→ 모의매매 성과 > 실거래 성과 (항상 이 점을 기억할 것)

수수료 시뮬레이션 (v2.0):
- 한국: 매수 0.015% + 매도 0.015% + 거래세 0.18%
- 미국: 무수수료 (Alpaca 기준) + SEC Fee 0.00278% + 환전 스프레드 0.25%
→ 수수료를 차감해야 현실적인 수익률이 나옴
=============================================================================
"""

import uuid
import threading
import logging
from typing import List, Optional, Dict
from datetime import datetime
from dataclasses import dataclass, field
from .base import (
    BaseExecutor, Order, Position, AccountInfo,
    OrderSide, OrderType, OrderStatus
)


@dataclass
class CommissionModel:
    """
    수수료 모델 — 실제 거래 비용을 시뮬레이션

    한국 주식 (KIS 온라인 기준):
    ─────────────────────────────
    매수: 증권사 수수료 0.015%
    매도: 증권사 수수료 0.015% + 증권거래세 0.18%
    → 왕복 약 0.21%

    미국 주식 (Alpaca 기준):
    ─────────────────────────────
    매수: 무수수료
    매도: SEC Fee ~0.00278%
    환전: 원화↔달러 환전 스프레드 0.25% (매수/매도 각각)
    → 왕복 약 0.50% (환전 포함)

    사용법:
        model = CommissionModel()  # 기본값 사용
        fee = model.calculate(symbol="005930.KS", side="BUY", amount=1_000_000)
        # → 150원 (0.015%)
    """
    # ── 한국 시장 ──
    kr_buy_commission: float = 0.00015     # 매수 수수료 0.015%
    kr_sell_commission: float = 0.00015    # 매도 수수료 0.015%
    kr_sell_tax: float = 0.0018            # 증권거래세 0.18%

    # ── 미국 시장 ──
    us_buy_commission: float = 0.0         # Alpaca 무수수료
    us_sell_commission: float = 0.0        # Alpaca 무수수료
    us_sec_fee: float = 0.0000278          # SEC Fee (매도만) ~0.00278%
    us_fx_spread: float = 0.0025           # 환전 스프레드 0.25% (매수/매도 각각)

    # ── 슬리피지 (시장가 주문 시 가격 불리) ──
    slippage_pct: float = 0.0005           # 0.05% 슬리피지 시뮬레이션

    # ── 누적 통계 ──
    total_fees_paid: float = field(default=0.0, repr=False)
    total_fees_kr: float = field(default=0.0, repr=False)
    total_fees_us: float = field(default=0.0, repr=False)
    trade_count_with_fees: int = field(default=0, repr=False)

    def calculate(self, symbol: str, side: str, amount_krw: float,
                  is_us: bool = False) -> float:
        """
        거래 수수료 계산 (KRW 기준)

        Parameters:
            symbol: 종목 코드
            side: "BUY" 또는 "SELL"
            amount_krw: 거래 금액 (KRW 환산)
            is_us: 미국 주식 여부

        Returns:
            수수료 금액 (KRW)
        """
        fee = 0.0

        if is_us:
            # 미국 주식
            if side.upper() == "BUY":
                fee += amount_krw * self.us_buy_commission
                fee += amount_krw * self.us_fx_spread    # 환전 스프레드
            else:
                fee += amount_krw * self.us_sell_commission
                fee += amount_krw * self.us_sec_fee      # SEC Fee
                fee += amount_krw * self.us_fx_spread    # 환전 스프레드
            self.total_fees_us += fee
        else:
            # 한국 주식
            if side.upper() == "BUY":
                fee += amount_krw * self.kr_buy_commission
            else:
                fee += amount_krw * self.kr_sell_commission
                fee += amount_krw * self.kr_sell_tax     # 거래세
            self.total_fees_kr += fee

        self.total_fees_paid += fee
        self.trade_count_with_fees += 1
        return fee

    def calculate_slippage(self, price: float, side: str) -> float:
        """
        슬리피지 적용 가격 반환

        매수: 약간 더 비싸게 체결 (불리)
        매도: 약간 더 싸게 체결 (불리)
        """
        if self.slippage_pct <= 0:
            return price
        if side.upper() == "BUY":
            return price * (1 + self.slippage_pct)
        else:
            return price * (1 - self.slippage_pct)

    def get_stats(self) -> dict:
        """수수료 통계 (대시보드용)"""
        return {
            "total_fees_paid": round(self.total_fees_paid, 2),
            "total_fees_kr": round(self.total_fees_kr, 2),
            "total_fees_us": round(self.total_fees_us, 2),
            "trade_count": self.trade_count_with_fees,
            "avg_fee_per_trade": round(
                self.total_fees_paid / max(self.trade_count_with_fees, 1), 2
            ),
        }

    def get_rate_summary(self) -> dict:
        """현재 적용 중인 수수료율 요약"""
        return {
            "kr_buy": f"{self.kr_buy_commission * 100:.3f}%",
            "kr_sell": f"{(self.kr_sell_commission + self.kr_sell_tax) * 100:.3f}%",
            "kr_roundtrip": f"{(self.kr_buy_commission + self.kr_sell_commission + self.kr_sell_tax) * 100:.3f}%",
            "us_buy": f"{(self.us_buy_commission + self.us_fx_spread) * 100:.3f}%",
            "us_sell": f"{(self.us_sell_commission + self.us_sec_fee + self.us_fx_spread) * 100:.3f}%",
            "us_roundtrip": f"{(self.us_buy_commission + self.us_sell_commission + self.us_sec_fee + self.us_fx_spread * 2) * 100:.3f}%",
            "slippage": f"{self.slippage_pct * 100:.3f}%",
        }

logger = logging.getLogger(__name__)

# DatabaseManager를 import하되, 없어도 동작하도록 처리
try:
    from database.cache import DatabaseManager
except ImportError:
    DatabaseManager = None  # type: ignore

# 시장 판별 + 환율 변환 유틸리티
try:
    from utils.market import detect_market, is_us_stock, to_krw, get_exchange_rate
except ImportError:
    # utils 모듈을 못 찾으면 인라인 폴백
    def detect_market(symbol: str) -> str:  # type: ignore
        return "KR" if symbol.endswith((".KS", ".KQ")) else "US"
    def is_us_stock(symbol: str) -> bool:  # type: ignore
        return detect_market(symbol) == "US"
    def to_krw(symbol: str, amount: float) -> float:  # type: ignore
        return amount * 1350.0 if is_us_stock(symbol) else amount
    def get_exchange_rate() -> float:  # type: ignore
        return 1350.0


class PaperExecutor(BaseExecutor):
    """
    모의매매 실행기

    메모리에 포지션과 거래 기록을 유지합니다.
    실거래와 동일한 인터페이스를 제공하여,
    나중에 실거래로 전환할 때 코드 변경 없이 교체 가능합니다.

    사용법:
        executor = PaperExecutor(initial_capital=10_000_000)
        executor.connect()

        # 매수
        order = executor.buy_market("AAPL", 10)
        logger.info(f"체결: {order.filled_price}")

        # 포지션 확인
        positions = executor.get_positions()

        # 매도
        executor.sell_market("AAPL", 10)
    """

    def __init__(
        self,
        initial_capital: float = 10_000_000,
        currency: str = "KRW",
        db: Optional["DatabaseManager"] = None,
        commission: Optional[CommissionModel] = None,
    ):
        """
        Parameters:
            initial_capital: 초기 가상 자본금
            currency: 통화 단위
            db: DatabaseManager 인스턴스 (선택적)
                - None이면 메모리만 사용 (기존 동작)
                - 있으면 거래/포지션을 DB에 자동 저장
            commission: 수수료 모델 (선택적)
                - None이면 기본 수수료 모델 사용
                  (한국 0.015%+거래세, 미국 무수수료+환전)
        """
        super().__init__(name="paper", paper=True)
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.currency = currency
        self.positions: Dict[str, Position] = {}
        self.trade_history: List[Dict] = []
        self.connected = False
        self.db = db
        self.commission = commission or CommissionModel()

        # ── 스레드 안전성 ──
        # 대시보드(Flask) 스레드와 봇 분석 스레드가 동시에
        # positions를 읽고/쓸 수 있으므로 RLock으로 보호
        # RLock: 같은 스레드에서 중첩 lock 허용 (get_account→get_positions 등)
        self._lock = threading.RLock()

        # 현재가 시뮬레이션용 (실제로는 실시간 데이터 필요)
        self._current_prices: Dict[str, float] = {}

    def connect(self) -> bool:
        """
        연결 (모의매매는 항상 성공)

        DB가 있으면 기존 포지션과 거래 이력 복원:
        - 봇 재시작 시 이전 상태 자동 복원
        - DB가 없으면 기존처럼 메모리에서 시작
        """
        self.connected = True

        # DB에서 포지션 복원
        if self.db:
            self._restore_from_db()

        rates = self.commission.get_rate_summary()
        logger.info(
            f"[Paper] 모의매매 연결 완료 "
            f"(자본금: {self.initial_capital:,.0f} {self.currency}, "
            f"수수료: KR 왕복 {rates['kr_roundtrip']}, "
            f"US 왕복 {rates['us_roundtrip']}, "
            f"슬리피지: {rates['slippage']})"
        )
        if self.db and self.positions:
            logger.info(f"[Paper] DB에서 {len(self.positions)}개 포지션 복원 완료")

        return True

    def set_current_price(self, symbol: str, price: float):
        """
        종목 현재가 설정 (외부에서 업데이트)

        실제 운용 시에는 데이터 피드에서 자동 업데이트됩니다.
        ★ numpy.int64/float64 → Python native float 변환 필수
           (JSON 직렬화 시 'Object of type int64 is not JSON serializable' 방지)
        """
        with self._lock:
            self._current_prices[symbol] = float(price)

    def submit_order(self, order: Order) -> Order:
        """
        주문 제출 및 즉시 체결 시뮬레이션 (스레드 안전)

        모의매매에서는:
        - 시장가: 현재가로 즉시 체결
        - 지정가: 현재가가 지정가 이하/이상이면 즉시 체결
        """
        # ★ 스레드 안전: 포지션/잔고 변경은 lock 내에서 실행
        with self._lock:
            return self._execute_order(order)

    def _execute_order(self, order: Order) -> Order:
        """실제 주문 실행 로직 (lock 내부에서 호출됨)"""
        # 주문 ID 생성
        order.order_id = f"PAPER-{uuid.uuid4().hex[:8]}"
        order.submitted_at = datetime.now()

        # 현재가 확인
        current_price = self._current_prices.get(order.symbol)
        if current_price is None:
            order.status = OrderStatus.REJECTED
            return order

        # 체결 가격 결정
        if order.order_type == OrderType.MARKET:
            fill_price = current_price
        elif order.order_type == OrderType.LIMIT:
            if order.side == OrderSide.BUY and current_price <= order.price:
                fill_price = order.price
            elif order.side == OrderSide.SELL and current_price >= order.price:
                fill_price = order.price
            else:
                order.status = OrderStatus.SUBMITTED  # 대기
                self.orders.append(order)
                return order
        else:
            fill_price = current_price

        # ── 슬리피지 적용 ──
        # 시장가 주문 시 실제로는 원하는 가격보다 약간 불리하게 체결됨
        # 매수: 조금 더 비싸게, 매도: 조금 더 싸게
        fill_price = self.commission.calculate_slippage(
            fill_price, order.side.value)

        # ── 체결 처리 ──
        # ★ 환율 변환: 미국 주식(USD)은 KRW로 환산하여 현금 차감/가산
        #   예: AAPL 10주 × $290 × 1,370원 = 3,973,000원 차감
        #   한국 주식(KRW)은 그대로: 삼성전자 10주 × 72,000원 = 720,000원
        total_cost_native = fill_price * order.quantity  # 원래 통화 기준
        total_cost_krw = to_krw(order.symbol, total_cost_native)  # KRW 환산
        sell_avg_price = 0.0  # 매도 시 평균매수가 (아래에서 설정됨)

        # ── 수수료 계산 ──
        us_stock = is_us_stock(order.symbol)
        fee_krw = self.commission.calculate(
            symbol=order.symbol,
            side=order.side.value,
            amount_krw=total_cost_krw,
            is_us=us_stock,
        )

        if order.side == OrderSide.BUY:
            # 매수: 현금(KRW) 충분한지 확인 (거래금액 + 수수료)
            total_with_fee = total_cost_krw + fee_krw
            if total_with_fee > self.cash:
                logger.warning(
                    f"[Paper] 잔고 부족: {order.symbol} "
                    f"필요 {total_with_fee:,.0f}원 "
                    f"(거래 {total_cost_krw:,.0f} + 수수료 {fee_krw:,.0f}) "
                    f"> 잔고 {self.cash:,.0f}원"
                )
                order.status = OrderStatus.REJECTED
                return order

            self.cash -= total_with_fee
            self._add_position(order.symbol, order.quantity, fill_price)
            if us_stock:
                rate = get_exchange_rate()
                logger.info(
                    f"[Paper] USD→KRW 변환: ${total_cost_native:,.2f} × "
                    f"{rate:,.1f} = {total_cost_krw:,.0f}원 "
                    f"(수수료: {fee_krw:,.0f}원)"
                )
            else:
                logger.info(
                    f"[Paper] 매수 체결: {order.symbol} "
                    f"{total_cost_krw:,.0f}원 "
                    f"(수수료: {fee_krw:,.0f}원)"
                )

        elif order.side == OrderSide.SELL:
            # 매도: 보유 수량 충분한지 확인
            pos = self.positions.get(order.symbol)
            if not pos or pos.quantity < order.quantity:
                order.status = OrderStatus.REJECTED
                return order

            # ★ 평균매수가를 _reduce_position 호출 전에 저장
            # _reduce_position이 포지션을 삭제할 수 있으므로 미리 캡처
            sell_avg_price = pos.avg_price

            # 매도: 거래 대금에서 수수료+세금 차감
            self.cash += (total_cost_krw - fee_krw)
            self._reduce_position(order.symbol, order.quantity, fill_price)
            logger.info(
                f"[Paper] 매도 체결: {order.symbol} "
                f"{total_cost_krw:,.0f}원 - 수수료 {fee_krw:,.0f}원 "
                f"= 순입금 {total_cost_krw - fee_krw:,.0f}원"
            )

        # 체결 완료
        order.status = OrderStatus.FILLED
        order.filled_price = fill_price
        order.filled_quantity = order.quantity
        order.filled_at = datetime.now()

        # 실현 손익 계산 (매도 시에만, KRW 기준)
        # ★ 중요: _reduce_position 호출 전에 avg_price를 저장해야 하므로
        #   위 매도 분기에서 미리 저장한 sell_avg_price를 사용
        # ★ 수수료 반영: 매수 시 수수료도 원가에 포함해야 정확한 PnL
        #   - 매수 수수료는 이미 cash에서 차감됨 (별도 추적 불필요)
        #   - 매도 수수료는 매도 대금에서 차감됨
        #   - realized_pnl은 순수 가격 차이로 계산 (수수료는 cash에서 이미 반영)
        realized_pnl = 0.0
        if order.side == OrderSide.SELL:
            pnl_native = (fill_price - sell_avg_price) * order.quantity
            realized_pnl = to_krw(order.symbol, pnl_native)

        # 거래 기록 (메모리)
        trade_record = {
            "order_id": order.order_id,
            "symbol": order.symbol,
            "side": order.side.value,
            "quantity": order.quantity,
            "price": fill_price,
            "total": total_cost_krw,  # KRW 환산 금액
            "fee": fee_krw,           # 수수료 (KRW)
            "strategy": order.strategy,
            "timestamp": order.filled_at,
            "realized_pnl": realized_pnl,
        }
        self.trade_history.append(trade_record)

        # DB 연동
        if self.db:
            # 시장 판별 (utils.market 유틸 사용)
            market = detect_market(order.symbol)

            # 거래 DB 저장 (매도 시 실현손익 포함)
            # ★ total_value에 KRW 환산 금액 전달
            # 미국 주식: fill_price(USD) × quantity → to_krw → KRW
            # 한국 주식: fill_price(KRW) × quantity → 그대로 KRW
            # ★ decision_json: Order에 첨부된 매매 결정 상세 (있으면)
            decision_json = getattr(order, "decision_json", None) or "{}"
            self.db.log_trade(
                symbol=order.symbol,
                side=order.side.value.upper(),
                quantity=order.quantity,
                price=fill_price,
                strategy=order.strategy or "",
                market=market,
                order_id=order.order_id,
                pnl=realized_pnl,
                total_value=total_cost_krw,
                decision_json=decision_json,
                mode=self.mode,  # ★ Phase 5: paper/live 태그
            )

            # 포지션 DB 동기화
            self._sync_positions_to_db()

        self.orders.append(order)
        return order

    def _add_position(self, symbol: str, quantity: int, price: float):
        """포지션 추가 (평균 단가 계산)"""
        # ★ numpy int64/float64 → Python native 변환
        quantity = int(quantity)
        price = float(price)

        if symbol in self.positions:
            pos = self.positions[symbol]
            # 평균 단가 재계산
            total_qty = int(pos.quantity + quantity)
            pos.avg_price = float(
                (pos.avg_price * pos.quantity + price * quantity) / total_qty
            )
            pos.quantity = total_qty
        else:
            self.positions[symbol] = Position(
                symbol=symbol,
                quantity=quantity,
                avg_price=price,
                current_price=price,
                market_value=float(price * quantity),
            )

    def _reduce_position(self, symbol: str, quantity: int, price: float):
        """포지션 축소"""
        if symbol in self.positions:
            pos = self.positions[symbol]
            pos.quantity -= quantity
            if pos.quantity <= 0:
                del self.positions[symbol]

    def cancel_order(self, order_id: str) -> bool:
        """주문 취소"""
        for order in self.orders:
            if order.order_id == order_id and order.status == OrderStatus.SUBMITTED:
                order.status = OrderStatus.CANCELLED
                return True
        return False

    def get_order_status(self, order_id: str) -> OrderStatus:
        """주문 상태 조회"""
        for order in self.orders:
            if order.order_id == order_id:
                return order.status
        return OrderStatus.REJECTED

    def get_positions(self) -> List[Position]:
        """전체 보유 포지션 (스레드 안전)"""
        with self._lock:
            # 현재가 업데이트 + KRW 환산
            for symbol, pos in self.positions.items():
                if symbol in self._current_prices:
                    pos.current_price = float(self._current_prices[symbol])
                    # ★ market_value, unrealized_pnl은 KRW로 환산
                    # (대시보드에서 합산할 때 통화 혼합 방지)
                    native_value = pos.current_price * pos.quantity
                    native_pnl = (pos.current_price - pos.avg_price) * pos.quantity
                    pos.market_value = float(to_krw(symbol, native_value))
                    pos.unrealized_pnl = float(to_krw(symbol, native_pnl))
                # ★ numpy 타입 → Python native 변환
                # (JSON 직렬화 실패 방지: int64, float64 등)
                pos.avg_price = float(pos.avg_price)
                pos.quantity = int(pos.quantity)
                # unrealized_pnl_pct는 @property(계산값)이므로 직접 할당 불가
                # → avg_price, current_price가 float이면 자동으로 float 반환됨

            return list(self.positions.values())

    def get_account(self) -> AccountInfo:
        """계좌 정보 (모든 금액은 KRW 기준)"""
        # ★ 환율 변환: 미국 주식은 USD 가격 × 환율 → KRW로 합산
        positions_value = float(sum(
            to_krw(
                pos.symbol,
                pos.quantity * self._current_prices.get(pos.symbol, pos.avg_price)
            )
            for pos in self.positions.values()
        ))

        total_equity = float(self.cash + positions_value)

        return AccountInfo(
            total_equity=total_equity,
            cash=float(self.cash),
            buying_power=float(self.cash),
            positions_value=positions_value,
            daily_pnl=float(total_equity - self.initial_capital),
            currency=self.currency,
        )

    def get_summary(self) -> str:
        """계좌 요약"""
        account = self.get_account()
        pnl_pct = (account.total_equity / self.initial_capital - 1) * 100
        fee_stats = self.commission.get_stats()

        lines = [
            "=== 모의매매 계좌 요약 ===",
            f"총 자산: {account.total_equity:,.0f} {self.currency}",
            f"현금: {account.cash:,.0f}",
            f"포지션: {account.positions_value:,.0f}",
            f"손익: {account.daily_pnl:+,.0f} ({pnl_pct:+.2f}%)",
            f"거래 횟수: {len(self.trade_history)}",
            f"보유 종목: {len(self.positions)}개",
            f"── 수수료 ──",
            f"총 수수료: {fee_stats['total_fees_paid']:,.0f}원",
            f"  한국: {fee_stats['total_fees_kr']:,.0f}원",
            f"  미국: {fee_stats['total_fees_us']:,.0f}원",
            f"  평균: {fee_stats['avg_fee_per_trade']:,.0f}원/거래",
        ]
        return "\n".join(lines)

    def get_commission_stats(self) -> dict:
        """수수료 통계 + 수수료율 (대시보드용)"""
        return {
            **self.commission.get_stats(),
            "rates": self.commission.get_rate_summary(),
        }

    # ─── DB 연동 메서드 ────────────────────────────────────────────────

    def _sync_positions_to_db(self):
        """
        현재 메모리 포지션을 DB에 동기화 (스레드 안전)

        매매 후 포지션이 변할 때마다 호출:
        - 메모리: Position 객체로 관리
        - DB: 실시간 추적을 위해 동기화

        ★ 핵심: 기존 DB의 메타데이터(포지션유형, 매매이유, 목표가 등)를
        보존합니다. save_positions()는 DELETE→INSERT를 하므로, 먼저
        기존 메타데이터를 읽어서 병합한 뒤 저장합니다.

        DB가 없으면 무시합니다.
        """
        if not self.db:
            return

        # 1. 기존 DB 메타데이터 읽기 (DELETE 전에!)
        # ★ mode 필터 — 자기 모드의 메타데이터만 (paper/live 메타 섞임 방지)
        existing_meta = {}
        try:
            db_positions = self.db.load_positions(mode=self.mode)
            for db_pos in db_positions:
                sym = db_pos.get("symbol", "")
                if sym:
                    existing_meta[sym] = {
                        "position_type": db_pos.get("position_type", ""),
                        "position_type_en": db_pos.get("position_type_en", ""),
                        "target_price": db_pos.get("target_price", 0),
                        "stop_price": db_pos.get("stop_price", 0),
                        "reasons_json": db_pos.get("reasons_json", "[]"),
                        "holding_period": db_pos.get("holding_period", ""),
                        "bought_at": db_pos.get("bought_at", ""),
                    }
        except Exception as e:
            logger.debug(f"[Paper DB] 기존 메타데이터 로드 실패 (무시): {e}")

        # 2. 메모리 포지션 + 기존 메타데이터 병합
        with self._lock:
            # ★ int()/float() 변환: numpy 타입이 SQLite에 bytes로 저장되는 것 방지
            positions_data = []
            for symbol, pos in self.positions.items():
                meta = existing_meta.get(symbol, {})
                positions_data.append({
                    "symbol": symbol,
                    "quantity": int(pos.quantity),
                    "avg_price": float(pos.avg_price),
                    "current_price": float(pos.current_price),
                    # 기존 메타데이터 보존 (있으면 유지, 없으면 기본값)
                    "position_type": meta.get("position_type", ""),
                    "position_type_en": meta.get("position_type_en", ""),
                    "target_price": meta.get("target_price", 0),
                    "stop_price": meta.get("stop_price", 0),
                    "reasons_json": meta.get("reasons_json", "[]"),
                    "holding_period": meta.get("holding_period", ""),
                    "bought_at": meta.get("bought_at", ""),
                })

        # 3. DB에 저장 (lock 해제 후 — DB I/O 동안 lock 안 잡음)
        # ★ Phase 5: 모드 태그 — 다른 모드 포지션은 건드리지 않음
        self.db.save_positions(positions_data, mode=self.mode)

    def _restore_from_db(self):
        """
        DB에서 포지션과 거래 이력 복원 (봇 재시작 대응)

        연결(connect) 시 자동 호출되어:
        1. 이전 거래 이력 메모리 로드
        2. 현재 포지션 메모리 복원

        이를 통해 봇을 재시작해도 상태가 사라지지 않습니다.
        """
        if not self.db:
            return

        try:
            # ★ Phase 5: 현재 모드 거래/포지션만 복원 (다른 모드와 격리)
            db_trades = self.db.get_trades(limit=1000, mode=self.mode)
            for trade in db_trades:
                self.trade_history.append({
                    "order_id": trade.get("order_id", ""),
                    "symbol": trade["symbol"],
                    "side": trade["side"].lower(),
                    "quantity": trade["quantity"],
                    "price": trade["price"],
                    "total": trade["total_value"],
                    "strategy": trade.get("strategy", ""),
                    "timestamp": datetime.fromisoformat(trade["timestamp"]),
                    # ★ DB pnl 컬럼에서 실현손익 복원
                    # (log_trade 시 pnl=realized_pnl로 DB에 저장됨)
                    "realized_pnl": trade.get("pnl", 0.0),
                })

            # 기존 포지션 로드 (★ 현재 모드만)
            db_positions = self.db.load_positions(mode=self.mode)
            for pos_data in db_positions:
                # ★ DB에서 numpy.int64가 bytes로 저장된 경우 안전 변환
                # Python 3.14 + numpy 조합에서 SQLite에 int64를 넣으면
                # bytes(8바이트 little-endian)로 직렬화되는 버그가 있음
                qty = pos_data["quantity"]
                avg = pos_data["avg_price"]
                cp = pos_data.get("current_price", 0)

                # bytes → 숫자 복원 (int64 또는 float64)
                if isinstance(qty, bytes):
                    import struct
                    qty = struct.unpack('<q', qty)[0]
                if isinstance(avg, bytes):
                    import struct
                    avg = struct.unpack('<d', avg)[0]
                if isinstance(cp, bytes):
                    import struct
                    try:
                        cp = struct.unpack('<q', cp)[0]  # int64
                    except Exception:
                        cp = struct.unpack('<d', cp)[0]  # float64

                pos = Position(
                    symbol=pos_data["symbol"],
                    quantity=int(qty),
                    avg_price=float(avg),
                    current_price=float(cp),
                    market_value=float(int(qty) * float(avg)),
                )
                self.positions[pos_data["symbol"]] = pos

            # ── ★ 핵심: 현금(cash) 복원 ──
            # ★ 버그 수정 (2026-05-08):
            # 이전 로직은 equity_history의 latest cash를 신뢰했으나,
            # 봇이 거래 직후 크래시하면 스냅샷에 거래가 반영되지 않은 상태로
            # 남아 재시작 시 cash가 오래된 값으로 복원되어 total_equity가
            # 부풀려지는 버그가 있었음.
            #
            # 새 복원 로직: 거래 이력을 직접 재계산
            # cash = initial_capital
            #        - sum(BUY 거래 KRW 환산금액)
            #        + sum(SELL 거래 KRW 환산금액)
            # → 항상 거래 이력과 일관된 cash 값 보장
            #
            # 주의: 수수료가 trades 테이블에 별도로 저장되지 않으므로
            #       수수료 만큼 cash가 약간 과대평가됨 (보통 0.015% × 거래액)
            #       → 매도 시 정산되거나, 다음 equity_snapshot 때 자연 동기화
            if self.positions or db_trades:
                computed_cash = float(self.initial_capital)
                fee_estimate = 0.0
                for trade in db_trades:
                    sym = trade["symbol"]
                    qty = int(trade["quantity"])
                    price = float(trade["price"])
                    side = trade.get("side", "").upper()
                    # ★ DB의 total_value가 신/구 형식 모두 처리:
                    # 신규 (2026-05 이후): KRW 환산 금액 그대로 사용
                    # 구버전: native currency → to_krw로 변환
                    # 안전을 위해 항상 to_krw로 재계산 (멱등 연산)
                    krw_total = to_krw(sym, qty * price)
                    # 수수료 근사: 0.015% (KIS 기준) — 정확도는 떨어지나 0보다 나음
                    fee_approx = krw_total * 0.00015
                    fee_estimate += fee_approx
                    if side == "BUY":
                        computed_cash -= (krw_total + fee_approx)
                    elif side == "SELL":
                        computed_cash += (krw_total - fee_approx)

                # 음수 방지 (수수료 누적 오차로 0 미만 가능)
                self.cash = max(0.0, computed_cash)

                # equity_history와 비교 로그 (디버깅용)
                latest = self.db.get_latest_equity()
                if latest and "cash" in latest:
                    snapshot_cash = float(latest["cash"])
                    diff = abs(snapshot_cash - self.cash)
                    if diff > 100000:  # 10만원 이상 차이나면 경고
                        logger.warning(
                            f"[Paper DB] 현금 복원 불일치: "
                            f"거래기록 기반={self.cash:,.0f}, "
                            f"equity_snapshot={snapshot_cash:,.0f}, "
                            f"차이={diff:,.0f} → 거래기록 기반 사용"
                        )
                logger.info(
                    f"[Paper DB] 현금 복원: {self.cash:,.0f}원 "
                    f"(자본 {self.initial_capital:,.0f} "
                    f"-수수료추정 {fee_estimate:,.0f} "
                    f"+거래내역 재계산)"
                )

            logger.info(f"[Paper DB] {len(self.trade_history)}건 거래, {len(self.positions)}개 포지션 복원")

        except Exception as e:
            logger.error(f"[Paper DB] 복원 중 오류: {e}")
            # 오류 발생 시에도 계속 진행 (DB 없이 시작)

    def save_equity_snapshot(self):
        """
        현재 자산 상태를 DB에 스냅샷으로 저장 (외부에서 주기적 호출)

        용도:
        - 일중 포트폴리오 변화 추적 (equity_history 테이블)
        - 차트 그리기 (시간별 자산가)
        - 최대 낙폭(MDD) 계산
        - 리포트 생성

        DB가 없으면 무시합니다.

        예시:
            # 매 분 호출 (또는 주기적으로)
            executor.save_equity_snapshot()

            # 나중에 차트 작성
            history = db.get_equity_history(days=7)
        """
        if not self.db:
            return

        account = self.get_account()

        self.db.save_equity_snapshot(
            total_equity=account.total_equity,
            cash=account.cash,
            positions_value=account.positions_value,
            daily_pnl=account.daily_pnl,
            cumulative_return=(account.total_equity / self.initial_capital - 1),
            mode=self.mode,  # ★ Phase 5: paper/live 분리
        )
