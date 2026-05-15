"""
=============================================================================
notifier/telegram_bot.py - 텔레그램 알림 봇
=============================================================================

매매 신호, 일일 리포트, 리스크 경고를 텔레그램으로 전송합니다.

텔레그램 봇 설정 방법:
1. @BotFather에게 메시지 → /newbot → 이름 입력 → 토큰 받기
2. 본인에게 아무 메시지 → https://api.telegram.org/bot{TOKEN}/getUpdates
   → chat_id 확인
3. .env 파일에 TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID 저장

왜 텔레그램을 쓰는가?
- 무료, 봇 API가 간단
- 모바일/PC 모두에서 즉시 알림 수신
- 봇에게 명령어를 보내서 상태 조회도 가능 (/status, /stop 등)
=============================================================================
"""

import os
import json
from typing import Optional, List, Dict
from datetime import datetime

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False


class TelegramNotifier:
    """
    텔레그램 알림 전송기

    환경변수가 없으면 자동으로 비활성화됩니다 (Graceful Degradation).

    사용법:
        notifier = TelegramNotifier()
        notifier.send_signal("AAPL", "BUY", 0.85, ["RSI 과매도", "MACD 골든크로스"])
        notifier.send_daily_report(results)
        notifier.send_risk_alert("MDD 10% 초과!")
    """

    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None
    ):
        """
        Parameters:
            bot_token: 텔레그램 봇 토큰 (None이면 환경변수에서 로드)
            chat_id: 메시지 수신 채팅 ID
        """
        self.bot_token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
        self.enabled = bool(self.bot_token and self.chat_id and REQUESTS_AVAILABLE)

        if not self.enabled:
            # 비활성화 사유 로깅 (에러가 아님, 선택적 기능이므로)
            pass

    @property
    def api_url(self) -> str:
        """텔레그램 API 기본 URL"""
        return f"https://api.telegram.org/bot{self.bot_token}"

    def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        """
        텔레그램 메시지 전송

        Parameters:
            text: 전송할 메시지 (HTML 또는 Markdown 포맷 지원)
            parse_mode: "HTML" 또는 "Markdown"

        Returns:
            성공 여부
        """
        if not self.enabled:
            print(f"[Telegram OFF] {text[:100]}...")
            return False

        try:
            response = requests.post(
                f"{self.api_url}/sendMessage",
                json={
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": parse_mode,
                },
                timeout=10
            )
            return response.status_code == 200
        except Exception as e:
            print(f"[Telegram 전송 실패] {e}")
            return False

    def send_signal(
        self,
        symbol: str,
        action: str,
        confidence: float,
        reasons: List[str],
        price: Optional[float] = None
    ) -> bool:
        """
        매매 신호 알림 전송

        Parameters:
            symbol: 종목 코드
            action: "BUY" / "SELL" / "HOLD"
            confidence: 신뢰도 (0~1)
            reasons: 신호 발생 이유 리스트
            price: 현재가

        Returns:
            전송 성공 여부
        """
        # 이모지 매핑
        icons = {"BUY": "🟢 매수", "SELL": "🔴 매도", "HOLD": "⚪ 관망"}
        icon = icons.get(action, action)

        # 메시지 구성
        price_str = f"\n💰 현재가: {price:,.2f}" if price else ""
        reasons_str = "\n".join(f"  • {r}" for r in reasons[:5])

        message = (
            f"<b>📊 매매 신호</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"종목: <b>{symbol}</b>\n"
            f"신호: <b>{icon}</b>\n"
            f"신뢰도: {confidence:.0%}{price_str}\n\n"
            f"<b>📋 근거:</b>\n{reasons_str}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )

        return self.send_message(message)

    def send_daily_report(self, results: List[Dict]) -> bool:
        """
        일일 분석 결과 리포트 전송

        Parameters:
            results: analyze_symbol() 반환 결과 리스트

        Returns:
            전송 성공 여부
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        lines = [f"<b>📈 일일 분석 리포트</b>", f"⏰ {now}", "━━━━━━━━━━━━━━━"]

        for r in results:
            if r.get("status") == "success":
                icons = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪"}
                icon = icons.get(r.get("signal", "").upper(), "❓")
                lines.append(
                    f"{icon} <b>{r['symbol']}</b>: "
                    f"{r.get('latest_price', 0):,.2f} "
                    f"(RSI: {r.get('rsi', 0):.0f}, "
                    f"강도: {r.get('strength', 0):.0%})"
                )
            else:
                lines.append(f"❌ {r['symbol']}: 수집 실패")

        lines.append("━━━━━━━━━━━━━━━")

        # 요약 통계
        buy_count = sum(1 for r in results if r.get("signal", "").upper() == "BUY")
        sell_count = sum(1 for r in results if r.get("signal", "").upper() == "SELL")
        lines.append(f"매수 {buy_count} | 매도 {sell_count} | 총 {len(results)}종목")

        return self.send_message("\n".join(lines))

    def send_risk_alert(self, message: str, level: str = "WARNING") -> bool:
        """
        리스크 경고 알림

        Parameters:
            message: 경고 내용
            level: "WARNING" 또는 "CRITICAL"

        Returns:
            전송 성공 여부
        """
        icon = "⚠️" if level == "WARNING" else "🚨"
        text = (
            f"{icon} <b>리스크 경고 [{level}]</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"{message}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        return self.send_message(text)

    def send_trade_executed(
        self,
        symbol: str,
        side: str,
        quantity: int,
        price: float,
        total_value: float
    ) -> bool:
        """
        거래 체결 알림

        Parameters:
            symbol: 종목
            side: "BUY" / "SELL"
            quantity: 수량
            price: 체결가
            total_value: 총 거래금액

        Returns:
            전송 성공 여부
        """
        icon = "🛒" if side == "BUY" else "💸"
        side_kr = "매수" if side == "BUY" else "매도"

        text = (
            f"{icon} <b>거래 체결</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"종목: <b>{symbol}</b>\n"
            f"방향: {side_kr}\n"
            f"수량: {quantity:,}주\n"
            f"가격: {price:,.2f}\n"
            f"금액: {total_value:,.0f}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"⏰ {datetime.now().strftime('%H:%M:%S')}"
        )
        return self.send_message(text)
