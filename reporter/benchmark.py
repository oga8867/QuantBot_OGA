"""
=============================================================================
reporter/benchmark.py - Buy-and-Hold 벤치마크 비교 모듈
=============================================================================

봇의 성과를 단순 매수 후 보유(buy-and-hold) 전략과 비교하는 모듈입니다.

핵심 질문: "이 봇이 정말로 그냥 SPY/QQQ 사놓는 것보다 나은가?"

학술적 배경:
- 효율적 시장 가설(EMH): 적극적 운용은 평균적으로 시장을 이기지 못함
- S&P 500 인덱스를 5년 이상 이기는 액티브 펀드는 25% 미만 (SPIVA 2024)
- 따라서 알고리즘 매매는 "벤치마크 대비 알파"로 평가해야 함

지표:
- 알파 (Alpha): 봇 수익률 - 벤치마크 수익률
- 베타 (Beta): 시장 변동에 대한 봇의 민감도
- 정보비율 (Information Ratio): 알파 / Tracking Error
  - 0.5 이상 = 우수
  - 1.0 이상 = 매우 우수 (대부분의 헤지펀드도 못 미침)

벤치마크 종목:
- 한국 시장: KODEX 200 (069500.KS) - KOSPI 200 ETF
- 미국 시장: SPY - S&P 500 ETF
- 자동 선택: 거래 비중에 따라 가중평균 사용
=============================================================================
"""

import logging
from dataclasses import dataclass
from typing import Optional, Dict, List
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


@dataclass
class BenchmarkResult:
    """벤치마크 비교 결과"""
    bot_return_pct: float = 0.0           # 봇 누적 수익률 (%)
    benchmark_return_pct: float = 0.0     # 벤치마크 수익률 (%)
    alpha_pct: float = 0.0                # 초과 수익률 (%, 봇 - 벤치마크)
    bot_sharpe: float = 0.0
    benchmark_sharpe: float = 0.0
    information_ratio: float = 0.0        # 알파 / Tracking Error
    correlation: float = 0.0              # 봇 vs 벤치마크 상관계수 (-1~1)
    bot_max_dd: float = 0.0               # 봇 최대 낙폭 (%)
    benchmark_max_dd: float = 0.0         # 벤치마크 최대 낙폭 (%)
    days: int = 0                         # 비교 기간 (일)
    benchmark_symbol: str = ""            # 사용된 벤치마크 종목
    benchmark_name: str = ""              # 사용자에게 표시할 이름
    verdict: str = ""                     # "outperform" / "underperform" / "tie" / "insufficient"
    summary_kr: str = ""                  # 한국어 요약


