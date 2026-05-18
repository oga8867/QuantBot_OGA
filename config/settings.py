"""
=============================================================================
config/settings.py - 퀀트봇 전역 설정 파일
=============================================================================

모든 설정값을 한 곳에서 중앙 관리합니다.
사용자가 CLI에서 --capital, --risk 등으로 오버라이드할 수 있습니다.

핵심 개념:
- DEFAULT_CAPITAL: 총 투자 가능 자본금 (원 또는 달러)
- RISK_PER_TRADE: 1회 거래당 감수할 수 있는 최대 손실 비율
  예) 0.02 = 자본금의 2%까지만 1건의 거래에서 잃겠다는 뜻
- MAX_POSITION_SIZE: 한 종목에 넣을 수 있는 최대 비율
  예) 0.05 = 자본금의 5%를 초과하여 한 종목에 투자하지 않음

이 값들은 리스크 관리의 근간이며, 수익률보다 중요합니다.
=============================================================================
"""

import os
from dataclasses import dataclass, field
from typing import Optional


# =============================================================================
# 자본금 & 리스크 설정 (사용자가 반드시 자신의 상황에 맞게 조정해야 함)
# =============================================================================

@dataclass
class CapitalConfig:
    """
    자본금 설정 클래스

    사용자가 직접 자본량을 입력하여 포지션 사이징에 반영합니다.
    CLI에서 오버라이드 가능: --capital 50000000
    """
    # 총 투자 자본금 (기본값: 1,000만원 또는 $10,000)
    total_capital: float = 10_000_000

    # 통화 단위 ('KRW' 또는 'USD')
    currency: str = "KRW"

    # 현재 현금 보유량 (자동 업데이트됨, 초기에는 total_capital과 동일)
    available_cash: Optional[float] = None

    def __post_init__(self):
        """초기화 후 available_cash가 None이면 total_capital로 설정"""
        if self.available_cash is None:
            self.available_cash = self.total_capital


@dataclass
class RiskConfig:
    """
    리스크 관리 설정 클래스

    모든 리스크 파라미터를 독립적으로 설정할 수 있습니다.
    CLI에서 오버라이드 가능: --risk-per-trade 0.01 --max-drawdown 0.08

    ★ 중요: 이 값들은 '벌 수 있는 돈'이 아니라 '잃을 수 있는 돈'을 제어합니다.
    보수적으로 설정할수록 생존 확률이 높아집니다.
    """

    # ─── 거래 단위 리스크 ─────────────────────────────────────────────

    # 1회 거래당 최대 손실 비율 (자본금 대비)
    # 예: 0.02 = 1건 거래에서 최대 자본금의 2%까지만 손실 허용
    # 권장: 초보자 0.01(1%), 중급 0.02(2%), 공격적 0.03(3%)
    risk_per_trade: float = 0.02

    # 단일 종목 최대 포지션 크기 (자본금 대비, 비율 한도)
    # 예: 0.10 = 한 종목에 자본금의 10% 이상 투자 금지
    max_position_size: float = 0.10

    # 단일 주문 최대 금액 (절대값, 원). 0이면 자동(자본금의 20%)으로 계산.
    # max_position_size(비율)와 함께 적용되며 둘 중 더 엄격한 한도가 우선.
    max_order_value: float = 0.0

    # ─── 포트폴리오 레벨 리스크 ───────────────────────────────────────

    # 일일 최대 손실 한도 (이 이상 손실 시 당일 거래 중단)
    max_daily_loss: float = 0.03  # 3%

    # 총 최대 낙폭 (MDD) 한도 (이 이상이면 전체 시스템 중단)
    max_drawdown: float = 0.15  # 15%

    # 동시 보유 가능 최대 포지션 수
    max_positions: int = 10

    # ─── 손절/익절 ────────────────────────────────────────────────────

    # 기본 손절 배수 (ATR의 몇 배에서 손절할지)
    # ATR = Average True Range, 일일 평균 변동폭
    # 예: 2.0 = 평균 변동폭의 2배 하락 시 손절
    stop_loss_atr_multiplier: float = 2.0

    # 리스크:보상 비율 (최소 이 비율 이상의 기대 수익이 있을 때만 진입)
    # 예: 2.0 = 1만원 손실 감수 시 최소 2만원 기대 수익이 있어야 진입
    risk_reward_ratio: float = 2.0

    # ─── 상관관계 제한 ────────────────────────────────────────────────

    # 보유 종목 간 최대 허용 상관계수
    # 높은 상관관계 종목을 동시에 들고 있으면 분산 효과가 없음
    max_correlation: float = 0.7

    # ─── 포지션 사이징 방법 ───────────────────────────────────────────

    # 'kelly' = Kelly Criterion (Half-Kelly)
    # 'fixed_ratio' = 고정 비율 (risk_per_trade 사용)
    # 'equal_weight' = 균등 배분
    sizing_method: str = "kelly"

    # Kelly Criterion 사용 시 축소 계수 (1.0=Full Kelly, 0.5=Half Kelly)
    # Half Kelly 권장: 변동성을 크게 줄이면서 수익의 75%를 유지
    kelly_fraction: float = 0.5

    # Kelly 적용 최소 거래 수 — 닫힌(매도 완료) 거래가 이 수 미만이면
    # 통계(승률·손익비)가 불충분하므로 Kelly 대신 fixed 사이징으로 폴백한다.
    # 거래량 자체를 제한하지 않음 (그 전에도 fixed로 정상 거래).
    kelly_min_trades: int = 20


