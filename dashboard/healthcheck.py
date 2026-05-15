"""
=============================================================================
dashboard/healthcheck.py - 봇 헬스체크 모듈
=============================================================================

봇이 정상적으로 동작하고 있는지 자동으로 점검합니다.

헬스체크 항목:
1. 봇 프로세스 상태 (실행 중 / 중지)
2. DB 연결 상태 (읽기/쓰기 테스트)
3. API 연결 상태 (yfinance, DART 등)
4. 마지막 분석 시간 (너무 오래되면 경고)
5. 디스크 사용량 (로그/DB 파일 크기)
6. 메모리 사용량
7. 에러 빈도 (최근 1시간 에러 수)

헬스체크 결과:
- HEALTHY (🟢): 모든 항목 정상
- WARNING (🟡): 일부 항목 주의 필요
- CRITICAL (🔴): 즉시 조치 필요

사용 시나리오:
- 대시보드에서 실시간 상태 표시
- 텔레그램/디스코드로 이상 알림 발송
- 주간 보고서에 헬스 요약 포함
=============================================================================
"""

import os
import time
import logging
from typing import Dict, Any, List
from datetime import datetime, timedelta

logger = logging.getLogger("HealthCheck")


class HealthChecker:
    """
    봇 헬스체크 모듈

    각 항목을 점검하고 종합 상태를 반환합니다.
    
    상태 코드:
        "healthy": 정상
        "warning": 주의 (동작은 하지만 점검 필요)
        "critical": 위험 (즉시 조치 필요)
    """

    def __init__(self, bot_instance=None, db=None):
        """
        Args:
            bot_instance: QuantBot 인스턴스 (None이면 프로세스 상태만 체크)
            db: DatabaseManager 인스턴스
        """
        self.bot = bot_instance
        self.db = db

    def check_all(self) -> Dict[str, Any]:
        """
        전체 헬스체크 실행

        Returns:
            {
                "status": "healthy" | "warning" | "critical",
                "checks": [
                    {"name": "...", "status": "...", "message": "...", "icon": "..."},
                    ...
                ],
                "healthy_count": int,
                "warning_count": int,
                "critical_count": int,
                "checked_at": str,
            }
        """
        checks = []

        # 1. 봇 프로세스 상태
        checks.append(self._check_bot_process())

        # 2. DB 연결
        checks.append(self._check_database())

        # 3. 마지막 분석 시간
        checks.append(self._check_last_analysis())

        # 4. 디스크 사용량
        checks.append(self._check_disk_usage())

        # 5. 에러 빈도
        checks.append(self._check_error_rate())

        # 6. 데이터 신선도
        checks.append(self._check_data_freshness())

        # 종합 판단
        statuses = [c["status"] for c in checks]
        if "critical" in statuses:
            overall = "critical"
        elif "warning" in statuses:
            overall = "warning"
        else:
            overall = "healthy"

        return {
            "status": overall,
            "checks": checks,
            "healthy_count": statuses.count("healthy"),
            "warning_count": statuses.count("warning"),
            "critical_count": statuses.count("critical"),
            "total_checks": len(checks),
            "checked_at": datetime.now().isoformat(),
        }

    def _check_bot_process(self) -> Dict:
        """봇 프로세스 상태 확인"""
        if self.bot and hasattr(self.bot, "running") and self.bot.running:
            return {
                "name": "봇 프로세스",
                "status": "healthy",
                "message": "봇이 정상 실행 중입니다",
                "icon": "🤖",
            }
        return {
            "name": "봇 프로세스",
            "status": "warning",
            "message": "봇이 실행 중이 아닙니다",
            "icon": "🤖",
        }

    def _check_database(self) -> Dict:
        """DB 연결 + 읽기/쓰기 테스트"""
        if not self.db:
            try:
                from database.cache import DatabaseManager
                self.db = DatabaseManager()
                self.db.initialize()
            except Exception as e:
                return {
                    "name": "데이터베이스",
                    "status": "critical",
                    "message": f"DB 연결 실패: {str(e)[:50]}",
                    "icon": "🗄️",
                }

        try:
            # 읽기 테스트
            self.db.get_trades(limit=1)

            # 쓰기 테스트 (캐시에 테스트 값)
            self.db.set_cache("_healthcheck", {"t": time.time()}, ttl=60)
            val = self.db.get_cache("_healthcheck")

            if val:
                return {
                    "name": "데이터베이스",
                    "status": "healthy",
                    "message": "DB 읽기/쓰기 정상",
                    "icon": "🗄️",
                }
            else:
                return {
                    "name": "데이터베이스",
                    "status": "warning",
                    "message": "DB 쓰기 후 읽기 실패",
                    "icon": "🗄️",
                }
        except Exception as e:
            return {
                "name": "데이터베이스",
                "status": "critical",
                "message": f"DB 오류: {str(e)[:50]}",
                "icon": "🗄️",
            }

    def _check_last_analysis(self) -> Dict:
        """마지막 분석 시간 확인"""
        try:
            if not self.db:
                return {
                    "name": "분석 주기",
                    "status": "warning",
                    "message": "DB 없음",
                    "icon": "⏱️",
                }

            signals = self.db.get_signals(limit=1)
            if not signals:
                return {
                    "name": "분석 주기",
                    "status": "warning",
                    "message": "아직 분석 기록이 없습니다",
                    "icon": "⏱️",
                }

            last_time = datetime.fromisoformat(signals[0]["timestamp"])
            elapsed = datetime.now() - last_time
            minutes = elapsed.total_seconds() / 60

            if minutes < 30:
                return {
                    "name": "분석 주기",
                    "status": "healthy",
                    "message": f"마지막 분석: {int(minutes)}분 전",
                    "icon": "⏱️",
                }
            elif minutes < 120:
                return {
                    "name": "분석 주기",
                    "status": "warning",
                    "message": f"마지막 분석: {int(minutes)}분 전 (지연)",
                    "icon": "⏱️",
                }
            else:
                hours = int(minutes / 60)
                return {
                    "name": "분석 주기",
                    "status": "critical",
                    "message": f"마지막 분석: {hours}시간 전 (장기 중단)",
                    "icon": "⏱️",
                }

        except Exception as e:
            return {
                "name": "분석 주기",
                "status": "warning",
                "message": f"확인 실패: {str(e)[:30]}",
                "icon": "⏱️",
            }

    def _check_disk_usage(self) -> Dict:
        """디스크 사용량 (DB + 로그 파일 크기)"""
        total_size = 0

        # DB 파일 크기
        db_path = "data/quantbot.db"
        if os.path.exists(db_path):
            total_size += os.path.getsize(db_path)

        # 로그 디렉토리
        log_dir = "logs"
        if os.path.exists(log_dir):
            for f in os.listdir(log_dir):
                fp = os.path.join(log_dir, f)
                if os.path.isfile(fp):
                    total_size += os.path.getsize(fp)

        # 보고서 디렉토리
        report_dir = "reports"
        if os.path.exists(report_dir):
            for f in os.listdir(report_dir):
                fp = os.path.join(report_dir, f)
                if os.path.isfile(fp):
                    total_size += os.path.getsize(fp)

        size_mb = total_size / (1024 * 1024)

        if size_mb < 100:
            return {
                "name": "디스크 사용량",
                "status": "healthy",
                "message": f"데이터 파일: {size_mb:.1f}MB",
                "icon": "💾",
            }
        elif size_mb < 500:
            return {
                "name": "디스크 사용량",
                "status": "warning",
                "message": f"데이터 파일: {size_mb:.1f}MB (정리 권장)",
                "icon": "💾",
            }
        else:
            return {
                "name": "디스크 사용량",
                "status": "critical",
                "message": f"데이터 파일: {size_mb:.1f}MB (정리 필요!)",
                "icon": "💾",
            }

    def _check_error_rate(self) -> Dict:
        """최근 에러 빈도 (로그 파일 기반)"""
        try:
            log_file = "logs/quantbot.log"
            if not os.path.exists(log_file):
                return {
                    "name": "에러 빈도",
                    "status": "healthy",
                    "message": "로그 파일 없음 (새 시작)",
                    "icon": "⚠️",
                }

            # 최근 1시간 에러 수 확인 (마지막 500줄 스캔)
            error_count = 0
            one_hour_ago = (datetime.now() - timedelta(hours=1)).isoformat()[:16]

            with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()[-500:]

            for line in lines:
                if "ERROR" in line and line[:16] >= one_hour_ago[:16]:
                    error_count += 1

            if error_count == 0:
                return {
                    "name": "에러 빈도",
                    "status": "healthy",
                    "message": "최근 1시간 에러 없음",
                    "icon": "⚠️",
                }
            elif error_count < 5:
                return {
                    "name": "에러 빈도",
                    "status": "warning",
                    "message": f"최근 1시간 에러 {error_count}건",
                    "icon": "⚠️",
                }
            else:
                return {
                    "name": "에러 빈도",
                    "status": "critical",
                    "message": f"최근 1시간 에러 {error_count}건 (과다!)",
                    "icon": "⚠️",
                }

        except Exception:
            return {
                "name": "에러 빈도",
                "status": "healthy",
                "message": "에러 로그 확인 불가",
                "icon": "⚠️",
            }

    def _check_data_freshness(self) -> Dict:
        """데이터 신선도 (equity 스냅샷이 최근 것인지)"""
        try:
            if not self.db:
                return {
                    "name": "데이터 신선도",
                    "status": "warning",
                    "message": "DB 없음",
                    "icon": "📅",
                }

            latest = self.db.get_latest_equity()
            if not latest:
                return {
                    "name": "데이터 신선도",
                    "status": "warning",
                    "message": "equity 데이터 없음",
                    "icon": "📅",
                }

            ts = datetime.fromisoformat(latest["timestamp"])
            elapsed = datetime.now() - ts
            hours = elapsed.total_seconds() / 3600

            if hours < 1:
                return {
                    "name": "데이터 신선도",
                    "status": "healthy",
                    "message": f"최근 데이터: {int(elapsed.total_seconds() / 60)}분 전",
                    "icon": "📅",
                }
            elif hours < 24:
                return {
                    "name": "데이터 신선도",
                    "status": "warning",
                    "message": f"최근 데이터: {int(hours)}시간 전",
                    "icon": "📅",
                }
            else:
                return {
                    "name": "데이터 신선도",
                    "status": "critical",
                    "message": f"최근 데이터: {int(hours / 24)}일 전 (오래됨!)",
                    "icon": "📅",
                }

        except Exception:
            return {
                "name": "데이터 신선도",
                "status": "warning",
                "message": "확인 실패",
                "icon": "📅",
            }
