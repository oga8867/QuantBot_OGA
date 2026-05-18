"""
=============================================================================
executor/dual_executor.py - 듀얼 마켓 주문 실행기 (KR + US 동시 운용)
=============================================================================

하나의 봇이 한국 시장(KIS)과 미국 시장(Alpaca)을 동시에 운용할 때 사용합니다.
종목 심볼을 기반으로 자동 라우팅:
  - .KS / .KQ 종목 → KIS 실행기
  - 영문 티커       → Alpaca 실행기

구조도:
    QuantBot
      └── DualExecutor
            ├── KISExecutor      (한국 주식)
            └── AlpacaExecutor   (미국 주식)

각 실행기의 포지션/계좌 정보를 하나로 합산하여 반환합니다.
통화는 KRW 기준으로 환산하여 총 자산/손익을 계산합니다.

사용법:
    executor = DualExecutor(paper=True)
    executor.connect()  # 양쪽 모두 연결

    # 종목에 따라 자동 라우팅
    executor.buy_market("005930.KS", 10)    # → KIS
    executor.buy_market("AAPL", 5)          # → Alpaca

    # 합산 포지션/계좌
    positions = executor.get_positions()     # KIS + Alpaca 포지션 합산
    account = executor.get_account()         # 총 자산 (KRW 환산)
=============================================================================
"""

import logging
from typing import List, Optional, Dict
from datetime import datetime

from .base import (
    BaseExecutor, Order, Position, AccountInfo,
    OrderSide, OrderType, OrderStatus
)
from .kis_executor import KISExecutor
from .alpaca_executor import AlpacaExecutor

logger = logging.getLogger(__name__)


def _is_kr_symbol(symbol: str) -> bool:
    """
    한국 종목 여부 판별

    판별 기준:
    - .KS (코스피) 또는 .KQ (코스닥) 접미사
    - 6자리 숫자 (한국 종목코드)
    """
    if symbol.endswith(".KS") or symbol.endswith(".KQ"):
        return True
    # 6자리 순수 숫자 코드 (예: "005930")
    code = symbol.split(".")[0]
    if code.isdigit() and len(code) == 6:
        return True
    return False


