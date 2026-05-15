"""
=============================================================================
risk/stop_loss.py - 손절/익절 관리기
=============================================================================

포지션의 손절(Stop-Loss)과 익절(Take-Profit)을 관리합니다.

손절이란?
- 미리 정한 가격에 도달하면 무조건 매도하여 손실을 제한하는 것
- "더 떨어지면 회복할 수 없다" 지점에서 자르는 것
- 감정적 판단을 배제하고 기계적으로 실행해야 함

왜 손절이 필수인가?
- 10% 손실 → 회복에 11% 필요
- 20% 손실 → 회복에 25% 필요
- 50% 손실 → 회복에 100% 필요 (2배 올라야!)
- 손실이 커질수록 회복이 기하급수적으로 어려워짐

지원하는 손절 방식:
1. 고정 비율: 진입가의 N% 하락 시
2. ATR 기반: ATR의 N배 하락 시 (변동성 반영, 권장)
3. 트레일링 스탑: 고점에서 N% 하락 시 (수익 보호)
4. 시간 기반: N일 후 수익이 없으면 청산
=============================================================================
"""

import numpy as np
import pandas as pd
from typing import Optional, Dict
from dataclasses import dataclass
from enum import Enum


class StopType(Enum):
    """손절 유형"""
    FIXED = "fixed"          # 고정 비율
    ATR = "atr"              # ATR 기반
    TRAILING = "trailing"    # 트레일링 스탑
    TIME = "time"            # 시간 기반


@dataclass
class StopLevel:
    """
    손절/익절 레벨 정의

    Attributes:
        stop_price: 손절 가격 (이 가격 이하로 떨어지면 매도)
        take_profit_price: 익절 가격 (이 가격 이상 오르면 매도)
        trail_distance: 트레일링 거리 (고점에서 이만큼 떨어지면 매도)
        time_limit: 시간 제한 (일)
        stop_type: 사용된 손절 유형
    """
    stop_price: float = 0.0
    take_profit_price: float = 0.0
    trail_distance: float = 0.0
    time_limit: int = 0
    stop_type: StopType = StopType.ATR
    reason: str = ""


