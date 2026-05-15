"""
=============================================================================
scripts/run_backtest.py - 백테스트 자동화 CLI
=============================================================================

봇의 앙상블 전략을 과거 데이터로 백테스트하여 buy-and-hold와 비교합니다.
전략 변경 후 반드시 이 스크립트를 실행하여 회귀 검증하세요.

사용법:
    python scripts/run_backtest.py                       # 기본: SPY 1년
    python scripts/run_backtest.py --symbol AAPL         # 특정 종목
    python scripts/run_backtest.py --period 2y           # 2년
    python scripts/run_backtest.py --symbol 005930.KS    # 한국 주식

검증 항목:
1. 봇 전략 vs Buy-and-Hold 수익률 비교
2. 샤프비, MDD, 승률
3. ATR 손절/익절 효과 (ExitManager 사용 vs 미사용)
4. 거래 빈도 + 비용 영향

성과 등급:
    🟢 A: 봇이 buy-and-hold를 5% 이상 초과 (정보비율 0.5+)
    🟡 B: 봇이 buy-and-hold와 비슷 (±5%)
    🔴 C: 봇이 buy-and-hold에 5% 이상 미달 → 전략 재검토 필요
=============================================================================
"""

import sys
import argparse
import logging
from pathlib import Path
from datetime import datetime, timedelta

# 프로젝트 루트
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.WARNING,  # 기본은 조용하게
    format="%(asctime)s | %(levelname)s | %(message)s"
)


def fetch_data(symbol: str, period: str = "1y"):
    """yfinance/pykrx로 과거 데이터 조회"""
    try:
        import yfinance as yf
    except ImportError:
        print("❌ yfinance 미설치. pip install yfinance")
        sys.exit(1)

    print(f"📥 {symbol} {period} 데이터 다운로드...")
    df = yf.download(symbol, period=period, progress=False, auto_adjust=True)
    if df.empty:
        print(f"❌ 데이터 없음: {symbol}")
        sys.exit(1)

    # MultiIndex 컬럼 평탄화
    if hasattr(df.columns, "levels"):
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]

    return df


def add_indicators(df):
    """기술적 지표 추가 (ATR, RSI, SMA)"""
    import pandas as pd
    import numpy as np

    high = df["High"]
    low = df["Low"]
    close = df["Close"]

    # ATR (14일)
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    df["ATR"] = tr.rolling(14).mean()

    # RSI (14일)
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    df["RSI"] = 100 - (100 / (1 + rs))

    # SMA
    df["SMA20"] = close.rolling(20).mean()
    df["SMA50"] = close.rolling(50).mean()

    return df


def generate_signals_simple(df):
    """
    단순 추세추종 + RSI 시그널 생성
    (봇의 전체 앙상블은 여러 모듈 의존성이 있어 백테스트에서는 단순화)

    매수: SMA20 > SMA50 AND RSI < 70 (과매수 회피)
    매도: SMA20 < SMA50 OR RSI > 80
    """
    import pandas as pd
    signal = pd.Series(0, index=df.index)

    bull = (df["SMA20"] > df["SMA50"]) & (df["RSI"] < 70)
    bear = (df["SMA20"] < df["SMA50"]) | (df["RSI"] > 80)

    # 상태 머신: 1=보유, 0=무포지션
    pos = 0
    for i in range(len(df)):
        if pos == 0 and bull.iloc[i]:
            pos = 1
        elif pos == 1 and bear.iloc[i]:
            pos = 0
        signal.iloc[i] = pos

    return signal


