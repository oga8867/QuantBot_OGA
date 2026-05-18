"""
=============================================================================
executor/kis_executor.py - 한국투자증권 주문 실행기
=============================================================================

한국투자증권 Open API를 통해 한국 주식(KOSPI/KOSDAQ)을 자동 매매합니다.

한국투자증권 Open API란?
- 국내 유일한 REST API 방식 증권사 API
- Windows/Mac/Linux 모두 사용 가능 (키움은 Windows만)
- 2022년 4월 서비스 시작, 활발한 커뮤니티

시작 방법:
1. 한국투자증권 계좌 개설
2. https://apiportal.koreainvestment.com 에서 API 신청
3. APP KEY, APP SECRET 발급
4. .env에 KIS_APP_KEY, KIS_APP_SECRET, KIS_ACCOUNT 저장
5. 모의투자 먼저 신청하여 테스트

API 특징:
- REST API (HTTP 요청/응답)
- OAuth2 토큰 인증 (토큰 유효기간 24시간)
- 실시간 데이터: WebSocket
- 주문: POST 요청
=============================================================================
"""

import os
import json
import time
import hashlib
import logging
import threading
from pathlib import Path
from typing import List, Optional, Dict
from datetime import datetime, timedelta

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

from .base import (
    BaseExecutor, Order, Position, AccountInfo,
    OrderSide, OrderType, OrderStatus
)

logger = logging.getLogger(__name__)