# =============================================================================
# 데이터 수집 설정
# =============================================================================

@dataclass
class DataConfig:
    """데이터 수집 관련 설정"""

    # 기본 분석 기간 (영업일 기준)
    lookback_days: int = 252  # 약 1년

    # 캐시 만료 시간 (초)
    cache_ttl: int = 3600  # 1시간

    # yfinance 요청 간 대기 시간 (초) - API 제한 방지
    request_delay: float = 0.5

    # FRED API 키 (환경변수에서 로드, 없으면 FRED 데이터 수집 건너뜀)
    fred_api_key: Optional[str] = field(
        default_factory=lambda: os.environ.get("FRED_API_KEY")
    )


# =============================================================================
# 기술적 분석 파라미터
# =============================================================================

@dataclass
class TechnicalConfig:
    """기술적 지표 계산 파라미터"""

    # 이동평균 기간
    sma_short: int = 20    # 단기 이동평균 (20일)
    sma_long: int = 50     # 중기 이동평균 (50일)
    sma_trend: int = 200   # 장기 추세 (200일)

    # RSI (Relative Strength Index) 설정
    rsi_period: int = 14       # RSI 계산 기간
    rsi_oversold: float = 30   # 과매도 기준 (이하면 매수 관심)
    rsi_overbought: float = 70 # 과매수 기준 (이상이면 매도 관심)

    # MACD 설정
    macd_fast: int = 12    # 빠른 EMA 기간
    macd_slow: int = 26    # 느린 EMA 기간
    macd_signal: int = 9   # 시그널 라인 기간

    # 볼린저 밴드 설정
    bb_period: int = 20    # 이동평균 기간
    bb_std: float = 2.0    # 표준편차 배수 (2σ = 약 95% 범위)

    # ATR (Average True Range) 기간
    atr_period: int = 14


# =============================================================================
# 시장 스캐너 설정
# =============================================================================

@dataclass
class ScannerConfig:
    """시장 스캐너 신호 기준값"""

    # 거래량 급증 배수 (20일 평균 대비)
    volume_surge_moderate: float = 2.0  # 보통 급증
    volume_surge_extreme: float = 3.0   # 극단적 급증

    # 가격 급등/급락 기준
    price_change_daily: float = 0.05    # 일일 5% 이상 변동
    price_change_weekly: float = 0.10   # 주간 10% 이상 변동

    # 52주 신고/신저 근접 기준
    high_low_proximity: float = 0.05    # 52주 고/저점 5% 이내


# =============================================================================
# 앙상블 가중치
# =============================================================================

@dataclass
class DiscoveryConfig:
    """
    종목 자동 발굴 설정

    워치리스트에 없는 유망 종목을 섹터 유니버스 + 시장 상위종목에서
    자동으로 탐색하여 분석 대상에 추가합니다.

    동작 방식:
    1. 정기 분석 N회마다 1번 발굴 사이클 실행 (cycle_multiplier)
    2. SECTOR_UNIVERSE + 시장 거래량 상위 종목을 빠른 기술적 스크리닝
    3. 우선순위 높은 종목을 자동으로 분석 워치리스트에 추가
    4. 연속 HOLD 시 자동 제거 (rotation)
    """
    enabled: bool = True                    # 발굴 기능 활성화
    cycle_multiplier: int = 4               # 정기 분석 N회당 1번 발굴
    max_discovered_per_market: int = 10      # 시장당 최대 발굴 종목 수
    max_total_watchlist: int = 35            # 통합 워치리스트 최대 크기
    rotation_hold_limit: int = 2            # 연속 HOLD N회 시 자동 제거
    min_priority_score: float = 2.0         # MarketScanner 최소 우선순위 점수
    include_market_movers: bool = True      # 시장 거래량 상위 종목 포함
    market_movers_count: int = 15           # 시장별 상위 N개 종목 스캔
    data_cache_ttl: int = 7200              # 가격 데이터 캐시 TTL (2시간)