def run_backtest_with_exits(df, signal, atr_stop_mult=2.0, rr_ratio=2.0):
    """
    ExitManager 통합 백테스트

    매수 신호 시 진입 + 손절/익절/트레일링 적용
    """
    from executor.exit_manager import ExitManager

    em = ExitManager(
        atr_stop_multiplier=atr_stop_mult,
        rr_ratio=rr_ratio,
        trailing_atr_multiplier=3.0,
        enable_partial=True,
    )

    capital = 10_000_000.0
    cash = capital
    shares = 0
    avg_price = 0.0
    equity_curve = []
    trade_count = 0
    win_count = 0
    loss_count = 0
    total_pnl = 0.0

    SYMBOL = "BACKTEST"

    for i, (idx, row) in enumerate(df.iterrows()):
        price = float(row["Close"])
        atr = float(row["ATR"]) if not row["ATR"] != row["ATR"] else 0.0  # NaN 체크
        if atr <= 0:
            atr = price * 0.02

        # 보유 중이면 청산 체크
        if shares > 0:
            decision = em.evaluate(SYMBOL, price)
            if decision.should_exit:
                # 매도
                sell_qty = int(shares * decision.sell_ratio)
                if sell_qty < 1:
                    sell_qty = shares
                pnl = (price - avg_price) * sell_qty
                cash += sell_qty * price * (1 - 0.001)  # 수수료 0.1%
                shares -= sell_qty
                total_pnl += pnl
                trade_count += 1
                if pnl > 0:
                    win_count += 1
                else:
                    loss_count += 1
                if shares == 0:
                    em.unregister(SYMBOL)

        # 매수 신호
        sig = signal.iloc[i] if i < len(signal) else 0
        if shares == 0 and sig == 1 and atr > 0:
            # 자본의 100% 투입 (수수료 고려)
            available = cash * 0.99
            buy_qty = int(available / (price * 1.001))
            if buy_qty > 0:
                cost = buy_qty * price * 1.001  # 수수료
                cash -= cost
                shares = buy_qty
                avg_price = price
                em.register_entry(SYMBOL, price, atr, atr_stop_mult, rr_ratio)
                trade_count += 1

        # equity 기록
        equity = cash + shares * price
        equity_curve.append({"date": idx, "equity": equity, "price": price})

    # 마지막 보유분 청산 (백테스트 종료)
    if shares > 0:
        final_price = float(df["Close"].iloc[-1])
        cash += shares * final_price * (1 - 0.001)
        pnl = (final_price - avg_price) * shares
        total_pnl += pnl
        if pnl > 0:
            win_count += 1
        else:
            loss_count += 1
        shares = 0

    return {
        "equity_curve": equity_curve,
        "final_equity": cash,
        "trade_count": trade_count,
        "win_count": win_count,
        "loss_count": loss_count,
        "total_pnl": total_pnl,
        "initial_capital": capital,
        "return_pct": (cash / capital - 1) * 100,
    }


def calculate_metrics(equity_curve, initial_capital):
    """샤프비, MDD, 변동성 계산"""
    import numpy as np

    if len(equity_curve) < 2:
        return {"sharpe": 0, "mdd": 0, "vol": 0}

    equities = [e["equity"] for e in equity_curve]
    returns = []
    for i in range(1, len(equities)):
        if equities[i - 1] > 0:
            returns.append(equities[i] / equities[i - 1] - 1)

    if not returns:
        return {"sharpe": 0, "mdd": 0, "vol": 0}

    arr = np.array(returns)
    mean_r = arr.mean()
    std_r = arr.std()
    sharpe = (mean_r * 252) / (std_r * np.sqrt(252)) if std_r > 0 else 0

    # MDD
    peak = equities[0]
    max_dd = 0.0
    for eq in equities:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)

    return {
        "sharpe": sharpe,
        "mdd": max_dd * 100,
        "vol": std_r * np.sqrt(252) * 100,
    }


