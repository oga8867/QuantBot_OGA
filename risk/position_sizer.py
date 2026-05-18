"""
=============================================================================
risk/position_sizer.py - 포지션 사이징 엔진
=============================================================================

"얼마나 살 것인가?"를 결정하는 핵심 리스크 관리 모듈입니다.

포지션 사이징(Position Sizing)이란?
- 한 번의 거래에 자본금의 몇 %를 투입할지 결정하는 것
- 수익률보다 더 중요한 요소 (잘못하면 파산)
- 아무리 좋은 전략이라도 오버사이징하면 파산할 수 있음

지원하는 방법:
1. 고정 비율 (Fixed Fraction): 항상 자본금의 N%만 투입
2. Kelly Criterion: 수학적 최적 투입 비율
3. ATR 기반: 변동성에 따라 동적으로 조절
4. 동일 리스크 (Equal Risk): 모든 포지션의 리스크 금액을 동일하게

★ 핵심 원칙:
- 1회 거래 손실 < 자본금의 2% (절대 넘지 말 것)
- 전체 포트폴리오 리스크 < 자본금의 6~10%
- 확신이 높더라도 단일 종목 20% 초과 금지
=============================================================================
"""

import numpy as np
from typing import Dict, Optional
from dataclasses import dataclass


@dataclass
class PositionSize:
    """
    포지션 사이징 결과

    Attributes:
        shares: 매수 수량 (주)
        value: 투입 금액
        pct_of_capital: 자본금 대비 비율
        risk_amount: 이 포지션의 최대 손실 금액
        stop_price: 손절 가격
        method: 사용된 사이징 방법
    """
    shares: int = 0
    value: float = 0.0
    pct_of_capital: float = 0.0
    risk_amount: float = 0.0
    stop_price: float = 0.0
    method: str = ""
    reason: str = ""


