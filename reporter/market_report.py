"""
=============================================================================
reporter/market_report.py - 시장 동향 보고서 생성기
=============================================================================

매일 시장 상황을 종합 분석하여 독립 HTML 보고서를 생성합니다.

보고서에 포함되는 정보:
┌───────────────────────────────────────────────────────────┐
│ 1. 주요 지수 동향 (S&P500, 나스닥, 코스피, 코스닥 등)    │
│    - 당일/주간/월간 변동률 + 미니 차트                    │
│ 2. 섹터별 흐름 분석                                       │
│    - 관심 섹터의 대표 종목 성과 + 상승/하락 분류          │
│ 3. 거시경제 지표 (VIX, 공포탐욕지수, 금리 등)            │
│ 4. 주요 뉴스 요약 + 시장 영향도 분석                     │
│ 5. AI 시장 브리핑 (종합 판단: 강세/약세/중립)            │
└───────────────────────────────────────────────────────────┘

사용법:
    from reporter.market_report import MarketReportGenerator
    gen = MarketReportGenerator()
    filename = gen.generate(settings=current_settings)

아키텍처:
    yfinance  ──→ 지수/섹터 데이터
    MacroAPI  ──→ 거시경제 지표
    NewsRSS   ──→ 뉴스 헤드라인
    ↓
    MarketReportGenerator ──→ HTML 파일 (reports/market_YYYY-MM-DD.html)
=============================================================================
"""

import os
import logging
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Any

logger = logging.getLogger("MarketReport")


# ═══════════════════════════════════════════════════════════════════════════
# 주요 시장 지수 정의
# ═══════════════════════════════════════════════════════════════════════════
# yfinance 심볼 → 표시 이름 매핑
# 이 지수들의 변동률을 보고서 상단에 카드로 보여줍니다.

MARKET_INDICES = {
    # ── 미국 ──
    "^GSPC":  {"name": "S&P 500",     "flag": "🇺🇸", "currency": "USD"},
    "^IXIC":  {"name": "NASDAQ",      "flag": "🇺🇸", "currency": "USD"},
    "^DJI":   {"name": "Dow Jones",   "flag": "🇺🇸", "currency": "USD"},
    # ── 한국 ──
    "^KS11":  {"name": "KOSPI",       "flag": "🇰🇷", "currency": "KRW"},
    "^KQ11":  {"name": "KOSDAQ",      "flag": "🇰🇷", "currency": "KRW"},
    # ── 기타 ──
    "^VIX":   {"name": "VIX (공포지수)", "flag": "📊", "currency": ""},
    "GC=F":   {"name": "Gold",        "flag": "🥇", "currency": "USD"},
    "CL=F":   {"name": "Crude Oil",   "flag": "🛢️", "currency": "USD"},
    "BTC-USD": {"name": "Bitcoin",    "flag": "₿",  "currency": "USD"},
}

# ═══════════════════════════════════════════════════════════════════════════
# 섹터 대표 종목 (섹터 성과 분석용)
# ═══════════════════════════════════════════════════════════════════════════
# 각 섹터의 대표 ETF + 개별 종목으로 섹터 흐름을 파악합니다.

SECTOR_ETFS = {
    "반도체/AI":       {"etf": "SOXX",  "stocks": ["NVDA", "AMD", "AVGO", "TSM"]},
    "빅테크":          {"etf": "XLK",   "stocks": ["AAPL", "MSFT", "GOOG", "META"]},
    "에너지/배터리":    {"etf": "XLE",   "stocks": ["TSLA", "ENPH", "FSLR"]},
    "금융":            {"etf": "XLF",   "stocks": ["JPM", "GS", "BAC", "V"]},
    "헬스케어":         {"etf": "XLV",   "stocks": ["UNH", "JNJ", "LLY", "PFE"]},
    "소비재":           {"etf": "XLY",   "stocks": ["AMZN", "HD", "MCD", "NKE"]},
    "산업재":           {"etf": "XLI",   "stocks": ["CAT", "BA", "HON", "UNP"]},
}


