"""
=============================================================================
executor/order_splitter.py - VWAP/TWAP 주문 분할기
=============================================================================

대량 주문을 작은 조각(Slice)으로 나누어 시장 충격을 최소화합니다.

왜 주문을 분할하는가?
━━━━━━━━━━━━━━━━━━━
큰 주문을 한 번에 넣으면:
1. 시장 충격(Market Impact): 내 주문이 가격을 움직여 불리한 가격에 체결
2. 정보 누출: 다른 참가자가 대량 주문을 감지하고 선행매매(Front-running)
3. 불리한 평균 단가: 유동성 부족 시 높은 가격에 체결

주문 분할 전략:
━━━━━━━━━━━━━━

1. TWAP (Time-Weighted Average Price)
   시간 균등 분할 — 정해진 시간 동안 균등하게 나누어 주문
   
   예: 1000주를 1시간 동안 실행
   → 6분마다 ~100주씩 10번 주문
   
   장점: 단순하고 예측 가능, 시장 조건 무관하게 실행
   단점: 거래량이 적은 시간대에도 동일하게 주문 → 비효율

2. VWAP (Volume-Weighted Average Price)
   거래량 가중 분할 — 거래량이 많은 시간에 더 많이 주문
   
   예: 1000주를 거래량 비율에 따라 분배
   → 개장 직후(거래량 많음): 200주
   → 점심시간(거래량 적음): 50주
   → 장 마감 전(거래량 많음): 200주
   
   장점: 시장 VWAP에 근접한 평균 단가 달성
   단점: 과거 거래량 패턴에 의존, 이상 시장에서는 부정확

3. POV (Percentage of Volume)
   실시간 거래량의 일정 비율만큼 주문
   
   예: 시장 거래량의 10%만 차지하도록 주문
   → 거래량 1000주/분이면 → 100주/분 주문
   
   장점: 시장 충격 최소화 (항상 시장의 작은 부분)
   단점: 실행 시간 예측 불가, 실시간 거래량 모니터링 필요

이 모듈에서는 TWAP과 VWAP을 구현합니다.
실제 주문은 executor를 통해 나가며, 모의매매에서도 동일하게 동작합니다.
=============================================================================
"""

import time
import logging
import math
from typing import List, Dict, Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

logger = logging.getLogger("OrderSplitter")


@dataclass
class SliceOrder:
    """
    분할된 개별 주문 (Slice)

    전체 주문의 한 조각을 나타냅니다.
    
    속성:
        slice_id: 조각 번호 (1부터)
        quantity: 이 조각의 수량
        scheduled_time: 예정 실행 시간
        executed: 실행 완료 여부
        filled_price: 체결 가격
        filled_quantity: 체결 수량
    """
    slice_id: int
    quantity: int
    scheduled_time: datetime
    executed: bool = False
    filled_price: float = 0.0
    filled_quantity: int = 0
    status: str = "pending"  # pending / executed / failed


@dataclass
class SplitPlan:
    """
    주문 분할 계획

    전체 주문을 어떻게 나눌 것인지에 대한 계획입니다.

    속성:
        symbol: 종목 코드
        side: "BUY" 또는 "SELL"
        total_quantity: 전체 주문 수량
        strategy: "TWAP" 또는 "VWAP"
        slices: 분할된 조각 리스트
        duration_minutes: 전체 실행 시간 (분)
    """
    symbol: str
    side: str
    total_quantity: int
    strategy: str  # "TWAP" or "VWAP"
    slices: List[SliceOrder] = field(default_factory=list)
    duration_minutes: int = 60
    status: str = "pending"  # pending / executing / completed / cancelled

    @property
    def executed_quantity(self) -> int:
        """체결된 총 수량"""
        return sum(s.filled_quantity for s in self.slices if s.executed)

    @property
    def avg_price(self) -> float:
        """가중 평균 체결 가격"""
        total_value = sum(s.filled_price * s.filled_quantity
                          for s in self.slices if s.executed and s.filled_quantity > 0)
        total_qty = self.executed_quantity
        return total_value / total_qty if total_qty > 0 else 0

    @property
    def progress_pct(self) -> float:
        """진행률 (%)"""
        if self.total_quantity <= 0:
            return 0
        return self.executed_quantity / self.total_quantity * 100