class PositionSizer:
    """
    포지션 사이징 계산기

    사용법:
        sizer = PositionSizer(capital=10_000_000, risk_per_trade=0.02)
        result = sizer.calculate(
            price=50000,
            atr=1500,
            method="atr",
            confidence=0.8
        )
        print(f"매수 수량: {result.shares}주, 손절가: {result.stop_price}")
    """

    def __init__(
        self,
        capital: float,
        risk_per_trade: float = 0.02,
        max_position_pct: float = 0.10,
        stop_loss_atr_mult: float = 2.0,
        kelly_fraction: float = 0.5
    ):
        """
        Parameters:
            capital: 현재 총 자본금
            risk_per_trade: 1회 최대 리스크 비율 (0.02 = 2%)
            max_position_pct: 단일 종목 최대 비율 (0.10 = 10%)
            stop_loss_atr_mult: ATR 손절 배수
            kelly_fraction: Kelly 축소 계수 (0.5 = Half Kelly)
        """
        self.capital = capital
        self.risk_per_trade = risk_per_trade
        self.max_position_pct = max_position_pct
        self.stop_loss_atr_mult = stop_loss_atr_mult
        self.kelly_fraction = kelly_fraction

    def calculate(
        self,
        price: float,
        atr: float = 0.0,
        method: str = "atr",
        confidence: float = 1.0,
        win_rate: float = 0.0,
        avg_win: float = 0.0,
        avg_loss: float = 0.0,
        symbol: str = ""
    ) -> PositionSize:
        """
        포지션 크기 계산 (메인 메서드)

        Parameters:
            price: 현재 주가 (또는 진입 예정 가격, 원래 통화 기준)
            atr: ATR 값 (변동성, atr/kelly 방법에 필요, 원래 통화 기준)
            method: 사이징 방법
                - "fixed": 고정 비율
                - "atr": ATR 기반 동적 사이징 (권장)
                - "kelly": Kelly Criterion
                - "equal_risk": 동일 리스크 금액
            confidence: 신호 신뢰도 (0~1, 낮으면 포지션 축소)
            win_rate: 승률 (kelly 방법에 필요)
            avg_win: 평균 수익률 (kelly 방법에 필요)
            avg_loss: 평균 손실률 (kelly 방법에 필요, 양수)
            symbol: 종목 코드 (환율 변환에 사용, 미국 주식이면 USD→KRW 변환)

        Returns:
            PositionSize 객체
        """
        if price <= 0:
            return PositionSize(reason="유효하지 않은 가격")

        # ★ 환율 변환: 미국 주식이면 가격/ATR을 KRW로 변환
        # 자본금(self.capital)이 KRW이므로, 같은 단위로 맞춰야
        # "자본금 대비 몇 %" 계산이 올바르게 됩니다.
        # 변환 후 계산한 shares 수는 그대로 유효 (주식 수 자체는 통화 무관)
        fx_rate = 1.0
        if symbol:
            try:
                from utils.market import is_us_stock, get_exchange_rate
                if is_us_stock(symbol):
                    fx_rate = get_exchange_rate()
            except ImportError:
                pass

        # KRW 환산 가격/ATR (사이징 계산용)
        price_krw = price * fx_rate
        atr_krw = atr * fx_rate

        # 방법별 계산 (KRW 환산 가격으로 계산 → shares는 통화 무관)
        if method == "kelly" and win_rate > 0 and avg_loss > 0 and avg_win > 0:
            result = self._kelly_sizing(price_krw, win_rate, avg_win, avg_loss)
        elif method == "atr" and atr > 0:
            result = self._atr_sizing(price_krw, atr_krw)
        elif method == "equal_risk" and atr > 0:
            result = self._equal_risk_sizing(price_krw, atr_krw)
        else:
            result = self._fixed_sizing(price_krw)

        # ★ value를 KRW로 재계산 (내부 메서드는 price_krw 기반이므로 이미 KRW)
        result.value = result.shares * price_krw
        result.pct_of_capital = result.value / self.capital if self.capital > 0 else 0

        # ── 신뢰도에 따른 조절 (신뢰도 낮으면 포지션 축소) ──
        # ★ 버그 수정: confidence 보정 전 원래 수량을 기억해두고,
        #   보정 결과가 0주가 되더라도 원래 1주 이상이었으면 최소 1주 보장.
        #   예) SK하이닉스: shares=1, confidence=0.5 → int(0.5)=0 → 1주로 복원
        pre_adjust_shares = result.shares
        if confidence < 1.0 and result.shares > 1:
            # 2주 이상일 때만 confidence 보정 적용 (1주는 더 줄일 수 없으므로)
            adjusted = int(result.shares * confidence)
            result.shares = max(adjusted, 1)  # 최소 1주 보장
            result.value = result.shares * price_krw
            result.pct_of_capital = result.value / self.capital

        # ─── 안전장치: 최대 포지션 한도 적용 ─────────────────────────
        max_value = self.capital * self.max_position_pct
        if result.value > max_value:
            result.shares = int(max_value / price_krw)
            result.value = result.shares * price_krw
            result.pct_of_capital = result.value / self.capital
            result.reason += " (최대한도 적용)"

        # 최소 1주 이상 보장
        # 원래 사이징 결과가 1주 이상이었는데 보정으로 0이 됐으면 1주 복원
        if result.shares < 1:
            if pre_adjust_shares >= 1:
                # 원래 계산에서 1주 이상 나왔으면, 최소 1주는 매수
                result.shares = 1
                result.value = price_krw
                result.pct_of_capital = price_krw / self.capital
                result.reason = "최소 1주 보장 (confidence 보정 후)"
            else:
                result.shares = 0
                result.value = 0
                result.reason = "자본 부족 또는 리스크 초과"

        return result

    def _fixed_sizing(self, price: float) -> PositionSize:
        """
        고정 비율 방식

        자본금의 risk_per_trade 비율만큼을 투입합니다.
        가장 단순하지만 변동성을 반영하지 않는 단점이 있습니다.
        """
        position_value = self.capital * self.risk_per_trade * 5  # 리스크의 5배 투입
        shares = int(position_value / price)

        return PositionSize(
            shares=shares,
            value=shares * price,
            pct_of_capital=(shares * price) / self.capital,
            risk_amount=self.capital * self.risk_per_trade,
            stop_price=0,  # 고정 방식에서는 별도 손절 없음
            method="fixed"
        )

    def _atr_sizing(self, price: float, atr: float) -> PositionSize:
        """
        ATR 기반 포지션 사이징 (★ 권장 방법)

        원리:
        1. 허용 손실 금액 = 자본금 × risk_per_trade
        2. 1주당 리스크 = ATR × 배수 (= 손절까지의 거리)
        3. 매수 수량 = 허용 손실 / 1주당 리스크

        이렇게 하면:
        - 변동성이 큰 종목 → 적게 삼 (자동으로 리스크 조절)
        - 변동성이 작은 종목 → 많이 삼 (효율적 자본 활용)
        """
        # 허용 리스크 금액
        risk_amount = self.capital * self.risk_per_trade

        # 1주당 리스크 (ATR × 배수)
        risk_per_share = atr * self.stop_loss_atr_mult

        if risk_per_share <= 0:
            return PositionSize(reason="ATR이 0")

        # 매수 수량 계산
        shares = int(risk_amount / risk_per_share)

        # 손절 가격
        stop_price = price - risk_per_share

        return PositionSize(
            shares=shares,
            value=shares * price,
            pct_of_capital=(shares * price) / self.capital,
            risk_amount=shares * risk_per_share,
            stop_price=stop_price,
            method="atr"
        )

    def _kelly_sizing(
        self,
        price: float,
        win_rate: float,
        avg_win: float,
        avg_loss: float
    ) -> PositionSize:
        """
        Kelly Criterion 포지션 사이징

        공식: f* = (p × b - q) / b
        - p = 승률
        - q = 패률 (1-p)
        - b = 평균수익 / 평균손실 (odds)

        Full Kelly는 변동성이 너무 크므로,
        Half Kelly (f*/2)를 사용합니다.
        → 수익의 75%를 유지하면서 변동성을 50% 줄임

        Parameters:
            price: 현재가
            win_rate: 승률 (0~1)
            avg_win: 평균 수익률 (양수)
            avg_loss: 평균 손실률 (양수)
        """
        # ★ avg_win<=0 가드 — b=avg_win/avg_loss가 0이 되면 kelly=.../b 에서
        #   ZeroDivisionError. avg_loss<=0도 함께 차단.
        if avg_loss <= 0 or win_rate <= 0 or avg_win <= 0:
            return PositionSize(reason="Kelly 계산 불가 (데이터 부족)")

        # 손익비 (odds)
        b = avg_win / avg_loss
        q = 1 - win_rate

        # Kelly 공식
        kelly = (win_rate * b - q) / b

        # 음수면 → 이 전략은 기대값이 음수 → 진입 금지!
        if kelly <= 0:
            return PositionSize(reason=f"Kelly 음수 ({kelly:.3f}): 전략 기대값 음수")

        # Half-Kelly (또는 사용자 지정 fraction)
        adjusted_kelly = kelly * self.kelly_fraction

        # 최대 25% 제한 (Full Kelly라도 25% 넘기면 위험)
        adjusted_kelly = min(adjusted_kelly, 0.25)

        # 투입 금액
        position_value = self.capital * adjusted_kelly
        shares = int(position_value / price)

        return PositionSize(
            shares=shares,
            value=shares * price,
            pct_of_capital=adjusted_kelly,
            risk_amount=shares * price * avg_loss,
            stop_price=price * (1 - avg_loss),
            method=f"kelly (f*={kelly:.3f}, adjusted={adjusted_kelly:.3f})"
        )

    def _equal_risk_sizing(self, price: float, atr: float) -> PositionSize:
        """
        동일 리스크 방식

        모든 포지션에서 동일한 금액의 리스크를 감수합니다.
        포트폴리오 내 모든 종목이 동일한 리스크 기여를 하게 됩니다.
        """
        # ATR 방식과 동일하지만, 개념적으로 포트폴리오 레벨 접근
        return self._atr_sizing(price, atr)

    def check_portfolio_risk(
        self,
        positions: Dict[str, Dict],
        new_position: Optional[PositionSize] = None
    ) -> Dict:
        """
        포트폴리오 전체 리스크 체크

        Parameters:
            positions: 현재 보유 포지션
                {"AAPL": {"shares": 10, "price": 150, "risk": 500}, ...}
            new_position: 추가하려는 새 포지션

        Returns:
            {"total_risk_pct": ..., "can_add": True/False, "reason": ...}
        """
        total_risk = sum(p.get("risk", 0) for p in positions.values())

        if new_position:
            total_risk += new_position.risk_amount

        risk_pct = total_risk / self.capital
        max_total_risk = self.risk_per_trade * 5  # 포트폴리오 총 리스크 한도

        return {
            "total_risk_pct": risk_pct,
            "total_risk_amount": total_risk,
            "max_allowed": max_total_risk,
            "can_add": risk_pct <= max_total_risk,
            "positions_count": len(positions),
            "reason": "OK" if risk_pct <= max_total_risk
                      else f"포트폴리오 리스크 한도 초과 ({risk_pct:.1%} > {max_total_risk:.1%})"
        }