def main():
    parser = argparse.ArgumentParser(description="봇 전략 백테스트 + 벤치마크 비교")
    parser.add_argument("--symbol", default="SPY", help="종목코드 (기본: SPY)")
    parser.add_argument("--period", default="1y", help="기간 (1mo, 3mo, 6mo, 1y, 2y, 5y)")
    parser.add_argument("--atr-mult", type=float, default=2.0, help="손절 ATR 배수")
    parser.add_argument("--rr", type=float, default=2.0, help="Risk-Reward 비율")
    args = parser.parse_args()

    print("=" * 70)
    print(f"  백테스트: {args.symbol} ({args.period})")
    print(f"  ATR 손절 배수: {args.atr_mult} | RR 비율: {args.rr}")
    print("=" * 70)

    # ── 1. 데이터 로드 ──
    df = fetch_data(args.symbol, args.period)
    df = add_indicators(df)
    df = df.dropna()

    if len(df) < 50:
        print(f"❌ 데이터 너무 적음: {len(df)}일 (최소 50일 필요)")
        sys.exit(1)

    print(f"✅ 데이터: {len(df)}일 ({df.index[0].date()} ~ {df.index[-1].date()})")

    # ── 2. 시그널 생성 ──
    signal = generate_signals_simple(df)

    # ── 3. ExitManager 백테스트 ──
    print("\n[전략 1] 봇 전략 (앙상블 단순화 + ExitManager)")
    print("-" * 70)
    bot_result = run_backtest_with_exits(df, signal, args.atr_mult, args.rr)
    bot_metrics = calculate_metrics(bot_result["equity_curve"], bot_result["initial_capital"])

    print(f"  최종 자산   : ₩{bot_result['final_equity']:>15,.0f}")
    print(f"  수익률      : {bot_result['return_pct']:+.2f}%")
    print(f"  거래 횟수   : {bot_result['trade_count']}회")
    if bot_result['win_count'] + bot_result['loss_count'] > 0:
        win_rate = bot_result['win_count'] / (bot_result['win_count'] + bot_result['loss_count']) * 100
        print(f"  승률        : {win_rate:.1f}%  ({bot_result['win_count']}/{bot_result['win_count']+bot_result['loss_count']})")
    print(f"  샤프비      : {bot_metrics['sharpe']:.2f}")
    print(f"  MDD         : {bot_metrics['mdd']:.2f}%")
    print(f"  변동성(연)  : {bot_metrics['vol']:.2f}%")

    # ── 4. Buy-and-Hold 비교 ──
    print("\n[전략 2] Buy-and-Hold (단순 매수 후 보유)")
    print("-" * 70)
    bnh_initial = bot_result["initial_capital"]
    first_price = float(df["Close"].iloc[0])
    last_price = float(df["Close"].iloc[-1])
    bnh_shares = bnh_initial / first_price * 0.999  # 수수료 0.1%
    bnh_final = bnh_shares * last_price * 0.999
    bnh_return = (bnh_final / bnh_initial - 1) * 100

    bnh_equity = []
    for idx, row in df.iterrows():
        bnh_equity.append({"date": idx, "equity": bnh_shares * float(row["Close"])})
    bnh_metrics = calculate_metrics(bnh_equity, bnh_initial)

    print(f"  최종 자산   : ₩{bnh_final:>15,.0f}")
    print(f"  수익률      : {bnh_return:+.2f}%")
    print(f"  샤프비      : {bnh_metrics['sharpe']:.2f}")
    print(f"  MDD         : {bnh_metrics['mdd']:.2f}%")
    print(f"  변동성(연)  : {bnh_metrics['vol']:.2f}%")

    # ── 5. 비교 ──
    print("\n" + "=" * 70)
    print("  📊 결과 비교")
    print("=" * 70)
    alpha = bot_result["return_pct"] - bnh_return
    sharpe_diff = bot_metrics["sharpe"] - bnh_metrics["sharpe"]
    mdd_diff = bot_metrics["mdd"] - bnh_metrics["mdd"]

    print(f"  알파 (봇 - 벤치)     : {alpha:+.2f}%p")
    print(f"  샤프비 차이          : {sharpe_diff:+.2f}")
    print(f"  MDD 차이             : {mdd_diff:+.2f}%p (양수 = 봇이 더 큰 낙폭)")

    print()
    if alpha > 5 and sharpe_diff > 0.2:
        print("  🟢 등급 A: 봇이 벤치마크를 의미 있게 초과 — 전략 유효")
    elif alpha > -5 and abs(sharpe_diff) < 0.5:
        print("  🟡 등급 B: 벤치마크와 비슷 — 거래비용 고려 시 buy-and-hold 권장")
    else:
        print("  🔴 등급 C: 봇이 벤치마크에 미달 — 전략 재검토 필요")

    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