@dataclass
class DashboardConfig:
    """
    대시보드 운영 파라미터

    매직넘버(하드코딩된 숫자)를 한 곳에서 관리하여
    변경 시 코드 전체를 뒤질 필요가 없게 합니다.
    """
    # 스캐너 캐시 유효 시간 (초) — 이 시간 이내의 재요청은 캐시 반환
    scanner_cache_ttl: int = 300       # 5분

    # 스캐너 상위 결과 수 — 시장별 최대 N개씩, 합산 후 다시 상위 N개
    scanner_top_results: int = 15

    # 시장별 스캐너 최대 결과 수 (KR, US 각각)
    scanner_per_market: int = 10

    # 활동 로그 최대 보관 수 (오래된 것부터 제거)
    activity_log_max: int = 50

    # 신호 목록 최대 보관 수
    signals_log_max: int = 50

    # equity 스냅샷 저장 주기 (초)
    equity_snapshot_interval: int = 300  # 5분

    # 상태 브로드캐스트 주기 (초)
    broadcast_interval: int = 2

    # ★ 보유 포지션 현재가 갱신 주기 (초) — 실시간 PnL 표시용
    # yfinance/pykrx API 호출이 발생하므로 너무 짧으면 부하 증가
    # 60초 = 1분 간격 (장중에만 의미 있음)
    position_price_refresh_interval: int = 60

    # 성과 계산용 무위험 수익률 (연간) — 미국 단기 국채 금리 기준
    risk_free_rate: float = 0.035


@dataclass
class EnsembleConfig:
    """앙상블 전략 가중치 + 매매 임계값 (합계 = 1.0이어야 함)"""

    technical: float = 0.45     # 기술적 분석 (핵심 모듈 - RSI, MACD, 볼린저 등)
    factor: float = 0.35       # 팩터 투자 (모멘텀, 밸류, 퀄리티)
    time_series: float = 0.0   # ARIMA/GARCH (미구현 → 0으로 비활성)
    monte_carlo: float = 0.0   # 몬테카를로 (미구현 → 0으로 비활성)
    ml_prediction: float = 0.0 # XGBoost (미구현 → 0으로 비활성)
    sentiment: float = 0.20    # 뉴스 감성 (활성)

    # ── 모듈 ON/OFF 토글 (대시보드 설정 페이지에서 변경) ──
    # False로 설정하면 해당 모듈의 점수가 앙상블 계산에서 제외되고
    # 나머지 활성 모듈들의 가중치가 자동으로 정규화됩니다.
    technical_enabled: bool = True
    factor_enabled: bool = True
    sentiment_enabled: bool = True

    # ── 매매 임계값 ──
    # 종합 점수가 이 값을 넘어야 매수/매도 신호 발생
    # 모의매매: 0.2 (적당한 빈도), 실거래: 0.4+ 권장
    buy_threshold: float = 0.2
    sell_threshold: float = -0.2

    def validate(self) -> bool:
        """가중치 합계가 1.0인지 검증"""
        total = (self.technical + self.factor + self.time_series +
                 self.monte_carlo + self.ml_prediction + self.sentiment)
        return abs(total - 1.0) < 0.001

    def get_effective_weights(self) -> dict:
        """
        활성화된 모듈만 포함하여 자동 정규화된 가중치 반환

        예: technical 비활성 시 → factor 0.35, sentiment 0.20 만 남음
              → 정규화: factor 0.636, sentiment 0.364 (합계 1.0)

        Returns:
            {module_name: normalized_weight} 딕셔너리
        """
        weights = {}
        if self.technical_enabled and self.technical > 0:
            weights["technical"] = self.technical
        if self.factor_enabled and self.factor > 0:
            weights["factor"] = self.factor
        if self.sentiment_enabled and self.sentiment > 0:
            weights["sentiment"] = self.sentiment
        # 미구현 모듈 (가중치 0 비활성)
        if self.time_series > 0:
            weights["time_series"] = self.time_series
        if self.monte_carlo > 0:
            weights["monte_carlo"] = self.monte_carlo
        if self.ml_prediction > 0:
            weights["ml_prediction"] = self.ml_prediction

        # 정규화 (합계 = 1.0)
        total = sum(weights.values())
        if total > 0:
            return {k: v / total for k, v in weights.items()}
        return weights


