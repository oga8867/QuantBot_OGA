"""
=============================================================================
database/cache.py - SQLite 기반 데이터 캐시 및 거래 기록
=============================================================================

API 호출 결과를 캐싱하고, 거래 기록/포트폴리오 스냅샷을 저장합니다.

테이블 구조:
1. cache        → API 데이터 캐시 (TTL 기반)
2. trades       → 거래 기록 (매수/매도 이력)
3. portfolio_snapshots → 일별 포트폴리오 요약
4. signals      → 매매 신호 로그
5. positions    → 현재 보유 포지션 (실시간)
6. equity_history → 시간별 자산 추적 (차트/MDD용)
=============================================================================
"""

import sqlite3
import json
import os
import threading
import logging
from contextlib import contextmanager
from typing import Optional, Any, List
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class DatabaseManager:
    """
    SQLite 데이터베이스 관리자

    동시성 안전 설계:
      - WAL 모드: 동시 reader 다수 허용 + 1 writer 비차단
      - busy_timeout: 5초까지 락 대기 후 재시도
      - threading.RLock: 같은 connection을 여러 스레드에서 직접 execute 시
        SQLite 모듈이 동시 호출에 안전하지 않으므로 명시적 보호
      - synchronous=NORMAL: WAL과 함께 사용 시 충분히 안전 + 빠름
    """

    def __init__(self, db_path: str = "data/quantbot.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else "data", exist_ok=True)
        self.conn = None
        # ★ Connection 자체에 대한 쓰레드 직렬화 락
        # SQLite Python 모듈은 check_same_thread=False 모드에서도 한 connection의
        # 동시 execute 호출은 안전하지 않으므로 명시적으로 직렬화합니다.
        self._lock = threading.RLock()

    def initialize(self):
        """DB 연결 및 테이블 생성 (WAL + 락 + 마이그레이션)"""
        # timeout=5: SQLite 내부 busy handler가 락 해제까지 5초 대기
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=5.0)
        self.conn.row_factory = sqlite3.Row
        # ── WAL 모드: 다중 reader + 단일 writer 동시성 ──
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA busy_timeout=5000")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.execute("PRAGMA foreign_keys=ON")
        except sqlite3.OperationalError as e:
            logger.warning(f"[DB] PRAGMA 설정 실패 (계속 진행): {e}")
        self._create_tables()

    @contextmanager
    def _exec(self):
        """
        쓰레드 직렬화된 execute 컨텍스트.
        Connection이 None일 때도 안전하게 동작합니다.

        사용: with self._exec() as c: c.execute(...)
        """
        with self._lock:
            if self.conn is None:
                raise sqlite3.OperationalError("DB not initialized (call initialize() first)")
            yield self.conn

    # ── Context Manager (with문) 지원 ──
    # DB 연결 누수를 방지: with문 벗어나면 자동으로 close() 호출
    # 사용법:
    #   with DatabaseManager() as db:
    #       trades = db.get_trades()
    #   # ← 여기서 자동 close() (예외 발생 시에도)
    def __enter__(self):
        self.initialize()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False  # 예외를 삼키지 않음

    def close(self):
        """DB 연결 종료"""
        if self.conn:
            self.conn.close()
            self.conn = None

    def _create_tables(self):
        """6개 테이블 생성"""
        c = self.conn.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS cache (
            key TEXT PRIMARY KEY, value TEXT NOT NULL,
            expires_at TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
        c.execute("""CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL, symbol TEXT NOT NULL,
            market TEXT NOT NULL DEFAULT 'US', side TEXT NOT NULL,
            quantity INTEGER NOT NULL, price REAL NOT NULL,
            total_value REAL NOT NULL, strategy TEXT DEFAULT '',
            signal_score REAL DEFAULT 0, status TEXT DEFAULT 'filled',
            order_id TEXT DEFAULT '', notes TEXT DEFAULT '',
            pnl REAL DEFAULT 0)""")
        c.execute("""CREATE TABLE IF NOT EXISTS portfolio_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE, total_value REAL NOT NULL,
            cash REAL NOT NULL, positions_json TEXT DEFAULT '{}',
            daily_return REAL DEFAULT 0, cumulative_return REAL DEFAULT 0,
            max_drawdown REAL DEFAULT 0, notes TEXT DEFAULT '')""")
        c.execute("""CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL, symbol TEXT NOT NULL,
            signal_type TEXT NOT NULL, confidence REAL DEFAULT 0,
            score REAL DEFAULT 0, components_json TEXT DEFAULT '{}',
            reasons_json TEXT DEFAULT '[]', acted_on INTEGER DEFAULT 0)""")
        c.execute("""CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL UNIQUE, quantity INTEGER NOT NULL,
            avg_price REAL NOT NULL, current_price REAL DEFAULT 0,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
        c.execute("""CREATE TABLE IF NOT EXISTS equity_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL, total_equity REAL NOT NULL,
            cash REAL NOT NULL, positions_value REAL DEFAULT 0,
            daily_pnl REAL DEFAULT 0, cumulative_return REAL DEFAULT 0)""")
        self.conn.commit()

        # ── 기존 DB 마이그레이션: trades 테이블에 pnl 컬럼이 없으면 추가 ──
        # ALTER TABLE은 CREATE TABLE IF NOT EXISTS와 달리
        # 컬럼이 이미 있으면 에러가 나므로 try/except로 처리
        try:
            c.execute("ALTER TABLE trades ADD COLUMN pnl REAL DEFAULT 0")
            self.conn.commit()
        except sqlite3.OperationalError:
            pass  # 이미 pnl 컬럼이 존재하면 무시

        # ── 포지션 유형 시스템 마이그레이션 ──
        # 포지션 테이블에 유형/목표가/손절가/매매이유 등 추가
        _pos_migrations = [
            ("positions", "position_type TEXT DEFAULT ''"),
            ("positions", "position_type_en TEXT DEFAULT ''"),
            ("positions", "target_price REAL DEFAULT 0"),
            ("positions", "stop_price REAL DEFAULT 0"),
            ("positions", "reasons_json TEXT DEFAULT '[]'"),
            ("positions", "holding_period TEXT DEFAULT ''"),
            ("positions", "bought_at TEXT DEFAULT ''"),
            ("trades", "position_type TEXT DEFAULT ''"),
            ("trades", "reasons_json TEXT DEFAULT '[]'"),
            # ── ExitManager 추적 필드 (트레일링 스탑 / 분할 매도 / 본전 상향) ──
            ("positions", "entry_atr REAL DEFAULT 0"),                  # 진입 시점 ATR
            ("positions", "current_stop REAL DEFAULT 0"),               # 현재 활성 손절선 (트레일링 갱신)
            ("positions", "highest_since_entry REAL DEFAULT 0"),        # 최고가 (Chandelier용)
            ("positions", "partial_sold_pct REAL DEFAULT 0"),           # 이미 매도한 비중 (0/0.5/1.0)
            ("positions", "target_1 REAL DEFAULT 0"),                   # 1차 목표가
            ("positions", "target_2 REAL DEFAULT 0"),                   # 2차 목표가
            ("trades", "exit_reason TEXT DEFAULT ''"),                  # 청산 사유 (stop_loss, take_profit_1 등)
            ("trades", "decision_json TEXT DEFAULT '{}'"),              # 매매 결정 상세 (앙상블 점수, 모듈 기여도, 임계값 등)
            # ── ★ Phase 5: 모드 구분 ('paper'/'live') ──
            # 기존 행은 DEFAULT 'paper'로 자동 태그 (KIS_PAPER='true'였던 과거 상태 반영)
            # 새 행부터는 executor.paper에 따라 명시적으로 'paper'/'live' 저장
            # 대시보드는 현재 봇 모드와 일치하는 행만 표시 → 모드별 완전 분리
            ("trades", "mode TEXT DEFAULT 'paper'"),
            ("positions", "mode TEXT DEFAULT 'paper'"),
            ("signals", "mode TEXT DEFAULT 'paper'"),
            ("equity_history", "mode TEXT DEFAULT 'paper'"),
            ("portfolio_snapshots", "mode TEXT DEFAULT 'paper'"),
        ]
        for table, col_def in _pos_migrations:
            try:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")
                self.conn.commit()
            except sqlite3.OperationalError:
                pass  # 이미 존재

        # ── ★ Phase 5: positions 테이블 UNIQUE 제약 마이그레이션 ──
        # 기존: UNIQUE(symbol) — paper와 live가 같은 종목 보유 시 충돌
        # 신규: UNIQUE(symbol, mode) — 모드별 독립적인 포지션 저장 가능
        # SQLite는 ALTER로 제약 변경 불가 → 테이블 재생성 (1회만)
        try:
            # 마이그레이션 여부 확인: positions_v2 (new schema marker) 존재 여부
            existing_indices = [
                r[0] for r in c.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' "
                    "AND tbl_name='positions'"
                ).fetchall()
            ]
            needs_migration = "idx_positions_symbol_mode_unique" not in existing_indices
            if needs_migration:
                logger.info("[DB] positions 테이블 UNIQUE(symbol)→UNIQUE(symbol,mode) 마이그레이션")
                # 기존 데이터 백업 후 재생성
                c.execute("BEGIN IMMEDIATE")
                c.execute("ALTER TABLE positions RENAME TO _positions_old")
                c.execute("""CREATE TABLE positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    avg_price REAL NOT NULL,
                    current_price REAL DEFAULT 0,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    position_type TEXT DEFAULT '',
                    position_type_en TEXT DEFAULT '',
                    target_price REAL DEFAULT 0,
                    stop_price REAL DEFAULT 0,
                    reasons_json TEXT DEFAULT '[]',
                    holding_period TEXT DEFAULT '',
                    bought_at TEXT DEFAULT '',
                    entry_atr REAL DEFAULT 0,
                    current_stop REAL DEFAULT 0,
                    highest_since_entry REAL DEFAULT 0,
                    partial_sold_pct REAL DEFAULT 0,
                    target_1 REAL DEFAULT 0,
                    target_2 REAL DEFAULT 0,
                    mode TEXT NOT NULL DEFAULT 'paper'
                )""")
                # 기존 데이터 복사 (mode='paper'로 태그)
                # 기존 컬럼 목록 동적 확인
                old_cols = [r[1] for r in c.execute("PRAGMA table_info(_positions_old)").fetchall()]
                common_cols = [col for col in [
                    "symbol", "quantity", "avg_price", "current_price", "updated_at",
                    "position_type", "position_type_en", "target_price", "stop_price",
                    "reasons_json", "holding_period", "bought_at",
                    "entry_atr", "current_stop", "highest_since_entry",
                    "partial_sold_pct", "target_1", "target_2",
                ] if col in old_cols]
                col_list = ", ".join(common_cols)
                c.execute(f"INSERT INTO positions ({col_list}, mode) "
                          f"SELECT {col_list}, 'paper' FROM _positions_old")
                c.execute("DROP TABLE _positions_old")
                # 새 UNIQUE 제약 (symbol + mode 조합)
                c.execute("CREATE UNIQUE INDEX idx_positions_symbol_mode_unique "
                          "ON positions (symbol, mode)")
                self.conn.execute("COMMIT")
                logger.info("[DB] positions 마이그레이션 완료 (기존 데이터는 mode='paper'로 태그)")
        except sqlite3.OperationalError as e:
            try:
                self.conn.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            logger.warning(f"[DB] positions 마이그레이션 건너뜀: {e}")

        # ── ★ Phase 5: portfolio_snapshots UNIQUE(date)→UNIQUE(date,mode) ──
        # 기존: date에만 UNIQUE → 같은 날 paper와 live 스냅샷 불가
        try:
            existing_snap_indices = [
                r[0] for r in c.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' "
                    "AND tbl_name='portfolio_snapshots'"
                ).fetchall()
            ]
            if "idx_snap_date_mode_unique" not in existing_snap_indices:
                logger.info("[DB] portfolio_snapshots UNIQUE(date)→UNIQUE(date,mode) 마이그레이션")
                c.execute("BEGIN IMMEDIATE")
                c.execute("ALTER TABLE portfolio_snapshots RENAME TO _snap_old")
                c.execute("""CREATE TABLE portfolio_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    total_value REAL NOT NULL,
                    cash REAL NOT NULL,
                    positions_json TEXT DEFAULT '{}',
                    daily_return REAL DEFAULT 0,
                    cumulative_return REAL DEFAULT 0,
                    max_drawdown REAL DEFAULT 0,
                    notes TEXT DEFAULT '',
                    mode TEXT NOT NULL DEFAULT 'paper'
                )""")
                old_cols = [r[1] for r in c.execute("PRAGMA table_info(_snap_old)").fetchall()]
                common = [col for col in [
                    "date", "total_value", "cash", "positions_json",
                    "daily_return", "cumulative_return", "max_drawdown", "notes",
                ] if col in old_cols]
                col_list = ", ".join(common)
                c.execute(f"INSERT INTO portfolio_snapshots ({col_list}, mode) "
                          f"SELECT {col_list}, 'paper' FROM _snap_old")
                c.execute("DROP TABLE _snap_old")
                c.execute("CREATE UNIQUE INDEX idx_snap_date_mode_unique "
                          "ON portfolio_snapshots (date, mode)")
                self.conn.execute("COMMIT")
                logger.info("[DB] portfolio_snapshots 마이그레이션 완료")
        except sqlite3.OperationalError as e:
            try:
                self.conn.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            logger.warning(f"[DB] portfolio_snapshots 마이그레이션 건너뜀: {e}")

        # ── ★ Phase 5: 모드별 빠른 필터링을 위한 인덱스 ──
        try:
            c.execute("CREATE INDEX IF NOT EXISTS idx_trades_mode_ts ON trades (mode, timestamp DESC)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_positions_mode ON positions (mode)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_equity_mode_ts ON equity_history (mode, timestamp DESC)")
            self.conn.commit()
        except sqlite3.OperationalError as e:
            logger.debug(f"[DB] 인덱스 생성 실패 (무시): {e}")

    # === 캐시 ===

    def set_cache(self, key: str, value: Any, ttl: int = 3600):
        """캐시 저장 (TTL: 유효 시간 초)"""
        expires_at = datetime.now() + timedelta(seconds=ttl)
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO cache (key, value, expires_at) VALUES (?, ?, ?)",
                (key, json.dumps(value, default=str), expires_at.isoformat()))
            self.conn.commit()

    def get_cache(self, key: str) -> Optional[Any]:
        """캐시 조회 (만료 시 None)"""
        with self._lock:
            row = self.conn.execute(
                "SELECT value, expires_at FROM cache WHERE key = ?", (key,)).fetchone()
            if not row:
                return None
            if datetime.now() > datetime.fromisoformat(row["expires_at"]):
                self.conn.execute("DELETE FROM cache WHERE key = ?", (key,))
                self.conn.commit()
                return None
            return json.loads(row["value"])

    def clear_expired_cache(self):
        """만료된 캐시 정리"""
        with self._lock:
            self.conn.execute("DELETE FROM cache WHERE expires_at < ?",
                              (datetime.now().isoformat(),))
            self.conn.commit()

    # === 거래 기록 ===

    def log_trade(self, symbol, side, quantity, price, strategy="", market="US",
                  signal_score=0.0, order_id="", pnl=0.0,
                  position_type="", reasons_json="[]",
                  total_value=None, decision_json="{}", mode="paper"):
        """
        거래 기록 저장

        Parameters:
            symbol: 종목 코드
            side: "BUY" 또는 "SELL"
            quantity: 수량
            price: 체결 가격 (원래 통화 기준)
            strategy: 전략 이름
            market: "US" 또는 "KR"
            signal_score: 신호 점수
            order_id: 주문 ID
            pnl: 실현 손익 (매도 시에만 의미 있음, KRW 기준)
                 = (매도가 - 평균매수가) × 수량
            position_type: 포지션 유형 ("단타", "스윙", "장기")
            reasons_json: 매매 이유 JSON 문자열
            total_value: 거래 총액 (KRW 환산 기준).
                         None이면 quantity*price로 계산 (한국 주식용 하위 호환)
            decision_json: 매매 결정 상세 JSON (앙상블 점수, 모듈 기여도,
                           임계값, 진입/청산 trigger 등). 클릭 시 모달 표시용.
            mode: 'paper'(모의투자) 또는 'live'(실거래) — 대시보드에서
                  현재 봇 모드와 일치하는 거래만 표시할 때 사용
        """
        # ★ total_value가 명시적으로 전달되면 KRW 환산액 사용
        # 미국 주식은 USD가격 × 환율로 환산한 KRW 금액이 들어옴
        # 한국 주식은 quantity × price 그대로 (KRW = KRW)
        actual_total = total_value if total_value is not None else quantity * price
        with self._lock:
            self.conn.execute(
                """INSERT INTO trades
                (timestamp, symbol, market, side, quantity, price, total_value,
                 strategy, signal_score, order_id, pnl, position_type, reasons_json,
                 decision_json, mode)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (datetime.now().isoformat(), symbol, market, side, quantity, price,
                 actual_total, strategy, signal_score, order_id, pnl,
                 position_type, reasons_json, decision_json, mode))
            self.conn.commit()

    def get_trades(self, symbol=None, limit=100, mode=None):
        """
        거래 기록 조회

        Parameters:
            mode: None이면 전체, 'paper'/'live' 지정 시 해당 모드 거래만 반환
        """
        with self._lock:
            conditions = []
            params = []
            if symbol:
                conditions.append("symbol = ?")
                params.append(symbol)
            if mode:
                conditions.append("mode = ?")
                params.append(mode)
            where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
            params.append(limit)
            cursor = self.conn.execute(
                f"SELECT * FROM trades {where} ORDER BY timestamp DESC LIMIT ?",
                tuple(params))
            return [dict(row) for row in cursor.fetchall()]

    def get_trades_by_date(self, date_str, mode=None):
        """특정 날짜 거래 조회 (mode 필터 가능)"""
        with self._lock:
            if mode:
                cursor = self.conn.execute(
                    "SELECT * FROM trades WHERE timestamp LIKE ? AND mode = ? ORDER BY timestamp",
                    (date_str + "%", mode))
            else:
                cursor = self.conn.execute(
                    "SELECT * FROM trades WHERE timestamp LIKE ? ORDER BY timestamp",
                    (date_str + "%",))
            return [dict(row) for row in cursor.fetchall()]

    def get_trade_stats(self, mode=None):
        """전체 거래 통계 (mode 필터 가능)"""
        with self._lock:
            if mode:
                trades = [dict(r) for r in self.conn.execute(
                    "SELECT * FROM trades WHERE mode = ?", (mode,)).fetchall()]
            else:
                trades = [dict(r) for r in self.conn.execute(
                    "SELECT * FROM trades").fetchall()]
        if not trades:
            return {"total_trades": 0, "buy_count": 0, "sell_count": 0,
                    "unique_symbols": 0, "total_buy_value": 0, "total_sell_value": 0}
        buys = [t for t in trades if t["side"].upper() == "BUY"]
        sells = [t for t in trades if t["side"].upper() == "SELL"]
        return {
            "total_trades": len(trades), "buy_count": len(buys),
            "sell_count": len(sells),
            "unique_symbols": len(set(t["symbol"] for t in trades)),
            "total_buy_value": sum(t["total_value"] for t in buys),
            "total_sell_value": sum(t["total_value"] for t in sells),
        }

    def get_kelly_stats(self, mode=None):
        """
        Kelly 사이징용 거래 통계 — 닫힌(매도 완료) 거래의 승률·평균손익률.

        Returns:
            dict: {win_rate, avg_win, avg_loss, sample_size}
              - win_rate: 승률 (0~1)
              - avg_win: 이긴 거래 평균 수익률 (양수, 예: 0.08 = +8%)
              - avg_loss: 진 거래 평균 손실률 (양수, 예: 0.04 = -4%)
              - sample_size: 손익이 확정된 닫힌 거래 수
        """
        with self._lock:
            if mode:
                rows = [dict(r) for r in self.conn.execute(
                    "SELECT pnl, total_value FROM trades "
                    "WHERE UPPER(side) = 'SELL' AND mode = ?", (mode,)).fetchall()]
            else:
                rows = [dict(r) for r in self.conn.execute(
                    "SELECT pnl, total_value FROM trades "
                    "WHERE UPPER(side) = 'SELL'").fetchall()]
        wins, losses = [], []
        for r in rows:
            pnl = r.get("pnl") or 0
            total = r.get("total_value") or 0
            cost = total - pnl  # 매도금액 - 손익 ≈ 매수원가
            if cost <= 0:
                continue  # 비정상 데이터 스킵
            ret = pnl / cost  # 1회 수익률
            if pnl > 0:
                wins.append(ret)
            elif pnl < 0:
                losses.append(-ret)  # 손실은 양수로 변환
            # pnl == 0 (본전)은 승/패 아님 → 통계에서 제외
        sample = len(wins) + len(losses)
        if sample == 0:
            return {"win_rate": 0.0, "avg_win": 0.0,
                    "avg_loss": 0.0, "sample_size": 0}
        return {
            "win_rate": len(wins) / sample,
            "avg_win": (sum(wins) / len(wins)) if wins else 0.0,
            "avg_loss": (sum(losses) / len(losses)) if losses else 0.0,
            "sample_size": sample,
        }

    # === 포지션 관리 ===

    def save_positions(self, positions, mode="paper"):
        """
        포지션 전체 저장 (트랜잭션으로 원자적 실행) — 모드별 격리

        ★ 중요: 같은 모드의 포지션만 DELETE 후 INSERT.
        다른 모드(paper vs live)의 포지션은 건드리지 않습니다.

        Parameters:
            positions: 저장할 포지션 리스트
            mode: 'paper' 또는 'live' — 이 모드의 기존 행만 교체
        """
        with self._lock:
            try:
                self.conn.execute("BEGIN IMMEDIATE")
                # ★ 같은 모드의 행만 삭제 (다른 모드 포지션 보존)
                self.conn.execute("DELETE FROM positions WHERE mode = ?", (mode,))
                for p in positions:
                    self.conn.execute(
                        """INSERT INTO positions
                        (symbol, quantity, avg_price, current_price,
                         position_type, position_type_en, target_price, stop_price,
                         reasons_json, holding_period, bought_at, mode)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (p["symbol"], p["quantity"], p["avg_price"],
                         p.get("current_price", 0),
                         p.get("position_type", ""),
                         p.get("position_type_en", ""),
                         p.get("target_price", 0),
                         p.get("stop_price", 0),
                         p.get("reasons_json", "[]"),
                         p.get("holding_period", ""),
                         p.get("bought_at", ""),
                         mode))
                self.conn.execute("COMMIT")
            except Exception:
                try:
                    self.conn.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass  # 이미 트랜잭션이 종료된 상태
                raise

    def update_position(self, symbol, quantity, avg_price, current_price=0,
                        position_type="", position_type_en="",
                        target_price=0, stop_price=0,
                        reasons_json="[]", holding_period="", bought_at="",
                        mode="paper"):
        """
        포지션 UPSERT (모드별 격리)

        composite unique key (symbol, mode) 사용하여
        같은 종목이라도 paper/live 모드별로 독립적인 행을 유지합니다.
        """
        with self._lock:
            # 같은 모드의 같은 종목이 있으면 업데이트, 없으면 삽입
            existing = self.conn.execute(
                "SELECT id FROM positions WHERE symbol = ? AND mode = ?",
                (symbol, mode)
            ).fetchone()
            if existing:
                self.conn.execute(
                    """UPDATE positions SET
                        quantity = ?, avg_price = ?, current_price = ?,
                        updated_at = ?, position_type = ?, position_type_en = ?,
                        target_price = ?, stop_price = ?,
                        reasons_json = ?, holding_period = ?, bought_at = ?
                       WHERE symbol = ? AND mode = ?""",
                    (quantity, avg_price, current_price,
                     datetime.now().isoformat(),
                     position_type, position_type_en,
                     target_price, stop_price,
                     reasons_json, holding_period, bought_at,
                     symbol, mode))
            else:
                self.conn.execute(
                    """INSERT INTO positions
                       (symbol, quantity, avg_price, current_price, updated_at,
                        position_type, position_type_en, target_price, stop_price,
                        reasons_json, holding_period, bought_at, mode)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (symbol, quantity, avg_price, current_price,
                     datetime.now().isoformat(),
                     position_type, position_type_en,
                     target_price, stop_price,
                     reasons_json, holding_period, bought_at,
                     mode))
            self.conn.commit()

    def update_exit_state(self, symbol, mode="paper", *,
                          entry_atr=0.0, current_stop=0.0,
                          highest_since_entry=0.0, partial_sold_pct=0.0,
                          target_1=0.0, target_2=0.0):
        """
        ExitManager 추적 필드만 갱신 (손절/익절/트레일링 상태 동기화)

        ⚠️ 반드시 (symbol, mode) 둘 다로 필터 — positions는 UNIQUE(symbol, mode).
        mode 누락 시 다른 모드의 같은 종목 행을 잘못 덮어쓸 수 있음.

        쓰레드 락(self._lock)으로 보호되어 다른 DB writer와 직렬화됩니다.
        해당 (symbol, mode) 행이 없으면 아무것도 안 함 (조용히 무시).
        """
        with self._lock:
            self.conn.execute(
                """UPDATE positions SET
                    entry_atr = ?, current_stop = ?, highest_since_entry = ?,
                    partial_sold_pct = ?, target_1 = ?, target_2 = ?,
                    stop_price = ?
                WHERE symbol = ? AND mode = ?""",
                (float(entry_atr), float(current_stop), float(highest_since_entry),
                 float(partial_sold_pct), float(target_1), float(target_2),
                 float(current_stop),  # stop_price도 current_stop으로 동기화
                 symbol, mode))
            self.conn.commit()

    def load_positions(self, mode=None):
        """
        DB에서 포지션 복원

        Parameters:
            mode: None이면 전체, 'paper'/'live' 지정 시 해당 모드만 반환
                  봇 시작 시에는 반드시 mode를 지정해야 모드별 격리 보장
        """
        with self._lock:
            if mode:
                return [dict(r) for r in self.conn.execute(
                    "SELECT * FROM positions WHERE mode = ? ORDER BY symbol",
                    (mode,)).fetchall()]
            return [dict(r) for r in
                    self.conn.execute("SELECT * FROM positions ORDER BY symbol").fetchall()]

    def get_position(self, symbol, mode=None):
        """
        특정 종목 포지션 조회 (모드 필터 가능)

        mode 지정 시 해당 모드의 포지션만 반환 (paper와 live가 같은 종목 보유 가능)
        """
        with self._lock:
            if mode:
                row = self.conn.execute(
                    "SELECT * FROM positions WHERE symbol = ? AND mode = ?",
                    (symbol, mode)).fetchone()
            else:
                row = self.conn.execute(
                    "SELECT * FROM positions WHERE symbol = ? LIMIT 1",
                    (symbol,)).fetchone()
            return dict(row) if row else None

    def delete_position(self, symbol, mode=None):
        """포지션 삭제 (mode 지정 시 해당 모드만)"""
        with self._lock:
            if mode:
                self.conn.execute(
                    "DELETE FROM positions WHERE symbol = ? AND mode = ?",
                    (symbol, mode))
            else:
                self.conn.execute("DELETE FROM positions WHERE symbol = ?", (symbol,))
            self.conn.commit()

    # === 포트폴리오 스냅샷 (일별) ===

    def save_snapshot(self, total_value, cash, positions,
                      daily_return=0.0, cumulative_return=0.0, max_drawdown=0.0,
                      mode="paper"):
        """
        일일 포트폴리오 스냅샷 저장 (모드별 분리)

        ★ UPSERT 키를 (date, mode)로 변경하여 같은 날 paper와 live가
        독립적인 스냅샷을 가질 수 있게 합니다.
        """
        with self._lock:
            # date+mode 기준 upsert (composite key)
            today = datetime.now().strftime("%Y-%m-%d")
            existing = self.conn.execute(
                "SELECT id FROM portfolio_snapshots WHERE date = ? AND mode = ?",
                (today, mode)
            ).fetchone()
            if existing:
                self.conn.execute(
                    """UPDATE portfolio_snapshots SET
                        total_value = ?, cash = ?, positions_json = ?,
                        daily_return = ?, cumulative_return = ?, max_drawdown = ?
                       WHERE date = ? AND mode = ?""",
                    (total_value, cash, json.dumps(positions, default=str),
                     daily_return, cumulative_return, max_drawdown,
                     today, mode))
            else:
                self.conn.execute(
                    """INSERT INTO portfolio_snapshots
                    (date, total_value, cash, positions_json,
                     daily_return, cumulative_return, max_drawdown, mode)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (today, total_value, cash, json.dumps(positions, default=str),
                     daily_return, cumulative_return, max_drawdown, mode))
            self.conn.commit()

    def get_snapshots(self, days=30, mode=None):
        """최근 N일간 스냅샷 (★ Phase 10: mode 필터)"""
        with self._lock:
            if mode:
                return [dict(r) for r in self.conn.execute(
                    "SELECT * FROM portfolio_snapshots WHERE mode = ? "
                    "ORDER BY date DESC LIMIT ?", (mode, days)
                ).fetchall()]
            return [dict(r) for r in self.conn.execute(
                "SELECT * FROM portfolio_snapshots ORDER BY date DESC LIMIT ?", (days,)
            ).fetchall()]

    def get_latest_snapshot(self, mode=None):
        """최신 스냅샷 (★ Phase 10: mode 필터)"""
        with self._lock:
            if mode:
                row = self.conn.execute(
                    "SELECT * FROM portfolio_snapshots WHERE mode = ? "
                    "ORDER BY date DESC LIMIT 1", (mode,)).fetchone()
            else:
                row = self.conn.execute(
                    "SELECT * FROM portfolio_snapshots ORDER BY date DESC LIMIT 1").fetchone()
            return dict(row) if row else None

    # === Equity History (시간별 자산 추적) ===

    def save_equity_snapshot(self, total_equity, cash, positions_value=0,
                              daily_pnl=0, cumulative_return=0, mode="paper"):
        """자산 스냅샷 저장 (5분 간격, 모드별 분리)"""
        with self._lock:
            self.conn.execute(
                """INSERT INTO equity_history
                   (timestamp, total_equity, cash, positions_value, daily_pnl, cumulative_return, mode)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (datetime.now().isoformat(),
                 total_equity, cash, positions_value, daily_pnl, cumulative_return, mode))
            self.conn.commit()

    def get_equity_history(self, days=30, mode=None):
        """
        최근 N일간 equity history (mode 필터 가능)

        대시보드 자산 차트가 paper/live를 섞어서 표시하지 않도록 mode 필터 사용
        """
        since = (datetime.now() - timedelta(days=days)).isoformat()
        with self._lock:
            if mode:
                return [dict(r) for r in self.conn.execute(
                    "SELECT * FROM equity_history WHERE timestamp >= ? AND mode = ? "
                    "ORDER BY timestamp ASC",
                    (since, mode)).fetchall()]
            return [dict(r) for r in self.conn.execute(
                "SELECT * FROM equity_history WHERE timestamp >= ? ORDER BY timestamp ASC",
                (since,)).fetchall()]

    def get_latest_equity(self, mode=None):
        """최신 equity 스냅샷 (mode 필터 가능)"""
        with self._lock:
            if mode:
                row = self.conn.execute(
                    "SELECT * FROM equity_history WHERE mode = ? ORDER BY timestamp DESC LIMIT 1",
                    (mode,)).fetchone()
            else:
                row = self.conn.execute(
                    "SELECT * FROM equity_history ORDER BY timestamp DESC LIMIT 1").fetchone()
            return dict(row) if row else None

    def calculate_max_drawdown(self, days=30, mode=None):
        """최대 낙폭(MDD) 계산 (0~1) — ★ Phase 10: mode 필터로 paper/live 분리"""
        history = self.get_equity_history(days=days, mode=mode)
        if not history:
            return 0.0
        equities = [h["total_equity"] for h in history]
        peak = equities[0]
        max_dd = 0.0
        for eq in equities:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)
        return max_dd

    # === 신호 로그 ===

    def log_signal(self, symbol, signal_type, confidence, score,
                   components, reasons, acted_on=False, mode="paper"):
        """매매 신호 기록 (모드별 구분)"""
        with self._lock:
            self.conn.execute(
                """INSERT INTO signals
                (timestamp, symbol, signal_type, confidence, score,
                 components_json, reasons_json, acted_on, mode)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (datetime.now().isoformat(), symbol, signal_type, confidence, score,
                 json.dumps(components), json.dumps(reasons), 1 if acted_on else 0,
                 mode))
            self.conn.commit()

    def get_signals(self, symbol=None, limit=50, mode=None):
        """매매 신호 조회 (mode 필터 가능)"""
        with self._lock:
            conditions = []
            params = []
            if symbol:
                conditions.append("symbol = ?")
                params.append(symbol)
            if mode:
                conditions.append("mode = ?")
                params.append(mode)
            where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
            params.append(limit)
            cursor = self.conn.execute(
                f"SELECT * FROM signals {where} ORDER BY timestamp DESC LIMIT ?",
                tuple(params))
            return [dict(row) for row in cursor.fetchall()]

    # === 데이터 초기화 ===

    def reset_all_data(self, mode=None):
        """
        거래/포지션/자산 데이터를 초기화합니다.

        Parameters:
            mode: "paper" → 모의거래 데이터만 삭제
                  "live"  → 실거래 데이터만 삭제
                  None    → 전체 삭제 (하위 호환)
            ⚠️ 모드를 지정하면 해당 모드 행만 지웁니다. 모의거래 초기화로
               실거래 이력까지 날려버리는 사고를 막기 위함입니다.

        삭제 대상:
        - positions: 보유 포지션
        - trades: 거래 이력
        - equity_history: 자산 추적 기록
        - portfolio_snapshots: 일일 스냅샷
        - signals: 매매 신호 기록

        보존 대상:
        - cache: 주가 데이터 캐시 (재다운로드 시간 절약)

        Returns:
            dict: 테이블별 삭제 건수
        """
        deleted = {}
        tables = ["positions", "trades", "equity_history",
                  "portfolio_snapshots", "signals"]
        mode_scoped = mode in ("paper", "live")

        with self._lock:
            for table in tables:
                try:
                    if mode_scoped:
                        cursor = self.conn.execute(
                            f"SELECT COUNT(*) FROM {table} WHERE mode = ?",
                            (mode,))
                        count = cursor.fetchone()[0]
                        self.conn.execute(
                            f"DELETE FROM {table} WHERE mode = ?", (mode,))
                    else:
                        cursor = self.conn.execute(
                            f"SELECT COUNT(*) FROM {table}")
                        count = cursor.fetchone()[0]
                        self.conn.execute(f"DELETE FROM {table}")
                    deleted[table] = count
                except Exception:
                    deleted[table] = 0

            # AUTO_INCREMENT 카운터 리셋 — 전체 삭제 시에만.
            # 모드별 삭제는 다른 모드 행이 남아 있으므로 sqlite_sequence를
            # 건드리지 않습니다 (건드리면 ID 재사용 충돌 위험).
            if not mode_scoped:
                try:
                    self.conn.execute(
                        "DELETE FROM sqlite_sequence WHERE name IN (?, ?, ?, ?, ?)",
                        tables
                    )
                except Exception:
                    pass

            self.conn.commit()
        return deleted
