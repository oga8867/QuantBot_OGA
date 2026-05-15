"""
reporter/daily_report.py - 일일 거래 보고서 생성기 (v2.0)
stock-analyzer 스타일: 라이트 테마 + 카드 UI + Plotly 차트
성과 통계: 승률, 손익비, 샤프비, MDD, 칼마비
"""

import os
import json
import statistics
from datetime import datetime, date
from typing import List, Dict, Optional


class DailyReportGenerator:
    """일일 거래 보고서를 HTML 파일로 생성"""

    def __init__(self, reports_dir=None):
        if reports_dir is None:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            reports_dir = os.path.join(project_root, "reports")
        self.reports_dir = reports_dir
        os.makedirs(self.reports_dir, exist_ok=True)

    def generate(self, trades=None, positions=None, account_info=None,
                 signals=None, initial_capital=10_000_000, currency="KRW",
                 news_summary="", equity_history=None, report_date=None):
        if report_date is None:
            report_date = date.today()
        trades = trades or []
        positions = positions or []
        account_info = account_info or {}
        signals = signals or []
        equity_history = equity_history or []

        total_equity = account_info.get("total_equity", initial_capital)
        cash = account_info.get("cash", initial_capital)
        positions_value = account_info.get("positions_value", 0)
        total_pnl = total_equity - initial_capital
        total_pnl_pct = (total_pnl / initial_capital) * 100 if initial_capital > 0 else 0

        # ★ side는 소문자("buy")로 저장될 수 있으므로 .upper() 비교 필수
        buy_trades = [t for t in trades if t.get("side", "").upper() == "BUY"]
        sell_trades = [t for t in trades if t.get("side", "").upper() == "SELL"]
        total_buy_value = sum(t.get("total", 0) for t in buy_trades)
        total_sell_value = sum(t.get("total", 0) for t in sell_trades)
        buy_signals = [s for s in signals if s.get("signal", "").upper() == "BUY"]
        sell_signals = [s for s in signals if s.get("signal", "").upper() == "SELL"]
        realized_pnl = sum(t.get("pnl", 0) for t in sell_trades)

        perf = self._calc_perf(sell_trades, equity_history, initial_capital, total_equity)
        cs = "\u20a9" if currency == "KRW" else "$"

        eq_chart = self._equity_chart(equity_history, initial_capital)
        dd_chart = self._drawdown_chart(equity_history, initial_capital)
        pos_chart = self._position_chart(positions, cash, cs)

        # ── Buy-and-Hold 벤치마크 비교 ──
        # 봇이 단순 매수 후 보유 전략을 이기는지 정량적으로 검증
        bench_html = self._benchmark_html(equity_history, initial_capital, positions)

        html = self._build_html(
            report_date=report_date, total_equity=total_equity, cash=cash,
            positions_value=positions_value, total_pnl=total_pnl,
            total_pnl_pct=total_pnl_pct, initial_capital=initial_capital,
            trades=trades, buy_trades=buy_trades, sell_trades=sell_trades,
            total_buy_value=total_buy_value, total_sell_value=total_sell_value,
            realized_pnl=realized_pnl, positions=positions, signals=signals,
            buy_signals=buy_signals, sell_signals=sell_signals,
            cs=cs, news_summary=news_summary, eq_chart=eq_chart,
            dd_chart=dd_chart, pos_chart=pos_chart, perf=perf,
            bench_html=bench_html,
        )

        filename = f"daily_{report_date.isoformat()}.html"
        filepath = os.path.join(self.reports_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(html)
        return filepath

    # === 성과 통계 ===

    def _calc_perf(self, sell_trades, equity_history, initial_capital, total_equity):
        s = dict(win_rate=0, profit_factor=0, avg_win=0, avg_loss=0,
                 max_drawdown=0, max_drawdown_pct=0, sharpe_ratio=0,
                 calmar_ratio=0, total_win=0, total_loss=0, win_count=0, loss_count=0)
        if sell_trades:
            wins = [t for t in sell_trades if t.get("pnl", 0) > 0]
            losses = [t for t in sell_trades if t.get("pnl", 0) < 0]
            s["win_count"] = len(wins)
            s["loss_count"] = len(losses)
            s["win_rate"] = len(wins) / len(sell_trades) * 100
            s["total_win"] = sum(t.get("pnl", 0) for t in wins)
            s["total_loss"] = abs(sum(t.get("pnl", 0) for t in losses))
            if wins: s["avg_win"] = s["total_win"] / len(wins)
            if losses: s["avg_loss"] = s["total_loss"] / len(losses)
            if s["total_loss"] > 0:
                s["profit_factor"] = s["total_win"] / s["total_loss"]
            elif s["total_win"] > 0:
                s["profit_factor"] = 999.99
        if equity_history:
            eqs = [h.get("equity", initial_capital) for h in equity_history]
            peak = eqs[0]; max_dd = 0
            for eq in eqs:
                if eq > peak: peak = eq
                dd = (peak - eq) / peak if peak > 0 else 0
                max_dd = max(max_dd, dd)
            s["max_drawdown"] = max_dd
            s["max_drawdown_pct"] = max_dd * 100
        if len(equity_history) >= 2:
            eqs = [h.get("equity", initial_capital) for h in equity_history]
            rets = []
            for i in range(1, len(eqs)):
                if eqs[i-1] > 0: rets.append(eqs[i]/eqs[i-1] - 1)
            if len(rets) > 1:
                mr = statistics.mean(rets)
                sr = statistics.stdev(rets)
                rf = 0.035 / 252
                if sr > 0: s["sharpe_ratio"] = (mr - rf) / sr * (252**0.5)
                if s["max_drawdown"] > 0 and eqs[0] > 0:
                    tr = eqs[-1]/eqs[0] - 1
                    ar = (1+tr)**(252/len(eqs)) - 1
                    s["calmar_ratio"] = ar / s["max_drawdown"]
        return s

    # === 차트 ===

    def _benchmark_html(self, equity_history, initial_capital, positions):
        """
        Buy-and-Hold 벤치마크 비교 카드 HTML

        equity 데이터가 충분하면 KOSPI 200 또는 S&P 500과 봇 성과를 비교.
        """
        if not equity_history or len(equity_history) < 2:
            return ""

        try:
            from reporter.benchmark import BenchmarkComparator

            # 보유 비중에 따라 벤치마크 선택
            kr_value = sum(
                p.get("market_value", 0) for p in positions
                if not (p.get("symbol", "")[:1].isalpha() and "." not in p.get("symbol", ""))
            )
            us_value = sum(
                p.get("market_value", 0) for p in positions
                if p.get("symbol", "")[:1].isalpha() and "." not in p.get("symbol", "")
            )
            total_pos = max(kr_value + us_value, 1)
            positions_summary = {
                "KR": kr_value / total_pos * 100,
                "US": us_value / total_pos * 100,
            }

            comp = BenchmarkComparator()
            result = comp.compare(
                equity_history=equity_history,
                initial_capital=initial_capital,
                market="auto",
                positions_summary=positions_summary,
            )

            # 색상
            alpha_color = "#4caf50" if result.alpha_pct >= 0 else "#ef5350"
            ir_color = "#4caf50" if result.information_ratio >= 0.5 else (
                "#ff9800" if result.information_ratio >= 0 else "#ef5350"
            )
            verdict_emoji = {
                "outperform": "✅",
                "tie": "⚖️",
                "underperform": "⚠️",
                "insufficient": "ℹ️",
            }.get(result.verdict, "")

            return f'''
<div class="card"><h2>{verdict_emoji} Buy-and-Hold 벤치마크 비교 ({result.benchmark_name})</h2>
<p style="font-size:12px;color:#666;margin-bottom:12px;">
이 봇이 단순 매수 후 보유보다 나은지 측정 — 정보비율 0.5 이상이 우수, 1.0+ 매우 우수
</p>
<div class="grid-4">
<div class="stat-box"><div class="value" style="color:{alpha_color};">{result.alpha_pct:+.2f}%</div><div class="label">알파 (초과수익)</div></div>
<div class="stat-box"><div class="value" style="color:{ir_color};">{result.information_ratio:.2f}</div><div class="label">정보비율</div></div>
<div class="stat-box"><div class="value">{result.bot_return_pct:+.2f}%</div><div class="label">봇 수익률 ({result.days}일)</div></div>
<div class="stat-box"><div class="value">{result.benchmark_return_pct:+.2f}%</div><div class="label">{result.benchmark_name}</div></div>
<div class="stat-box"><div class="value">{result.bot_sharpe:.2f}</div><div class="label">봇 샤프비</div></div>
<div class="stat-box"><div class="value">{result.benchmark_sharpe:.2f}</div><div class="label">벤치 샤프비</div></div>
<div class="stat-box"><div class="value">{result.bot_max_dd:.2f}%</div><div class="label">봇 MDD</div></div>
<div class="stat-box"><div class="value">{result.benchmark_max_dd:.2f}%</div><div class="label">벤치 MDD</div></div>
</div>
<div style="font-size:11px;color:#888;margin-top:10px;text-align:center;">
상관계수: {result.correlation:+.2f} ({"독립적" if abs(result.correlation) < 0.3 else "강한 연관" if abs(result.correlation) > 0.7 else "보통 연관"})
</div>
</div>'''
        except Exception as e:
            return f'<div class="card"><p style="color:#888;">벤치마크 비교 실패: {e}</p></div>'

    def _equity_chart(self, eh, ic):
        if not eh:
            return '<p style="color:#888;text-align:center;">자산 추이 데이터 없음</p>'
        try:
            dates = [h.get("date","") for h in eh]
            eqs = [h.get("equity", ic) for h in eh]
            rets = [(eq/ic-1)*100 for eq in eqs]
            return (
                '<div id="equityChart" style="width:100%;height:350px;"></div><script>'
                '(function(){var d=' + json.dumps(dates) + ',e=' + json.dumps(eqs)
                + ',r=' + json.dumps([round(x,2) for x in rets])
                + ';var t1={x:d,y:e,type:"scatter",mode:"lines",line:{color:"#1565c0",width:2},name:"\uc790\uc0b0",yaxis:"y"}'
                + ';var t2={x:d,y:r,type:"scatter",mode:"lines",line:{color:"#4caf50",width:1.5,dash:"dot"},name:"\uc218\uc775\ub960(%)",yaxis:"y2"}'
                + ';var bl={x:d,y:Array(d.length).fill(' + str(ic) + '),type:"scatter",mode:"lines",line:{color:"#999",width:1,dash:"dash"},name:"\ucd08\uae30\uc790\ubcf8",yaxis:"y"}'
                + ';var lo={xaxis:{title:""},yaxis:{title:"\uc790\uc0b0",side:"left"},yaxis2:{title:"%",side:"right",overlaying:"y",ticksuffix:"%"},'
                + 'margin:{l:80,r:80,t:20,b:40},legend:{x:0,y:1.1,orientation:"h"},paper_bgcolor:"rgba(0,0,0,0)",plot_bgcolor:"rgba(0,0,0,0)"};'
                + 'if(typeof Plotly!=="undefined")Plotly.newPlot("equityChart",[t1,t2,bl],lo,{responsive:true})})();</script>'
            )
        except Exception:
            return '<p style="color:#888;text-align:center;">차트 생성 실패</p>'

    def _drawdown_chart(self, eh, ic):
        if not eh or len(eh) < 2:
            return '<p style="color:#888;text-align:center;">드로우다운 데이터 부족</p>'
        try:
            dates = [h.get("date","") for h in eh]
            eqs = [h.get("equity", ic) for h in eh]
            dd = []; peak = eqs[0]
            for eq in eqs:
                if eq > peak: peak = eq
                dd.append(round((eq-peak)/peak*100, 2) if peak > 0 else 0)
            return (
                '<div id="drawdownChart" style="width:100%;height:300px;"></div><script>'
                '(function(){var d=' + json.dumps(dates) + ',dd=' + json.dumps(dd)
                + ';var t={x:d,y:dd,type:"scatter",mode:"lines",fill:"tozeroy",'
                + 'fillcolor:"rgba(239,83,80,0.3)",line:{color:"#EF5350",width:1.5},name:"Drawdown"};'
                + 'var lo={title:{text:"Drawdown",font:{size:14}},xaxis:{title:""},'
                + 'yaxis:{title:"%",ticksuffix:"%"},margin:{l:60,r:20,t:40,b:40},'
                + 'paper_bgcolor:"rgba(0,0,0,0)",plot_bgcolor:"rgba(0,0,0,0)",'
                + 'shapes:[{type:"line",x0:d[0],x1:d[d.length-1],y0:0,y1:0,line:{color:"#888",width:1,dash:"dash"}}]};'
                + 'if(typeof Plotly!=="undefined")Plotly.newPlot("drawdownChart",[t],lo,{responsive:true})})();</script>'
            )
        except Exception:
            return '<p style="color:#888;text-align:center;">드로우다운 차트 실패</p>'

    def _position_chart(self, positions, cash, cs):
        if not positions: return ""
        try:
            labels = [p.get("name", p.get("symbol","?")) for p in positions] + ["\ud604\uae08"]
            # \u2605 market_value(KRW \ud658\uc0b0) \uc6b0\uc120 \uc0ac\uc6a9 \u2014 current_price\ub294 native\ub77c\uc11c USD \ud63c\ud569 \uc2dc \ucc28\ud2b8 \uc65c\uace1\ub428
            values = [
                p.get("market_value") or p.get("shares",0)*p.get("current_price",0)
                for p in positions
            ] + [cash]
            return (
                '<div id="posChart" style="width:100%;height:300px;"></div><script>'
                '(function(){var t={labels:' + json.dumps(labels) + ',values:' + json.dumps(values)
                + ',type:"pie",hole:0.5,textinfo:"label+percent",textposition:"outside",'
                + 'marker:{line:{width:1,color:"#fff"}}};'
                + 'var lo={margin:{l:20,r:20,t:20,b:20},paper_bgcolor:"rgba(0,0,0,0)",showlegend:false};'
                + 'if(typeof Plotly!=="undefined")Plotly.newPlot("posChart",[t],lo,{responsive:true})})();</script>'
            )
        except Exception:
            return ""

    # === HTML 빌더 헬퍼 ===

    def _perf_html(self, p, cs):
        def c(v, th=0): return "#4caf50" if v >= th else "#ef5350"
        return f"""<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
<div class="stat-box"><div class="value" style="color:{c(p['win_rate'],50)};">{p['win_rate']:.1f}%</div><div class="label">승률 (Win Rate)</div></div>
<div class="stat-box"><div class="value" style="color:{c(p['profit_factor'],1)};">{p['profit_factor']:.2f}</div><div class="label">손익비 (Profit Factor)</div></div>
<div class="stat-box"><div class="value" style="color:{c(p['sharpe_ratio'])};">{p['sharpe_ratio']:.2f}</div><div class="label">샤프비 (Sharpe)</div></div>
<div class="stat-box"><div class="value" style="color:#ef5350;">-{p['max_drawdown_pct']:.2f}%</div><div class="label">최대낙폭 (MDD)</div></div>
<div class="stat-box"><div class="value" style="color:#4caf50;">{cs}{p['avg_win']:,.0f}</div><div class="label">평균 수익</div></div>
<div class="stat-box"><div class="value" style="color:#ef5350;">{cs}{p['avg_loss']:,.0f}</div><div class="label">평균 손실</div></div>
<div class="stat-box"><div class="value">{p['win_count']} / {p['loss_count']}</div><div class="label">수익/손실 거래</div></div>
<div class="stat-box"><div class="value" style="color:{c(p['calmar_ratio'])};">{p['calmar_ratio']:.2f}</div><div class="label">칼마비 (Calmar)</div></div>
</div>"""

    def _trades_html(self, trades, cs):
        if not trades:
            return '<tr><td colspan="7" style="text-align:center;color:#999;padding:20px;">거래 없음</td></tr>'
        rows = ""
        for t in trades:
            side = t.get("side","").upper()
            sc = "#4caf50" if side == "BUY" else "#ef5350"
            sl = "매수" if side == "BUY" else "매도"
            rows += f'<tr><td>{t.get("timestamp","")}</td><td><b>{t.get("name",t.get("symbol",""))}</b></td>'
            rows += f'<td style="color:{sc};font-weight:bold;">{sl}</td><td>{t.get("quantity",0):,}</td>'
            rows += f'<td>{cs}{t.get("price",0):,.0f}</td><td>{cs}{t.get("total",0):,.0f}</td>'
            rows += f'<td>{t.get("strategy","")}</td></tr>'
        return rows

    def _positions_html(self, positions, cs):
        if not positions:
            return '<tr><td colspan="7" style="text-align:center;color:#999;padding:20px;">보유 포지션 없음</td></tr>'
        rows = ""
        for p in positions:
            pnl = p.get("pnl",0); pp = p.get("pnl_pct",0)
            pc = "#4caf50" if pnl >= 0 else "#ef5350"
            ps = "+" if pnl >= 0 else ""
            # ★ 평가액(ev): market_value는 KRW 환산값. 미국 주식의 경우
            # current_price * shares는 USD라서 잘못된 값이 나옴
            # market_value 우선 사용, 없으면 fallback (한국 주식은 동일)
            ev = p.get("market_value") or p.get("shares",0)*p.get("current_price",0)
            # ★ 평균가/현재가 표시: 미국 주식은 USD로 표시 (헷갈림 방지)
            from utils.market import is_us_stock
            sym = p.get("symbol", "")
            us = is_us_stock(sym)
            price_prefix = "$" if us else cs
            rows += f'<tr><td><b>{p.get("name",p.get("symbol",""))}</b></td><td>{p.get("shares",0):,}</td>'
            rows += f'<td>{price_prefix}{p.get("avg_price",0):,.2f}</td><td>{price_prefix}{p.get("current_price",0):,.2f}</td>'
            rows += f'<td style="color:{pc};">{ps}{cs}{pnl:,.0f}</td><td style="color:{pc};">{ps}{pp:.2f}%</td>'
            rows += f'<td>{cs}{ev:,.0f}</td></tr>'
        return rows

    def _signals_html(self, signals):
        if not signals:
            return '<p style="text-align:center;color:#999;">당일 신호 없음</p>'
        h = '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px;">'
        for s in signals:
            sig = s.get("signal","HOLD").upper()
            c = "#4caf50" if sig == "BUY" else "#ef5350" if sig == "SELL" else "#ff9800"
            l = "매수" if sig == "BUY" else "매도" if sig == "SELL" else "관망"
            st = s.get("strength",0)
            reasons = s.get("reasons",[])
            if isinstance(reasons, str): reasons = [reasons]
            rt = ", ".join(reasons[:3]) if reasons else "없음"
            h += f'<div style="background:#f5f5f5;border-left:4px solid {c};padding:12px;border-radius:8px;">'
            h += f'<div style="display:flex;justify-content:space-between;"><b>{s.get("symbol","")}</b>'
            h += f'<span style="color:{c};font-weight:bold;">{l} ({st:.0%})</span></div>'
            h += f'<div style="font-size:12px;color:#666;margin-top:6px;">근거: {rt}</div>'
            h += f'<div style="font-size:11px;color:#999;margin-top:4px;">{s.get("timestamp","")}</div></div>'
        h += '</div>'
        return h

    # === 메인 HTML ===

    def _build_html(self, **ctx):
        rd = ctx["report_date"]; te = ctx["total_equity"]; ca = ctx["cash"]
        pv = ctx["positions_value"]; tp = ctx["total_pnl"]; tpp = ctx["total_pnl_pct"]
        ic = ctx["initial_capital"]; trades = ctx["trades"]
        bt = ctx["buy_trades"]; st = ctx["sell_trades"]
        tbv = ctx["total_buy_value"]; tsv = ctx["total_sell_value"]
        rp = ctx["realized_pnl"]; pos = ctx["positions"]
        sigs = ctx["signals"]; bs = ctx["buy_signals"]; ss = ctx["sell_signals"]
        cs = ctx["cs"]; ns = ctx["news_summary"]
        eq_c = ctx["eq_chart"]; dd_c = ctx["dd_chart"]; pc = ctx["pos_chart"]
        perf = ctx["perf"]
        bench = ctx.get("bench_html", "")

        pcol = "#4caf50" if tp >= 0 else "#ef5350"
        psign = "+" if tp >= 0 else ""
        rcol = "#4caf50" if rp >= 0 else "#ef5350"
        rsign = "+" if rp >= 0 else ""

        tr_rows = self._trades_html(trades, cs)
        pos_rows = self._positions_html(pos, cs)
        sig_html = self._signals_html(sigs)
        perf_html = self._perf_html(perf, cs)
        pos_or_empty = pc if pc else '<div style="text-align:center;color:#999;padding:30px;">보유 포지션 없음</div>'

        news_sec = ""
        if ns:
            news_sec = f'<div class="card" style="border-left:4px solid #1565c0;"><h2 style="color:#0d47a1;">AI 뉴스 분석</h2><div style="font-size:14px;line-height:1.8;color:#333;">{ns}</div></div>'

        CSS = """* { margin:0; padding:0; box-sizing:border-box; }
body { font-family:'Segoe UI',-apple-system,BlinkMacSystemFont,sans-serif; background:#f8f9fa; color:#333; line-height:1.6; }
.container { max-width:1100px; margin:0 auto; padding:20px; }
.header { background:linear-gradient(135deg,#1a237e,#283593); color:white; padding:30px; border-radius:16px; margin-bottom:20px; }
.header h1 { font-size:28px; margin-bottom:5px; }
.header .subtitle { font-size:14px; opacity:0.8; }
.card { background:white; border-radius:12px; padding:20px; margin-bottom:16px; box-shadow:0 2px 8px rgba(0,0,0,0.06); }
.card h2 { font-size:18px; color:#1a237e; margin-bottom:12px; border-bottom:2px solid #e8eaf6; padding-bottom:8px; }
.grid-2 { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
.grid-4 { display:grid; grid-template-columns:repeat(4,1fr); gap:12px; }
.stat-box { background:#f5f5f5; border-radius:10px; padding:14px; text-align:center; }
.stat-box .value { font-size:22px; font-weight:700; }
.stat-box .label { font-size:12px; color:#888; margin-top:4px; }
table { width:100%; border-collapse:collapse; font-size:13px; }
th { background:#e8eaf6; color:#1a237e; padding:10px 8px; text-align:left; font-weight:600; }
td { padding:8px; border-bottom:1px solid #eee; }
tr:hover { background:#f5f5f5; }
.disclaimer { background:#fff3e0; border-radius:8px; padding:16px; margin-top:20px; font-size:12px; color:#e65100; }
@media(max-width:768px){ .grid-2,.grid-4{grid-template-columns:1fr;} }"""

        return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>일일 거래 보고서 - {rd.isoformat()}</title>
<script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
<style>{CSS}</style>
</head>
<body>
<div class="container">
<div class="header"><h1>일일 거래 보고서</h1><div class="subtitle">{rd.isoformat()} | 퀀트봇 자동매매 시스템</div></div>

<div class="grid-4">
<div class="stat-box"><div class="value" style="color:{pcol};">{cs}{te:,.0f}</div><div class="label">총 자산</div></div>
<div class="stat-box"><div class="value" style="color:{pcol};">{psign}{tpp:.2f}%</div><div class="label">수익률</div></div>
<div class="stat-box"><div class="value">{cs}{ca:,.0f}</div><div class="label">현금</div></div>
<div class="stat-box"><div class="value">{cs}{pv:,.0f}</div><div class="label">포지션 평가액</div></div>
<div class="stat-box"><div class="value" style="color:{pcol};">{psign}{cs}{tp:,.0f}</div><div class="label">총 손익</div></div>
<div class="stat-box"><div class="value">{len(trades)}</div><div class="label">거래 건수</div></div>
<div class="stat-box"><div class="value">{len(bt)} / {len(st)}</div><div class="label">매수 / 매도</div></div>
<div class="stat-box"><div class="value">{len(bs)} / {len(ss)}</div><div class="label">매수신호 / 매도신호</div></div>
</div>

<div class="card"><h2>자산 추이</h2>
<p style="font-size:13px;color:#666;margin-bottom:10px;">봇 운영 기간 총 자산 변화. 점선=초기자본({cs}{ic:,.0f})</p>
{eq_c}</div>

{bench}

<div class="grid-2">
<div class="card"><h2>성과 통계</h2>
<p style="font-size:12px;color:#888;margin-bottom:12px;">매도 거래 기준 핵심 지표</p>
{perf_html}</div>
<div class="card"><h2>드로우다운 (Drawdown)</h2>
<p style="font-size:12px;color:#888;margin-bottom:12px;">고점 대비 자산 하락폭</p>
{dd_c}</div>
</div>

<div class="grid-2">
<div class="card"><h2>포트폴리오 구성</h2>{pos_or_empty}</div>
<div class="card"><h2>거래 금액 요약</h2>
<div style="display:grid;gap:12px;">
<div class="stat-box"><div class="value" style="color:#4caf50;">{cs}{tbv:,.0f}</div><div class="label">총 매수 금액</div></div>
<div class="stat-box"><div class="value" style="color:#ef5350;">{cs}{tsv:,.0f}</div><div class="label">총 매도 금액</div></div>
<div class="stat-box"><div class="value" style="color:{rcol};">{rsign}{cs}{rp:,.0f}</div><div class="label">실현 손익</div></div>
<div class="stat-box"><div class="value">{cs}{ic:,.0f}</div><div class="label">초기 자본금</div></div>
</div></div>
</div>

<div class="card"><h2>거래 내역 ({len(trades)}건)</h2>
<table><thead><tr><th>시간</th><th>종목</th><th>매매</th><th>수량</th><th>가격</th><th>총액</th><th>전략</th></tr></thead>
<tbody>{tr_rows}</tbody></table></div>

<div class="card"><h2>보유 포지션 ({len(pos)}종목)</h2>
<table><thead><tr><th>종목</th><th>수량</th><th>평균가</th><th>현재가</th><th>손익</th><th>수익률</th><th>평가액</th></tr></thead>
<tbody>{pos_rows}</tbody></table></div>

<div class="card"><h2>매매 신호 ({len(sigs)}건)</h2>{sig_html}</div>

{news_sec}

<div class="disclaimer"><b>면책 조항:</b> 이 보고서는 자동 생성된 것으로, 투자 자문이 아닙니다. 과거 성과가 미래 수익을 보장하지 않습니다.</div>
</div>
</body></html>"""