class BenchmarkComparator:
    """
    봇 성과를 buy-and-hold와 비교

    사용법:
        comp = BenchmarkComparator()
        result = comp.compare(
            equity_history=[{"timestamp": ..., "total_equity": ...}, ...],
            initial_capital=10_000_000,
            market="auto",  # "KR", "US", "auto"
        )
        print(result.summary_kr)
    """

    BENCHMARKS = {
        "KR": ("069500.KS", "KODEX 200"),  # KOSPI 200 ETF
        "US": ("SPY", "S&P 500"),
        "MIX": ("ACWI", "MSCI All-World"),  # 글로벌 분산
    }

    def compare(
        self,
        equity_history: List[Dict],
        initial_capital: float,
        market: str = "auto",
        positions_summary: Optional[Dict] = None,
    ) -> BenchmarkResult:
        """
        봇 equity 곡선을 벤치마크와 비교

        Parameters:
            equity_history: [{"timestamp": ISO, "total_equity": KRW}, ...]
            initial_capital: 초기 자본 (KRW)
            market: 벤치마크 선택 ("KR", "US", "MIX", "auto")
            positions_summary: {"KR": pct, "US": pct} - 자동 선택용

        Returns:
            BenchmarkResult: 비교 결과 + 요약
        """
        result = BenchmarkResult(verdict="insufficient")

        # ── 데이터 검증 ──
        if not equity_history or len(equity_history) < 2:
            result.summary_kr = "비교 데이터 부족 (equity 스냅샷 2개 미만)"
            return result

        # ── 벤치마크 선택 ──
        if market == "auto":
            if positions_summary:
                kr_pct = positions_summary.get("KR", 0)
                us_pct = positions_summary.get("US", 0)
                if kr_pct > us_pct * 1.5:
                    market = "KR"
                elif us_pct > kr_pct * 1.5:
                    market = "US"
                else:
                    market = "MIX"
            else:
                market = "MIX"

        symbol, name = self.BENCHMARKS.get(market, self.BENCHMARKS["MIX"])
        result.benchmark_symbol = symbol
        result.benchmark_name = name

        # ── 기간 계산 ──
        try:
            start_str = equity_history[0].get("timestamp", "")
            end_str = equity_history[-1].get("timestamp", "")
            start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
            result.days = max(1, (end_dt - start_dt).days)
        except Exception:
            result.days = max(1, len(equity_history))
            start_dt = datetime.now() - timedelta(days=result.days)
            end_dt = datetime.now()

        # ── 봇 수익률 / 샤프 / MDD 계산 ──
        equities = [float(e.get("total_equity", initial_capital)) for e in equity_history]
        bot_returns = self._daily_returns(equities)

        if equities[0] > 0:
            result.bot_return_pct = (equities[-1] / equities[0] - 1) * 100
        result.bot_sharpe = self._sharpe(bot_returns)
        result.bot_max_dd = self._max_drawdown(equities) * 100

        # ── 벤치마크 가격 가져오기 ──
        try:
            import yfinance as yf
            # +5일 버퍼 (휴일 대응)
            buffer_start = start_dt - timedelta(days=5)
            buffer_end = end_dt + timedelta(days=1)
            data = yf.download(
                symbol,
                start=buffer_start.strftime("%Y-%m-%d"),
                end=buffer_end.strftime("%Y-%m-%d"),
                progress=False,
            )
            if data.empty:
                result.summary_kr = f"벤치마크({name}) 데이터 없음 (네트워크 또는 종목코드 문제)"
                return result

            # MultiIndex Close 컬럼 처리
            if "Close" in data.columns:
                bench_prices = data["Close"]
            else:
                bench_prices = data.iloc[:, 0]

            # numpy 배열로 변환
            if hasattr(bench_prices, "values"):
                bench_prices = bench_prices.values
            bench_prices = [float(p) for p in bench_prices if p == p]  # NaN 제외

            if len(bench_prices) < 2:
                result.summary_kr = f"{name} 가격 데이터 부족"
                return result

            # ── 벤치마크 수익률 ──
            result.benchmark_return_pct = (
                bench_prices[-1] / bench_prices[0] - 1
            ) * 100
            bench_returns = self._daily_returns(bench_prices)
            result.benchmark_sharpe = self._sharpe(bench_returns)
            result.benchmark_max_dd = self._max_drawdown(bench_prices) * 100

            # ── 알파 / 정보비율 ──
            result.alpha_pct = result.bot_return_pct - result.benchmark_return_pct

            # 일별 수익률 길이 맞추기 (봇과 벤치마크가 다름)
            min_len = min(len(bot_returns), len(bench_returns))
            if min_len >= 2:
                bot_r = bot_returns[-min_len:]
                bench_r = bench_returns[-min_len:]
                excess = [b - m for b, m in zip(bot_r, bench_r)]
                avg_excess = sum(excess) / len(excess)
                # Tracking Error = 초과 수익률의 표준편차
                if len(excess) > 1:
                    mean_e = avg_excess
                    var_e = sum((x - mean_e) ** 2 for x in excess) / (len(excess) - 1)
                    tracking_error = var_e ** 0.5
                    if tracking_error > 1e-9:
                        # 연환산 (252 거래일)
                        result.information_ratio = (
                            avg_excess / tracking_error * (252 ** 0.5)
                        )

                # 상관계수
                result.correlation = self._correlation(bot_r, bench_r)

        except ImportError:
            result.summary_kr = "yfinance 미설치 - 벤치마크 비교 불가"
            return result
        except Exception as e:
            logger.warning(f"[벤치마크] 비교 실패: {e}")
            result.summary_kr = f"벤치마크 조회 오류: {e}"
            return result

        # ── 판정 ──
        if abs(result.alpha_pct) < 0.5:
            result.verdict = "tie"
            verdict_kr = "동률"
        elif result.alpha_pct > 0:
            result.verdict = "outperform"
            verdict_kr = "✅ 벤치마크 초과"
        else:
            result.verdict = "underperform"
            verdict_kr = "⚠️ 벤치마크 미달"

        # ── 한국어 요약 ──
        result.summary_kr = (
            f"{verdict_kr} ({result.days}일 기준)\n"
            f"  봇 수익률   : {result.bot_return_pct:+.2f}% "
            f"(샤프 {result.bot_sharpe:.2f}, MDD {result.bot_max_dd:.2f}%)\n"
            f"  {name:>10}: {result.benchmark_return_pct:+.2f}% "
            f"(샤프 {result.benchmark_sharpe:.2f}, MDD {result.benchmark_max_dd:.2f}%)\n"
            f"  알파       : {result.alpha_pct:+.2f}%\n"
            f"  정보비율   : {result.information_ratio:.2f} "
            f"(0.5+ 우수, 1.0+ 매우 우수)\n"
            f"  상관계수   : {result.correlation:+.2f}"
        )

        return result

    @staticmethod
    def _daily_returns(prices: List[float]) -> List[float]:
        """일별 수익률 계산"""
        rets = []
        for i in range(1, len(prices)):
            if prices[i - 1] > 0:
                rets.append(prices[i] / prices[i - 1] - 1)
        return rets

    @staticmethod
    def _sharpe(returns: List[float], risk_free: float = 0.035 / 252) -> float:
        """일별 수익률 → 연환산 Sharpe (무위험수익률 3.5%/연 가정)"""
        if len(returns) < 2:
            return 0.0
        mean_r = sum(returns) / len(returns)
        var = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
        std = var ** 0.5
        if std < 1e-9:
            return 0.0
        return (mean_r - risk_free) / std * (252 ** 0.5)

    @staticmethod
    def _max_drawdown(prices: List[float]) -> float:
        """최대 낙폭 (0~1)"""
        if len(prices) < 2:
            return 0.0
        peak = prices[0]
        max_dd = 0.0
        for p in prices:
            if p > peak:
                peak = p
            dd = (peak - p) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)
        return max_dd

    @staticmethod
    def _correlation(x: List[float], y: List[float]) -> float:
        """피어슨 상관계수"""
        if len(x) < 2 or len(x) != len(y):
            return 0.0
        n = len(x)
        mean_x = sum(x) / n
        mean_y = sum(y) / n
        num = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n))
        var_x = sum((xi - mean_x) ** 2 for xi in x)
        var_y = sum((yi - mean_y) ** 2 for yi in y)
        denom = (var_x * var_y) ** 0.5
        if denom < 1e-9:
            return 0.0
        return num / denom
