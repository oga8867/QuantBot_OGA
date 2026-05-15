"""
=============================================================================
collectors/dart.py - DART 전자공시 수집기
=============================================================================

DART(Data Analysis, Retrieval and Transfer System)란?
- 금융감독원이 운영하는 전자공시 시스템
- 모든 상장사의 공시(사업보고서, 분기보고서, 주요사항보고서 등)가 여기 올라옴
- 공시 = 기업이 의무적으로 공개하는 경영/재무 정보

투자에서 공시의 중요성:
- 공시는 주가에 직접적 영향을 미치는 1차 정보원
- 예: 유상증자 공시 → 주가 하락 가능, 자사주 매입 공시 → 주가 상승 가능
- 뉴스보다 빠르고 정확 (뉴스는 공시를 바탕으로 쓰여짐)

이 모듈의 역할:
1. DART Open API로 최신 공시 목록 가져오기
2. 공시 유형별 분류 (호재/악재 자동 판별)
3. 관심 종목의 공시만 필터링
4. 간단한 요약 텍스트 생성

API 키 발급 (무료):
- https://opendart.fss.or.kr/ 에서 회원가입 후 API 키 발급
- config.yaml의 dart_api_key에 입력

공시 유형 코드 (주요):
- A001: 사업보고서        - A002: 반기보고서
- A003: 분기보고서        - B001: 주요사항보고서
- B002: 발행공시          - B003: 지분공시
- C001: 외부감사관련      - D001: 기금운용보고서
- E001: 거래소공시        - F001: 공정위공시
- I001: 기타공시

핵심 API 엔드포인트:
- /api/list.json → 최근 공시 목록 (날짜/기업/유형/제목)
- /api/company.json → 기업 기본 정보 조회
=============================================================================
"""

import requests
import json
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from .base import BaseCollector


# ═══════════════════════════════════════════════════════════════════════════
# 공시 키워드 기반 호재/악재 분류 사전
# ═══════════════════════════════════════════════════════════════════════════
#
# 공시 제목에 포함된 키워드로 투자에 미치는 영향을 자동 판단합니다.
# impact: 양수 = 호재(주가 상승 요인), 음수 = 악재(주가 하락 요인)
# 0.0~1.0 범위로 정규화됩니다.

DISCLOSURE_KEYWORDS = {
    # ── 호재 키워드 (주가 상승 요인) ──
    "자기주식취득": 0.7,       # 자사주 매입 → 주당 가치 상승
    "자사주매입": 0.7,
    "자기주식처분": -0.3,      # 자사주 매도 → 물량 부담
    "배당": 0.5,               # 배당 공시 → 주주환원
    "무상증자": 0.6,           # 무상증자 → 주주 친화적 (실질 가치 변동 없으나 긍정 시그널)
    "영업이익증가": 0.8,
    "흑자전환": 0.9,           # 적자 → 흑자 = 강한 호재
    "매출증가": 0.6,
    "수주": 0.5,               # 신규 수주 = 매출 파이프라인 확보
    "계약체결": 0.5,
    "합병": 0.3,               # 합병은 상황에 따라 다르지만 평균적으로 약한 호재
    "인수": 0.4,
    "신규사업": 0.4,
    "특허": 0.5,               # 기술 경쟁력 확보
    "상향": 0.4,               # 목표가 상향 등
    "최대실적": 0.8,

    # ── 악재 키워드 (주가 하락 요인) ──
    "유상증자": -0.8,          # 유상증자 → 지분 희석, 대표적 악재
    "전환사채": -0.6,          # CB 발행 → 향후 지분 희석 우려
    "신주인수권": -0.5,        # BW 발행 → 지분 희석 가능성
    "감자": -0.7,              # 자본 감소 → 기업 재무 위기 시그널
    "적자": -0.6,
    "영업손실": -0.6,
    "손실": -0.4,
    "매출감소": -0.5,
    "하향": -0.4,              # 실적 하향 조정
    "소송": -0.3,
    "횡령": -0.8,              # 경영진 리스크 = 강한 악재
    "배임": -0.8,
    "상장폐지": -1.0,          # 최악의 악재
    "관리종목": -0.9,
    "불성실공시": -0.5,
    "조회공시": -0.2,          # 조회공시 자체는 중립~약한 악재 (루머 확인)
    "정정": -0.1,              # 정정공시는 약한 부정 시그널
}


