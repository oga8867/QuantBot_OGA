"""
=============================================================================
reporter/weekly_report.py - 주간/월간 보고서 생성기
=============================================================================

일일 보고서의 확장판으로, 주간(7일) 또는 월간(30일) 기간의
성과를 종합적으로 분석합니다.

주간 보고서에 포함되는 항목:
1. 기간 수익률 요약 (총 수익, 일 평균, 최고/최저일)
2. 거래 통계 (승률, 손익비, 가장 수익/손실 큰 거래)
3. 종목별 성과 히트맵
4. 전략별 성과 비교
5. 리스크 지표 (MDD, 샤프비, 칼마비)
6. 다음 주 전망/주의사항

보고서 생성 주기:
- 주간: 매주 일요일 or 월요일 아침
- 월간: 매월 1일
- 수동: 대시보드에서 버튼 클릭

아키텍처:
    DB (equity_history, trades, signals)
        ↓
    WeeklyReportGenerator
        ↓
    HTML 보고서 → reports/ 폴더에 저장
=============================================================================
"""

import os
import json
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta

logger = logging.getLogger("WeeklyReport")


class WeeklyReportGenerator:
    """
    주간/월간 보고서 생성기

    SQLite DB에서 기간별 데이터를 가져와 종합 보고서를 만듭니다.

    속성:
        db: DatabaseManager 인스턴스
        report_dir: 보고서 저장 디렉토리
    """

    def __init__(self, db=None, report_dir: str = "reports"):
        """
        Args:
            db: DatabaseManager 인스턴스 (None이면 내부 생성)
            report_dir: 보고서 HTML 파일 저장 경로
        """
        self.db = db
        self.report_dir = report_dir
        os.makedirs(report_dir, exist_ok=True)

    def generate(self, period: str = "weekly",
                 capital: float = 100000) -> Optional[str]:
        """
        보고서 생성 (메인 진입점)

        Args:
            period: "weekly" (7일) 또는 "monthly" (30일)
            capital: 초기 자본금 (수익률 계산용)

        Returns:
            생성된 HTML 파일 경로, 실패 시 None
        """
        days = 7 if period == "weekly" else 30
        label = "주간" if period == "weekly" else "월간"

        try:
            # DB에서 데이터 수집
            if not self.db:
                from database.cache import DatabaseManager
                self.db = DatabaseManager()
                self.db.initialize()

            # ── 1. Equity 데이터 ──
            equity_data = self.db.get_equity_history(days=days)

            # ── 2. 거래 데이터 ──
            trades = self.db.get_trades(limit=10000)
            # 기간 내 거래만 필터
            since = (datetime.now() - timedelta(days=days)).isoformat()
            period_trades = [t for t in trades if t.get("timestamp", "") >= since]

            # ── 3. 신호 데이터 ──
            signals = self.db.get_signals(limit=1000)
            period_signals = [s for s in signals if s.get("timestamp", "") >= since]

            # ── 4. 성과 계산 ──
            perf = self._calc_performance(equity_data, period_trades, capital)

            # ── 5. 종목별 통계 ──
            symbol_stats = self._calc_symbol_stats(period_trades)

            # ── 6. HTML 생성 ──
            html = self._build_html(
                label=label, days=days, perf=perf,
                trades=period_trades, signals=period_signals,
                symbol_stats=symbol_stats, equity_data=equity_data
            )

            # ── 7. 파일 저장 ──
            date_str = datetime.now().strftime("%Y%m%d")
            filename = f"{period}_report_{date_str}.html"
            filepath = os.path.join(self.report_dir, filename)

            with open(filepath, "w", encoding="utf-8") as f:
                f.write(html)

            logger.info(f"[{label} 보고서] 생성 완료: {filepath}")
            return filepath

        except Exception as e:
            logger.error(f"[{label} 보고서] 생성 실패: {e}")
            return None

    def _calc_performance(self, equity_data: List[Dict],
                          trades: List[Dict],
                          capital: float) -> Dict[str, Any]:
        """
        기간 성과 지표 계산

        주요 지표:
        - total_return: 기간 총 수익률 (%)
        - daily_avg_return: 일 평균 수익률
        - best_day / worst_day: 최고/최저 수익일
        - win_rate: 승률 (매도 거래 기준)
        - profit_factor: 손익비
        - mdd: 최대 낙폭
        - sharpe: 샤프 비율
        """
        perf = {
            "total_return": 0, "daily_avg_return": 0,
            "best_day": 0, "worst_day": 0,
            "win_rate": 0, "profit_factor": 0,
            "mdd": 0, "sharpe": 0,
            "total_trades": len(trades),
            "start_equity": capital, "end_equity": capital,
        }

        if equity_data and len(equity_data) >= 2:
            equities = [e.get("total_equity", 0) for e in equity_data]
            perf["start_equity"] = equities[0]
            perf["end_equity"] = equities[-1]

            if equities[0] > 0:
                perf["total_return"] = (equities[-1] / equities[0] - 1) * 100

            # 일별 수익률
            daily_returns = []
            for i in range(1, len(equities)):
                if equities[i-1] > 0:
                    daily_returns.append((equities[i] / equities[i-1] - 1) * 100)

            if daily_returns:
                perf["daily_avg_return"] = sum(daily_returns) / len(daily_returns)
                perf["best_day"] = max(daily_returns)
                perf["worst_day"] = min(daily_returns)

                # 샤프비 (연율화)
                import statistics
                if len(daily_returns) > 1:
                    mean_r = statistics.mean(daily_returns) / 100
                    std_r = statistics.stdev(daily_returns) / 100
                    rf = 0.035 / 252
                    if std_r > 0:
                        perf["sharpe"] = (mean_r - rf) / std_r * (252 ** 0.5)

            # MDD
            peak = equities[0]
            max_dd = 0
            for eq in equities:
                if eq > peak:
                    peak = eq
                dd = (peak - eq) / peak if peak > 0 else 0
                max_dd = max(max_dd, dd)
            perf["mdd"] = max_dd * 100

        # 거래 통계 (매도 기준)
        sells = [t for t in trades if t.get("side", "").upper() == "SELL"]
        wins = [t for t in sells if t.get("pnl", 0) and t["pnl"] > 0]
        losses = [t for t in sells if t.get("pnl", 0) and t["pnl"] < 0]

        if sells:
            perf["win_rate"] = len(wins) / len(sells) * 100
            total_win = sum(t.get("pnl", 0) for t in wins)
            total_loss = sum(abs(t.get("pnl", 0)) for t in losses)
            if total_loss > 0:
                perf["profit_factor"] = total_win / total_loss

        return perf

    def _calc_symbol_stats(self, trades: List[Dict]) -> Dict[str, Dict]:
        """
        종목별 거래 통계

        각 종목의 거래 횟수, 수익률, 승률을 계산합니다.
        히트맵 렌더링에 사용됩니다.
        """
        stats = {}
        for t in trades:
            sym = t.get("symbol", "?")
            if sym not in stats:
                stats[sym] = {"trades": 0, "buys": 0, "sells": 0,
                              "pnl": 0, "volume": 0}
            stats[sym]["trades"] += 1
            stats[sym]["volume"] += t.get("total_value", 0)
            if t.get("side", "").upper() == "BUY":
                stats[sym]["buys"] += 1
            else:
                stats[sym]["sells"] += 1
                stats[sym]["pnl"] += t.get("pnl", 0) or 0

        return stats

    def _build_html(self, label: str, days: int, perf: Dict,
                    trades: List, signals: List,
                    symbol_stats: Dict, equity_data: List) -> str:
        """
        보고서 HTML 생성

        PlayStation 다크 테마 기반의 보고서를 만듭니다.
        """
        now = datetime.now()
        start_date = (now - timedelta(days=days)).strftime("%Y.%m.%d")
        end_date = now.strftime("%Y.%m.%d")

        # 수익률 색상
        ret_color = "#009900" if perf["total_return"] >= 0 else "#d53b00"
        wr_color = "#009900" if perf["win_rate"] >= 50 else "#d53b00"

        # 종목별 통계 HTML
        sym_rows = ""
        for sym, st in sorted(symbol_stats.items(),
                                key=lambda x: abs(x[1]["pnl"]), reverse=True)[:15]:
            pnl_color = "#009900" if st["pnl"] >= 0 else "#d53b00"
            sym_rows += f"""<tr>
                <td style="font-weight:600;">{sym}</td>
                <td>{st['trades']}</td>
                <td>{st['buys']}</td>
                <td>{st['sells']}</td>
                <td style="color:{pnl_color};font-weight:600;">
                    ${st['pnl']:,.0f}</td>
                <td>${st['volume']:,.0f}</td>
            </tr>"""

        # 최근 거래 HTML
        trade_rows = ""
        for t in trades[:20]:
            side_color = "#009900" if t.get("side", "").upper() == "BUY" else "#d53b00"
            trade_rows += f"""<tr>
                <td>{t.get('timestamp', '')[:16]}</td>
                <td>{t.get('symbol', '?')}</td>
                <td style="color:{side_color};font-weight:600;">
                    {t.get('side', '?')}</td>
                <td>{t.get('quantity', 0)}</td>
                <td>${t.get('price', 0):,.2f}</td>
                <td>${t.get('total_value', 0):,.0f}</td>
            </tr>"""

        html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{label} 보고서 ({start_date} ~ {end_date})</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            background: #000; color: #fff; font-family: Inter, -apple-system, sans-serif;
            max-width: 800px; margin: 0 auto; padding: 24px; line-height: 1.6;
        }}
        .header {{
            text-align: center; padding: 32px 0;
            border-bottom: 1px solid rgba(255,255,255,0.1);
        }}
        .header h1 {{ font-weight: 300; font-size: 32px; }}
        .header .period {{
            color: rgba(255,255,255,0.5); font-size: 14px; margin-top: 8px;
        }}
        .section {{ margin: 24px 0; }}
        .section-title {{
            font-size: 18px; font-weight: 600; margin-bottom: 16px;
            padding-left: 12px; border-left: 3px solid #0070d1;
        }}
        .grid {{ display: grid; gap: 12px; }}
        .grid-4 {{ grid-template-columns: repeat(4, 1fr); }}
        .grid-3 {{ grid-template-columns: repeat(3, 1fr); }}
        .card {{
            background: rgba(255,255,255,0.04); border-radius: 8px; padding: 20px;
            text-align: center;
        }}
        .card-label {{ font-size: 12px; color: rgba(255,255,255,0.5); margin-bottom: 6px; }}
        .card-value {{ font-size: 24px; font-weight: 300; }}
        table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
        th {{ text-align: left; padding: 10px 8px; color: rgba(255,255,255,0.5);
              border-bottom: 1px solid rgba(255,255,255,0.1); font-weight: 500; }}
        td {{ padding: 10px 8px; border-bottom: 1px solid rgba(255,255,255,0.05); }}
        .footer {{ text-align: center; padding: 24px; color: rgba(255,255,255,0.3);
                   font-size: 11px; margin-top: 32px;
                   border-top: 1px solid rgba(255,255,255,0.05); }}
        @media (max-width: 600px) {{
            .grid-4 {{ grid-template-columns: repeat(2, 1fr); }}
            .grid-3 {{ grid-template-columns: repeat(1, 1fr); }}
            .card-value {{ font-size: 20px; }}
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>📊 {label} 보고서</h1>
        <div class="period">{start_date} ~ {end_date} ({days}일)</div>
    </div>

    <div class="section">
        <div class="section-title">성과 요약</div>
        <div class="grid grid-4">
            <div class="card">
                <div class="card-label">기간 수익률</div>
                <div class="card-value" style="color:{ret_color}">
                    {perf['total_return']:+.2f}%</div>
            </div>
            <div class="card">
                <div class="card-label">승률</div>
                <div class="card-value" style="color:{wr_color}">
                    {perf['win_rate']:.1f}%</div>
            </div>
            <div class="card">
                <div class="card-label">최대 낙폭 (MDD)</div>
                <div class="card-value" style="color:#d53b00">
                    -{perf['mdd']:.2f}%</div>
            </div>
            <div class="card">
                <div class="card-label">샤프 비율</div>
                <div class="card-value">{perf['sharpe']:.2f}</div>
            </div>
        </div>
    </div>

    <div class="section">
        <div class="section-title">세부 지표</div>
        <div class="grid grid-3">
            <div class="card">
                <div class="card-label">총 거래</div>
                <div class="card-value">{perf['total_trades']}</div>
            </div>
            <div class="card">
                <div class="card-label">손익비</div>
                <div class="card-value">{perf['profit_factor']:.2f}</div>
            </div>
            <div class="card">
                <div class="card-label">일평균 수익률</div>
                <div class="card-value">{perf['daily_avg_return']:+.3f}%</div>
            </div>
            <div class="card">
                <div class="card-label">최고 수익일</div>
                <div class="card-value" style="color:#009900">
                    {perf['best_day']:+.2f}%</div>
            </div>
            <div class="card">
                <div class="card-label">최대 손실일</div>
                <div class="card-value" style="color:#d53b00">
                    {perf['worst_day']:+.2f}%</div>
            </div>
            <div class="card">
                <div class="card-label">신호 발생</div>
                <div class="card-value">{len(signals)}</div>
            </div>
        </div>
    </div>

    <div class="section">
        <div class="section-title">종목별 성과</div>
        <div style="overflow-x:auto;">
            <table>
                <tr>
                    <th>종목</th><th>거래</th><th>매수</th>
                    <th>매도</th><th>손익</th><th>거래액</th>
                </tr>
                {sym_rows if sym_rows else '<tr><td colspan="6" style="text-align:center;color:rgba(255,255,255,0.3);padding:20px;">기간 내 거래 없음</td></tr>'}
            </table>
        </div>
    </div>

    <div class="section">
        <div class="section-title">최근 거래</div>
        <div style="overflow-x:auto;">
            <table>
                <tr>
                    <th>시간</th><th>종목</th><th>방향</th>
                    <th>수량</th><th>가격</th><th>금액</th>
                </tr>
                {trade_rows if trade_rows else '<tr><td colspan="6" style="text-align:center;color:rgba(255,255,255,0.3);padding:20px;">기간 내 거래 없음</td></tr>'}
            </table>
        </div>
    </div>

    <div class="footer">
        Quant Bot {label} Report · 자동 생성 {now.strftime("%Y-%m-%d %H:%M")}
    </div>
</body>
</html>"""

        return html
