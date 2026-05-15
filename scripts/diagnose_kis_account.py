"""
=============================================================================
scripts/diagnose_kis_account.py - KIS 계좌 잔고 문제 진단
=============================================================================

실거래 연결은 됐는데 현금이 안 보이거나 매수가 안 될 때 사용합니다.
KIS API 응답을 그대로 출력하여 어느 단계에서 막히는지 확인합니다.

실행:
    python scripts/diagnose_kis_account.py
=============================================================================
"""

import os
import sys
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def mask(value: str, show: int = 4) -> str:
    if not value:
        return "(비어있음)"
    if len(value) <= show:
        return "*" * len(value)
    return value[:show] + "*" * (len(value) - show - 4) + value[-4:]


def main():
    # ─── .env / API_KEYS.txt 로드 ───
    try:
        from dotenv import load_dotenv
        env_path = PROJECT_ROOT / ".env"
        if env_path.exists():
            load_dotenv(env_path)
    except ImportError:
        pass

    # API_KEYS.txt 우선 적용
    keys_path = PROJECT_ROOT / "API_KEYS.txt"
    if keys_path.exists():
        with open(keys_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip("'\"")
                if value and all(c.isalnum() or c == "_" for c in key):
                    os.environ[key] = value

    app_key = os.environ.get("KIS_APP_KEY", "")
    app_secret = os.environ.get("KIS_APP_SECRET", "")
    account = os.environ.get("KIS_ACCOUNT", "")
    paper = os.environ.get("KIS_PAPER", "true").lower() in ("true", "1", "yes")

    print("=" * 70)
    print("                KIS 계좌 잔고 진단")
    print("=" * 70)
    print()

    # ─── 1. 환경변수 확인 ───
    print("[1] 환경변수 확인")
    print(f"  KIS_APP_KEY    : {mask(app_key)} ({len(app_key)}자)")
    print(f"  KIS_APP_SECRET : {mask(app_secret)} ({len(app_secret)}자)")
    print(f"  KIS_ACCOUNT    : {account}")
    print(f"  KIS_PAPER      : {paper} ({'모의투자' if paper else '실거래'})")
    print()

    if not all([app_key, app_secret, account]):
        print("  ❌ 필수 값 누락. API_KEYS.txt를 확인하세요.")
        return False

    # ── 계좌번호 분리 (KISExecutor와 동일한 lenient 파싱) ──
    # 허용 형식: "44501321-01", "4450132101", "44501321"
    cleaned = "".join(c for c in account.strip() if c.isdigit() or c == "-")
    if "-" in cleaned:
        parts = cleaned.split("-", 1)
        cano, prdt = parts[0], (parts[1] if len(parts) > 1 and parts[1] else "01")
        print(f"  → 형식: 하이픈 분리 ('{account}')")
    elif len(cleaned) == 10:
        cano, prdt = cleaned[:8], cleaned[8:]
        print(f"  → 형식: 10자리 자동 분리 ('{account}' → '{cano}-{prdt}')")
    elif len(cleaned) == 8:
        cano, prdt = cleaned, "01"
        print(f"  → 형식: 8자리 + 기본 PRDT ('{account}' → '{cano}-01')")
    else:
        cano, prdt = cleaned, "01"
        print(f"  ⚠️ 비표준 길이 ({len(cleaned)}자) — KIS 잔고 조회가 실패할 수 있습니다")
        print("     권장: '12345678-01' (8자리-2자리) 또는 '1234567801' (10자리)")
    print(f"  → CANO (앞 8자리): {cano}")
    print(f"  → ACNT_PRDT_CD (뒤 2자리, 상품코드): {prdt}")

    if not (cano.isdigit() and len(cano) == 8):
        print(f"  ⚠️ CANO가 8자리 숫자가 아닙니다: '{cano}'")
        print("     한국투자증권 앱에서 정확한 계좌번호를 확인하세요.")

    print()
    print("  💡 상품 코드 의미 (한국투자증권 기준):")
    print("     01 = 종합매매계좌 (주식 거래 가능)")
    print("     22 = 연금저축계좌 (API 매매 제한)")
    print("     29 = ISA (API 매매 제한)")
    print("     기타 = 별도 확인 필요")
    if prdt != "01":
        print(f"  ⚠️ 상품코드가 '{prdt}'입니다. '01'이 아니면 매매가 안 될 수 있습니다.")
    print()

    # ─── 2. KIS 연결 + 토큰 ───
    print("[2] KIS API 연결")
    try:
        from executor.kis_executor import KISExecutor
        ex = KISExecutor(paper=paper)
        if not ex.connect():
            print("  ❌ 토큰 발급 실패")
            return False
        print(f"  ✅ 토큰 발급 성공 ({'모의' if paper else '실거래'} 서버)")
        print(f"     서버: {ex.base_url}")
    except Exception as e:
        print(f"  ❌ 연결 실패: {e}")
        return False
    print()

    # ─── 3. 잔고 API 직접 호출 ───
    print("[3] 잔고 조회 API 직접 호출 (응답 원본 표시)")
    import requests

    tr_id = "VTTC8434R" if paper else "TTTC8434R"
    url = f"{ex.base_url}/uapi/domestic-stock/v1/trading/inquire-balance"

    params = {
        "CANO": cano,
        "ACNT_PRDT_CD": prdt,
        "AFHR_FLPR_YN": "N",
        "OFL_YN": "",
        "INQR_DVSN": "02",
        "UNPR_DVSN": "01",
        "FUND_STTL_ICLD_YN": "N",
        "FNCG_AMT_AUTO_RDPT_YN": "N",
        "PRCS_DVSN": "01",
        "CTX_AREA_FK100": "",
        "CTX_AREA_NK100": "",
    }
    headers = ex._get_headers(tr_id)

    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        print(f"  HTTP 상태: {response.status_code}")

        if response.status_code != 200:
            print(f"  ❌ HTTP 오류")
            print(f"  응답 내용: {response.text[:500]}")
            return False

        data = response.json()
        rt_cd = data.get("rt_cd", "")
        msg = data.get("msg1", "")
        print(f"  rt_cd: {rt_cd}  (0=성공, 0이 아니면 오류)")
        print(f"  msg1 : {msg}")

        if rt_cd != "0":
            print()
            print("  ❌ KIS API 오류")
            print()
            print("  자주 나오는 오류 원인:")
            print("  • 'EGW00121': 계좌번호 또는 상품코드 잘못")
            print("  • 'EGW00133': 토큰 발급 횟수 초과 (1분 대기)")
            print("  • 'EGW00201': 실전투자 API 신청 안 됨 → 한국투자 앱에서 신청")
            print("  • 'OPSP0007': 비밀번호 오류 (HTS 비밀번호 필요)")
            return False

        # ─── 4. 응답 분석 ───
        print()
        print("[4] 응답 데이터 분석")
        output1 = data.get("output1", [])  # 종목별 보유
        output2 = data.get("output2", [])  # 계좌 요약

        print(f"  보유 종목 수: {len(output1)}개")
        if output1:
            print(f"  보유 종목 미리보기 (최대 3개):")
            for i, item in enumerate(output1[:3]):
                print(
                    f"    {i+1}. {item.get('pdno', '?')} "
                    f"{item.get('prdt_name', '?')} "
                    f"수량 {item.get('hldg_qty', '0')}"
                )

        print()
        print("  계좌 요약 (output2):")
        if not output2:
            print("  ⚠️ output2가 비어있음 → KIS 응답 이상")
            print(f"  전체 응답: {json.dumps(data, ensure_ascii=False, indent=2)[:800]}")
            return False

        summary = output2[0]
        print("  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        for key in [
            "dnca_tot_amt",      # 예수금 총액
            "tot_evlu_amt",      # 총평가금액
            "nass_amt",          # 순자산금액 (매수가능액 근사)
            "scts_evlu_amt",     # 유가증권평가액
            "evlu_pfls_smtl_amt", # 평가손익 합계
            "prvs_rcdl_excc_amt", # 가수도정산금액
        ]:
            val = summary.get(key, "(없음)")
            try:
                fval = float(val)
                print(f"    {key:>28} = {fval:>15,.0f}원")
            except (ValueError, TypeError):
                print(f"    {key:>28} = {val}")
        print("  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        # ─── 5. 진단 ───
        print()
        print("[5] 진단 결과")
        cash = float(summary.get("dnca_tot_amt", 0))
        total = float(summary.get("tot_evlu_amt", 0))
        buying_power = float(summary.get("nass_amt", 0))

        if cash == 0 and total == 0:
            print("  ❌ 모든 금액이 0원")
            if paper:
                print("  → 모의투자 계좌 신청 안 했거나 활성화 미완료")
                print("     한국투자 앱 → '모의투자' 메뉴 → 신청 → 1억원 자동 입금")
            else:
                print("  → 가능한 원인 (확률 순):")
                print()
                print("  ① 계좌번호 오타 (가장 흔함)")
                print(f"     현재 입력값: {account}")
                print("     → 한국투자 앱에서 실제 계좌번호 다시 확인")
                print("     → 형식: 앞8자리-뒤2자리 (예: 12345678-01)")
                print("     → 10자리 계좌면 앞 8자리에 하이픈 뒤 2자리")
                print()
                print("  ② 다른 상품 코드 계좌 (-22, -29 등)")
                print("     → 종합매매계좌(-01)에 입금 필요")
                print()
                print("  ③ 입금 안 됨")
                print("     → 한국투자 앱에서 입금 또는 다른 계좌에서 이체")
                print()
                print("  ④ 실전투자 API 신청 미완료")
                print("     → 한국투자 앱 → 'Open API' → 실전투자 신청")
        elif cash == 0 and total > 0:
            print("  ⚠️ 예수금(현금) 0원, 보유 주식만 있음")
            print("     → 새 매수가 불가능합니다. 계좌에 입금 또는 일부 매도 필요.")
        elif buying_power == 0:
            print("  ⚠️ 매수가능액 0원 (예수금은 있지만 묶여있음)")
            print("     → 미체결 주문이 있거나 일부 자금이 잠겨있을 수 있음")
        else:
            print(f"  ✅ 정상 — 예수금 {cash:,.0f}원, 매수가능 {buying_power:,.0f}원")
            print()
            print("  봇이 현금을 못 본다면 다음을 확인:")
            print("  1. 봇 재시작 (KIS_PAPER 변경 후 반드시 재시작)")
            print("  2. 대시보드 → 설정 → 브로커 = 'KIS 한국투자증권'")
            print("  3. 대시보드 → 'LIVE' 배지 표시 여부")

        return True

    except Exception as e:
        print(f"  ❌ 호출 실패: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    main()
    print()
    print("=" * 70)
    input("Enter 키를 누르면 종료...")
