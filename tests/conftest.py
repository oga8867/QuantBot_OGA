"""
=============================================================================
tests/conftest.py - 테스트 공통 설정 + Fixture 모음
=============================================================================

pytest가 자동으로 이 파일을 로드합니다.
여기에 정의된 fixture는 모든 테스트 파일에서 사용 가능합니다.

Fixture란?
- 테스트에 필요한 "준비물"을 만들어주는 함수
- @pytest.fixture 데코레이터로 정의
- 테스트 함수의 파라미터로 이름을 적으면 자동 주입됨
- 예: def test_something(safety_guard): ← safety_guard fixture 자동 생성

왜 conftest.py에 모아두나?
- 여러 테스트 파일에서 동일한 준비물을 재사용
- 테스트 코드 중복 제거
- 설정 변경 시 한 곳만 수정
=============================================================================
"""

import sys
import os
import sqlite3
import tempfile
import pytest
from dataclasses import dataclass, field
from typing import List, Optional

# 프로젝트 루트를 path에 추가 (import 가능하게)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# =============================================================================
# Config Fixtures
# =============================================================================

@pytest.fixture
def default_settings():
    """기본 Settings 객체 생성"""
    from config.settings import Settings
    return Settings()


@pytest.fixture
def safety_config():
    """테스트용 SafetyConfig (실제보다 빡빡한 제한)"""
    from executor.safety_guard import SafetyConfig
    return SafetyConfig(
        max_daily_loss_pct=0.03,
        max_order_pct=0.10,
        max_positions=5,
        max_position_weight=0.20,
        max_daily_trades=10,
        consecutive_loss_limit=3,
        order_delay_sec=0,        # 테스트에서는 대기 없음
        min_order_value=10.0,
        max_order_value=50000.0,
    )


@pytest.fixture
def safety_guard(safety_config):
    """테스트용 SafetyGuard 인스턴스"""
    from executor.safety_guard import SafetyGuard
    return SafetyGuard(capital=100000, paper=True, config=safety_config)


@pytest.fixture
def ensemble_strategy():
    """기본 가중치 EnsembleStrategy"""
    from strategy.ensemble import EnsembleStrategy
    return EnsembleStrategy()


# =============================================================================
# Mock Position (dict/object 양쪽 테스트용)
# =============================================================================

@dataclass
class MockPosition:
    """
    PaperExecutor의 Position을 흉내내는 모의 객체

    실제 Position dataclass와 동일한 속성을 가짐.
    SafetyGuard, app.py 등에서 Position 객체를 받는 함수를 테스트할 때 사용.
    """
    symbol: str = "AAPL"
    quantity: int = 10
    avg_price: float = 150.0
    current_price: float = 155.0
    market_value: float = 1550.0
    unrealized_pnl: float = 50.0
    unrealized_pnl_pct: float = 3.33


@pytest.fixture
def mock_position():
    """단일 MockPosition"""
    return MockPosition()


@pytest.fixture
def mock_positions():
    """여러 종목 MockPosition 리스트"""
    return [
        MockPosition(symbol="AAPL", quantity=10, avg_price=150.0,
                     current_price=155.0, market_value=1550.0,
                     unrealized_pnl=50.0, unrealized_pnl_pct=3.33),
        MockPosition(symbol="005930.KS", quantity=100, avg_price=70000.0,
                     current_price=72000.0, market_value=7200000.0,
                     unrealized_pnl=200000.0, unrealized_pnl_pct=2.86),
        MockPosition(symbol="MSFT", quantity=5, avg_price=400.0,
                     current_price=410.0, market_value=2050.0,
                     unrealized_pnl=50.0, unrealized_pnl_pct=2.5),
    ]


def make_dict_position(symbol="AAPL", quantity=10, avg_price=150.0,
                        current_price=155.0, market_value=1550.0,
                        unrealized_pnl=50.0, unrealized_pnl_pct=3.33):
    """dict 형태 포지션 생성 헬퍼 (DB 로드 시뮬레이션)"""
    return {
        "symbol": symbol,
        "quantity": quantity,
        "avg_price": avg_price,
        "current_price": current_price,
        "market_value": market_value,
        "unrealized_pnl": unrealized_pnl,
        "unrealized_pnl_pct": unrealized_pnl_pct,
    }


# =============================================================================
# Database Fixtures
# =============================================================================

@pytest.fixture
def temp_db_path():
    """
    임시 SQLite DB 파일 경로 제공

    테스트 종료 시 자동 삭제됨 (tmpdir 사용).
    실제 DB 파일을 사용하여 DatabaseManager를 테스트할 수 있음.
    """
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    # 정리: 테스트 후 DB 파일 삭제
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def db_manager(temp_db_path):
    """
    임시 DB를 사용하는 DatabaseManager 인스턴스

    context manager로 사용 가능:
        with db_manager as db:
            db.log_trade(...)
    """
    from database.cache import DatabaseManager
    db = DatabaseManager(db_path=temp_db_path)
    db.initialize()
    yield db
    db.close()


# =============================================================================
# Flask Test Client
# =============================================================================

@pytest.fixture
def flask_app():
    """
    Flask 테스트용 앱 인스턴스

    dashboard/app.py의 Flask 앱을 테스트 모드로 설정.
    실제 서버를 띄우지 않고도 API 엔드포인트를 테스트할 수 있음.
    """
    from dashboard.app import app
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(flask_app):
    """
    Flask 테스트 클라이언트

    HTTP 요청을 시뮬레이션:
        response = client.get("/api/status")
        assert response.status_code == 200
    """
    return flask_app.test_client()