class StopLossManager:
    """
    손절/익절 관리기

    사용법:
        manager = StopLossManager(atr_multiplier=2.0, risk_reward=2.0)
        stop = manager.calculate_stops(entry_price=50000, atr=1500)
        print(f"손절가: {stop.stop_price}, 익절가: {stop.take_profit_price}")

        # 트레일링 스탑 업데이트
        new_stop = manager.update_trailing(current_high=55000, stop)
    """

    def __init__(
        self,
        atr_multiplier: float = 2.0,
        risk_reward_ratio: float = 2.0,
        trailing_pct: float = 0.05,
        max_hold_days: int = 30
    ):
        """
        Parameters:
            atr_multiplier: ATR 손절 배수 (기본 2.0 = ATR의 2배에서 손절)
            risk_reward_ratio: 리스크:보상 비율 (기본 2.0 = 손절폭의 2배에서 익절)
            trailing_pct: 트레일링 스탑 비율 (고점 대비 5% 하락 시)
            max_hold_days: 최대 보유 기간 (이 기간 후 수익 없으면 청산)
        """
        self.atr_multiplier = atr_multiplier
        self.risk_reward_ratio = risk_reward_ratio
        self.trailing_pct = trailing_pct
        self.max_hold_days = max_hold_days

    def calculate_stops(
        self,
        entry_price: float,
        atr: float = 0.0,
        stop_type: StopType = StopType.ATR,
        fixed_pct: float = 0.05
    ) -> StopLevel:
        """
        진입 가격 기준 손절/익절 레벨 계산

        Parameters:
            entry_price: 진입(매수) 가격
            atr: ATR 값 (ATR 방식에 필요)
            stop_type: 손절 유형
            fixed_pct: 고정 비율 (FIXED 방식에서 사용)

        Returns:
            StopLevel 객체
        """
        if stop_type == StopType.ATR and atr > 0:
            return self._atr_stop(entry_price, atr)
        elif stop_type == StopType.TRAILING:
            return self._trailing_stop(entry_price)
        else:
            return self._fixed_stop(entry_price, fixed_pct)

    def _atr_stop(self, entry_price: float, atr: float) -> StopLevel:
        """
        ATR 기반 손절 (★ 가장 권장하는 방식)

        원리:
        - ATR = 하루 평균 변동폭
        - 손절 = 진입가 - (ATR × 배수)
          → "정상적인 변동"을 넘어서는 하락이면 빠져나옴
        - 익절 = 진입가 + (ATR × 배수 × 리스크보상비율)

        예: 진입가 50,000, ATR 1,500, 배수 2.0, 보상비율 2.0
            손절 = 50,000 - 3,000 = 47,000
            익절 = 50,000 + 6,000 = 56,000
            → 3,000원 손실 감수, 6,000원 이익 기대
        """
        stop_distance = atr * self.atr_multiplier
        stop_price = entry_price - stop_distance
        take_profit = entry_price + (stop_distance * self.risk_reward_ratio)

        return StopLevel(
            stop_price=stop_price,
            take_profit_price=take_profit,
            trail_distance=stop_distance,
            time_limit=self.max_hold_days,
            stop_type=StopType.ATR,
            reason=(
                f"ATR({atr:.2f}) × {self.atr_multiplier} = "
                f"손절폭 {stop_distance:.2f}"
            )
        )

    def _fixed_stop(self, entry_price: float, pct: float) -> StopLevel:
        """고정 비율 손절"""
        stop_price = entry_price * (1 - pct)
        take_profit = entry_price * (1 + pct * self.risk_reward_ratio)

        return StopLevel(
            stop_price=stop_price,
            take_profit_price=take_profit,
            stop_type=StopType.FIXED,
            reason=f"고정 {pct*100:.1f}% 손절"
        )

    def _trailing_stop(self, entry_price: float) -> StopLevel:
        """트레일링 스탑 초기 설정"""
        trail_distance = entry_price * self.trailing_pct
        stop_price = entry_price - trail_distance

        return StopLevel(
            stop_price=stop_price,
            take_profit_price=0,  # 트레일링은 익절이 없음 (계속 올라감)
            trail_distance=trail_distance,
            stop_type=StopType.TRAILING,
            reason=f"트레일링 {self.trailing_pct*100:.1f}%"
        )

    def update_trailing_stop(
        self,
        current_high: float,
        current_stop: StopLevel
    ) -> StopLevel:
        """
        트레일링 스탑 업데이트

        가격이 올라가면 손절선도 따라 올라갑니다.
        단, 내려갈 때는 손절선이 내려가지 않습니다 (단방향).

        Parameters:
            current_high: 진입 이후 최고가
            current_stop: 현재 StopLevel

        Returns:
            업데이트된 StopLevel
        """
        new_stop_price = current_high * (1 - self.trailing_pct)

        # 손절선은 올라가기만 함 (내려가지 않음)
        if new_stop_price > current_stop.stop_price:
            current_stop.stop_price = new_stop_price
            current_stop.reason = (
                f"트레일링 업데이트: 고점 {current_high:.2f} → "
                f"손절 {new_stop_price:.2f}"
            )

        return current_stop

    def should_exit(
        self,
        current_price: float,
        stop_level: StopLevel,
        holding_days: int = 0
    ) -> Dict:
        """
        현재 가격에서 청산해야 하는지 판단

        Parameters:
            current_price: 현재가
            stop_level: 적용 중인 StopLevel
            holding_days: 현재 보유 일수

        Returns:
            {"should_exit": bool, "reason": str, "exit_type": str}
        """
        # 손절 체크
        if current_price <= stop_level.stop_price:
            return {
                "should_exit": True,
                "reason": f"손절선 도달 ({current_price:.2f} <= {stop_level.stop_price:.2f})",
                "exit_type": "STOP_LOSS"
            }

        # 익절 체크
        if (stop_level.take_profit_price > 0 and
                current_price >= stop_level.take_profit_price):
            return {
                "should_exit": True,
                "reason": f"익절선 도달 ({current_price:.2f} >= {stop_level.take_profit_price:.2f})",
                "exit_type": "TAKE_PROFIT"
            }

        # 시간 제한 체크
        if (stop_level.time_limit > 0 and
                holding_days >= stop_level.time_limit):
            return {
                "should_exit": True,
                "reason": f"보유 기간 초과 ({holding_days}일 >= {stop_level.time_limit}일)",
                "exit_type": "TIME_LIMIT"
            }

        return {
            "should_exit": False,
            "reason": "유지",
            "exit_type": None
        }
