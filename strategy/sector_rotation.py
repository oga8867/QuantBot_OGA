"""
=============================================================================
strategy/sector_rotation.py - 섹터 로테이션 전략
=============================================================================

경기 사이클에 따라 강세 섹터를 선정하고 종목을 발굴하는 알파 모듈입니다.

이론적 배경:
- Sector Rotation Theory (Stovall 1996)
- 경기 사이클은 4국면: 회복(Recovery) → 호황(Peak) → 후퇴(Recession) → 회복
- 각 국면에 강세 섹터가 다름

국면별 우세 섹터:
┌──────────────┬──────────────────────────────────────────────┐
│ 국면          │ 우세 섹터                                     │
├──────────────┼──────────────────────────────────────────────┤
│ 회복기 초기   │ 경기소비재(XLY), 금융(XLF), 부동산(XLRE)      │
│ 호황          │ 기술주(XLK), 산업재(XLI), 임의소비재          │
│ 정점          │ 에너지(XLE), 원자재(XLB)                     │
│ 후퇴 초기     │ 헬스케어(XLV), 필수소비재(XLP), 유틸리티(XLU) │
│ 후퇴 후기     │ 채권, 현금                                    │
└──────────────┴──────────────────────────────────────────────┘

상대 강도(Relative Strength) 기반 자동 선정:
- 각 섹터 ETF의 60일 모멘텀 계산
- SPY 대비 초과 수익 = 상대 강도
- 상대 강도 상위 N개 섹터 선택

학술 자료:
- "The Best of Both Worlds: A Pragmatic Approach to Tactical Asset Allocation"
  (Faber 2007) — 섹터 로테이션 + 모멘텀 결합
- Two Sigma "Sector Rotation Using Dynamic Factor Models" (2022)

활용:
- 종목 발굴 시 우세 섹터에 가중치 부여
- 약세 섹터 종목은 매수 대상에서 제외
=============================================================================
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


# 미국 섹터 ETF (SPDR Sector ETFs)
US_SECTOR_ETFS = {
    "XLK": "Technology",         # 기술주
    "XLF": "Financials",         # 금융
    "XLV": "Healthcare",         # 헬스케어
    "XLY": "Consumer Discretionary",  # 임의소비재
    "XLP": "Consumer Staples",   # 필수소비재
    "XLI": "Industrials",        # 산업재
    "XLE": "Energy",             # 에너지
    "XLB": "Materials",          # 원자재
    "XLU": "Utilities",          # 유틸리티
    "XLRE": "Real Estate",       # 부동산
    "XLC": "Communications",     # 통신
}

# 한국 섹터 ETF (KODEX 섹터 시리즈)
KR_SECTOR_ETFS = {
    "091160.KS": "KODEX 반도체",
    "091170.KS": "KODEX 은행",
    "139220.KS": "KODEX 건설",
    "228810.KS": "KODEX 미디어&엔터",
    "117460.KS": "KODEX 에너지화학",
    "266390.KS": "KODEX 헬스케어",
    "117680.KS": "KODEX 철강",
    "098560.KS": "TIGER 방송통신",
    "266370.KS": "KODEX 자동차",
}


@dataclass
class SectorScore:
    """섹터별 상대 강도 점수"""
    symbol: str
    name: str
    momentum_60d: float           # 60일 수익률 (%)
    relative_strength: float      # 벤치마크 대비 초과 수익 (%)
    rs_rank: int = 0              # 상대 강도 순위 (1=최강)
    is_leading: bool = False      # 상위 N개에 속하는지


@dataclass
class SectorRotationResult:
    """섹터 로테이션 분석 결과"""
    leading_sectors: List[SectorScore] = field(default_factory=list)
    lagging_sectors: List[SectorScore] = field(default_factory=list)
    benchmark_return_pct: float = 0.0
    analyzed_at: datetime = field(default_factory=datetime.now)
    market: str = "US"             # "US" or "KR"


class SectorRotationAnalyzer:
    """
    섹터 로테이션 분석 엔진

    사용법:
        analyzer = SectorRotationAnalyzer()
        result = analyzer.analyze(market="US", top_n=3)
        print(f"강세 섹터: {[s.name for s in result.leading_sectors]}")

        # 종목 가중치 부여
        boost = analyzer.get_sector_boost("AAPL", result)  # 기술주 → +0.1
    """

    def __init__(self, lookback_days: int = 60, top_n: int = 3):
        """
        Parameters:
            lookback_days: 모멘텀 계산 기간 (60일 = 약 3개월)
            top_n: 강세 섹터 개수 (기본 3개)
        """
        self.lookback_days = lookback_days
        self.top_n = top_n

        # 종목-섹터 매핑 캐시 (yfinance.info 호출 비용 절감)
        self._symbol_sector_cache: Dict[str, str] = {}

    def analyze(self, market: str = "US") -> SectorRotationResult:
        """
        시장 전체 섹터 모멘텀 분석

        Parameters:
            market: "US" 또는 "KR"

        Returns:
            SectorRotationResult: 강세/약세 섹터 + 점수
        """
        try:
            import yfinance as yf
        except ImportError:
            logger.error("[섹터로테이션] yfinance 미설치")
            return SectorRotationResult(market=market)

        # 시장별 섹터 ETF + 벤치마크
        if market == "US":
            sectors = US_SECTOR_ETFS
            benchmark_symbol = "SPY"
        elif market == "KR":
            sectors = KR_SECTOR_ETFS
            benchmark_symbol = "069500.KS"  # KODEX 200
        else:
            return SectorRotationResult(market=market)

        # ── 벤치마크 모멘텀 ──
        try:
            bench_data = yf.download(
                benchmark_symbol,
                period=f"{self.lookback_days + 10}d",
                progress=False,
            )
            if bench_data.empty:
                return SectorRotationResult(market=market)
            bench_close = bench_data["Close"].dropna()
            if len(bench_close) < self.lookback_days:
                return SectorRotationResult(market=market)
            bench_return = (
                float(bench_close.iloc[-1]) / float(bench_close.iloc[-self.lookback_days]) - 1
            ) * 100
        except Exception as e:
            logger.debug(f"[섹터로테이션] 벤치마크 조회 실패: {e}")
            return SectorRotationResult(market=market)

        # ── 각 섹터 ETF 모멘텀 계산 ──
        scores: List[SectorScore] = []
        for sym, name in sectors.items():
            try:
                data = yf.download(
                    sym,
                    period=f"{self.lookback_days + 10}d",
                    progress=False,
                )
                if data.empty:
                    continue
                close = data["Close"].dropna()
                if len(close) < self.lookback_days:
                    continue
                momentum = (
                    float(close.iloc[-1]) / float(close.iloc[-self.lookback_days]) - 1
                ) * 100
                rs = momentum - bench_return
                scores.append(SectorScore(
                    symbol=sym,
                    name=name,
                    momentum_60d=momentum,
                    relative_strength=rs,
                ))
            except Exception:
                continue

        # ── 상대 강도 순위 ──
        scores.sort(key=lambda s: s.relative_strength, reverse=True)
        for rank, s in enumerate(scores, start=1):
            s.rs_rank = rank
            s.is_leading = rank <= self.top_n

        leading = [s for s in scores if s.is_leading]
        lagging = scores[-3:] if len(scores) >= 3 else []

        return SectorRotationResult(
            leading_sectors=leading,
            lagging_sectors=lagging,
            benchmark_return_pct=bench_return,
            market=market,
        )

    def get_sector_for_symbol(self, symbol: str, market: str = "US") -> Optional[str]:
        """
        종목의 섹터를 yfinance.info에서 조회 (캐시됨)

        Returns:
            섹터명 ("Technology", "Financials" 등) 또는 None
        """
        if symbol in self._symbol_sector_cache:
            return self._symbol_sector_cache[symbol]
        try:
            import yfinance as yf
            ticker = yf.Ticker(symbol)
            info = ticker.info or {}
            sector = info.get("sector", "")
            self._symbol_sector_cache[symbol] = sector
            return sector
        except Exception:
            self._symbol_sector_cache[symbol] = ""
            return None

    def get_sector_boost(
        self, symbol: str, result: SectorRotationResult, boost: float = 0.10
    ) -> float:
        """
        종목이 강세 섹터에 속하면 +boost, 약세 섹터에 속하면 -boost 점수

        활용: ensemble.combine() 결과에 더해서 최종 신호 강화/약화

        Parameters:
            symbol: 종목코드
            result: analyze() 반환값
            boost: 가/감점 폭 (기본 ±0.10)

        Returns:
            점수 보정 (-boost ~ +boost)
        """
        sector = self.get_sector_for_symbol(symbol, market=result.market)
        if not sector:
            return 0.0

        # 강세 섹터에 속하면 +
        for s in result.leading_sectors:
            if sector.lower() in s.name.lower() or s.name.lower() in sector.lower():
                return boost
        # 약세 섹터에 속하면 -
        for s in result.lagging_sectors:
            if sector.lower() in s.name.lower() or s.name.lower() in sector.lower():
                return -boost
        return 0.0

    def get_summary_kr(self, result: SectorRotationResult) -> str:
        """한국어 요약 텍스트"""
        if not result.leading_sectors:
            return "섹터 로테이션 데이터 없음"

        lines = [
            f"📊 섹터 로테이션 분석 ({result.market})",
            f"벤치마크 60일 수익: {result.benchmark_return_pct:+.2f}%",
            "",
            "🚀 강세 섹터:",
        ]
        for s in result.leading_sectors:
            lines.append(
                f"  {s.rs_rank}. {s.name:<25} | "
                f"60일 {s.momentum_60d:+.2f}% (벤치 대비 {s.relative_strength:+.2f}%)"
            )
        if result.lagging_sectors:
            lines.append("")
            lines.append("📉 약세 섹터:")
            for s in result.lagging_sectors:
                lines.append(
                    f"  #{s.rs_rank}. {s.name:<25} | "
                    f"60일 {s.momentum_60d:+.2f}% (벤치 대비 {s.relative_strength:+.2f}%)"
                )
        return "\n".join(lines)
