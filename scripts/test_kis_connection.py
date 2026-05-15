"""
=============================================================================
KIS API 연결 테스트 스크립트
=============================================================================
.env에 입력한 KIS 자격증명이 올바른지 안전하게 검증합니다.

⚠️ 보안: 키 값을 절대 출력하지 않고 길이/유효성만 표시합니다.

사용법:
    python scripts/test_kis_connection.py
=============================================================================
"""

import os
import sys
from pathlib import Path

# ─── 프로젝트 루트 PATH 추가 ───
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ─── .env 로드 ───
try:
    from dotenv import load_dotenv
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        print(f"❌ .env 파일이 없습니다: {env_path}")
        sys.exit(1)
    load_dotenv(env_path)
except ImportError:
    print("❌ python-dotenv 미설치. pip install python-dotenv")
    sys.exit(1)


def mask(value: str, show_chars: int = 4) -> str:
    """키 값을 안전하게 마스킹 (앞 4자리만 표시)"""
    if not value:
        return "(비어있음)"
    if len(value) <= show_chars:
        return "*" * len(value)
    return value[:show_chars] + "*" * (len(value) - show_chars - 4) + value[-4:]


def main():
    print("=" * 65)
    print("  KIS API 연결 테스트")
    print("=" * 65)

    # ─── 1. .env 변수 확인 ───
    app_key = os.environ.get("KIS_APP_KEY", "")
    app_secret = os.environ.get("KIS_APP_SECRET", "")
    account = os.environ.get("KIS_ACCOUNT", "")
    paper_str = os.environ.get("KIS_PAPER", "true").lower()
    paper = paper_str in ("true", "1", "yes")

    print("\n[1] 환경변수 확인")
    print(f"  KIS_APP_KEY     : {mask(app_key)} (길이: {len(app_key)})")
    print(f"  KIS_APP_SECRET  : {mask(app_secret)} (길이: {len(app_secret)})")
    print(f"  KIS_ACCOUNT     : {account if account else '(비어있음)'}")
    print(f"  KIS_PAPER       : {paper} ({'모의투자' if paper else '실거래'})")

    if not all([app_key, app_secret, account]):
        print("\n❌ 필수 값이 비어있습니다. .env 파일을 확인하세요.")
        return False

    # 계좌번호 형식 검증
    if "-" not in account:
        print(f"\n⚠️ 계좌번호 형식이 잘못됨: {account}")
        print("   올바른 형식: '12345678-01' (8자리-2자리)")
        return False

    cano, prdt = account.split("-", 1)
    if len(cano) != 8 or not cano.isdigit():
        print(f"\n⚠️ 계좌번호 앞 8자리가 숫자가 아닙니다: {cano}")
        return False
    if len(prdt) != 2 or not prdt.isdigit():
        print(f"\n⚠️ 계좌번호 뒤 2자리가 숫자가 아닙니다: {prdt}")
        return False

    print("\n  ✅ 형식 검증 통과")

    # ─── 2. KIS Executor로 연결 테스트 ───
    print("\n[2] OAuth2 토큰 발급 테스트")
    print(f"  서버: {'모의투자' if paper else '실거래'}")

    try:
        from executor.kis_executor import KISExecutor
        executor = KISExecutor(paper=paper)
        success = executor.connect()

        if not success:
            print("\n❌ 토큰 발급 실패")
            print("   가능한 원인:")
            print("   - APP KEY/SECRET 오타")
            print("   - 모의투자/실거래 키 불일치 (KIS_PAPER 설정 확인)")
            print("   - API 신청 미승인 상태")
            return False

        print(f"  ✅ 토큰 발급 성공 (만료: {executor.token_expires})")

    except Exception as e:
        print(f"\n❌ 연결 오류: {type(e).__name__}: {e}")
        return False

    # ─── 3. 계좌 조회 테스트 (간단한 권한 검증) ───
    print("\n[3] 계좌 정보 조회 테스트")
    try:
        account_info = executor.get_account()
        if account_info.total_equity > 0 or account_info.cash > 0:
            print(f"  ✅ 계좌 조회 성공")
            print(f"    총 평가금액: ₩{account_info.total_equity:,.0f}")
            print(f"    예수금     : ₩{account_info.cash:,.0f}")
            print(f"    유가증권   : ₩{account_info.positions_value:,.0f}")
        else:
            print(f"  ⚠️ 계좌가 비어있습니다 (모의투자는 보통 1억원 자동 지급)")
            print(f"    총 평가금액: ₩{account_info.total_equity:,.0f}")
            print(f"    예수금     : ₩{account_info.cash:,.0f}")
    except Exception as e:
        print(f"  ❌ 계좌 조회 실패: {e}")
        return False

    # ─── 4. 실시간 시세 조회 테스트 (삼성전자) ───
    print("\n[4] 실시간 시세 조회 테스트 (삼성전자 005930)")
    try:
        import requests
        url = f"{executor.base_url}/uapi/domestic-stock/v1/quotations/inquire-price"
        headers = executor._get_headers("FHKST01010100")
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": "005930",
        }
        response = requests.get(url, headers=headers, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get("rt_cd") == "0":
                output = data.get("output", {})
                price = output.get("stck_prpr", "0")
                change = output.get("prdy_vrss", "0")
                change_pct = output.get("prdy_ctrt", "0")
                print(f"  ✅ 시세 조회 성공")
                print(f"    삼성전자 현재가: ₩{int(price):,}")
                print(f"    전일 대비    : ₩{int(change):,} ({change_pct}%)")
            else:
                print(f"  ❌ API 오류: {data.get('msg1', '')}")
                return False
        else:
            print(f"  ❌ HTTP 오류: {response.status_code}")
            return False
    except Exception as e:
        print(f"  ❌ 시세 조회 실패: {e}")
        return False

    print("\n" + "=" * 65)
    print("  ✅ 모든 테스트 통과! KIS API 연동 준비 완료")
    print("=" * 65)
    return True


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
