# -*- coding: utf-8 -*-
"""
=============================================================================
repair_positions.py — 깨진 실거래 positions 행 1회성 보정 도구
=============================================================================

배경:
  실거래(KIS) 매수 체결 후 _execute_buy가 positions 테이블에 기록하는
  단계가 누락되어, positions가 reconcile(_reconcile_with_broker)에 의해서만
  채워졌다. 그 결과 holding_period="복원됨", bought_at="", 수량/평단이
  실제와 다른 행이 남았다.

  run_bot.py의 _execute_buy/_execute_sell 수정으로 앞으로는 정상 기록되지만,
  이미 깨진 기존 행은 이 스크립트로 한 번 보정한다.

동작:
  positions 테이블에서 holding_period == "복원됨" 인 행을 찾아,
  같은 (symbol, mode)의 trades 기록으로부터
    - 실제 보유수량(순매수)
    - 평균 매수가(매수금액 합 / 매수수량 합)
    - 최초 매수 시각(bought_at)
  를 재계산하고, position_type에 맞는 holding_period와
  ATR 기반 손절/목표가를 일관되게 다시 기록한다.

사용법 (★ 반드시 봇을 정지한 상태에서 실행):
  미리보기(변경 안 함):   venv\\Scripts\\python.exe repair_positions.py
  실제 적용:              venv\\Scripts\\python.exe repair_positions.py apply

재실행 안전: 적용 후에는 holding_period가 "복원됨"이 아니므로 다시 돌려도
            아무 행도 건드리지 않는다.
=============================================================================
"""
import sqlite3
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

DB_PATH = "data/quantbot.db"

# position_type → holding_period 표준 라벨
HOLDING_LABEL = {"단타": "1~3일", "스윙": "1~4주", "장기": "1개월+"}
# ATR 추정 비율 (한국 주식 평균 일변동성 — reconcile과 동일한 가정)
ATR_RATIO = 0.025


def main():
    apply = len(sys.argv) > 1 and sys.argv[1].lower() == "apply"
    mode_label = "★ 실제 적용 모드" if apply else "미리보기 모드 (변경 안 함)"

    print("=" * 68)
    print(f"  positions 보정 도구 — {mode_label}")
    print("=" * 68)
    if apply:
        print("⚠️  봇이 실행 중이면 먼저 정지하세요. 보정 후 봇을 재시작하면")
        print("    수정된 코드 + 정상 DB 상태로 동작합니다.")
    print()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    broken = c.execute(
        "SELECT * FROM positions WHERE holding_period = '복원됨' ORDER BY mode, symbol"
    ).fetchall()

    if not broken:
        print("보정 대상 없음 (holding_period='복원됨' 행 0개).")
        conn.close()
        return

    print(f"보정 대상: {len(broken)}개 행\n")
    changes = []

    for row in broken:
        d = dict(row)
        symbol = d["symbol"]
        mode = d["mode"]
        ptype = d.get("position_type") or "스윙"

        # ── 같은 (symbol, mode)의 trades로 실제 보유분 재계산 ──
        trades = c.execute(
            "SELECT timestamp, side, quantity, price FROM trades "
            "WHERE symbol = ? AND mode = ? ORDER BY timestamp",
            (symbol, mode),
        ).fetchall()

        buy_qty = 0
        buy_cost = 0.0
        sell_qty = 0
        first_buy_ts = ""
        for t in trades:
            side = (t["side"] or "").upper()
            q = int(t["quantity"] or 0)
            p = float(t["price"] or 0)
            if side == "BUY":
                buy_qty += q
                buy_cost += q * p
                if not first_buy_ts:
                    first_buy_ts = t["timestamp"] or ""
            elif side == "SELL":
                sell_qty += q

        net_qty = buy_qty - sell_qty
        if buy_qty <= 0:
            print(f"  [건너뜀] {symbol} ({mode}): trades에 매수 기록 없음 → 수동 확인 필요")
            continue
        if net_qty <= 0:
            print(f"  [건너뜀] {symbol} ({mode}): 순매수 {net_qty}주 ≤ 0 → 수동 확인 필요")
            continue

        # 평단 = 총 매수금액 / 총 매수수량 (매도는 평단을 바꾸지 않음)
        avg_price = buy_cost / buy_qty
        atr = avg_price * ATR_RATIO
        stop = round(avg_price - atr * 2.0, 2)
        target_1 = round(avg_price + atr * 4.0, 2)
        target_2 = round(avg_price + atr * 8.0, 2)
        holding = HOLDING_LABEL.get(ptype, "1~4주")
        highest = max(float(d.get("highest_since_entry") or 0), avg_price)

        print(f"  [{symbol}] mode={mode}")
        print(f"     수량       {d.get('quantity')}  →  {net_qty}")
        print(f"     평단       {d.get('avg_price')}  →  {avg_price:,.2f}")
        print(f"     holding    '{d.get('holding_period')}'  →  '{holding}'")
        print(f"     bought_at  '{d.get('bought_at')}'  →  '{first_buy_ts}'")
        print(f"     entry_atr  {d.get('entry_atr')}  →  {round(atr, 2)}")
        print(f"     손절선     {d.get('current_stop')}  →  {stop}")
        print(f"     목표1/2    {d.get('target_1')}/{d.get('target_2')}"
              f"  →  {target_1}/{target_2}")
        print()

        changes.append({
            "symbol": symbol, "mode": mode,
            "quantity": net_qty, "avg_price": avg_price,
            "entry_atr": round(atr, 2), "stop": stop,
            "target_1": target_1, "target_2": target_2,
            "highest": highest, "holding": holding,
            "bought_at": first_buy_ts,
        })

    if not changes:
        print("적용할 변경 없음.")
        conn.close()
        return

    if not apply:
        print("-" * 68)
        print("미리보기 종료. 실제 적용하려면:  python repair_positions.py apply")
        conn.close()
        return

    # ── 실제 적용 ──
    for ch in changes:
        c.execute(
            """UPDATE positions SET
                 quantity = ?, avg_price = ?, current_price = ?,
                 entry_atr = ?, current_stop = ?, stop_price = ?,
                 target_1 = ?, target_2 = ?, target_price = ?,
                 highest_since_entry = ?, holding_period = ?, bought_at = ?
               WHERE symbol = ? AND mode = ?""",
            (ch["quantity"], ch["avg_price"], ch["avg_price"],
             ch["entry_atr"], ch["stop"], ch["stop"],
             ch["target_1"], ch["target_2"], ch["target_1"],
             ch["highest"], ch["holding"], ch["bought_at"],
             ch["symbol"], ch["mode"]),
        )
    conn.commit()
    conn.close()
    print("=" * 68)
    print(f"✓ {len(changes)}개 행 보정 완료. 이제 봇을 재시작하세요.")
    print("=" * 68)


if __name__ == "__main__":
    main()