class KISExecutor(BaseExecutor):
    """
    한국투자증권 REST API 기반 주문 실행기

    사용법:
        executor = KISExecutor(paper=True)
        executor.connect()  # 토큰 발급

        # 시장가 매수 (삼성전자 10주)
        order = executor.buy_market("005930", 10)

        # 포지션 확인
        positions = executor.get_positions()
    """

    # API 엔드포인트
    BASE_URL_REAL = "https://openapi.koreainvestment.com:9443"
    BASE_URL_PAPER = "https://openapivts.koreainvestment.com:29443"

    def __init__(
        self,
        app_key: Optional[str] = None,
        app_secret: Optional[str] = None,
        account: Optional[str] = None,
        paper: bool = True,
        db=None,
        fill_poll_seconds: float = 5.0,
    ):
        """
        Parameters:
            app_key: 한투 APP KEY
            app_secret: 한투 APP SECRET
            account: 계좌번호 (예: "50012345-01")
            paper: True=모의투자, False=실거래
            db: DatabaseManager 인스턴스 (체결 시 자동으로 trades 테이블에 기록)
            fill_poll_seconds: submit 후 체결 폴링 시간 (시장가 평균 1-3초, 한도 5초)
        """
        super().__init__(name="kis", paper=paper)

        self.app_key = app_key or os.environ.get("KIS_APP_KEY")
        self.app_secret = app_secret or os.environ.get("KIS_APP_SECRET")
        self.account = account or os.environ.get("KIS_ACCOUNT", "")
        self.base_url = self.BASE_URL_PAPER if paper else self.BASE_URL_REAL

        # 인증 토큰 (connect()에서 발급)
        self.access_token: Optional[str] = None
        self.token_expires: Optional[datetime] = None

        # ── 계좌번호 분리 (CANO + ACNT_PRDT_CD) ──
        # 허용 입력 형식:
        #   1) "44501321-01"  → CANO="44501321", PRDT="01"  (권장 표준)
        #   2) "4450132101"   → CANO="44501321", PRDT="01"  (하이픈 누락, 자동 보정)
        #   3) "44501321"     → CANO="44501321", PRDT="01"  (PRDT 누락, 기본 "01")
        # 10자리 통합 입력은 앞 8자리=CANO, 뒤 2자리=PRDT로 자동 분리합니다.
        # 이는 사용자가 한국투자증권 앱에서 보이는 계좌번호를 그대로 붙여넣어도
        # 동작하도록 하기 위함이지만, 길이가 어긋나면 KIS API가 EGW00121 오류를
        # 반환하므로 로그로 명확히 경고합니다.
        raw_account = (self.account or "").strip()
        # 숫자/하이픈만 남기기 (공백·특수문자 제거)
        cleaned = "".join(c for c in raw_account if c.isdigit() or c == "-")

        if "-" in cleaned:
            parts = cleaned.split("-", 1)
            self.cano = parts[0]
            self.acnt_prdt_cd = parts[1] if len(parts) > 1 and parts[1] else "01"
        elif len(cleaned) == 10:
            # 하이픈 없이 10자리 → 앞 8자리 / 뒤 2자리로 분리
            self.cano = cleaned[:8]
            self.acnt_prdt_cd = cleaned[8:]
            logger.info(
                f"[KIS] 계좌번호 자동 분리: '{cleaned}' → "
                f"CANO='{self.cano}', PRDT='{self.acnt_prdt_cd}'"
            )
        elif len(cleaned) == 8:
            # CANO만 입력 → PRDT는 기본값 01
            self.cano = cleaned
            self.acnt_prdt_cd = "01"
        else:
            # 비표준 길이 → 그대로 사용하되 경고
            self.cano = cleaned
            self.acnt_prdt_cd = "01"

        # 검증: CANO가 8자리 숫자가 아니면 경고
        if not (self.cano.isdigit() and len(self.cano) == 8):
            logger.warning(
                f"[KIS] 계좌번호 형식 오류: CANO='{self.cano}' (8자리 숫자여야 함). "
                f"올바른 형식: '12345678-01' 또는 '1234567801'. "
                f"잘못된 형식이면 잔고 조회가 실패할 수 있습니다."
            )

        # ── ★ Phase 7: requests.Session으로 TCP/TLS 재사용 ──
        # 매번 새 TCP+TLS handshake (~50-200ms)를 피해 연결 풀링.
        # 매수/매도/잔고가 빈번한 거래 환경에서 latency를 줄입니다.
        self.session = requests.Session() if REQUESTS_AVAILABLE else None

        # ── ★ Phase 6: 주문 idempotency 안전장치 ──
        # 동일 (symbol, side, qty)에 대한 중복 주문을 N초 내 차단
        # KIS가 timeout 후에도 실제로는 주문이 들어갈 수 있으므로,
        # 재시도 시 _query_today_orders_for_match로 실제 체결 여부를 먼저 확인
        self._recent_orders: List[Dict] = []  # 최근 N초 내 제출한 주문 추적
        self._order_dedupe_window_sec = 30  # 30초 내 동일 주문 차단
        self._inflight_lock = threading.Lock()

        # ── ★ API 호출 상태 추적 (CRITICAL): "[] = 빈 포지션 vs API 실패" 구분 ──
        # 이전 버그: get_positions()가 API 실패 시 []를 반환 → 호출자가 "보유 없음"으로 오인 → 이중 매수
        # 수정: 각 호출의 성공/실패를 기록. 봇이 이중 매수 위험 판단에 사용
        self._last_positions_call_ok: bool = True   # 최근 get_positions 성공 여부
        self._last_account_call_ok: bool = True     # 최근 get_account 성공 여부

        # ── ★ 체결 확정 폴링 + DB 기록 ──
        # KIS submit_order는 SUBMITTED만 반환 (FILLED 아님). 시장가는 보통 1-3초에 체결되므로
        # 짧게 폴링해서 FILLED로 갱신해야 caller가 정상 분기를 탈 수 있음.
        # 폴링 안 하면: ExitManager 등록 안 됨 + SafetyGuard PnL 누락 + DB 거래 미기록.
        self.db = db
        self.fill_poll_seconds = fill_poll_seconds
        # 체결 시점 trade_history 메모리 (paper_executor 호환)
        # 호출자가 hasattr(executor, 'trade_history')로 체크함
        self.trade_history: List[Dict] = []

    @staticmethod
    def _strip_suffix(symbol: str) -> str:
        """
        종목 코드에서 .KS/.KQ 접미사 제거 (KIS API용)

        KIS API는 순수 6자리 종목코드만 허용:
        - "005930.KS" -> "005930"
        - "035720.KS" -> "035720"
        - "005930"    -> "005930" (이미 순수 코드)
        """
        if symbol.endswith(".KS") or symbol.endswith(".KQ"):
            return symbol[:-3]
        return symbol

    @staticmethod
    def get_korean_market_session(now: Optional[datetime] = None) -> str:
        """
        현재 한국 시장 세션 반환 (KST 기준, 서버 TZ와 무관)

        Returns:
            "pre_close"         : 08:30~08:40  장전 시간외 종가 (전일 종가 매매)
            "pre_auction"       : 08:30~09:00  장전 동시호가 (시가 결정)
            "regular"           : 09:00~15:20  정규장
            "close_auction"     : 15:20~15:30  마감 동시호가 (종가 결정)
            "after_close"       : 15:40~16:00  장후 시간외 종가
            "after_single"      : 16:00~18:00  시간외 단일가
            "closed"            : 그 외 (휴장)

        주의: 공휴일은 별도 처리 안 함 (KIS가 자동 거부)
        """
        if now is None:
            # ★ 서버 TZ가 KST가 아니어도 정확하도록 KST 명시
            try:
                from utils.timezones import now_kst
                now = now_kst()
            except ImportError:
                now = datetime.now()  # fallback
        # tz-aware라면 KST로 변환
        if now.tzinfo is not None:
            try:
                from utils.timezones import KST
                now = now.astimezone(KST)
            except ImportError:
                pass

        if now.weekday() >= 5:  # 토/일
            return "closed"

        t = now.time()
        from datetime import time as _time
        if _time(8, 30) <= t < _time(8, 40):
            return "pre_close"  # 장전 시간외 종가도 가능 (전일 종가)
        if _time(8, 40) <= t < _time(9, 0):
            return "pre_auction"  # 장전 동시호가만
        if _time(9, 0) <= t < _time(15, 20):
            return "regular"
        if _time(15, 20) <= t < _time(15, 30):
            return "close_auction"
        if _time(15, 40) <= t < _time(16, 0):
            return "after_close"
        if _time(16, 0) <= t < _time(18, 0):
            return "after_single"
        return "closed"

    def _validate_session_for_order(self, order_type: "OrderType") -> tuple:
        """
        현재 시간이 주문 유형과 맞는지 검증

        Returns:
            (ok: bool, message: str)
        """
        session = self.get_korean_market_session()
        ot = order_type

        # OrderType별로 허용 세션
        if ot in (OrderType.MARKET, OrderType.LIMIT):
            # 정규장 + 동시호가 시간에만
            if session in ("regular", "pre_auction", "close_auction"):
                return True, f"정규 거래 시간 ({session})"
            return False, (
                f"정규장 외 시간 — 현재 세션: '{session}'. "
                f"09:00~15:30 정규장 시간에만 시장가/지정가 주문 가능"
            )
        elif ot == OrderType.PRE_MARKET_CLOSE:
            if session == "pre_close":
                return True, "장전 시간외 종가 시간"
            return False, f"장전 시간외 종가는 08:30~08:40만 — 현재 '{session}'"
        elif ot == OrderType.AFTER_HOURS_CLOSE:
            if session == "after_close":
                return True, "장후 시간외 종가 시간"
            return False, f"장후 시간외 종가는 15:40~16:00만 — 현재 '{session}'"
        elif ot == OrderType.AFTER_HOURS_SINGLE:
            if session == "after_single":
                return True, "시간외 단일가 시간"
            return False, f"시간외 단일가는 16:00~18:00만 — 현재 '{session}'"
        return True, "검증 생략"

    # ─── 토큰 캐시 파일 경로 ───────────────────────────────────────────
    # data/kis_token_paper.json (모의투자)
    # data/kis_token_live.json  (실거래)
    # 토큰을 파일에 저장하여 봇 재시작 후에도 24시간 동안 재사용.
    # → KIS의 "1분에 1회 발급 제한" 회피 + SMS 알림 빈도 감소
    @property
    def _token_cache_path(self) -> Path:
        """토큰 캐시 파일 경로 (모의/실거래 분리)"""
        project_root = Path(__file__).resolve().parent.parent
        data_dir = project_root / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        suffix = "paper" if self.paper else "live"
        return data_dir / f"kis_token_{suffix}.json"

    def _app_key_fingerprint(self) -> str:
        """App Key의 SHA256 앞 16자 (캐시 검증용)"""
        if not self.app_key:
            return ""
        return hashlib.sha256(self.app_key.encode()).hexdigest()[:16]

    def _load_cached_token(self) -> bool:
        """
        파일에서 캐시된 토큰 로드 시도

        검증 조건:
        1. 파일 존재
        2. JSON 정상 파싱
        3. App Key 지문 일치 (재발급 시 캐시 자동 무효화)
        4. 만료 시각이 아직 유효 (1시간 여유)

        Returns:
            True이면 캐시된 토큰 로드 성공, False면 새로 발급 필요
        """
        cache_path = self._token_cache_path
        if not cache_path.exists():
            return False

        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cached = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            # 파일 손상 감지 → 백업 후 새로 발급
            # (silent 새 토큰 발급은 SMS + 1분 1회 제한 위험)
            logger.warning(
                f"[KIS] 토큰 캐시 손상 감지 ({e}). "
                f"백업 후 새로 발급합니다 — SMS가 1회 발송될 수 있습니다."
            )
            try:
                from datetime import datetime as _dt
                backup_path = cache_path.with_suffix(
                    f".corrupted-{_dt.now().strftime('%Y%m%d_%H%M%S')}"
                )
                cache_path.rename(backup_path)
                logger.info(f"[KIS] 손상된 토큰을 {backup_path.name}로 백업")
            except OSError:
                pass
            return False

        try:
            # App Key 변경 감지 (재발급 시 캐시 무효화)
            if cached.get("app_key_fp") != self._app_key_fingerprint():
                logger.info("[KIS] 캐시된 토큰의 App Key가 다름 → 새로 발급")
                return False

            # 만료 시각 확인 (1시간 여유 두고 미리 갱신)
            expires_str = cached.get("token_expires", "")
            if not expires_str:
                return False
            expires_at = datetime.fromisoformat(expires_str)
            remaining = expires_at - datetime.now()
            if remaining < timedelta(hours=1):
                logger.info(
                    f"[KIS] 캐시된 토큰 만료 임박 ({int(remaining.total_seconds()/60)}분 남음) → 새로 발급"
                )
                return False

            # 캐시 사용
            self.access_token = cached["access_token"]
            self.token_expires = expires_at
            mode = "모의투자" if self.paper else "실거래"
            hours_left = int(remaining.total_seconds() / 3600)
            logger.info(
                f"[KIS] {mode} 캐시된 토큰 재사용 "
                f"(만료까지 {hours_left}시간 남음, SMS 발송 없음)"
            )
            return True

        except (json.JSONDecodeError, KeyError, ValueError, OSError) as e:
            logger.debug(f"[KIS] 토큰 캐시 로드 실패 (새로 발급): {e}")
            return False

    def _save_cached_token(self):
        """
        현재 토큰을 파일에 원자적으로 저장

        쓰기 도중 크래시/전원차단으로 파일이 손상되면 다음 봇 시작 시
        새 토큰을 발급해야 하고, 이는 SMS 발송 + KIS의 1분 1회 제한 위반 위험이 있어
        반드시 원자적 쓰기로 보호합니다.
        """
        if not self.access_token or not self.token_expires:
            return
        cache_path = self._token_cache_path
        try:
            data = {
                "access_token": self.access_token,
                "token_expires": self.token_expires.isoformat(),
                "issued_at": datetime.now().isoformat(),
                "paper": self.paper,
                "app_key_fp": self._app_key_fingerprint(),
            }
            # ★ temp → rename: 크래시 시 옛 파일이 통째로 남거나, 새 파일이 완전히 적용되거나
            from utils.atomic_io import atomic_write_json
            atomic_write_json(str(cache_path), data)
            try:
                # 파일 권한 제한 (소유자만 읽기/쓰기) — Windows는 chmod 제한적
                os.chmod(cache_path, 0o600)
            except (OSError, NotImplementedError):
                pass
            logger.debug(f"[KIS] 토큰 캐시 원자적 저장: {cache_path.name}")
        except OSError as e:
            logger.warning(f"[KIS] 토큰 캐시 저장 실패: {e}")
        except ImportError:
            # atomic_io 미사용 fallback
            try:
                with open(cache_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
            except OSError as e:
                logger.warning(f"[KIS] 토큰 캐시 저장 실패: {e}")

    def connect(self) -> bool:
        """
        API 연결: 캐시된 토큰 사용 또는 신규 발급

        KIS 토큰은 24시간 유효하므로 파일에 저장하여 재사용합니다.
        → 봇 재시작 시마다 새 토큰 발급으로 인한 SMS 알림 방지
        → KIS의 "1분에 1회 발급 제한" 회피
        """
        if not REQUESTS_AVAILABLE:
            logger.error("[KIS] requests 미설치")
            return False

        if not all([self.app_key, self.app_secret]):
            logger.error("[KIS] API 키가 설정되지 않았습니다. .env 파일을 확인하세요.")
            return False

        # 1순위: 파일 캐시에서 토큰 로드 시도
        if self._load_cached_token():
            return True

        # 2순위: 새 토큰 발급 (SMS 알림 발송됨)
        return self._request_token()

    def _request_token(self) -> bool:
        """
        OAuth2 토큰 신규 발급 (KIS 서버 호출)

        주의:
        - 1분에 1회 호출 제한 (초과 시 EGW00133 에러)
        - 토큰 발급 시 KIS에서 SMS 알림 발송
        → connect()에서 캐시 먼저 확인하므로 가능한 회피
        """
        try:
            url = f"{self.base_url}/oauth2/tokenP"
            body = {
                "grant_type": "client_credentials",
                "appkey": self.app_key,
                "appsecret": self.app_secret,
            }

            # ★ Session 사용으로 TLS handshake 재사용 (성능 + 안정성)
            sess = self.session or requests
            response = sess.post(url, json=body, timeout=10)
            if response.status_code == 200:
                data = response.json()
                self.access_token = data.get("access_token")
                # 토큰 만료 시간 기록
                # KIS 응답의 expires_in(초)을 사용하고, 없으면 23시간 가정
                expires_in_sec = data.get("expires_in", 23 * 3600)
                self.token_expires = datetime.now() + timedelta(seconds=int(expires_in_sec))

                mode = "모의투자" if self.paper else "실거래"
                logger.info(
                    f"[KIS] {mode} 신규 토큰 발급 "
                    f"(만료: {self.token_expires.strftime('%Y-%m-%d %H:%M')})"
                )

                # 파일에 캐싱 (다음 재시작 시 재사용)
                self._save_cached_token()
                return True
            else:
                # EGW00133: 1분 이내 재발급 시도
                try:
                    err = response.json()
                    err_code = err.get("error_code", "")
                    err_msg = err.get("error_description", response.text[:200])
                except Exception:
                    err_code = ""
                    err_msg = response.text[:200]
                logger.error(
                    f"[KIS] 토큰 발급 실패 (HTTP {response.status_code}, "
                    f"code={err_code}): {err_msg}"
                )
                return False

        except Exception as e:
            logger.error(f"[KIS] 연결 실패: {e}")
            return False

    def _ensure_token(self) -> bool:
        """
        토큰이 유효한지 확인하고, 만료 1시간 전이면 자동 갱신

        실거래 중 인증 실패 방지를 위해 만료 임박 전에 미리 갱신.
        """
        if not self.access_token:
            # 토큰 없으면 캐시부터 확인
            if self._load_cached_token():
                return True
            return self._request_token()

        if self.token_expires:
            remaining = self.token_expires - datetime.now()
            if remaining < timedelta(hours=1):
                logger.info(
                    f"[KIS] 토큰 만료 임박 ({int(remaining.total_seconds()/60)}분 남음) → 자동 갱신"
                )
                return self._request_token()

        return True

    def _get_headers(self, tr_id: str) -> Dict:
        """API 요청 헤더 생성"""
        return {
            "Content-Type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.access_token}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
        }

    def _record_recent_order(self, symbol: str, side: str, quantity: int, order_id: str):
        """최근 주문 기록 (중복 차단용)"""
        now = time.time()
        with self._inflight_lock:
            # 오래된 항목 제거
            self._recent_orders = [
                o for o in self._recent_orders
                if (now - o["ts"]) < self._order_dedupe_window_sec
            ]
            self._recent_orders.append({
                "symbol": symbol, "side": side, "quantity": quantity,
                "order_id": order_id, "ts": now,
            })

    def _check_duplicate_order(self, symbol: str, side: str, quantity: int) -> Optional[str]:
        """
        최근 N초 내 동일 (symbol, side, quantity) 주문이 있었는지 확인.
        있으면 그 order_id 반환 (중복 방지), 없으면 None.
        """
        now = time.time()
        with self._inflight_lock:
            for o in self._recent_orders:
                if (now - o["ts"]) < self._order_dedupe_window_sec \
                   and o["symbol"] == symbol \
                   and o["side"] == side \
                   and o["quantity"] == quantity:
                    return o.get("order_id", "")
        return None

    def _query_today_orders_for_match(self, kis_symbol: str, side: OrderSide,
                                      quantity: int, after_ts: float) -> Optional[str]:
        """
        오늘 주문 이력에서 (종목, 매수/매도, 수량, 제출시각 이후)와 일치하는 주문 검색

        timeout 후 reconciliation 용도: 우리가 보낸 주문이 KIS에 실제로 들어갔는지
        조회해서, 들어갔으면 그 ODNO를 반환합니다.

        Returns:
            매칭되는 order_id 또는 None
        """
        if not self._ensure_token():
            return None
        try:
            tr_id = "VTTC8001R" if self.paper else "TTTC8001R"
            url = f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-daily-ccld"
            params = {
                "CANO": self.cano,
                "ACNT_PRDT_CD": self.acnt_prdt_cd,
                "INQR_STRT_DT": datetime.now().strftime("%Y%m%d"),
                "INQR_END_DT": datetime.now().strftime("%Y%m%d"),
                "SLL_BUY_DVSN_CD": "01" if side == OrderSide.SELL else "02",
                "INQR_DVSN": "00",
                "PDNO": kis_symbol,
                "CCLD_DVSN": "00",
                "ORD_GNO_BRNO": "",
                "ODNO": "",
                "INQR_DVSN_3": "00",
                "INQR_DVSN_1": "",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
            }
            headers = self._get_headers(tr_id)
            sess = self.session or requests
            response = sess.get(url, headers=headers, params=params, timeout=10)
            if response.status_code != 200:
                return None
            data = response.json()
            if data.get("rt_cd") != "0":
                return None
            for item in data.get("output1", []):
                # 수량 일치
                if int(item.get("ord_qty", 0) or 0) != int(quantity):
                    continue
                # 종목 일치
                if item.get("pdno", "") != kis_symbol:
                    continue
                # 매수/매도 일치 (sll_buy_dvsn_cd: "01"=매도, "02"=매수)
                expected = "02" if side == OrderSide.BUY else "01"
                if item.get("sll_buy_dvsn_cd", "") != expected:
                    continue
                # ★ CRITICAL: 시각 확인 시 KST 타임존 명시 (서버 TZ가 달라도 정확)
                # 이전 버그: datetime.strptime(...).timestamp()은 naive datetime을 로컬 TZ로
                # 해석 → 서버가 UTC면 9시간 어긋나 reconciliation 실패 → 이중 매수 가능
                ord_dt = item.get("ord_dt", "")  # YYYYMMDD
                ord_tmd = item.get("ord_tmd", "")  # HHMMSS
                if ord_dt and ord_tmd and len(ord_tmd) == 6:
                    try:
                        from utils.timezones import KST
                        order_ts = datetime.strptime(
                            f"{ord_dt}{ord_tmd}", "%Y%m%d%H%M%S"
                        ).replace(tzinfo=KST).timestamp()
                        # after_ts보다 10초 전 ~ 60초 후 범위
                        if order_ts < after_ts - 10 or order_ts > after_ts + 60:
                            continue
                    except (ValueError, ImportError):
                        # fallback: naive로 처리 (서버가 KST라고 가정)
                        try:
                            order_ts = datetime.strptime(
                                f"{ord_dt}{ord_tmd}", "%Y%m%d%H%M%S"
                            ).timestamp()
                            if order_ts < after_ts - 10 or order_ts > after_ts + 60:
                                continue
                        except ValueError:
                            pass
                return item.get("odno", "")
            return None
        except Exception as e:
            logger.debug(f"[KIS] 주문 조회 실패 (reconciliation): {e}")
            return None

    def submit_order(self, order: Order) -> Order:
        """
        한국투자증권에 주문 제출 (Phase 6 안전장치 포함)

        안전장치:
          1. 입력 검증: 수량 ≤ 0, 비현실적 가격 차단
          2. 중복 차단: 30초 내 같은 (symbol, side, qty) 주문 차단
          3. timeout 시 reconciliation: KIS가 실제로 주문 받았는지 조회 →
             받았으면 SUBMITTED + 그 ODNO 반환 (이중 매수 방지)

        한국 주식 주문 tr_id:
        - 모의: VTTC0802U(매수), VTTC0801U(매도)
        - 실전: TTTC0802U(매수), TTTC0801U(매도)
        """
        # ── ① 입력 검증 (CRITICAL) ──
        # 수량/가격이 비정상이면 KIS에 보내기 전에 차단
        if order.quantity <= 0:
            logger.error(f"[KIS] 잘못된 주문 수량: {order.quantity} → 거부")
            order.status = OrderStatus.REJECTED
            return order
        if order.quantity > 1_000_000:  # 비현실적 대량 주문 차단
            logger.error(f"[KIS] 수량 한도 초과: {order.quantity}주 → 거부")
            order.status = OrderStatus.REJECTED
            return order
        if order.order_type == OrderType.LIMIT:
            if not order.price or order.price <= 0:
                logger.error(f"[KIS] 지정가 주문 가격 오류: {order.price} → 거부")
                order.status = OrderStatus.REJECTED
                return order

        # ── ② 중복 차단: 최근 30초 내 동일 주문 무효화 ──
        # 분석 사이클이 빠르게 돌면서 같은 신호로 동일 주문이 두 번 갈 수 있음 → 차단
        side_str = order.side.value
        dup_order_id = self._check_duplicate_order(order.symbol, side_str, order.quantity)
        if dup_order_id is not None:
            logger.warning(
                f"[KIS] ⚠️ 중복 주문 차단: {order.symbol} {side_str} {order.quantity}주 "
                f"(이미 {self._order_dedupe_window_sec}초 내 제출됨, 이전 ODNO={dup_order_id})"
            )
            order.status = OrderStatus.REJECTED
            order.order_id = f"DUP:{dup_order_id}"  # 디버그용 표시
            return order

        # 토큰 유효성 확인 (만료 시 자동 갱신)
        if not self._ensure_token():
            order.status = OrderStatus.REJECTED
            logger.error("[KIS] 토큰 없음/갱신 실패 -> 주문 거부")
            return order

        # tr_id 결정
        if self.paper:
            tr_id = "VTTC0802U" if order.side == OrderSide.BUY else "VTTC0801U"
        else:
            tr_id = "TTTC0802U" if order.side == OrderSide.BUY else "TTTC0801U"

        # ── KIS ORD_DVSN 코드 (공식 문서 기준) ──
        # "00" = 지정가 (Limit)
        # "01" = 시장가 (Market)
        # "02" = 조건부지정가
        # "03" = 최유리지정가
        # "04" = 최우선지정가
        # "05" = 장전 시간외 (08:30~09:00 동시호가 보조)
        # "06" = 장후 시간외 종가 (15:40~16:00, 종가로 거래)
        # "07" = 시간외 단일가 (16:00~18:00, 10분 단일가, 전일 종가 ±10%)
        # ⚠️ 이전 버그: MARKET=05, LIMIT=01로 잘못 설정 → 모든 실거래 주문 거부 위험
        if order.order_type == OrderType.MARKET:
            ord_dvsn = "01"  # 시장가 (정규장 09:00~15:30)
            ord_unpr = "0"
        elif order.order_type == OrderType.LIMIT:
            ord_dvsn = "00"  # 지정가
            ord_unpr = str(int(order.price)) if order.price else "0"
        elif order.order_type == OrderType.AFTER_HOURS_CLOSE:
            # 장후 시간외 종가 매매 (15:40~16:00) — 당일 종가로 거래
            ord_dvsn = "06"
            ord_unpr = "0"  # 종가로 자동 체결되므로 가격 0
        elif order.order_type == OrderType.AFTER_HOURS_SINGLE:
            # 시간외 단일가 (16:00~18:00) — 반드시 지정가 + 전일 종가 ±10% 범위
            ord_dvsn = "07"
            ord_unpr = str(int(order.price)) if order.price else "0"
            if not order.price or order.price <= 0:
                logger.error(
                    f"[KIS] 시간외 단일가는 지정가 필수 (현재 price={order.price}) → 거부"
                )
                order.status = OrderStatus.REJECTED
                return order
        elif order.order_type == OrderType.PRE_MARKET_CLOSE:
            # ★ FIX: 장전 시간외 종가는 "05" (장후는 "06") — KIS 공식 문서 기준 명확히 구분
            # 이전 버그: "06" 사용 → KIS가 08:30~08:40 시간대 거부
            ord_dvsn = "05"
            ord_unpr = "0"
        else:
            # STOP / STOP_LIMIT 등 KIS에서 미지원 — 거부
            logger.error(
                f"[KIS] 미지원 주문 유형: {order.order_type.value} → 거부 "
                f"(STOP은 봇이 ExitManager로 자체 관리)"
            )
            order.status = OrderStatus.REJECTED
            return order

        # ── 시간외 거래 시간대 사전 검증 ──
        # 시간외 주문은 정해진 시간대에만 KIS가 받음. 시간 밖이면 KIS가 거부하므로
        # 미리 알려서 사용자 혼란 방지.
        time_ok, time_msg = self._validate_session_for_order(order.order_type)
        if not time_ok:
            logger.warning(f"[KIS] 시간대 불일치: {time_msg} → 거부 가능성 높음")
            # 거부하지 않고 KIS에 보내봄 — 사용자가 명시적으로 요청했으면 KIS 응답으로 판단

        # [FIX] 종목코드에서 .KS/.KQ 접미사 제거 (KIS는 6자리만 허용)
        kis_symbol = self._strip_suffix(order.symbol)

        # 제출 시각 기록 (timeout reconciliation 용)
        submit_ts = time.time()

        # 주문 요청
        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/order-cash"
        body = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "PDNO": kis_symbol,
            "ORD_DVSN": ord_dvsn,
            "ORD_QTY": str(order.quantity),
            "ORD_UNPR": ord_unpr,
        }
        headers = self._get_headers(tr_id)
        sess = self.session or requests

        # ── ③ 실제 POST 요청 + timeout 시 reconciliation ──
        try:
            response = sess.post(url, headers=headers, json=body, timeout=10)
        except requests.Timeout:
            # ⚠️ CRITICAL: timeout이라도 KIS가 실제로는 주문을 받았을 수 있음
            # 무작정 REJECTED로 처리하면 다음 사이클에서 재시도 → 이중 매수
            logger.warning(
                f"[KIS] ⚠️ 주문 timeout — 실제 체결 여부 확인 중..."
            )
            time.sleep(2)  # KIS가 처리할 시간 부여
            found_id = self._query_today_orders_for_match(
                kis_symbol, order.side, order.quantity, submit_ts
            )
            if found_id:
                logger.warning(
                    f"[KIS] ✓ Timeout이었지만 주문 실제 접수됨 — ODNO={found_id} "
                    f"(이중 매수 방지)"
                )
                order.order_id = found_id
                order.status = OrderStatus.SUBMITTED
                order.submitted_at = datetime.fromtimestamp(submit_ts)
                self._record_recent_order(order.symbol, side_str, order.quantity, found_id)
                self.orders.append(order)
                return order
            else:
                logger.error("[KIS] Timeout + 주문 미접수 확인 → 안전하게 REJECTED")
                order.status = OrderStatus.REJECTED
                return order
        except requests.ConnectionError as e:
            # 네트워크 오류 — 같은 처리: 실제 접수 여부 확인
            logger.warning(f"[KIS] ⚠️ 네트워크 오류 ({e}) — 실제 체결 여부 확인 중...")
            time.sleep(2)
            found_id = self._query_today_orders_for_match(
                kis_symbol, order.side, order.quantity, submit_ts
            )
            if found_id:
                logger.warning(f"[KIS] ✓ 네트워크 오류였지만 주문 실제 접수됨 — ODNO={found_id}")
                order.order_id = found_id
                order.status = OrderStatus.SUBMITTED
                order.submitted_at = datetime.fromtimestamp(submit_ts)
                self._record_recent_order(order.symbol, side_str, order.quantity, found_id)
                self.orders.append(order)
                return order
            order.status = OrderStatus.REJECTED
            return order
        except Exception as e:
            logger.error(f"[KIS] 주문 예외: {e}")
            order.status = OrderStatus.REJECTED
            return order

        # ── ④ HTTP 응답 처리 ──
        if response.status_code == 200:
            data = response.json()
            if data.get("rt_cd") == "0":  # 성공
                order.order_id = data.get("output", {}).get("ODNO", "")
                order.status = OrderStatus.SUBMITTED
                order.submitted_at = datetime.now()
                # 중복 차단을 위해 기록
                self._record_recent_order(order.symbol, side_str, order.quantity, order.order_id)
            else:
                order.status = OrderStatus.REJECTED
                msg_cd = data.get("msg_cd", "")
                msg1 = data.get("msg1", "")
                logger.error(f"[KIS] 주문 거부 [{msg_cd}]: {msg1}")
        else:
            # HTTP 4xx/5xx — body에 EGW02007 같은 정보 있을 수 있음
            try:
                err_data = response.json()
                logger.error(
                    f"[KIS] HTTP {response.status_code} 주문 실패: "
                    f"[{err_data.get('msg_cd', '')}] {err_data.get('msg1', '')}"
                )
            except (ValueError, Exception):
                logger.error(f"[KIS] HTTP {response.status_code} 주문 실패: {response.text[:200]}")
            order.status = OrderStatus.REJECTED

        # ── ★ CRITICAL FIX (Phase 12): SUBMITTED 후 체결 확정 폴링 ──
        # 이전 버그: 여기서 self.get_positions()를 호출해 pre_sell_avg를 조회했는데
        # API 실패 시 self._last_positions_call_ok=False가 설정되어
        # → 같은 사이클의 후속 매수 신호가 silent하게 차단됨
        # 수정: pre_sell_avg는 order.avg_price_hint에 caller가 미리 넣어 전달
        #       (run_bot._execute_sell이 held.avg_price를 전달)
        if order.status == OrderStatus.SUBMITTED and order.order_id:
            pre_sell_avg = getattr(order, "avg_price_hint", 0.0) or 0.0
            self._poll_fill_and_record(order, pre_sell_avg_price=pre_sell_avg)

        self.orders.append(order)
        return order

    @staticmethod
    def _safe_int(v, default: int = 0) -> int:
        """KIS 응답의 문자열/None/빈문자열을 안전하게 int로 변환"""
        if v is None or v == "" or v == " ":
            return default
        try:
            return int(v)
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _safe_float(v, default: float = 0.0) -> float:
        """KIS 응답의 문자열/None/빈문자열을 안전하게 float로 변환"""
        if v is None or v == "" or v == " ":
            return default
        try:
            return float(v)
        except (ValueError, TypeError):
            return default

    def _poll_fill_and_record(self, order: Order, max_wait_sec: Optional[float] = None,
                               pre_sell_avg_price: float = 0.0) -> None:
        """
        주문 제출 후 체결 상태를 짧게 폴링하여 FILLED/PARTIAL로 갱신 + DB/메모리 기록

        ★ Phase 11 안정화:
          - filled_price 추출: tot_ccld_amt / tot_ccld_qty 우선 (avg_prvs는 0일 수 있음)
          - 안전 형변환: 빈 문자열/None → 0 (이전 ValueError로 silent SUBMITTED)
          - 매도 실현 PnL: pre_sell_avg_price를 받아 DB에도 정확히 기록
          - 폴링 1초 간격 (KIS 1/sec 한도 준수, 기본 5초→총 5회 호출)
          - PARTIAL도 FILLED와 동일하게 caller가 처리 가능하도록 일단 FILLED로 승격
            (caller `_execute_buy/_execute_sell`은 `== "filled"`만 검사하므로
             PARTIAL을 그대로 두면 ExitManager 미등록 → 손절 안 됨)

        Parameters:
            order: 제출된 주문 (status=SUBMITTED, order_id 설정됨)
            max_wait_sec: 폴링 최대 대기 시간 (기본 self.fill_poll_seconds)
            pre_sell_avg_price: 매도 전 보유 평균매수가 (실현PnL 정확 계산용)
                               caller가 get_positions()로 조회 후 전달

        체결 확정 시:
        1. order.status = FILLED + filled_price 갱신
        2. trade_history.append (memory)
        3. db.log_trade with realized_pnl (있으면)
        """
        max_wait = max_wait_sec if max_wait_sec is not None else self.fill_poll_seconds
        start = time.time()
        last_status = OrderStatus.SUBMITTED
        filled_qty = 0
        filled_price = 0.0
        partial_fill_warning = False

        # KIS 1/sec 한도 준수: 1초 간격으로 폴링 (5초 max = 5회)
        POLL_INTERVAL_SEC = 1.0

        while (time.time() - start) < max_wait:
            try:
                # ★ Phase 11: 날짜는 KST 기준 (서버 TZ가 UTC면 자정~09KST 사이 잘못된 날짜)
                try:
                    from utils.timezones import now_kst
                    kst_today = now_kst().strftime("%Y%m%d")
                except ImportError:
                    kst_today = datetime.now().strftime("%Y%m%d")

                tr_id = "VTTC8001R" if self.paper else "TTTC8001R"
                url = f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-daily-ccld"
                params = {
                    "CANO": self.cano,
                    "ACNT_PRDT_CD": self.acnt_prdt_cd,
                    "INQR_STRT_DT": kst_today,
                    "INQR_END_DT": kst_today,
                    "SLL_BUY_DVSN_CD": "00",
                    "INQR_DVSN": "00",
                    "PDNO": self._strip_suffix(order.symbol),
                    "CCLD_DVSN": "00",
                    "ORD_GNO_BRNO": "",
                    "ODNO": order.order_id,
                    "INQR_DVSN_3": "00",
                    "INQR_DVSN_1": "",
                    "CTX_AREA_FK100": "",
                    "CTX_AREA_NK100": "",
                }
                headers = self._get_headers(tr_id)
                sess = self.session or requests
                r = sess.get(url, headers=headers, params=params, timeout=5)
                if r.status_code == 200:
                    data = r.json()
                    if data.get("rt_cd") == "0":
                        for item in data.get("output1", []):
                            if item.get("odno") != order.order_id:
                                continue

                            # ★ 안전 형변환 (빈 문자열 → 0)
                            fq = self._safe_int(item.get("tot_ccld_qty"))
                            oq = self._safe_int(item.get("ord_qty"))
                            tot_amt = self._safe_float(item.get("tot_ccld_amt"))
                            avg = self._safe_float(item.get("avg_prvs"))
                            unpr = self._safe_float(item.get("ord_unpr"))

                            # ★ Phase 11 C2 FIX: fill_price 정확 추출
                            # 우선순위: 1) tot_amt/qty (실제 체결 평균)
                            #          2) avg_prvs (KIS가 채워준 평균, 미체결 시 0)
                            #          3) unpr (지정가 주문 한정, 시장가는 0)
                            if fq > 0 and tot_amt > 0:
                                computed_price = tot_amt / fq
                            elif avg > 0:
                                computed_price = avg
                            elif unpr > 0:
                                computed_price = unpr
                            else:
                                computed_price = 0.0  # 아직 모름

                            # 취소 확인
                            if item.get("cncl_yn", "N") == "Y":
                                last_status = OrderStatus.CANCELLED
                                break

                            # FILLED 확정 — qty만 채워졌으면 일단 FILLED 승격
                            # 가격은 다음 폴링에서 갱신 가능
                            if fq >= oq and oq > 0:
                                last_status = OrderStatus.FILLED
                                filled_qty = fq
                                if computed_price > 0:
                                    filled_price = computed_price
                                break
                            elif fq > 0:
                                # PARTIAL: 일부만 체결 → 계속 폴링하면서 추적
                                filled_qty = fq
                                if computed_price > 0:
                                    filled_price = computed_price
                                partial_fill_warning = True
                                last_status = OrderStatus.PARTIAL
                            break  # 같은 ODNO 더 이상 없음
                        if last_status in (OrderStatus.FILLED, OrderStatus.CANCELLED):
                            break
            except Exception as e:
                logger.debug(f"[KIS] 체결 폴링 중 예외 (계속): {e}")
            time.sleep(POLL_INTERVAL_SEC)

        # ── Phase 11 H1 FIX: PARTIAL은 FILLED로 승격 처리 ──
        # caller(_execute_buy 등)가 `== "filled"`만 체크하므로, PARTIAL이면
        # 일부는 체결됐는데 ExitManager 미등록 → 손절 안 되는 위험.
        # 일부 체결이라도 포지션은 발생했으므로 caller가 인식하도록 FILLED 처리.
        # 단, filled_quantity는 실제 체결분만 반영하여 caller가 정확한 수량 사용.
        if last_status == OrderStatus.PARTIAL and filled_qty > 0:
            logger.warning(
                f"[KIS] {order.symbol} 부분 체결: {filled_qty}/{order.quantity}주 "
                f"— FILLED로 승격하여 ExitManager 등록 보장. ODNO={order.order_id}"
            )
            last_status = OrderStatus.FILLED

        order.status = last_status

        # ★ Phase 11 L3 FIX: filled_quantity는 FILLED/PARTIAL일 때만 의미 있음
        if last_status == OrderStatus.FILLED:
            order.filled_quantity = filled_qty
        else:
            order.filled_quantity = 0

        # FILLED 처리 — 단, filled_price가 0이면 DB/메모리 기록 안 함 (잘못된 데이터 방지)
        if last_status == OrderStatus.FILLED:
            if filled_price <= 0:
                # 체결은 됐는데 가격을 못 찾음 — 다음 사이클에서 reconcile로 보정
                logger.warning(
                    f"[KIS] {order.symbol} 체결 확인되었으나 평균가 미수신 "
                    f"(qty={filled_qty}) → DB 기록 보류, reconcile에서 보정 필요"
                )
                # status는 FILLED 유지 (caller가 ExitManager 등록 등을 진행하도록)
                # 가격은 cached_price (caller가 갖고 있음) 사용 가능
                return

            order.filled_price = filled_price
            order.filled_at = datetime.now()

            # ★ Phase 11 BUG-1 FIX: 매도 실현 PnL 계산 (DB에도 정확히 기록)
            realized_pnl = 0.0
            if order.side == OrderSide.SELL and pre_sell_avg_price > 0:
                # KIS는 KRW이므로 그대로 (price - avg) × qty
                realized_pnl = (filled_price - pre_sell_avg_price) * filled_qty

            # ── 메모리 trade_history 기록 (caller 호환) ──
            try:
                self.trade_history.append({
                    "order_id": order.order_id,
                    "symbol": order.symbol,
                    "side": order.side.value,
                    "quantity": filled_qty,
                    "price": filled_price,
                    "total": filled_qty * filled_price,
                    "fee": 0.0,  # KIS는 별도 조회 — 0으로 보고
                    "strategy": order.strategy or "",
                    "timestamp": order.filled_at,
                    "realized_pnl": realized_pnl,
                })
            except Exception as e:
                logger.debug(f"[KIS] trade_history 기록 실패 (무시): {e}")

            # ── DB 거래 기록 ──
            if self.db is not None:
                try:
                    market = "KR"
                    decision_json = getattr(order, "decision_json", None) or "{}"
                    self.db.log_trade(
                        symbol=order.symbol,
                        side=order.side.value.upper(),
                        quantity=filled_qty,
                        price=filled_price,
                        strategy=order.strategy or "",
                        market=market,
                        order_id=order.order_id,
                        pnl=realized_pnl,  # ★ 정확한 실현 PnL
                        total_value=filled_qty * filled_price,
                        decision_json=decision_json,
                        mode=self.mode,
                    )
                    logger.info(
                        f"[KIS] 체결 + DB 기록: {order.symbol} {order.side.value} "
                        f"{filled_qty}주 @ ₩{filled_price:,.0f}"
                        + (f" | 실현PnL ₩{realized_pnl:,.0f}" if realized_pnl != 0 else "")
                        + f" (ODNO={order.order_id})"
                    )
                except Exception as e:
                    logger.error(f"[KIS] DB log_trade 실패: {e}")
        elif last_status == OrderStatus.SUBMITTED:
            # ★ Phase 11 BUG-3: 폴링 만료 후에도 SUBMITTED — 다음 사이클에서 재확인
            # caller는 이 주문을 자체 추적해야 함 (현재는 _recent_orders에 기록됨)
            logger.warning(
                f"[KIS] {order.symbol} 주문 폴링 {max_wait:.0f}초 만료 — 여전히 SUBMITTED. "
                f"다음 분석 사이클에서 reconcile로 확인 예정. ODNO={order.order_id}"
            )

    def cancel_order(self, order_id: str, original_ord_dvsn: Optional[str] = None) -> bool:
        """
        주문 취소

        ⚠️ CRITICAL FIX:
          1. ORD_DVSN을 원주문 종류에 맞춰 보냄 (이전엔 "01" 하드코딩 → 지정가 취소 실패)
          2. HTTP 200만 보지 말고 rt_cd="0"까지 확인 (이전엔 KIS 에러도 성공으로 봄)
          3. 예외를 silently 삼키지 않고 로깅

        Parameters:
            order_id: KIS ODNO
            original_ord_dvsn: 원주문의 ORD_DVSN ("00"=지정가, "01"=시장가 등).
                               미지정 시 "00"(지정가)으로 시도.
        """
        if not self._ensure_token():
            logger.error(f"[KIS] 취소 실패: 토큰 발급/갱신 실패 (ODNO={order_id})")
            return False

        try:
            tr_id = "VTTC0803U" if self.paper else "TTTC0803U"
            url = f"{self.base_url}/uapi/domestic-stock/v1/trading/order-rvsecncl"

            # 원주문 ORD_DVSN을 모르면 "00" (지정가)을 기본값으로
            # KIS는 취소 시 원주문 종류와 일치해야 함 — 불일치 시 거부
            ord_dvsn_for_cancel = original_ord_dvsn or "00"

            body = {
                "CANO": self.cano,
                "ACNT_PRDT_CD": self.acnt_prdt_cd,
                "KRX_FWDG_ORD_ORGNO": "",
                "ORGN_ODNO": order_id,
                "ORD_DVSN": ord_dvsn_for_cancel,
                "RVSE_CNCL_DVSN_CD": "02",  # 02=취소 (01=정정)
                "ORD_QTY": "0",
                "ORD_UNPR": "0",
                "QTY_ALL_ORD_YN": "Y",
            }

            headers = self._get_headers(tr_id)
            sess = self.session or requests
            response = sess.post(url, headers=headers, json=body, timeout=10)

            if response.status_code != 200:
                logger.error(
                    f"[KIS] 취소 HTTP {response.status_code} (ODNO={order_id}): "
                    f"{response.text[:200]}"
                )
                return False

            try:
                data = response.json()
            except (ValueError, Exception) as e:
                logger.error(f"[KIS] 취소 응답 파싱 실패 (ODNO={order_id}): {e}")
                return False

            # ★ rt_cd="0"인 경우에만 진짜 성공
            if data.get("rt_cd") == "0":
                logger.info(f"[KIS] 취소 성공: ODNO={order_id}")
                return True
            else:
                msg_cd = data.get("msg_cd", "")
                msg1 = data.get("msg1", "")
                logger.warning(
                    f"[KIS] 취소 거부 (ODNO={order_id}) [{msg_cd}]: {msg1} "
                    f"— 원주문 ORD_DVSN 불일치일 가능성 (현재 '{ord_dvsn_for_cancel}')"
                )
                return False

        except Exception as e:
            logger.error(f"[KIS] 취소 예외 (ODNO={order_id}): {e}")
            return False

    def get_order_status(self, order_id: str) -> OrderStatus:
        """
        주문 체결 상태 조회

        KIS 체결 조회 API (VTTC8001R/TTTC8001R)를 호출하여
        주문의 실제 상태를 확인합니다.
        조회 실패 시 안전하게 SUBMITTED 반환 (보수적 접근)
        """
        if not self._ensure_token():
            return OrderStatus.REJECTED

        try:
            tr_id = "VTTC8001R" if self.paper else "TTTC8001R"
            url = f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-daily-ccld"

            params = {
                "CANO": self.cano,
                "ACNT_PRDT_CD": self.acnt_prdt_cd,
                "INQR_STRT_DT": datetime.now().strftime("%Y%m%d"),
                "INQR_END_DT": datetime.now().strftime("%Y%m%d"),
                "SLL_BUY_DVSN_CD": "00",  # 전체 (매수+매도)
                "INQR_DVSN": "00",
                "PDNO": "",
                "CCLD_DVSN": "00",
                "ORD_GNO_BRNO": "",
                "ODNO": order_id,
                "INQR_DVSN_3": "00",
                "INQR_DVSN_1": "",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
            }

            headers = self._get_headers(tr_id)
            sess = self.session or requests
            response = sess.get(url, headers=headers, params=params, timeout=10)

            if response.status_code == 200:
                data = response.json()
                for item in data.get("output1", []):
                    if item.get("odno") == order_id:
                        filled_qty = int(item.get("tot_ccld_qty", 0))
                        ord_qty = int(item.get("ord_qty", 0))
                        if filled_qty >= ord_qty and ord_qty > 0:
                            return OrderStatus.FILLED
                        elif filled_qty > 0:
                            return OrderStatus.PARTIAL
                        # 취소/정정 여부 확인
                        cncl_yn = item.get("cncl_yn", "N")
                        if cncl_yn == "Y":
                            return OrderStatus.CANCELLED
                        return OrderStatus.SUBMITTED

        except Exception as e:
            logger.debug(f"[KIS] 체결 조회 실패 (안전하게 SUBMITTED 반환): {e}")

        return OrderStatus.SUBMITTED

    def get_positions(self) -> List[Position]:
        """
        보유 포지션 조회

        ⚠️ CRITICAL: API 실패 시도 []를 반환하지만, 호출자가 "빈 포지션"과
        혼동하지 않도록 self._last_positions_call_ok 플래그를 설정합니다.
        새 매수 결정 시 self.positions_query_succeeded() 확인 필수.
        """
        if not self._ensure_token():
            self._last_positions_call_ok = False
            logger.error("[KIS] 포지션 조회: 토큰 발급/갱신 실패 → 빈 리스트 (API 실패 플래그 ON)")
            return []

        try:
            tr_id = "VTTC8434R" if self.paper else "TTTC8434R"
            url = f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-balance"

            params = {
                "CANO": self.cano,
                "ACNT_PRDT_CD": self.acnt_prdt_cd,
                "AFHR_FLPR_YN": "N",
                "OFL_YN": "",
                "INQR_DVSN": "02",
                "UNPR_DVSN": "01",
                "FUND_STTL_ICLD_YN": "N",
                "FNCG_AMT_AUTO_RDPT_YN": "N",
                "PRCS_DVSN": "01",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
            }

            headers = self._get_headers(tr_id)
            sess = self.session or requests
            response = sess.get(url, headers=headers, params=params, timeout=10)

            if response.status_code != 200:
                self._last_positions_call_ok = False
                logger.error(f"[KIS] 포지션 조회 HTTP {response.status_code} → 빈 리스트")
                return []

            data = response.json()
            if data.get("rt_cd") != "0":
                self._last_positions_call_ok = False
                logger.error(
                    f"[KIS] 포지션 조회 오류 [{data.get('msg_cd')}]: {data.get('msg1')} → 빈 리스트"
                )
                return []

            positions = []
            for item in data.get("output1", []):
                qty = int(item.get("hldg_qty", 0))
                if qty > 0:
                    # KIS는 순수 6자리 코드를 반환 -> .KS 접미사 추가하여
                    # 봇 내부의 심볼 포맷과 일치시킴
                    raw_symbol = item.get("pdno", "")
                    symbol = f"{raw_symbol}.KS" if raw_symbol.isdigit() else raw_symbol

                    positions.append(Position(
                        symbol=symbol,
                        quantity=qty,
                        avg_price=float(item.get("pchs_avg_pric", 0)),
                        current_price=float(item.get("prpr", 0)),
                        unrealized_pnl=float(item.get("evlu_pfls_amt", 0)),
                        market_value=float(item.get("evlu_amt", 0)),
                    ))

            # 성공 — API 정상 동작 확인
            self._last_positions_call_ok = True
            return positions

        except Exception as e:
            self._last_positions_call_ok = False
            logger.error(f"[KIS] 포지션 조회 예외: {e}")
            return []

    def positions_query_succeeded(self) -> bool:
        """
        가장 최근 get_positions() 호출이 성공했는지 반환

        호출자는 새 매수 결정 시 이 메서드로 API 상태를 확인해야 합니다.
        False이면 "보유 없음"인지 "API 실패"인지 알 수 없으므로
        새 매수를 보류해야 이중 매수를 방지할 수 있습니다.
        """
        return self._last_positions_call_ok

    def account_query_succeeded(self) -> bool:
        """가장 최근 get_account() 호출이 성공했는지 반환"""
        return self._last_account_call_ok

    def get_current_price(self, symbol: str) -> Optional[float]:
        """
        실시간 현재가 조회 (한국 주식 전용)

        KIS의 inquire-price API를 사용하여 약 1초 지연 수준의
        실시간 시세를 조회합니다. yfinance는 15-20분 지연이라
        KIS가 훨씬 정확한 가격을 제공합니다.

        Parameters:
            symbol: 종목코드 (".KS"/".KQ" 접미사 자동 제거)

        Returns:
            현재가 (KRW). 실패 시 None.

        호출 한도:
            - 모의투자: 1초당 20회
            - 실거래: 1초당 20회
            대시보드 1분 갱신은 충분히 여유로움.
        """
        if not self._ensure_token():
            return None

        try:
            kis_symbol = self._strip_suffix(symbol)

            url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-price"
            headers = self._get_headers("FHKST01010100")
            params = {
                "FID_COND_MRKT_DIV_CODE": "J",  # J=주식
                "FID_INPUT_ISCD": kis_symbol,
            }
            sess = self.session or requests
            response = sess.get(url, headers=headers, params=params, timeout=5)
            if response.status_code != 200:
                return None
            data = response.json()
            if data.get("rt_cd") != "0":
                return None
            price_str = data.get("output", {}).get("stck_prpr", "")
            if not price_str:
                return None
            return float(price_str)

        except Exception as e:
            logger.debug(f"[KIS] 시세 조회 실패 {symbol}: {e}")
            return None

    def get_index_quote(self, index_code: str) -> Optional[Dict]:
        """
        시장 지수 조회 (KOSPI=0001, KOSDAQ=1001)

        KIS의 inquire-index-price API를 사용하여 실시간 지수와
        전일 대비 변동률을 반환합니다.

        Parameters:
            index_code: "0001"(KOSPI), "1001"(KOSDAQ), "2001"(KOSPI200)

        Returns:
            {"price": float, "change": float, "change_pct": float} 또는 None
        """
        if not self._ensure_token():
            return None

        try:
            url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-index-price"
            headers = self._get_headers("FHPUP02100000")
            params = {
                "FID_COND_MRKT_DIV_CODE": "U",  # U=업종/지수
                "FID_INPUT_ISCD": index_code,
            }
            sess = self.session or requests
            response = sess.get(url, headers=headers, params=params, timeout=5)
            if response.status_code != 200:
                return None
            data = response.json()
            if data.get("rt_cd") != "0":
                return None
            output = data.get("output", {})
            return {
                "price": float(output.get("bstp_nmix_prpr", 0)),
                "change": float(output.get("bstp_nmix_prdy_vrss", 0)),
                "change_pct": float(output.get("prdy_ctrt", 0)),
            }
        except Exception as e:
            logger.debug(f"[KIS] 지수 조회 실패 {index_code}: {e}")
            return None

    def get_current_prices(self, symbols: List[str]) -> Dict[str, float]:
        """
        여러 종목 현재가 일괄 조회

        KIS는 일괄 조회 API가 없어서 각 종목을 순차 호출합니다.
        1초당 20회 제한이 있으므로 종목 수가 20개를 넘으면 100ms 대기.

        Returns:
            {symbol: price} 딕셔너리. 실패한 종목은 제외.
        """
        result = {}
        for i, sym in enumerate(symbols):
            price = self.get_current_price(sym)
            if price is not None:
                result[sym] = price
            # API 한도 보호: 매 20개마다 1초 대기
            if (i + 1) % 18 == 0 and i < len(symbols) - 1:
                time.sleep(1.0)
        return result

    def get_account(self) -> AccountInfo:
        """
        계좌 정보 조회

        ⚠️ API 실패 시도 0짜리 AccountInfo를 반환하지만, 호출자가 "현금 0원"으로
        오인하지 않도록 self._last_account_call_ok 플래그를 설정합니다.
        """
        if not self._ensure_token():
            self._last_account_call_ok = False
            logger.error("[KIS] 계좌 조회: 토큰 발급/갱신 실패")
            return AccountInfo(currency="KRW")

        try:
            tr_id = "VTTC8434R" if self.paper else "TTTC8434R"
            url = f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-balance"

            params = {
                "CANO": self.cano,
                "ACNT_PRDT_CD": self.acnt_prdt_cd,
                "AFHR_FLPR_YN": "N",
                "OFL_YN": "",
                "INQR_DVSN": "02",
                "UNPR_DVSN": "01",
                "FUND_STTL_ICLD_YN": "N",
                "FNCG_AMT_AUTO_RDPT_YN": "N",
                "PRCS_DVSN": "01",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
            }

            headers = self._get_headers(tr_id)
            sess = self.session or requests
            response = sess.get(url, headers=headers, params=params, timeout=10)

            if response.status_code == 200:
                data = response.json()
                if data.get("rt_cd") != "0":
                    self._last_account_call_ok = False
                    logger.error(
                        f"[KIS] 계좌 조회 오류 [{data.get('msg_cd')}]: {data.get('msg1')}"
                    )
                    return AccountInfo(currency="KRW")

                output2 = data.get("output2", [{}])
                if output2:
                    summary = output2[0]
                    self._last_account_call_ok = True
                    # ★ 현금: 가수도정산금액(prvs_rcdl_excc_amt, D+2 정산) 사용.
                    #   당일 매수·매도가 즉시 반영돼 매수 직후 현금이 줄어든다.
                    #   이전 버그: dnca_tot_amt(예수금총금액)는 T+2 정산 전까지
                    #   안 줄어 "주식 샀는데 현금 변동 없음"으로 보였음.
                    #   buying_power도 nass_amt(순자산=현금+주식)는 매수해도
                    #   거의 안 변해 잘못 → 정산 반영 현금으로 통일.
                    _cash_raw = summary.get("prvs_rcdl_excc_amt")
                    if _cash_raw in (None, ""):
                        _cash_raw = summary.get("dnca_tot_amt", 0)
                    cash_amt = self._safe_float(_cash_raw)
                    return AccountInfo(
                        total_equity=float(summary.get("tot_evlu_amt", 0)),
                        cash=cash_amt,
                        buying_power=cash_amt,  # 주문가능 현금 = 정산 반영 현금
                        positions_value=float(summary.get("scts_evlu_amt", 0)),
                        currency="KRW",
                    )

            self._last_account_call_ok = False
            logger.error(f"[KIS] 계좌 조회 HTTP {response.status_code}")
            return AccountInfo(currency="KRW")

        except Exception as e:
            self._last_account_call_ok = False
            logger.error(f"[KIS] 계좌 조회 예외: {e}")
            return AccountInfo(currency="KRW")
