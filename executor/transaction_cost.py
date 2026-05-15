"""
=============================================================================
executor/transaction_cost.py - 정교한 거래비용 모델
=============================================================================

기존 봇은 고정 수수료(0.015%)와 고정 슬리피지(0.05%)를 사용했습니다.
이 모듈은 시장 미시구조 이론에 기반한 동적 거래비용을 추정합니다.

거래비용 = 수수료 + 슬리피지 + 시장 충격(Market Impact) + 스프레드

학술적 모델:
1. Almgren-Chriss (2000) "Optimal Execution of Portfolio Transactions"
   - 슬리피지 = 영구적 충격 + 일시적 충격
   - 일시적 충격 ≈ η × (Q/V) × σ
     · Q: 주문량, V: 일평균 거래량, σ: 변동성

2. Bertsimas & Lo (1998) "Optimal Control of Execution Costs"
   - 분할 매매(VWAP)로 시장 충격 최소화

3. Square-Root Law (Almgren et al. 2005)
   - 시장 충격 ≈ Y × σ × sqrt(Q / ADV)
   - Y: ~0.1 (학술 추정), σ: 변동성, ADV: 일평균 거래량

실용 추정식 (이 봇 사용):
    슬리피지(%) = 기본 + α × 변동성 × sqrt(주문량/일평균거래량)
    α: 0.1 (보수적), 0.05 (적극적)
    기본: 0.05% (호가 스프레드 추정)

주의:
- 모의매매에서는 단순화된 비용 사용 (실거래 정확도 ≠ 백테스트)
- 실거래 시 KIS API의 실제 체결가로 재보정 권장
=============================================================================
"""

import logging
import math
from dataclasses import dataclass
from typing import Optional, Dict
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class CostBreakdown:
    """거래비용 분해 (모든 값은 KRW)"""
    commission: float = 0.0           # 수수료
    spread_cost: float = 0.0          # 호가 스프레드
    slippage: float = 0.0             # 일시적 시장 충격
    permanent_impact: float = 0.0     # 영구적 시장 충격 (대량 거래만)
    total: float = 0.0
    total_pct: float = 0.0            # 거래액 대비 %
    detail: str = ""