class OrderSplitter:
    """
    주문 분할기

    대량 주문을 TWAP/VWAP 전략으로 분할하고,
    스케줄에 따라 순차적으로 실행합니다.

    사용법:
        splitter = OrderSplitter()
        
        # 분할 계획 생성
        plan = splitter.create_twap_plan("AAPL", "BUY", 1000, duration=60, slices=10)
        
        # 계획 실행 (executor의 주문 함수를 콜백으로 전달)
        splitter.execute_plan(plan, order_func=executor.buy_market)
    """

    def __init__(self, min_slice_qty: int = 1, min_slice_value: float = 10.0):
        """
        Args:
            min_slice_qty: 조각당 최소 수량 (기본 1주)
            min_slice_value: 조각당 최소 금액 (기본 $10)
        """
        self.min_slice_qty = min_slice_qty
        self.min_slice_value = min_slice_value
        self.active_plans: List[SplitPlan] = []

    def create_twap_plan(self, symbol: str, side: str,
                          total_quantity: int,
                          duration_minutes: int = 60,
                          num_slices: int = 10) -> SplitPlan:
        """
        TWAP 분할 계획 생성

        시간을 균등하게 나누어 각 시간 슬롯에 동일 수량을 배정합니다.

        예: 1000주, 60분, 10조각
        → 6분 간격으로 100주씩 10번 주문

        Args:
            symbol: 종목 코드
            side: "BUY" 또는 "SELL"
            total_quantity: 전체 수량
            duration_minutes: 실행 기간 (분)
            num_slices: 분할 조각 수

        Returns:
            SplitPlan 객체
        """
        # 조각당 수량 계산
        base_qty = total_quantity // num_slices
        remainder = total_quantity % num_slices

        # 시간 간격
        interval = timedelta(minutes=duration_minutes / num_slices)
        now = datetime.now()

        slices = []
        for i in range(num_slices):
            # 나머지는 앞쪽 조각에 1개씩 추가 분배
            qty = base_qty + (1 if i < remainder else 0)
            if qty <= 0:
                continue

            scheduled = now + interval * i
            slices.append(SliceOrder(
                slice_id=i + 1,
                quantity=qty,
                scheduled_time=scheduled,
            ))

        plan = SplitPlan(
            symbol=symbol,
            side=side,
            total_quantity=total_quantity,
            strategy="TWAP",
            slices=slices,
            duration_minutes=duration_minutes,
        )

        logger.info(
            f"[TWAP] 계획 생성: {symbol} {side} {total_quantity}주 "
            f"→ {len(slices)}조각, {duration_minutes}분"
        )

        return plan

    def create_vwap_plan(self, symbol: str, side: str,
                          total_quantity: int,
                          duration_minutes: int = 60,
                          volume_profile: Optional[List[float]] = None,
                          num_slices: int = 10) -> SplitPlan:
        """
        VWAP 분할 계획 생성

        거래량 프로파일에 따라 수량을 가중 배분합니다.
        거래량이 많은 시간에 더 많이 주문하여 VWAP에 근접합니다.

        기본 거래량 프로파일 (미국 시장 기준):
        - 개장 30분: 높음 (15%)
        - 오전 중반: 보통 (8%)
        - 점심: 낮음 (5%)
        - 오후 중반: 보통 (8%)
        - 마감 30분: 높음 (15%)
        → U자형 패턴 (장 초반/후반에 거래량 집중)

        Args:
            symbol: 종목 코드
            side: "BUY" 또는 "SELL"
            total_quantity: 전체 수량
            duration_minutes: 실행 기간 (분)
            volume_profile: 시간별 거래량 비율 리스트 (합계 1.0)
                           None이면 기본 U자형 프로파일 사용
            num_slices: 분할 조각 수

        Returns:
            SplitPlan 객체
        """
        # 기본 거래량 프로파일: U자형 (장 초반/후반 높음)
        if volume_profile is None or len(volume_profile) != num_slices:
            # U자형 프로파일 생성
            profile = []
            for i in range(num_slices):
                # 0과 num_slices-1에서 높고, 중간에서 낮은 U자형
                x = i / (num_slices - 1) if num_slices > 1 else 0.5
                # U자형: y = 2*(x-0.5)^2 + 0.5
                weight = 2 * (x - 0.5) ** 2 + 0.5
                profile.append(weight)

            # 정규화 (합계 1.0)
            total_weight = sum(profile)
            volume_profile = [w / total_weight for w in profile]

        # 가중치에 따라 수량 배분
        interval = timedelta(minutes=duration_minutes / num_slices)
        now = datetime.now()

        slices = []
        allocated = 0
        for i in range(num_slices):
            if i == num_slices - 1:
                # 마지막 조각은 나머지 전부
                qty = total_quantity - allocated
            else:
                qty = max(self.min_slice_qty,
                          round(total_quantity * volume_profile[i]))

            if qty <= 0:
                continue

            allocated += qty
            scheduled = now + interval * i

            slices.append(SliceOrder(
                slice_id=i + 1,
                quantity=qty,
                scheduled_time=scheduled,
            ))

        plan = SplitPlan(
            symbol=symbol,
            side=side,
            total_quantity=total_quantity,
            strategy="VWAP",
            slices=slices,
            duration_minutes=duration_minutes,
        )

        logger.info(
            f"[VWAP] 계획 생성: {symbol} {side} {total_quantity}주 "
            f"→ {len(slices)}조각, {duration_minutes}분"
        )

        return plan

    def execute_plan(self, plan: SplitPlan,
                     order_func: Callable,
                     dry_run: bool = False) -> SplitPlan:
        """
        분할 계획 실행

        각 조각을 스케줄된 시간에 순차적으로 실행합니다.
        실제 실행 함수(order_func)는 executor의 buy_market/sell_market을 전달합니다.

        Args:
            plan: 분할 계획
            order_func: 주문 실행 함수 (symbol, quantity, strategy) → Order
            dry_run: True이면 실제 주문 없이 시뮬레이션만

        Returns:
            실행 결과가 업데이트된 SplitPlan
        """
        plan.status = "executing"
        self.active_plans.append(plan)

        logger.info(
            f"[{plan.strategy}] 실행 시작: {plan.symbol} {plan.side} "
            f"{plan.total_quantity}주 → {len(plan.slices)}조각"
        )

        for s in plan.slices:
            # 스케줄된 시간까지 대기
            now = datetime.now()
            if s.scheduled_time > now and not dry_run:
                wait_seconds = (s.scheduled_time - now).total_seconds()
                if wait_seconds > 0:
                    logger.info(
                        f"[{plan.strategy}] 조각 #{s.slice_id} 대기: "
                        f"{wait_seconds:.0f}초 후 {s.quantity}주"
                    )
                    time.sleep(min(wait_seconds, 600))  # 최대 10분 대기

            # 주문 실행
            try:
                if dry_run:
                    # 시뮬레이션: 체결 가격은 0으로 표시
                    s.executed = True
                    s.filled_quantity = s.quantity
                    s.filled_price = 0
                    s.status = "executed"
                    logger.info(
                        f"[{plan.strategy}] 조각 #{s.slice_id} 시뮬레이션: "
                        f"{s.quantity}주"
                    )
                else:
                    order = order_func(
                        plan.symbol, s.quantity,
                        strategy=f"{plan.strategy}_split"
                    )

                    if order.status.value == "filled":
                        s.executed = True
                        s.filled_quantity = order.filled_quantity or s.quantity
                        s.filled_price = order.filled_price or 0
                        s.status = "executed"
                        logger.info(
                            f"[{plan.strategy}] 조각 #{s.slice_id} 체결: "
                            f"{s.filled_quantity}주 @ ${s.filled_price:.2f}"
                        )
                    else:
                        s.status = "failed"
                        logger.warning(
                            f"[{plan.strategy}] 조각 #{s.slice_id} 실패: "
                            f"{order.status.value}"
                        )

            except Exception as e:
                s.status = "failed"
                logger.error(
                    f"[{plan.strategy}] 조각 #{s.slice_id} 오류: {e}"
                )

        plan.status = "completed"

        logger.info(
            f"[{plan.strategy}] 실행 완료: {plan.symbol} "
            f"{plan.executed_quantity}/{plan.total_quantity}주 "
            f"(평균 ${plan.avg_price:.2f}, 진행률 {plan.progress_pct:.1f}%)"
        )

        return plan

    def cancel_plan(self, plan: SplitPlan):
        """미실행 조각 취소"""
        for s in plan.slices:
            if not s.executed:
                s.status = "cancelled"
        plan.status = "cancelled"
        logger.info(f"[{plan.strategy}] 계획 취소: {plan.symbol}")

    def get_active_plans(self) -> List[Dict]:
        """활성 계획 목록 (대시보드용)"""
        return [
            {
                "symbol": p.symbol,
                "side": p.side,
                "strategy": p.strategy,
                "total": p.total_quantity,
                "executed": p.executed_quantity,
                "progress": round(p.progress_pct, 1),
                "avg_price": round(p.avg_price, 2),
                "status": p.status,
                "slices": len(p.slices),
            }
            for p in self.active_plans
        ]