class DARTCollector(BaseCollector):
    """
    DART 전자공시 수집기
    
    OpenDART API를 통해 최신 공시를 가져오고,
    관심 종목의 공시를 필터링하여 호재/악재를 판별합니다.
    
    속성:
        api_key (str): DART Open API 인증 키
        base_url (str): API 기본 URL
    """

    def __init__(self, api_key: str = ""):
        """
        Args:
            api_key: DART Open API 키 (없으면 기능 제한)
                     https://opendart.fss.or.kr/ 에서 무료 발급
        """
        super().__init__("DART")
        self.api_key = api_key
        self.base_url = "https://opendart.fss.or.kr"

    def collect(self, symbols: Optional[List[str]] = None,
                days: int = 7, **kwargs) -> Dict[str, Any]:
        """
        최근 공시 수집 (메인 진입점)

        Args:
            symbols: 관심 종목 코드 리스트 (예: ["005930", "035720"])
                     None이면 전체 공시
            days: 몇 일 전부터 조회할지 (기본 7일)

        Returns:
            {
                "disclosures": [...],    # 공시 목록
                "summary": "...",        # 요약 텍스트
                "positive_count": int,   # 호재 공시 수
                "negative_count": int,   # 악재 공시 수
                "neutral_count": int,    # 중립 공시 수
            }
        """
        if not self.api_key:
            self.logger.warning("DART API 키 미설정 → 공시 수집 건너뜀")
            return self._empty_result("API 키 미설정")

        # 날짜 범위 설정
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")

        disclosures = self._fetch_disclosures(start_date, end_date)

        if not disclosures:
            return self._empty_result("공시 없음")

        # 관심 종목 필터링 (종목 코드가 주어진 경우)
        if symbols:
            # 한국 종목은 보통 "005930.KS" 형태 → 코드만 추출
            clean_codes = [s.split(".")[0] for s in symbols]
            disclosures = [
                d for d in disclosures
                if d.get("stock_code", "").strip() in clean_codes
            ]

        # 각 공시에 호재/악재 점수 부여
        for d in disclosures:
            d["impact_score"] = self._calc_impact(d.get("report_nm", ""))
            d["impact_label"] = (
                "호재" if d["impact_score"] > 0.2
                else "악재" if d["impact_score"] < -0.2
                else "중립"
            )

        # 통계 계산
        positive = [d for d in disclosures if d["impact_label"] == "호재"]
        negative = [d for d in disclosures if d["impact_label"] == "악재"]
        neutral = [d for d in disclosures if d["impact_label"] == "중립"]

        # 요약 텍스트 생성
        summary = self._build_summary(disclosures, positive, negative)

        return {
            "disclosures": disclosures[:100],  # 최대 100건
            "summary": summary,
            "positive_count": len(positive),
            "negative_count": len(negative),
            "neutral_count": len(neutral),
            "total_count": len(disclosures),
        }

    def _fetch_disclosures(self, start_date: str, end_date: str) -> List[Dict]:
        """
        DART Open API로 공시 목록 조회

        API 호출 흐름:
        1. GET /api/list.json?crtfc_key=...&bgn_de=...&end_de=...
        2. 응답의 list 배열에서 각 공시 정보 파싱

        API 응답 필드:
        - corp_code: 기업 고유번호 (8자리)
        - corp_name: 기업명
        - stock_code: 종목코드 (6자리)
        - report_nm: 공시 제목
        - rcept_dt: 접수일 (YYYYMMDD)
        - flr_nm: 공시 제출인명
        
        Args:
            start_date: 시작일 (YYYYMMDD)
            end_date: 종료일 (YYYYMMDD)
            
        Returns:
            공시 딕셔너리 리스트
        """
        try:
            url = f"{self.base_url}/api/list.json"
            params = {
                "crtfc_key": self.api_key,
                "bgn_de": start_date,
                "end_de": end_date,
                "page_count": 100,      # 한 번에 100건 (최대)
                "sort": "date",         # 날짜순 정렬
                "sort_mth": "desc",     # 최신순
            }

            response = requests.get(url, params=params, timeout=10)
            data = response.json()

            # DART API 상태 코드:
            # "000" = 정상, "010" = 등록된 키 아님, "013" = 요청 제한 초과
            if data.get("status") != "000":
                self.logger.warning(f"DART API 오류: {data.get('message', 'Unknown')}")
                return []

            return data.get("list", [])

        except requests.RequestException as e:
            self.logger.error(f"DART API 요청 실패: {e}")
            return []
        except (json.JSONDecodeError, KeyError) as e:
            self.logger.error(f"DART API 응답 파싱 실패: {e}")
            return []

    def _calc_impact(self, title: str) -> float:
        """
        공시 제목으로 호재/악재 점수 계산

        키워드 매칭 방식:
        - DISCLOSURE_KEYWORDS 사전에서 제목에 포함된 키워드를 찾음
        - 여러 키워드가 매칭되면 점수를 합산 후 -1~1로 클리핑
        
        예시:
        - "삼성전자 자기주식취득 결정" → "자기주식취득" 매칭 → +0.7
        - "OO기업 유상증자 결정" → "유상증자" 매칭 → -0.8
        - "분기보고서 정정" → "정정" 매칭 → -0.1
        
        Args:
            title: 공시 제목 문자열
            
        Returns:
            -1.0 ~ 1.0 사이의 영향 점수
        """
        if not title:
            return 0.0

        score = 0.0
        matches = 0
        for keyword, impact in DISCLOSURE_KEYWORDS.items():
            if keyword in title:
                score += impact
                matches += 1

        # 여러 키워드가 매칭되었으면 평균 → 극단값 방지
        if matches > 1:
            score = score / matches

        # -1 ~ 1 범위로 클리핑
        return max(-1.0, min(1.0, score))

    def _build_summary(self, all_disc: List[Dict],
                       positive: List[Dict], negative: List[Dict]) -> str:
        """
        공시 요약 텍스트 생성

        주요 호재/악재 공시를 모아서 브리핑 형태로 만듭니다.
        이 텍스트는 대시보드와 리포트에서 사용됩니다.

        Args:
            all_disc: 전체 공시 리스트
            positive: 호재 공시 리스트
            negative: 악재 공시 리스트

        Returns:
            요약 텍스트 문자열
        """
        lines = []
        lines.append(f"[DART 공시 요약] 총 {len(all_disc)}건 "
                      f"(호재 {len(positive)}건 / 악재 {len(negative)}건)")

        if positive:
            lines.append("\n📈 주요 호재:")
            for d in positive[:5]:  # 상위 5건
                lines.append(f"  • {d.get('corp_name', '?')} - {d.get('report_nm', '?')} "
                             f"({d.get('rcept_dt', '')})")

        if negative:
            lines.append("\n📉 주요 악재:")
            for d in negative[:5]:  # 상위 5건
                lines.append(f"  • {d.get('corp_name', '?')} - {d.get('report_nm', '?')} "
                             f"({d.get('rcept_dt', '')})")

        return "\n".join(lines)

    def _empty_result(self, reason: str) -> Dict[str, Any]:
        """빈 결과 반환 (API 키 없거나 공시 없을 때)"""
        return {
            "disclosures": [],
            "summary": f"[DART] {reason}",
            "positive_count": 0,
            "negative_count": 0,
            "neutral_count": 0,
            "total_count": 0,
        }

    def get_company_info(self, corp_code: str) -> Optional[Dict]:
        """
        기업 기본 정보 조회
        
        DART에서 기업의 기본 정보(업종, 대표자, 설립일 등)를 가져옵니다.
        팩터 분석이나 밸류에이션에 보조 데이터로 활용 가능합니다.
        
        Args:
            corp_code: DART 기업 고유번호 (8자리)
            
        Returns:
            기업 정보 딕셔너리 또는 None
        """
        if not self.api_key:
            return None

        try:
            url = f"{self.base_url}/api/company.json"
            params = {
                "crtfc_key": self.api_key,
                "corp_code": corp_code,
            }
            response = requests.get(url, params=params, timeout=10)
            data = response.json()

            if data.get("status") == "000":
                return data
            return None

        except Exception as e:
            self.logger.error(f"기업 정보 조회 실패: {e}")
            return None
