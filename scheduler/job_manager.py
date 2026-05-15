"""
=============================================================================
scheduler/job_manager.py - 작업 스케줄러
=============================================================================

APScheduler를 사용하여 분석/매매 작업을 자동으로 예약 실행합니다.

스케줄링이란?
- 특정 시간에 자동으로 프로그램을 실행하는 것
- 예: 매일 아침 9시에 분석 실행, 매 시간마다 포지션 체크

APScheduler란?
- Advanced Python Scheduler
- Python에서 가장 많이 쓰이는 스케줄링 라이브러리
- cron 표현식, 인터벌, 특정 시각 등 다양한 방식 지원
- pip install apscheduler

일정 계획:
- 06:00: 거시경제 데이터 업데이트
- 08:30: 한국 장 시작 전 분석
- 09:00: 한국 장 시작 - 매매 신호
- 15:30: 한국 장 마감 - 결산
- 22:00: 미국 장 시작 전 분석
- 22:30: 미국 장 시작 - 매매 신호
=============================================================================
"""

import os
import sys
from typing import Callable, Optional, Dict, List
from datetime import datetime, time
from dataclasses import dataclass, field

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger
    APSCHEDULER_AVAILABLE = True
except ImportError:
    APSCHEDULER_AVAILABLE = False


@dataclass
class ScheduledJob:
    """스케줄 작업 정의"""
    name: str                    # 작업 이름
    func: Callable              # 실행할 함수
    trigger_type: str           # "cron" 또는 "interval"
    trigger_args: Dict = field(default_factory=dict)  # 트리거 파라미터
    enabled: bool = True        # 활성화 여부


class JobManager:
    """
    작업 스케줄 관리자

    사용법:
        manager = JobManager()

        # 매일 오전 9시에 한국 분석 실행
        manager.add_cron_job("kr_morning", analyze_kr, hour=9, minute=0)

        # 30분마다 포지션 체크
        manager.add_interval_job("check_positions", check_func, minutes=30)

        manager.start()
    """

    def __init__(self):
        """스케줄러 초기화"""
        self.scheduler = None
        self.jobs: Dict[str, ScheduledJob] = {}

        if APSCHEDULER_AVAILABLE:
            self.scheduler = BackgroundScheduler(
                timezone="Asia/Seoul"  # 한국 시간 기준
            )
        else:
            print("[경고] APScheduler 미설치: pip install apscheduler")

    def add_cron_job(
        self,
        name: str,
        func: Callable,
        **cron_args
    ) -> bool:
        """
        Cron 방식 작업 추가 (특정 시각에 실행)

        Parameters:
            name: 작업 이름 (고유해야 함)
            func: 실행할 함수
            **cron_args: cron 파라미터
                - hour: 시 (0~23)
                - minute: 분 (0~59)
                - day_of_week: 요일 (mon-fri = 평일만)
                - day: 일 (1~31)

        예시:
            # 평일 오전 9시
            add_cron_job("morning", func, hour=9, minute=0, day_of_week="mon-fri")

            # 매월 1일 자정
            add_cron_job("monthly", func, day=1, hour=0)

        Returns:
            추가 성공 여부
        """
        if not self.scheduler:
            return False

        job = ScheduledJob(
            name=name,
            func=func,
            trigger_type="cron",
            trigger_args=cron_args
        )
        self.jobs[name] = job

        self.scheduler.add_job(
            func,
            trigger=CronTrigger(**cron_args),
            id=name,
            name=name,
            replace_existing=True
        )
        return True

    def add_interval_job(
        self,
        name: str,
        func: Callable,
        **interval_args
    ) -> bool:
        """
        인터벌 방식 작업 추가 (N분/시간마다 실행)

        Parameters:
            name: 작업 이름
            func: 실행할 함수
            **interval_args:
                - minutes: N분마다
                - hours: N시간마다
                - seconds: N초마다

        예시:
            add_interval_job("check", func, minutes=30)  # 30분마다

        Returns:
            추가 성공 여부
        """
        if not self.scheduler:
            return False

        job = ScheduledJob(
            name=name,
            func=func,
            trigger_type="interval",
            trigger_args=interval_args
        )
        self.jobs[name] = job

        self.scheduler.add_job(
            func,
            trigger=IntervalTrigger(**interval_args),
            id=name,
            name=name,
            replace_existing=True
        )
        return True

    def remove_job(self, name: str) -> bool:
        """작업 제거"""
        if self.scheduler and name in self.jobs:
            try:
                self.scheduler.remove_job(name)
                del self.jobs[name]
                return True
            except Exception:
                return False
        return False

    def start(self):
        """스케줄러 시작 (백그라운드 실행)"""
        if self.scheduler and not self.scheduler.running:
            self.scheduler.start()
            print(f"[스케줄러] 시작됨 - {len(self.jobs)}개 작업 등록")

    def stop(self):
        """스케줄러 중지"""
        if self.scheduler and self.scheduler.running:
            self.scheduler.shutdown()
            print("[스케줄러] 중지됨")

    def get_status(self) -> List[Dict]:
        """등록된 작업 상태 조회"""
        status = []
        for name, job in self.jobs.items():
            status.append({
                "name": name,
                "trigger": job.trigger_type,
                "args": job.trigger_args,
                "enabled": job.enabled,
            })
        return status

    def setup_default_schedule(
        self,
        analyze_kr_func: Optional[Callable] = None,
        analyze_us_func: Optional[Callable] = None,
        risk_check_func: Optional[Callable] = None,
    ):
        """
        기본 스케줄 설정 (퀀트봇 표준 일정)

        Parameters:
            analyze_kr_func: 한국 시장 분석 함수
            analyze_us_func: 미국 시장 분석 함수
            risk_check_func: 리스크 체크 함수
        """
        if analyze_kr_func:
            # 한국 장 시작 전 분석 (평일 08:30)
            self.add_cron_job(
                "kr_pre_market",
                analyze_kr_func,
                hour=8, minute=30, day_of_week="mon-fri"
            )

        if analyze_us_func:
            # 미국 장 시작 전 분석 (평일 22:00 KST)
            self.add_cron_job(
                "us_pre_market",
                analyze_us_func,
                hour=22, minute=0, day_of_week="mon-fri"
            )

        if risk_check_func:
            # 30분마다 리스크 체크 (장 중에만)
            self.add_interval_job(
                "risk_monitor",
                risk_check_func,
                minutes=30
            )

        print(f"[스케줄러] 기본 일정 설정 완료: {len(self.jobs)}개 작업")