class DualExecutor(BaseExecutor):
    """
    듀얼 마켓 실행기 — KIS + Alpaca를 하나의 인터페이스로 통합

    주문 흐름:
    1. submit_order(order) 호출
    2. order.symbol로 한국/미국 판별
    3. 해당 시장의 실행기로 주문 전달
    4. 결과 반환

    계좌 조회:
    - get_positions(): 양쪽 포지션 합산
    - get_account(): 총 자산을 KRW로 환산하여 합산
    """

    def __init__(
        self,
        # KIS 설정
        kis_app_key: Optional[str] = None,
        kis_app_secret: Optional[str] = None,
        kis_account: Optional[str] = None,
        # Alpaca 설정
        alpaca_api_key: Optional[str] = None,
        alpaca_secret_key: Optional[str] = None,
        # 공통
        paper: bool = True,
        db=None,
    ):
        """
        Parameters:
            kis_app_key: 한투 APP KEY (None이면 환경변수)
            kis_app_secret: 한투 APP SECRET
            kis_account: 한투 계좌번호 ("50012345-01")
            alpaca_api_key: Alpaca API Key (None이면 환경변수)
            alpaca_secret_key: Alpaca Secret Key
            paper: True=모의매매, False=실거래
            db: DatabaseManager — KIS 체결 시 trades 테이블에 자동 기록
        """
        super().__init__(name="dual", paper=paper)
        self.db = db

        # ── 개별 실행기 생성 ──
        self.kr_executor = KISExecutor(
            app_key=kis_app_key,
            app_secret=kis_app_secret,
            account=kis_account,
            paper=paper,
            db=db,  # ★ KIS도 DB 기록 가능하게
        )
        self.us_executor = AlpacaExecutor(
            api_key=alpaca_api_key,
            secret_key=alpaca_secret_key,
            paper=paper
        )

        # 연결 상태 추적
        self.kr_connected = False
        self.us_connected = False

    def positions_query_succeeded(self) -> bool:
        """
        ★ Phase 10: 두 executor 모두 성공해야 True 반환
        한쪽이라도 실패하면 caller가 매수 보류 (이중 매수 방지)
        """
        kr_ok = self.kr_executor.positions_query_succeeded() if self.kr_connected else True
        us_ok = self.us_executor.positions_query_succeeded() if self.us_connected else True
        return kr_ok and us_ok

    def account_query_succeeded(self) -> bool:
        """두 executor 모두 성공해야 True"""
        kr_ok = self.kr_executor.account_query_succeeded() if self.kr_connected else True
        us_ok = self.us_executor.account_query_succeeded() if self.us_connected else True
        return kr_ok and us_ok

    def get_current_price(self, symbol: str):
        """
        ★ Phase 10: 종목 시장에 따라 적절한 executor에 라우팅
        run_bot._execute_buy의 5% 갭 검증이 dual에서도 작동
        """
        from utils.market import is_us_stock
        if is_us_stock(symbol):
            # Alpaca: get_latest_quote 등 (구현되어 있으면)
            if hasattr(self.us_executor, "get_current_price"):
                return self.us_executor.get_current_price(symbol)
            return None
        else:
            return self.kr_executor.get_current_price(symbol)

    def cancel_order(self, order_id: str, original_ord_dvsn: Optional[str] = None) -> bool:
        """★ Phase 10: original_ord_dvsn을 KIS로 전달 (지정가 취소 실패 방지)"""
        if self.kr_connected:
            try:
                if self.kr_executor.cancel_order(order_id, original_ord_dvsn=original_ord_dvsn):
                    return True
            except Exception as e:
                logger.warning(f"[Dual] KIS 취소 시도 실패: {e}")
        if self.us_connected:
            try:
                if self.us_executor.cancel_order(order_id):
                    return True
            except Exception as e:
                logger.warning(f"[Dual] Alpaca 취소 시도 실패: {e}")
        return False

    def connect(self) -> bool:
        """
        양쪽 브로커 연결 시도

        - 한쪽만 성공해도 True 반환 (해당 시장만 거래 가능)
        - 둘 다 실패하면 False 반환
        - 연결 실패한 쪽은 해당 시장 주문 시 거부됨
        """
        logger.info("[DualExecutor] 듀얼 마켓 연결 시작...")

        # ── KIS 연결 ──
        try:
            self.kr_connected = self.kr_executor.connect()
            if self.kr_connected:
                logger.info("[DualExecutor] ✅ KIS (한국 시장) 연결 성공")
            else:
                logger.warning(
                    "[DualExecutor] ⚠️ KIS 연결 실패 — 한국 주식 거래 불가. "
                    "API 키를 확인하세요 (설정 → 브로커 API 키)"
                )
        except Exception as e:
            logger.error(f"[DualExecutor] KIS 연결 오류: {e}")
            self.kr_connected = False

        # ── Alpaca 연결 ──
        try:
            self.us_connected = self.us_executor.connect()
            if self.us_connected:
                logger.info("[DualExecutor] ✅ Alpaca (미국 시장) 연결 성공")
            else:
                logger.warning(
                    "[DualExecutor] ⚠️ Alpaca 연결 실패 — 미국 주식 거래 불가. "
                    "API 키를 확인하세요 (설정 → 브로커 API 키)"
                )
        except Exception as e:
            logger.error(f"[DualExecutor] Alpaca 연결 오류: {e}")
            self.us_connected = False

        # 연결 요약
        if self.kr_connected and self.us_connected:
            mode = "모의투자" if self.paper else "실거래"
            logger.info(f"[DualExecutor] 듀얼 마켓 {mode} 모드 — KR ✅ + US ✅")
        elif self.kr_connected or self.us_connected:
            kr_s = "✅" if self.kr_connected else "❌"
            us_s = "✅" if self.us_connected else "❌"
            logger.warning(
                f"[DualExecutor] 부분 연결 — KR {kr_s} + US {us_s} "
                "(한쪽 시장만 거래 가능)"
            )
        else:
            logger.error("[DualExecutor] 양쪽 모두 연결 실패!")

        return self.kr_connected or self.us_connected

    def _route(self, symbol: str) -> Optional[BaseExecutor]:
        """
        종목 심볼 기반 실행기 라우팅

        Returns:
            해당 시장의 실행기, 연결 안 되어 있으면 None
        """
        if _is_kr_symbol(symbol):
            if not self.kr_connected:
                logger.error(
                    f"[DualExecutor] {symbol}: KIS 미연결 상태 — 한국 주문 불가"
                )
                return None
            return self.kr_executor
        else:
            if not self.us_connected:
                logger.error(
                    f"[DualExecutor] {symbol}: Alpaca 미연결 상태 — 미국 주문 불가"
                )
                return None
            return self.us_executor

    def submit_order(self, order: Order) -> Order:
        """
        주문 제출 — 종목에 따라 자동 라우팅

        한국 종목 (.KS/.KQ) → KIS
        미국 종목           → Alpaca
        """
        executor = self._route(order.symbol)
        if executor is None:
            order.status = OrderStatus.REJECTED
            logger.error(
                f"[DualExecutor] 주문 거부: {order.symbol} — "
                f"해당 시장 실행기 미연결"
            )
            return order

        market = "KR" if _is_kr_symbol(order.symbol) else "US"
        logger.info(
            f"[DualExecutor] {order.symbol} → {market} 실행기로 라우팅 "
            f"({order.side.value} {order.quantity}주)"
        )

        result = executor.submit_order(order)
        self.orders.append(result)
        return result

    def get_order_status(self, order_id: str) -> OrderStatus:
        """주문 상태 조회 — 양쪽에서 시도"""
        if self.kr_connected:
            try:
                status = self.kr_executor.get_order_status(order_id)
                if status != OrderStatus.REJECTED:
                    return status
            except Exception:
                pass

        if self.us_connected:
            try:
                return self.us_executor.get_order_status(order_id)
            except Exception:
                pass

        return OrderStatus.REJECTED

    def get_positions(self) -> List[Position]:
        """
        전체 보유 포지션 조회 (KR + US 합산)

        양쪽 시장의 포지션을 하나의 리스트로 합쳐서 반환합니다.
        """
        positions = []

        if self.kr_connected:
            try:
                kr_positions = self.kr_executor.get_positions()
                positions.extend(kr_positions)
            except Exception as e:
                logger.warning(f"[DualExecutor] KIS 포지션 조회 실패: {e}")

        if self.us_connected:
            try:
                us_positions = self.us_executor.get_positions()
                positions.extend(us_positions)
            except Exception as e:
                logger.warning(f"[DualExecutor] Alpaca 포지션 조회 실패: {e}")

        return positions

    def get_account(self) -> AccountInfo:
        """
        합산 계좌 정보 조회

        미국 계좌는 USD → KRW 환산하여 합산합니다.
        한쪽만 연결된 경우 해당 시장 계좌만 반환합니다.
        """
        # 환율 가져오기
        # ★ Phase 7: 환율 캐시 (마지막 성공값) 사용 — 1350 하드코딩보다 안전
        try:
            from utils.market import get_exchange_rate
            rate = get_exchange_rate()
            if rate and rate > 0:
                usd_to_krw = float(rate)
                # 환율 성공 시 마지막 값 캐시
                self._last_known_fx = usd_to_krw
            else:
                # 캐시된 마지막 값 사용 → 첫 호출 실패 시 fallback
                usd_to_krw = getattr(self, "_last_known_fx", None) or 1400.0
                logger.warning(
                    f"[DualExecutor] 환율 조회 실패 → 마지막 알려진 값 사용: ₩{usd_to_krw:.1f}"
                )
        except Exception as e:
            usd_to_krw = getattr(self, "_last_known_fx", None) or 1400.0
            logger.warning(
                f"[DualExecutor] 환율 조회 예외 ({e}) → fallback ₩{usd_to_krw:.1f}"
            )

        total = AccountInfo(currency="KRW")

        # ── KIS 계좌 ──
        if self.kr_connected:
            try:
                kr_account = self.kr_executor.get_account()
                total.total_equity += kr_account.total_equity
                total.cash += kr_account.cash
                total.buying_power += kr_account.buying_power
                total.positions_value += kr_account.positions_value
                total.daily_pnl += kr_account.daily_pnl
            except Exception as e:
                logger.warning(f"[DualExecutor] KIS 계좌 조회 실패: {e}")

        # ── Alpaca 계좌 (USD → KRW 환산) ──
        if self.us_connected:
            try:
                us_account = self.us_executor.get_account()
                total.total_equity += us_account.total_equity * usd_to_krw
                total.cash += us_account.cash * usd_to_krw
                total.buying_power += us_account.buying_power * usd_to_krw
                total.positions_value += us_account.positions_value * usd_to_krw
                total.daily_pnl += us_account.daily_pnl * usd_to_krw
            except Exception as e:
                logger.warning(f"[DualExecutor] Alpaca 계좌 조회 실패: {e}")

        return total

    def get_market_status(self) -> Dict[str, dict]:
        """
        각 시장의 연결 상태 및 계좌 요약 (대시보드용)

        Returns:
            {
                "kr": {"connected": True, "equity": 5000000, "positions": 3},
                "us": {"connected": True, "equity": 3500000, "positions": 2},
            }
        """
        result = {
            "kr": {"connected": self.kr_connected, "equity": 0, "positions": 0},
            "us": {"connected": self.us_connected, "equity": 0, "positions": 0},
        }

        if self.kr_connected:
            try:
                kr_acc = self.kr_executor.get_account()
                kr_pos = self.kr_executor.get_positions()
                result["kr"]["equity"] = kr_acc.total_equity
                result["kr"]["positions"] = len(kr_pos)
            except Exception:
                pass

        if self.us_connected:
            try:
                us_acc = self.us_executor.get_account()
                us_pos = self.us_executor.get_positions()
                result["us"]["equity"] = us_acc.total_equity
                result["us"]["positions"] = len(us_pos)
            except Exception:
                pass

        return result

    def disconnect(self):
        """양쪽 연결 해제 (정리용)"""
        self.kr_connected = False
        self.us_connected = False
        logger.info("[DualExecutor] 듀얼 마켓 연결 해제")
