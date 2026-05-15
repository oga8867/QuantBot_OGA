"""
=============================================================================
notifier/discord_bot.py - 디스코드 봇 (양방향 명령)
=============================================================================

디스코드 웹훅(단방향 알림)과 달리, 이 모듈은 Bot Application을 사용하여
디스코드 채널에서 슬래시 명령어로 퀀트봇을 제어할 수 있게 합니다.

지원 명령어:
    /상태      - 봇 실행 상태, 자산, 수익률 확인
    /포지션    - 보유 종목 전체 현황
    /거래내역  - 최근 매매 이력 (기본 10건)
    /시작      - 봇 시작
    /중지      - 봇 중지
    /분석      - 특정 종목 즉석 분석
    /보고서    - 일일 보고서 요약
    /도움말    - 명령어 목록

설정 방법:
    1. https://discord.com/developers/applications 에서 New Application
    2. Bot 탭 → Reset Token → 토큰 복사
    3. Bot 탭 → MESSAGE CONTENT INTENT 활성화
    4. OAuth2 → URL Generator → bot + applications.commands 선택
       → Send Messages, Embed Links, Read Message History 권한 체크
    5. 생성된 URL로 서버에 봇 초대
    6. 대시보드 Settings에서 봇 토큰 입력

아키텍처:
    - 대시보드(Flask)와 같은 프로세스에서 별도 스레드로 실행
    - app.py의 전역 변수(bot_instance, bot_status, current_settings)에
      직접 접근하여 상태 조회 / 제어
    - discord.py의 Client를 asyncio 이벤트 루프에서 실행
=============================================================================
"""

import asyncio
import threading
import logging
from typing import Optional, Callable, Any
from datetime import datetime

logger = logging.getLogger("discord_bot")

# discord.py 임포트 시도
try:
    import discord
    from discord import app_commands
    DISCORD_PY_AVAILABLE = True
except ImportError:
    DISCORD_PY_AVAILABLE = False
    logger.info("[Discord Bot] discord.py 미설치 — pip install discord.py")


# ─── 색상 상수 ──────────────────────────────────────────────────────────
COLOR_SUCCESS = 0x00D166   # 초록
COLOR_DANGER = 0xED4245    # 빨강
COLOR_WARNING = 0xFEE75C   # 노랑
COLOR_INFO = 0x0070D1      # 파랑
COLOR_NEUTRAL = 0x99AAB5   # 회색