@dataclass
class WatchlistConfig:
    """
    워치리스트 운용 정책

    strict_mode: 엄격 화이트리스트 모드
        - True: 사용자가 워치리스트에 명시한 종목만 매수 가능
          · 워치리스트가 비어도 기본값(US_WATCHLIST 등) fallback 안 함
          · 자동 발굴 종목 분석은 가능하지만 매수 차단
          · 보유 종목은 매도/홀드만 가능 (워치리스트 외라도 안전을 위해)
        - False: 기존 동작 (워치리스트 + 자동발굴 + 보유종목 모두 매매 대상)
    """
    strict_mode: bool = False


@dataclass
class PositionTypeConfig:
    """
    포지션 유형(단타/스윙/장기) ON/OFF 토글

    OFF로 설정하면 해당 유형으로 분류된 매수 신호가 무시됩니다.
    예: 단타 OFF → 기술적 신호가 강한 종목도 매수 안 함
        스윙 OFF → 혼합 신호 종목 매수 안 함
        장기 OFF → 펀더멘탈 우위 종목 매수 안 함

    주의: 모두 OFF로 하면 어떤 매수도 안 됩니다.
    """
    short_enabled: bool = True    # 단타 (1~3일, 기술적 신호 지배)
    swing_enabled: bool = True    # 스윙 (1~4주, 혼합 신호)
    long_enabled: bool = True     # 장기 (1개월+, 펀더멘탈 우위)


# =============================================================================
# 종합 설정 클래스
# =============================================================================

