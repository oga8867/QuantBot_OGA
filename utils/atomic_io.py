"""
=============================================================================
utils/atomic_io.py - 원자적(atomic) 파일 쓰기 유틸리티
=============================================================================

실거래 금융 봇에서 설정·토큰·DB 파일이 쓰기 도중 크래시/전원차단으로
손상되면 안 됩니다. 이 모듈은 "temp 파일에 다 쓴 후 rename"으로
중간 상태를 외부에서 절대 볼 수 없게 보장합니다.

POSIX와 Windows 모두에서 os.replace()는 원자적입니다.

사용:
    from utils.atomic_io import atomic_write_text, atomic_write_json

    atomic_write_text("/path/to/file.txt", "content")
    atomic_write_json("/path/to/settings.json", {"key": "value"})
=============================================================================
"""

import os
import json
import tempfile
import logging
import threading
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 같은 경로에 대한 동시 쓰기를 직렬화 (서로 다른 프로세스 보호는 별도 필요)
_path_locks: dict = {}
_path_locks_lock = threading.Lock()


def _get_path_lock(path: str) -> threading.Lock:
    """파일별 락 (같은 파일에 대한 동시 쓰기 직렬화)"""
    abs_path = os.path.abspath(path)
    with _path_locks_lock:
        if abs_path not in _path_locks:
            _path_locks[abs_path] = threading.Lock()
        return _path_locks[abs_path]


def atomic_write_text(path: str, content: str, encoding: str = "utf-8") -> None:
    """
    텍스트 파일을 원자적으로 씁니다.

    동작:
      1. 같은 디렉토리에 임시파일 생성 (rename은 같은 파일시스템에서만 원자적)
      2. 내용 작성 + fsync (디스크 flush)
      3. os.replace()로 원본 덮어쓰기 (원자적)

    크래시·전원차단 시 보장:
      - 옛 파일이 통째로 남거나
      - 새 파일이 완전히 적용되거나
      - 둘 중 하나. **중간 상태(빈 파일/잘린 파일)는 절대 외부에 보이지 않음.**
    """
    abs_path = os.path.abspath(path)
    dir_name = os.path.dirname(abs_path) or "."
    os.makedirs(dir_name, exist_ok=True)

    with _get_path_lock(abs_path):
        # delete=False: 우리가 직접 rename하므로 자동 삭제 금지
        # 같은 디렉토리에 만들어야 rename이 원자적
        fd, tmp_path = tempfile.mkstemp(
            prefix=".tmp_",
            suffix=os.path.basename(abs_path),
            dir=dir_name,
        )
        try:
            with os.fdopen(fd, "w", encoding=encoding, newline="") as f:
                f.write(content)
                f.flush()
                try:
                    os.fsync(f.fileno())  # 디스크에 강제 flush
                except (OSError, AttributeError):
                    pass  # 일부 환경에서 fsync 미지원

            # 원자적 rename (POSIX + Windows 모두 지원)
            os.replace(tmp_path, abs_path)
            tmp_path = None  # 성공 → 정리 불필요
        except Exception as e:
            logger.error(f"[atomic_write] 쓰기 실패 {path}: {e}")
            raise
        finally:
            # 실패 시 임시파일 정리
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass


def atomic_write_json(path: str, data: Any, indent: int = 2,
                       ensure_ascii: bool = False) -> None:
    """JSON 직렬화 + 원자적 쓰기"""
    content = json.dumps(data, ensure_ascii=ensure_ascii, indent=indent, default=str)
    atomic_write_text(path, content + "\n")


def safe_load_json(path: str, default: Optional[Any] = None,
                    backup_on_corruption: bool = True) -> Any:
    """
    JSON 파일을 안전하게 로드합니다.

    파일이 손상되면 .corrupted-{timestamp} 로 백업 후 default 반환.
    실거래 봇이 손상된 설정으로 silent fallback되는 것을 막기 위해 사용합니다.

    Returns:
        파싱된 JSON 객체 또는 default
    """
    if not os.path.exists(path):
        return default

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f"[atomic_io] JSON 손상 감지 {path}: {e}")
        if backup_on_corruption:
            from datetime import datetime
            backup = f"{path}.corrupted-{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            try:
                os.rename(path, backup)
                logger.error(f"[atomic_io] 손상 파일을 {backup}로 백업")
            except OSError as rename_err:
                logger.error(f"[atomic_io] 백업 실패: {rename_err}")
        return default
    except OSError as e:
        logger.error(f"[atomic_io] 파일 읽기 실패 {path}: {e}")
        return default