class QuantBotDiscord:
    """
    디스코드 봇 — 퀀트봇 원격 제어 및 모니터링

    app.py에서 생성하며, 대시보드의 전역 상태에 접근하는
    콜백 함수들을 주입받습니다.

    Usage:
        bot = QuantBotDiscord(token="BOT_TOKEN")
        bot.set_callbacks(
            get_status=lambda: bot_status,
            get_settings=lambda: current_settings,
            get_bot_instance=lambda: bot_instance,
            start_bot=start_bot_func,
            stop_bot=stop_bot_func,
        )
        bot.start()   # 별도 스레드에서 실행
        bot.stop()    # 종료
    """

    def __init__(self, token: str, allowed_channel_id: Optional[int] = None):
        """
        Parameters:
            token: 디스코드 봇 토큰
            allowed_channel_id: 명령을 허용할 채널 ID (None이면 모든 채널)
        """
        if not DISCORD_PY_AVAILABLE:
            raise ImportError(
                "discord.py가 설치되지 않았습니다. "
                "pip install discord.py 를 실행하세요."
            )

        self.token = token
        self.allowed_channel_id = allowed_channel_id
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._running = False

        # ── 콜백 함수들 (app.py에서 주입) ──
        self._get_status: Optional[Callable] = None
        self._get_settings: Optional[Callable] = None
        self._get_bot_instance: Optional[Callable] = None
        self._start_bot: Optional[Callable] = None
        self._stop_bot: Optional[Callable] = None
        self._get_db: Optional[Callable] = None

        # ── discord.py 클라이언트 설정 ──
        intents = discord.Intents.default()
        intents.message_content = True

        self.client = discord.Client(intents=intents)
        self.tree = app_commands.CommandTree(self.client)

        # 이벤트 핸들러 등록
        self._register_events()
        self._register_commands()

    def set_callbacks(
        self,
        get_status: Callable,
        get_settings: Callable,
        get_bot_instance: Callable,
        start_bot: Callable,
        stop_bot: Callable,
        get_db: Optional[Callable] = None,
    ):
        """
        대시보드 전역 상태에 접근하는 콜백 함수 주입

        Parameters:
            get_status: () -> dict  (bot_status 반환)
            get_settings: () -> dict  (current_settings 반환)
            get_bot_instance: () -> QuantBot or None
            start_bot: () -> dict  (봇 시작, 결과 반환)
            stop_bot: () -> dict  (봇 중지, 결과 반환)
            get_db: () -> DatabaseManager  (DB 접근)
        """
        self._get_status = get_status
        self._get_settings = get_settings
        self._get_bot_instance = get_bot_instance
        self._start_bot = start_bot
        self._stop_bot = stop_bot
        self._get_db = get_db

    # ═══════════════════════════════════════════════════════════════════
    # 이벤트 핸들러
    # ═══════════════════════════════════════════════════════════════════

    def _register_events(self):
        """디스코드 이벤트 핸들러 등록"""

        @self.client.event
        async def on_ready():
            """봇이 디스코드에 연결되었을 때"""
            logger.info(
                f"[Discord Bot] 연결 완료: {self.client.user} "
                f"(서버 {len(self.client.guilds)}개)"
            )
            # 슬래시 명령어를 디스코드에 동기화
            try:
                synced = await self.tree.sync()
                logger.info(f"[Discord Bot] 슬래시 명령어 {len(synced)}개 등록 완료")
            except Exception as e:
                logger.error(f"[Discord Bot] 명령어 동기화 실패: {e}")

    # ═══════════════════════════════════════════════════════════════════
    # 슬래시 명령어 등록
    # ═══════════════════════════════════════════════════════════════════

    def _register_commands(self):
        """모든 슬래시 명령어 등록"""

        # ── 채널 확인 헬퍼 ──
        async def _check_channel(interaction: discord.Interaction) -> bool:
            """허용된 채널에서만 명령 실행"""
            if self.allowed_channel_id and interaction.channel_id != self.allowed_channel_id:
                await interaction.response.send_message(
                    "⚠️ 이 채널에서는 사용할 수 없습니다.", ephemeral=True
                )
                return False
            return True

        # ──────────────────────────────────────────────────────────────
        # /상태 — 봇 상태 조회
        # ──────────────────────────────────────────────────────────────
        @self.tree.command(name="상태", description="퀀트봇 실행 상태, 자산, 수익률 확인")
        async def cmd_status(interaction: discord.Interaction):
            if not await _check_channel(interaction):
                return

            status = self._get_status() if self._get_status else {}
            settings = self._get_settings() if self._get_settings else {}

            running = status.get("running", False)
            live = status.get("live", False)
            equity = status.get("total_equity", 0)
            cash = status.get("cash", 0)
            pnl = status.get("total_pnl", 0)
            trades = status.get("total_trades", 0)
            positions = status.get("positions", {})

            # 상태 이모지
            if running:
                status_text = "🔴 **실거래 중**" if live else "🟢 **실행 중** (모의매매)"
                color = COLOR_DANGER if live else COLOR_SUCCESS
            else:
                status_text = "⏸️ **중지됨**"
                color = COLOR_NEUTRAL

            # 수익률 색상
            pnl_str = f"+{pnl:.2f}%" if pnl >= 0 else f"{pnl:.2f}%"
            pnl_emoji = "📈" if pnl >= 0 else "📉"

            embed = discord.Embed(
                title="🤖 퀀트봇 상태",
                color=color,
                timestamp=datetime.utcnow(),
            )
            embed.add_field(name="상태", value=status_text, inline=False)
            embed.add_field(name="💰 총 자산", value=f"₩{equity:,.0f}", inline=True)
            embed.add_field(name="💵 현금", value=f"₩{cash:,.0f}", inline=True)
            embed.add_field(
                name=f"{pnl_emoji} 수익률", value=pnl_str, inline=True
            )
            embed.add_field(name="📊 총 거래", value=f"{trades}건", inline=True)
            embed.add_field(
                name="📋 보유 종목", value=f"{len(positions)}개", inline=True
            )

            # 분석 간격
            interval = settings.get("analysis_interval", "60")
            embed.add_field(name="⏱️ 분석 간격", value=f"{interval}분", inline=True)

            # 시작 시간
            started = status.get("started_at", "")
            if started and running:
                embed.set_footer(text=f"시작: {started[:19]}")

            await interaction.response.send_message(embed=embed)

        # ──────────────────────────────────────────────────────────────
        # /포지션 — 보유 종목 조회
        # ──────────────────────────────────────────────────────────────
        @self.tree.command(name="포지션", description="보유 종목 전체 현황")
        async def cmd_positions(interaction: discord.Interaction):
            if not await _check_channel(interaction):
                return

            status = self._get_status() if self._get_status else {}
            positions = status.get("positions", {})

            if not positions:
                embed = discord.Embed(
                    title="📋 보유 포지션",
                    description="보유 중인 종목이 없습니다.",
                    color=COLOR_NEUTRAL,
                )
                await interaction.response.send_message(embed=embed)
                return

            embed = discord.Embed(
                title=f"📋 보유 포지션 ({len(positions)}개)",
                color=COLOR_INFO,
                timestamp=datetime.utcnow(),
            )

            total_value = 0
            total_pnl = 0

            for symbol, pos in positions.items():
                name = pos.get("name", symbol)
                shares = pos.get("shares", 0)
                avg_price = pos.get("avg_price", 0)
                current = pos.get("current_price", 0)
                pnl_val = pos.get("pnl", 0)
                pnl_pct = pos.get("pnl_pct", 0)
                pos_type = pos.get("position_type", "")
                currency = pos.get("currency", "KRW")
                market_val = pos.get("market_value_krw", shares * current)

                total_value += market_val
                total_pnl += pnl_val

                # 수익률 이모지
                pnl_emoji = "🟢" if pnl_val >= 0 else "🔴"
                pnl_str = f"+{pnl_pct:.1f}%" if pnl_pct >= 0 else f"{pnl_pct:.1f}%"
                cur_sym = "$" if currency == "USD" else "₩"

                value_text = (
                    f"{shares}주 @ {cur_sym}{avg_price:,.2f}\n"
                    f"현재가: {cur_sym}{current:,.2f}\n"
                    f"{pnl_emoji} 손익: {pnl_str} (₩{pnl_val:,.0f})"
                )
                if pos_type:
                    value_text = f"[{pos_type}] " + value_text

                # Embed 필드 제한(25개)에 맞춰 최대 15개까지만
                if len(embed.fields) < 15:
                    embed.add_field(
                        name=f"{'🇺🇸' if currency == 'USD' else '🇰🇷'} {name}",
                        value=value_text,
                        inline=True,
                    )

            embed.set_footer(
                text=f"총 평가: ₩{total_value:,.0f} | "
                     f"총 손익: ₩{total_pnl:,.0f}"
            )

            if len(positions) > 15:
                embed.description = f"(상위 15개 표시, 전체 {len(positions)}개)"

            await interaction.response.send_message(embed=embed)

        # ──────────────────────────────────────────────────────────────
        # /거래내역 — 최근 매매 이력
        # ──────────────────────────────────────────────────────────────
        @self.tree.command(name="거래내역", description="최근 매매 이력 조회")
        @app_commands.describe(건수="조회할 거래 수 (기본 10)")
        async def cmd_trades(
            interaction: discord.Interaction, 건수: int = 10
        ):
            if not await _check_channel(interaction):
                return

            건수 = min(건수, 20)  # 최대 20건

            trades = []
            if self._get_db:
                try:
                    db = self._get_db()
                    trades = db.get_trades(limit=건수)
                except Exception as e:
                    logger.warning(f"[Discord Bot] 거래내역 조회 실패: {e}")

            if not trades:
                embed = discord.Embed(
                    title="📜 거래 내역",
                    description="거래 기록이 없습니다.",
                    color=COLOR_NEUTRAL,
                )
                await interaction.response.send_message(embed=embed)
                return

            embed = discord.Embed(
                title=f"📜 최근 거래 내역 ({len(trades)}건)",
                color=COLOR_INFO,
                timestamp=datetime.utcnow(),
            )

            # 오늘 손익 합계 + 개별 거래 손익 표시
            total_pnl_today = 0.0
            today_str = datetime.now().strftime("%Y-%m-%d")
            lines = []
            for t in trades:
                side = t.get("side", "").upper()
                symbol = t.get("symbol", "?")
                qty = t.get("quantity", 0)
                price = t.get("price", 0)
                ts = t.get("timestamp", "")[:16]  # 초 단위까지
                pnl = float(t.get("pnl", 0) or 0)
                emoji = "🛒" if side == "BUY" else "💸"
                side_kr = "매수" if side == "BUY" else "매도"

                # 오늘 거래면 손익 합산
                if side == "SELL" and ts.startswith(today_str):
                    total_pnl_today += pnl

                # 손익 표시 (매도만)
                pnl_str = ""
                if side == "SELL" and abs(pnl) > 0.01:
                    if pnl > 0:
                        pnl_str = f"  🟢 **+₩{int(pnl):,}**"
                    else:
                        pnl_str = f"  🔴 **₩{int(pnl):,}**"

                lines.append(
                    f"{emoji} **{symbol}** {side_kr} {qty}주 "
                    f"@ {price:,.2f}{pnl_str}  `{ts}`"
                )

            # 오늘 손익 헤더 추가
            header = ""
            if total_pnl_today != 0:
                pnl_emoji = "🟢" if total_pnl_today > 0 else "🔴"
                pnl_sign = "+" if total_pnl_today > 0 else ""
                header = (
                    f"{pnl_emoji} **오늘 실현 손익: "
                    f"{pnl_sign}₩{int(total_pnl_today):,}**\n"
                    f"━━━━━━━━━━━━━━━\n"
                )

            # Embed description 길이 제한(4096)
            embed.description = header + "\n".join(lines[:20])

            await interaction.response.send_message(embed=embed)

        # ──────────────────────────────────────────────────────────────
        # /시작 — 봇 시작
        # ──────────────────────────────────────────────────────────────
        @self.tree.command(name="시작", description="퀀트봇 시작")
        async def cmd_start(interaction: discord.Interaction):
            if not await _check_channel(interaction):
                return

            status = self._get_status() if self._get_status else {}
            if status.get("running"):
                await interaction.response.send_message(
                    "⚠️ 봇이 이미 실행 중입니다.", ephemeral=True
                )
                return

            await interaction.response.defer()  # 시간이 걸릴 수 있음

            try:
                if self._start_bot:
                    result = self._start_bot()
                    if result.get("success"):
                        embed = discord.Embed(
                            title="🟢 봇 시작됨",
                            description="퀀트봇이 시작되었습니다. 분석을 시작합니다.",
                            color=COLOR_SUCCESS,
                            timestamp=datetime.utcnow(),
                        )
                        await interaction.followup.send(embed=embed)
                    else:
                        error = result.get("error", "알 수 없는 오류")
                        await interaction.followup.send(f"❌ 시작 실패: {error}")
                else:
                    await interaction.followup.send("❌ 봇 시작 기능이 연결되지 않았습니다.")
            except Exception as e:
                await interaction.followup.send(f"❌ 시작 오류: {str(e)[:200]}")

        # ──────────────────────────────────────────────────────────────
        # /중지 — 봇 중지
        # ──────────────────────────────────────────────────────────────
        @self.tree.command(name="중지", description="퀀트봇 중지")
        async def cmd_stop(interaction: discord.Interaction):
            if not await _check_channel(interaction):
                return

            status = self._get_status() if self._get_status else {}
            if not status.get("running"):
                await interaction.response.send_message(
                    "⚠️ 봇이 이미 중지되어 있습니다.", ephemeral=True
                )
                return

            try:
                if self._stop_bot:
                    result = self._stop_bot()
                    if result.get("success"):
                        embed = discord.Embed(
                            title="🛑 봇 중지됨",
                            description="퀀트봇이 안전하게 중지되었습니다.",
                            color=COLOR_DANGER,
                            timestamp=datetime.utcnow(),
                        )
                        await interaction.response.send_message(embed=embed)
                    else:
                        error = result.get("error", "알 수 없는 오류")
                        await interaction.response.send_message(
                            f"❌ 중지 실패: {error}"
                        )
                else:
                    await interaction.response.send_message(
                        "❌ 봇 중지 기능이 연결되지 않았습니다."
                    )
            except Exception as e:
                await interaction.response.send_message(
                    f"❌ 중지 오류: {str(e)[:200]}"
                )

        # ──────────────────────────────────────────────────────────────
        # /분석 — 특정 종목 분석
        # ──────────────────────────────────────────────────────────────
        @self.tree.command(name="분석", description="특정 종목 즉석 분석")
        @app_commands.describe(종목="종목 코드 (예: AAPL, 005930.KS)")
        async def cmd_analyze(interaction: discord.Interaction, 종목: str):
            if not await _check_channel(interaction):
                return

            await interaction.response.defer()  # 분석에 시간 소요

            try:
                # 분석 실행
                result = self._run_quick_analysis(종목.strip().upper())

                if result.get("error"):
                    await interaction.followup.send(
                        f"❌ 분석 실패: {result['error']}"
                    )
                    return

                signal = result.get("signal", "HOLD")
                strength = result.get("strength", 0)
                price = result.get("price", 0)
                reasons = result.get("reasons", [])
                name = result.get("name", 종목)

                # 신호별 설정
                signal_config = {
                    "BUY": ("🟢", "매수", COLOR_SUCCESS),
                    "SELL": ("🔴", "매도", COLOR_DANGER),
                    "HOLD": ("⚪", "관망", COLOR_NEUTRAL),
                }
                emoji, label, color = signal_config.get(
                    signal, ("❓", signal, COLOR_NEUTRAL)
                )

                embed = discord.Embed(
                    title=f"📊 분석 결과 — {name}",
                    color=color,
                    timestamp=datetime.utcnow(),
                )
                embed.add_field(
                    name="신호", value=f"{emoji} **{label}**", inline=True
                )
                embed.add_field(
                    name="강도", value=f"{strength:.0%}", inline=True
                )
                embed.add_field(
                    name="현재가", value=f"{price:,.2f}", inline=True
                )

                if reasons:
                    reasons_text = "\n".join(f"• {r}" for r in reasons[:8])
                    embed.add_field(
                        name="📋 분석 근거",
                        value=reasons_text,
                        inline=False,
                    )

                # 기술 지표 추가
                rsi = result.get("rsi")
                macd = result.get("macd_signal")
                if rsi is not None:
                    indicators = f"RSI: {rsi:.1f}"
                    if macd:
                        indicators += f" | MACD: {macd}"
                    embed.add_field(
                        name="📉 주요 지표", value=indicators, inline=False
                    )

                await interaction.followup.send(embed=embed)

            except Exception as e:
                await interaction.followup.send(
                    f"❌ 분석 오류: {str(e)[:200]}"
                )

        # ──────────────────────────────────────────────────────────────
        # /보고서 — 요약 보고서
        # ──────────────────────────────────────────────────────────────
        @self.tree.command(name="보고서", description="포트폴리오 요약 보고서")
        async def cmd_report(interaction: discord.Interaction):
            if not await _check_channel(interaction):
                return

            status = self._get_status() if self._get_status else {}
            positions = status.get("positions", {})
            settings = self._get_settings() if self._get_settings else {}

            equity = status.get("total_equity", 0)
            cash = status.get("cash", 0)
            capital = settings.get("capital", 10_000_000)
            pnl = status.get("total_pnl", 0)
            total_trades = status.get("total_trades", 0)

            # 포지션별 요약
            kr_positions = []
            us_positions = []
            total_pos_value = 0

            for sym, pos in positions.items():
                name = pos.get("name", sym)
                pnl_pct = pos.get("pnl_pct", 0)
                market_val = pos.get("market_value_krw", 0)
                currency = pos.get("currency", "KRW")
                total_pos_value += market_val

                emoji = "🟢" if pnl_pct >= 0 else "🔴"
                pnl_s = f"+{pnl_pct:.1f}%" if pnl_pct >= 0 else f"{pnl_pct:.1f}%"
                line = f"{emoji} {name}: {pnl_s} (₩{market_val:,.0f})"

                if currency == "USD":
                    us_positions.append(line)
                else:
                    kr_positions.append(line)

            embed = discord.Embed(
                title="📈 포트폴리오 보고서",
                color=COLOR_SUCCESS if pnl >= 0 else COLOR_DANGER,
                timestamp=datetime.utcnow(),
            )

            # 자산 개요
            pnl_str = f"+{pnl:.2f}%" if pnl >= 0 else f"{pnl:.2f}%"
            invest_ratio = (total_pos_value / equity * 100) if equity > 0 else 0

            overview = (
                f"💰 총 자산: ₩{equity:,.0f}\n"
                f"💵 현금: ₩{cash:,.0f}\n"
                f"📊 수익률: {pnl_str}\n"
                f"📋 투자비중: {invest_ratio:.0f}%\n"
                f"🔄 총 거래: {total_trades}건"
            )
            embed.add_field(name="자산 개요", value=overview, inline=False)

            # 한국 포지션
            if kr_positions:
                embed.add_field(
                    name=f"🇰🇷 한국 ({len(kr_positions)})",
                    value="\n".join(kr_positions[:10]),
                    inline=False,
                )

            # 미국 포지션
            if us_positions:
                embed.add_field(
                    name=f"🇺🇸 미국 ({len(us_positions)})",
                    value="\n".join(us_positions[:10]),
                    inline=False,
                )

            if not positions:
                embed.add_field(
                    name="포지션",
                    value="보유 중인 종목이 없습니다.",
                    inline=False,
                )

            # 수수료 정보
            commission = status.get("commission", {})
            if commission:
                fees = commission.get("total_fees_paid", 0)
                embed.set_footer(text=f"총 수수료: ₩{fees:,.0f}")

            await interaction.response.send_message(embed=embed)

        # ──────────────────────────────────────────────────────────────
        # /도움말 — 명령어 목록
        # ──────────────────────────────────────────────────────────────
        @self.tree.command(name="도움말", description="퀀트봇 명령어 목록")
        async def cmd_help(interaction: discord.Interaction):
            embed = discord.Embed(
                title="🤖 퀀트봇 명령어",
                description="디스코드에서 퀀트봇을 제어합니다.",
                color=COLOR_INFO,
            )
            commands_text = (
                "**/상태** — 봇 실행 상태, 자산, 수익률\n"
                "**/포지션** — 보유 종목 전체 현황\n"
                "**/거래내역** [건수] — 최근 매매 이력\n"
                "**/시작** — 퀀트봇 시작\n"
                "**/중지** — 퀀트봇 중지\n"
                "**/분석** <종목코드> — 특정 종목 즉석 분석\n"
                "**/보고서** — 포트폴리오 요약 보고서\n"
                "**/도움말** — 이 메시지"
            )
            embed.add_field(name="명령어", value=commands_text, inline=False)

            embed.set_footer(
                text="💡 종목코드 예시: AAPL, NVDA, 005930.KS, 035720.KS"
            )

            await interaction.response.send_message(embed=embed)

    # ═══════════════════════════════════════════════════════════════════
    # 분석 헬퍼 (별도 스레드에서 동기 실행)
    # ═══════════════════════════════════════════════════════════════════

    def _run_quick_analysis(self, symbol: str) -> dict:
        """
        종목 즉석 분석 (동기)

        discord 명령어 핸들러에서 호출됩니다.
        기존 대시보드의 분석 로직과 동일한 흐름을 사용합니다.
        """
        try:
            from utils.market import detect_market, is_us_stock
            market = detect_market(symbol)

            # 데이터 수집
            if market == "US":
                from collectors.price_us import PriceCollectorUS
                collector = PriceCollectorUS()
            else:
                from collectors.price_kr import PriceCollectorKR
                collector = PriceCollectorKR()

            df = collector.get_daily(symbol, period=100)
            if df is None or df.empty:
                return {"error": f"{symbol}: 데이터 수집 실패"}

            # 기술 분석
            from analyzers.technical import TechnicalAnalyzer
            from config.settings import TechnicalConfig
            analyzer = TechnicalAnalyzer(TechnicalConfig())
            df_analyzed = analyzer.calculate_all(df)
            signal = analyzer.generate_signal(df_analyzed)

            # 종목명
            name = symbol
            try:
                if market == "KR":
                    import pykrx.stock as stock
                    pure = symbol.replace(".KS", "").replace(".KQ", "")
                    name = stock.get_market_ticker_name(pure) or symbol
                    name = f"{name} ({pure})"
                else:
                    import yfinance as yf
                    info = yf.Ticker(symbol).info or {}
                    name = info.get("shortName", symbol)
            except Exception:
                pass

            # RSI
            rsi = None
            if "RSI" in df_analyzed.columns:
                rsi_val = df_analyzed["RSI"].iloc[-1]
                if rsi_val is not None:
                    rsi = float(rsi_val)

            return {
                "signal": signal.signal,
                "strength": signal.strength,
                "price": float(df_analyzed["Close"].iloc[-1]),
                "reasons": signal.reasons if hasattr(signal, "reasons") else [],
                "name": name,
                "rsi": rsi,
            }

        except Exception as e:
            return {"error": str(e)[:200]}

    # ═══════════════════════════════════════════════════════════════════
    # 시작 / 중지
    # ═══════════════════════════════════════════════════════════════════

    def start(self):
        """별도 스레드에서 디스코드 봇 실행"""
        if self._running:
            logger.warning("[Discord Bot] 이미 실행 중")
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._run_in_thread, daemon=True, name="discord-bot"
        )
        self._thread.start()
        logger.info("[Discord Bot] 백그라운드 스레드 시작")

    def _run_in_thread(self):
        """스레드 내에서 asyncio 이벤트 루프 생성 + 봇 실행"""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        try:
            self._loop.run_until_complete(self.client.start(self.token))
        except Exception as e:
            logger.error(f"[Discord Bot] 실행 오류: {e}")
        finally:
            self._running = False
            logger.info("[Discord Bot] 스레드 종료")

    def stop(self):
        """봇 안전 종료"""
        if not self._running:
            return

        logger.info("[Discord Bot] 종료 요청...")
        self._running = False

        if self._loop and self.client:
            # asyncio 루프에 종료 예약
            asyncio.run_coroutine_threadsafe(
                self.client.close(), self._loop
            )

    @property
    def is_running(self) -> bool:
        """봇 실행 상태"""
        return self._running and self.client.is_ready()

    @property
    def status_summary(self) -> dict:
        """현재 봇 상태 요약 (대시보드 API용)"""
        return {
            "running": self.is_running,
            "connected": self.client.is_ready() if self._running else False,
            "username": str(self.client.user) if self.client.user else None,
            "guilds": len(self.client.guilds) if self._running and self.client.is_ready() else 0,
        }
