"""
=============================================================================
dashboard/app.py - 퀀트봇 웹 대시보드 서버
=============================================================================

Flask + Flask-SocketIO 기반 실시간 웹 대시보드입니다.
브라우저에서 봇 상태를 모니터링하고, 설정을 변경하고, 매매를 제어합니다.

주요 기능:
┌───────────────────────────────────────────────────────────┐
│ 1. 실시간 상태 모니터링 (WebSocket으로 1초마다 업데이트)  │
│ 2. 모의매매 ↔ 실거래 전환                                 │
│ 3. 전략 파라미터 설정 (자본금, 리스크, 종목 리스트 등)    │
│ 4. 거래 이력 조회                                         │
│ 5. 수익 차트 (equity curve)                               │
│ 6. 봇 시작/중지 제어                                      │
│ 7. 알림 설정 (텔레그램 토큰 등)                           │
└───────────────────────────────────────────────────────────┘

아키텍처:
    [브라우저] ←── WebSocket ──→ [Flask 서버] ←→ [QuantBot 인스턴스]
                                      ↕
                                 [SQLite DB]

사용법:
    python dashboard/app.py          # http://localhost:5000 에서 실행
    python dashboard/app.py --port 8080  # 포트 변경
=============================================================================
"""

import sys
import os
import json
import threading
import time
import numpy as np
from datetime import datetime, timedelta
from typing import Optional, Dict

# 프로젝트 루트를 path에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ─── API 키 로드 (사용자 친화적 API_KEYS.txt + .env 둘 다 지원) ───
# 우선순위: API_KEYS.txt > .env > 시스템 환경변수
# 사용자는 메모장으로 쉽게 편집 가능한 API_KEYS.txt를 사용
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_api_keys_txt(path: str):
    """
    API_KEYS.txt 파일 파싱하여 환경변수에 주입.

    형식 지원:
      KEY = 'value'
      KEY = "value"
      KEY=value
      KEY = ''        (빈 값은 무시 - 기존 환경변수 유지)

    한국어 주석/구분선은 자동 무시 (# 또는 ═ 시작 라인).
    """
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line_no, raw in enumerate(f, start=1):
                line = raw.strip()
                # 빈 줄, 주석, 구분선 무시
                if not line or line.startswith("#") or line.startswith("═"):
                    continue
                # KEY=VALUE 형식만 파싱
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                # 따옴표 제거 ('value' 또는 "value")
                if len(value) >= 2 and (
                    (value[0] == "'" and value[-1] == "'")
                    or (value[0] == '"' and value[-1] == '"')
                ):
                    value = value[1:-1]
                # 빈 값은 스킵 (사용자가 미입력)
                if not value:
                    continue
                # 유효한 키만 (영문 대문자 + 숫자 + 언더스코어)
                if not all(c.isalnum() or c == "_" for c in key):
                    continue
                os.environ[key] = value
    except Exception:
        pass  # 파일 형식 오류 시 silently skip (다른 소스에서 로드 시도)


# 1순위: API_KEYS.txt (사용자 친화적 메모장 파일)
_load_api_keys_txt(os.path.join(_project_root, "API_KEYS.txt"))

# 2순위: .env (기존 호환)
try:
    from dotenv import load_dotenv
    _env_path = os.path.join(_project_root, ".env")
    if os.path.exists(_env_path):
        # override=False: 이미 API_KEYS.txt에서 설정된 값은 유지
        load_dotenv(_env_path, override=False)
except ImportError:
    pass  # python-dotenv 미설치 시 환경변수만 사용

from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit

from config.settings import (
    Settings, CapitalConfig, RiskConfig, DashboardConfig,
    US_WATCHLIST, KR_WATCHLIST,
    SECTOR_UNIVERSE, AVAILABLE_SECTORS
)
from database.cache import DatabaseManager
from utils.logger import setup_logger
from utils.market import detect_market, is_kr_stock, is_us_stock, get_exchange_rate
from reporter.daily_report import DailyReportGenerator

# ── 대시보드 운영 파라미터 (매직넘버 중앙 관리) ──
_dash_cfg = DashboardConfig()

# ─── Flask 앱 초기화 ─────────────────────────────────────────────────────
app = Flask(
    __name__,
    template_folder="templates",
    static_folder="static"
)
# ★ SECRET_KEY: 하드코딩 대신 환경변수 또는 랜덤 생성
# 하드코딩된 키는 세션 위조(session forgery) 가능 → 보안 위험
app.config["SECRET_KEY"] = os.environ.get(
    "FLASK_SECRET_KEY",
    os.urandom(32).hex()  # 서버 시작마다 새로 생성 (개발용)
)
# 정적 파일(JS/CSS) 브라우저 캐시 비활성화 (개발 모드)
# 0초 = 매번 서버에서 최신 파일을 확인하도록 강제
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

# ── numpy 타입 안전 JSON 인코더 ──
# pandas/numpy 데이터가 WebSocket으로 전송될 때
# int64, float64 등이 json.dumps()에서 실패하는 것을 방지합니다.
class NumpySafeJSON(json.JSONEncoder):
    """numpy 타입을 Python native로 자동 변환하는 JSON 인코더"""
    def default(self, obj):
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, (np.bool_,)):
            return bool(obj)
        if isinstance(obj, bytes):
            return obj.decode('utf-8', errors='replace')
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)

# ── SocketIO가 사용할 커스텀 JSON 래퍼 ──
# socketio.emit()은 내부적으로 json.dumps()를 호출합니다.
# 이 래퍼를 전달하면 numpy 타입이 섞여 있어도 직렬화가 실패하지 않습니다.
class _SafeJSON:
    """SocketIO에 주입할 json 호환 모듈 래퍼"""
    @staticmethod
    def dumps(*args, **kwargs):
        kwargs.setdefault('cls', NumpySafeJSON)
        return json.dumps(*args, **kwargs)

    @staticmethod
    def loads(*args, **kwargs):
        return json.loads(*args, **kwargs)

# SocketIO: WebSocket 실시간 통신
# ★ CORS를 localhost만 허용 (같은 네트워크의 악성 페이지가 WebSocket 접근 차단)
_CORS_ORIGINS = os.environ.get(
    "CORS_ORIGINS",
    "http://localhost:5000,http://127.0.0.1:5000"
).split(",")
socketio = SocketIO(
    app,
    cors_allowed_origins=_CORS_ORIGINS,
    async_mode="threading",
    json=_SafeJSON,  # ★ numpy int64/float64 안전 직렬화
)

# ─── 전역 상태 ───────────────────────────────────────────────────────────
logger = setup_logger(level="INFO")

# 봇 인스턴스 (None = 아직 시작 안 됨)
bot_instance = None
bot_thread = None

# 디스코드 봇 인스턴스 (양방향 명령)
discord_bot_instance = None

# ── 봇 인스턴스 보호 Lock ──
# Flask는 멀티스레드로 요청을 처리하므로,
# /api/bot/start 와 /api/bot/stop 이 동시에 호출되면
# bot_instance가 두 번 생성되거나 불완전하게 파괴될 수 있음.
# 이 Lock은 봇 생성/파괴를 원자적으로 만든다.
_bot_lock = threading.Lock()

# ═══════════════════════════════════════════════════════════════════════════
# 설정 영속화 (JSON 파일)
# ═══════════════════════════════════════════════════════════════════════════
#
# 사용자가 대시보드에서 변경한 설정(관심종목, 자본금, 리스크 등)을
# config/user_settings.json 파일에 저장합니다.
# 서버를 재시작해도 마지막으로 저장한 설정이 자동으로 복원됩니다.
#
# 저장 시점:
#   - /api/settings POST (설정 저장 버튼 클릭) 시 자동 호출
#   - 관심종목 추가/제거 시 자동 호출
#
# 파일 위치: quant-bot/config/user_settings.json
# ═══════════════════════════════════════════════════════════════════════════

# 설정 파일 경로 (프로젝트 루트/config/user_settings.json)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SETTINGS_FILE = os.path.join(_PROJECT_ROOT, "config", "user_settings.json")


def _get_default_settings() -> dict:
    """
    기본 설정값 반환

    최초 실행이거나 설정 파일이 없을 때 사용되는 기본값입니다.
    이 값들은 코드에서만 정의되며, user_settings.json에 저장되면
    이후부터는 저장된 값이 우선 적용됩니다.
    """
    return {
        "capital": 10_000_000,           # 초기 자본금 (원)
        "currency": "KRW",               # 통화 단위
        "risk_per_trade": 0.02,          # 거래당 리스크 (2%)
        "max_position_size": 0.10,       # 최대 포지션 크기 (10%)
        "max_daily_loss": 0.03,          # 일일 최대 손실 (3%)
        "max_drawdown": 0.15,            # 최대 드로우다운 (15%)
        "stop_loss_atr_multiplier": 2.0, # 손절 ATR 배수
        "risk_reward_ratio": 2.0,        # 위험보상비율
        "sizing_method": "kelly",        # 포지션 사이징 방법
        "kelly_fraction": 0.5,           # 켈리 비율 (하프 켈리)
        "broker": "paper",               # 브로커 (paper/alpaca/kis)
        "live_mode": False,              # 실거래 모드 여부
        "us_watchlist": US_WATCHLIST.copy(),   # 미국 관심종목
        "kr_watchlist": KR_WATCHLIST.copy(),   # 한국 관심종목
        "telegram_token": "",            # 텔레그램 봇 토큰
        "telegram_chat_id": "",          # 텔레그램 채팅 ID
        "discord_webhook_url": "",       # 디스코드 웹훅 URL
        "discord_bot_token": "",         # 디스코드 봇 토큰 (양방향 명령용)
        "discord_bot_channel_id": "",    # 명령 허용 채널 ID (빈값이면 전체 허용)
        "discord_bot_autostart": False,  # 대시보드 시작 시 봇 자동 시작
        "dart_api_key": "",              # DART 공시 API 키
        # ── 브로커 API 키 (실거래용) ──
        "kis_app_key": "",               # 한국투자증권 APP KEY
        "kis_app_secret": "",            # 한국투자증권 APP SECRET
        "kis_account": "",               # 한국투자증권 계좌번호 (예: 50012345-01)
        "alpaca_api_key": "",            # Alpaca API Key
        "alpaca_secret_key": "",         # Alpaca Secret Key
        "analysis_interval": "60",       # 분석 주기 (분 단위)
        "schedule_kr_start": "09:05",    # 한국 시장 분석 시작 시간
        "schedule_kr_end": "15:20",      # 한국 시장 분석 종료 시간
        "schedule_us_start": "22:35",    # 미국 시장 분석 시작 시간 (한국시간)
        "schedule_us_end": "05:00",      # 미국 시장 분석 종료 시간
        "interest_sectors": ["semiconductor_ai", "bigtech_platform", "energy_battery"],
        # 종목 자동 발굴 설정
        "discovery_enabled": True,              # 자동 발굴 활성화
        "discovery_cycle_multiplier": 4,        # N번째 분석마다 발굴 실행
        "discovery_max_per_market": 10,         # 시장당 최대 발굴 수
        "discovery_max_watchlist": 35,          # 통합 워치리스트 최대 크기
        "discovery_include_movers": True,       # 시장 거래량 상위종목 포함
        # ── 포지션 유형 토글 (단타/스윙/장기) ──
        # OFF 시 해당 유형으로 분류되는 매수 신호는 차단됨
        # (단타+스윙+장기 모두 OFF면 매수 자체 안 됨)
        "position_type_short_enabled": True,    # 단타 (1~3일, 기술적 지배)
        "position_type_swing_enabled": True,    # 스윙 (1~4주, 혼합 신호)
        "position_type_long_enabled": True,     # 장기 (1개월+, 펀더멘탈 지배)
        # ── 분석 모듈 토글 (technical/factor/sentiment) ──
        # OFF 시 해당 모듈의 점수가 앙상블 계산에서 제외됨
        # 나머지 모듈들의 가중치가 자동 재정규화됨
        "module_technical_enabled": True,       # 기술적 분석 (RSI, MACD, BB 등)
        "module_factor_enabled": True,          # 팩터 분석 (Value, Quality, Momentum)
        "module_sentiment_enabled": True,       # 뉴스 감성 분석
        # ── 엄격 화이트리스트 모드 ──
        # ON: 사용자 워치리스트에 명시한 종목만 매수 가능
        #     (자동 발굴 종목 차단, 빈 워치리스트일 때 기본값 fallback 안 함)
        # OFF: 기본 동작 (워치리스트 + 자동발굴 + 보유종목)
        "watchlist_strict_mode": False,
        # ── 한국 시간외 거래 (KRX 전용) ──
        # OFF (기본): 정규장 09:00~15:30만 매매
        # ON: 봇이 정규장 외 시간대에도 적합한 주문 유형 자동 선택
        #     - 15:40~16:00 → AFTER_HOURS_CLOSE (종가 매매)
        #     - 16:00~18:00 → AFTER_HOURS_SINGLE (시간외 단일가, 전일 종가 ±10%)
        # ⚠️ 시간외는 유동성 낮음 + 슬리피지 큼 → 권장 OFF
        # 청산(매도) 전용으로만 쓰려면 "exit_only" 설정 가능
        "kr_after_hours_mode": "off",  # "off" / "exit_only" / "full"
    }


def _load_saved_settings() -> dict:
    """
    저장된 설정 파일(user_settings.json)에서 설정을 로드합니다.

    동작 흐름:
    1. 기본값으로 시작
    2. user_settings.json 파일이 있으면 읽어서 기본값에 덮어쓰기(merge)
    3. JSON이 손상되었으면 .corrupted-* 로 백업하고 기본값 사용 (CRITICAL 경고)
    4. 파일이 없으면 기본값 그대로 사용

    ⚠️ 실거래 안전성:
       JSON 손상 시 silent paper-mode fallback이 일어나면 사용자 자본을 위협하므로
       반드시 경고 로그를 띄우고, 백업 파일 위치를 안내합니다.

    merge 방식이므로, 새로운 설정 키가 코드에 추가되어도
    기존 저장 파일에 없는 키는 기본값이 자동 적용됩니다.
    """
    defaults = _get_default_settings()

    if not os.path.exists(_SETTINGS_FILE):
        logger.info("[설정] 저장된 설정 파일 없음 → 기본값 사용")
        return defaults

    # utils.atomic_io의 safe_load_json: 손상 시 .corrupted-* 로 자동 백업
    try:
        from utils.atomic_io import safe_load_json
        saved = safe_load_json(_SETTINGS_FILE, default=None, backup_on_corruption=True)
    except ImportError:
        # atomic_io 미사용 환경 fallback
        try:
            with open(_SETTINGS_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.error(
                f"⚠️ [설정] {_SETTINGS_FILE} 로드 실패 — 기본값 사용. "
                f"실거래 모드였다면 paper로 silent 전환되었을 수 있습니다. "
                f"원인: {e}"
            )
            return defaults

    if saved is None:
        logger.error(
            f"⚠️ [설정] {_SETTINGS_FILE} 손상 — 백업 후 기본값 사용. "
            f"실거래 중이었다면 paper로 silent 전환되었을 수 있으니 "
            f"대시보드 설정에서 다시 확인하세요."
        )
        return defaults

    # 기본값에 저장된 값을 덮어쓰기 (새 키는 기본값 유지)
    for key in defaults:
        if key in saved:
            defaults[key] = saved[key]

    logger.info(f"[설정] 저장된 설정 로드 완료: {_SETTINGS_FILE}")
    return defaults


def _save_settings_to_file():
    """
    현재 설정을 user_settings.json 파일에 원자적으로 저장합니다.

    안전성:
      - temp 파일 작성 후 os.replace()로 원자 교체
      - 쓰기 도중 크래시/전원차단 시에도 옛 파일 무사
      - 다중 스레드에서 동시 호출해도 파일별 락으로 직렬화

    호출 시점:
    - 설정 저장 API (/api/settings POST) 처리 후
    - 관심종목 변경 API 처리 후
    """
    try:
        from utils.atomic_io import atomic_write_json
        atomic_write_json(_SETTINGS_FILE, current_settings)
        logger.debug(f"[설정] 설정 파일 저장 완료 (atomic): {_SETTINGS_FILE}")
    except Exception as e:
        logger.warning(f"[설정] 설정 파일 저장 실패: {e}")


# 현재 설정 (대시보드에서 수정 가능, 서버 재시작 시 파일에서 복원)
current_settings = _load_saved_settings()


def _current_display_mode() -> str:
    """
    대시보드가 현재 표시할 모드 결정 ('paper' 또는 'live')

    우선순위:
      1. 봇이 실행 중이면 → bot_instance.executor.mode (실제 실행 모드)
      2. 봇이 중지 중이면 → current_settings의 live_mode + broker로 추정
         (paper broker → 'paper', kis/alpaca with live_mode=True → 'live')

    이렇게 결정한 모드로 모든 거래/PnL/포지션을 필터링하여
    대시보드가 paper와 live 데이터를 섞지 않도록 합니다.
    """
    try:
        if bot_instance is not None and hasattr(bot_instance, "executor"):
            exec_obj = bot_instance.executor
            if exec_obj is not None and hasattr(exec_obj, "mode"):
                return exec_obj.mode
    except Exception:
        pass

    # 봇 중지 중: settings 기준 추정
    broker = current_settings.get("broker", "paper")
    live = bool(current_settings.get("live_mode", False))
    if broker == "paper":
        return "paper"
    if broker in ("kis", "alpaca", "dual"):
        return "live" if live else "paper"
    return "paper"


def _derive_display_currency(broker: str, settings_currency: str = "KRW") -> str:
    """
    브로커에 따른 표시 통화 결정 (대시보드 $ vs ₩ 결정)

    - KIS (한국투자증권)  → KRW
    - Alpaca, Dual         → USD
    - paper 또는 미설정    → 설정값 (기본 KRW)
    """
    b = (broker or "").lower()
    if b == "kis":
        return "KRW"
    if b in ("alpaca", "dual"):
        return "USD"
    return (settings_currency or "KRW").upper()


# 봇 런타임 상태
# ★ bot_status는 분석 스레드 / 브로드캐스터 / Flask 라우트 / 스케줄러 등
# 여러 스레드에서 동시 접근하므로 _bot_status_lock으로 보호합니다.
# 단일 키 갱신은 GIL로 atomic이지만 다중 키 일괄 업데이트(positions 등)는 락 필요.
_bot_status_lock = threading.RLock()
bot_status = {
    "running": False,
    "mode": current_settings.get("broker", "paper"),
    "live": current_settings.get("live_mode", False),
    "currency": _derive_display_currency(
        current_settings.get("broker", "paper"),
        current_settings.get("currency", "KRW"),
    ),
    "started_at": None,
    "last_analysis": None,
    "total_equity": 0,
    "cash": 0,
    "positions": {},
    "daily_pnl": 0,
    "total_pnl": 0,
    "total_trades": 0,
    "win_rate": 0,
    "signals_today": [],
}

# ── 실시간 거래 감지를 위한 상태 추적 ──
# _status_broadcaster에서 trade_history 길이를 비교해 새 거래를 감지함
_last_trade_count = 0
_trade_count_lock = threading.Lock()  # ★ 동시 수정 방지
_last_snapshot_time = 0  # equity 스냅샷 마지막 저장 시각 (time.time())
_last_price_refresh = 0  # 보유 포지션 가격 새로고침 마지막 시각

# 시장 지수 캐시 (KOSPI, NASDAQ 등)
_market_indices_cache = {}
_market_indices_cache_time = 0

# ── KIS API 시세 조회용 싱글톤 ──
# 매번 토큰을 발급받지 않고 24시간 동안 1개 인스턴스를 재사용
# (토큰 발급 자체에 호출 한도가 있음)
_kis_price_client = None
_kis_price_client_lock = threading.Lock()


def _preflight_kis_credentials() -> dict:
    """
    봇 시작 전 KIS 자격증명 검증 (CRITICAL 안전장치)

    토큰 발급 + 잔고 조회를 둘 다 테스트해서 다음 mismatch를 미리 잡습니다:
      - EGW02007: 앱키가 모의/실거래 중 다른 모드용
      - EGW00121: 계좌번호 오류
      - EGW00201: 실전투자 API 미신청

    이렇게 안 하면 봇이 시작되어 분석은 계속 돌지만 모든 주문이 silent하게
    REJECTED → 사용자는 봇이 정상 작동한다고 착각함.

    Returns:
        {"ok": bool, "message": str, "code": str, "action": str}
    """
    app_key = os.environ.get("KIS_APP_KEY", "")
    app_secret = os.environ.get("KIS_APP_SECRET", "")
    account = os.environ.get("KIS_ACCOUNT", "")
    paper_str = os.environ.get("KIS_PAPER", "true").lower()
    paper = paper_str in ("true", "1", "yes")

    if not all([app_key, app_secret, account]):
        return {
            "ok": False,
            "message": "KIS_APP_KEY/SECRET/ACCOUNT 중 일부가 비어있습니다",
            "code": "MISSING_CREDS",
            "action": "API_KEYS.txt 또는 대시보드 설정에서 자격증명을 입력하세요",
        }

    # 토큰 발급 + 잔고 조회 시도
    try:
        # ★ 캐시 무효화 후 새로 만들기 (자격증명 검증용)
        invalidate_kis_price_client("preflight 검증")
        client = get_kis_price_client()
        if client is None:
            return {
                "ok": False,
                "message": "KIS 토큰 발급 실패",
                "code": "TOKEN_FAIL",
                "action": "API_KEYS.txt의 KIS_APP_KEY/SECRET을 다시 확인하세요",
            }

        # 잔고 조회 시도 — EGW02007 등을 잡는 핵심
        import requests as _rq
        tr_id = "VTTC8434R" if paper else "TTTC8434R"
        url = f"{client.base_url}/uapi/domestic-stock/v1/trading/inquire-balance"
        params = {
            "CANO": client.cano,
            "ACNT_PRDT_CD": client.acnt_prdt_cd,
            "AFHR_FLPR_YN": "N", "OFL_YN": "", "INQR_DVSN": "02",
            "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
        }
        headers = client._get_headers(tr_id)
        r = _rq.get(url, headers=headers, params=params, timeout=10)

        # ★ HTTP 200이든 500이든 본문을 파싱 (KIS는 4xx/5xx에도 JSON으로 msg_cd 반환)
        data = None
        try:
            data = r.json()
        except (ValueError, Exception):
            pass

        if data is None:
            return {
                "ok": False,
                "message": f"KIS 잔고 API HTTP {r.status_code} (JSON 파싱 실패)",
                "code": f"HTTP_{r.status_code}",
                "action": "KIS 서버 또는 네트워크 일시 장애 가능. 잠시 후 재시도하세요.",
            }

        rt_cd = data.get("rt_cd", "")
        msg_cd = data.get("msg_cd", "")
        msg1 = (data.get("msg1", "") or "").strip()

        if rt_cd == "0":
            return {"ok": True, "message": "정상", "code": "OK", "action": ""}

        # 오류별 친절한 안내
        if msg_cd == "EGW02007":
            opposite_paper = "true" if not paper else "false"
            return {
                "ok": False,
                "message": f"앱키-모드 불일치: {msg1}",
                "code": msg_cd,
                "action": (
                    f"현재 모드는 {'모의투자' if paper else '실거래'}인데 앱키는 반대 종류입니다. "
                    f"API_KEYS.txt에서 KIS_PAPER='{opposite_paper}'로 변경하거나 "
                    f"{'모의투자' if paper else '실거래'}용 앱키를 새로 발급받으세요."
                ),
            }
        if msg_cd == "EGW00121":
            return {
                "ok": False,
                "message": f"계좌번호 오류: {msg1}",
                "code": msg_cd,
                "action": "한국투자증권 앱에서 정확한 계좌번호를 확인 후 다시 입력하세요.",
            }
        if msg_cd == "EGW00201":
            return {
                "ok": False,
                "message": f"실전투자 API 미신청: {msg1}",
                "code": msg_cd,
                "action": "한국투자 앱 → 'Open API' 메뉴에서 실전투자 신청 후 재시도하세요.",
            }
        return {
            "ok": False,
            "message": f"KIS API 오류 [{msg_cd}]: {msg1}",
            "code": msg_cd,
            "action": "오류 코드를 한국투자증권 개발자 가이드에서 확인하세요.",
        }
    except Exception as e:
        return {
            "ok": False,
            "message": f"KIS 검증 중 예외: {e}",
            "code": "EXCEPTION",
            "action": "네트워크 연결 또는 KIS API 서버 상태를 확인하세요.",
        }


def invalidate_kis_price_client(reason: str = ""):
    """
    캐시된 KIS 시세 클라이언트를 무효화합니다.

    호출 시점:
      - KIS_PAPER 환경변수가 변경됐을 때 (모의↔실거래 전환)
      - live_mode 토글로 KIS_PAPER가 자동 변경됐을 때
      - 계좌번호/앱키가 변경됐을 때

    다음 get_kis_price_client() 호출 시 새 paper/live 모드로 재생성됩니다.
    """
    global _kis_price_client
    with _kis_price_client_lock:
        if _kis_price_client is not None:
            old_mode = "모의투자" if getattr(_kis_price_client, "paper", True) else "실거래"
            _kis_price_client = None
            logger.info(
                f"[KIS 시세] 클라이언트 무효화 (이전: {old_mode}){' — ' + reason if reason else ''}"
            )


def get_kis_price_client():
    """
    KIS 시세 조회용 싱글톤 클라이언트 반환

    .env에 KIS_APP_KEY/KIS_APP_SECRET가 설정되어 있으면
    1회 토큰을 발급받아 인스턴스를 캐싱하여 반환합니다.
    토큰 만료 시 자동 갱신됩니다.

    실거래/모의투자 구분: KIS_PAPER 환경변수 (기본 true=모의투자)

    안전장치: 캐시된 클라이언트의 paper 속성이 현재 환경변수와 다르면
    자동 무효화 후 재생성합니다 (런타임 모드 전환 대응).

    Returns:
        KISExecutor 인스턴스 또는 None (자격증명 없음/연결 실패)
    """
    global _kis_price_client

    # 현재 환경변수의 paper 상태
    paper_str = os.environ.get("KIS_PAPER", "true").lower()
    want_paper = paper_str in ("true", "1", "yes")

    # ── 빠른 경로 (race-safe) ──
    # 글로벌을 로컬 변수로 스냅샷한 뒤 비교 → invalidate가 끼어들어도
    # 우리는 일관된 인스턴스를 사용 (TOCTOU race 방지)
    snapshot = _kis_price_client
    if snapshot is not None and getattr(snapshot, "paper", True) == want_paper:
        return snapshot

    # 모드 불일치이거나 None — 락 잡고 재확인 + 재생성
    with _kis_price_client_lock:
        # 락 진입 후 다시 확인 (다른 스레드가 이미 만들었을 수 있음)
        if _kis_price_client is not None:
            if getattr(_kis_price_client, "paper", True) == want_paper:
                return _kis_price_client
            # 락 안에서 안전하게 무효화
            old_mode = "모의" if getattr(_kis_price_client, "paper", True) else "실거래"
            _kis_price_client = None
            logger.info(f"[KIS 시세] 모드 불일치 자동 무효화 (이전: {old_mode})")

        app_key = os.environ.get("KIS_APP_KEY", "")
        app_secret = os.environ.get("KIS_APP_SECRET", "")
        if not app_key or not app_secret:
            return None

        try:
            from executor.kis_executor import KISExecutor
            client = KISExecutor(paper=want_paper)
            if client.connect():
                _kis_price_client = client
                logger.info(
                    f"[KIS 시세] {'모의투자' if want_paper else '실거래'} 클라이언트 초기화 완료"
                )
                return client
            else:
                logger.warning("[KIS 시세] 토큰 발급 실패 - yfinance로 fallback")
                return None
        except Exception as e:
            logger.warning(f"[KIS 시세] 초기화 실패: {e} - yfinance로 fallback")
            return None

# ── 일일 보고서 생성기 ──
# DailyReportGenerator: 봇 중지 시 또는 수동 API 호출 시 일일 보고서 HTML 생성
# reports/ 폴더에 daily_YYYY-MM-DD.html 형태로 저장됨
_report_generator = DailyReportGenerator()

# ── .env 파일 저장 (텔레그램/디스코드 토큰 영속화) ──

def _save_env_file():
    """
    텔레그램/디스코드/DART/KIS/Alpaca API 키를 .env 파일에 원자적으로 저장합니다.

    안전성 보장:
      - 기존 파일의 알 수 없는 키는 보존 (FRED_API_KEY, 사용자 추가 키 등)
      - 빈 값은 기존 값을 덮어쓰지 않음 (사용자가 실수로 비웠을 때 보호)
      - 원자적 쓰기로 크래시 시 파일 손상 방지
    """
    env_path = os.path.join(_PROJECT_ROOT, ".env")
    env_lines = {}  # 보존할 모든 키 = 기존 파일 키 + 대시보드 업데이트

    # 1. 기존 .env 파일 읽기 → 모든 키 보존 (알 수 없는 키도 유지)
    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if "=" in line and not line.startswith("#"):
                        key, val = line.split("=", 1)
                        env_lines[key.strip()] = val.strip()
        except OSError as e:
            logger.warning(f"[설정] 기존 .env 읽기 실패 (덮어쓰기로 진행): {e}")

    # 2. 현재 설정의 값만 덮어쓰기 (빈 문자열은 무시 — 사용자 실수 보호)
    _key_map = {
        "telegram_token":      "TELEGRAM_TOKEN",
        "telegram_chat_id":    "TELEGRAM_CHAT_ID",
        "discord_webhook_url": "DISCORD_WEBHOOK_URL",
        "discord_bot_token":   "DISCORD_BOT_TOKEN",
        "dart_api_key":        "DART_API_KEY",
        "fred_api_key":        "FRED_API_KEY",
        "kis_app_key":         "KIS_APP_KEY",
        "kis_app_secret":      "KIS_APP_SECRET",
        "kis_account":         "KIS_ACCOUNT",
        "alpaca_api_key":      "ALPACA_API_KEY",
        "alpaca_secret_key":   "ALPACA_SECRET_KEY",
    }
    for setting_key, env_key in _key_map.items():
        val = current_settings.get(setting_key, "")
        if val:  # 빈 값은 기존 키를 덮어쓰지 않음
            env_lines[env_key] = val

    # ── ★ KIS_PAPER 동기화 (live_mode와 1:1 매핑) ──
    # 대시보드 토글이 .env / API_KEYS.txt에 영속화됨. 빈 값 보호 로직과 무관하게
    # KIS_PAPER는 명시적 bool이므로 항상 업데이트.
    if current_settings.get("broker") in ("kis", "dual"):
        env_lines["KIS_PAPER"] = "false" if current_settings.get("live_mode") else "true"

    # 3. 원자적 쓰기 (temp → rename)
    try:
        from utils.atomic_io import atomic_write_text
        content = "\n".join(f"{k}={v}" for k, v in env_lines.items()) + "\n"
        atomic_write_text(env_path, content)
        logger.debug(f"[설정] .env 원자적 저장 완료 ({len(env_lines)}개 키)")
    except Exception as e:
        logger.warning(f"[설정] .env 파일 저장 실패: {e}")

    # ── ★ API_KEYS.txt도 동기화 (사용자 친화 파일) ──
    # API_KEYS.txt는 .env보다 우선 로드되므로, 대시보드 입력값이 환경변수에
    # 정확히 반영되려면 API_KEYS.txt도 함께 업데이트해야 함.
    _save_api_keys_file(env_lines)


def _save_api_keys_file(env_dict: dict):
    """
    API_KEYS.txt 파일 업데이트 (대시보드 입력값과 동기화)

    기존 파일의 형식(주석/구분선)을 보존하면서 KEY = 'value' 라인만 교체합니다.
    파일이 없으면 .example 템플릿에서 복사 후 적용합니다.
    """
    keys_path = os.path.join(_PROJECT_ROOT, "API_KEYS.txt")
    example_path = os.path.join(_PROJECT_ROOT, "API_KEYS.txt.example")

    # 파일이 없으면 템플릿에서 복사
    if not os.path.exists(keys_path):
        if os.path.exists(example_path):
            import shutil
            shutil.copy(example_path, keys_path)
        else:
            return  # 템플릿도 없으면 스킵

    try:
        with open(keys_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        # 각 라인 처리: KEY = 'value' 형식만 교체
        updated_keys = set()
        new_lines = []
        for line in lines:
            stripped = line.strip()
            # 주석/구분선/빈 줄은 그대로 유지
            if not stripped or stripped.startswith("#") or stripped.startswith("═"):
                new_lines.append(line)
                continue
            # KEY = 'value' 패턴
            if "=" in stripped:
                key_part, _, _ = stripped.partition("=")
                key = key_part.strip()
                # 환경변수 사전에 있는 키만 교체
                if key in env_dict and all(c.isalnum() or c == "_" for c in key):
                    # 들여쓰기 보존
                    indent = line[:len(line) - len(line.lstrip())]
                    new_lines.append(f"{indent}{key} = '{env_dict[key]}'\n")
                    updated_keys.add(key)
                    continue
            new_lines.append(line)

        # 파일에 없던 새 키들은 맨 끝에 추가
        appended = []
        for key, val in env_dict.items():
            if key not in updated_keys and val:
                appended.append(f"{key} = '{val}'\n")
        if appended:
            new_lines.append("\n# 대시보드에서 추가된 키\n")
            new_lines.extend(appended)

        # ★ 원자적 쓰기: temp 파일에 다 쓰고 rename → 크래시 시 파일 손상 방지
        from utils.atomic_io import atomic_write_text
        atomic_write_text(keys_path, "".join(new_lines))
        logger.debug(f"[설정] API_KEYS.txt 원자적 저장 완료 ({len(updated_keys)}개 키 업데이트)")
    except Exception as e:
        logger.warning(f"[설정] API_KEYS.txt 저장 실패: {e}")


# ── 실시간 활동 로그 (프론트엔드 피드에 표시) ──
# 최대 50개까지 유지. 봇 분석/매수/매도 등 주요 이벤트를 기록
_activity_log = []
_activity_log_lock = threading.Lock()

def _add_activity(action: str, detail: str, level: str = "info"):
    """
    봇 활동을 로그에 추가하고 WebSocket으로 실시간 전송

    Parameters:
        action: 활동 종류 ("analyzing", "buy", "sell", "signal", "risk_check", "start", "stop")
        detail: 상세 설명 텍스트
        level: "info", "success", "warning", "danger" 중 하나 (UI 색상 결정)
    """
    entry = {
        "action": action,
        "detail": detail,
        "level": level,
        "timestamp": datetime.now().isoformat(),
    }
    with _activity_log_lock:
        _activity_log.append(entry)
        # 최대 N개만 유지 (오래된 것부터 제거)
        if len(_activity_log) > _dash_cfg.activity_log_max:
            _activity_log[:] = _activity_log[-_dash_cfg.activity_log_max:]
    # WebSocket으로 즉시 브로드캐스트
    try:
        socketio.emit("bot_activity", entry)
    except Exception:
        pass


# =============================================================================
# 라우트 (페이지)
# =============================================================================

@app.route("/")
def index():
    """
    메인 대시보드 페이지

    cache_bust: 서버 시작 시각 기반 타임스탬프를 전달하여
    브라우저가 항상 최신 JS/CSS를 로드하도록 강제합니다.
    (개발 중 캐시 문제 방지)
    """
    import time as _time
    return render_template("index.html", cache_bust=int(_time.time()))


# =============================================================================
# REST API 엔드포인트
# =============================================================================

@app.route("/api/status")
def api_status():
    """
    현재 봇 상태 반환 (thread-safe)

    bot_status는 분석 스레드/브로드캐스터/스케줄러에서 동시 갱신되므로
    JSON 직렬화 직전에 락으로 스냅샷을 떠서 일관된 상태를 반환합니다.
    """
    with _bot_status_lock:
        # 얕은 복사로 충분 (값들은 모두 기본 타입 또는 dict)
        snapshot = dict(bot_status)
        # 중첩 dict (positions)도 복사
        if "positions" in snapshot:
            snapshot["positions"] = dict(snapshot["positions"])
        if "signals_today" in snapshot:
            snapshot["signals_today"] = list(snapshot["signals_today"])
    return jsonify(snapshot)


@app.route("/api/settings", methods=["GET"])
def api_get_settings():
    """현재 설정 조회"""
    return jsonify(current_settings)


@app.route("/api/settings", methods=["POST"])
def api_save_settings():
    """
    설정 저장 (JSON body)

    프론트엔드에서 설정 폼을 제출하면 여기로 옵니다.
    봇이 실행 중이면 일부 설정은 즉시 반영, 일부는 재시작 필요.

    ⚠️ 안전 정책 (CRITICAL — Phase 2):
      봇이 실행 중일 때 live_mode 또는 broker 변경은 거부됩니다 (409).
      이유: 봇의 executor 인스턴스는 시작 시점의 broker로 고정되어 있어
      런타임에 paper↔live 전환 시 분석 사이클 중간에 잘못된 서버로 주문이 갈 수 있습니다.
      사용자는 "봇 중지 → 설정 변경 → 봇 시작" 순서로 명시적으로 진행해야 합니다.
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data"}), 400

    # ── 분석 간격 변경 감지 (봇 실행 중 스케줄러 동적 업데이트용) ──
    old_interval = current_settings.get("analysis_interval", "60")
    new_interval = data.get("analysis_interval", old_interval)
    interval_changed = (str(old_interval) != str(new_interval))

    # ── ★ 변경 감지 (이전 값 스냅샷) ──
    old_live_mode = bool(current_settings.get("live_mode", False))
    old_kis_account = current_settings.get("kis_account", "")
    old_kis_app_key = current_settings.get("kis_app_key", "")
    old_kis_app_secret = current_settings.get("kis_app_secret", "")
    old_broker = current_settings.get("broker", "paper")

    # ── ★ 봇 실행 중 모드 전환 차단 (CRITICAL 안전장치) ──
    # 봇의 broker 인스턴스는 시작 시점에 paper/live가 고정되므로 런타임에 바꿀 수 없음.
    # 사용자가 모드를 바꾸려면 반드시: 봇 중지 → 모드 변경 → 봇 재시작
    requested_live_mode = data.get("live_mode")
    requested_broker = data.get("broker")
    if bot_status.get("running"):
        if requested_live_mode is not None and bool(requested_live_mode) != old_live_mode:
            return jsonify({
                "error": "봇 실행 중에는 실거래/모의 모드를 변경할 수 없습니다",
                "detail": (
                    "안전을 위해 먼저 '봇 중지'를 누른 후 모드를 변경하고 "
                    "다시 시작해 주세요. 실행 중 모드 전환은 분석 사이클이 "
                    "잘못된 서버로 주문할 위험이 있습니다."
                ),
                "action_required": "stop_bot_first",
            }), 409
        if requested_broker and requested_broker != old_broker:
            return jsonify({
                "error": "봇 실행 중에는 브로커를 변경할 수 없습니다",
                "detail": "먼저 봇을 중지한 후 브로커를 변경하고 다시 시작해 주세요.",
                "action_required": "stop_bot_first",
            }), 409

    # 설정 업데이트
    for key in current_settings:
        if key in data:
            current_settings[key] = data[key]

    new_live_mode = bool(current_settings.get("live_mode", False))
    new_broker = current_settings.get("broker", "paper")
    new_kis_account = current_settings.get("kis_account", "")
    new_kis_app_key = current_settings.get("kis_app_key", "")
    new_kis_app_secret = current_settings.get("kis_app_secret", "")

    # ── ★ KIS 자격증명을 os.environ에 즉시 반영 (대시보드 입력 우선) ──
    # 사용자가 대시보드 설정 탭에서 입력한 값이 즉시 환경변수에 반영되어
    # 다음 KIS API 호출부터 새 값으로 동작합니다.
    if new_kis_app_key:
        os.environ["KIS_APP_KEY"] = new_kis_app_key
    if new_kis_app_secret:
        os.environ["KIS_APP_SECRET"] = new_kis_app_secret
    if new_kis_account:
        os.environ["KIS_ACCOUNT"] = new_kis_account

    # ── ★ live_mode ↔ KIS_PAPER 양방향 자동 동기화 ──
    # 사용자가 대시보드에서 "실거래" 토글을 켜면 KIS_PAPER='false'로 변경되고
    # 캐시된 KIS 클라이언트가 무효화되어 다음 호출 시 실거래 서버로 재연결됩니다.
    kis_paper_changed = False
    if old_live_mode != new_live_mode and new_broker in ("kis", "dual"):
        new_kis_paper = "false" if new_live_mode else "true"
        old_kis_paper_env = os.environ.get("KIS_PAPER", "true").lower()
        if old_kis_paper_env != new_kis_paper:
            os.environ["KIS_PAPER"] = new_kis_paper
            kis_paper_changed = True
            logger.info(
                f"[모드 동기화] live_mode={new_live_mode} → "
                f"KIS_PAPER='{new_kis_paper}' ({'실거래' if new_live_mode else '모의투자'} 서버)"
            )

    # ── ★ KIS 클라이언트 무효화 (자격증명/모드 중 하나라도 변경됐으면) ──
    # 캐시된 클라이언트는 이전 자격증명/모드로 동작하므로 반드시 폐기 → 재생성
    needs_invalidate = (
        kis_paper_changed
        or (old_kis_account != new_kis_account and new_kis_account)
        or (old_kis_app_key != new_kis_app_key and new_kis_app_key)
        or (old_kis_app_secret != new_kis_app_secret and new_kis_app_secret)
        or (old_broker != new_broker)
    )
    if needs_invalidate:
        reasons = []
        if kis_paper_changed:
            reasons.append("live_mode 토글")
        if old_kis_account != new_kis_account and new_kis_account:
            reasons.append("계좌번호 변경")
        if old_kis_app_key != new_kis_app_key and new_kis_app_key:
            reasons.append("APP_KEY 변경")
        if old_kis_app_secret != new_kis_app_secret and new_kis_app_secret:
            reasons.append("APP_SECRET 변경")
        if old_broker != new_broker:
            reasons.append(f"브로커 변경 ({old_broker}→{new_broker})")
        invalidate_kis_price_client(", ".join(reasons))

    # .env 파일에 텔레그램 정보 저장
    _save_env_file()

    # ── 봇 실행 중이면 분석 간격 즉시 반영 ──
    # 스케줄러에 등록된 분석 작업의 주기를 동적으로 변경합니다.
    # 봇 재시작 없이도 설정 탭에서 변경한 간격이 바로 적용됩니다.
    reschedule_msg = ""
    if interval_changed and bot_status["running"] and bot_instance:
        try:
            new_minutes = _parse_interval(new_interval)
            _reschedule_analysis(new_minutes)
            reschedule_msg = f" (분석 간격 {new_minutes}분으로 즉시 변경됨)"
            _add_activity("settings", f"분석 간격 → {new_minutes}분 변경 적용", "info")
            logger.info(f"[대시보드] 분석 간격 동적 변경: {old_interval} → {new_interval} ({new_minutes}분)")
        except Exception as e:
            logger.warning(f"[대시보드] 분석 간격 동적 변경 실패 (재시작 필요): {e}")
            reschedule_msg = " (간격 변경은 봇 재시작 후 적용됩니다)"

    # ── 설정을 파일에 영속화 (서버 재시작 후에도 유지) ──
    _save_settings_to_file()

    logger.info(f"[대시보드] 설정 업데이트 + 파일 저장: {json.dumps(data, ensure_ascii=False)}{reschedule_msg}")
    return jsonify({"success": True, "settings": current_settings, "message": reschedule_msg})


@app.route("/api/bot/start", methods=["POST"])
def api_start_bot():
    """
    봇 시작

    현재 설정으로 QuantBot을 생성하고 별도 스레드에서 실행합니다.
    이미 실행 중이면 에러 반환.
    """
    global bot_instance, bot_thread

    # ★ Lock으로 start/stop 동시 호출 방지
    if not _bot_lock.acquire(blocking=False):
        return jsonify({"error": "다른 작업이 진행 중입니다"}), 409

    try:
        if bot_status["running"]:
            return jsonify({"error": "Bot is already running"}), 400

        # 설정 객체 생성
        settings = Settings(
            capital=CapitalConfig(
                total_capital=current_settings["capital"],
                currency=current_settings["currency"]
            ),
            risk=RiskConfig(
                risk_per_trade=current_settings["risk_per_trade"],
                max_position_size=current_settings["max_position_size"],
                max_daily_loss=current_settings["max_daily_loss"],
                max_drawdown=current_settings["max_drawdown"],
                stop_loss_atr_multiplier=current_settings["stop_loss_atr_multiplier"],
                risk_reward_ratio=current_settings["risk_reward_ratio"],
                sizing_method=current_settings["sizing_method"],
                kelly_fraction=current_settings["kelly_fraction"],
            )
        )

        # 종목 발굴 설정 반영
        from config.settings import (
            DiscoveryConfig, EnsembleConfig, PositionTypeConfig, WatchlistConfig,
        )
        settings.discovery = DiscoveryConfig(
            enabled=current_settings.get("discovery_enabled", True),
            cycle_multiplier=current_settings.get("discovery_cycle_multiplier", 4),
            max_discovered_per_market=current_settings.get("discovery_max_per_market", 10),
            max_total_watchlist=current_settings.get("discovery_max_watchlist", 35),
            include_market_movers=current_settings.get("discovery_include_movers", True),
        )

        # ── 분석 모듈 enable/disable 토글 반영 ──
        # 비활성 모듈은 ensemble.combine()에서 자동 제외 + 가중치 재정규화
        settings.ensemble = EnsembleConfig(
            technical_enabled=current_settings.get("module_technical_enabled", True),
            factor_enabled=current_settings.get("module_factor_enabled", True),
            sentiment_enabled=current_settings.get("module_sentiment_enabled", True),
        )

        # ── 포지션 유형 enable/disable 토글 반영 ──
        # 비활성 유형으로 분류된 매수 신호는 차단됨
        settings.position_types = PositionTypeConfig(
            short_enabled=current_settings.get("position_type_short_enabled", True),
            swing_enabled=current_settings.get("position_type_swing_enabled", True),
            long_enabled=current_settings.get("position_type_long_enabled", True),
        )

        # ── 엄격 화이트리스트 모드 반영 ──
        # ON: 워치리스트에 명시한 종목만 매수 가능 (자동발굴 제외)
        settings.watchlist = WatchlistConfig(
            strict_mode=current_settings.get("watchlist_strict_mode", False),
        )

        # ── 브로커 API 키를 환경변수에 설정 (실거래 시 필요) ──
        # 대시보드에서 입력한 API 키를 os.environ에 반영하여
        # KISExecutor/AlpacaExecutor가 자동으로 읽을 수 있게 함
        _broker_env_keys = {
            "KIS_APP_KEY": current_settings.get("kis_app_key", ""),
            "KIS_APP_SECRET": current_settings.get("kis_app_secret", ""),
            "KIS_ACCOUNT": current_settings.get("kis_account", ""),
            "ALPACA_API_KEY": current_settings.get("alpaca_api_key", ""),
            "ALPACA_SECRET_KEY": current_settings.get("alpaca_secret_key", ""),
        }
        for env_key, env_val in _broker_env_keys.items():
            if env_val:
                os.environ[env_key] = env_val

        # ── ★ KIS_PAPER ↔ live_mode 양방향 자동 동기화 ──
        # 두 설정이 어긋나면 사용자 혼란 (실거래 표시인데 모의 서버 사용 등).
        # 정책: 대시보드의 live_mode를 진실의 원천(Source of Truth)으로 삼고
        # KIS_PAPER 환경변수를 거기에 맞춰 강제 동기화합니다.
        _kis_paper_env = os.environ.get("KIS_PAPER", "true").lower()
        _kis_is_live_env = _kis_paper_env in ("false", "0", "no")
        _broker = current_settings["broker"]
        _live_mode = bool(current_settings.get("live_mode", False))

        if _broker in ("kis", "dual"):
            # ① 대시보드 live_mode=True인데 KIS_PAPER=true → KIS_PAPER='false'로 강제
            if _live_mode and not _kis_is_live_env:
                os.environ["KIS_PAPER"] = "false"
                invalidate_kis_price_client("봇 시작 시 live_mode=True 동기화")
                logger.info(
                    "[모드 동기화] live_mode=True → KIS_PAPER='false' 강제 (실거래 서버)"
                )
                # API_KEYS.txt / .env 영속화
                _save_env_file()
            # ② KIS_PAPER=false인데 live_mode=False → live_mode=True로 자동 전환
            elif _kis_is_live_env and not _live_mode:
                logger.info(
                    "[모드 동기화] KIS_PAPER=false → live_mode=True 자동 전환"
                )
                current_settings["live_mode"] = True
                _save_settings_to_file()
            # ③ live_mode=False이고 KIS_PAPER=true → 정상 (모의)
            # ④ live_mode=True이고 KIS_PAPER=false → 정상 (실거래)

            # ── ★ Pre-flight: KIS 자격증명 ↔ 모드 일치 검증 ──
            # EGW02007 ("해당 앱키는 모의투자용 앱키가 아닙니다") 같은 mismatch는
            # 토큰은 발급되지만 잔고/주문이 전부 실패하는 silent-failure 패턴이라
            # 시작 전에 미리 잡아야 합니다.
            _kis_creds_ok = _preflight_kis_credentials()
            if not _kis_creds_ok["ok"]:
                bot_status["running"] = False
                return jsonify({
                    "error": "KIS 자격증명 검증 실패",
                    "detail": _kis_creds_ok.get("message", "알 수 없는 오류"),
                    "code": _kis_creds_ok.get("code", ""),
                    "action_required": _kis_creds_ok.get("action", ""),
                }), 400

        # QuantBot 임포트 및 생성
        from run_bot import QuantBot
        bot_instance = QuantBot(
            settings=settings,
            broker=current_settings["broker"],
            live=current_settings["live_mode"]
        )

        # 상태 업데이트 (스레드 시작 전에 먼저 설정 → UI 즉시 반응)
        bot_status["running"] = True
        bot_status["mode"] = current_settings["broker"]
        bot_status["live"] = current_settings["live_mode"]
        bot_status["currency"] = _derive_display_currency(
            current_settings["broker"],
            current_settings.get("currency", "KRW"),
        )
        bot_status["started_at"] = datetime.now().isoformat()
        bot_status["total_equity"] = current_settings["capital"]
        bot_status["cash"] = current_settings["capital"]

        # 별도 스레드에서 봇 실행 (_run_bot_thread가 직접 connect/schedule/분석 담당)
        bot_thread = threading.Thread(target=_run_bot_thread, daemon=True)
        bot_thread.start()

        # 거래 감지 카운터는 _run_bot_thread 내에서 connect() 직후에 동기화
        # (과거 DB 복원 거래가 "새 거래"로 알림되는 것 방지)

        logger.info(f"[대시보드] 봇 시작 - 모드: {current_settings['broker']}, "
                    f"실거래: {current_settings['live_mode']}")
        return jsonify({"success": True, "status": bot_status})

    except Exception as e:
        logger.error(f"[대시보드] 봇 시작 실패: {e}")
        bot_status["running"] = False
        return jsonify({"error": str(e)}), 500
    finally:
        _bot_lock.release()


@app.route("/api/broker/test", methods=["POST"])
def api_test_broker():
    """
    브로커 API 연결 테스트

    현재 설정된 API 키로 KIS/Alpaca에 연결을 시도하고
    성공 여부를 반환합니다. 실제 주문은 하지 않습니다.
    """
    broker = current_settings.get("broker", "paper")
    result = {"success": True, "kr_status": False, "us_status": False,
              "kr_connected": False, "us_connected": False,
              "kr_error": "", "us_error": ""}

    if broker == "paper":
        return jsonify({"success": True, "message": "Paper 모드는 연결 테스트가 필요 없습니다."})

    # ── API 키를 환경변수에 설정 ──
    _env_map = {
        "KIS_APP_KEY": current_settings.get("kis_app_key", ""),
        "KIS_APP_SECRET": current_settings.get("kis_app_secret", ""),
        "KIS_ACCOUNT": current_settings.get("kis_account", ""),
        "ALPACA_API_KEY": current_settings.get("alpaca_api_key", ""),
        "ALPACA_SECRET_KEY": current_settings.get("alpaca_secret_key", ""),
    }
    for k, v in _env_map.items():
        if v:
            os.environ[k] = v

    # ── KIS 테스트 ──
    if broker in ("kis", "dual"):
        result["kr_status"] = True
        try:
            from executor.kis_executor import KISExecutor
            kis = KISExecutor(paper=True)  # 항상 모의투자로 테스트
            if kis.connect():
                result["kr_connected"] = True
            else:
                result["kr_error"] = "API 키 또는 계좌번호를 확인하세요"
        except Exception as e:
            result["kr_error"] = str(e)[:100]

    # ── Alpaca 테스트 ──
    if broker in ("alpaca", "dual"):
        result["us_status"] = True
        try:
            from executor.alpaca_executor import AlpacaExecutor
            alp = AlpacaExecutor(paper=True)  # 항상 Paper로 테스트
            if alp.connect():
                result["us_connected"] = True
            else:
                result["us_error"] = "API 키를 확인하세요 (alpaca.markets → API Keys)"
        except Exception as e:
            result["us_error"] = str(e)[:100]

    return jsonify(result)


@app.route("/api/bot/stop", methods=["POST"])
def api_stop_bot():
    """
    봇 중지 + 일일 보고서 자동 생성

    안전성 강화 (Phase 2):
      1. _bot_lock으로 start/stop 동시 호출 방지
      2. bot_instance.stop() 호출 후 bot_thread.join(timeout=30)으로 분석 사이클 완료 대기
      3. join 타임아웃 시 강제 종료가 아닌 경고만 로깅 (thread는 daemon)
      4. join 완료 후에야 bot_instance=None 처리 → 다른 곳에서 NPE 방지
      5. KIS 클라이언트 캐시도 무효화 (다음 시작 시 깔끔하게 재연결)
    """
    global bot_instance, bot_thread

    # ★ Lock으로 start/stop 동시 호출 방지
    if not _bot_lock.acquire(blocking=False):
        return jsonify({"error": "다른 작업이 진행 중입니다"}), 409

    try:
        if not bot_status["running"]:
            return jsonify({"error": "Bot is not running"}), 400

        # ── 봇 중지 전에 일일 보고서 생성 ──
        # 중지 시점의 거래 이력/포지션/계좌 정보를 스냅샷으로 보고서에 기록
        report_path = None
        if bot_instance:
            try:
                report_path = _generate_daily_report()
                if report_path:
                    _add_activity("analyzing", f"일일 보고서 생성 완료", "success")
                    logger.info(f"[대시보드] 일일 보고서 생성: {report_path}")
            except Exception as re:
                logger.warning(f"[대시보드] 보고서 생성 실패 (무시): {re}")

        # ── 1단계: 봇에 정지 신호 송신 ──
        if bot_instance:
            try:
                bot_instance.stop()
            except Exception as e:
                logger.warning(f"[대시보드] bot_instance.stop() 예외: {e}")

        # ── 2단계: 분석 루프가 사이클을 마칠 때까지 대기 (최대 30초) ──
        # 이렇게 안 하면 분석 중간에 bot_instance=None이 되어
        # 다음 코드가 AttributeError로 죽거나 부분 거래로 끝납니다.
        if bot_thread and bot_thread.is_alive():
            logger.info("[대시보드] 봇 스레드 종료 대기 (최대 30초)...")
            bot_thread.join(timeout=30)
            if bot_thread.is_alive():
                logger.warning(
                    "[대시보드] ⚠️ 봇 스레드가 30초 내 종료되지 않음. "
                    "분석 루프가 멈춰있을 수 있습니다."
                )

        # ── 3단계: 인스턴스 정리 + KIS 캐시 무효화 ──
        bot_instance = None
        bot_thread = None
        try:
            invalidate_kis_price_client("봇 중지 시 캐시 정리")
        except Exception:
            pass

        bot_status["running"] = False
        _add_activity("stop", "봇이 중지되었습니다", "warning")
        logger.info("[대시보드] 봇 중지 완료")

        result = {"success": True}
        if report_path:
            result["report"] = report_path
        return jsonify(result)

    except Exception as e:
        logger.error(f"[대시보드] 봇 중지 실패: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        _bot_lock.release()


@app.route("/api/report/generate", methods=["POST"])
def api_generate_report():
    """
    일일 보고서 수동 생성 API

    봇이 실행 중일 때 수동으로 보고서를 생성합니다.
    대시보드 UI의 '보고서 생성' 버튼에서 호출됩니다.
    """
    try:
        report_path = _generate_daily_report()
        if report_path:
            return jsonify({"success": True, "path": report_path})
        else:
            return jsonify({"error": "보고서 생성에 필요한 데이터가 없습니다"}), 400
    except Exception as e:
        logger.error(f"[대시보드] 보고서 생성 실패: {e}")
        return jsonify({"error": str(e)}), 500




@app.route("/api/report/market", methods=["POST"])
def api_generate_market_report():
    """
    시장 동향 보고서 생성 API

    주요 지수 + 섹터 + 거시경제 + 뉴스를 종합 분석한
    시장 보고서를 별도 HTML 파일로 생성합니다.
    대시보드 UI의 '시장 보고서 생성' 버튼에서 호출됩니다.
    """
    try:
        from reporter.market_report import MarketReportGenerator
        gen = MarketReportGenerator()
        filename = gen.generate(settings=current_settings)
        logger.info(f"[대시보드] 시장 보고서 생성 완료: {filename}")
        return jsonify({"success": True, "filename": filename})
    except Exception as e:
        logger.error(f"[대시보드] 시장 보고서 생성 실패: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/reports")
def api_list_reports():
    """
    저장된 보고서 목록 조회 API

    reports/ 폴더에 있는 HTML 보고서 파일 목록을 반환합니다.
    최신순으로 정렬되어 반환됩니다.
    """
    try:
        reports_dir = _report_generator.reports_dir
        if not os.path.exists(reports_dir):
            return jsonify({"reports": []})

        files = []
        for f in os.listdir(reports_dir):
            if f.endswith(".html") and (f.startswith("daily_") or f.startswith("market_")):
                filepath = os.path.join(reports_dir, f)
                stat = os.stat(filepath)
                files.append({
                    "filename": f,
                    "date": f.replace("daily_", "").replace("market_", "").replace(".html", ""),
                    "size": stat.st_size,
                    "created": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                })

        # 최신순 정렬
        files.sort(key=lambda x: x["date"], reverse=True)
        return jsonify({"reports": files})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/reports/<filename>")
def serve_report(filename):
    """
    보고서 HTML 파일을 직접 서빙

    /reports/daily_2026-05-06.html 형태로 접근 가능합니다.
    대시보드에서 보고서 목록의 링크를 클릭하면 새 탭으로 열립니다.
    """
    from flask import send_from_directory, abort
    # ★ Path Traversal 방지: 파일명에 '..' 또는 '/' 포함 차단
    # 예: /reports/../../etc/passwd → 서버 파일 유출 가능
    from werkzeug.utils import secure_filename
    safe_name = secure_filename(filename)
    if not safe_name or safe_name != filename:
        abort(400, "잘못된 파일명입니다")
    reports_dir = _report_generator.reports_dir
    return send_from_directory(reports_dir, safe_name)


@app.route("/api/trades")
def api_trades():
    """
    거래 이력 조회

    DB에서 거래 이력을 가져오고, 한국 종목에는 종목명을 추가합니다.
    프론트엔드에서 name 필드를 사용해 종목명을 표시합니다.
    """
    limit = request.args.get("limit", 50, type=int)
    # ★ Phase 5: 모드 필터 (paper/live 격리)
    # ?mode=all 쿼리로 모든 모드 표시 가능 (디버그/legacy 데이터 확인용)
    mode_filter = request.args.get("mode", "current")
    if mode_filter == "current":
        mode_filter = _current_display_mode()
    elif mode_filter == "all":
        mode_filter = None  # 필터 안 함
    try:
        with DatabaseManager() as db:
            trades = db.get_trades(limit=limit, mode=mode_filter)

        # ── 각 거래에 종목명 추가 + side 대문자 통일 + 포지션 유형 파싱 ──
        for t in trades:
            if isinstance(t, dict):
                if "symbol" in t and not t.get("name"):
                    t["name"] = get_stock_display_name(t["symbol"])
                # ★ side를 대문자로 통일 (DB에 "buy"/"sell" 소문자로 저장됨)
                if "side" in t:
                    t["side"] = t["side"].upper()
                # 매매 이유 JSON 파싱
                if "reasons_json" in t and t["reasons_json"]:
                    try:
                        t["reasons"] = json.loads(t["reasons_json"])
                    except (json.JSONDecodeError, TypeError):
                        t["reasons"] = []
                # ★ 매매 결정 상세 (decision_json) 파싱 - 클릭 모달용
                if t.get("decision_json"):
                    try:
                        t["decision"] = json.loads(t["decision_json"])
                    except (json.JSONDecodeError, TypeError):
                        t["decision"] = None

        return jsonify(trades)
    except Exception as e:
        return jsonify({"error": str(e), "trades": []}), 500


@app.route("/api/positions")
def api_positions():
    """
    현재 보유 포지션 조회 (REST API)

    WebSocket이 끊어져도 포지션을 확인할 수 있도록 REST 엔드포인트 제공.
    봇이 실행 중이면 executor에서 실시간 포지션을 가져오고,
    봇이 꺼져있으면 DB 캐시에서 마지막 저장된 포지션을 반환.
    """
    try:
        # 봇이 실행 중이면 executor에서 직접 조회
        if bot_instance and hasattr(bot_instance, 'executor'):
            from utils.market import get_position_attr as _gpa
            positions = bot_instance.executor.get_positions()
            result = []

            # DB에서 포지션 메타데이터 한번에 조회 (★ Phase 5: 현재 모드만)
            db_meta = {}
            try:
                _mode = _current_display_mode()
                with DatabaseManager() as _db:
                    for db_pos in _db.load_positions(mode=_mode):
                        db_meta[db_pos["symbol"]] = db_pos
            except Exception:
                pass

            for p in positions:
                sym = _gpa(p, 'symbol', '')
                meta = db_meta.get(sym, {})
                # reasons_json → 리스트로 파싱
                reasons = []
                try:
                    raw = meta.get("reasons_json", "[]")
                    if raw:
                        reasons = json.loads(raw) if isinstance(raw, str) else raw
                except (json.JSONDecodeError, TypeError):
                    reasons = []

                result.append({
                    "symbol": sym,
                    "name": get_stock_display_name(sym),
                    "shares": _gpa(p, 'quantity', 0),
                    "avg_price": _gpa(p, 'avg_price', 0),
                    "current_price": _gpa(p, 'current_price', 0),
                    "market_value": _gpa(p, 'market_value', 0),
                    "pnl": _gpa(p, 'unrealized_pnl', 0),
                    "pnl_pct": _gpa(p, 'unrealized_pnl_pct', 0),
                    # ── 포지션 메타데이터 (DB에서 병합) ──
                    "position_type": meta.get("position_type", ""),
                    "position_type_en": meta.get("position_type_en", ""),
                    "target_price": meta.get("target_price", 0),
                    "stop_price": meta.get("stop_price", 0),
                    "holding_period": meta.get("holding_period", ""),
                    "bought_at": meta.get("bought_at", ""),
                    "reasons": reasons,
                })
            return jsonify(result)

        # 봇이 꺼져있으면 DB 캐시에서 조회 (★ Phase 5: 현재 모드만)
        with DatabaseManager() as db:
            cached = db.load_positions(mode=_current_display_mode()) if hasattr(db, 'load_positions') else []
        return jsonify(cached if cached else [])

    except Exception as e:
        logger.error(f"[API] 포지션 조회 오류: {e}")
        return jsonify([])


@app.route("/api/equity")
def api_equity():
    """
    포트폴리오 가치 히스토리 (차트용)

    equity_history 테이블에서 시간별 자산 데이터를 가져옵니다.
    portfolio_snapshots(일별)가 없으면 equity_history(시간별)를 사용합니다.
    """
    days = request.args.get("days", 90, type=int)
    # ★ Phase 5: 현재 모드의 자산 추적만 (paper/live 분리)
    mode_filter = _current_display_mode()
    try:
        with DatabaseManager() as db:
            # 먼저 equity_history (시간별 상세 데이터) 시도
            eq_history = db.get_equity_history(days=days, mode=mode_filter)
            if eq_history:
                return jsonify(eq_history)

            # 없으면 portfolio_snapshots (일별) 폴백
            snapshots = db.get_snapshots(days=days)
            # snapshots는 mode 필터를 별도로 추가하지 않았으므로 메모리에서 필터
            snapshots = [s for s in snapshots if s.get("mode", "paper") == mode_filter]
            rows = [
                {"date": s["date"], "total_value": s["total_value"],
                 "daily_return": s.get("daily_return", 0),
                 "cumulative_return": s.get("cumulative_return", 0)}
                for s in snapshots
            ]
        return jsonify(rows)
    except Exception as e:
        return jsonify({"error": str(e), "data": []}), 500


@app.route("/api/performance")
def api_performance():
    """
    성과 통계 API (승률, 샤프비, MDD, 손익비 등)

    대시보드에서 실시간으로 성과 지표를 보여주기 위한 엔드포인트.
    DB에서 거래 기록 + equity history를 가져와 계산합니다.

    Returns:
        {win_rate, profit_factor, sharpe_ratio, max_drawdown_pct,
         avg_win, avg_loss, calmar_ratio, win_count, loss_count, ...}
    """
    # ★ Phase 5: 현재 모드의 성과만 (paper/live 분리)
    mode_filter = _current_display_mode()
    try:
        with DatabaseManager() as db:
            # 매도 거래에서 승률/손익비 계산
            trades = db.get_trades(limit=10000, mode=mode_filter)
            sell_trades = [t for t in trades if t.get("side", "").upper() == "SELL"]

            win_count = 0
            loss_count = 0
            total_win = 0.0
            total_loss = 0.0

            for t in sell_trades:
                pnl = t.get("pnl", 0) or 0
                if pnl > 0:
                    win_count += 1
                    total_win += pnl
                elif pnl < 0:
                    loss_count += 1
                    total_loss += abs(pnl)

            total_sells = len(sell_trades)
            win_rate = (win_count / total_sells * 100) if total_sells > 0 else 0
            profit_factor = (total_win / total_loss) if total_loss > 0 else 0
            avg_win = (total_win / win_count) if win_count > 0 else 0
            avg_loss = (total_loss / loss_count) if loss_count > 0 else 0

            # MDD 계산
            mdd = db.calculate_max_drawdown(days=90)

            # 샤프비 계산 (★ Phase 5: 현재 모드만)
            eq_history = db.get_equity_history(days=90, mode=mode_filter)
            sharpe = 0.0
            calmar = 0.0
            if len(eq_history) >= 2:
                import statistics
                equities = [h["total_equity"] for h in eq_history]
                daily_returns = []
                for i in range(1, len(equities)):
                    if equities[i - 1] > 0:
                        daily_returns.append(equities[i] / equities[i - 1] - 1)
                if daily_returns and len(daily_returns) > 1:
                    mean_r = statistics.mean(daily_returns)
                    std_r = statistics.stdev(daily_returns)
                    risk_free = _dash_cfg.risk_free_rate / 252
                    if std_r > 0:
                        sharpe = (mean_r - risk_free) / std_r * (252 ** 0.5)

                    # 칼마비
                    if mdd > 0 and equities[0] > 0:
                        total_return = equities[-1] / equities[0] - 1
                        n_days = len(equities)
                        annual_return = (1 + total_return) ** (252 / n_days) - 1
                        calmar = annual_return / mdd
        return jsonify({
            "win_rate": round(win_rate, 2),
            "profit_factor": round(profit_factor, 2),
            "sharpe_ratio": round(sharpe, 2),
            "calmar_ratio": round(calmar, 2),
            "max_drawdown_pct": round(mdd * 100, 2),
            "avg_win": round(avg_win, 0),
            "avg_loss": round(avg_loss, 0),
            "win_count": win_count,
            "loss_count": loss_count,
            "total_trades": len(trades),
            "total_sells": total_sells,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/signals")
def api_signals():
    """
    최근 신호 로그

    DB에서 신호를 가져오고 한국 종목에는 종목명을 추가합니다.
    """
    limit = request.args.get("limit", 30, type=int)
    try:
        with DatabaseManager() as db:
            cursor = db.conn.execute(
                "SELECT * FROM signals ORDER BY timestamp DESC LIMIT ?", (limit,)
            )
            rows = [dict(row) for row in cursor.fetchall()]

        # ── 각 신호에 종목명 추가 (DB 해제 후 처리) ──
        for r in rows:
            if "symbol" in r and not r.get("name"):
                r["name"] = get_stock_display_name(r["symbol"])

        return jsonify(rows)
    except Exception as e:
        return jsonify({"error": str(e), "signals": []}), 500


@app.route("/api/analyze", methods=["POST"])
def api_analyze_now():
    """즉시 분석 실행 (수동 트리거)"""
    data = request.get_json() or {}
    symbol = data.get("symbol", "")

    if not symbol:
        return jsonify({"error": "Symbol required"}), 400

    # 비동기로 분석 실행
    threading.Thread(
        target=_run_single_analysis,
        args=(symbol,),
        daemon=True
    ).start()

    return jsonify({"success": True, "message": f"Analyzing {symbol}..."})


@app.route("/api/sectors")
def api_sectors():
    """
    사용 가능한 섹터 목록 반환

    프론트엔드에서 관심 분야 선택 UI를 렌더링할 때 사용합니다.
    각 섹터의 키, 이름(한/영), 아이콘, 종목 수를 반환합니다.
    """
    sectors = []
    for key, data in SECTOR_UNIVERSE.items():
        sectors.append({
            "key": key,
            "name_ko": data["name_ko"],
            "name_en": data["name_en"],
            "icon": data["icon"],
            "stock_count": len(data["stocks"]),
        })
    return jsonify({
        "sectors": sectors,
        "selected": current_settings.get("interest_sectors", [])
    })


@app.route("/api/scanner")
def api_scanner():
    """
    시장 스캐너 - 관심 분야에서 "주목할 종목" 추출

    선택된 섹터의 종목들을 분석하여 신호 강도 기준 상위 종목을 반환합니다.
    결과는 캐시되어 반복 요청 시 빠르게 응답합니다.

    Returns:
        {
            "results": [{ symbol, name, market, sector, signal, strength, price, change_1d, rsi }],
            "scanned_at": "ISO timestamp",
            "total_scanned": N
        }
    """
    # 캐시 확인 (설정된 TTL 이내 결과가 있으면 재사용)
    global _scanner_cache
    now = datetime.now()
    if (_scanner_cache and
        _scanner_cache.get("scanned_at") and
        (now - datetime.fromisoformat(_scanner_cache["scanned_at"])).seconds < _dash_cfg.scanner_cache_ttl):
        return jsonify(_scanner_cache)

    # 관심 섹터에서 종목 리스트 추출
    selected_sectors = current_settings.get("interest_sectors", [])
    if not selected_sectors:
        selected_sectors = AVAILABLE_SECTORS[:3]  # 기본값: 처음 3개 섹터

    stocks_to_scan = []
    for sector_key in selected_sectors:
        sector_data = SECTOR_UNIVERSE.get(sector_key, {})
        for symbol in sector_data.get("stocks", []):
            if symbol not in [s["symbol"] for s in stocks_to_scan]:
                stocks_to_scan.append({
                    "symbol": symbol,
                    "sector_key": sector_key,
                    "sector_name": sector_data.get("name_ko", sector_key),
                })

    # 비동기로 스캔 실행 (이미 진행 중이 아니면, Lock으로 동시 시작 방지)
    with _scanner_lock:
        should_start = not _scanner_running
    if should_start:
        threading.Thread(
            target=_run_scanner,
            args=(stocks_to_scan,),
            daemon=True
        ).start()
        # 아직 결과 없으면 빈 결과 + scanning 상태 반환
        if not _scanner_cache:
            return jsonify({
                "results": [],
                "scanning": True,
                "total_scanned": 0,
                "scanned_at": None
            })

    return jsonify(_scanner_cache or {"results": [], "scanning": True})


# 스캐너 캐시 & 상태 (Lock으로 동시 실행 방지)
_scanner_cache = None
_scanner_running = False
_scanner_lock = threading.Lock()

# ── 전역 한국 종목명 캐시 ──────────────────────────────────────────────
# pykrx에서 한 번 로드하면 앱 전체에서 재사용 (스캐너, 분석, 포지션 표시 등)
# { "005930": "삼성전자", "035720": "카카오", ... }
_kr_name_cache = {}
_kr_name_cache_loaded = False

def _load_kr_name_cache():
    """
    한국 주식 종목명을 pykrx에서 한 번에 로드하여 전역 캐시에 저장.
    코스피 + 코스닥 전체 종목을 로드합니다.
    이미 로드됐으면 즉시 반환합니다.

    pykrx가 실패할 경우를 대비해 주요 종목 폴백 딕셔너리를 사용합니다.
    """
    global _kr_name_cache, _kr_name_cache_loaded
    if _kr_name_cache_loaded and len(_kr_name_cache) > 0:
        return _kr_name_cache

    # ── 폴백: 주요 한국 종목 (pykrx 로드 실패 시 최소한 이 종목들은 표시) ──
    _FALLBACK_NAMES = {
        "005930": "삼성전자", "000660": "SK하이닉스", "035720": "카카오",
        "035420": "NAVER", "005380": "현대차", "051910": "LG화학",
        "006400": "삼성SDI", "003670": "포스코퓨처엠", "028260": "삼성물산",
        "105560": "KB금융", "055550": "신한지주", "086790": "하나금융지주",
        "066570": "LG전자", "012330": "현대모비스", "003550": "LG",
        "096770": "SK이노베이션", "034730": "SK", "015760": "한국전력",
        "032830": "삼성생명", "090430": "아모레퍼시픽", "018260": "삼성에스디에스",
        "033780": "KT&G", "030200": "KT", "017670": "SK텔레콤",
        "000270": "기아", "009150": "삼성전기", "010130": "고려아연",
        "034020": "두산에너빌리티", "003490": "대한항공", "011200": "HMM",
        "259960": "크래프톤", "352820": "하이브", "263750": "펄어비스",
        "036570": "엔씨소프트", "251270": "넷마블", "068270": "셀트리온",
        "207940": "삼성바이오로직스", "326030": "SK바이오팜",
        "373220": "LG에너지솔루션", "247540": "에코프로비엠", "086520": "에코프로",
        # ── 워치리스트에 포함되어 있지만 폴백에 누락된 종목 추가 ──
        "042700": "한미반도체", "403870": "HPSP", "012450": "한화에어로스페이스",
        "145020": "휴젤", "196170": "알테오젠", "316140": "우리금융지주",
        "051900": "LG생활건강", "004170": "신세계",
    }

    # 폴백을 기본으로 넣어두고, pykrx 성공 시 덮어씀
    if len(_kr_name_cache) == 0:
        _kr_name_cache.update(_FALLBACK_NAMES)

    try:
        from pykrx import stock as pykrx_stock
        loaded = 0
        for mkt in ("KOSPI", "KOSDAQ"):
            tickers = pykrx_stock.get_market_ticker_list(market=mkt)
            for ticker in tickers:
                name = pykrx_stock.get_market_ticker_name(ticker)
                if name:
                    _kr_name_cache[ticker] = name
                    loaded += 1
        if loaded > 0:
            _kr_name_cache_loaded = True
            logger.info(f"[종목명 캐시] pykrx에서 {loaded}개 종목 로드 완료")
        else:
            logger.warning("[종목명 캐시] pykrx 반환값 0개, 폴백 사용 중")
    except Exception as e:
        logger.warning(f"[종목명 캐시] pykrx 로드 실패 (폴백 {len(_FALLBACK_NAMES)}개 사용): {e}")

    # 폴백이라도 있으면 loaded 상태로 전환 (반복 로드 시도 방지)
    if len(_kr_name_cache) > 0:
        _kr_name_cache_loaded = True

    return _kr_name_cache


def get_stock_display_name(symbol: str) -> str:
    """
    종목 코드에서 표시용 이름 반환 (모든 곳에서 공통 사용)

    한국 주식: "삼성전자 (005930)" 형태
    미국 주식: 그대로 반환 (이름은 별도 조회 필요)

    Returns:
        str: 종목 표시명. 이름을 못 찾으면 원래 symbol 그대로 반환.
    """
    if is_kr_stock(symbol):
        pure_code = symbol.replace(".KS", "").replace(".KQ", "")
        # 전역 캐시에서 조회
        if not _kr_name_cache_loaded:
            _load_kr_name_cache()
        name = _kr_name_cache.get(pure_code, "")
        if name:
            return f"{name} ({pure_code})"
    return symbol


def _run_scanner(stocks_to_scan: list):
    """
    시장 스캐너 백그라운드 실행

    모든 종목을 분석하고 신호 강도 기준 상위 15개를 캐시에 저장합니다.
    분석 실패한 종목은 건너뜁니다.
    """
    global _scanner_cache, _scanner_running
    with _scanner_lock:
        _scanner_running = True

    results = []
    try:
        from collectors.price_us import PriceCollectorUS
        from collectors.price_kr import PriceCollectorKR
        from analyzers.technical import TechnicalAnalyzer
        from config.settings import TechnicalConfig

        analyzer = TechnicalAnalyzer(TechnicalConfig())
        us_collector = PriceCollectorUS()
        kr_collector = PriceCollectorKR()

        # ── 전역 한국 종목명 캐시 로드 (아직 안 됐으면) ──
        _load_kr_name_cache()

        for item in stocks_to_scan:
            symbol = item["symbol"]
            try:
                # 시장 판별 & 데이터 수집 (통합 유틸리티 사용)
                market = detect_market(symbol)
                if market == "KR":
                    df = kr_collector.safe_collect(symbol, period="3mo")
                else:
                    df = us_collector.safe_collect(symbol, period="3mo")

                if df is None or df.empty or len(df) < 20:
                    logger.debug(f"[스캐너] {symbol} 데이터 부족, 스킵")
                    continue

                # 기술적 분석
                df_analyzed = analyzer.calculate_all(df)
                signal = analyzer.generate_signal(df_analyzed)

                close = df_analyzed["Close"]
                change_1d = ((close.iloc[-1] / close.iloc[-2]) - 1) * 100 if len(close) > 1 else 0

                # 종목명 가져오기 (한국: 캐시 우선, 미국: yfinance)
                # 종목명 실패해도 분석 결과는 포함시킴
                stock_name = symbol
                try:
                    if market == "KR":
                        # 캐시에서 조회 (.KS/.KQ 제거한 순수 코드)
                        pure_code = symbol.replace(".KS", "").replace(".KQ", "")
                        stock_name = _kr_name_cache.get(pure_code, symbol)
                    else:
                        info = _get_stock_info(symbol)
                        stock_name = info.get("name", symbol)
                except Exception:
                    pass  # 이름 조회 실패해도 결과는 포함

                results.append({
                    "symbol": symbol,
                    "name": stock_name,
                    "market": market,
                    "sector": item["sector_name"],
                    "sector_key": item["sector_key"],
                    "signal": signal.signal,
                    "strength": round(signal.strength, 3),
                    "price": round(float(close.iloc[-1]), 2),
                    "change_1d": round(change_1d, 2),
                    "rsi": round(float(df_analyzed["RSI"].iloc[-1]), 1) if "RSI" in df_analyzed.columns else 0,
                    "volume": int(df_analyzed["Volume"].iloc[-1]) if "Volume" in df_analyzed.columns else 0,
                })

                logger.info(f"[스캐너] {symbol} ({stock_name}): {signal.signal} "
                           f"(강도: {signal.strength:.2f})")

            except Exception as e:
                logger.warning(f"[스캐너] {symbol} 분석 실패: {e}")
                continue

        # 신호 강도 기준 정렬 (BUY > HOLD > SELL, 강도 높은 순)
        signal_priority = {"BUY": 3, "HOLD": 2, "SELL": 1}
        results.sort(key=lambda x: (
            signal_priority.get(x["signal"], 0),
            x["strength"]
        ), reverse=True)

        # 상위 N개 (한국/미국 균형) — 각 시장에서 최소 절반은 포함
        kr_results = [r for r in results if r["market"] == "KR"]
        us_results = [r for r in results if r["market"] == "US"]
        per_mkt = _dash_cfg.scanner_per_market
        balanced = (kr_results[:per_mkt] + us_results[:per_mkt])
        balanced.sort(key=lambda x: (
            signal_priority.get(x["signal"], 0),
            x["strength"]
        ), reverse=True)
        top_n = _dash_cfg.scanner_top_results
        top_results = balanced[:top_n] if balanced else results[:top_n]

        logger.info(f"[스캐너] 결과: US {len(us_results)}개, KR {len(kr_results)}개 "
                    f"→ 상위 {len(top_results)}개 선택")

        # ── 자동 발굴 종목 정보 수집 (이름 포함) ──
        # 봇이 실행 중이면 발굴 시스템의 종목 리스트를 함께 제공
        # 프론트엔드에서 종목명을 표시할 수 있도록 {symbol, name} 객체로 전달
        discovered_info = {"us": [], "kr": [], "enabled": False}
        try:
            if bot_instance and hasattr(bot_instance, 'get_discovery_status'):
                disc = bot_instance.get_discovery_status()
                discovered_info["enabled"] = disc.get("enabled", False)
                # US: {symbol, name} 형태로 변환
                for sym in disc.get("discovered_us", []):
                    us_name = sym
                    try:
                        info = _get_stock_info(sym)
                        us_name = info.get("name", sym)
                    except Exception:
                        pass
                    discovered_info["us"].append({"symbol": sym, "name": us_name})
                # KR: {symbol, name} 형태로 변환
                for sym in disc.get("discovered_kr", []):
                    kr_name = get_stock_display_name(sym)
                    discovered_info["kr"].append({"symbol": sym, "name": kr_name})
        except Exception:
            pass

        # 캐시에 저장 (전체 결과 + 상위 결과 분리)
        _scanner_cache = {
            "results": top_results,           # 기본 표시용 (상위 N개)
            "all_results": results,            # 더보기용 (전체 결과)
            "total_results": len(results),     # 전체 결과 수
            "scanned_at": datetime.now().isoformat(),
            "total_scanned": len(stocks_to_scan),
            "scanning": False,
            "discovered": discovered_info,     # 자동 발굴 종목 정보
        }

        logger.info(f"[스캐너] 완료: {len(stocks_to_scan)}개 스캔 → "
                    f"상위 {len(top_results)}개 + 전체 {len(results)}개 캐시")

    except Exception as e:
        logger.error(f"[스캐너] 오류: {e}")
        _scanner_cache = {
            "results": [],
            "scanned_at": datetime.now().isoformat(),
            "total_scanned": 0,
            "scanning": False,
            "error": str(e)
        }
    finally:
        with _scanner_lock:
            _scanner_running = False


@app.route("/api/stock/search")
def api_stock_search():
    """
    종목 검색 API

    쿼리로 종목명 또는 티커를 검색합니다.
    yfinance의 Ticker.info를 활용하여 종목명을 가져옵니다.

    Parameters:
        q: 검색어 (예: "apple", "삼성", "AAPL")
        market: "us" 또는 "kr" (선택)
    """
    query = request.args.get("q", "").strip()
    market = request.args.get("market", "all")

    if not query or len(query) < 1:
        return jsonify([])

    results = []

    try:
        # 한국 주식: pykrx에서 종목명 매칭
        if market in ("kr", "all"):
            results.extend(_search_kr_stocks(query))

        # 미국 주식: yfinance ticker info
        if market in ("us", "all"):
            results.extend(_search_us_stocks(query))

    except Exception as e:
        logger.error(f"[검색] 오류: {e}")

    return jsonify(results[:20])  # 최대 20개


@app.route("/api/stock/info")
def api_stock_info():
    """
    종목 기본 정보 조회

    Parameters:
        symbol: 종목 코드 (예: "AAPL", "005930.KS")
    """
    symbol = request.args.get("symbol", "").strip()
    if not symbol:
        return jsonify({"error": "symbol required"}), 400

    try:
        info = _get_stock_info(symbol)
        return jsonify(info)
    except Exception as e:
        return jsonify({"error": str(e), "symbol": symbol, "name": symbol})


def _get_stock_info(symbol: str) -> dict:
    """
    종목 기본 정보 조회 (미국 주식: yfinance, 한국 주식: pykrx 캐시)

    [이전 truncation으로 사라진 함수 복원]

    yfinance의 Ticker.info에서 종목명, 시가총액, 섹터 등을 가져옵니다.
    한국 주식은 전역 _kr_name_cache에서 이름을 조회합니다.

    Parameters:
        symbol: 종목 코드 (예: "AAPL", "005930.KS")

    Returns:
        dict: {"symbol": ..., "name": ..., "sector": ..., "market_cap": ...}
    """
    result = {"symbol": symbol, "name": symbol}

    if symbol.endswith((".KS", ".KQ")):
        # 한국 주식: 캐시에서 조회
        pure_code = symbol.replace(".KS", "").replace(".KQ", "")
        if not _kr_name_cache_loaded:
            _load_kr_name_cache()
        name = _kr_name_cache.get(pure_code, "")
        if name:
            result["name"] = name
        result["market"] = "KR"
    else:
        # 미국 주식: yfinance
        try:
            import yfinance as yf
            ticker = yf.Ticker(symbol)
            info = ticker.info or {}
            result["name"] = info.get("shortName") or info.get("longName") or symbol
            result["sector"] = info.get("sector", "")
            result["industry"] = info.get("industry", "")
            result["market_cap"] = info.get("marketCap", 0)
        except Exception:
            # 하드코딩 폴백 (주요 미국 종목)
            _US_NAMES = {
                "AAPL": "Apple", "MSFT": "Microsoft", "GOOGL": "Alphabet",
                "AMZN": "Amazon", "NVDA": "NVIDIA", "META": "Meta Platforms",
                "TSLA": "Tesla", "SPY": "S&P 500 ETF", "QQQ": "NASDAQ 100 ETF",
                "DIS": "Walt Disney", "BLK": "BlackRock", "DIA": "Dow Jones ETF",
                "ARKK": "ARK Innovation", "SMCI": "Super Micro", "ENPH": "Enphase Energy",
                "RIVN": "Rivian", "CRM": "Salesforce", "AMD": "AMD",
                "INTC": "Intel", "SOXX": "Semiconductor ETF", "XLK": "Tech ETF",
                "XLE": "Energy ETF", "XLF": "Financial ETF", "XLV": "Healthcare ETF",
                "NFLX": "Netflix", "BABA": "Alibaba", "JPM": "JPMorgan Chase",
                "V": "Visa", "MA": "Mastercard", "BAC": "Bank of America",
                "WMT": "Walmart", "PG": "Procter & Gamble", "JNJ": "Johnson & Johnson",
                "UNH": "UnitedHealth", "HD": "Home Depot", "COST": "Costco",
                "AVGO": "Broadcom", "ADBE": "Adobe", "COP": "ConocoPhillips",
                "CVX": "Chevron", "XOM": "ExxonMobil", "PFE": "Pfizer",
                "MRK": "Merck", "ABBV": "AbbVie", "LLY": "Eli Lilly",
                "CSCO": "Cisco", "ORCL": "Oracle", "IBM": "IBM",
                "GS": "Goldman Sachs", "MS": "Morgan Stanley",
            }
            result["name"] = _US_NAMES.get(symbol, symbol)
        result["market"] = "US"

    return result


@app.route("/api/activity")
def api_activity():
    """
    봇 활동 로그 조회

    실시간 활동 피드에 표시할 최근 봇 활동 목록을 반환합니다.
    (분석 시작, 신호 발견, 매수/매도 체결, 리스크 체크 등)
    """
    with _activity_log_lock:
        return jsonify(list(_activity_log))


@app.route("/api/trades/recent")
def api_trades_recent():
    """
    최근 거래 이력 — 현재 모드(paper/live)만 표시

    우선순위:
      1. bot_instance.executor.trade_history (in-memory, paper에서만 사용 가능)
      2. DB의 trades 테이블 (mode 필터 적용) — KIS/Alpaca live에서 사용

    어느 경로든 ★ 현재 봇 모드와 일치하는 거래만 반환합니다.
    """
    mode_filter = _current_display_mode()

    # 1순위: 메모리 내 trade_history (PaperExecutor)
    if bot_instance and hasattr(bot_instance.executor, 'trade_history') and bot_instance.executor.trade_history:
        # in-memory는 이미 _restore_from_db에서 모드 필터링됨
        trades = bot_instance.executor.trade_history[-50:]
        result = []
        for t in trades:
            sym = t.get("symbol", "")
            result.append({
                "order_id": t.get("order_id", ""),
                "symbol": sym,
                "name": get_stock_display_name(sym),
                "side": t.get("side", "").upper(),
                "quantity": t.get("quantity", 0),
                "price": t.get("price", 0),
                "total": t.get("total", 0),
                "strategy": t.get("strategy", ""),
                "timestamp": t.get("timestamp", "").isoformat()
                    if hasattr(t.get("timestamp", ""), "isoformat")
                    else str(t.get("timestamp", "")),
            })
        return jsonify(result)

    # 2순위: DB에서 현재 모드 거래만 (KIS/Alpaca live 또는 봇 중지 시)
    try:
        with DatabaseManager() as db:
            db_trades = db.get_trades(limit=50, mode=mode_filter)
        result = []
        for t in db_trades:
            sym = t.get("symbol", "")
            result.append({
                "order_id": t.get("order_id", ""),
                "symbol": sym,
                "name": get_stock_display_name(sym),
                "side": (t.get("side", "") or "").upper(),
                "quantity": t.get("quantity", 0),
                "price": t.get("price", 0),
                "total": t.get("total_value", 0),
                "strategy": t.get("strategy", ""),
                "timestamp": t.get("timestamp", ""),
                "pnl": t.get("pnl", 0),
            })
        return jsonify(result)
    except Exception as e:
        logger.debug(f"[/api/trades/recent] DB 조회 실패: {e}")
        return jsonify([])


@app.route("/api/stock/name")
def api_stock_name():
    """
    종목 코드 → 표시명 변환 API

    Parameters:
        symbol: 종목 코드 (예: "005930.KS", "AAPL")
    Returns:
        { "symbol": "005930.KS", "name": "삼성전자 (005930)", "market": "KR" }
    """
    symbol = request.args.get("symbol", "").strip()
    if not symbol:
        return jsonify({"error": "symbol required"}), 400

    display_name = get_stock_display_name(symbol)
    market = detect_market(symbol)
    return jsonify({"symbol": symbol, "name": display_name, "market": market})


@app.route("/api/stock/news")
def api_stock_news():
    """
    종목 관련 뉴스 + AI 감성 분석 API

    Google News RSS에서 뉴스를 수집하고,
    LLM(Ollama/Gemma4 → OpenAI → Anthropic → 규칙기반)으로 감성 분석을 수행합니다.

    Parameters:
        symbol: 종목 코드 (예: "AAPL", "005930.KS")
        name: 종목명 (선택, 검색 품질 향상)
    """
    symbol = request.args.get("symbol", "").strip()
    name = request.args.get("name", "").strip()

    if not symbol:
        return jsonify({"error": "symbol required"}), 400

    is_kr = is_kr_stock(symbol)

    # 종목명이 없으면 캐시에서 가져오기
    if not name or name == symbol:
        name = get_stock_display_name(symbol)

    try:
        from analyzers.news_llm import get_news_with_analysis
        result = get_news_with_analysis(
            symbol=symbol, name=name, is_kr=is_kr, lang="ko"
        )
        return jsonify(result)
    except Exception as e:
        logger.error(f"[뉴스] 오류: {e}")
        return jsonify({"error": str(e), "news": [], "ai_summary": None})


@app.route("/api/dart")
def api_dart():
    """
    DART 전자공시 API

    최근 공시를 가져와 호재/악재를 분류합니다.
    DART API 키가 config에 있어야 동작합니다.

    쿼리 파라미터:
        days (int): 조회 기간 (기본 7일)

    Returns:
        { disclosures, summary, positive_count, negative_count, ... }
    """
    days = request.args.get("days", 7, type=int)
    try:
        from collectors.dart import DARTCollector
        api_key = current_settings.get("dart_api_key", "")
        collector = DARTCollector(api_key=api_key)

        # 관심 종목 중 한국 종목만 필터
        kr_symbols = [s for s in current_settings.get("watchlist", [])
                      if s.endswith(".KS") or s.endswith(".KQ")]
        result = collector.collect(symbols=kr_symbols if kr_symbols else None, days=days)
        return jsonify(result)
    except Exception as e:
        logger.error(f"[DART] 오류: {e}")
        return jsonify({"error": str(e), "disclosures": [], "summary": "오류 발생"})


@app.route("/api/briefing")
def api_briefing():
    """
    시장 AI 브리핑 API

    DART 공시 + 뉴스 + 거시경제 + 포트폴리오를 취합하여
    종합 시장 브리핑을 생성합니다.

    Returns:
        { html, summary, sentiment, sentiment_score, sections, generated_at }
    """
    try:
        from reporter.market_briefing import MarketBriefing

        briefing = MarketBriefing(current_settings)

        # 1. DART 데이터 수집 (선택)
        dart_data = None
        try:
            from collectors.dart import DARTCollector
            api_key = current_settings.get("dart_api_key", "")
            if api_key:
                dart_collector = DARTCollector(api_key=api_key)
                dart_data = dart_collector.collect(days=3)
        except Exception as e:
            logger.warning(f"[브리핑] DART 수집 실패: {e}")

        # 2. 뉴스 데이터 (간단히 최근 뉴스)
        news_data = None
        try:
            from collectors.news import NewsCollector
            nc = NewsCollector()
            watchlist = current_settings.get("watchlist", ["AAPL", "MSFT"])
            all_news = []
            for sym in watchlist[:3]:  # 상위 3종목만
                articles = nc.collect(sym)
                if isinstance(articles, list):
                    all_news.extend(articles)
                elif isinstance(articles, dict) and "articles" in articles:
                    all_news.extend(articles["articles"])
            if all_news:
                news_data = all_news[:10]
        except Exception as e:
            logger.warning(f"[브리핑] 뉴스 수집 실패: {e}")

        # 3. 거시경제 데이터 (선택)
        macro_data = None
        try:
            from collectors.macro import MacroCollector
            mc = MacroCollector()
            macro_data = mc.collect()
        except Exception as e:
            logger.warning(f"[브리핑] 매크로 수집 실패: {e}")

        # 4. 포트폴리오 데이터
        portfolio_data = {
            "total_equity": bot_status.get("total_equity", 0),
            "cash": bot_status.get("cash", 0),
            "total_pnl": bot_status.get("total_pnl", 0),
            "positions": bot_status.get("positions", {}),
        }

        # 브리핑 생성
        result = briefing.generate(
            dart_data=dart_data,
            news_data=news_data,
            macro_data=macro_data,
            portfolio_data=portfolio_data,
        )
        return jsonify(result)

    except Exception as e:
        logger.error(f"[브리핑] 생성 실패: {e}")
        return jsonify({"error": str(e), "html": "", "summary": "브리핑 생성 실패"})


@app.route("/api/healthcheck")
def api_healthcheck():
    """
    봇 헬스체크 API

    봇 프로세스, DB 연결, 분석 주기, 디스크 사용량 등을 점검합니다.

    Returns:
        { status, checks, healthy_count, warning_count, critical_count }
    """
    try:
        from dashboard.healthcheck import HealthChecker

        with DatabaseManager() as db:
            checker = HealthChecker(bot_instance=bot_instance, db=db)
            result = checker.check_all()
        return jsonify(result)
    except Exception as e:
        logger.error(f"[헬스체크] 실패: {e}")
        return jsonify({"status": "critical", "error": str(e), "checks": []})


@app.route("/api/report/weekly", methods=["POST"])
def api_weekly_report():
    """
    주간/월간 보고서 생성 API

    쿼리 파라미터:
        period: "weekly" 또는 "monthly" (기본 weekly)
    """
    data = request.get_json() or {}
    period = data.get("period", "weekly")

    try:
        from reporter.weekly_report import WeeklyReportGenerator

        with DatabaseManager() as db:
            gen = WeeklyReportGenerator(db=db, report_dir="reports")
            capital = current_settings.get("capital", 100000)
            filepath = gen.generate(period=period, capital=capital)

        if filepath:
            filename = os.path.basename(filepath)
            return jsonify({"success": True, "filename": filename})
        else:
            return jsonify({"success": False, "error": "보고서 생성 실패"})

    except Exception as e:
        logger.error(f"[보고서] 생성 실패: {e}")
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/safety")
def api_safety():
    """
    안전장치 상태 API

    킬 스위치, 일일 손익, 연속 손실 등 안전장치 상태를 반환합니다.
    """
    global bot_instance
    if bot_instance and hasattr(bot_instance, 'safety'):
        return jsonify(bot_instance.safety.get_status())
    return jsonify({
        "kill_switch": False, "daily_trades": 0,
        "daily_pnl": 0, "consecutive_losses": 0,
        "paper_mode": True, "message": "봇이 실행 중이 아닙니다"
    })


@app.route("/api/safety/kill", methods=["POST"])
def api_kill_switch():
    """
    킬 스위치 토글 API

    모든 신규 주문을 즉시 차단하거나 해제합니다.
    """
    global bot_instance
    data = request.get_json() or {}
    action = data.get("action", "activate")

    if not bot_instance or not hasattr(bot_instance, 'safety'):
        return jsonify({"error": "봇이 실행 중이 아닙니다"}), 400

    if action == "activate":
        reason = data.get("reason", "대시보드에서 수동 활성화")
        bot_instance.safety.activate_kill_switch(reason)
        return jsonify({"success": True, "kill_switch": True, "reason": reason})
    elif action == "deactivate":
        bot_instance.safety.deactivate_kill_switch()
        return jsonify({"success": True, "kill_switch": False})
    else:
        return jsonify({"error": "action: activate 또는 deactivate"}), 400


@app.route("/api/discovery/status")
def api_discovery_status():
    """
    종목 자동 발굴 상태 API

    발굴 활성화 여부, 현재 발굴된 종목 목록, 다음 발굴까지 남은 사이클 등을 반환합니다.
    """
    if bot_instance and hasattr(bot_instance, 'get_discovery_status'):
        status = bot_instance.get_discovery_status()
        # 종목명 추가
        for sym in status.get("discovered_us", []):
            pass  # US는 티커가 곧 이름
        kr_names = []
        for sym in status.get("discovered_kr", []):
            kr_names.append({
                "symbol": sym,
                "name": get_stock_display_name(sym),
            })
        status["discovered_kr_details"] = kr_names
        return jsonify(status)

    return jsonify({
        "enabled": current_settings.get("discovery_enabled", True),
        "discovered_us": [],
        "discovered_kr": [],
        "total_discovered": 0,
        "message": "봇이 실행 중이 아닙니다"
    })


@app.route("/api/discovery/trigger", methods=["POST"])
def api_discovery_trigger():
    """
    수동 종목 발굴 트리거

    봇이 실행 중일 때 즉시 발굴을 실행합니다.
    """
    if not bot_instance or not bot_status["running"]:
        return jsonify({"error": "봇이 실행 중이 아닙니다"}), 400

    try:
        _add_activity("analyzing", "🔍 수동 종목 발굴 시작...", "info")
        threading.Thread(
            target=_run_manual_discovery,
            daemon=True
        ).start()
        return jsonify({"success": True, "message": "발굴을 시작했습니다"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _run_manual_discovery():
    """수동 발굴 백그라운드 실행"""
    try:
        bot_instance._run_discovery()
        disc = bot_instance.get_discovery_status()
        _add_activity(
            "analyzing",
            f"수동 발굴 완료: US {len(disc['discovered_us'])}개, "
            f"KR {len(disc['discovered_kr'])}개",
            "success"
        )
        # 프론트엔드용 이름 정보 추가
        disc["discovered_us_details"] = []
        for sym in disc.get("discovered_us", []):
            us_name = sym
            try:
                info = _get_stock_info(sym)
                us_name = info.get("name", sym)
            except Exception:
                pass
            disc["discovered_us_details"].append({"symbol": sym, "name": us_name})
        disc["discovered_kr_details"] = []
        for sym in disc.get("discovered_kr", []):
            disc["discovered_kr_details"].append({
                "symbol": sym, "name": get_stock_display_name(sym)
            })
        socketio.emit("discovery_update", disc)
    except Exception as e:
        logger.error(f"[발굴] 수동 발굴 실패: {e}")
        _add_activity("analyzing", f"발굴 실패: {str(e)[:50]}", "danger")


@app.route("/api/discord/test", methods=["POST"])
def api_discord_test():
    """
    디스코드 웹훅 연결 테스트

    프론트엔드에서 입력한 웹훅 URL로 테스트 메시지를 보냅니다.
    """
    data = request.get_json() or {}
    webhook_url = data.get("webhook_url", "").strip()

    if not webhook_url:
        return jsonify({"error": "Webhook URL required"}), 400

    if not webhook_url.startswith("https://discord.com/api/webhooks/"):
        return jsonify({"error": "Invalid Discord webhook URL format"}), 400

    try:
        from notifier.discord_webhook import DiscordNotifier
        notifier = DiscordNotifier(webhook_url=webhook_url)
        success = notifier.test_connection()

        if success:
            return jsonify({"success": True})
        else:
            return jsonify({"success": False, "error": "Failed to send test message"}), 400

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════
# 디스코드 봇 (양방향 명령) API
# ═══════════════════════════════════════════════════════════════════════════

def _discord_start_bot_callback() -> dict:
    """
    디스코드 봇에서 호출하는 봇 시작 콜백

    Flask request 컨텍스트 없이 동작합니다.
    api_start_bot()과 동일한 로직을 수행합니다.
    """
    global bot_instance, bot_thread

    if not _bot_lock.acquire(blocking=False):
        return {"error": "다른 작업이 진행 중입니다"}

    try:
        if bot_status["running"]:
            return {"error": "Bot is already running"}

        from config.settings import (
            Settings, CapitalConfig, RiskConfig, DiscoveryConfig,
            EnsembleConfig, PositionTypeConfig, WatchlistConfig,
        )
        settings = Settings(
            capital=CapitalConfig(
                total_capital=current_settings["capital"],
                currency=current_settings["currency"]
            ),
            risk=RiskConfig(
                risk_per_trade=current_settings["risk_per_trade"],
                max_position_size=current_settings["max_position_size"],
                max_daily_loss=current_settings["max_daily_loss"],
                max_drawdown=current_settings["max_drawdown"],
                stop_loss_atr_multiplier=current_settings["stop_loss_atr_multiplier"],
                risk_reward_ratio=current_settings["risk_reward_ratio"],
                sizing_method=current_settings["sizing_method"],
                kelly_fraction=current_settings["kelly_fraction"],
            )
        )
        settings.discovery = DiscoveryConfig(
            enabled=current_settings.get("discovery_enabled", True),
            cycle_multiplier=current_settings.get("discovery_cycle_multiplier", 4),
            max_discovered_per_market=current_settings.get("discovery_max_per_market", 10),
            max_total_watchlist=current_settings.get("discovery_max_watchlist", 35),
            include_market_movers=current_settings.get("discovery_include_movers", True),
        )
        # 모듈 + 포지션 유형 토글 반영
        settings.ensemble = EnsembleConfig(
            technical_enabled=current_settings.get("module_technical_enabled", True),
            factor_enabled=current_settings.get("module_factor_enabled", True),
            sentiment_enabled=current_settings.get("module_sentiment_enabled", True),
        )
        settings.position_types = PositionTypeConfig(
            short_enabled=current_settings.get("position_type_short_enabled", True),
            swing_enabled=current_settings.get("position_type_swing_enabled", True),
            long_enabled=current_settings.get("position_type_long_enabled", True),
        )
        settings.watchlist = WatchlistConfig(
            strict_mode=current_settings.get("watchlist_strict_mode", False),
        )

        # ── KIS_PAPER / live_mode 자동 동기화 (디스코드 시작 명령용) ──
        _kis_paper_env = os.environ.get("KIS_PAPER", "true").lower()
        _kis_is_live = _kis_paper_env in ("false", "0", "no")
        if current_settings["broker"] in ("kis", "dual") and _kis_is_live:
            if not current_settings.get("live_mode"):
                logger.info("[모드 자동 동기화] KIS_PAPER=false → live_mode=True")
                current_settings["live_mode"] = True
                _save_settings_to_file()

        from run_bot import QuantBot
        bot_instance = QuantBot(
            settings=settings,
            broker=current_settings["broker"],
            live=current_settings["live_mode"]
        )

        bot_status["running"] = True
        bot_status["mode"] = current_settings["broker"]
        bot_status["live"] = current_settings["live_mode"]
        bot_status["currency"] = _derive_display_currency(
            current_settings["broker"],
            current_settings.get("currency", "KRW"),
        )
        bot_status["started_at"] = datetime.now().isoformat()
        bot_status["total_equity"] = current_settings["capital"]
        bot_status["cash"] = current_settings["capital"]

        bot_thread = threading.Thread(target=_run_bot_thread, daemon=True)
        bot_thread.start()

        logger.info("[디스코드] 봇 시작 명령 실행")
        return {"success": True}

    except Exception as e:
        logger.error(f"[디스코드] 봇 시작 실패: {e}")
        bot_status["running"] = False
        return {"error": str(e)}
    finally:
        _bot_lock.release()


def _discord_stop_bot_callback() -> dict:
    """디스코드 봇에서 호출하는 봇 중지 콜백"""
    global bot_instance

    if not _bot_lock.acquire(blocking=False):
        return {"error": "다른 작업이 진행 중입니다"}

    try:
        if not bot_status["running"]:
            return {"error": "Bot is not running"}

        if bot_instance:
            bot_instance.stop()
            bot_instance = None

        bot_status["running"] = False
        _add_activity("stop", "봇이 중지되었습니다 (Discord)", "warning")
        logger.info("[디스코드] 봇 중지 명령 실행")
        return {"success": True}

    except Exception as e:
        return {"error": str(e)}
    finally:
        _bot_lock.release()


def _start_discord_bot():
    """디스코드 봇 인스턴스 생성 및 시작"""
    global discord_bot_instance

    token = current_settings.get("discord_bot_token", "").strip()
    if not token:
        logger.info("[Discord Bot] 토큰 미설정 — 봇 비활성화")
        return False

    # 이미 실행 중이면 중지 후 재시작
    if discord_bot_instance:
        discord_bot_instance.stop()
        discord_bot_instance = None

    try:
        from notifier.discord_bot import QuantBotDiscord

        channel_id_str = current_settings.get("discord_bot_channel_id", "").strip()
        channel_id = int(channel_id_str) if channel_id_str else None

        discord_bot_instance = QuantBotDiscord(
            token=token,
            allowed_channel_id=channel_id,
        )

        # 콜백 연결: 디스코드 봇이 대시보드 상태에 접근할 수 있도록
        discord_bot_instance.set_callbacks(
            get_status=lambda: bot_status,
            get_settings=lambda: current_settings,
            get_bot_instance=lambda: bot_instance,
            start_bot=_discord_start_bot_callback,
            stop_bot=_discord_stop_bot_callback,
            get_db=lambda: DatabaseManager(),
        )

        discord_bot_instance.start()
        logger.info("[Discord Bot] 시작됨")
        return True

    except ImportError:
        logger.warning("[Discord Bot] discord.py 미설치 — pip install discord.py")
        return False
    except Exception as e:
        logger.error(f"[Discord Bot] 시작 실패: {e}")
        return False


def _stop_discord_bot():
    """디스코드 봇 중지"""
    global discord_bot_instance
    if discord_bot_instance:
        discord_bot_instance.stop()
        discord_bot_instance = None
        logger.info("[Discord Bot] 중지됨")


@app.route("/api/discord/bot/start", methods=["POST"])
def api_discord_bot_start():
    """디스코드 봇 시작 API"""
    data = request.get_json() or {}
    token = data.get("token", "").strip()
    channel_id = data.get("channel_id", "").strip()

    if token:
        current_settings["discord_bot_token"] = token
    if channel_id:
        current_settings["discord_bot_channel_id"] = channel_id

    if not current_settings.get("discord_bot_token"):
        return jsonify({"error": "봇 토큰이 필요합니다"}), 400

    _save_settings_to_file()

    success = _start_discord_bot()
    if success:
        return jsonify({"success": True, "message": "디스코드 봇이 시작되었습니다"})
    else:
        return jsonify({"error": "디스코드 봇 시작 실패 (토큰 확인 또는 discord.py 설치 필요)"}), 500


@app.route("/api/discord/bot/stop", methods=["POST"])
def api_discord_bot_stop():
    """디스코드 봇 중지 API"""
    _stop_discord_bot()
    return jsonify({"success": True, "message": "디스코드 봇이 중지되었습니다"})


@app.route("/api/discord/bot/status")
def api_discord_bot_status():
    """디스코드 봇 상태 조회 API"""
    if discord_bot_instance:
        return jsonify(discord_bot_instance.status_summary)
    return jsonify({
        "running": False, "connected": False,
        "username": None, "guilds": 0,
    })


@app.route("/api/bot/reset", methods=["POST"])
def api_reset_bot():
    """
    봇 데이터 초기화 API

    모든 포지션, 거래 이력, 자산 기록을 삭제하고
    초기 자본금 상태로 되돌립니다.

    ⚠️ 봇이 실행 중이면 먼저 중지해야 합니다.
    ⚠️ 이 작업은 되돌릴 수 없습니다.
    """
    global bot_instance, _last_trade_count

    # 봇이 실행 중이면 거부
    if bot_status["running"]:
        return jsonify({
            "error": "봇이 실행 중입니다. 먼저 봇을 중지한 후 초기화하세요."
        }), 400

    try:
        # DB 데이터 삭제
        with DatabaseManager() as reset_db:
            deleted = reset_db.reset_all_data()
        logger.info(f"[대시보드] DB 초기화 완료: {deleted}")

        # 메모리 상태 초기화
        with _trade_count_lock:
            _last_trade_count = 0

        # 봇 인스턴스의 메모리 데이터도 초기화
        if bot_instance and hasattr(bot_instance, 'executor'):
            executor = bot_instance.executor
            with executor._lock:
                executor.positions.clear()
                executor.trade_history.clear()
                executor.cash = executor.initial_capital
                executor.orders.clear()

        # 대시보드 상태 초기화
        bot_status["total_equity"] = 0
        bot_status["cash"] = 0
        bot_status["positions"] = {}
        bot_status["daily_pnl"] = 0
        bot_status["total_pnl"] = 0
        bot_status["total_trades"] = 0
        bot_status["win_rate"] = 0
        bot_status["signals_today"] = []

        _add_activity("reset", "봇 데이터가 초기화되었습니다", "warning")

        # 설정에서 초기 자본금 읽기
        capital = float(current_settings.get("capital", 10_000_000))

        return jsonify({
            "success": True,
            "message": f"초기화 완료. 자본금 {capital:,.0f}원으로 리셋됩니다.",
            "deleted": deleted
        })

    except Exception as e:
        logger.error(f"[대시보드] 초기화 실패: {e}")
        return jsonify({"error": str(e)}), 500


# =============================================================================
# WebSocket 이벤트
# =============================================================================

@socketio.on("connect")
def handle_connect(auth=None):
    """
    클라이언트 연결 시 현재 상태 전송

    F5 새로고침이나 네트워크 끊김 후 재연결 시에도 호출됩니다.
    재연결된 클라이언트에게 최신 상태를 즉시 전송하여
    빈 화면 / 이전 데이터 표시 문제를 방지합니다.

    ★ auth 파라미터: Flask-SocketIO >= 5.x에서 connect 이벤트에
       인증 데이터를 전달합니다. 사용하지 않더라도 받아야
       TypeError 방지됩니다.
    """
    try:
        emit("status_update", bot_status)
        emit("settings_update", current_settings)
    except Exception as e:
        logger.warning(f"[WebSocket] 연결 시 초기 데이터 전송 실패: {e}")
    logger.info(f"[WebSocket] 클라이언트 연결됨 (sid: {request.sid})")


@socketio.on("disconnect")
def handle_disconnect():
    """
    클라이언트 연결 해제 핸들러

    F5 새로고침, 탭 닫기, 네트워크 끊김 등으로 발생합니다.
    연결 해제 시 리소스 정리 및 로그를 남깁니다.
    """
    logger.info(f"[WebSocket] 클라이언트 연결 해제 (sid: {request.sid})")


@socketio.on("request_status")
def handle_request_status():
    """클라이언트가 상태 요청"""
    emit("status_update", bot_status)


# =============================================================================
# 일일 보고서 생성
# =============================================================================

def _generate_daily_report() -> Optional[str]:
    """
    현재 봇 상태를 기반으로 일일 거래 보고서를 생성합니다.

    [호출 시점]
    1. 봇 중지(Stop) 시 자동 호출 → 당일 거래 내역 스냅샷을 HTML로 저장
    2. /api/report/generate 엔드포인트 → 수동 보고서 생성 버튼

    [데이터 수집 과정]
    - bot_instance.executor.trade_history → 거래 내역 (PaperExecutor가 기록)
    - bot_instance.executor.get_positions() → 현재 보유 포지션
    - bot_instance.executor.get_account() → 계좌 정보 (총자산, 현금)
    - bot_status["signals_today"] → 당일 매매 신호

    Returns:
        str: 생성된 보고서 파일 경로, 실패 시 None
    """
    global bot_instance

    if not bot_instance:
        logger.warning("[보고서] bot_instance가 없어 보고서 생성 불가")
        return None

    try:
        # ── 1. 거래 내역 수집 ──
        trades = []
        if hasattr(bot_instance.executor, 'trade_history'):
            for t in bot_instance.executor.trade_history:
                sym = t.get("symbol", "")
                trades.append({
                    "symbol": sym,
                    "name": get_stock_display_name(sym),
                    # ★ side를 대문자로 통일: OrderSide.BUY.value="buy"(소문자)
                    # 보고서에서 "BUY"/"SELL" 대문자로 비교하므로 .upper() 필수
                    "side": t.get("side", "").upper(),
                    "quantity": t.get("quantity", 0),
                    "price": t.get("price", 0),
                    "total": t.get("total", 0),
                    "strategy": t.get("strategy", ""),
                    "timestamp": t.get("timestamp", ""),
                    # ★ in-memory trade_history는 "realized_pnl" 키 사용
                    # (paper_executor.py가 "realized_pnl"로 저장하므로)
                    "pnl": t.get("realized_pnl", t.get("pnl", 0)),
                })

        # ── 2. 포지션 수집 ──
        positions = []
        try:
            pos_list = bot_instance.executor.get_positions()
            for p in pos_list:
                # ★ market_value는 KRW 환산값 (paper_executor.get_positions에서 to_krw 적용)
                # current_price는 native 통화이므로 평가액 계산에 직접 쓰면 안됨
                positions.append({
                    "symbol": p.symbol,
                    "name": get_stock_display_name(p.symbol),
                    "shares": p.quantity,
                    "avg_price": p.avg_price,         # native 통화
                    "current_price": p.current_price, # native 통화
                    "market_value": p.market_value,   # ★ KRW 환산
                    "pnl": p.unrealized_pnl,           # KRW 환산
                    "pnl_pct": p.unrealized_pnl_pct,
                })
        except Exception as e:
            logger.warning(f"[보고서] 포지션 수집 오류: {e}")

        # ── 3. 계좌 정보 수집 ──
        account_info = {}
        try:
            account = bot_instance.executor.get_account()
            # ★ positions_value는 get_account()에서 이미 KRW 환산됨
            account_info = {
                "total_equity": account.total_equity,
                "cash": account.cash,
                "positions_value": account.positions_value,
            }
        except Exception as e:
            logger.warning(f"[보고서] 계좌 정보 수집 오류: {e}")

        # ── 4. 매매 신호 수집 ──
        signals = bot_status.get("signals_today", [])

        # ── 5. equity history 수집 (DB에서) — ★ Phase 5: 현재 모드만 ──
        equity_history = []
        try:
            if hasattr(bot_instance, 'db') and bot_instance.db:
                _mode = getattr(bot_instance.executor, "mode", "paper")
                eq_data = bot_instance.db.get_equity_history(days=90, mode=_mode)
                equity_history = [
                    {"date": e["timestamp"][:10], "equity": e["total_equity"]}
                    for e in eq_data
                ]
        except Exception:
            pass

        # ── 6. 보고서 생성 ──
        report_path = _report_generator.generate(
            trades=trades,
            positions=positions,
            account_info=account_info,
            signals=signals,
            initial_capital=current_settings.get("capital", 10_000_000),
            currency="KRW",
            news_summary="",  # 향후 LLM 뉴스 요약 연동 가능
            equity_history=equity_history,
        )

        logger.info(f"[보고서] 일일 보고서 생성 완료: {report_path}")
        return report_path

    except Exception as e:
        logger.error(f"[보고서] 일일 보고서 생성 실패: {e}")
        return None


# =============================================================================
# 백그라운드 작업
# =============================================================================

def _run_bot_thread():
    """
    봇을 별도 스레드에서 실행

    기본 start()는 스케줄만 설정하고 대기하는데, 대시보드에서는:
    1. 즉시 1회 분석을 실행하여 사용자에게 빠른 피드백 제공
    2. analysis_interval 설정에 따른 반복 분석 등록
    """
    global bot_instance
    try:
        if bot_instance:
            # 브로커 연결
            if not bot_instance.executor.connect():
                logger.error("[봇 스레드] 브로커 연결 실패")
                bot_status["running"] = False
                return

            # ── ★ Phase 6B: 브로커 ↔ DB 포지션 reconcile (이중 매수 방지) ──
            # 봇 크래시 후 재시작 시 DB와 브로커가 어긋날 수 있음
            try:
                if hasattr(bot_instance, "_reconcile_with_broker"):
                    bot_instance._reconcile_with_broker()
            except Exception as recon_err:
                logger.warning(f"[봇 스레드] reconcile 실패 (계속 진행): {recon_err}")

            # ── ★ 거래 카운터를 DB 복원된 거래 수에 맞춤 ──
            # connect() → _restore_from_db()로 과거 trade_history가 메모리에 로드됨
            # 카운터를 이 수에 맞추지 않으면, _status_broadcaster가
            # DB 복원된 과거 거래를 전부 "새 거래"로 인식하여
            # "매수 체결! 매도 체결!" 토스트 알림을 쏟아냄
            global _last_trade_count
            if hasattr(bot_instance.executor, 'trade_history'):
                restored_count = len(bot_instance.executor.trade_history)
                with _trade_count_lock:
                    _last_trade_count = restored_count
                logger.info(
                    f"[봇 스레드] 거래 카운터 동기화: {restored_count}건 "
                    f"(DB 복원분 스킵 → 새 거래만 알림)"
                )

            # ── 최근 신호 복원: DB에서 오늘자 신호를 메모리로 로드 ──
            # bot_status["signals_today"]는 메모리에만 있어 재시작 시 사라짐.
            # DB의 signals 테이블에서 오늘자 신호를 가져와 UI에 표시되도록 복원.
            try:
                if hasattr(bot_instance, 'db') and bot_instance.db:
                    today_str = datetime.now().strftime("%Y-%m-%d")
                    cursor = bot_instance.db.conn.execute(
                        "SELECT timestamp, symbol, signal_type, confidence, score, "
                        "components_json, reasons_json "
                        "FROM signals WHERE timestamp LIKE ? "
                        "ORDER BY timestamp DESC LIMIT ?",
                        (f"{today_str}%", _dash_cfg.signals_log_max)
                    )
                    restored_signals = []
                    for row in cursor.fetchall():
                        # 종목 정보 (이름/시장)
                        sym = row["symbol"]
                        info = _get_stock_info(sym)
                        try:
                            reasons = json.loads(row["reasons_json"] or "[]")
                        except (json.JSONDecodeError, TypeError):
                            reasons = []
                        restored_signals.append({
                            "symbol": sym,
                            "name": info.get("name", sym),
                            "market": info.get("market", ""),
                            "signal": (row["signal_type"] or "").upper(),
                            "strength": float(row["confidence"] or 0),
                            "score": float(row["score"] or 0),
                            "reasons": reasons,
                            "timestamp": row["timestamp"],
                            # ★ 복원된 신호는 가격/RSI 정보 없음 (DB에 미저장)
                            "price": 0,
                            "rsi": 0,
                        })
                    # 시간순 정렬 (오래된 → 최신, signals_today는 append로 쌓이므로)
                    restored_signals.reverse()
                    bot_status["signals_today"] = restored_signals
                    logger.info(
                        f"[봇 스레드] 신호 복원: {len(restored_signals)}건 "
                        f"(DB의 오늘자 신호)"
                    )
            except Exception as e:
                logger.warning(f"[봇 스레드] 신호 복원 실패: {e}")

            # ── 분석 간격을 설정에서 가져와 스케줄에 적용 ──
            interval_str = current_settings.get("analysis_interval", "1h")
            interval_minutes = _parse_interval(interval_str)

            # 봇 running 상태 설정
            bot_instance.running = True

            logger.info(f"[봇 스레드] 시작 완료 (분석 간격: {interval_minutes}분)")
            _add_activity("start", f"봇 시작됨 (간격: {interval_minutes}분)", "success")

            # ══════════════════════════════════════════════════════
            # 분석 실행 함수 (초기 + 반복에서 공통 사용)
            # ══════════════════════════════════════════════════════
            def _run_one_cycle(cycle_label="분석"):
                """
                미국+한국 시장 1회 분석 실행 + 활동 로그 기록

                APScheduler에 의존하지 않고, 메인 루프의 타이머에서 직접 호출.
                이렇게 하면 스케줄러 import 실패, daemon thread 충돌 등의
                문제를 완전히 우회할 수 있음.

                ★ 종목 발굴은 _analyze_us_market() 내부에서 자체적으로
                  cycle_multiplier 주기마다 실행됩니다.
                  여기서 중복 호출하면 카운터가 2배로 증가하는 버그가 생기므로
                  dashboard에서는 발굴을 직접 호출하지 않습니다.
                """
                _add_activity("analyzing", f"미국 시장 {cycle_label} 중...", "info")
                try:
                    bot_instance._analyze_us_market()
                    _add_activity("analyzing", f"미국 시장 {cycle_label} 완료 ✓", "success")
                except Exception as e:
                    logger.warning(f"[봇 스레드] 미국 분석 오류: {e}")
                    _add_activity("analyzing", f"미국 분석 오류: {str(e)[:60]}", "warning")

                _add_activity("analyzing", f"한국 시장 {cycle_label} 중...", "info")
                try:
                    bot_instance._analyze_kr_market()
                    _add_activity("analyzing", f"한국 시장 {cycle_label} 완료 ✓", "success")
                except Exception as e:
                    logger.warning(f"[봇 스레드] 한국 분석 오류: {e}")
                    _add_activity("analyzing", f"한국 분석 오류: {str(e)[:60]}", "warning")

                # 분석 후 신호/포지션 상태 요약
                try:
                    from datetime import timezone, timedelta as _td
                    kst_now = datetime.now(timezone(+_td(hours=9)))
                    kst_today = kst_now.strftime("%Y-%m-%d")
                    with DatabaseManager() as db:
                        cursor = db.conn.execute(
                            "SELECT COUNT(*) FROM signals WHERE timestamp LIKE ?",
                            (f"{kst_today}%",)
                        )
                        today_signals = cursor.fetchone()[0]
                    positions = bot_instance.executor.get_positions()
                    _add_activity(
                        "analyzing",
                        f"{cycle_label} 완료: 오늘 신호 {today_signals}개, "
                        f"보유 {len(positions)}종목",
                        "success"
                    )
                except Exception as db_e:
                    logger.debug(f"신호 카운트 오류: {db_e}")
                    _add_activity("analyzing", f"{cycle_label} 완료", "success")

            # ══════════════════════════════════════════════════════
            # 즉시 1회 분석 (초기)
            # ══════════════════════════════════════════════════════
            try:
                logger.info("[봇 스레드] 초기 분석 실행 중...")
                _run_one_cycle("초기 분석")
            except Exception as e:
                logger.warning(f"[봇 스레드] 초기 분석 중 오류 (무시): {e}")
                _add_activity("analyzing", f"분석 오류: {str(e)[:50]}", "danger")

            # ══════════════════════════════════════════════════════
            # 메인 루프 (타이머 기반 반복 분석)
            # ══════════════════════════════════════════════════════
            # APScheduler에 의존하지 않는 직접 타이머 방식
            # interval_minutes마다 분석 사이클을 실행
            # 장점: 별도 라이브러리 불필요, daemon 스레드 충돌 없음
            interval_seconds = interval_minutes * 60
            last_analysis_time = time.time()  # 초기 분석 직후
            cycle_count = 0

            logger.info(
                f"[봇 스레드] 반복 분석 타이머 시작: "
                f"{interval_minutes}분({interval_seconds}초) 간격"
            )

            while bot_instance and bot_instance.running:
                # ★ Phase 7B: Event 기반 대기 → stop 호출 시 즉시 반응 (이전: 최대 1초 지연)
                # _stop_event.wait(1)은 1초 후 또는 set() 호출 시 둘 중 먼저 발생하면 반환
                try:
                    if hasattr(bot_instance, "_stop_event"):
                        if bot_instance._stop_event.wait(1):
                            # stop 신호 → 즉시 루프 종료
                            break
                    else:
                        time.sleep(1)
                except Exception:
                    time.sleep(1)

                # 설정된 간격이 지났는지 체크
                elapsed = time.time() - last_analysis_time
                if elapsed >= interval_seconds:
                    cycle_count += 1
                    logger.info(
                        f"[봇 스레드] 정기 분석 #{cycle_count} 시작 "
                        f"({interval_minutes}분 경과)"
                    )
                    try:
                        _run_one_cycle(f"정기 분석 #{cycle_count}")
                    except Exception as e:
                        logger.error(f"[봇 스레드] 정기 분석 오류: {e}")
                        _add_activity(
                            "analyzing",
                            f"정기 분석 오류: {str(e)[:50]}",
                            "danger"
                        )
                    last_analysis_time = time.time()

    except Exception as e:
        logger.error(f"[봇 스레드] 오류: {e}")
        bot_status["running"] = False


def _parse_interval(interval_str) -> int:
    """
    분석 간격 문자열을 분(minutes)으로 변환

    지원 형식: "15m", "30m", "1h", "2h", "4h", "1d"
    숫자만 오면 분 단위로 간주
    """
    if isinstance(interval_str, (int, float)):
        return int(interval_str)

    s = str(interval_str).strip().lower()
    if s.endswith("m"):
        return int(s[:-1])
    elif s.endswith("h"):
        return int(s[:-1]) * 60
    elif s.endswith("d"):
        return int(s[:-1]) * 1440
    else:
        try:
            return int(s)
        except ValueError:
            return 60  # 기본값 1시간


def _reschedule_analysis(new_minutes: int):
    """
    실행 중인 봇의 분석 스케줄러 간격을 동적으로 변경

    기존에 등록된 'dashboard_kr_analysis', 'dashboard_us_analysis' 작업을
    제거하고 새로운 간격으로 재등록합니다.
    봇 재시작 없이 설정 변경이 즉시 반영되게 해줍니다.

    Args:
        new_minutes: 새로운 분석 간격 (분 단위)
    """
    global bot_instance
    if not bot_instance or not hasattr(bot_instance, 'scheduler'):
        raise RuntimeError("봇 인스턴스 또는 스케줄러 없음")

    scheduler = bot_instance.scheduler

    # ── 기존 분석 작업 제거 ──
    # APScheduler의 remove_job 또는 커스텀 스케줄러의 제거 메서드 사용
    for job_id in ["dashboard_kr_analysis", "dashboard_us_analysis"]:
        try:
            if hasattr(scheduler, 'remove_job'):
                scheduler.remove_job(job_id)
            elif hasattr(scheduler, 'jobs') and isinstance(scheduler.jobs, dict):
                scheduler.jobs.pop(job_id, None)
        except Exception:
            pass  # 작업이 없으면 무시

    # ── 새 간격으로 재등록 ──
    scheduler.add_interval_job(
        "dashboard_kr_analysis",
        bot_instance._analyze_kr_market,
        minutes=new_minutes
    )
    scheduler.add_interval_job(
        "dashboard_us_analysis",
        bot_instance._analyze_us_market,
        minutes=new_minutes
    )

    logger.info(f"[스케줄러] 분석 간격 동적 변경 완료: {new_minutes}분")


def _refresh_position_prices():
    """
    보유 포지션의 현재가를 가볍게 새로고침 (실시간 PnL 표시용)

    분석 사이클은 5~30분에 1번만 실행되지만, 사용자는 대시보드에서
    실시간 PnL 변화를 보고 싶어합니다. 이 함수는 broadcaster에서
    1분마다 호출되어 보유 종목의 가격만 빠르게 갱신합니다.

    - 한국 주식: pykrx로 일봉 종가 조회 (장중에는 직전 분봉 가격)
    - 미국 주식: yfinance로 1분봉 마지막 가격 조회
    - 봇이 paper 모드일 때만 set_current_price() 호출
      (실거래는 broker가 직접 가격 갱신)

    실패해도 무시 (다음 분석 사이클 또는 다음 호출에서 재시도)
    """
    if not bot_instance or not hasattr(bot_instance, 'executor'):
        return

    executor = bot_instance.executor
    if not hasattr(executor, 'set_current_price'):
        return

    try:
        positions = executor.get_positions()
    except Exception:
        return

    if not positions:
        return

    # 한국/미국 종목 분리
    kr_symbols = []
    us_symbols = []
    for p in positions:
        if is_us_stock(p.symbol):
            us_symbols.append(p.symbol)
        else:
            kr_symbols.append(p.symbol)

    # ── 미국 종목: yfinance로 1분봉 최신 가격 ──
    if us_symbols:
        try:
            import yfinance as yf
            # tickers 일괄 조회 (개별 호출보다 빠름)
            tickers_str = " ".join(us_symbols)
            data = yf.download(
                tickers_str, period="1d", interval="1m",
                progress=False, prepost=True, threads=True
            )
            if not data.empty:
                # 단일 ticker vs 다중 ticker 처리
                if len(us_symbols) == 1:
                    closes = data["Close"].dropna()
                    if len(closes) > 0:
                        executor.set_current_price(us_symbols[0], float(closes.iloc[-1]))
                else:
                    for sym in us_symbols:
                        try:
                            closes = data["Close"][sym].dropna()
                            if len(closes) > 0:
                                executor.set_current_price(sym, float(closes.iloc[-1]))
                        except (KeyError, AttributeError):
                            continue
        except Exception as e:
            logger.debug(f"[가격갱신] 미국 종목 실패: {e}")

    # ── 한국 종목: KIS API 우선, 실패 시 pykrx fallback ──
    if kr_symbols:
        # 1순위: KIS API (실시간 시세, ~1초 지연)
        kis_client = get_kis_price_client()
        kr_remaining = list(kr_symbols)

        if kis_client:
            try:
                prices = kis_client.get_current_prices(kr_symbols)
                for sym, price in prices.items():
                    if price > 0:
                        executor.set_current_price(sym, price)
                        kr_remaining.remove(sym) if sym in kr_remaining else None
                if prices:
                    logger.debug(
                        f"[가격갱신] KIS API로 한국 종목 {len(prices)}개 갱신"
                    )
            except Exception as e:
                logger.debug(f"[가격갱신] KIS API 실패, pykrx로 fallback: {e}")

        # 2순위: pykrx (KIS 미설정 또는 일부 실패한 경우)
        if kr_remaining:
            try:
                from pykrx import stock as pyk_stock
                from datetime import datetime as _dt
                today = _dt.now().strftime("%Y%m%d")
                for sym in kr_remaining:
                    try:
                        code = sym.replace(".KS", "").replace(".KQ", "")
                        df = pyk_stock.get_market_ohlcv(today, today, code)
                        if not df.empty:
                            last_price = float(df["종가"].iloc[-1])
                            if last_price > 0:
                                executor.set_current_price(sym, last_price)
                    except Exception:
                        continue
            except Exception as e:
                logger.debug(f"[가격갱신] 한국 종목 pykrx fallback 실패: {e}")


def _status_broadcaster():
    """
    2초마다 봇 상태를 WebSocket으로 브로드캐스트

    봇이 실행 중이면 executor에서 실시간 포트폴리오 정보를 가져와서
    모든 연결된 클라이언트에게 전송합니다.

    추가 기능:
    - trade_history 길이 변화를 감지하여 새 거래 발생 시
      'trade_executed' 이벤트를 별도로 emit
    - 5분마다 equity 스냅샷을 DB에 저장 (equity curve 추적용)
    """
    global _last_trade_count, _last_snapshot_time, _last_price_refresh

    while True:
        time.sleep(_dash_cfg.broadcast_interval)
        try:
            if bot_instance and bot_status["running"]:
                # ── 보유 포지션 현재가 새로고침 (1분마다) ──
                # 분석 사이클(보통 5~30분) 중간에도 PnL이 갱신되도록
                # 보유 종목만 가볍게 가격을 조회하여 set_current_price() 호출
                _now_price = time.time()
                if (_now_price - _last_price_refresh
                        >= _dash_cfg.position_price_refresh_interval):
                    try:
                        _refresh_position_prices()
                    except Exception as e:
                        logger.debug(f"[브로드캐스터] 포지션 가격 갱신 실패: {e}")
                    _last_price_refresh = _now_price

                # executor에서 현재 상태 가져오기 (블로킹 작업 — 락 밖에서)
                account = bot_instance.executor.get_account()
                positions = bot_instance.executor.get_positions()

                # ★ 빌드는 락 밖에서, 일괄 대입만 락 안에서 (블로킹 최소화)
                _new_positions = {
                    p.symbol: {
                        "shares": int(p.quantity),
                        "avg_price": float(p.avg_price),
                        "current_price": float(p.current_price),
                        "pnl": float(p.unrealized_pnl),          # KRW 환산
                        "pnl_pct": float(getattr(p, 'unrealized_pnl_pct', 0) or 0),
                        "market_value_krw": float(p.market_value),  # KRW 환산 시가
                        "currency": "USD" if is_us_stock(p.symbol) else "KRW",
                        "name": get_stock_display_name(p.symbol),
                    }
                    for p in positions
                }
                with _bot_status_lock:
                    bot_status["total_equity"] = float(account.total_equity)
                    bot_status["cash"] = float(account.cash)
                    bot_status["positions"] = _new_positions

                # ── 포지션 유형 메타데이터 DB에서 병합 (★ Phase 5: 현재 모드) ──
                try:
                    _bcast_mode = getattr(bot_instance.executor, "mode", "paper")
                    with DatabaseManager() as _db:
                        for sym in bot_status["positions"]:
                            db_pos = _db.get_position(sym, mode=_bcast_mode)
                            if db_pos:
                                bot_status["positions"][sym]["position_type"] = db_pos.get("position_type", "")
                                bot_status["positions"][sym]["position_type_en"] = db_pos.get("position_type_en", "")
                                bot_status["positions"][sym]["target_price"] = db_pos.get("target_price", 0)
                                bot_status["positions"][sym]["stop_price"] = db_pos.get("stop_price", 0)
                                bot_status["positions"][sym]["holding_period"] = db_pos.get("holding_period", "")
                                bot_status["positions"][sym]["bought_at"] = db_pos.get("bought_at", "")
                                # ── ExitManager 추적 데이터 (손절/익절/트레일링) ──
                                bot_status["positions"][sym]["entry_atr"] = db_pos.get("entry_atr", 0)
                                bot_status["positions"][sym]["current_stop"] = db_pos.get("current_stop", 0)
                                bot_status["positions"][sym]["highest_since_entry"] = db_pos.get("highest_since_entry", 0)
                                bot_status["positions"][sym]["partial_sold_pct"] = db_pos.get("partial_sold_pct", 0)
                                bot_status["positions"][sym]["target_1"] = db_pos.get("target_1", 0)
                                bot_status["positions"][sym]["target_2"] = db_pos.get("target_2", 0)
                                try:
                                    bot_status["positions"][sym]["reasons"] = json.loads(
                                        db_pos.get("reasons_json", "[]"))
                                except (json.JSONDecodeError, TypeError):
                                    bot_status["positions"][sym]["reasons"] = []
                except Exception:
                    pass  # DB 조회 실패해도 기본 포지션 정보는 유지

                # ★ numpy/bytes 타입 안전 변환 + 0 나누기 방지
                capital = float(current_settings.get("capital", 1) or 1)
                equity = float(account.total_equity)
                bot_status["total_pnl"] = ((equity / capital) - 1) * 100

                # 환율 정보 (프론트엔드에서 USD 가격 표시용)
                try:
                    bot_status["exchange_rate"] = float(get_exchange_rate())
                except Exception:
                    bot_status["exchange_rate"] = 1350.0

                # 수수료 통계
                try:
                    if hasattr(bot_instance.executor, 'get_commission_stats'):
                        bot_status["commission"] = bot_instance.executor.get_commission_stats()
                except Exception:
                    pass

                # 발굴 상태 추가
                try:
                    if hasattr(bot_instance, 'get_discovery_status'):
                        disc = bot_instance.get_discovery_status()
                        bot_status["discovery"] = {
                            "enabled": disc["enabled"],
                            "total": disc["total_discovered"],
                            "us_count": len(disc["discovered_us"]),
                            "kr_count": len(disc["discovered_kr"]),
                        }
                except Exception:
                    pass

                # ── 새 거래 감지 (Lock으로 동시 수정 방지) ──
                new_trades = []
                if hasattr(bot_instance.executor, 'trade_history'):
                    current_count = len(bot_instance.executor.trade_history)
                    with _trade_count_lock:
                        if current_count > _last_trade_count:
                            # Lock 내에서 스냅샷만 빠르게 캡처
                            new_trades = list(bot_instance.executor.trade_history[_last_trade_count:current_count])
                            _last_trade_count = current_count

                # Lock 해제 후 I/O (WebSocket emit은 느릴 수 있으므로 Lock 밖)
                for trade in new_trades:
                    sym = trade.get("symbol", "")
                    display_name = get_stock_display_name(sym)
                    trade_data = {
                        "order_id": trade.get("order_id", ""),
                        "symbol": sym,
                        "name": display_name,
                        # ★ side를 대문자로 통일: OrderSide.BUY.value="buy"(소문자)
                        # 프론트엔드는 "BUY"/"SELL"(대문자)로 비교하므로 .upper() 필수
                        "side": trade.get("side", "").upper(),
                        "quantity": trade.get("quantity", 0),
                        "price": trade.get("price", 0),
                        "total": trade.get("total", 0),
                        "strategy": trade.get("strategy", ""),
                        "timestamp": trade.get("timestamp", datetime.now()).isoformat()
                            if hasattr(trade.get("timestamp", ""), "isoformat")
                            else str(trade.get("timestamp", "")),
                    }
                    socketio.emit("trade_executed", trade_data)

                    side_kr = "매수" if trade_data["side"] == "BUY" else "매도"
                    _add_activity(
                        action=trade_data["side"].lower(),
                        detail=f"{side_kr}: {display_name} "
                               f"{trade_data['quantity']}주 @ "
                               f"{trade_data['price']:,.2f}",
                        level="success" if trade_data["side"] == "BUY" else "warning"
                    )

                if new_trades:
                    bot_status["total_trades"] = _last_trade_count

                # ── equity 스냅샷 저장 (5분마다) ──
                # PaperExecutor에 DB가 연결되어 있으면, 5분 주기로
                # 현재 자산 상태를 equity_history 테이블에 기록
                # → 보고서의 equity curve 차트 + MDD 계산에 사용됨
                now = time.time()
                if now - _last_snapshot_time >= _dash_cfg.equity_snapshot_interval:
                    try:
                        if hasattr(bot_instance.executor, 'save_equity_snapshot'):
                            bot_instance.executor.save_equity_snapshot()
                    except Exception as e:
                        logger.debug(f"[브로드캐스터] equity 스냅샷 저장 실패: {e}")
                    _last_snapshot_time = now

            # WebSocket으로 전체 상태 브로드캐스트
            socketio.emit("status_update", bot_status)
        except Exception as e:
            # ★ 에러를 로그로 남겨야 디버깅 가능
            # 단, broadcaster가 멈추면 안 되므로 try-except는 유지
            logger.warning(f"[브로드캐스터] 상태 브로드캐스트 실패: {e}")


def _run_single_analysis(symbol: str):
    """단일 종목 즉시 분석 (백그라운드)"""
    try:
        from collectors.price_us import PriceCollectorUS
        from collectors.price_kr import PriceCollectorKR
        from analyzers.technical import TechnicalAnalyzer
        from config.settings import TechnicalConfig

        # 시장 판별 (통합 유틸리티 사용)
        market = detect_market(symbol)
        if market == "KR":
            collector = PriceCollectorKR()
        else:
            collector = PriceCollectorUS()

        df = collector.safe_collect(symbol, period="6mo")
        if df is None or df.empty:
            socketio.emit("analysis_result", {
                "symbol": symbol, "error": "No data"
            })
            return

        analyzer = TechnicalAnalyzer(TechnicalConfig())
        df_analyzed = analyzer.calculate_all(df)
        signal = analyzer.generate_signal(df_analyzed)

        # 종목 정보 가져오기
        stock_info = _get_stock_info(symbol)

        # 추가 데이터: 최근 변동률, 거래량
        close = df_analyzed["Close"]
        volume = df_analyzed["Volume"] if "Volume" in df_analyzed.columns else None
        change_1d = ((close.iloc[-1] / close.iloc[-2]) - 1) * 100 if len(close) > 1 else 0
        change_5d = ((close.iloc[-1] / close.iloc[-5]) - 1) * 100 if len(close) > 5 else 0
        change_20d = ((close.iloc[-1] / close.iloc[-20]) - 1) * 100 if len(close) > 20 else 0

        result = {
            "symbol": symbol,
            "name": stock_info.get("name", symbol),
            "market": market,
            "sector": stock_info.get("sector", ""),
            "signal": signal.signal,
            "strength": signal.strength,
            "reasons": signal.reasons,
            "price": float(close.iloc[-1]),
            "change_1d": round(change_1d, 2),
            "change_5d": round(change_5d, 2),
            "change_20d": round(change_20d, 2),
            "volume": int(volume.iloc[-1]) if volume is not None and len(volume) > 0 else 0,
            "high_52w": float(close.tail(252).max()) if len(close) >= 252 else float(close.max()),
            "low_52w": float(close.tail(252).min()) if len(close) >= 252 else float(close.min()),
            "rsi": float(df_analyzed["RSI"].iloc[-1]) if "RSI" in df_analyzed.columns else 0,
            "atr": float(df_analyzed["ATR"].iloc[-1]) if "ATR" in df_analyzed.columns else 0,
            "timestamp": datetime.now().isoformat(),
        }

        # 신호 목록에 추가
        bot_status["signals_today"].append(result)
        if len(bot_status["signals_today"]) > _dash_cfg.signals_log_max:
            bot_status["signals_today"] = bot_status["signals_today"][-_dash_cfg.signals_log_max:]

        socketio.emit("analysis_result", result)
        logger.info(f"[분석] {symbol} ({stock_info.get('name', '')}): "
                    f"{signal.signal} (강도: {signal.strength:.2f})")

    except Exception as e:
        socketio.emit("analysis_result", {
            "symbol": symbol, "error": str(e)
        })






@app.route("/api/server/restart", methods=["POST"])
def api_restart_server():
    """
    서버 재시작 API

    봇이 실행 중이면 먼저 중지한 후 서버 프로세스를 재시작합니다.
    os.execv()를 사용하여 동일한 인자로 프로세스를 교체합니다.
    """
    global bot_instance, bot_thread

    try:
        # 봇 실행 중이면 먼저 중지
        if bot_status["running"] and bot_instance:
            try:
                bot_instance.running = False
                if hasattr(bot_instance, 'scheduler'):
                    bot_instance.scheduler.stop()
                bot_status["running"] = False
                logger.info("[대시보드] 재시작 전 봇 중지 완료")
            except Exception:
                pass

        logger.info("[대시보드] 서버 재시작 요청 수신")

        # 별도 스레드에서 약간의 딜레이 후 재시작 (응답 먼저 보내기 위해)
        def _do_restart():
            time.sleep(1)
            os.execv(sys.executable, [sys.executable] + sys.argv)

        restart_thread = threading.Thread(target=_do_restart, daemon=True)
        restart_thread.start()

        return jsonify({"success": True, "message": "서버가 재시작됩니다..."})

    except Exception as e:
        logger.error(f"[대시보드] 서버 재시작 실패: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/pnl/today")
def api_pnl_today():
    """
    오늘의 실현 손익 조회

    오늘(KST 자정 기준) 발생한 매도 거래의 pnl을 합산합니다.
    KPI 카드 + 거래 이력 손익 요약 갱신용.

    Returns:
        {
            "date": "2026-05-10",
            "total_pnl": 125000.0,
            "sell_count": 5,
            "win_count": 3,
            "loss_count": 2,
            "win_rate": 60.0,
            "total_value_krw": 5_000_000.0,   # 매수 + 매도 거래액 합산
            "buy_count": 8,
        }
    """
    try:
        today_str = datetime.now().strftime("%Y-%m-%d")
        # ★ Phase 5: 현재 모드 거래만 (paper/live 분리)
        mode_filter = _current_display_mode()

        with DatabaseManager() as db:
            # 오늘자 거래만 (LIKE 패턴 매칭, mode 필터)
            cursor = db.conn.execute(
                "SELECT side, pnl, total_value FROM trades "
                "WHERE timestamp LIKE ? AND mode = ? ORDER BY timestamp DESC",
                (f"{today_str}%", mode_filter)
            )
            rows = cursor.fetchall()

        total_pnl = 0.0
        sell_count = 0
        win_count = 0
        loss_count = 0
        buy_count = 0
        total_value_krw = 0.0

        for row in rows:
            side = (row["side"] or "").upper()
            pnl = float(row["pnl"] or 0)
            tv = float(row["total_value"] or 0)
            total_value_krw += tv
            if side == "SELL":
                sell_count += 1
                total_pnl += pnl
                if pnl > 0:
                    win_count += 1
                elif pnl < 0:
                    loss_count += 1
            elif side == "BUY":
                buy_count += 1

        decided = win_count + loss_count
        win_rate = (win_count / decided * 100) if decided > 0 else 0

        return jsonify({
            "date": today_str,
            "total_pnl": round(total_pnl, 2),
            "sell_count": sell_count,
            "buy_count": buy_count,
            "win_count": win_count,
            "loss_count": loss_count,
            "win_rate": round(win_rate, 2),
            "total_value_krw": round(total_value_krw, 2),
        })
    except Exception as e:
        logger.warning(f"[손익] 오늘 손익 조회 실패: {e}")
        return jsonify({
            "date": datetime.now().strftime("%Y-%m-%d"),
            "total_pnl": 0, "sell_count": 0, "buy_count": 0,
            "win_count": 0, "loss_count": 0, "win_rate": 0,
            "total_value_krw": 0, "error": str(e),
        })


@app.route("/api/market/halt")
def api_market_halt():
    """
    시장 정지 상태 조회 (서킷브레이커/사이드카/VI)

    봇이 실행 중이면 현재 캐시된 상태 반환,
    실행 중이 아니면 즉석에서 한 번 체크 후 반환.

    실거래 안전성을 위한 핵심 엔드포인트.
    """
    global bot_instance

    # 봇 실행 중: 봇의 halt_detector 캐시 사용
    if bot_instance and hasattr(bot_instance, 'halt_detector'):
        return jsonify(bot_instance.halt_detector.get_status_summary())

    # 봇 미실행: 즉석 1회 체크
    try:
        from strategy.market_halt_detector import MarketHaltDetector
        detector = MarketHaltDetector(kis_client=get_kis_price_client())
        detector.check(holding_symbols=[])
        return jsonify(detector.get_status_summary())
    except Exception as e:
        return jsonify({
            "checked": False,
            "can_trade_new": True,
            "can_trade_exit": True,
            "message": f"조회 실패: {e}",
        })


@app.route("/api/kis/status")
def api_kis_status():
    """
    KIS API 연결 상태 확인

    환경변수 설정 → 토큰 발급 → 시세 조회 → 잔고 조회 4단계를 모두 검증해서
    어디서 막히는지 정확히 표시합니다. 특히 "토큰은 됐는데 잔고가 0원"인
    EGW02007 류 오류를 대시보드에서 즉시 발견할 수 있습니다.
    """
    app_key = os.environ.get("KIS_APP_KEY", "")
    app_secret = os.environ.get("KIS_APP_SECRET", "")
    account = os.environ.get("KIS_ACCOUNT", "")
    paper_str = os.environ.get("KIS_PAPER", "true").lower()
    paper = paper_str in ("true", "1", "yes")

    result = {
        "configured": bool(app_key and app_secret and account),
        "mode": "paper" if paper else "live",
        "connected": False,
        "token_expires": None,
        "account_masked": "",
        "test_price": None,      # 삼성전자 시세로 시세 엔드포인트 검증
        "balance_ok": False,      # 잔고 조회 가능 여부 (실거래 가능성 판단)
        "balance_krw": None,      # 매수가능 현금 (KRW)
        "balance_error": None,    # 잔고 조회 실패 원인 (EGW02007 등)
        "warnings": [],           # 사용자에게 보여줄 경고 모음
    }

    if not result["configured"]:
        result["message"] = "환경변수 미설정 (.env에 KIS_APP_KEY/SECRET/ACCOUNT 입력 필요)"
        return jsonify(result)

    # 계좌번호 마스킹 (앞 4자리만)
    if len(account) >= 6:
        result["account_masked"] = account[:4] + "****" + account[-3:]

    # 클라이언트 가져오기 (싱글톤, 토큰 자동 발급)
    client = get_kis_price_client()
    if not client:
        result["message"] = "토큰 발급 실패 (자격증명 확인 또는 모의투자/실거래 매칭 확인)"
        return jsonify(result)

    result["connected"] = True
    if client.token_expires:
        result["token_expires"] = client.token_expires.isoformat()

    # ── [3] 시세 엔드포인트 검증 (삼성전자) ──
    try:
        test_price = client.get_current_price("005930")
        if test_price and test_price > 0:
            result["test_price"] = test_price
        else:
            result["warnings"].append("시세 조회 0원 반환 — KIS 서버 응답 이상")
    except Exception as e:
        result["warnings"].append(f"시세 검증 실패: {e}")

    # ── [4] 잔고 엔드포인트 검증 (실제 매매 가능성 판단) ──
    # 이게 핵심: 토큰만 발급되고 잔고가 안 잡히는 EGW02007 패턴을 잡아냄
    try:
        import requests as _rq
        tr_id = "VTTC8434R" if paper else "TTTC8434R"
        url = f"{client.base_url}/uapi/domestic-stock/v1/trading/inquire-balance"
        params = {
            "CANO": client.cano,
            "ACNT_PRDT_CD": client.acnt_prdt_cd,
            "AFHR_FLPR_YN": "N", "OFL_YN": "", "INQR_DVSN": "02",
            "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
        }
        headers = client._get_headers(tr_id)
        r = _rq.get(url, headers=headers, params=params, timeout=5)
        if r.status_code == 200:
            data = r.json()
            rt_cd = data.get("rt_cd", "")
            msg_cd = data.get("msg_cd", "")
            msg1 = data.get("msg1", "")
            if rt_cd == "0":
                # 정상 — 매수가능액 추출
                output2 = data.get("output2", [])
                if output2:
                    summary = output2[0]
                    nass = float(summary.get("nass_amt", 0) or 0)
                    dnca = float(summary.get("dnca_tot_amt", 0) or 0)
                    result["balance_ok"] = True
                    result["balance_krw"] = max(nass, dnca)
            else:
                # KIS 오류 코드별 사용자 안내
                result["balance_error"] = {
                    "code": msg_cd,
                    "message": msg1,
                }
                if msg_cd == "EGW02007":
                    result["warnings"].append(
                        f"⚠️ 앱키-모드 불일치: '{msg1.strip()}' — "
                        f"API_KEYS.txt에서 KIS_PAPER='"
                        f"{'false' if paper else 'true'}'로 변경하거나 "
                        f"{'모의투자' if paper else '실거래'}용 앱키를 발급받으세요"
                    )
                elif msg_cd == "EGW00121":
                    result["warnings"].append(
                        f"⚠️ 계좌번호 오류: '{msg1.strip()}' — "
                        f"한국투자 앱에서 정확한 계좌번호 재확인 필요"
                    )
                elif msg_cd == "EGW00201":
                    result["warnings"].append(
                        f"⚠️ 실전투자 API 미신청: '{msg1.strip()}' — "
                        f"한국투자 앱 > Open API 메뉴에서 신청 필요"
                    )
                else:
                    result["warnings"].append(
                        f"⚠️ 잔고 조회 오류 [{msg_cd}]: {msg1.strip()}"
                    )
        else:
            result["balance_error"] = {
                "code": f"HTTP_{r.status_code}",
                "message": r.text[:200],
            }
            result["warnings"].append(f"잔고 API HTTP 오류: {r.status_code}")
    except Exception as e:
        result["balance_error"] = {"code": "EXCEPTION", "message": str(e)}
        result["warnings"].append(f"잔고 검증 예외: {e}")

    # ── 최종 메시지 ──
    if result["balance_ok"]:
        result["message"] = f"정상 — 매수가능 ₩{result['balance_krw']:,.0f}"
    elif result["test_price"]:
        result["message"] = "시세는 조회되지만 잔고 조회 실패 — 위 경고 확인"
    else:
        result["message"] = "KIS 연결 부분 실패 — 위 경고 확인"

    return jsonify(result)


def _fetch_naver_index(name: str) -> Optional[Dict]:
    """
    Naver Finance에서 한국 지수 실시간 값 조회

    Naver의 모바일 주식 API는 KRX 데이터를 실시간으로 중계합니다.
    장중에는 ~1-5초 지연, 장 종료 후에는 종가를 반환합니다.

    Parameters:
        name: "KOSPI" 또는 "KOSDAQ"

    Returns:
        {"price", "change", "change_pct", "traded_at"} 또는 None
    """
    import requests as _rq
    try:
        url = f"https://m.stock.naver.com/api/index/{name}/basic"
        r = _rq.get(
            url,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=5,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        # closePrice는 "7,968.63" 형태로 컴마 포함
        price_str = str(data.get("closePrice", "")).replace(",", "")
        change_str = str(data.get("compareToPreviousClosePrice", "0")).replace(",", "")
        pct_str = str(data.get("fluctuationsRatio", "0")).replace(",", "")
        if not price_str:
            return None
        price = float(price_str)
        if price <= 0:
            return None
        return {
            "price": price,
            "change": float(change_str) if change_str else 0.0,
            "change_pct": float(pct_str) if pct_str else 0.0,
            "traded_at": data.get("localTradedAt", ""),
        }
    except Exception as e:
        logger.debug(f"[지수] Naver {name} 조회 실패: {e}")
        return None


@app.route("/api/market/indices")
def api_market_indices():
    """
    주요 시장 지수 조회 API (KOSPI, KOSDAQ, NASDAQ, S&P500, Dow, USD/KRW)

    데이터 소스 우선순위 (한국 지수):
      1. Naver Finance — KRX 데이터를 ~1-5초 지연으로 중계, 가장 정확
      2. KIS 실거래 API — 실시간이지만 모의투자 서버는 가짜 값을 반환하므로 제외
      3. yfinance — 15-20분 지연, 마지막 fallback

    데이터 소스 우선순위 (미국 지수 + 환율):
      1. yfinance — KIS는 해외 지수를 무료로 제공하지 않음

    KIS 모의투자 서버 차단:
      모의투자 서버는 KOSPI 7000+ 같은 비현실적 값을 반환하므로
      paper=True인 경우 KIS 지수 API를 호출하지 않습니다.

    캐시: 30초 TTL (실시간성을 위해 60초 → 30초로 단축).

    Returns:
        {symbol: {name, price, change, change_pct, source, traded_at?}, ...}
        source: "naver" | "kis" | "yfinance"
    """
    global _market_indices_cache, _market_indices_cache_time

    # 캐시 확인 (30초 TTL)
    now = time.time()
    if _market_indices_cache and (now - _market_indices_cache_time) < 30:
        return jsonify(_market_indices_cache)

    result = {}
    kr_obtained = set()

    # ─────────────────────────────────────────────────────────
    # 1순위: Naver Finance (한국 지수, 실시간 ~1-5초 지연)
    # ─────────────────────────────────────────────────────────
    for name, yf_sym in [("KOSPI", "^KS11"), ("KOSDAQ", "^KQ11")]:
        q = _fetch_naver_index(name)
        if q is None:
            continue
        result[yf_sym] = {
            "name": name,
            "price": q["price"],
            "change": q["change"],
            "change_pct": q["change_pct"],
            "source": "naver",
            "traded_at": q.get("traded_at", ""),
        }
        kr_obtained.add(yf_sym)

    # ─────────────────────────────────────────────────────────
    # 2순위: KIS API (실거래 모드에서만)
    # ─ 모의투자 서버는 지수 값을 가짜로 반환하므로 사용 금지
    # ─────────────────────────────────────────────────────────
    if len(kr_obtained) < 2:
        kis_client = get_kis_price_client()
        if kis_client and not getattr(kis_client, "paper", True):
            try:
                for name, code, yf_sym in [
                    ("KOSPI", "0001", "^KS11"),
                    ("KOSDAQ", "1001", "^KQ11"),
                ]:
                    if yf_sym in kr_obtained:
                        continue
                    q = kis_client.get_index_quote(code)
                    if not q or q.get("price", 0) <= 0:
                        continue
                    result[yf_sym] = {
                        "name": name,
                        "price": q.get("price", 0),
                        "change": q.get("change", 0),
                        "change_pct": q.get("change_pct", 0),
                        "source": "kis",
                    }
                    kr_obtained.add(yf_sym)
            except Exception as e:
                logger.debug(f"[지수] KIS 조회 실패: {e}")
        elif kis_client and getattr(kis_client, "paper", True):
            logger.debug(
                "[지수] KIS 모의투자 모드 — 지수는 Naver/yfinance만 사용 "
                "(모의 서버는 가짜 지수 반환)"
            )

    # ─────────────────────────────────────────────────────────
    # 3순위: yfinance — 미국 지수 + 환율 + (KR 못 가져왔으면) KR fallback
    # ─────────────────────────────────────────────────────────
    us_indices = {
        "^IXIC": "NASDAQ",
        "^GSPC": "S&P 500",
        "^DJI": "Dow Jones",
        "USDKRW=X": "USD/KRW",
    }
    if "^KS11" not in kr_obtained:
        us_indices["^KS11"] = "KOSPI"
    if "^KQ11" not in kr_obtained:
        us_indices["^KQ11"] = "KOSDAQ"

    try:
        import yfinance as yf
        data = yf.download(
            list(us_indices.keys()),
            period="2d",
            interval="1d",
            progress=False,
            threads=True,
        )

        if not data.empty and "Close" in data:
            for sym in us_indices:
                try:
                    closes = (
                        data["Close"][sym].dropna()
                        if len(us_indices) > 1
                        else data["Close"].dropna()
                    )
                    if len(closes) < 1:
                        continue
                    curr = float(closes.iloc[-1])
                    name = us_indices[sym]
                    if len(closes) >= 2:
                        prev = float(closes.iloc[-2])
                        change = curr - prev
                        change_pct = (change / prev * 100) if prev > 0 else 0.0
                    else:
                        change = 0.0
                        change_pct = 0.0
                    result[sym] = {
                        "name": name,
                        "price": curr,
                        "change": change,
                        "change_pct": change_pct,
                        "source": "yfinance",
                    }
                except (KeyError, AttributeError, IndexError):
                    continue
    except Exception as e:
        logger.warning(f"[지수] yfinance 조회 실패: {e}")

    _market_indices_cache = result
    _market_indices_cache_time = now
    return jsonify(result)


@app.route("/api/market/status")
def api_market_status():
    """
    한국/미국 시장 개장 상태 API

    현재 시각 기준으로 한국(KRX) 및 미국(NYSE/NASDAQ) 시장의
    개장 여부, 남은 시간, 이번 주 일정을 반환합니다.

    한국 시장: 09:00~15:30 KST (평일)
    미국 시장: 09:30~16:00 ET = 23:30~06:00+1 KST (서머타임 기준)

    한국 공휴일 및 미국 공휴일은 별도 체크합니다.
    """
    from datetime import datetime, timedelta
    import calendar

    now_kst = datetime.now()  # 서버 시간 = KST 가정
    weekday = now_kst.weekday()  # 0=월~6=일
    hour = now_kst.hour
    minute = now_kst.minute
    time_minutes = hour * 60 + minute

    # ── 한국 시장 (KRX) ──
    kr_open = 9 * 60  # 09:00
    kr_close = 15 * 60 + 30  # 15:30

    kr_is_open = False
    kr_status = "휴장"
    kr_next = ""

    if weekday < 5:  # 평일
        if time_minutes < kr_open:
            kr_status = "개장 전"
            remaining = kr_open - time_minutes
            kr_next = f"개장까지 {remaining // 60}시간 {remaining % 60}분"
        elif time_minutes < kr_close:
            kr_is_open = True
            kr_status = "거래 중"
            remaining = kr_close - time_minutes
            kr_next = f"폐장까지 {remaining // 60}시간 {remaining % 60}분"
        else:
            kr_status = "폐장"
            # 다음 영업일 개장까지
            if weekday == 4:  # 금요일
                kr_next = "다음 거래일: 월요일 09:00"
            else:
                kr_next = "다음 거래일: 내일 09:00"
    else:
        kr_status = "주말 휴장"
        days_until = 7 - weekday  # 월요일까지 남은 일수
        kr_next = f"다음 거래일: 월요일 09:00 ({days_until}일 후)"

    # ── 미국 시장 (NYSE/NASDAQ) ──
    # ET = KST - 14시간 (서머타임), KST - 13시간 (겨울)
    # 서머타임 (3월~11월) 기준: 개장 23:30 KST, 폐장 06:00 KST (+1일)
    us_open_kst = 23 * 60 + 30   # 23:30 KST
    us_close_kst = 6 * 60        # 06:00 KST (다음날)

    us_is_open = False
    us_status = "휴장"
    us_next = ""

    # 미국 장 시간은 KST 기준 23:30 ~ 다음날 06:00
    if weekday < 5:  # 월~금
        if time_minutes >= us_open_kst:
            # 23:30 이후 = 미국 장 시작 (월~금 밤)
            us_is_open = True
            us_status = "거래 중"
            remaining = (24 * 60 - time_minutes) + us_close_kst
            us_next = f"폐장까지 {remaining // 60}시간 {remaining % 60}분"
        elif time_minutes < us_close_kst and weekday > 0:
            # 00:00~06:00 = 전날 밤 시작된 미국 장 (화~토 새벽)
            us_is_open = True
            us_status = "거래 중"
            remaining = us_close_kst - time_minutes
            us_next = f"폐장까지 {remaining // 60}시간 {remaining % 60}분"
        elif time_minutes < us_open_kst:
            us_status = "개장 전"
            remaining = us_open_kst - time_minutes
            us_next = f"개장까지 {remaining // 60}시간 {remaining % 60}분"
        else:
            us_status = "폐장"
            us_next = "다음 거래일: 오늘 23:30"
    elif weekday == 5:  # 토요일
        if time_minutes < us_close_kst:
            # 금요일 밤 시작된 장 (토요일 새벽)
            us_is_open = True
            us_status = "거래 중"
            remaining = us_close_kst - time_minutes
            us_next = f"폐장까지 {remaining // 60}시간 {remaining % 60}분"
        else:
            us_status = "주말 휴장"
            us_next = "다음 거래일: 월요일 23:30"
    else:  # 일요일
        us_status = "주말 휴장"
        us_next = "다음 거래일: 월요일 23:30"

    # ── 이번 달 거래일 캘린더 ──
    year = now_kst.year
    month = now_kst.month
    today = now_kst.day
    _, days_in_month = calendar.monthrange(year, month)

    cal_days = []
    for d in range(1, days_in_month + 1):
        dt = datetime(year, month, d)
        wd = dt.weekday()
        is_trading = wd < 5  # 주말 제외 (공휴일은 별도 DB 필요)
        cal_days.append({
            "day": d,
            "weekday": ["월","화","수","목","금","토","일"][wd],
            "is_trading": is_trading,
            "is_today": d == today,
            "is_past": d < today,
        })

    return jsonify({
        "server_time": now_kst.strftime("%Y-%m-%d %H:%M:%S"),
        "kr": {
            "name": "한국 (KRX)",
            "is_open": kr_is_open,
            "status": kr_status,
            "next": kr_next,
            "hours": "09:00 ~ 15:30 KST",
        },
        "us": {
            "name": "미국 (NYSE)",
            "is_open": us_is_open,
            "status": us_status,
            "next": us_next,
            "hours": "23:30 ~ 06:00 KST (ET 09:30~16:00)",
        },
        "calendar": {
            "year": year,
            "month": month,
            "month_name": f"{year}년 {month}월",
            "days": cal_days,
        },
    })

def _search_kr_stocks(query: str) -> list:
    """한국 주식 검색 (pykrx 종목 리스트에서 매칭)"""
    results = []
    try:
        from pykrx import stock as pykrx_stock
        # pykrx에서 전체 종목 리스트 가져오기
        # 코스피
        kospi_tickers = pykrx_stock.get_market_ticker_list(market="KOSPI")
        for ticker in kospi_tickers:
            name = pykrx_stock.get_market_ticker_name(ticker)
            if query.upper() in ticker or query in name:
                results.append({
                    "symbol": f"{ticker}.KS",
                    "name": name,
                    "market": "KOSPI",
                    "type": "stock"
                })
                if len(results) >= 10:
                    break

        if len(results) < 10:
            # 코스닥
            kosdaq_tickers = pykrx_stock.get_market_ticker_list(market="KOSDAQ")
            for ticker in kosdaq_tickers:
                name = pykrx_stock.get_market_ticker_name(ticker)
                if query.upper() in ticker or query in name:
                    results.append({
                        "symbol": f"{ticker}.KQ",
                        "name": name,
                        "market": "KOSDAQ",
                        "type": "stock"
                    })
                    if len(results) >= 10:
                        break

    except Exception as e:
        logger.warning(f"[검색] 한국 종목 검색 실패: {e}")

    return results


def _search_us_stocks(query: str) -> list:
    """
    미국 종목 검색 (하드코딩된 주요 종목 + yfinance)

    주요 미국 종목은 사전에 매핑하여 빠르게 검색하고,
    찾지 못하면 yfinance ticker.info를 시도합니다.
    """
    results = []
    query_upper = query.upper()

    # 주요 미국 종목 사전 (빠른 검색용)
    _US_STOCKS = {
        "AAPL": "Apple Inc.", "MSFT": "Microsoft Corp.", "GOOG": "Alphabet Inc.",
        "GOOGL": "Alphabet Inc.", "AMZN": "Amazon.com Inc.", "NVDA": "NVIDIA Corp.",
        "META": "Meta Platforms Inc.", "TSLA": "Tesla Inc.", "BRK.B": "Berkshire Hathaway",
        "UNH": "UnitedHealth Group", "JNJ": "Johnson & Johnson", "JPM": "JPMorgan Chase",
        "V": "Visa Inc.", "PG": "Procter & Gamble", "MA": "Mastercard Inc.",
        "HD": "Home Depot Inc.", "CVX": "Chevron Corp.", "MRK": "Merck & Co.",
        "ABBV": "AbbVie Inc.", "LLY": "Eli Lilly", "PEP": "PepsiCo Inc.",
        "KO": "Coca-Cola Co.", "COST": "Costco Wholesale", "AVGO": "Broadcom Inc.",
        "TMO": "Thermo Fisher", "MCD": "McDonald's Corp.", "WMT": "Walmart Inc.",
        "CSCO": "Cisco Systems", "ACN": "Accenture plc", "ABT": "Abbott Laboratories",
        "DHR": "Danaher Corp.", "ADBE": "Adobe Inc.", "CRM": "Salesforce Inc.",
        "NKE": "Nike Inc.", "TXN": "Texas Instruments", "NEE": "NextEra Energy",
        "PM": "Philip Morris", "UNP": "Union Pacific", "RTX": "RTX Corp.",
        "HON": "Honeywell Intl.", "LOW": "Lowe's Companies", "SPGI": "S&P Global",
        "BA": "Boeing Co.", "INTC": "Intel Corp.", "AMD": "AMD Inc.",
        "QCOM": "Qualcomm Inc.", "CAT": "Caterpillar Inc.", "GS": "Goldman Sachs",
        "MS": "Morgan Stanley", "BLK": "BlackRock Inc.", "SCHW": "Charles Schwab",
        "AMAT": "Applied Materials", "LRCX": "Lam Research", "MU": "Micron Technology",
        "NFLX": "Netflix Inc.", "DIS": "Walt Disney Co.", "PYPL": "PayPal Holdings",
        "XYZ": "Block Inc.", "COIN": "Coinbase Global", "PLTR": "Palantir Technologies",
        "SNOW": "Snowflake Inc.", "UBER": "Uber Technologies", "ABNB": "Airbnb Inc.",
        "RIVN": "Rivian Automotive", "LCID": "Lucid Group", "SOFI": "SoFi Technologies",
        "SPY": "SPDR S&P 500 ETF", "QQQ": "Invesco QQQ Trust", "IWM": "iShares Russell 2000",
        "DIA": "SPDR Dow Jones", "VTI": "Vanguard Total Market",
        "ARKK": "ARK Innovation ETF", "XLF": "Financial Select SPDR",
        "SMCI": "Super Micro Computer", "ENPH": "Enphase Energy",
        "SOXX": "iShares Semiconductor ETF", "XLK": "Technology Select SPDR",
        "XLE": "Energy Select SPDR", "XLV": "Health Care Select SPDR",
    }

    for symbol, name in _US_STOCKS.items():
        if query_upper in symbol or query.lower() in name.lower():
            results.append({
                "symbol": symbol,
                "name": name,
                "market": "US",
                "type": "stock"
            })
            if len(results) >= 10:
                break

    # yfinance 폴백 (사전에 없는 종목)
    if not results:
        try:
            import yfinance as yf
            ticker = yf.Ticker(query_upper)
            info = ticker.info
            if info and info.get("shortName"):
                results.append({
                    "symbol": query_upper,
                    "name": info["shortName"],
                    "market": "US",
                    "type": "stock"
                })
        except Exception:
            pass

    return results


# =============================================================================
# 서버 실행
# =============================================================================

if __name__ == "__main__":
    # ── werkzeug 중복 로그 억제 ──
    # Flask의 werkzeug 서버가 HTTP 요청 로그를 별도로 출력하면
    # quantbot 로거와 겹쳐서 콘솔이 지저분해집니다.
    # WARNING 이상만 표시하여 핵심 에러만 노출합니다.
    import logging as _logging
    _logging.getLogger("werkzeug").setLevel(_logging.WARNING)
    _logging.getLogger("engineio").setLevel(_logging.WARNING)

    logger.info(f"Dashboard starting on http://localhost:5000")

    # ── 상태 브로드캐스터 스레드 시작 ──
    # _status_broadcaster()는 봇 상태(포지션, 잔고, PnL 등)를
    # 주기적으로 WebSocket 클라이언트에게 전송하는 백그라운드 작업.
    # ★ 이 스레드가 없으면 대시보드에 포지션이 표시되지 않음!
    import threading
    broadcast_thread = threading.Thread(
        target=_status_broadcaster,
        daemon=True,  # 메인 프로세스 종료 시 자동 정리
        name="status-broadcaster"
    )
    broadcast_thread.start()
    logger.info("[Dashboard] 상태 브로드캐스터 스레드 시작됨")

    # ── 디스코드 봇 자동 시작 ──
    # 설정에 토큰이 있고 autostart가 켜져있으면 대시보드 시작 시 자동 연결
    if current_settings.get("discord_bot_token") and current_settings.get("discord_bot_autostart", False):
        try:
            _start_discord_bot()
        except Exception as e:
            logger.warning(f"[Discord Bot] 자동 시작 실패: {e}")

    socketio.run(
        app,
        host="0.0.0.0",
        port=5000,
        debug=False,
        allow_unsafe_werkzeug=True,
    )