@dataclass
class Settings:
    """
    전체 설정을 하나로 묶는 최상위 클래스

    사용법:
        settings = Settings()                    # 기본값
        settings = Settings(
            capital=CapitalConfig(total_capital=50_000_000, currency="KRW"),
            risk=RiskConfig(risk_per_trade=0.01, max_drawdown=0.10)
        )

    CLI에서:
        python main.py AAPL --capital 50000000 --risk-per-trade 0.01
    """
    capital: CapitalConfig = field(default_factory=CapitalConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    data: DataConfig = field(default_factory=DataConfig)
    technical: TechnicalConfig = field(default_factory=TechnicalConfig)
    scanner: ScannerConfig = field(default_factory=ScannerConfig)
    discovery: DiscoveryConfig = field(default_factory=DiscoveryConfig)
    ensemble: EnsembleConfig = field(default_factory=EnsembleConfig)
    position_types: PositionTypeConfig = field(default_factory=PositionTypeConfig)
    watchlist: WatchlistConfig = field(default_factory=WatchlistConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)

    def summary(self) -> str:
        """현재 설정 요약 출력"""
        return (
            f"=== 퀀트봇 설정 요약 ===\n"
            f"자본금: {self.capital.total_capital:,.0f} {self.capital.currency}\n"
            f"1회 리스크: {self.risk.risk_per_trade*100:.1f}%\n"
            f"최대 포지션: {self.risk.max_position_size*100:.0f}%\n"
            f"최대 낙폭 한도: {self.risk.max_drawdown*100:.0f}%\n"
            f"손절 ATR배수: {self.risk.stop_loss_atr_multiplier}x\n"
            f"포지션 사이징: {self.risk.sizing_method}\n"
            f"분석 기간: {self.data.lookback_days}일\n"
        )


# =============================================================================
# 감시 종목 리스트 (기본값)
# =============================================================================

# 미국 주요 종목
US_WATCHLIST = [
    "AAPL",   # Apple
    "MSFT",   # Microsoft
    "GOOGL",  # Alphabet
    "AMZN",   # Amazon
    "NVDA",   # NVIDIA
    "META",   # Meta
    "TSLA",   # Tesla
    "SPY",    # S&P 500 ETF
    "QQQ",    # NASDAQ 100 ETF
]

# 한국 주요 종목 (종목코드.KS=코스피, .KQ=코스닥)
KR_WATCHLIST = [
    "005930.KS",  # 삼성전자
    "000660.KS",  # SK하이닉스
    "035720.KS",  # 카카오
    "035420.KS",  # NAVER
    "051910.KS",  # LG화학
    "006400.KS",  # 삼성SDI
    "373220.KS",  # LG에너지솔루션
    "068270.KS",  # 셀트리온
]


# =============================================================================
# 섹터별 종목 유니버스 (Market Scanner용)
# =============================================================================
# 사용자가 관심 분야를 선택하면 해당 섹터의 종목들을 스캔합니다.
# 각 섹터는 미국 + 한국 종목을 모두 포함합니다.

SECTOR_UNIVERSE = {
    # ─── 반도체 / AI ─────────────────────────────────────────────────────────
    "semiconductor_ai": {
        "name_ko": "반도체 / AI",
        "name_en": "Semiconductor / AI",
        "icon": "�chip",
        "stocks": [
            # 미국 반도체
            "NVDA",        # NVIDIA - GPU, AI 학습/추론
            "AMD",         # AMD - CPU/GPU, 데이터센터
            "INTC",        # Intel - CPU, 파운드리
            "AVGO",        # Broadcom - 네트워크 칩, AI 커스텀
            "QCOM",        # Qualcomm - 모바일 AP, AI 엣지
            "MU",          # Micron - DRAM/NAND 메모리
            "MRVL",        # Marvell - 데이터센터 반도체
            "ARM",         # ARM Holdings - 칩 설계 IP
            "TSM",         # TSMC - 파운드리 (대만)
            "ASML",        # ASML - EUV 장비 (네덜란드)
            # AI 소프트웨어/인프라
            "PLTR",        # Palantir - AI 분석 플랫폼
            "AI",          # C3.ai - 엔터프라이즈 AI
            "SMCI",        # Super Micro - AI 서버
            # 한국 반도체
            "005930.KS",   # 삼성전자 - 메모리, 파운드리
            "000660.KS",   # SK하이닉스 - HBM, DRAM
            "042700.KQ",   # 한미반도체 - 반도체 장비
            "403870.KQ",   # HPSP - 반도체 장비
        ]
    },

    # ─── 빅테크 / 플랫폼 ─────────────────────────────────────────────────────
    "bigtech_platform": {
        "name_ko": "빅테크 / 플랫폼",
        "name_en": "Big Tech / Platform",
        "icon": "💻",
        "stocks": [
            "AAPL",        # Apple - 하드웨어 + 서비스
            "MSFT",        # Microsoft - 클라우드, AI, OS
            "GOOGL",       # Alphabet - 검색, 클라우드, AI
            "AMZN",        # Amazon - 이커머스, AWS
            "META",        # Meta - SNS, 메타버스
            "NFLX",        # Netflix - 스트리밍
            "CRM",         # Salesforce - 엔터프라이즈 SaaS
            "ADBE",        # Adobe - 크리에이티브 SaaS
            "ORCL",        # Oracle - 클라우드 DB
            "SNOW",        # Snowflake - 데이터 클라우드
            # 한국 플랫폼
            "035420.KS",   # NAVER
            "035720.KS",   # 카카오
            "263750.KS",   # 펄어비스 (게임)
            "259960.KS",   # 크래프톤 (게임)
        ]
    },

    # ─── 에너지 / 2차전지 / 신재생 ───────────────────────────────────────────
    "energy_battery": {
        "name_ko": "에너지 / 2차전지",
        "name_en": "Energy / Battery",
        "icon": "🔋",
        "stocks": [
            "TSLA",        # Tesla - EV + 에너지 저장
            "ENPH",        # Enphase Energy - 태양광 인버터
            "FSLR",        # First Solar - 태양광 패널
            "NEE",         # NextEra Energy - 풍력/태양광 유틸리티
            "PLUG",        # Plug Power - 수소 연료전지
            "ALB",         # Albemarle - 리튬 채굴
            "RIVN",        # Rivian - EV 트럭
            "XOM",         # ExxonMobil - 전통 에너지 (비교용)
            "CVX",         # Chevron - 전통 에너지 (비교용)
            # 한국 2차전지/에너지
            "373220.KS",   # LG에너지솔루션
            "006400.KS",   # 삼성SDI
            "051910.KS",   # LG화학
            "247540.KS",   # 에코프로비엠
            "086520.KS",   # 에코프로
            "012450.KS",   # 한화에어로스페이스
        ]
    },

    # ─── 바이오 / 헬스케어 ────────────────────────────────────────────────────
    "bio_healthcare": {
        "name_ko": "바이오 / 헬스케어",
        "name_en": "Bio / Healthcare",
        "icon": "🧬",
        "stocks": [
            "LLY",         # Eli Lilly - 비만치료제, 당뇨
            "UNH",         # UnitedHealth - 건강보험
            "JNJ",         # Johnson & Johnson - 제약 + 의료기기
            "ABBV",        # AbbVie - 면역학, 종양학
            "MRK",         # Merck - 종양학 (키트루다)
            "PFE",         # Pfizer - 백신, 제약
            "MRNA",        # Moderna - mRNA 플랫폼
            "ISRG",        # Intuitive Surgical - 수술 로봇
            "TMO",         # Thermo Fisher - 생명과학 장비
            # 한국 바이오
            "068270.KS",   # 셀트리온 - 바이오시밀러
            "207940.KS",   # 삼성바이오로직스
            "326030.KS",   # SK바이오팜
            "145020.KS",   # 휴젤 - 보톡스
            "196170.KQ",   # 알테오젠 - 피하주사 플랫폼
        ]
    },

    # ─── 금융 / 핀테크 ───────────────────────────────────────────────────────
    "finance_fintech": {
        "name_ko": "금융 / 핀테크",
        "name_en": "Finance / Fintech",
        "icon": "🏦",
        "stocks": [
            "JPM",         # JPMorgan Chase - 투자은행
            "V",           # Visa - 결제 네트워크
            "MA",          # Mastercard - 결제 네트워크
            "GS",          # Goldman Sachs - 투자은행
            "BLK",         # BlackRock - 자산운용
            "COIN",        # Coinbase - 암호화폐 거래소
            "XYZ",         # Block Inc (구 Square) - 핀테크 (SQ→XYZ 티커 변경)
            "PYPL",        # PayPal - 온라인 결제
            "SOFI",        # SoFi - 디지털 금융
            # 한국 금융
            "105560.KS",   # KB금융
            "055550.KS",   # 신한지주
            "316140.KS",   # 우리금융지주
            "086790.KS",   # 하나금융지주
        ]
    },

    # ─── 소비재 / 리테일 ─────────────────────────────────────────────────────
    "consumer_retail": {
        "name_ko": "소비재 / 리테일",
        "name_en": "Consumer / Retail",
        "icon": "🛒",
        "stocks": [
            "WMT",         # Walmart - 대형 리테일
            "COST",        # Costco - 회원제 창고형
            "NKE",         # Nike - 스포츠 브랜드
            "SBUX",        # Starbucks - F&B
            "MCD",         # McDonald's - 글로벌 F&B
            "PG",          # Procter & Gamble - 생활용품
            "KO",          # Coca-Cola - 음료
            "DIS",         # Disney - 엔터테인먼트
            # 한국 소비재
            "051900.KS",   # LG생활건강
            "090430.KS",   # 아모레퍼시픽
            "034730.KS",   # SK네트웍스 (렌탈)
            "004170.KS",   # 신세계
        ]
    },

    # ─── ETF / 인덱스 (시장 전체 추적용) ─────────────────────────────────────
    "etf_index": {
        "name_ko": "ETF / 인덱스",
        "name_en": "ETF / Index",
        "icon": "📊",
        "stocks": [
            "SPY",         # S&P 500
            "QQQ",         # Nasdaq-100
            "IWM",         # Russell 2000 (소형주)
            "DIA",         # Dow Jones
            "ARKK",        # ARK Innovation (혁신주)
            "SOXX",        # 반도체 ETF
            "XLF",         # 금융 ETF
            "XLE",         # 에너지 ETF
            "XLV",         # 헬스케어 ETF
            "VOO",         # Vanguard S&P 500
            "VTI",         # 미국 전체 시장
            "TQQQ",        # Nasdaq 3x 레버리지
        ]
    },
}

# 사용 가능한 모든 섹터 키 목록
AVAILABLE_SECTORS = list(SECTOR_UNIVERSE.keys())

# FRED 거시경제 시계열 (미국 경제 핵심 지표)
FRED_SERIES = {
    "GDP": "GDP",           # 미국 GDP (분기별)
    "CPI": "CPIAUCSL",      # 소비자물가지수 (인플레이션)
    "FEDFUNDS": "FEDFUNDS", # 연방기금금리 (기준금리)
    "T10Y2Y": "T10Y2Y",     # 장단기 금리차 (< 0 이면 경기침체 경고!)
    "VIX": "VIXCLS",        # 공포지수 (> 30 이면 극도의 공포)
    "DGS10": "DGS10",       # 10년 국채 수익률
    "UNRATE": "UNRATE",     # 실업률
}