class MarketReportGenerator:
    """
    시장 동향 보고서 생성기

    yfinance + 거시경제 + 뉴스 데이터를 수집하여
    시각적으로 보기 좋은 HTML 보고서를 생성합니다.
    """

    def __init__(self, reports_dir: Optional[str] = None):
        """
        Parameters:
            reports_dir: 보고서 저장 디렉토리 (기본: 프로젝트/reports)
        """
        if reports_dir is None:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            reports_dir = os.path.join(project_root, "reports")
        self.reports_dir = reports_dir
        os.makedirs(self.reports_dir, exist_ok=True)

    def generate(self, settings: Optional[Dict] = None,
                 report_date: Optional[date] = None) -> str:
        """
        시장 보고서 생성 (메인 진입점)

        Parameters:
            settings: 대시보드 설정 (관심 섹터, API 키 등)
            report_date: 보고서 날짜 (기본: 오늘)

        Returns:
            생성된 파일명 (예: "market_2026-05-07.html")
        """
        if report_date is None:
            report_date = date.today()
        if settings is None:
            settings = {}

        logger.info(f"[시장 보고서] 생성 시작: {report_date}")

        # ── 1. 데이터 수집 ──
        index_data = self._collect_indices()
        sector_data = self._collect_sectors()
        macro_data = self._collect_macro(settings)
        news_data = self._collect_news(settings)

        # ── 2. 시장 종합 판단 ──
        sentiment = self._analyze_sentiment(index_data, macro_data)

        # ── 3. HTML 생성 ──
        html = self._build_html(
            report_date=report_date,
            index_data=index_data,
            sector_data=sector_data,
            macro_data=macro_data,
            news_data=news_data,
            sentiment=sentiment,
            settings=settings,
        )

        # ── 4. 파일 저장 ──
        filename = f"market_{report_date.isoformat()}.html"
        filepath = os.path.join(self.reports_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(html)

        logger.info(f"[시장 보고서] 저장 완료: {filepath}")
        return filename

    # ═══════════════════════════════════════════════════════════════════════
    # 데이터 수집 함수들
    # ═══════════════════════════════════════════════════════════════════════

    def _collect_indices(self) -> Dict[str, Dict]:
        """
        주요 시장 지수 데이터 수집

        yfinance로 각 지수의 현재가, 변동률, 최근 차트 데이터를 가져옵니다.
        실패한 지수는 건너뜁니다 (Graceful Degradation).

        Returns:
            {"^GSPC": {"name": "S&P 500", "price": 5200, "change_pct": 0.5, ...}, ...}
        """
        results = {}
        try:
            import yfinance as yf
        except ImportError:
            logger.warning("[시장 보고서] yfinance 미설치")
            return results

        for symbol, info in MARKET_INDICES.items():
            try:
                ticker = yf.Ticker(symbol)
                # 1개월 일봉 데이터 (차트 + 변동률 계산용)
                hist = ticker.history(period="1mo", interval="1d", auto_adjust=True)

                if hist.empty or len(hist) < 2:
                    continue

                current = hist["Close"].iloc[-1]
                prev = hist["Close"].iloc[-2]
                day_change = ((current - prev) / prev) * 100

                # 주간 변동률 (5거래일 전 대비)
                week_ago = hist["Close"].iloc[-6] if len(hist) >= 6 else hist["Close"].iloc[0]
                week_change = ((current - week_ago) / week_ago) * 100

                # 월간 변동률 (첫 데이터 대비)
                month_ago = hist["Close"].iloc[0]
                month_change = ((current - month_ago) / month_ago) * 100

                # 최근 20일 종가 (미니 차트용)
                chart_data = hist["Close"].tail(20).tolist()

                results[symbol] = {
                    **info,
                    "price": round(current, 2),
                    "day_change": round(day_change, 2),
                    "week_change": round(week_change, 2),
                    "month_change": round(month_change, 2),
                    "chart_data": chart_data,
                    "high_52w": round(hist["Close"].max(), 2),
                    "low_52w": round(hist["Close"].min(), 2),
                }

            except Exception as e:
                logger.debug(f"[시장 보고서] 지수 수집 실패 ({symbol}): {e}")
                continue

        logger.info(f"[시장 보고서] 지수 {len(results)}개 수집 완료")
        return results

    def _collect_sectors(self) -> Dict[str, Dict]:
        """
        섹터별 성과 데이터 수집

        각 섹터 ETF의 변동률 + 대표 종목 성과를 가져옵니다.

        Returns:
            {"반도체/AI": {"etf_change": 1.5, "stocks": [{"symbol": "NVDA", ...}], ...}}
        """
        results = {}
        try:
            import yfinance as yf
        except ImportError:
            return results

        for sector_name, sector_info in SECTOR_ETFS.items():
            try:
                # 섹터 ETF 변동률
                etf_ticker = yf.Ticker(sector_info["etf"])
                etf_hist = etf_ticker.history(period="5d", interval="1d", auto_adjust=True)

                etf_change = 0
                if len(etf_hist) >= 2:
                    cur = etf_hist["Close"].iloc[-1]
                    prev = etf_hist["Close"].iloc[-2]
                    etf_change = ((cur - prev) / prev) * 100

                # 대표 종목들 성과
                stock_results = []
                for stock_sym in sector_info["stocks"]:
                    try:
                        st = yf.Ticker(stock_sym)
                        sh = st.history(period="5d", interval="1d", auto_adjust=True)
                        if len(sh) >= 2:
                            sc = sh["Close"].iloc[-1]
                            sp = sh["Close"].iloc[-2]
                            s_change = ((sc - sp) / sp) * 100
                            stock_results.append({
                                "symbol": stock_sym,
                                "price": round(sc, 2),
                                "change_pct": round(s_change, 2),
                            })
                    except Exception:
                        continue

                results[sector_name] = {
                    "etf": sector_info["etf"],
                    "etf_change": round(etf_change, 2),
                    "stocks": stock_results,
                }

            except Exception as e:
                logger.debug(f"[시장 보고서] 섹터 수집 실패 ({sector_name}): {e}")
                continue

        logger.info(f"[시장 보고서] 섹터 {len(results)}개 수집 완료")
        return results

    def _collect_macro(self, settings: Dict) -> Dict[str, Any]:
        """
        거시경제 지표 수집

        MacroCollector를 사용하여 VIX, 금리 등 거시 데이터를 가져옵니다.
        API 키가 없으면 yfinance의 VIX 데이터만 사용합니다.
        """
        macro = {}

        # VIX는 이미 지수 데이터에서 가져올 수 있으므로 별도 처리 불필요
        try:
            from collectors.macro import MacroCollector
            collector = MacroCollector()
            if collector.fred:
                raw = collector.collect()
                if raw and isinstance(raw, dict):
                    macro = raw
        except Exception as e:
            logger.debug(f"[시장 보고서] 거시경제 수집 실패: {e}")

        # 공포탐욕지수 (CNN Fear & Greed) - yfinance 대체
        try:
            import yfinance as yf
            vix = yf.Ticker("^VIX")
            vh = vix.history(period="5d", interval="1d", auto_adjust=True)
            if not vh.empty:
                vix_val = vh["Close"].iloc[-1]
                macro["vix"] = round(vix_val, 2)
                # VIX 기반 간이 공포탐욕 판단
                if vix_val < 15:
                    macro["fear_greed"] = "극도의 탐욕"
                    macro["fear_greed_score"] = 80
                elif vix_val < 20:
                    macro["fear_greed"] = "탐욕"
                    macro["fear_greed_score"] = 65
                elif vix_val < 25:
                    macro["fear_greed"] = "중립"
                    macro["fear_greed_score"] = 50
                elif vix_val < 30:
                    macro["fear_greed"] = "공포"
                    macro["fear_greed_score"] = 35
                else:
                    macro["fear_greed"] = "극도의 공포"
                    macro["fear_greed_score"] = 15
        except Exception:
            pass

        return macro

    def _collect_news(self, settings: Dict) -> List[Dict]:
        """
        뉴스 수집 + 감성 분석

        RSS 피드에서 시장 관련 뉴스를 가져오고,
        키워드 기반으로 긍정/부정을 분류합니다.
        """
        news_list = []
        try:
            from collectors.news import NewsCollector
            collector = NewsCollector()
            raw = collector.safe_collect("market")
            if raw and isinstance(raw, list):
                news_list = raw[:10]  # 최대 10개
            elif raw and isinstance(raw, dict) and "articles" in raw:
                news_list = raw["articles"][:10]
        except Exception as e:
            logger.debug(f"[시장 보고서] 뉴스 수집 실패: {e}")

        return news_list

    def _analyze_sentiment(self, index_data: Dict, macro_data: Dict) -> Dict:
        """
        시장 종합 판단

        지수 변동률과 거시경제 지표를 종합하여
        BULLISH / NEUTRAL / BEARISH를 결정합니다.

        판단 로직:
        - 주요 지수 평균 변동률 > +0.5% → 강세 성향
        - 주요 지수 평균 변동률 < -0.5% → 약세 성향
        - VIX > 25 → 약세 보정
        - VIX < 15 → 강세 보정
        """
        score = 50  # 기본 중립 (0~100)

        # 지수 변동률 반영
        changes = []
        for sym in ["^GSPC", "^IXIC", "^KS11"]:
            if sym in index_data:
                changes.append(index_data[sym].get("day_change", 0))

        if changes:
            avg_change = sum(changes) / len(changes)
            score += avg_change * 10  # 1% 변동 = 10점

        # VIX 보정
        vix = macro_data.get("vix", 20)
        if vix > 30:
            score -= 15
        elif vix > 25:
            score -= 8
        elif vix < 15:
            score += 10
        elif vix < 18:
            score += 5

        # 범위 제한
        score = max(0, min(100, score))

        if score >= 65:
            label = "BULLISH"
            label_kr = "강세"
            emoji = "🟢"
            desc = "시장이 전반적으로 긍정적인 흐름을 보이고 있습니다."
        elif score <= 35:
            label = "BEARISH"
            label_kr = "약세"
            emoji = "🔴"
            desc = "시장에 하락 압력이 존재하며 주의가 필요합니다."
        else:
            label = "NEUTRAL"
            label_kr = "중립"
            emoji = "🟡"
            desc = "뚜렷한 방향성 없이 혼조세를 보이고 있습니다."

        return {
            "score": round(score),
            "label": label,
            "label_kr": label_kr,
            "emoji": emoji,
            "description": desc,
            "vix": vix,
        }

    # ═══════════════════════════════════════════════════════════════════════
    # HTML 생성
    # ═══════════════════════════════════════════════════════════════════════

    def _build_html(self, report_date, index_data, sector_data,
                    macro_data, news_data, sentiment, settings) -> str:
        """
        최종 HTML 보고서 조립

        라이트 테마 + 카드 UI + Plotly 미니 차트 스타일
        기존 일일 보고서와 동일한 디자인 언어를 사용합니다.
        """
        date_str = report_date.strftime("%Y년 %m월 %d일")
        now_str = datetime.now().strftime("%H:%M:%S")

        # 각 섹션 HTML 생성
        index_html = self._render_indices(index_data)
        sector_html = self._render_sectors(sector_data)
        macro_html = self._render_macro(macro_data)
        news_html = self._render_news(news_data)
        sentiment_html = self._render_sentiment(sentiment)

        return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>시장 동향 보고서 - {date_str}</title>
<script src="https://cdn.jsdelivr.net/npm/plotly.js-dist@2.27.0/plotly-basic.min.js"></script>
<style>
/* ── 기본 스타일 (라이트 테마) ── */
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    font-family: 'Segoe UI', -apple-system, sans-serif;
    background: #f5f7fa; color: #1a1a2e;
    line-height: 1.6; padding: 20px;
}}
.container {{ max-width: 1100px; margin: 0 auto; }}

/* ── 헤더 ── */
.header {{
    background: linear-gradient(135deg, #0d1b2a 0%, #1b2838 100%);
    color: white; border-radius: 16px; padding: 32px;
    margin-bottom: 24px; text-align: center;
}}
.header h1 {{ font-size: 28px; margin-bottom: 8px; }}
.header .subtitle {{ color: rgba(255,255,255,0.7); font-size: 14px; }}

/* ── 센티먼트 배지 ── */
.sentiment-badge {{
    display: inline-flex; align-items: center; gap: 8px;
    padding: 8px 20px; border-radius: 20px; font-weight: 700;
    font-size: 16px; margin-top: 16px;
}}
.sentiment-BULLISH {{ background: rgba(46,204,113,0.2); color: #2ecc71; }}
.sentiment-BEARISH {{ background: rgba(231,76,60,0.2); color: #e74c3c; }}
.sentiment-NEUTRAL {{ background: rgba(241,196,15,0.2); color: #f1c40f; }}

/* ── 카드 ── */
.section {{ margin-bottom: 24px; }}
.section-title {{
    font-size: 18px; font-weight: 700; margin-bottom: 16px;
    padding-left: 12px; border-left: 4px solid #0070d1;
}}
.card {{
    background: white; border-radius: 12px; padding: 20px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.06);
    margin-bottom: 12px;
}}

/* ── 지수 그리드 ── */
.index-grid {{
    display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    gap: 12px;
}}
.index-card {{
    background: white; border-radius: 12px; padding: 16px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.06);
    transition: transform 0.2s;
}}
.index-card:hover {{ transform: translateY(-2px); }}
.index-name {{ font-size: 13px; color: #666; margin-bottom: 4px; }}
.index-price {{ font-size: 22px; font-weight: 700; }}
.index-change {{ font-size: 14px; font-weight: 600; }}
.up {{ color: #e74c3c; }}
.down {{ color: #3498db; }}
.change-row {{ display: flex; gap: 12px; margin-top: 8px; font-size: 12px; color: #888; }}
.change-row span {{ display: flex; flex-direction: column; align-items: center; }}
.change-row .label {{ font-size: 10px; color: #aaa; }}

/* ── 섹터 테이블 ── */
.sector-table {{ width: 100%; border-collapse: collapse; }}
.sector-table th {{
    text-align: left; padding: 10px 12px; font-size: 13px;
    color: #666; border-bottom: 2px solid #eee;
}}
.sector-table td {{
    padding: 10px 12px; border-bottom: 1px solid #f0f0f0; font-size: 14px;
}}
.sector-table tr:hover {{ background: #f8f9fa; }}

/* ── 거시경제 게이지 ── */
.macro-grid {{
    display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
    gap: 12px;
}}
.macro-card {{
    background: white; border-radius: 12px; padding: 16px; text-align: center;
    box-shadow: 0 2px 8px rgba(0,0,0,0.06);
}}
.macro-label {{ font-size: 12px; color: #888; margin-bottom: 4px; }}
.macro-value {{ font-size: 24px; font-weight: 700; }}

/* ── 뉴스 ── */
.news-item {{
    display: flex; align-items: flex-start; gap: 12px;
    padding: 12px 0; border-bottom: 1px solid #f0f0f0;
}}
.news-item:last-child {{ border-bottom: none; }}
.news-dot {{ width: 8px; height: 8px; border-radius: 50%; margin-top: 6px; flex-shrink: 0; }}
.news-title {{ font-size: 14px; font-weight: 500; }}
.news-source {{ font-size: 12px; color: #999; margin-top: 2px; }}

/* ── 미니 차트 ── */
.mini-chart {{ width: 100%; height: 40px; margin-top: 8px; }}

/* ── 푸터 ── */
.footer {{
    text-align: center; color: #aaa; font-size: 12px;
    margin-top: 32px; padding: 16px;
}}

/* ── 반응형 ── */
@media (max-width: 768px) {{
    body {{ padding: 12px; }}
    .index-grid {{ grid-template-columns: repeat(2, 1fr); }}
    .macro-grid {{ grid-template-columns: repeat(2, 1fr); }}
    .header h1 {{ font-size: 22px; }}
}}
</style>
</head>
<body>
<div class="container">

<!-- 헤더 -->
<div class="header">
    <h1>📊 시장 동향 보고서</h1>
    <div class="subtitle">{date_str} · 생성 시각 {now_str}</div>
    {sentiment_html}
</div>

<!-- 1. 주요 지수 -->
<div class="section">
    <div class="section-title">📈 주요 지수 동향</div>
    {index_html}
</div>

<!-- 2. 섹터별 흐름 -->
<div class="section">
    <div class="section-title">🏭 섹터별 흐름</div>
    {sector_html}
</div>

<!-- 3. 거시경제 -->
<div class="section">
    <div class="section-title">🌐 거시경제 지표</div>
    {macro_html}
</div>

<!-- 4. 뉴스 -->
<div class="section">
    <div class="section-title">📰 주요 뉴스</div>
    {news_html}
</div>

<!-- 푸터 -->
<div class="footer">
    Quant Bot Market Report · 이 보고서는 자동 생성되었으며 투자 조언이 아닙니다.
</div>

</div>
</body>
</html>"""

    # ── 각 섹션 렌더링 ──

    def _render_sentiment(self, sentiment: Dict) -> str:
        """시장 종합 판단 배지"""
        return (
            f'<div class="sentiment-badge sentiment-{sentiment["label"]}">'
            f'{sentiment["emoji"]} {sentiment["label_kr"]} '
            f'(점수: {sentiment["score"]}/100)'
            f'</div>'
            f'<div style="color:rgba(255,255,255,0.6);font-size:13px;margin-top:8px;">'
            f'{sentiment["description"]}</div>'
        )

    def _render_indices(self, index_data: Dict) -> str:
        """주요 지수 카드 그리드"""
        if not index_data:
            return '<div class="card">지수 데이터를 가져올 수 없습니다.</div>'

        cards = []
        for i, (symbol, data) in enumerate(index_data.items()):
            change = data["day_change"]
            color_class = "up" if change >= 0 else "down"
            sign = "+" if change >= 0 else ""

            # 가격 포맷
            price = data["price"]
            if data.get("currency") == "KRW":
                price_str = f"{price:,.0f}"
            elif price > 1000:
                price_str = f"{price:,.2f}"
            else:
                price_str = f"{price:.2f}"

            # 미니 차트 ID
            chart_id = f"chart_{i}"

            # 주간/월간 변동률
            wc = data.get("week_change", 0)
            mc = data.get("month_change", 0)
            w_sign = "+" if wc >= 0 else ""
            m_sign = "+" if mc >= 0 else ""
            w_class = "up" if wc >= 0 else "down"
            m_class = "up" if mc >= 0 else "down"

            card = f"""
            <div class="index-card">
                <div class="index-name">{data["flag"]} {data["name"]}</div>
                <div class="index-price">{price_str}</div>
                <div class="index-change {color_class}">{sign}{change:.2f}%</div>
                <div class="change-row">
                    <span><span class="label">주간</span><span class="{w_class}">{w_sign}{wc:.1f}%</span></span>
                    <span><span class="label">월간</span><span class="{m_class}">{m_sign}{mc:.1f}%</span></span>
                </div>
                <div class="mini-chart" id="{chart_id}"></div>
            </div>"""
            cards.append(card)

        # Plotly 미니 차트 스크립트
        chart_scripts = []
        for i, (symbol, data) in enumerate(index_data.items()):
            chart_data = data.get("chart_data", [])
            if chart_data:
                color = "#e74c3c" if data["day_change"] >= 0 else "#3498db"
                chart_scripts.append(f"""
                Plotly.newPlot('chart_{i}', [{{
                    y: {chart_data},
                    type: 'scatter', mode: 'lines',
                    line: {{color: '{color}', width: 1.5}},
                    fill: 'tozeroy', fillcolor: '{color}22',
                }}], {{
                    margin: {{t:0,b:0,l:0,r:0}}, height: 40,
                    xaxis: {{visible:false}}, yaxis: {{visible:false}},
                    paper_bgcolor: 'transparent', plot_bgcolor: 'transparent',
                }}, {{displayModeBar: false, responsive: true}});""")

        grid_html = '<div class="index-grid">' + ''.join(cards) + '</div>'

        if chart_scripts:
            grid_html += '<script>' + ''.join(chart_scripts) + '</script>'

        return grid_html

    def _render_sectors(self, sector_data: Dict) -> str:
        """섹터별 성과 테이블"""
        if not sector_data:
            return '<div class="card">섹터 데이터를 가져올 수 없습니다.</div>'

        rows = []
        # 섹터를 ETF 변동률 순으로 정렬
        sorted_sectors = sorted(
            sector_data.items(),
            key=lambda x: x[1].get("etf_change", 0),
            reverse=True
        )

        for sector_name, data in sorted_sectors:
            etf_change = data.get("etf_change", 0)
            color_class = "up" if etf_change >= 0 else "down"
            sign = "+" if etf_change >= 0 else ""

            # 대표 종목 표시
            stock_strs = []
            for s in data.get("stocks", [])[:4]:
                sc = s.get("change_pct", 0)
                s_sign = "+" if sc >= 0 else ""
                s_class = "up" if sc >= 0 else "down"
                stock_strs.append(
                    f'<span style="margin-right:12px;">'
                    f'{s["symbol"]} <span class="{s_class}">{s_sign}{sc:.1f}%</span></span>'
                )

            rows.append(f"""
            <tr>
                <td><strong>{sector_name}</strong></td>
                <td>{data.get("etf", "")}</td>
                <td class="{color_class}" style="font-weight:600;">{sign}{etf_change:.2f}%</td>
                <td>{''.join(stock_strs)}</td>
            </tr>""")

        return f"""
        <div class="card">
            <table class="sector-table">
                <thead>
                    <tr>
                        <th>섹터</th>
                        <th>ETF</th>
                        <th>변동률</th>
                        <th>대표 종목</th>
                    </tr>
                </thead>
                <tbody>{''.join(rows)}</tbody>
            </table>
        </div>"""

    def _render_macro(self, macro_data: Dict) -> str:
        """거시경제 지표 카드"""
        if not macro_data:
            return '<div class="card">거시경제 데이터를 가져올 수 없습니다.</div>'

        cards = []

        # VIX
        if "vix" in macro_data:
            vix = macro_data["vix"]
            vix_color = "#e74c3c" if vix > 25 else "#f39c12" if vix > 20 else "#2ecc71"
            cards.append(f"""
            <div class="macro-card">
                <div class="macro-label">VIX 공포지수</div>
                <div class="macro-value" style="color:{vix_color}">{vix:.1f}</div>
            </div>""")

        # 공포탐욕지수
        if "fear_greed" in macro_data:
            fg = macro_data["fear_greed"]
            fg_score = macro_data.get("fear_greed_score", 50)
            fg_color = "#e74c3c" if fg_score < 30 else "#f39c12" if fg_score < 60 else "#2ecc71"
            cards.append(f"""
            <div class="macro-card">
                <div class="macro-label">시장 심리</div>
                <div class="macro-value" style="color:{fg_color}">{fg}</div>
                <div style="font-size:12px;color:#aaa;margin-top:4px;">점수: {fg_score}/100</div>
            </div>""")

        # FRED 지표들 (있으면)
        fred_items = {
            "FEDFUNDS": ("기준금리", "%"),
            "T10Y2Y": ("장단기 금리차", "%"),
            "DGS10": ("10년 국채", "%"),
            "UNRATE": ("실업률", "%"),
        }
        for key, (label, unit) in fred_items.items():
            if key in macro_data:
                val = macro_data[key]
                if isinstance(val, (int, float)):
                    cards.append(f"""
                    <div class="macro-card">
                        <div class="macro-label">{label}</div>
                        <div class="macro-value">{val:.2f}{unit}</div>
                    </div>""")

        if not cards:
            return '<div class="card">거시경제 데이터를 가져올 수 없습니다.</div>'

        return '<div class="macro-grid">' + ''.join(cards) + '</div>'

    def _render_news(self, news_data: List[Dict]) -> str:
        """뉴스 목록"""
        if not news_data:
            return '<div class="card">수집된 뉴스가 없습니다.</div>'

        items = []
        for news in news_data[:10]:
            title = news.get("title", news.get("headline", "제목 없음"))
            source = news.get("source", news.get("publisher", ""))
            sentiment = news.get("sentiment", "neutral")

            dot_color = "#2ecc71" if sentiment == "positive" else "#e74c3c" if sentiment == "negative" else "#f39c12"

            items.append(f"""
            <div class="news-item">
                <div class="news-dot" style="background:{dot_color}"></div>
                <div>
                    <div class="news-title">{title}</div>
                    <div class="news-source">{source}</div>
                </div>
            </div>""")

        return '<div class="card">' + ''.join(items) + '</div>'
