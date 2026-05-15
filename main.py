"""
=============================================================================
main.py - 퀀트봇 CLI 진입점
=============================================================================

프로그램의 시작점입니다. 명령줄에서 종목코드와 옵션을 받아
전체 분석 파이프라인을 실행합니다.

사용법:
    # 기본 실행 (미국 종목)
    python main.py AAPL

    # 한국 종목
    python main.py 005930.KS

    # 자본금과 리스크 직접 지정
    python main.py AAPL --capital 50000000 --currency KRW --risk-per-trade 0.01

    # 여러 종목 한번에
    python main.py AAPL MSFT NVDA --capital 100000 --currency USD

    # 분석 기간 지정
    python main.py AAPL --period 2y

파이프라인 흐름:
    CLI 인자 파싱 → 설정 생성 → 데이터 수집 → 기술적 분석 → 신호 생성 → 보고서 출력
=============================================================================
"""

import argparse
import sys
import os
from datetime import datetime

# 프로젝트 루트를 Python path에 추가 (상대 import 가능하게)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config.settings import (
    Settings, CapitalConfig, RiskConfig, DataConfig, TechnicalConfig
)
from collectors.price_us import PriceCollectorUS
from collectors.price_kr import PriceCollectorKR
from analyzers.technical import TechnicalAnalyzer
from reporter.html_report import HTMLReportGenerator
from utils.logger import setup_logger
from utils.market import detect_market


