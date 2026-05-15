"""
=============================================================================
reporter/market_briefing.py - 시장 AI 브리핑 생성기
=============================================================================

매일 아침 시장 상황을 요약하는 브리핑 리포트를 생성합니다.

브리핑에 포함되는 정보:
1. DART 공시 요약 (호재/악재 분류)
2. 주요 뉴스 헤드라인 + 감성 분석
3. 거시경제 지표 (공포탐욕지수, VIX 등)
4. 포트폴리오 상태 요약
5. 종합 시장 전망 (키워드 기반)

"AI 브리핑"이란?
- 여러 데이터 소스를 자동으로 취합하여
- 키워드 기반으로 시장 분위기를 판단하고
- 사람이 읽기 좋은 형태로 정리한 리포트
- 진정한 AI(LLM)는 아니지만, 규칙 기반 자동화의 장점:
  → 비용 0, 속도 빠름, 일관성 있음, 오프라인 동작

아키텍처:
    DART API ──┐
    News RSS ──┤
    Macro API ──┼──→ MarketBriefing ──→ HTML 리포트 / 텔레그램 알림
    Portfolio ──┘

사용법:
    briefing = MarketBriefing(config)
    report = briefing.generate()
    # report["html"] → HTML 문자열
    # report["summary"] → 텍스트 요약
    # report["sentiment"] → "BULLISH" / "BEARISH" / "NEUTRAL"
=============================================================================
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime


logger = logging.getLogger("MarketBriefing")


class MarketBriefing:
    """
    시장 AI 브리핑 생성기

    여러 데이터 소스를 취합하여 하나의 브리핑으로 만듭니다.
    각 소스는 선택적 — 없으면 해당 섹션만 건너뜁니다.

    속성:
        config (dict): 봇 설정 (API 키, 종목 리스트 등)
    """

    def __init__(self, config: dict):
        """
        Args:
            config: 봇 설정 딕셔너리
                필요 키:
                - dart_api_key: DART API 키 (선택)
                - watchlist: 관심 종목 리스트
                - capital: 초기 자본금
        """
        self.config = config

    def generate(self, dart_data: Optional[Dict] = None,
                 news_data: Optional[List[Dict]] = None,
                 macro_data: Optional[Dict] = None,
                 portfolio_data: Optional[Dict] = None) -> Dict[str, Any]:
        """
        브리핑 생성 (메인 진입점)

        각 데이터는 이미 수집된 상태로 전달받습니다.
        None인 소스는 건너뜁니다.

        Args:
            dart_data: DARTCollector.collect() 결과
            news_data: NewsCollector 결과 리스트
            macro_data: MacroCollector 결과 (fear_greed, vix 등)
            portfolio_data: 포트폴리오 상태 딕셔너리

        Returns:
            {
                "html": str,           # HTML 브리핑 전문
                "summary": str,        # 텍스트 요약 (텔레그램용)
                "sentiment": str,      # BULLISH / BEARISH / NEUTRAL
                "sentiment_score": float,  # -1.0 ~ 1.0
                "sections": list,      # 개별 섹션 리스트
                "generated_at": str,   # 생성 시간
            }
        """
        sections = []
        sentiment_scores = []

        # 1. 시장 상태 헤더
        header = self._make_header()
        sections.append(header)

        # 2. DART 공시 섹션
        if dart_data and dart_data.get("total_count", 0) > 0:
            dart_section = self._dart_section(dart_data)
            sections.append(dart_section)
            # 호재 비율로 감성 점수 계산
            total = dart_data["total_count"]
            if total > 0:
                dart_sentiment = (dart_data["positive_count"] - dart_data["negative_count"]) / total
                sentiment_scores.append(("DART", dart_sentiment))

        # 3. 뉴스 섹션
        if news_data and len(news_data) > 0:
            news_section = self._news_section(news_data)
            sections.append(news_section)
            # 뉴스 감성 평균
            sentiments = [n.get("sentiment", 0) for n in news_data if "sentiment" in n]
            if sentiments:
                sentiment_scores.append(("뉴스", sum(sentiments) / len(sentiments)))

        # 4. 거시경제 섹션
        if macro_data:
            macro_section = self._macro_section(macro_data)
            sections.append(macro_section)
            # 공포탐욕 → 감성 변환 (0=극도 공포, 100=극도 탐욕)
            fg = macro_data.get("fear_greed", {}).get("value", 50)
            if fg is not None:
                macro_sentiment = (fg - 50) / 50  # 0~100 → -1~1
                sentiment_scores.append(("매크로", macro_sentiment))

        # 5. 포트폴리오 섹션
        if portfolio_data:
            port_section = self._portfolio_section(portfolio_data)
            sections.append(port_section)

        # 종합 감성 판단
        overall_score = 0.0
        if sentiment_scores:
            overall_score = sum(s[1] for s in sentiment_scores) / len(sentiment_scores)

        sentiment_label = (
            "BULLISH" if overall_score > 0.15
            else "BEARISH" if overall_score < -0.15
            else "NEUTRAL"
        )

        # 텍스트 요약 생성 (텔레그램 등에서 사용)
        summary = self._text_summary(sections, sentiment_label, overall_score)

        # HTML 렌더링
        html = self._render_html(sections, sentiment_label, overall_score)

        return {
            "html": html,
            "summary": summary,
            "sentiment": sentiment_label,
            "sentiment_score": round(overall_score, 3),
            "sentiment_details": sentiment_scores,
            "sections": [s["title"] for s in sections],
            "generated_at": datetime.now().isoformat(),
        }

    # ── 섹션 빌더들 ──

    def _make_header(self) -> Dict:
        """브리핑 헤더 (날짜 + 인사말)"""
        now = datetime.now()
        weekdays = ["월", "화", "수", "목", "금", "토", "일"]
        wd = weekdays[now.weekday()]
        greeting = "좋은 아침입니다" if now.hour < 12 else "안녕하세요"

        return {
            "title": "시장 브리핑",
            "content": f"{greeting}! {now.strftime('%Y년 %m월 %d일')} ({wd}요일) 시장 브리핑입니다.",
            "icon": "📋",
        }

    def _dart_section(self, dart_data: Dict) -> Dict:
        """
        DART 공시 섹션

        호재/악재를 분류하여 중요 공시를 하이라이트합니다.
        공시는 기업의 의무 공개 정보이므로 가장 신뢰도 높은 소스입니다.
        """
        lines = []
        lines.append(f"총 {dart_data['total_count']}건의 공시 "
                      f"(호재 {dart_data['positive_count']} / 악재 {dart_data['negative_count']})")

        # 주요 공시 목록
        disclosures = dart_data.get("disclosures", [])
        
        # 영향 점수 기준 정렬 (절대값이 큰 것 = 중요한 것)
        sorted_disc = sorted(disclosures,
                              key=lambda d: abs(d.get("impact_score", 0)),
                              reverse=True)

        for d in sorted_disc[:8]:  # 상위 8건
            icon = "🟢" if d.get("impact_label") == "호재" else \
                   "🔴" if d.get("impact_label") == "악재" else "⚪"
            corp = d.get("corp_name", "?")
            title = d.get("report_nm", "?")
            date = d.get("rcept_dt", "")
            lines.append(f"{icon} {corp} - {title} ({date})")

        return {
            "title": "DART 전자공시",
            "content": "\n".join(lines),
            "icon": "📜",
        }

    def _news_section(self, news_data: List[Dict]) -> Dict:
        """
        뉴스 섹션

        주요 뉴스 헤드라인과 감성 분석 결과를 보여줍니다.
        뉴스는 공시 다음으로 시장에 영향을 미치는 정보입니다.
        """
        lines = []
        total = len(news_data)
        positive = sum(1 for n in news_data if n.get("sentiment", 0) > 0)
        negative = sum(1 for n in news_data if n.get("sentiment", 0) < 0)
        lines.append(f"총 {total}건 (긍정 {positive} / 부정 {negative})")

        # 영향력 순으로 정렬
        sorted_news = sorted(news_data,
                              key=lambda n: abs(n.get("sentiment", 0)),
                              reverse=True)

        for n in sorted_news[:6]:
            sent = n.get("sentiment", 0)
            icon = "📈" if sent > 0 else "📉" if sent < 0 else "📰"
            title = n.get("title", "")[:60]
            lines.append(f"{icon} {title}")

        return {
            "title": "주요 뉴스",
            "content": "\n".join(lines),
            "icon": "📰",
        }

    def _macro_section(self, macro_data: Dict) -> Dict:
        """
        거시경제 섹션

        주요 거시경제 지표:
        - Fear & Greed Index: CNN이 만든 시장 심리 지표 (0=극도 공포, 100=극도 탐욕)
        - VIX: 변동성 지수 (높을수록 시장 불안)
        - 국채 금리: 장단기 금리차가 중요 (역전 시 경기침체 신호)
        """
        lines = []

        # 공포탐욕 지수
        fg = macro_data.get("fear_greed", {})
        if fg:
            value = fg.get("value", "N/A")
            label = fg.get("label", "")
            icon = "😱" if isinstance(value, (int, float)) and value < 25 else \
                   "😰" if isinstance(value, (int, float)) and value < 40 else \
                   "😐" if isinstance(value, (int, float)) and value < 60 else \
                   "😀" if isinstance(value, (int, float)) and value < 75 else "🤑"
            lines.append(f"{icon} 공포탐욕지수: {value} ({label})")

        # VIX
        vix = macro_data.get("vix")
        if vix:
            vix_val = vix if isinstance(vix, (int, float)) else vix.get("value", "N/A")
            vix_icon = "🔴" if isinstance(vix_val, (int, float)) and vix_val > 30 else \
                       "🟡" if isinstance(vix_val, (int, float)) and vix_val > 20 else "🟢"
            lines.append(f"{vix_icon} VIX: {vix_val}")

        # 기타 지표
        rates = macro_data.get("treasury_rates", {})
        if rates:
            y10 = rates.get("10y", "N/A")
            y2 = rates.get("2y", "N/A")
            lines.append(f"🏦 국채 금리: 10Y={y10}% / 2Y={y2}%")

        if not lines:
            lines.append("거시경제 데이터를 수집할 수 없습니다")

        return {
            "title": "거시경제 지표",
            "content": "\n".join(lines),
            "icon": "🌍",
        }

    def _portfolio_section(self, portfolio_data: Dict) -> Dict:
        """포트폴리오 상태 섹션"""
        lines = []
        total = portfolio_data.get("total_equity", 0)
        cash = portfolio_data.get("cash", 0)
        pnl = portfolio_data.get("total_pnl", 0)
        positions = portfolio_data.get("positions", {})

        lines.append(f"총 자산: ${total:,.0f} / 현금: ${cash:,.0f}")
        lines.append(f"총 수익률: {pnl:+.2f}%")
        lines.append(f"보유 종목: {len(positions)}개")

        # 종목별 요약
        for sym, p in list(positions.items())[:5]:
            name = p.get("name", sym)
            pnl_pct = p.get("pnl_pct", 0)
            icon = "🟢" if pnl_pct >= 0 else "🔴"
            lines.append(f"  {icon} {name}: {pnl_pct:+.2f}%")

        return {
            "title": "포트폴리오",
            "content": "\n".join(lines),
            "icon": "💼",
        }

    def _text_summary(self, sections: List[Dict],
                      sentiment: str, score: float) -> str:
        """
        텍스트 요약 (텔레그램/콘솔용)

        전체 브리핑을 2-3줄로 압축합니다.
        """
        emoji = {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "🟡"}
        lines = [
            f"{emoji.get(sentiment, '🟡')} [{sentiment}] 시장 브리핑 "
            f"(감성지수: {score:+.2f})",
        ]
        for sec in sections[1:]:  # 헤더 제외
            # 각 섹션의 첫 줄만 가져옴
            first_line = sec["content"].split("\n")[0]
            lines.append(f"  {sec['icon']} {sec['title']}: {first_line}")

        return "\n".join(lines)

    def _render_html(self, sections: List[Dict],
                     sentiment: str, score: float) -> str:
        """
        HTML 브리핑 렌더링

        PlayStation 디자인 시스템 기반의 다크 테마 HTML을 생성합니다.
        대시보드에 임베드되거나 별도 페이지로 열립니다.
        """
        sentiment_colors = {
            "BULLISH": "#009900",
            "BEARISH": "#d53b00",
            "NEUTRAL": "#888888",
        }
        color = sentiment_colors.get(sentiment, "#888888")

        # 섹션 HTML 조립
        sections_html = ""
        for sec in sections:
            content_escaped = sec["content"].replace("\n", "<br>")
            sections_html += f"""
            <div style="background:rgba(255,255,255,0.04);border-radius:8px;
                        padding:16px;margin-bottom:12px;">
                <div style="font-size:16px;font-weight:600;margin-bottom:8px;">
                    {sec['icon']} {sec['title']}
                </div>
                <div style="font-size:13px;line-height:1.7;color:rgba(255,255,255,0.75);">
                    {content_escaped}
                </div>
            </div>"""

        now = datetime.now().strftime("%Y-%m-%d %H:%M")

        html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <title>시장 브리핑 - {now}</title>
    <style>
        body {{
            background: #000; color: #fff; font-family: Inter, sans-serif;
            max-width: 720px; margin: 0 auto; padding: 24px;
        }}
        .header {{
            text-align: center; padding: 24px 0; border-bottom: 1px solid rgba(255,255,255,0.1);
            margin-bottom: 20px;
        }}
        .sentiment-badge {{
            display: inline-block; padding: 6px 20px; border-radius: 9999px;
            background: {color}; color: #fff; font-weight: 600; font-size: 14px;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1 style="font-weight:300;font-size:28px;margin:0;">📋 시장 AI 브리핑</h1>
        <p style="color:rgba(255,255,255,0.5);font-size:13px;margin:8px 0;">{now}</p>
        <div class="sentiment-badge">{sentiment} (감성지수: {score:+.2f})</div>
    </div>
    {sections_html}
    <div style="text-align:center;padding:16px;color:rgba(255,255,255,0.3);font-size:11px;">
        Quant Bot Market Briefing · 자동 생성
    </div>
</body>
</html>"""

        return html
