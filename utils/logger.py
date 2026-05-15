"""
=============================================================================
utils/logger.py - 로깅 설정
=============================================================================

프로그램 전체에서 사용하는 로깅(logging) 설정을 통일합니다.

로깅이란?
- print()와 비슷하지만 더 체계적인 메시지 출력 시스템
- 레벨별 필터링 가능 (DEBUG < INFO < WARNING < ERROR < CRITICAL)
- 파일과 콘솔에 동시 출력 가능
- 타임스탬프, 모듈명 등 메타정보 자동 포함

왜 print() 대신 logging을 쓰나?
- print()는 나중에 지우기 어렵고, 레벨 구분이 없음
- logging은 설정 하나로 전체 출력 레벨을 조절할 수 있음
- 예: 개발 중에는 DEBUG, 실제 운용 시에는 WARNING만 표시
=============================================================================
"""

import logging
import sys
from datetime import datetime


def setup_logger(
    name: str = "quantbot",
    level: str = "INFO",
    log_file: bool = False,
    backup_count: int = 14,
) -> logging.Logger:
    """
    로거를 설정하고 반환

    Parameters:
        name: 로거 이름
        level: 로깅 레벨 ("DEBUG", "INFO", "WARNING", "ERROR")
        log_file: True이면 파일에도 기록 (자동 회전)
        backup_count: 보관할 과거 로그 파일 수 (기본 14일)

    Returns:
        설정된 Logger 객체

    ★ Phase 7 변경: 매일 자정에 자동 회전 + N일 후 자동 삭제
       이렇게 안 하면 1분 간격 분석 시 로그가 무제한 누적되어 디스크 풀.
    """
    logger = logging.getLogger(name)

    # 이미 핸들러가 있으면 중복 추가 방지
    if logger.handlers:
        return logger

    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # ★ 루트 로거로 전파 방지 → 중복 출력 제거
    # logging 모듈은 기본적으로 부모 로거에게도 메시지를 전달(propagate)합니다.
    # Flask/werkzeug가 루트 로거에 핸들러를 등록하면 같은 메시지가 2번 출력됩니다.
    # propagate=False로 설정하면 이 로거의 핸들러만 사용합니다.
    logger.propagate = False

    # 포맷 설정: [시간] [레벨] [모듈명] 메시지
    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%H:%M:%S"
    )

    # 콘솔 출력 핸들러
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # ── 파일 출력 핸들러 (자동 회전) ──
    if log_file:
        import os
        from logging.handlers import TimedRotatingFileHandler
        os.makedirs("logs", exist_ok=True)
        log_path = "logs/quantbot.log"
        # when='midnight': 매일 자정에 회전
        # backupCount=14: 14일치 보관 후 자동 삭제 (디스크 풀 방지)
        file_handler = TimedRotatingFileHandler(
            log_path,
            when="midnight",
            interval=1,
            backupCount=backup_count,
            encoding="utf-8",
            utc=False,
        )
        # 회전된 파일은 quantbot.log.2026-05-15 형태로 저장됨
        file_handler.suffix = "%Y-%m-%d"
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger
