"""
=============================================================================
utils/market.py - 시장 판별 및 환율 유틸리티
=============================================================================

종목 코드(symbol)에서 시장(한국/미국)을 판별하고,
USD ↔ KRW 환율 변환을 제공하는 공통 모듈입니다.

왜 필요한가:
- 시장 판별 로직이 paper_executor, run_bot, dashboard 등 10곳 이상에 산재
- 한 곳을 수정하면 다른 곳을 빠뜨리기 쉬움 (일관성 위험)
- 이 파일에서 통합 관리하면 수정 시 한 곳만 바꾸면 됨

환율 변환:
- 이 봇은 KRW 기반 자본금으로 미국 주식(USD)도 거래합니다.
- 미국 주식 매수 시 USD 가격 × 환율 → KRW로 변환 후 현금 차감
- 환율은 실시간 API로 조회하며, 실패 시 기본값(1,350원) 사용
- 캐싱: 1시간마다 갱신 (급변 시 수동 갱신 가능)

사용법:
    from utils.market import detect_market, is_kr_stock, get_exchange_rate, to_krw

    market = detect_market("005930.KS")  # "KR"
    market = detect_market("AAPL")       # "US"

    rate = get_exchange_rate()            # 예: 1370.5
    krw_price = to_krw("AAPL", 290.0)    # 290 * 1370.5 = 397,445
    krw_price = to_krw("005930.KS", 72000)  # 72000 (그대로)
=============================================================================
"""

import time
import logging
import threading

logger = logging.getLogger(__name__)

# ── 환율 캐시 (전역 싱글턴) ──
# 매 거래마다 API를 호출하면 느리고 rate-limit에 걸릴 수 있으므로
# 한 번 조회한 환율을 _FX_CACHE_TTL 동안 재사용합니다.
_fx_cache = {
    "rate": None,           # 캐시된 환율 (float)
    "updated_at": 0,        # 마지막 갱신 시각 (time.time())
}
_fx_lock = threading.Lock()

# 설정 상수
_FX_CACHE_TTL = 3600        # 캐시 유효 시간: 1시간 (초)
_FX_DEFAULT_RATE = 1_350.0  # API 실패 시 기본 환율 (안전한 보수적 값)
_FX_API_TIMEOUT = 5         # API 타임아웃 (초)


def get_exchange_rate(force_refresh: bool = False) -> float:
    """
    현재 USD/KRW 환율을 반환합니다.

    조회 우선순위:
    1. 캐시가 유효하면 (1시간 이내) 캐시값 반환
    2. Yahoo Finance API로 실시간 조회
    3. 실패 시 기본값 1,350원 반환

    Parameters:
        force_refresh: True이면 캐시 무시하고 새로 조회

    Returns:
        USD/KRW 환율 (예: 1370.5)
    """
    global _fx_cache

    now = time.time()

    # 1. 캐시 확인 (TTL 이내면 재사용)
    if not force_refresh and _fx_cache["rate"] is not None:
        age = now - _fx_cache["updated_at"]
        if age < _FX_CACHE_TTL:
            return _fx_cache["rate"]

    # 2. API 조회 (스레드 안전)
    with _fx_lock:
        # 다른 스레드가 이미 갱신했을 수 있으므로 다시 확인
        if not force_refresh and _fx_cache["rate"] is not None:
            age = now - _fx_cache["updated_at"]
            if age < _FX_CACHE_TTL:
                return _fx_cache["rate"]

        rate = _fetch_exchange_rate()
        if rate is not None:
            _fx_cache["rate"] = rate
            _fx_cache["updated_at"] = time.time()
            logger.info(f"[환율] USD/KRW = {rate:,.1f} (실시간 조회)")
            return rate

    # 3. API 실패 시: 이전 캐시값 또는 기본값
    if _fx_cache["rate"] is not None:
        logger.warning(
            f"[환율] API 실패 → 이전 캐시값 사용: {_fx_cache['rate']:,.1f}"
        )
        return _fx_cache["rate"]

    logger.warning(f"[환율] API 실패 → 기본값 사용: {_FX_DEFAULT_RATE:,.0f}")
    return _FX_DEFAULT_RATE


