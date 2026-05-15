"""
=============================================================================
executor/exit_manager.py - 포지션 청산(Exit) 관리 모듈
=============================================================================

진입 후 보유 포지션에 대한 청산 의사결정을 담당합니다.
앙상블 신호와 별개로 다음 조건을 자동 체크합니다:

1. 하드 스탑 (Hard Stop Loss)
   - 진입가 - ATR × multiplier 도달 시 강제 매도
   - 표준: 2~3× ATR (Le Beau Chandelier Exit 연구 기준)

2. 익절 (Take Profit) - 분할 청산
   - 1차 목표(avg + ATR × RR): 50% 매도, 손절선 본전(breakeven)으로 상향
   - 2차 목표(avg + ATR × RR × 2): 나머지 50% 매도

3. 트레일링 스탑 (Trailing Stop) - Chandelier Exit
   - 1차 목표 도달 후 활성화
   - stop = max(highest_high_since_entry - ATR × 3, breakeven_price)
   - 추세 시 이익 보호, 반전 시 즉시 청산

4. 시간 기반 청산 (Time Stop) - 옵션
   - 보유 기간 초과 시 강제 청산 (단타: 5일, 스윙: 30일, 장기: 180일)

[참고: ATR 손절 이론]
- Le Beau (Chandelier Exit 창시자): 22일 ATR × 3
- 백테스트: 3× ATR이 profit factor 1.61로 최적
- 2× ATR은 스윙, 3× ATR은 트렌드 팔로잉에 적합

[학술 자료]
- "ATR Trailing Stop Optimization" — drawdown 22% 감소
- Half-Kelly + ATR stop = 약 75% 성장률, 변동성 대폭 감소
=============================================================================
"""

import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, List
from datetime import datetime, timedelta
from enum import Enum

logger = logging.getLogger(__name__)


class ExitReason(Enum):
    """청산 사유 (감사 추적용)"""
    NONE = "none"
    STOP_LOSS = "stop_loss"           # 하드 스탑 발동
    TAKE_PROFIT_1 = "take_profit_1"   # 1차 목표 (분할)
    TAKE_PROFIT_2 = "take_profit_2"   # 2차 목표 (전량)
    TRAILING_STOP = "trailing_stop"   # 트레일링 스탑
    TIME_STOP = "time_stop"           # 보유기간 초과
    SIGNAL_SELL = "signal_sell"       # 앙상블 매도 신호


@dataclass
class ExitDecision:
    """청산 의사결정 결과"""
    should_exit: bool = False
    reason: ExitReason = ExitReason.NONE
    sell_ratio: float = 1.0           # 0.0(매도 안함) ~ 1.0(전량)
    new_stop_price: Optional[float] = None  # 트레일링 시 갱신된 손절가
    detail: str = ""                   # 로그/UI용 상세

    def __bool__(self):
        return self.should_exit


@dataclass
class PositionExitState:
    """
    포지션별 청산 상태 추적 (메모리 + DB 동기화)

    분할 매도와 트레일링 스탑을 위해 진입 후 변동 사항을 추적합니다.
    """
    symbol: str
    entry_price: float                 # 평균 진입가 (native currency)
    entry_atr: float                   # 진입 시점 ATR (단위 통화)
    entry_time: datetime
    initial_stop: float                # 최초 하드 스탑
    target_1: float                    # 1차 목표가 (분할)
    target_2: float                    # 2차 목표가 (전량)

    current_stop: float = 0.0          # 현재 활성 손절선 (트레일링으로 변경 가능)
    highest_price_since_entry: float = 0.0  # Chandelier Exit용
    partial_sold_pct: float = 0.0      # 이미 매도한 비중 (0.0/0.5/1.0)
    holding_period_days_max: int = 30  # 시간 기반 청산 임계값

    def __post_init__(self):
        if self.current_stop == 0.0:
            self.current_stop = self.initial_stop
        if self.highest_price_since_entry == 0.0:
            self.highest_price_since_entry = self.entry_price


