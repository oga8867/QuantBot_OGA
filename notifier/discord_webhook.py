"""
=============================================================================
notifier/discord_webhook.py - 디스코드 웹훅 알림
=============================================================================

디스코드 채널 웹훅을 통해 매매 신호, 체결, 리스크 경고를 전송합니다.

디스코드 웹훅이란?
- 특정 채널에 외부에서 메시지를 보낼 수 있는 URL입니다.
- 봇 계정 없이 단방향 알림만 가능합니다. (단순하고 빠른 설정)
- Embed(임베드)를 사용하면 카드 형태로 예쁘게 보낼 수 있습니다.

설정 방법:
1. 디스코드 서버 → 채널 설정 (톱니바퀴) → 연동 → 웹훅
2. "새 웹훅" 클릭 → 이름 설정 → "웹훅 URL 복사"
3. .env 파일에 DISCORD_WEBHOOK_URL=복사한URL 저장
   또는 대시보드 Settings에서 입력

Embed 구조 (참고):
    {
        "embeds": [{
            "title": "제목",
            "description": "내용",
            "color": 0x00FF00,       ← 16진수 색상
            "fields": [
                {"name": "필드명", "value": "값", "inline": true}
            ],
            "footer": {"text": "하단 텍스트"},
            "timestamp": "2024-01-01T00:00:00Z"
        }]
    }

색상 코드:
    - 매수 (초록): 0x00D166
    - 매도 (빨강): 0xED4245
    - 경고 (노랑): 0xFEE75C
    - 정보 (파랑): 0x0070D1
    - 위험 (빨강 강조): 0xFF0000
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


# ─── 색상 상수 (디스코드 Embed용 10진수) ─────────────────────────────────
COLOR_BUY = 0x00D166      # 초록 (매수 신호)
COLOR_SELL = 0xED4245     # 빨강 (매도 신호)
COLOR_HOLD = 0x99AAB5     # 회색 (관망)
COLOR_WARNING = 0xFEE75C  # 노랑 (주의)
COLOR_CRITICAL = 0xFF0000 # 빨강 강조 (위험)
COLOR_INFO = 0x0070D1     # PlayStation Blue (정보)
COLOR_TRADE = 0x5865F2    # 디스코드 퍼플 (체결)


class DiscordNotifier:
    """
    디스코드 웹훅 알림 전송기

    텔레그램 TelegramNotifier와 동일한 메서드를 제공합니다.
    환경변수(DISCORD_WEBHOOK_URL)가 없으면 자동 비활성화됩니다.

    사용법:
        notifier = DiscordNotifier()
        notifier.send_signal("AAPL", "BUY", 0.85, ["RSI 과매도", "MACD 골든크로스"])
        notifier.send_daily_report(results)
        notifier.send_risk_alert("MDD 10% 초과!")
    """

    def __init__(self, webhook_url: Optional[str] = None):
        """
        Parameters:
            webhook_url: 디스코드 웹훅 URL
                         None이면 환경변수 DISCORD_WEBHOOK_URL에서 로드
        """
        self.webhook_url = webhook_url or os.environ.get("DISCORD_WEBHOOK_URL", "")
        self.enabled = bool(self.webhook_url and REQUESTS_AVAILABLE)

        # 웹훅 봇 이름과 아바타 (디스코드에서 표시)
        self.bot_name = "Quant Bot"
        self.avatar_url = ""  # 원하면 이미지 URL 설정 가능

        if not self.enabled:
            pass  # 선택적 기능 — 미설정이면 조용히 비활성화

    def send_message(self, content: str) -> bool:
        """
        단순 텍스트 메시지 전송

        Parameters:
            content: 텍스트 내용 (마크다운 지원)

        Returns:
            성공 여부
        """
        if not self.enabled:
            print(f"[Discord OFF] {content[:100]}...")
            return False

        payload = {
            "username": self.bot_name,
            "content": content,
        }
        if self.avatar_url:
            payload["avatar_url"] = self.avatar_url

        return self._send(payload)

    def send_embed(self, embed: dict) -> bool:
        """
        Embed(임베드 카드) 형식 전송

        Parameters:
            embed: 디스코드 Embed 딕셔너리
                   (title, description, color, fields, footer, timestamp 등)

        Returns:
            성공 여부
        """
        if not self.enabled:
            print(f"[Discord OFF] Embed: {embed.get('title', '?')}")
            return False

        payload = {
            "username": self.bot_name,
            "embeds": [embed],
        }
        if self.avatar_url:
            payload["avatar_url"] = self.avatar_url

        return self._send(payload)

    def send_signal(
        self,
        symbol: str,
        action: str,
        confidence: float,
        reasons: List[str],
        price: Optional[float] = None
    ) -> bool:
        """
        매매 신호 알림 (Embed 카드)

        Parameters:
            symbol: 종목 코드 (예: "AAPL", "005930.KS")
            action: "BUY" / "SELL" / "HOLD"
            confidence: 신뢰도 (0.0 ~ 1.0)
            reasons: 신호 발생 이유 리스트
            price: 현재가 (선택)

        Returns:
            전송 성공 여부
        """
        # 신호별 색상과 이모지
        color_map = {"BUY": COLOR_BUY, "SELL": COLOR_SELL, "HOLD": COLOR_HOLD}
        emoji_map = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪"}
        label_map = {"BUY": "매수", "SELL": "매도", "HOLD": "관망"}

        color = color_map.get(action, COLOR_INFO)
        emoji = emoji_map.get(action, "❓")
        label = label_map.get(action, action)

        # Embed 필드 구성
        fields = [
            {"name": "신호", "value": f"{emoji} **{label}**", "inline": True},
            {"name": "신뢰도", "value": f"{confidence:.0%}", "inline": True},
        ]

        if price:
            fields.append({"name": "현재가", "value": f"{price:,.2f}", "inline": True})

        # 근거를 한 필드에 합침
        if reasons:
            reasons_text = "\n".join(f"• {r}" for r in reasons[:5])
            fields.append({"name": "📋 분석 근거", "value": reasons_text, "inline": False})

        embed = {
            "title": f"📊 매매 신호 — {symbol}",
            "color": color,
            "fields": fields,
            "footer": {"text": "Quant Bot Signal"},
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

        return self.send_embed(embed)

    def send_daily_report(self, results: List[Dict]) -> bool:
        """
        일일 분석 리포트 (Embed 카드)

        Parameters:
            results: 분석 결과 리스트
                     각 항목: {symbol, signal, strength, price, ...}

        Returns:
            전송 성공 여부
        """
        if not results:
            return False

        # 종목별 한 줄 요약 생성
        emoji_map = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪"}
        lines = []
        for r in results[:20]:  # 최대 20개 (Embed 글자 제한)
            if r.get("status") == "success" or r.get("signal"):
                emoji = emoji_map.get(r.get("signal", "").upper(), "❓")
                price_str = f"{r.get('price', 0):,.0f}" if r.get("price") else "?"
                strength = r.get("strength", 0)
                lines.append(
                    f"{emoji} **{r['symbol']}** — "
                    f"{price_str} (강도: {strength:.0%})"
                )
            else:
                lines.append(f"❌ {r.get('symbol', '?')}: 분석 실패")

        # 요약 통계
        buy_count = sum(1 for r in results if r.get("signal", "").upper() == "BUY")
        sell_count = sum(1 for r in results if r.get("signal", "").upper() == "SELL")
        summary = f"매수 {buy_count} | 매도 {sell_count} | 전체 {len(results)}종목"

        embed = {
            "title": "📈 일일 분석 리포트",
            "description": "\n".join(lines),
            "color": COLOR_INFO,
            "fields": [
                {"name": "요약", "value": summary, "inline": False}
            ],
            "footer": {"text": "Quant Bot Daily Report"},
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

        return self.send_embed(embed)

    def send_risk_alert(self, message: str, level: str = "WARNING") -> bool:
        """
        리스크 경고 알림

        Parameters:
            message: 경고 내용
            level: "WARNING" (주의) 또는 "CRITICAL" (위험)

        Returns:
            전송 성공 여부
        """
        color = COLOR_CRITICAL if level == "CRITICAL" else COLOR_WARNING
        emoji = "🚨" if level == "CRITICAL" else "⚠️"

        embed = {
            "title": f"{emoji} 리스크 경고 [{level}]",
            "description": message,
            "color": color,
            "footer": {"text": "Quant Bot Risk Monitor"},
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

        # CRITICAL이면 @everyone 멘션으로 모두에게 알림
        if level == "CRITICAL":
            # Embed와 함께 content도 보냄 (멘션은 content에만 동작)
            payload = {
                "username": self.bot_name,
                "content": "@everyone ⚠️ **긴급 리스크 경고**",
                "embeds": [embed],
            }
            return self._send(payload)

        return self.send_embed(embed)

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
            symbol: 종목 코드
            side: "BUY" / "SELL"
            quantity: 체결 수량
            price: 체결가
            total_value: 총 거래 금액

        Returns:
            전송 성공 여부
        """
        emoji = "🛒" if side == "BUY" else "💸"
        side_kr = "매수" if side == "BUY" else "매도"
        color = COLOR_BUY if side == "BUY" else COLOR_SELL

        embed = {
            "title": f"{emoji} 거래 체결 — {symbol}",
            "color": color,
            "fields": [
                {"name": "방향", "value": f"**{side_kr}**", "inline": True},
                {"name": "수량", "value": f"{quantity:,}주", "inline": True},
                {"name": "체결가", "value": f"{price:,.2f}", "inline": True},
                {"name": "거래금액", "value": f"{total_value:,.0f}", "inline": True},
            ],
            "footer": {"text": "Quant Bot Execution"},
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

        return self.send_embed(embed)

    def send_bot_status(self, running: bool, mode: str, equity: float, pnl_pct: float) -> bool:
        """
        봇 상태 변경 알림 (시작/종료)

        Parameters:
            running: True=시작, False=종료
            mode: "paper" / "live"
            equity: 현재 총 자산
            pnl_pct: 수익률 (%)
        """
        if running:
            title = "🤖 퀀트봇 시작"
            color = COLOR_INFO
        else:
            title = "🛑 퀀트봇 종료"
            color = COLOR_HOLD

        mode_str = "모의매매" if mode == "paper" else "**실거래**"
        pnl_str = f"+{pnl_pct:.2f}%" if pnl_pct >= 0 else f"{pnl_pct:.2f}%"

        embed = {
            "title": title,
            "color": color,
            "fields": [
                {"name": "모드", "value": mode_str, "inline": True},
                {"name": "총 자산", "value": f"{equity:,.0f}", "inline": True},
                {"name": "수익률", "value": pnl_str, "inline": True},
            ],
            "footer": {"text": "Quant Bot"},
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

        return self.send_embed(embed)

    def _send(self, payload: dict) -> bool:
        """
        웹훅으로 payload 전송 (내부 메서드)

        디스코드 웹훅 API:
        - POST https://discord.com/api/webhooks/{id}/{token}
        - Content-Type: application/json
        - 성공 시 204 No Content 반환

        Rate Limit:
        - 채널당 5회/2초, 30회/60초
        - 429 응답 시 Retry-After 헤더만큼 대기
        """
        try:
            response = requests.post(
                self.webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=10
            )

            # 204 = 성공 (No Content), 200도 가끔 옴
            if response.status_code in (200, 204):
                return True

            # Rate limit 걸렸을 때
            if response.status_code == 429:
                retry_after = response.json().get("retry_after", 1)
                print(f"[Discord] Rate limit - {retry_after}초 후 재시도 필요")
                return False

            print(f"[Discord 전송 실패] HTTP {response.status_code}: "
                  f"{response.text[:200]}")
            return False

        except Exception as e:
            print(f"[Discord 전송 오류] {e}")
            return False

    def test_connection(self) -> bool:
        """
        웹훅 연결 테스트 (설정 확인용)

        성공 시 테스트 메시지를 채널에 전송합니다.
        대시보드에서 "테스트" 버튼 클릭 시 호출됩니다.
        """
        embed = {
            "title": "✅ Quant Bot 연결 테스트",
            "description": "디스코드 알림이 정상적으로 연결되었습니다!",
            "color": COLOR_BUY,
            "fields": [
                {"name": "상태", "value": "연결 성공", "inline": True},
                {"name": "시각", "value": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "inline": True},
            ],
            "footer": {"text": "이 메시지가 보이면 설정이 완료된 것입니다"},
        }
        return self.send_embed(embed)