def _fetch_exchange_rate() -> float | None:
    """
    Yahoo Finance에서 USD/KRW 환율을 조회합니다.

    yfinance를 사용하여 USDKRW=X 티커의 최근 종가를 가져옵니다.
    실패 시 None을 반환합니다.
    """
    # 방법 1: yfinance (이미 프로젝트에서 사용 중)
    try:
        import yfinance as yf
        ticker = yf.Ticker("USDKRW=X")
        hist = ticker.history(period="1d")
        if not hist.empty:
            rate = float(hist["Close"].iloc[-1])
            # 환율이 비정상적이면 무시 (500~2000 범위)
            if 500 < rate < 2000:
                return rate
            else:
                logger.warning(f"[환율] 비정상 환율: {rate} → 무시")
    except Exception as e:
        logger.debug(f"[환율] yfinance 조회 실패: {e}")

    # 방법 2: urllib (외부 라이브러리 없이)
    try:
        import urllib.request
        import json as _json
        url = (
            "https://query1.finance.yahoo.com/v8/finance/chart/USDKRW=X"
            "?interval=1d&range=1d"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=_FX_API_TIMEOUT) as resp:
            data = _json.loads(resp.read().decode())
            price = data["chart"]["result"][0]["meta"]["regularMarketPrice"]
            rate = float(price)
            if 500 < rate < 2000:
                return rate
    except Exception as e:
        logger.debug(f"[환율] urllib 조회 실패: {e}")

    return None


def to_krw(symbol: str, amount: float) -> float:
    """
    종목의 통화에 맞게 금액을 KRW로 변환합니다.

    - 한국 주식 (KRW): 그대로 반환
    - 미국 주식 (USD): amount × 환율 → KRW

    Parameters:
        symbol: 종목 코드 (시장 판별에 사용)
        amount: 원본 금액 (해당 종목의 통화 기준)

    Returns:
        KRW 환산 금액
    """
    if is_kr_stock(symbol):
        return float(amount)
    return float(amount) * get_exchange_rate()


def from_krw(symbol: str, krw_amount: float) -> float:
    """
    KRW 금액을 종목의 원래 통화로 역변환합니다.

    - 한국 주식 (KRW): 그대로 반환
    - 미국 주식 (USD): krw_amount ÷ 환율 → USD

    Parameters:
        symbol: 종목 코드
        krw_amount: KRW 금액

    Returns:
        원래 통화 금액
    """
    if is_kr_stock(symbol):
        return float(krw_amount)
    return float(krw_amount) / get_exchange_rate()


def detect_market(symbol: str) -> str:
    """
    종목 코드에서 시장 판별

    한국 주식:
    - .KS (KOSPI, 코스피) 접미사
    - .KQ (KOSDAQ, 코스닥) 접미사
    - 6자리 순수 숫자코드 (예: "005930") — KIS API가 이 형식을 반환함

    미국 주식:
    - 영문 티커 (AAPL, MSFT 등)

    Parameters:
        symbol: 종목 코드 (예: "005930.KS", "AAPL", "005930")

    Returns:
        "KR" 또는 "US"
    """
    if symbol.endswith((".KS", ".KQ")):
        return "KR"
    # 6자리 순수 숫자코드는 한국 종목 (KIS API 반환 형식)
    code = symbol.split(".")[0]
    if code.isdigit() and len(code) == 6:
        return "KR"
    return "US"


def is_kr_stock(symbol: str) -> bool:
    """한국 주식인지 판별"""
    return detect_market(symbol) == "KR"


def is_us_stock(symbol: str) -> bool:
    """미국 주식인지 판별"""
    return detect_market(symbol) == "US"


def get_currency(symbol: str) -> str:
    """
    종목의 거래 통화 반환

    Parameters:
        symbol: 종목 코드

    Returns:
        "KRW" (한국) 또는 "USD" (미국)
    """
    return "KRW" if is_kr_stock(symbol) else "USD"


def get_position_attr(pos, attr: str, default=0):
    """
    Position 객체/딕셔너리에서 속성을 안전하게 꺼내는 유틸리티

    왜 필요한가:
    - PaperExecutor의 Position은 dataclass(속성 접근: pos.symbol)
    - DB에서 로드한 포지션은 dict(키 접근: pos["symbol"])
    - 코드 전체에 hasattr/getattr 분기가 반복 → 일관성 없음, 실수 유발
    - 이 함수 하나로 통일하면 어디서든 동일하게 동작

    Parameters:
        pos: Position 객체 또는 dict
        attr: 가져올 속성/키 이름
        default: 없을 때 반환값

    Returns:
        속성 값 또는 default
    """
    if isinstance(pos, dict):
        return pos.get(attr, default)
    return getattr(pos, attr, default)