class ExitManager:
    """
    포지션 청산 의사결정 엔진

    각 분석 사이클에서 보유 포지션에 대해 다음 순서로 체크:
    1. 하드 스탑 (가장 우선)
    2. 트레일링 스탑 (1차 목표 달성 후)
    3. 익절 1차 (50% 분할)
    4. 익절 2차 (전량)
    5. 시간 기반 청산 (옵션)

    Parameters:
        atr_stop_multiplier: 하드 스탑 ATR 배수 (기본 2.0)
        rr_ratio: Risk-Reward 비율 (기본 2.0 = 1:2)
        trailing_atr_multiplier: 트레일링 스탑 ATR 배수 (기본 3.0, Chandelier)
        enable_partial: 분할 청산 사용 여부 (기본 True)
        enable_time_stop: 시간 기반 청산 (기본 False)
    """

    def __init__(
        self,
        atr_stop_multiplier: float = 2.0,
        rr_ratio: float = 2.0,
        trailing_atr_multiplier: float = 3.0,
        enable_partial: bool = True,
        enable_time_stop: bool = False,
    ):
        self.atr_stop_mult = atr_stop_multiplier
        self.rr_ratio = rr_ratio
        self.trailing_mult = trailing_atr_multiplier
        self.enable_partial = enable_partial
        self.enable_time_stop = enable_time_stop

        # 활성 포지션 상태 (symbol -> PositionExitState)
        self.states: Dict[str, PositionExitState] = {}

    def register_entry(
        self,
        symbol: str,
        entry_price: float,
        atr: float,
        atr_stop_mult: Optional[float] = None,
        rr_ratio: Optional[float] = None,
        holding_days_max: int = 30,
    ) -> PositionExitState:
        """
        신규 진입 시 청산 상태 등록

        포지션 유형(단타/스윙/장기)별로 atr_stop_mult, rr_ratio를 다르게
        적용 가능하므로 인자로 받음 (None이면 기본값 사용).
        """
        stop_mult = atr_stop_mult if atr_stop_mult is not None else self.atr_stop_mult
        rr = rr_ratio if rr_ratio is not None else self.rr_ratio

        initial_stop = entry_price - (atr * stop_mult)
        target_1 = entry_price + (atr * stop_mult * rr)         # 1차: 1R 이익
        target_2 = entry_price + (atr * stop_mult * rr * 2.0)   # 2차: 2R 이익

        state = PositionExitState(
            symbol=symbol,
            entry_price=entry_price,
            entry_atr=atr,
            entry_time=datetime.now(),
            initial_stop=initial_stop,
            target_1=target_1,
            target_2=target_2,
            holding_period_days_max=holding_days_max,
        )
        self.states[symbol] = state
        logger.info(
            f"[Exit] {symbol} 진입 등록: "
            f"진입 {entry_price:,.2f}, "
            f"손절 {initial_stop:,.2f} ({-stop_mult}×ATR), "
            f"목표1 {target_1:,.2f}, 목표2 {target_2:,.2f}"
        )
        return state

    def restore_state(self, state: PositionExitState):
        """DB에서 복원한 상태를 메모리에 등록 (재시작 대응)"""
        self.states[state.symbol] = state

    def unregister(self, symbol: str):
        """포지션 청산 완료 시 상태 제거"""
        self.states.pop(symbol, None)

    def commit_exit(self, symbol: str, decision: "ExitDecision") -> None:
        """
        매도 성공 확정 후 ExitManager 내부 상태 갱신 (Phase 6C 핵심)

        ⚠️ 반드시 매도 주문이 FILLED 상태로 확정된 직후에만 호출하세요.
        매도 실패 시 호출하면 안 됩니다 (상태 미스매치 발생).

        sell_ratio == 1.0: 전량 청산 → unregister()로 state 제거
        sell_ratio == 0.5: 분할 익절 → partial_sold_pct 갱신 + 손절선 본전 상향
        """
        if symbol not in self.states:
            return

        state = self.states[symbol]
        if decision.sell_ratio >= 1.0:
            # 전량 청산 → 상태 제거 (다음 evaluate에서 무시됨)
            self.states.pop(symbol, None)
            logger.info(f"[Exit] {symbol} 전량 청산 확정 → 상태 제거")
        else:
            # ★ CRITICAL FIX: 누적 매도비중 (이전: max() → 2회 분할매도 시 state 불일치)
            # 시나리오: 50% 매도 → max(0, 0.5)=0.5, 추가 50% 매도 → max(0.5, 0.5)=0.5
            #          실제 포지션은 0%인데 ExitManager는 50% 남아있다고 판단 → 청산 누락
            # 수정: min(1.0, prev + ratio) 누적
            state.partial_sold_pct = min(1.0, state.partial_sold_pct + decision.sell_ratio)
            # 손절선은 항상 위로만 (트레일링 원칙)
            if decision.new_stop_price and decision.new_stop_price > state.current_stop:
                state.current_stop = decision.new_stop_price
            # 100% 도달 시 상태 제거
            if state.partial_sold_pct >= 0.999:
                self.states.pop(symbol, None)
                logger.info(
                    f"[Exit] {symbol} 누적 매도비중 {state.partial_sold_pct*100:.0f}% → 상태 제거"
                )
            else:
                logger.info(
                    f"[Exit] {symbol} 분할매도 확정 — "
                    f"누적 매도비중 {state.partial_sold_pct*100:.0f}%, "
                    f"손절가 ₩{state.current_stop:,.2f}"
                )

    def evaluate(
        self,
        symbol: str,
        current_price: float,
    ) -> ExitDecision:
        """
        보유 포지션의 청산 조건을 평가합니다.

        매 분석 사이클(또는 가격 갱신 시)에 호출하여 청산 여부 결정.

        Parameters:
            symbol: 종목코드
            current_price: 현재가 (native currency)

        Returns:
            ExitDecision: 청산 여부, 사유, 매도 비중, 갱신된 손절가
        """
        state = self.states.get(symbol)
        if not state:
            # 등록되지 않은 종목 (구버전 포지션 등)
            return ExitDecision()

        # ── 트래킹: 최고가 갱신 (Chandelier Exit용) ──
        if current_price > state.highest_price_since_entry:
            state.highest_price_since_entry = current_price

        # ── 1. 트레일링 스탑 갱신 (Chandelier Exit) ──
        # 1차 목표 도달 후에만 활성화 — 손절선만 위로(상향), 절대 아래로(하향) 안 함
        if state.partial_sold_pct >= 0.5:
            chandelier_stop = (
                state.highest_price_since_entry - state.entry_atr * self.trailing_mult
            )
            if chandelier_stop > state.current_stop:
                state.current_stop = chandelier_stop
                logger.debug(
                    f"[Exit] {symbol} 트레일링 갱신: 손절 {chandelier_stop:,.2f} "
                    f"(최고가 {state.highest_price_since_entry:,.2f} - ATR×{self.trailing_mult})"
                )

        # ── 2. 손절/트레일링 발동 체크 ──
        # 분할 익절 전: 최초 하드 스탑 (entry - ATR×stop_mult) → STOP_LOSS
        # 분할 익절 후: 본전 또는 트레일링 → TRAILING_STOP (이미 이익 본 상태)
        if current_price <= state.current_stop:
            is_trailing = state.partial_sold_pct >= 0.5
            pnl_pct = (current_price / state.entry_price - 1) * 100
            if is_trailing:
                return ExitDecision(
                    should_exit=True,
                    reason=ExitReason.TRAILING_STOP,
                    sell_ratio=1.0,
                    new_stop_price=state.current_stop,
                    detail=(
                        f"트레일링 스탑: "
                        f"현재 {current_price:,.2f} ≤ 트레일링 {state.current_stop:,.2f} "
                        f"(누적 손익 {pnl_pct:+.2f}%)"
                    ),
                )
            else:
                return ExitDecision(
                    should_exit=True,
                    reason=ExitReason.STOP_LOSS,
                    sell_ratio=1.0,
                    detail=(
                        f"하드 스탑 발동: "
                        f"현재 {current_price:,.2f} ≤ 손절 {state.current_stop:,.2f} "
                        f"(손실 {pnl_pct:+.2f}%)"
                    ),
                )

        # ── 3. 익절 1차 (분할 50%) ──
        # ⚠️ CRITICAL (Phase 6C): state 변경은 commit_exit()에서만 — 매도 성공 확정 후
        # 이전 버그: 여기서 partial_sold_pct=0.5로 미리 변경 → 매도 실패 시
        # ExitManager는 "이미 50% 팔았다"고 판단해서 트레일링 스탑만 보호하지만
        # 실제 포지션은 100%이고 손절가는 breakeven으로 올라가 있어
        # 큰 변동성에서 보호받지 못함 → 분할 익절을 실패하면서 보호 무력화
        # 수정: 매도 성공 후 commit_exit()에서만 state 변경
        if (
            self.enable_partial
            and state.partial_sold_pct < 0.5
            and current_price >= state.target_1
        ):
            new_stop = state.entry_price  # 본전 (commit 시 적용)
            profit_pct = (current_price / state.entry_price - 1) * 100
            return ExitDecision(
                should_exit=True,
                reason=ExitReason.TAKE_PROFIT_1,
                sell_ratio=0.5,
                new_stop_price=new_stop,
                detail=(
                    f"1차 목표 달성: "
                    f"현재 {current_price:,.2f} ≥ 목표 {state.target_1:,.2f} "
                    f"(이익 {profit_pct:+.2f}%) | "
                    f"50% 매도 + 손절선 본전 상향 (매도 성공 시)"
                ),
            )

        # ── 4. 익절 2차 (전량) ──
        if current_price >= state.target_2:
            profit_pct = (current_price / state.entry_price - 1) * 100
            return ExitDecision(
                should_exit=True,
                reason=ExitReason.TAKE_PROFIT_2,
                sell_ratio=1.0,
                detail=(
                    f"2차 목표 달성: "
                    f"현재 {current_price:,.2f} ≥ 목표2 {state.target_2:,.2f} "
                    f"(이익 {profit_pct:+.2f}%)"
                ),
            )

        # ── 5. 시간 기반 청산 ──
        if self.enable_time_stop:
            held_days = (datetime.now() - state.entry_time).days
            if held_days >= state.holding_period_days_max:
                return ExitDecision(
                    should_exit=True,
                    reason=ExitReason.TIME_STOP,
                    sell_ratio=1.0,
                    detail=(
                        f"시간 청산: 보유 {held_days}일 ≥ "
                        f"한도 {state.holding_period_days_max}일"
                    ),
                )

        return ExitDecision()

    def get_state_dict(self, symbol: str) -> Optional[Dict]:
        """대시보드/리포트용 현재 상태 딕셔너리 반환"""
        state = self.states.get(symbol)
        if not state:
            return None
        return {
            "symbol": state.symbol,
            "entry_price": state.entry_price,
            "entry_atr": state.entry_atr,
            "current_stop": state.current_stop,
            "initial_stop": state.initial_stop,
            "target_1": state.target_1,
            "target_2": state.target_2,
            "highest_since_entry": state.highest_price_since_entry,
            "partial_sold_pct": state.partial_sold_pct,
            "entry_time": state.entry_time.isoformat() if state.entry_time else None,
        }