class TransactionCostModel:
    """
    동적 거래비용 추정기

    사용법:
        model = TransactionCostModel(market="KR")
        cost = model.estimate(
            symbol="005930.KS",
            side="BUY",
            quantity=100,
            price=72000,
            avg_volume=10_000_000,  # ADV
            volatility=0.20,         # 연환산 변동성
        )
        print(f"예상 거래비용: ₩{cost.total:,.0f} ({cost.total_pct:.3f}%)")
    """

    # 시장별 기본 수수료율 (편도, 부가세 포함)
    COMMISSION_RATES = {
        "KR": {
            "paper": 0.00015,  # 모의: 0.015%
            "live": 0.00015,   # 실거래: 0.015% (KIS 기준, 증권사마다 다름)
        },
        "US": {
            "paper": 0.0,      # Alpaca/IBKR 무료
            "live": 0.0,
        },
    }

    # 시장별 거래세 (매도시에만)
    # ⚠️ 한국 거래세는 2024년 인하 후:
    #   KOSPI: 0.18% (증권거래세 0.03% + 농어촌특별세 0.15%)
    #   KOSDAQ: 0.18% (증권거래세만 0.18%, 농특세 없음)
    #   KONEX: 0.10%
    # 이전 버그: 0.23% (옛 코드, paper_executor.py와 28% 차이) → 통일
    TAX_RATES = {
        "KR": {
            "BUY": 0.0,
            "SELL": 0.0018,  # 0.18% (KOSPI/KOSDAQ 2024+ 통일)
        },
        "US": {
            "BUY": 0.0,
            "SELL": 0.0000231,  # SEC fee (매우 작음)
        },
    }

    # 호가 스프레드 추정 (변동성 비례)
    BASE_SPREAD_PCT = 0.0005  # 0.05% (대형주 평균)

    # Almgren Square-Root Law 계수
    IMPACT_COEFFICIENT = 0.10

    def __init__(self, market: str = "KR", paper: bool = True):
        self.market = market
        self.paper = paper

    def estimate(
        self,
        symbol: str,
        side: str,
        quantity: int,
        price: float,
        avg_volume: Optional[float] = None,
        volatility: Optional[float] = None,
    ) -> CostBreakdown:
        """
        주문에 대한 예상 거래비용 추정

        Parameters:
            symbol: 종목코드
            side: "BUY" 또는 "SELL"
            quantity: 주문 수량
            price: 체결 예상 가격 (단위 통화)
            avg_volume: 일평균 거래량 (주). None이면 시장 충격 = 0
            volatility: 연환산 변동성 (예: 0.20 = 20%). None이면 기본 0.20

        Returns:
            CostBreakdown: 비용 분해 + 총합
        """
        side = side.upper()
        notional = quantity * price

        # ── 1. 수수료 ──
        comm_rate = self.COMMISSION_RATES.get(self.market, {}).get(
            "paper" if self.paper else "live", 0.00015
        )
        commission = notional * comm_rate

        # ── 2. 거래세 (매도시) ──
        tax_rate = self.TAX_RATES.get(self.market, {}).get(side, 0)
        tax = notional * tax_rate

        # ── 3. 호가 스프레드 (왕복 0.5 × spread → 편도는 0.25 × spread) ──
        # 변동성이 높은 종목일수록 스프레드 큼
        vol = volatility if volatility else 0.20
        spread_pct = self.BASE_SPREAD_PCT * (1 + vol)  # vol 0.2 → spread 0.06%
        spread_cost = notional * spread_pct * 0.5  # 편도이므로 절반

        # ── 4. 시장 충격 (Almgren Square-Root Law) ──
        # impact_pct = Y × σ × sqrt(Q / ADV)
        slippage = 0.0
        permanent_impact = 0.0
        if avg_volume and avg_volume > 0:
            participation_rate = quantity / avg_volume  # ADV 대비 비중
            # 일시적 충격: 거래 시점에만 발생
            temporary_impact_pct = (
                self.IMPACT_COEFFICIENT * vol * math.sqrt(participation_rate)
            )
            slippage = notional * temporary_impact_pct

            # 영구적 충격: 대량 거래(>1% ADV)에서만 의미 있음
            if participation_rate > 0.01:
                permanent_impact = (
                    notional * 0.5 * self.IMPACT_COEFFICIENT * vol * participation_rate
                )

        total = commission + tax + spread_cost + slippage + permanent_impact
        total_pct = total / notional * 100 if notional > 0 else 0

        detail = (
            f"수수료 {commission:,.0f} + "
            f"세금 {tax:,.0f} + "
            f"스프레드 {spread_cost:,.0f} + "
            f"슬리피지 {slippage:,.0f} + "
            f"충격 {permanent_impact:,.0f}"
        )

        return CostBreakdown(
            commission=commission + tax,  # 표시 단순화
            spread_cost=spread_cost,
            slippage=slippage,
            permanent_impact=permanent_impact,
            total=total,
            total_pct=total_pct,
            detail=detail,
        )

    def round_trip_cost_pct(
        self,
        avg_volume: Optional[float] = None,
        volatility: Optional[float] = None,
    ) -> float:
        """
        왕복 거래비용 추정 (% — 매수+매도 모두)

        백테스트 / 전략 비교에서 빠른 추정용.
        예: 1% 왕복 비용 → 매매 신호의 알파가 1% 이상이어야 수익

        Returns:
            왕복 비용 비율 (예: 0.012 = 1.2%)
        """
        # 가상 100주 × 100,000원 거래 (대형주 평균)
        notional = 100 * 100_000
        buy_cost = self.estimate(
            "TEST", "BUY", 100, 100_000,
            avg_volume=avg_volume, volatility=volatility,
        )
        sell_cost = self.estimate(
            "TEST", "SELL", 100, 100_000,
            avg_volume=avg_volume, volatility=volatility,
        )
        return (buy_cost.total + sell_cost.total) / notional

    def is_trade_profitable(
        self,
        expected_return_pct: float,
        avg_volume: Optional[float] = None,
        volatility: Optional[float] = None,
    ) -> bool:
        """
        예상 수익률이 거래비용을 상회하는지 빠르게 체크

        매매 직전 호출하여 경계선 신호(예상 수익 < 비용)는 매매 차단 권장.

        Parameters:
            expected_return_pct: 예상 수익률 (예: 0.015 = 1.5%)

        Returns:
            True이면 거래 가치 있음, False면 비용 초과
        """
        round_trip = self.round_trip_cost_pct(avg_volume, volatility)
        # 안전 마진 1.5배 (실제 비용은 추정치보다 높을 수 있음)
        return expected_return_pct > round_trip * 1.5
