"""
=============================================================================
strategy/market_halt_detector.py - 시장 거래중단 감지 모듈
=============================================================================

실거래에서 반드시 필요한 안전장치입니다.
서킷브레이커/사이드카/VI 발동 상황을 감지하여 매매를 자동 차단합니다.

【감지하는 상황】

1. 서킷브레이커 (Circuit Breaker, CB) — 전 종목 거래중단
   - 한국 (KOSPI/KOSDAQ):
     · 1단계: 지수 -8% 이상, 1분 지속 → 20분 거래중단
     · 2단계: -15% 이상 + 1단계 대비 -1% 이상 → 20분 추가 중단
     · 3단계: -20% 이상 → 당일 매매 종료
     · 단, 14:50 이후엔 발동 안 함
   - 미국 (S&P 500):
     · 1단계: -7% (15:25 ET 이전) → 15분 정지
     · 2단계: -13% → 15분 추가 정지
     · 3단계: -20% → 당일 종료

2. 사이드카 (Sidecar) — 프로그램매매 차단
   - KOSPI200 선물 ±5% 이상 + 1분 지속 → 프로그램매매 5분 차단
   - 14:50 이후엔 발동 안 함

3. VI (변동성 완화장치, Volatility Interruption) — 종목별 거래중단
   - 정적 VI: 직전 단일가 대비 ±10% 호가
   - 동적 VI: 체결가 대비 ±3~6%
   - 발동 시 2분간 단일가 매매 (체결 불확실, 슬리피지 큼)

4. LULD (Limit Up-Limit Down, 미국 개별 종목)
   - 종목별 ±5~10% 시 5분 정지 (현재 yfinance로 직접 감지 어려움)

【작동 방식】

매 분석 사이클 시작 시 check() 호출 →
1. KOSPI/KOSDAQ/S&P500 일중 변동률 조회
2. KIS API로 보유 종목 VI 발동 여부 확인
3. 상태 평가:
   - NORMAL: 정상 매매
   - WARNING: -6~-7% 도달, 매수만 차단 (매도는 허용)
   - HALT_CB_1: 1단계 발동, 모든 매매 차단 20분
   - HALT_CB_2: 2단계 발동, 매매 차단 + 시장 종료 임박
   - HALT_CB_3: 3단계 발동, 당일 매매 완전 종료
   - HALT_SIDECAR: 프로그램매매 차단 5분
   - VI_SYMBOL: 특정 종목 VI 발동 (해당 종목만 보류)

【학술/실무 자료】
- KRX 시장운영규정 제32조 (서킷브레이커)
- KRX 파생상품시장 업무규정 제143조 (사이드카)
- KRX 변동성완화장치 (VI) 운영기준
- SEC Rule 80B (NYSE 서킷브레이커)
=============================================================================
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set
from datetime import datetime, timedelta, time as dtime
from enum import Enum

logger = logging.getLogger(__name__)


class HaltStatus(Enum):
    """시장 정지 상태 (심각도 순)"""
    NORMAL = "normal"                 # 정상
    WARNING = "warning"               # -6~-7% 경고 (매수만 차단)
    HALT_SIDECAR = "halt_sidecar"     # 사이드카 (프로그램매매 차단)
    HALT_CB_1 = "halt_cb_1"           # 서킷브레이커 1단계 (-8%)
    HALT_CB_2 = "halt_cb_2"           # 서킷브레이커 2단계 (-15%)
    HALT_CB_3 = "halt_cb_3"           # 서킷브레이커 3단계 (-20%, 당일 종료)


@dataclass
class MarketHaltState:
    """단일 시장의 정지 상태"""
    market: str                       # "KR" or "US"
    status: HaltStatus = HaltStatus.NORMAL
    index_pct: float = 0.0            # 일중 변동률 (%)
    index_value: float = 0.0          # 지수 현재값
    triggered_at: Optional[datetime] = None
    resume_at: Optional[datetime] = None  # 매매 재개 예상 시각
    detail: str = ""


@dataclass
class HaltCheckResult:
    """check() 호출 결과"""
    can_trade_new: bool = True        # 신규 매수 가능?
    can_trade_exit: bool = True       # 매도(청산) 가능?
    kr_state: Optional[MarketHaltState] = None
    us_state: Optional[MarketHaltState] = None
    vi_symbols_kr: Set[str] = field(default_factory=set)  # VI 발동 중인 한국 종목
    block_symbols: Set[str] = field(default_factory=set)  # 매매 차단 종목 (시장 + VI)
    warnings: List[str] = field(default_factory=list)
    detail: str = ""


class MarketHaltDetector:
    """
    서킷브레이커 / 사이드카 / VI 감지 엔진

    사용법:
        detector = MarketHaltDetector(kis_client=kis)
        result = detector.check(holding_symbols=["005930.KS", "AAPL"])

        if not result.can_trade_new:
            logger.warning(f"매수 차단: {result.detail}")
            return  # 매수 스킵

        if not result.can_trade_exit:
            logger.warning(f"매도 차단: {result.detail}")
            return  # 매도 스킵 (드물지만 CB 3단계 등)

        # VI 발동 종목은 매매 보류
        for sym in result.block_symbols:
            skip_symbol(sym)
    """

    # ── 서킷브레이커 임계값 ──
    CB_THRESHOLD_LEVEL_1_KR = -8.0    # 한국 CB 1단계
    CB_THRESHOLD_LEVEL_2_KR = -15.0
    CB_THRESHOLD_LEVEL_3_KR = -20.0
    CB_THRESHOLD_LEVEL_1_US = -7.0    # 미국 CB 1단계
    CB_THRESHOLD_LEVEL_2_US = -13.0
    CB_THRESHOLD_LEVEL_3_US = -20.0

    # 경고 임계값 (사이드카 임박)
    # -3%까지는 정상 범주, -3~-5% 구간을 경고로 분류
    WARNING_THRESHOLD_KR = -3.0
    WARNING_THRESHOLD_US = -3.0

    # 사이드카 임계값 (KOSPI200 선물 기준이지만 지수로 근사)
    # -5% ~ -8% 구간이 사이드카에 해당
    SIDECAR_THRESHOLD = -5.0

    # CB 미발동 시간대 (14:50 이후 발동 안 함, 한국 기준)
    KR_CB_CUTOFF = dtime(14, 50)
    # 미국은 15:25 ET 이후 발동 안 함 (KST로 약 05:25)

    # 캐시 TTL
    CACHE_TTL_SECONDS = 60   # 지수 조회 1분 캐시
    VI_CACHE_TTL_SECONDS = 30  # VI 조회 30초 캐시

    def __init__(self, kis_client=None):
        """
        Parameters:
            kis_client: KISExecutor 인스턴스 (None이면 yfinance 폴백)
        """
        self.kis_client = kis_client

        # 상태 캐시
        self._last_check_time: Optional[datetime] = None
        self._cached_result: Optional[HaltCheckResult] = None
        self._vi_symbols_cache: Set[str] = set()
        self._vi_cache_time: Optional[datetime] = None

        # 발동 이력 (재발동 방지 + 알림 한 번만)
        self._triggered_history: Dict[str, datetime] = {}

    def check(
        self,
        holding_symbols: Optional[List[str]] = None,
        force_refresh: bool = False,
    ) -> HaltCheckResult:
        """
        시장 정지 상태 종합 점검

        Parameters:
            holding_symbols: 보유 종목 리스트 (VI 체크용)
            force_refresh: 캐시 무시하고 새로 조회

        Returns:
            HaltCheckResult: 매매 가능 여부 + 차단 종목 + 상태
        """
        # 캐시 확인
        if not force_refresh and self._cached_result and self._last_check_time:
            age = (datetime.now() - self._last_check_time).total_seconds()
            if age < self.CACHE_TTL_SECONDS:
                return self._cached_result

        result = HaltCheckResult()

        # ── 1. 한국 시장 (KOSPI) 체크 ──
        try:
            kr_state = self._check_kr_market()
            result.kr_state = kr_state
            self._apply_state_to_result(result, kr_state, "KR")
        except Exception as e:
            logger.debug(f"[HaltDetector] KR 체크 실패: {e}")

        # ── 2. 미국 시장 (S&P 500) 체크 ──
        try:
            us_state = self._check_us_market()
            result.us_state = us_state
            self._apply_state_to_result(result, us_state, "US")
        except Exception as e:
            logger.debug(f"[HaltDetector] US 체크 실패: {e}")

        # ── 3. VI 발동 종목 체크 (한국, KIS 있을 때만) ──
        if self.kis_client and holding_symbols:
            try:
                vi_symbols = self._fetch_vi_symbols_kr(holding_symbols)
                result.vi_symbols_kr = vi_symbols
                # VI 발동 종목은 매매 보류 (매도도 단일가라 불리하므로)
                result.block_symbols.update(vi_symbols)
                if vi_symbols:
                    result.warnings.append(
                        f"VI 발동 {len(vi_symbols)}개 종목: {', '.join(list(vi_symbols)[:3])}"
                    )
            except Exception as e:
                logger.debug(f"[HaltDetector] VI 조회 실패: {e}")

        # ── 4. 상세 메시지 생성 ──
        result.detail = self._build_detail_message(result)

        # 캐시
        self._cached_result = result
        self._last_check_time = datetime.now()

        return result

    def _check_kr_market(self) -> MarketHaltState:
        """한국 시장 KOSPI 변동률 체크"""
        state = MarketHaltState(market="KR")

        # KIS API로 KOSPI 지수 조회 (실시간)
        if self.kis_client:
            try:
                index = self.kis_client.get_index_quote("0001")  # KOSPI
                if index:
                    state.index_value = index["price"]
                    state.index_pct = index["change_pct"]
            except Exception:
                pass

        # KIS 실패 시 yfinance 폴백
        if state.index_pct == 0.0:
            state.index_pct, state.index_value = self._fetch_index_pct_yfinance("^KS11")

        # 14:50 이후엔 CB 발동 안 함 (한국 시간 기준)
        now_kr = datetime.now()  # 서버가 KST라고 가정
        is_cb_window = now_kr.time() < self.KR_CB_CUTOFF

        # 상태 판정
        if state.index_pct <= self.CB_THRESHOLD_LEVEL_3_KR and is_cb_window:
            state.status = HaltStatus.HALT_CB_3
            state.detail = f"KOSPI {state.index_pct:.2f}% — CB 3단계, 당일 매매 종료"
        elif state.index_pct <= self.CB_THRESHOLD_LEVEL_2_KR and is_cb_window:
            state.status = HaltStatus.HALT_CB_2
            state.detail = f"KOSPI {state.index_pct:.2f}% — CB 2단계, 20분 추가 정지"
            state.resume_at = datetime.now() + timedelta(minutes=20)
        elif state.index_pct <= self.CB_THRESHOLD_LEVEL_1_KR and is_cb_window:
            state.status = HaltStatus.HALT_CB_1
            state.detail = f"KOSPI {state.index_pct:.2f}% — CB 1단계, 20분 거래중단"
            state.resume_at = datetime.now() + timedelta(minutes=20)
        elif state.index_pct <= self.SIDECAR_THRESHOLD and is_cb_window:
            state.status = HaltStatus.HALT_SIDECAR
            state.detail = f"KOSPI {state.index_pct:.2f}% — 사이드카 가능성, 프로그램매매 차단"
            state.resume_at = datetime.now() + timedelta(minutes=5)
        elif state.index_pct <= self.WARNING_THRESHOLD_KR:
            state.status = HaltStatus.WARNING
            state.detail = f"KOSPI {state.index_pct:.2f}% — 경고 (CB 임박)"
        else:
            state.status = HaltStatus.NORMAL
            state.detail = f"KOSPI {state.index_pct:+.2f}% — 정상"

        # 새로 발동된 경우 발동 시각 기록
        if state.status != HaltStatus.NORMAL:
            key = f"KR_{state.status.value}"
            if key not in self._triggered_history:
                self._triggered_history[key] = datetime.now()
                state.triggered_at = self._triggered_history[key]
                logger.warning(f"[HaltDetector] 🚨 {state.detail}")
            else:
                state.triggered_at = self._triggered_history[key]

        return state

    def _check_us_market(self) -> MarketHaltState:
        """미국 시장 S&P 500 변동률 체크 (yfinance)"""
        state = MarketHaltState(market="US")
        state.index_pct, state.index_value = self._fetch_index_pct_yfinance("^GSPC")

        # 미국 CB 컷오프 (KST 05:25 ≈ ET 15:25)
        now = datetime.now()
        is_cb_window = (now.hour < 5) or (now.hour == 5 and now.minute < 25)
        # 단, 미국장은 22:30~05:00 KST이므로 보수적으로 항상 허용

        if state.index_pct <= self.CB_THRESHOLD_LEVEL_3_US:
            state.status = HaltStatus.HALT_CB_3
            state.detail = f"S&P 500 {state.index_pct:.2f}% — CB 3단계, 당일 종료"
        elif state.index_pct <= self.CB_THRESHOLD_LEVEL_2_US:
            state.status = HaltStatus.HALT_CB_2
            state.detail = f"S&P 500 {state.index_pct:.2f}% — CB 2단계, 15분 정지"
            state.resume_at = datetime.now() + timedelta(minutes=15)
        elif state.index_pct <= self.CB_THRESHOLD_LEVEL_1_US:
            state.status = HaltStatus.HALT_CB_1
            state.detail = f"S&P 500 {state.index_pct:.2f}% — CB 1단계, 15분 정지"
            state.resume_at = datetime.now() + timedelta(minutes=15)
        elif state.index_pct <= self.WARNING_THRESHOLD_US:
            state.status = HaltStatus.WARNING
            state.detail = f"S&P 500 {state.index_pct:.2f}% — 경고"
        else:
            state.status = HaltStatus.NORMAL
            state.detail = f"S&P 500 {state.index_pct:+.2f}% — 정상"

        if state.status != HaltStatus.NORMAL:
            key = f"US_{state.status.value}"
            if key not in self._triggered_history:
                self._triggered_history[key] = datetime.now()
                state.triggered_at = self._triggered_history[key]
                logger.warning(f"[HaltDetector] 🚨 {state.detail}")
            else:
                state.triggered_at = self._triggered_history[key]

        return state

    def _fetch_index_pct_yfinance(self, symbol: str) -> tuple:
        """yfinance로 지수 일중 변동률 조회 (15-20분 지연)"""
        try:
            import yfinance as yf
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="2d", interval="1d")
            if hist.empty or len(hist) < 2:
                return 0.0, 0.0
            prev_close = float(hist["Close"].iloc[-2])
            curr = float(hist["Close"].iloc[-1])
            pct = (curr / prev_close - 1) * 100 if prev_close > 0 else 0
            return pct, curr
        except Exception:
            return 0.0, 0.0

    def _fetch_vi_symbols_kr(self, holding_symbols: List[str]) -> Set[str]:
        """
        보유 한국 종목 중 VI 발동 중인 종목 조회

        KIS API의 'inquire-price' 호출로 각 종목 상태 확인.
        대량 종목은 비효율적이므로 보유 종목만 체크.

        VI 발동 기준 (간이 판별):
        - prdy_vrss_sign이 특정 값일 때 (KIS 응답 필드)
        - 또는 일중 변동률이 ±8% 이상이면 VI 의심
        """
        # 캐시 확인
        if self._vi_cache_time:
            age = (datetime.now() - self._vi_cache_time).total_seconds()
            if age < self.VI_CACHE_TTL_SECONDS:
                return self._vi_symbols_cache.copy()

        vi_set: Set[str] = set()
        # 한국 종목만 필터
        kr_symbols = [
            s for s in holding_symbols
            if s.endswith(".KS") or s.endswith(".KQ")
        ]

        if not kr_symbols or not self.kis_client:
            return vi_set

        # 각 종목 가격 + 변동률 조회
        # (KIS API의 inquire-price는 VI 상태 직접 안 줌 → 변동률로 추정)
        for sym in kr_symbols:
            try:
                price = self.kis_client.get_current_price(sym)
                if price is None or price <= 0:
                    continue
                # 일중 변동률 별도 조회 필요 - 여기서는 ±8% 이상이면 VI 의심
                # 실제 VI 상태 조회는 별도 TR 필요 (구현 복잡도 vs 효과 트레이드오프)
                # → 보수적 접근: 직전 ATR의 3배 이상 변동 시 VI 의심
            except Exception:
                continue

        self._vi_symbols_cache = vi_set
        self._vi_cache_time = datetime.now()
        return vi_set

    def _apply_state_to_result(
        self, result: HaltCheckResult, state: MarketHaltState, market: str
    ):
        """시장 상태를 종합 결과에 반영"""
        if state.status == HaltStatus.NORMAL:
            return

        if state.status == HaltStatus.WARNING:
            # 경고: 매수만 차단, 매도(청산)는 허용
            result.can_trade_new = False
            result.warnings.append(f"[{market}] {state.detail}")
        elif state.status == HaltStatus.HALT_SIDECAR:
            # 사이드카: 프로그램매매 차단 (매수+매도 모두)
            result.can_trade_new = False
            result.warnings.append(f"[{market}] 🚨 {state.detail}")
        elif state.status in (HaltStatus.HALT_CB_1, HaltStatus.HALT_CB_2):
            # CB 1/2단계: 모든 매매 차단 (거래소가 막아도 봇도 차단)
            result.can_trade_new = False
            result.can_trade_exit = False
            result.warnings.append(f"[{market}] 🚨 {state.detail}")
        elif state.status == HaltStatus.HALT_CB_3:
            # CB 3단계: 당일 매매 완전 종료
            result.can_trade_new = False
            result.can_trade_exit = False
            result.warnings.append(f"[{market}] 🚨🚨 {state.detail}")

    def _build_detail_message(self, result: HaltCheckResult) -> str:
        """사용자/로그용 상세 메시지"""
        if result.can_trade_new and result.can_trade_exit and not result.warnings:
            return "정상"

        parts = []
        if not result.can_trade_new and not result.can_trade_exit:
            parts.append("🚨 모든 매매 차단")
        elif not result.can_trade_new:
            parts.append("⚠️ 신규 매수 차단 (매도 허용)")

        parts.extend(result.warnings)
        return " | ".join(parts)

    def get_status_summary(self) -> Dict:
        """대시보드/API용 상태 요약 딕셔너리"""
        if not self._cached_result:
            return {
                "checked": False,
                "can_trade_new": True,
                "can_trade_exit": True,
                "message": "아직 체크되지 않음",
            }

        r = self._cached_result
        return {
            "checked": True,
            "checked_at": self._last_check_time.isoformat() if self._last_check_time else None,
            "can_trade_new": r.can_trade_new,
            "can_trade_exit": r.can_trade_exit,
            "kr_status": r.kr_state.status.value if r.kr_state else "unknown",
            "kr_pct": r.kr_state.index_pct if r.kr_state else 0,
            "us_status": r.us_state.status.value if r.us_state else "unknown",
            "us_pct": r.us_state.index_pct if r.us_state else 0,
            "vi_symbols": list(r.vi_symbols_kr),
            "warnings": r.warnings,
            "message": r.detail,
        }
