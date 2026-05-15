"""
=============================================================================
reporter/html_report.py - HTML 보고서 생성기
=============================================================================

분석 결과를 단일 HTML 파일로 생성합니다.
Plotly.js로 인터랙티브 차트를 만들고, CSS를 인라인으로 포함하여
파일 하나만 있으면 어디서든 열 수 있습니다.

Plotly란?
- Python/JS 기반의 인터랙티브 차트 라이브러리
- 마우스 호버, 줌, 패닝 등 인터랙션 지원
- HTML로 export하면 브라우저에서 바로 볼 수 있음
- matplotlib과 달리 웹 기반이라 공유가 쉬움

보고서 구성:
1. 종목 요약 정보 (이름, 가격, 변동률)
2. 캔들스틱 차트 + 이동평균
3. RSI 차트
4. MACD 차트
5. 볼린저 밴드 차트
6. 거래량 차트
7. 매매 신호 요약
8. 리스크 분석 (자본금/리스크 설정 기반)
=============================================================================
"""

import pandas as pd
from typing import Optional
from datetime import datetime

try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    import plotly.io as pio
    PLOTLY_AVAILABLE = True
except ImportError:
    PLOTLY_AVAILABLE = False


class HTMLReportGenerator:
    """
    HTML 보고서 생성기

    분석 결과를 시각적으로 정리하여 HTML 파일로 출력합니다.
    Plotly 차트가 인터랙티브하게 동작합니다.
    """

    def __init__(self, settings=None):
        """
        Parameters:
            settings: Settings 객체 (자본금/리스크 설정 포함)
        """
        self.settings = settings

    def generate(
        self,
        symbol: str,
        df: pd.DataFrame,
        signal=None,
        info: Optional[dict] = None,
        output_path: Optional[str] = None
    ) -> str:
        """
        종합 분석 보고서를 HTML로 생성

        Parameters:
            symbol: 종목 코드
            df: 기술적 지표가 포함된 OHLCV DataFrame
            signal: TechnicalSignal 객체
            info: 기업 기본 정보 딕셔너리
            output_path: 저장 경로 (None이면 자동 생성)

        Returns:
            저장된 HTML 파일 경로
        """
        if not PLOTLY_AVAILABLE:
            return self._generate_simple_report(symbol, df, signal, output_path)

        # 출력 경로 설정
        if output_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = f"reports/{symbol}_{timestamp}.html"

        # Plotly 차트 생성 (4행 서브플롯)
        fig = self._create_chart(symbol, df)

        # 차트를 HTML 조각으로 변환 (plotly.js는 CDN에서 로드)
        chart_html = pio.to_html(fig, full_html=False, include_plotlyjs=False)

        # 전체 HTML 조립
        html_content = self._assemble_html(
            symbol=symbol,
            chart_html=chart_html,
            df=df,
            signal=signal,
            info=info
        )

        # 파일 저장
        import os
        os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else "reports", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        return output_path

    def _create_chart(self, symbol: str, df: pd.DataFrame) -> go.Figure:
        """
        Plotly 서브플롯 차트 생성

        4개의 차트를 수직으로 배치:
        1. 캔들스틱 + 이동평균 + 볼린저밴드 (60%)
        2. RSI (15%)
        3. MACD (15%)
        4. 거래량 (10%)
        """
        # 서브플롯 생성: 4행, 공유 X축
        fig = make_subplots(
            rows=4, cols=1,
            shared_xaxes=True,
            vertical_spacing=0.03,
            row_heights=[0.5, 0.17, 0.17, 0.16],
            subplot_titles=[
                f"{symbol} 가격 차트",
                "RSI (14)",
                "MACD",
                "거래량"
            ]
        )

        # ─── 1행: 캔들스틱 + 이동평균 + 볼린저 ─────────────────────

        # 캔들스틱
        fig.add_trace(
            go.Candlestick(
                x=df.index,
                open=df["Open"],
                high=df["High"],
                low=df["Low"],
                close=df["Close"],
                name="OHLC",
                increasing_line_color="#26a69a",  # 상승: 초록
                decreasing_line_color="#ef5350",  # 하락: 빨강
            ),
            row=1, col=1
        )

        # 이동평균선
        if "SMA_20" in df.columns:
            fig.add_trace(
                go.Scatter(
                    x=df.index, y=df["SMA_20"],
                    name="SMA 20", line=dict(color="#ff9800", width=1)
                ),
                row=1, col=1
            )
        if "SMA_50" in df.columns:
            fig.add_trace(
                go.Scatter(
                    x=df.index, y=df["SMA_50"],
                    name="SMA 50", line=dict(color="#2196f3", width=1)
                ),
                row=1, col=1
            )

        # 볼린저 밴드 (반투명 영역)
        if "BB_Upper" in df.columns:
            fig.add_trace(
                go.Scatter(
                    x=df.index, y=df["BB_Upper"],
                    name="BB Upper", line=dict(color="gray", width=0.5, dash="dot"),
                    showlegend=False
                ),
                row=1, col=1
            )
            fig.add_trace(
                go.Scatter(
                    x=df.index, y=df["BB_Lower"],
                    name="BB Lower", line=dict(color="gray", width=0.5, dash="dot"),
                    fill="tonexty", fillcolor="rgba(128,128,128,0.1)",
                    showlegend=False
                ),
                row=1, col=1
            )

        # ─── 2행: RSI ──────────────────────────────────────────────

        if "RSI" in df.columns:
            fig.add_trace(
                go.Scatter(
                    x=df.index, y=df["RSI"],
                    name="RSI", line=dict(color="#ab47bc", width=1.5)
                ),
                row=2, col=1
            )
            # 과매수/과매도 기준선
            fig.add_hline(y=70, line_dash="dash", line_color="red",
                         opacity=0.5, row=2, col=1)
            fig.add_hline(y=30, line_dash="dash", line_color="green",
                         opacity=0.5, row=2, col=1)
            fig.add_hline(y=50, line_dash="dot", line_color="gray",
                         opacity=0.3, row=2, col=1)

        # ─── 3행: MACD ─────────────────────────────────────────────

        if "MACD" in df.columns:
            fig.add_trace(
                go.Scatter(
                    x=df.index, y=df["MACD"],
                    name="MACD", line=dict(color="#2196f3", width=1.5)
                ),
                row=3, col=1
            )
            fig.add_trace(
                go.Scatter(
                    x=df.index, y=df["MACD_Signal"],
                    name="Signal", line=dict(color="#ff9800", width=1)
                ),
                row=3, col=1
            )
            # 히스토그램 (막대)
            colors = ["#26a69a" if v >= 0 else "#ef5350"
                      for v in df["MACD_Hist"]]
            fig.add_trace(
                go.Bar(
                    x=df.index, y=df["MACD_Hist"],
                    name="Histogram", marker_color=colors,
                    showlegend=False
                ),
                row=3, col=1
            )

        # ─── 4행: 거래량 ───────────────────────────────────────────

        if "Volume" in df.columns:
            # 상승/하락에 따라 색 구분
            colors = ["#26a69a" if df["Close"].iloc[i] >= df["Open"].iloc[i]
                      else "#ef5350" for i in range(len(df))]
            fig.add_trace(
                go.Bar(
                    x=df.index, y=df["Volume"],
                    name="Volume", marker_color=colors,
                    showlegend=False
                ),
                row=4, col=1
            )

        # ─── 레이아웃 설정 ─────────────────────────────────────────

        fig.update_layout(
            height=900,
            title_text=f"{symbol} 기술적 분석",
            xaxis_rangeslider_visible=False,
            template="plotly_white",
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
            margin=dict(l=60, r=20, t=80, b=40)
        )

        # Y축 라벨
        fig.update_yaxes(title_text="가격", row=1, col=1)
        fig.update_yaxes(title_text="RSI", row=2, col=1, range=[0, 100])
        fig.update_yaxes(title_text="MACD", row=3, col=1)
        fig.update_yaxes(title_text="거래량", row=4, col=1)

        return fig

    def _assemble_html(
        self,
        symbol: str,
        chart_html: str,
        df: pd.DataFrame,
        signal=None,
        info: Optional[dict] = None
    ) -> str:
        """HTML 전체 페이지 조립"""

        # 최신 데이터
        latest = df.iloc[-1]
        prev_close = df["Close"].iloc[-2] if len(df) > 1 else latest["Close"]
        change_pct = (latest["Close"] - prev_close) / prev_close * 100

        # 기업명
        company_name = ""
        if info:
            company_name = info.get("shortName", info.get("longName", symbol))

        # 신호 정보
        signal_html = ""
        if signal:
            signal_color = {"BUY": "#26a69a", "SELL": "#ef5350", "HOLD": "#ff9800"}
            color = signal_color.get(signal.signal, "#666")
            reasons_list = "".join(f"<li>{r}</li>" for r in signal.reasons)
            signal_html = (
                f'<div class="signal-box" style="border-left: 4px solid {color};">'
                f'<h3>매매 신호: <span style="color:{color}">{signal.signal}</span>'
                f' (강도: {signal.strength:.0%})</h3>'
                f'<ul>{reasons_list}</ul>'
                f'</div>'
            )

        # 리스크 분석 섹션
        risk_html = ""
        if self.settings:
            cap = self.settings.capital
            risk = self.settings.risk
            atr_val = latest.get("ATR", 0)
            price = latest["Close"]

            # 포지션 크기 계산 (ATR 기반)
            if atr_val > 0:
                # 리스크 금액 = 자본금 × 1회 리스크 비율
                risk_amount = cap.total_capital * risk.risk_per_trade
                # 손절폭 = ATR × 배수
                stop_distance = atr_val * risk.stop_loss_atr_multiplier
                # 매수 가능 수량 = 리스크 금액 / 손절폭
                position_shares = int(risk_amount / stop_distance)
                position_value = position_shares * price
                position_pct = (position_value / cap.total_capital) * 100
            else:
                position_shares = 0
                position_value = 0
                position_pct = 0

            risk_html = (
                '<div class="risk-box">'
                '<h3>리스크 분석</h3>'
                '<table>'
                f'<tr><td>총 자본금</td><td>{cap.total_capital:,.0f} {cap.currency}</td></tr>'
                f'<tr><td>1회 리스크</td><td>{risk.risk_per_trade*100:.1f}% '
                f'({cap.total_capital * risk.risk_per_trade:,.0f} {cap.currency})</td></tr>'
                f'<tr><td>ATR (14일)</td><td>{atr_val:.2f}</td></tr>'
                f'<tr><td>손절선</td><td>{price - atr_val * risk.stop_loss_atr_multiplier:.2f} '
                f'(현재가 - {risk.stop_loss_atr_multiplier}×ATR)</td></tr>'
                f'<tr><td>권장 수량</td><td>{position_shares:,}주</td></tr>'
                f'<tr><td>포지션 크기</td><td>{position_value:,.0f} {cap.currency} '
                f'({position_pct:.1f}%)</td></tr>'
                f'<tr><td>최대 포지션 한도</td><td>{risk.max_position_size*100:.0f}%</td></tr>'
                '</table>'
                '</div>'
            )

        # 변동률 색상
        change_color = "#26a69a" if change_pct >= 0 else "#ef5350"
        change_sign = "+" if change_pct >= 0 else ""

        # 전체 HTML 조립
        html = (
            '<!DOCTYPE html>\n'
            '<html lang="ko">\n'
            '<head>\n'
            '<meta charset="UTF-8">\n'
            f'<title>{symbol} 분석 보고서</title>\n'
            '<script src="https://cdn.plot.ly/plotly-latest.min.js"></script>\n'
            '<style>\n'
            'body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", '
            'Roboto, sans-serif; max-width: 1200px; margin: 0 auto; padding: 20px; '
            'background: #fafafa; color: #333; }\n'
            '.header { background: white; border-radius: 8px; padding: 24px; '
            'margin-bottom: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }\n'
            '.header h1 { margin: 0 0 8px; font-size: 24px; }\n'
            '.price { font-size: 32px; font-weight: bold; }\n'
            '.change { font-size: 18px; margin-left: 12px; }\n'
            '.chart-container { background: white; border-radius: 8px; padding: 16px; '
            'margin-bottom: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }\n'
            '.signal-box { background: white; border-radius: 8px; padding: 16px; '
            'margin-bottom: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }\n'
            '.signal-box h3 { margin-top: 0; }\n'
            '.signal-box ul { margin: 8px 0; padding-left: 20px; }\n'
            '.risk-box { background: white; border-radius: 8px; padding: 16px; '
            'margin-bottom: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }\n'
            '.risk-box h3 { margin-top: 0; }\n'
            '.risk-box table { width: 100%; border-collapse: collapse; }\n'
            '.risk-box td { padding: 8px 12px; border-bottom: 1px solid #eee; }\n'
            '.risk-box td:first-child { font-weight: 500; color: #666; width: 40%; }\n'
            '.footer { text-align: center; color: #999; font-size: 12px; '
            'margin-top: 24px; }\n'
            '</style>\n'
            '</head>\n'
            '<body>\n'
            '<div class="header">\n'
            f'<h1>{company_name} ({symbol})</h1>\n'
            f'<span class="price">{latest["Close"]:.2f}</span>\n'
            f'<span class="change" style="color:{change_color}">'
            f'{change_sign}{change_pct:.2f}%</span>\n'
            f'<p style="color:#999;margin-top:8px;">분석 시점: '
            f'{datetime.now().strftime("%Y-%m-%d %H:%M")} | '
            f'데이터 기간: {df.index[0].strftime("%Y-%m-%d")} ~ '
            f'{df.index[-1].strftime("%Y-%m-%d")}</p>\n'
            '</div>\n'
            f'{signal_html}\n'
            f'{risk_html}\n'
            f'<div class="chart-container">{chart_html}</div>\n'
            '<div class="footer">\n'
            '<p>이 보고서는 참고용이며 투자 권유가 아닙니다. '
            '투자 결정은 본인의 판단과 책임하에 이루어져야 합니다.</p>\n'
            f'<p>Generated by QuantBot | {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>\n'
            '</div>\n'
            '</body>\n'
            '</html>'
        )

        return html

    def _generate_simple_report(
        self,
        symbol: str,
        df: pd.DataFrame,
        signal,
        output_path: Optional[str]
    ) -> str:
        """Plotly가 없을 때 간단한 텍스트 HTML 생성 (폴백)"""
        if output_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = f"reports/{symbol}_{timestamp}.html"

        latest = df.iloc[-1]
        html = (
            '<!DOCTYPE html><html><head><meta charset="UTF-8">'
            f'<title>{symbol} Report</title></head><body>'
            f'<h1>{symbol} 분석 결과</h1>'
            f'<p>종가: {latest["Close"]:.2f}</p>'
            f'<p>RSI: {latest.get("RSI", "N/A")}</p>'
            f'<p>Plotly를 설치하면 인터랙티브 차트를 볼 수 있습니다: '
            f'pip install plotly</p>'
            '</body></html>'
        )

        import os
        os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else "reports", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)

        return output_path