def parse_args():
    """
    명령줄 인자(argument) 파싱

    argparse란?
    - Python 표준 라이브러리의 CLI 인자 처리 도구
    - --name value 형태의 옵션을 자동으로 파싱해줌
    - 도움말(--help)도 자동 생성
    """
    parser = argparse.ArgumentParser(
        description="퀀트봇 - 주식 기술적 분석 도구",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
사용 예시:
  python main.py AAPL                           # Apple 분석 (기본 설정)
  python main.py 005930.KS --capital 50000000   # 삼성전자, 자본금 5천만원
  python main.py NVDA --risk-per-trade 0.01     # NVIDIA, 보수적 리스크(1%)
  python main.py AAPL MSFT --period 2y          # 여러 종목, 2년 데이터
        """
    )

    # 필수: 종목 코드 (1개 이상)
    parser.add_argument(
        "symbols",
        nargs="+",
        help="분석할 종목 코드 (예: AAPL, 005930.KS)"
    )

    # ─── 자본금 설정 ──────────────────────────────────────────────────
    capital_group = parser.add_argument_group("자본금 설정")

    capital_group.add_argument(
        "--capital",
        type=float,
        default=10_000_000,
        help="총 투자 자본금 (기본: 10,000,000)"
    )
    capital_group.add_argument(
        "--currency",
        choices=["KRW", "USD"],
        default="KRW",
        help="통화 단위 (기본: KRW)"
    )

    # ─── 리스크 설정 ──────────────────────────────────────────────────
    risk_group = parser.add_argument_group("리스크 설정")

    risk_group.add_argument(
        "--risk-per-trade",
        type=float,
        default=0.02,
        help="1회 거래당 최대 손실 비율 (기본: 0.02 = 2%%)"
    )
    risk_group.add_argument(
        "--max-position",
        type=float,
        default=0.10,
        help="단일 종목 최대 포지션 크기 (기본: 0.10 = 10%%)"
    )
    risk_group.add_argument(
        "--max-drawdown",
        type=float,
        default=0.15,
        help="최대 낙폭 한도 (기본: 0.15 = 15%%)"
    )
    risk_group.add_argument(
        "--stop-loss-atr",
        type=float,
        default=2.0,
        help="손절 ATR 배수 (기본: 2.0)"
    )

    # ─── 분석 설정 ────────────────────────────────────────────────────
    analysis_group = parser.add_argument_group("분석 설정")

    analysis_group.add_argument(
        "--period",
        default="1y",
        choices=["1mo", "3mo", "6mo", "1y", "2y", "5y"],
        help="분석 데이터 기간 (기본: 1y)"
    )
    analysis_group.add_argument(
        "--output-dir",
        default="reports",
        help="보고서 저장 디렉토리 (기본: reports/)"
    )

    # ─── 기타 ─────────────────────────────────────────────────────────
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="상세 로그 출력 (DEBUG 레벨)"
    )

    return parser.parse_args()


def analyze_symbol(
    symbol: str,
    settings: Settings,
    period: str,
    output_dir: str,
    logger
) -> dict:
    """
    단일 종목 분석 파이프라인 실행

    흐름: 데이터 수집 → 기술적 지표 계산 → 신호 생성 → 보고서 생성

    Parameters:
        symbol: 종목 코드
        settings: 설정 객체
        period: 데이터 기간
        output_dir: 보고서 저장 디렉토리
        logger: 로거

    Returns:
        분석 결과 딕셔너리
    """
    market = detect_market(symbol)
    logger.info(f"{'='*50}")
    logger.info(f"종목: {symbol} | 시장: {market}")
    logger.info(f"{'='*50}")

    # ─── Step 1: 데이터 수집 ──────────────────────────────────────────
    logger.info("[1/4] 주가 데이터 수집 중...")

    if market == "US":
        collector = PriceCollectorUS()
        price_df = collector.safe_collect(symbol, period=period)
        info = collector.get_info(symbol)
    else:
        collector = PriceCollectorKR()
        price_df = collector.safe_collect(symbol, period=period)
        info = None  # 한국 종목 info는 추후 구현

    if price_df is None or price_df.empty:
        logger.error(f"[실패] '{symbol}' 데이터를 가져올 수 없습니다.")
        return {"symbol": symbol, "status": "failed", "reason": "데이터 수집 실패"}

    logger.info(f"       수집 완료: {len(price_df)}일 데이터")

    # ─── Step 2: 기술적 지표 계산 ─────────────────────────────────────
    logger.info("[2/4] 기술적 지표 계산 중...")

    analyzer = TechnicalAnalyzer(settings.technical)
    df_analyzed = analyzer.calculate_all(price_df)

    logger.info(f"       계산 완료: {len(df_analyzed.columns)}개 지표")

    # ─── Step 3: 매매 신호 생성 ───────────────────────────────────────
    logger.info("[3/4] 매매 신호 생성 중...")

    signal = analyzer.generate_signal(df_analyzed)

    # 신호 색상 표시
    signal_icons = {"BUY": "▲ 매수", "SELL": "▼ 매도", "HOLD": "■ 관망"}
    logger.info(f"       신호: {signal_icons.get(signal.signal, signal.signal)}"
                f" (강도: {signal.strength:.0%})")
    for reason in signal.reasons:
        logger.info(f"         - {reason}")

    # ─── Step 4: 보고서 생성 ──────────────────────────────────────────
    logger.info("[4/4] HTML 보고서 생성 중...")

    reporter = HTMLReportGenerator(settings=settings)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = os.path.join(output_dir, f"{symbol}_{timestamp}.html")

    report_path = reporter.generate(
        symbol=symbol,
        df=df_analyzed,
        signal=signal,
        info=info,
        output_path=output_path
    )

    logger.info(f"       보고서 저장: {report_path}")

    return {
        "symbol": symbol,
        "market": market,
        "status": "success",
        "signal": signal.signal,
        "strength": signal.strength,
        "reasons": signal.reasons,
        "report_path": report_path,
        "latest_price": df_analyzed["Close"].iloc[-1],
        "rsi": df_analyzed["RSI"].iloc[-1] if "RSI" in df_analyzed.columns else None,
    }


def print_summary(results: list, settings: Settings):
    """분석 결과 요약 출력"""
    print("\n")
    print("=" * 60)
    print("                 퀀트봇 분석 결과 요약")
    print("=" * 60)
    print(f"  자본금: {settings.capital.total_capital:,.0f} {settings.capital.currency}")
    print(f"  1회 리스크: {settings.risk.risk_per_trade*100:.1f}%"
          f" ({settings.capital.total_capital * settings.risk.risk_per_trade:,.0f}"
          f" {settings.capital.currency})")
    print(f"  최대 낙폭 한도: {settings.risk.max_drawdown*100:.0f}%")
    print("-" * 60)

    for r in results:
        if r["status"] == "success":
            signal_mark = {"BUY": "[매수]", "SELL": "[매도]", "HOLD": "[관망]"}
            mark = signal_mark.get(r["signal"], "[???]")
            rsi_str = f"{r['rsi']:>5.1f}" if r['rsi'] is not None else "  N/A"
            print(f"  {mark:6s} {r['symbol']:12s} "
                  f"가격: {r['latest_price']:>10.2f}  "
                  f"RSI: {rsi_str}  "
                  f"강도: {r['strength']:.0%}")
        else:
            print(f"  [실패] {r['symbol']:12s} - {r.get('reason', '알 수 없음')}")

    print("-" * 60)
    print(f"  보고서 위치: {results[0].get('report_path', 'N/A')}"
          if results else "  결과 없음")
    print("=" * 60)
    print()


def main():
    """메인 실행 함수"""
    args = parse_args()

    # 로거 설정
    log_level = "DEBUG" if args.verbose else "INFO"
    logger = setup_logger(level=log_level)

    # ─── 설정 생성 (CLI 인자로 오버라이드) ────────────────────────────
    settings = Settings(
        capital=CapitalConfig(
            total_capital=args.capital,
            currency=args.currency
        ),
        risk=RiskConfig(
            risk_per_trade=args.risk_per_trade,
            max_position_size=args.max_position,
            max_drawdown=args.max_drawdown,
            stop_loss_atr_multiplier=args.stop_loss_atr
        ),
        data=DataConfig(),
        technical=TechnicalConfig()
    )

    # 설정 요약 출력
    logger.info(settings.summary())

    # ─── 종목별 분석 실행 ─────────────────────────────────────────────
    results = []
    for symbol in args.symbols:
        try:
            result = analyze_symbol(
                symbol=symbol.upper(),
                settings=settings,
                period=args.period,
                output_dir=args.output_dir,
                logger=logger
            )
            results.append(result)
        except Exception as e:
            logger.error(f"[치명적 오류] {symbol}: {type(e).__name__}: {e}")
            results.append({
                "symbol": symbol,
                "status": "failed",
                "reason": str(e)
            })

    # ─── 결과 요약 출력 ───────────────────────────────────────────────
    print_summary(results, settings)

    # 성공한 보고서가 있으면 첫 번째 보고서를 브라우저로 열기
    success_results = [r for r in results if r["status"] == "success"]
    if success_results:
        report_path = success_results[0]["report_path"]
        try:
            import webbrowser
            webbrowser.open(os.path.abspath(report_path))
            logger.info(f"브라우저에서 보고서를 엽니다: {report_path}")
        except Exception:
            logger.info(f"보고서를 수동으로 열어주세요: {report_path}")


if __name__ == "__main__":
    main()
