/**
 * =============================================================================
 * main.js - 퀀트봇 대시보드 프론트엔드 로직
 * =============================================================================
 *
 * WebSocket으로 서버와 실시간 통신하며, UI를 업데이트합니다.
 *
 * 구조:
 *  1. 다국어 (i18n) 시스템
 *  2. WebSocket 연결 & 이벤트 핸들링
 *  3. UI 업데이트 함수들
 *  4. API 호출 함수들 (REST)
 *  5. 차트 관리
 *  6. 유틸리티
 * =============================================================================
 */

// ═══════════════════════════════════════════════════════════════════════════
// 1. 다국어 (i18n) 시스템
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 현재 언어 (localStorage에 저장하여 새로고침 후에도 유지)
 * 주의: localStorage는 브라우저에서 사용 가능
 */
let currentLang = "ko";
try { currentLang = localStorage.getItem("lang") || "ko"; } catch(e) {}

/**
 * 번역 사전
 * key: data-i18n 속성값
 * value: { ko: 한국어, en: English }
 */
const i18n = {
    // 탭
    tab_overview: { ko: "개요", en: "Overview" },
    tab_trades: { ko: "거래 이력", en: "Trades" },
    tab_settings: { ko: "설정", en: "Settings" },
    tab_analyze: { ko: "분석", en: "Analyze" },
    tab_guide: { ko: "가이드", en: "Guide" },

    // 버튼
    btn_start: { ko: "봇 시작", en: "Start Bot" },
    btn_stop: { ko: "봇 중지", en: "Stop Bot" },
    btn_reset: { ko: "초기화", en: "Reset" },
    btn_refresh: { ko: "새로고침", en: "Refresh" },
    btn_save: { ko: "설정 저장", en: "Save Settings" },
    btn_add: { ko: "추가", en: "Add" },
    btn_analyze: { ko: "분석 실행", en: "Analyze" },

    // 모드 패널
    mode_paper_title: { ko: "모의매매", en: "Paper Trading" },
    mode_paper_desc: { ko: "모의매매 모드 - 가상 자본으로 시뮬레이션", en: "Paper trading mode - simulating with virtual capital" },
    mode_live_title: { ko: "실거래", en: "Live Trading" },
    mode_live_desc: { ko: "실거래 모드 - 실제 돈으로 매매 중", en: "Live trading mode - trading with real money" },

    // KPI 카드
    kpi_equity: { ko: "총 자산", en: "Total Equity" },
    kpi_cash: { ko: "현금", en: "Cash" },
    kpi_daily_pnl: { ko: "오늘 수익", en: "Today's P&L" },
    kpi_winrate: { ko: "승률", en: "Win Rate" },
    kpi_available: { ko: "사용 가능", en: "Available" },

    // 툴팁
    tip_equity: { ko: "현금 + 보유 주식 평가액의 합계입니다. 봇이 관리하는 전체 자산 가치를 나타냅니다.", en: "Sum of cash + stock value. Total portfolio value managed by the bot." },
    tip_cash: { ko: "아직 투자되지 않은 현금입니다. 새로운 매수에 사용할 수 있는 가용 자금입니다.", en: "Uninvested cash available for new purchases." },
    tip_daily_pnl: { ko: "오늘 하루 동안의 수익/손실입니다. 장 마감 후 최종 확정됩니다.", en: "Today's profit/loss. Finalized after market close." },
    tip_winrate: { ko: "수익으로 끝난 거래의 비율입니다. 50% 이상이면 절반 넘게 이기고 있다는 뜻입니다. 단, 승률만으로는 전략의 좋고 나쁨을 판단할 수 없습니다.", en: "Percentage of profitable trades. Above 50% means winning more than half. But win rate alone doesn't determine strategy quality." },
    tip_equity_curve: { ko: "시간에 따른 총 자산 변화 그래프입니다. 우상향이면 수익, 하락하면 손실 구간입니다.", en: "Graph showing portfolio value over time. Uptrend = profit, downtrend = loss periods." },

    // 차트 & 섹션
    chart_equity: { ko: "자산 변화 추이", en: "Equity Curve" },
    positions_title: { ko: "보유 포지션", en: "Positions" },
    signals_title: { ko: "최근 신호", en: "Recent Signals" },
    trades_title: { ko: "거래 이력", en: "Trade History" },
    analyze_title: { ko: "즉시 분석", en: "Quick Analysis" },

    // 빈 상태
    empty_positions: { ko: "보유 중인 종목이 없습니다", en: "No open positions" },
    empty_signals: { ko: "아직 신호가 없습니다. 봇을 시작하거나 분석을 실행하세요.", en: "No signals yet - start the bot or run analysis" },
    empty_trades: { ko: "아직 거래가 없습니다", en: "No trades yet" },
    empty_analyze: { ko: "종목을 입력하고 분석 실행을 누르세요", en: "Enter a symbol and click Analyze" },

    // 테이블 헤더
    th_time: { ko: "시간", en: "Time" },
    th_symbol: { ko: "종목", en: "Symbol" },
    th_side: { ko: "매매", en: "Side" },
    th_qty: { ko: "수량", en: "Qty" },
    th_price: { ko: "가격", en: "Price" },
    th_total: { ko: "총액", en: "Total" },
    th_strategy: { ko: "전략", en: "Strategy" },

    // 분석 탭 (Analyze)
    label_market: { ko: "시장", en: "Market" },
    label_search_stock: { ko: "종목 검색", en: "Search Stock" },
    label_interval: { ko: "분석 간격", en: "Analysis Interval" },
    label_kr_schedule: { ko: "한국 시장 시간", en: "KR Market Hours" },
    label_us_schedule: { ko: "미국 시장 시간", en: "US Market Hours" },
    settings_schedule_title: { ko: "거래 스케줄", en: "Trading Schedule" },
    tip_interval: { ko: "봇이 시장을 분석하는 주기입니다. 짧을수록 빈번하게 신호를 체크하지만, 너무 짧으면 노이즈가 많아집니다. 일반적으로 1시간 이상을 권장합니다.", en: "How often the bot analyzes the market. Shorter = more frequent checks but more noise. 1 hour or longer recommended." },
    tip_kr_schedule: { ko: "한국 시장(KOSPI/KOSDAQ) 매매 시간입니다. 정규장: 09:00~15:30. 이 시간 외에는 봇이 한국 주식을 거래하지 않습니다.", en: "Korean market trading hours. Regular: 09:00-15:30. Bot won't trade KR stocks outside these hours." },
    tip_us_schedule: { ko: "미국 시장(NYSE/NASDAQ) 매매 시간입니다. 한국시간 기준 23:30~06:00 (서머타임: 22:30~05:00). 이 시간 외에는 봇이 미국 주식을 거래하지 않습니다.", en: "US market trading hours. KST: 23:30-06:00 (DST: 22:30-05:00). Bot won't trade US stocks outside these hours." },
    placeholder_search: { ko: "종목명 또는 코드 검색...", en: "Search by name or symbol..." },
    analyze_market_all: { ko: "전체", en: "All" },
    analyze_market_us: { ko: "🇺🇸 미국", en: "🇺🇸 US" },
    analyze_market_kr: { ko: "🇰🇷 한국", en: "🇰🇷 KR" },
    analysis_sector: { ko: "섹터", en: "Sector" },
    analysis_volume: { ko: "거래량", en: "Volume" },
    analysis_52w_range: { ko: "52주 범위", en: "52W Range" },
    analysis_price_change: { ko: "등락률", en: "Price Change" },

    // 스캐너 (주목할 종목)
    scanner_title: { ko: "주목할 종목", en: "Market Scanner" },
    scanner_desc: { ko: "관심 분야에서 현재 매매 신호가 강한 종목들입니다", en: "Top stocks by signal strength from your selected sectors" },
    empty_scanner: { ko: "스캔 버튼을 눌러 주목할 종목을 찾아보세요", en: "Click Scan to find noteworthy stocks" },
    btn_scan: { ko: "스캔", en: "Scan" },
    scanner_scanning: { ko: "스캔 중...", en: "Scanning..." },
    scanner_scanned_at: { ko: "마지막 스캔", en: "Last scan" },

    // 종목 모달
    modal_news_title: { ko: "관련 뉴스 + AI 분석", en: "Related News + AI Analysis" },
    modal_news_loading: { ko: "뉴스 로딩 중...", en: "Loading news..." },

    // 실시간 활동 피드
    activity_title: { ko: "봇 활동", en: "Bot Activity" },
    activity_desc: { ko: "봇의 분석, 매수, 매도 활동을 실시간으로 표시합니다", en: "Shows bot analysis, buy, and sell activity in real-time" },
    empty_activity: { ko: "봇을 시작하면 활동이 여기에 표시됩니다", en: "Start the bot to see activity here" },
    trade_buy_toast: { ko: "매수 체결", en: "BUY Executed" },
    trade_sell_toast: { ko: "매도 체결", en: "SELL Executed" },

    // 관심 분야 설정
    settings_sectors_title: { ko: "관심 분야", en: "Interest Sectors" },
    settings_sectors_desc: { ko: "선택한 분야의 종목들을 메인 화면에서 스캔하여 주목할 종목을 보여줍니다", en: "Scans stocks from selected sectors and shows top picks on Overview" },

    // 설정 섹션
    settings_broker_title: { ko: "브로커 & 모드", en: "Broker & Mode" },
    settings_capital_title: { ko: "자본 & 리스크 관리", en: "Capital & Risk Management" },
    settings_watchlist_title: { ko: "감시 종목 (Watchlist)", en: "Watchlist" },
    settings_notify_title: { ko: "알림 설정", en: "Notifications" },

    // 설정 라벨
    label_broker: { ko: "브로커", en: "Broker" },
    label_mode: { ko: "매매 모드", en: "Trading Mode" },
    label_capital: { ko: "총 자본금", en: "Total Capital" },
    label_currency: { ko: "통화", en: "Currency" },
    label_sizing: { ko: "포지션 사이징", en: "Position Sizing" },
    label_risk_per_trade: { ko: "거래당 리스크 (%)", en: "Risk per Trade (%)" },
    label_max_position: { ko: "최대 포지션 (%)", en: "Max Position (%)" },
    label_max_drawdown: { ko: "최대 낙폭 한도 (%)", en: "Max Drawdown (%)" },
    label_stop_loss: { ko: "손절 ATR 배수", en: "Stop Loss ATR Mult" },
    label_rr: { ko: "손익비 (R:R)", en: "Risk:Reward Ratio" },
    label_kelly: { ko: "켈리 비율", en: "Kelly Fraction" },
    label_max_daily: { ko: "일일 최대 손실 (%)", en: "Max Daily Loss (%)" },
    label_us_stocks: { ko: "미국 주식", en: "US Stocks" },
    label_kr_stocks: { ko: "한국 주식", en: "KR Stocks" },
    label_tg_token: { ko: "텔레그램 봇 토큰", en: "Telegram Bot Token" },
    label_tg_chat: { ko: "텔레그램 채팅 ID", en: "Telegram Chat ID" },

    // 설정 툴팁
    tip_broker: { ko: "매매를 실행할 증권사를 선택합니다. Paper는 가상매매, Alpaca는 미국 주식, KIS는 한국 주식을 실제로 거래합니다.", en: "Select the broker for trade execution. Paper = simulated, Alpaca = US stocks, KIS = Korean stocks." },
    tip_mode: { ko: "Paper: 가상 돈으로 연습. Live: 실제 돈으로 매매. Live 전환 시 반드시 소액으로 시작하세요!", en: "Paper: practice with virtual money. Live: real money trades. Always start small with Live!" },
    tip_capital: { ko: "봇이 운용할 전체 자본금입니다. 이 금액을 기준으로 포지션 크기와 리스크를 계산합니다.", en: "Total capital the bot will manage. Position sizes and risk are calculated from this amount." },
    tip_sizing: { ko: "한 번 매수할 때 얼마나 살지를 결정하는 방법입니다. Kelly는 승률과 손익비를 고려한 최적 비율, ATR은 변동성 기반, Fixed는 고정 금액입니다.", en: "Method for deciding how much to buy. Kelly = optimal based on win rate, ATR = volatility-based, Fixed = constant amount." },
    tip_risk_per_trade: { ko: "한 번의 거래에서 최대 잃을 수 있는 금액 비율입니다. 2%면 1000만원 중 최대 20만원까지만 손실을 허용합니다. 전문가는 보통 1~3%를 사용합니다.", en: "Max loss per trade as % of capital. 2% of $100k = $2k max loss per trade. Pros use 1-3%." },
    tip_max_position: { ko: "한 종목에 투자할 수 있는 최대 비율입니다. 10%면 전체 자본의 10%까지만 한 종목에 넣습니다. 분산투자를 강제하는 장치입니다.", en: "Max allocation to a single stock. 10% = at most 10% of capital in one stock. Forces diversification." },
    tip_max_drawdown: { ko: "전체 자산이 고점 대비 이만큼 떨어지면 봇을 자동 중단합니다. 15%면 1000만원→850만원이 되면 모든 거래를 멈춥니다. 파산 방지 장치입니다.", en: "If portfolio drops this much from peak, bot auto-stops. 15% of $100k = stops at $85k. Prevents ruin." },
    tip_stop_loss: { ko: "손절선을 ATR(평균 변동폭)의 몇 배로 설정할지입니다. 2.0이면 ATR×2 아래에 손절선을 놓습니다. 높을수록 넓은 손절(덜 빈번하게 걸림), 낮을수록 타이트한 손절입니다.", en: "Stop-loss distance in ATR multiples. 2.0 = stop at entry - 2×ATR. Higher = wider stops (fewer triggers)." },
    tip_rr: { ko: "목표 수익 대 손실의 비율입니다. 2.0이면 1만원 잃을 각오로 2만원 수익을 노립니다. 높을수록 한번 이길 때 크게 벌지만 승률이 낮아질 수 있습니다.", en: "Target profit vs loss ratio. 2.0 = risking $1 to make $2. Higher = bigger wins but lower win rate." },
    tip_kelly: { ko: "Kelly Criterion의 보수적 배수입니다. 0.5 = Half Kelly (권장). 풀 켈리(1.0)는 이론상 최적이지만 변동이 심해서, 절반(0.5)을 쓰면 안정적입니다.", en: "Conservative Kelly multiplier. 0.5 = Half Kelly (recommended). Full Kelly (1.0) is too volatile in practice." },
    tip_max_daily: { ko: "하루에 이 이상 잃으면 그날은 더 이상 거래하지 않습니다. 감정적 매매를 방지하는 자동 브레이크입니다.", en: "If daily loss exceeds this, no more trades for the day. Auto-brake against emotional trading." },
    tip_tg_token: { ko: "@BotFather에서 봇을 만들면 받는 토큰입니다. 매매 신호, 체결, 리스크 알림을 텔레그램으로 보내줍니다.", en: "Token from @BotFather. Sends trade signals, executions, and risk alerts to Telegram." },
    tip_tg_chat: { ko: "알림을 받을 채팅방 ID입니다. @userinfobot 에게 메시지를 보내면 확인할 수 있습니다.", en: "Chat ID to receive alerts. Send a message to @userinfobot to find your ID." },
    label_discord: { ko: "디스코드 웹훅 URL", en: "Discord Webhook URL" },
    tip_discord: { ko: "디스코드 채널 설정 → 연동 → 웹훅에서 URL을 복사하세요. 매매 신호, 체결, 리스크 경고를 디스코드로 받을 수 있습니다.", en: "Copy URL from Discord channel settings → Integrations → Webhooks. Sends signals, executions, and risk alerts." },
    btn_test: { ko: "테스트", en: "Test" },

    // 가이드 탭 (한글은 HTML에 이미 있으므로 영어만 필요)
    guide_basics_title: { ko: "기본 개념", en: "Basic Concepts" },
    guide_paper_title: { ko: "모의매매 (Paper Trading)", en: "Paper Trading" },
    guide_paper_desc: { ko: "실제 돈을 사용하지 않고 가상 자본으로 매매를 시뮬레이션합니다. 전략이 실제로 돈을 벌 수 있는지 검증하는 필수 단계입니다. 최소 2~4주 이상 모의매매를 한 뒤에 실거래를 고려하세요.", en: "Simulates trading with virtual capital without using real money. Essential step to verify if a strategy can actually make money. Run paper trading for at least 2-4 weeks before considering live trading." },
    guide_signal_title: { ko: "매매 신호 (Signal)", en: "Trading Signal" },
    guide_signal_desc: { ko: "기술적 지표(RSI, MACD, 볼린저밴드 등)를 종합 분석하여 BUY(매수), SELL(매도), HOLD(관망) 중 하나를 결정합니다. 강도(Strength)는 0~1 사이 값으로, 높을수록 확신이 큽니다. 기본적으로 강도 0.5 이상일 때만 매수를 실행합니다.", en: "Combines technical indicators (RSI, MACD, Bollinger Bands, etc.) to decide BUY, SELL, or HOLD. Strength is 0-1, higher = more confident. Default: only buys above 0.5 strength." },
    guide_equity_title: { ko: "자산 곡선 (Equity Curve)", en: "Equity Curve" },
    guide_equity_desc: { ko: "시간에 따라 내 전체 자산(현금 + 주식)이 어떻게 변했는지 보여주는 그래프입니다. 이상적인 모양은 꾸준히 우상향하면서 큰 하락 없이 올라가는 것입니다. 급격한 하락 구간이 MDD(최대 낙폭)가 됩니다.", en: "Graph showing how your total assets (cash + stocks) changed over time. Ideal shape: steady uptrend without large drops. Sharp decline periods become MDD." },

    guide_risk_title: { ko: "리스크 관리", en: "Risk Management" },
    guide_kelly_desc: { ko: "수학적으로 최적인 베팅 비율을 계산하는 공식입니다. 승률과 손익비를 넣으면 \"자본의 몇 %를 걸어야 장기적으로 가장 빠르게 자산이 늘어나는가\"를 알려줍니다.", en: "Formula that calculates the mathematically optimal bet size. Given win rate and payoff ratio, tells you \"what % of capital to bet for fastest long-term growth.\"" },
    guide_kelly_detail: { ko: "W = 승률, R = 평균이익/평균손실. 예: 승률 60%, 손익비 2:1이면 Kelly = 0.6 - 0.4/2 = 0.4 (40%). 하지만 풀 켈리는 변동이 극심해서 Half Kelly(절반)를 사용합니다. 이 봇에서는 기본 0.5(Half Kelly)로 설정되어 있습니다.", en: "W = win rate, R = avg win/avg loss. Example: 60% win rate, 2:1 payoff → Kelly = 0.6 - 0.4/2 = 0.4 (40%). Full Kelly is too volatile, so we use Half Kelly (0.5) by default." },
    guide_atr_desc: { ko: "주가가 하루에 평균적으로 얼마나 움직이는지를 나타내는 지표입니다. 예를 들어 ATR이 2,000원이면 그 주식은 하루에 보통 2,000원 폭으로 오르내립니다. 이걸 손절선 계산에 사용합니다.", en: "Measures how much a stock typically moves in one day. If ATR = $2, the stock usually moves $2 up or down per day. Used to calculate stop-loss distance." },
    guide_atr_stoploss: { ko: "손절 ATR 배수가 2.0이면: 손절선 = 매수가 - ATR×2. ATR이 2,000원일 때 10만원에 샀다면 손절선은 96,000원이 됩니다.", en: "If ATR multiplier = 2.0: Stop = Entry - ATR×2. With ATR=$2 and entry at $100, stop is at $96." },
    guide_mdd_desc: { ko: "고점에서 저점까지 가장 크게 떨어진 비율입니다. \"최악의 경우 얼마를 잃을 수 있나?\"에 대한 답입니다. MDD -15%면 1000만원이 850만원까지 떨어진 적이 있다는 뜻입니다.", en: "Largest peak-to-trough decline. Answers: \"What's the worst loss I could face?\" MDD of -15% means $100k dropped to $85k at some point." },
    guide_mdd_limit: { ko: "이 봇에서는 MDD 한도를 설정할 수 있습니다. 한도를 넘으면 자동으로 모든 거래를 중단하여 더 큰 손실을 방지합니다.", en: "This bot lets you set an MDD limit. If exceeded, it automatically stops all trading to prevent further losses." },
    guide_rr_title: { ko: "손익비 (Risk:Reward Ratio)", en: "Risk:Reward Ratio" },
    guide_rr_desc: { ko: "한 번 거래할 때 \"얼마를 잃을 각오로 얼마를 벌겠다\"의 비율입니다. R:R = 2.0이면 1만원 손실을 감수하고 2만원 수익을 목표로 합니다. 손익비가 높으면 승률이 낮아도 전체적으로 수익을 낼 수 있습니다.", en: "Ratio of potential profit to potential loss per trade. R:R = 2.0 means risking $1 to make $2. High R:R can be profitable even with low win rate." },
    guide_rr_example: { ko: "예시: 승률 40%여도 손익비 3:1이면 → 10번 거래 시 4번 이기며 12만원 수익, 6번 지며 6만원 손실 → 순이익 6만원", en: "Example: Even 40% win rate with 3:1 R:R → 10 trades: 4 wins × $3 = $12, 6 losses × $1 = $6 → Net profit $6" },

    guide_metrics_title: { ko: "성과 지표", en: "Performance Metrics" },
    guide_sharpe_desc: { ko: "리스크 1단위당 초과수익이 얼마인지를 측정합니다. \"같은 위험을 감수하면서 얼마나 효율적으로 돈을 벌고 있나?\"에 대한 답입니다.", en: "Measures excess return per unit of risk. Answers: \"How efficiently am I making money for the risk taken?\"" },
    guide_sharpe_rating: { ko: "해석: 0 이하 = 쓸모없음, 0~1 = 보통, 1~2 = 좋음, 2+ = 매우 우수, 3+ = 의심스러움(과적합 가능성)", en: "Rating: Below 0 = useless, 0-1 = average, 1-2 = good, 2+ = excellent, 3+ = suspicious (possible overfitting)" },
    guide_sortino_desc: { ko: "Sharpe의 개선 버전입니다. Sharpe는 상승/하락 변동 모두를 위험으로 보지만, Sortino는 하락 변동만 위험으로 봅니다. 상승은 좋은 것이니까요. 그래서 더 공정한 평가라고 봅니다.", en: "Improved Sharpe. Sharpe penalizes all volatility; Sortino only penalizes downside volatility. Upside volatility is good, so Sortino is fairer." },
    guide_pf_desc: { ko: "총 이익 ÷ 총 손실입니다. 1보다 크면 돈을 벌고 있다는 뜻입니다.", en: "Total profits ÷ Total losses. Above 1 = making money overall." },
    guide_pf_rating: { ko: "해석: 1.0 미만 = 손실, 1.0~1.5 = 약한 수익, 1.5~2.0 = 좋은 전략, 2.0+ = 매우 우수 (또는 데이터 부족/과적합 의심)", en: "Rating: Below 1.0 = losing, 1.0-1.5 = weak profit, 1.5-2.0 = good, 2.0+ = excellent (or suspect overfitting)" },
    guide_cagr_desc: { ko: "매년 평균 몇 %씩 복리로 성장했는지를 나타냅니다. 단순 수익률과 달리 복리 효과를 반영합니다.", en: "Annualized compound growth rate. Unlike simple returns, accounts for compounding." },
    guide_cagr_example: { ko: "예: 2년간 1000만→1500만이면 CAGR = 22.5% (매년 22.5% 복리로 2년 = 50% 수익)", en: "Example: $100k → $150k in 2 years → CAGR = 22.5% (22.5% compounded annually for 2 years = 50%)" },

    guide_strategy_title: { ko: "전략 구성 요소", en: "Strategy Components" },
    guide_ta_title: { ko: "기술적 분석 (Technical Analysis)", en: "Technical Analysis" },
    guide_ta_desc: { ko: "과거 주가와 거래량 패턴을 분석하여 미래 방향을 예측합니다. 이 봇은 RSI(과매수/과매도), MACD(추세 전환), 볼린저밴드(변동성 이탈), SMA/EMA(이동평균), OBV(거래량 흐름), ATR(변동성) 7개 지표를 사용합니다.", en: "Analyzes past price/volume patterns to predict future direction. This bot uses 7 indicators: RSI, MACD, Bollinger Bands, SMA/EMA, OBV, and ATR." },
    guide_ensemble_title: { ko: "앙상블 전략 (Ensemble)", en: "Ensemble Strategy" },
    guide_ensemble_desc: { ko: "여러 분석 방법의 결과를 가중치로 합산하여 최종 판단을 내립니다. 한 가지 지표만 믿으면 속을 수 있지만, 여러 지표가 동시에 같은 방향을 가리키면 신뢰도가 높아집니다. 현재 기술적 분석에 가장 높은 가중치(40%)를 부여합니다.", en: "Combines multiple analysis methods with weighted scoring for final decisions. One indicator can be wrong, but when many agree, confidence is higher. Technical analysis has highest weight (40%)." },
    guide_walkforward_title: { ko: "Walk-Forward 검증", en: "Walk-Forward Validation" },
    guide_walkforward_desc: { ko: "과거 데이터를 \"학습 구간\"과 \"테스트 구간\"으로 나누어, 학습한 전략이 미래에도 통하는지 반복 검증하는 방법입니다. 단순 백테스트보다 과적합(overfitting) 위험이 적습니다.", en: "Splits historical data into training and testing periods, repeatedly validating that a trained strategy works on unseen data. Lower overfitting risk than simple backtesting." },

    guide_howto_title: { ko: "사용 가이드", en: "How to Use" },
    guide_step1_title: { ko: "1단계: 모의매매로 시작", en: "Step 1: Start with Paper Trading" },
    guide_step1_desc: { ko: "Settings에서 Broker를 Paper로, 자본금과 리스크를 설정한 뒤 봇을 시작하세요. 2~4주간 신호 품질, 승률, MDD를 관찰합니다.", en: "Set Broker to Paper in Settings, configure capital and risk, then start the bot. Observe signal quality, win rate, and MDD for 2-4 weeks." },
    guide_step2_title: { ko: "2단계: 분석 탭으로 개별 종목 테스트", en: "Step 2: Test Individual Stocks" },
    guide_step2_desc: { ko: "Analyze 탭에서 관심 종목을 입력하면 즉시 기술적 분석 결과와 매매 신호를 보여줍니다. 다양한 종목으로 테스트하여 전략의 특성을 파악하세요.", en: "Enter stocks in the Analyze tab for instant technical analysis and signals. Test various stocks to understand the strategy's characteristics." },
    guide_step3_title: { ko: "3단계: 텔레그램 알림 연결", en: "Step 3: Connect Telegram Alerts" },
    guide_step3_desc: { ko: "Settings에서 텔레그램 토큰을 입력하면 매매 신호, 체결, 리스크 경고를 핸드폰으로 받을 수 있습니다. 컴퓨터 앞에 없어도 상황을 파악할 수 있습니다.", en: "Enter your Telegram token in Settings to receive signals, executions, and risk alerts on your phone — even when away from your computer." },
    guide_step4_title: { ko: "4단계: 소액 실거래 전환", en: "Step 4: Switch to Live (Small Amount)" },
    guide_step4_desc: { ko: "모의매매 결과가 만족스러우면 Broker를 Alpaca 또는 KIS로 변경하고 Live 모드로 전환합니다. 반드시 소액(전체 자산의 10~20%)으로 시작하세요. API 키는 각 증권사에서 발급받아야 합니다.", en: "When paper results are satisfactory, switch Broker to Alpaca or KIS and enable Live mode. ALWAYS start with a small amount (10-20% of total). API keys must be obtained from each broker." },
};

/**
 * 기술 용어 인라인 툴팁 사전
 * 대시보드 내 동적 콘텐츠(포지션 카드, 신호, 거래 이력)에서
 * 전문 용어에 마우스를 올리면 간단한 설명이 표시됩니다.
 */
const TERM_TOOLTIPS = {
    "ATR": {
        ko: "Average True Range — 주가의 하루 평균 변동폭. 손절선 계산에 사용됩니다.",
        en: "Average True Range — average daily price range, used for stop-loss calculation."
    },
    "RSI": {
        ko: "Relative Strength Index — 0~100 사이 값. 70 이상=과매수, 30 이하=과매도.",
        en: "Relative Strength Index — 0-100 scale. Above 70=overbought, below 30=oversold."
    },
    "MACD": {
        ko: "이동평균 수렴·확산 — 두 이동평균의 차이로 추세 전환을 감지합니다.",
        en: "Moving Average Convergence Divergence — detects trend changes."
    },
    "골든크로스": {
        ko: "단기 이동평균이 장기 이동평균을 위로 돌파 — 상승 전환 신호.",
        en: "Short-term MA crosses above long-term MA — bullish reversal signal."
    },
    "데드크로스": {
        ko: "단기 이동평균이 장기 이동평균을 아래로 돌파 — 하락 전환 신호.",
        en: "Short-term MA crosses below long-term MA — bearish reversal signal."
    },
    "볼린저": {
        ko: "볼린저 밴드 — 이동평균 ± 표준편차×2. 밴드 밖으로 나가면 과매수/과매도.",
        en: "Bollinger Bands — MA ± 2 std devs. Price outside = overbought/oversold."
    },
    "SMA": {
        ko: "Simple Moving Average — 단순 이동평균. N일간 종가의 단순 평균.",
        en: "Simple Moving Average — average closing price over N days."
    },
    "EMA": {
        ko: "Exponential Moving Average — 지수 이동평균. 최근 가격에 더 큰 비중.",
        en: "Exponential Moving Average — more weight on recent prices."
    },
    "Kelly": {
        ko: "Kelly Criterion — 승률과 손익비로 최적 베팅 비율을 계산. 0.5 = Half Kelly (권장).",
        en: "Kelly Criterion — optimal bet sizing from win rate. 0.5 = Half Kelly (recommended)."
    },
    "MDD": {
        ko: "Maximum Drawdown — 고점 대비 최대 하락폭. 낮을수록 안정적.",
        en: "Maximum Drawdown — largest peak-to-trough decline. Lower = more stable."
    },
    "Sharpe": {
        ko: "Sharpe Ratio — 리스크 1단위당 초과수익. 1↑ 양호, 2↑ 우수.",
        en: "Sharpe Ratio — excess return per unit of risk. >1 good, >2 excellent."
    },
    "OBV": {
        ko: "On Balance Volume — 거래량 누적 지표. 가격 상승일에 +, 하락일에 -.",
        en: "On Balance Volume — cumulative volume indicator for confirming trends."
    },
    "과매수": {
        ko: "주가가 과도하게 올라 조정(하락) 가능성이 높은 상태.",
        en: "Overbought — price has risen excessively, correction likely."
    },
    "과매도": {
        ko: "주가가 과도하게 내려 반등 가능성이 높은 상태.",
        en: "Oversold — price has fallen excessively, bounce likely."
    },
    "손익비": {
        ko: "Risk:Reward Ratio — 손실 대비 기대 수익 비율. 2:1이면 1원 리스크로 2원 노림.",
        en: "Risk:Reward Ratio — expected profit vs loss. 2:1 means risking $1 to make $2."
    },
};

/**
 * 기술 용어가 포함된 텍스트에 툴팁 <span>을 입히는 함수
 * reasons 배열의 각 항목에서 용어를 찾아 마우스 호버 시 설명이 나타나게 합니다.
 */
function wrapTermTooltips(text) {
    if (!text) return text;
    for (const [term, tips] of Object.entries(TERM_TOOLTIPS)) {
        const escapedTerm = term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        // 이미 <span> 안에 래핑된 용어는 건드리지 않도록,
        // term-tip 클래스가 포함되지 않은 경우에만 치환
        if (text.includes(`>${term}<`)) continue;  // 이미 래핑됨
        const regex = new RegExp(`(${escapedTerm})`, 'g');
        const tip = (tips[currentLang] || tips["ko"]).replace(/"/g, '&quot;');
        text = text.replace(regex, `<span class="term-tip" data-tip="${tip}">$1</span>`);
    }
    return text;
}

/**
 * 언어 전환
 */
function setLang(lang) {
    currentLang = lang;
    try { localStorage.setItem("lang", lang); } catch(e) {}

    // 버튼 활성화 표시
    document.querySelectorAll(".lang-btn").forEach(btn => {
        btn.classList.toggle("active", btn.textContent.trim() === (lang === "ko" ? "한국어" : "EN"));
    });

    // 모든 data-i18n 요소 업데이트
    applyTranslations();
}

/**
 * 번역 적용
 */
function applyTranslations() {
    document.querySelectorAll("[data-i18n]").forEach(el => {
        const key = el.getAttribute("data-i18n");
        if (i18n[key] && i18n[key][currentLang]) {
            el.textContent = i18n[key][currentLang];
        }
    });
}


// ═══════════════════════════════════════════════════════════════════════════
// 2. WebSocket 연결 & 이벤트
// ═══════════════════════════════════════════════════════════════════════════

// ── Socket.IO 연결 설정 ──
// F5 새로고침 시에도 자동 재연결되도록 reconnection 옵션 활성화
// reconnectionDelay: 첫 재연결 시도까지 1초 대기
// reconnectionDelayMax: 최대 대기 시간 5초 (지수 백오프 상한)
// reconnectionAttempts: 무한 재시도 (브라우저가 열려있는 동안 계속)
const socket = io({
    reconnection: true,
    reconnectionDelay: 1000,
    reconnectionDelayMax: 5000,
    reconnectionAttempts: Infinity,
    timeout: 10000,
});

// ── 연결 상태 이벤트 핸들링 ──
socket.on("connect", () => {
    console.log("[WS] 서버 연결됨 (sid:", socket.id, ")");
});

socket.on("disconnect", (reason) => {
    console.warn("[WS] 서버 연결 끊김:", reason);
    // 서버가 끊었을 때 (io server disconnect) 수동 재연결 필요
    if (reason === "io server disconnect") {
        socket.connect();
    }
    // 그 외(transport close, ping timeout 등)는 자동 재연결됨
});

socket.on("reconnect", (attemptNumber) => {
    console.log("[WS] 재연결 성공 (시도:", attemptNumber, ")");
    showToast(
        currentLang === "ko" ? "서버 재연결됨" : "Reconnected",
        "success"
    );
});

socket.on("reconnect_attempt", (attemptNumber) => {
    console.log("[WS] 재연결 시도 #" + attemptNumber);
});

socket.on("reconnect_error", (err) => {
    console.error("[WS] 재연결 실패:", err.message);
});

socket.on("status_update", (data) => { updateStatusUI(data); });
socket.on("settings_update", (data) => { updateSettingsUI(data); });
socket.on("analysis_result", (data) => { displayAnalysisResult(data); });
socket.on("discovery_update", (data) => {
    updateDiscoveryBar(data);
    const usCount = (data.discovered_us || []).length;
    const krCount = (data.discovered_kr || []).length;
    if (usCount + krCount > 0) {
        showToast(
            currentLang === "ko"
                ? `✅ 종목 발굴 완료 — US: ${usCount}개, KR: ${krCount}개`
                : `✅ Discovery done — US: ${usCount}, KR: ${krCount}`,
            "success"
        );
    }
});

/**
 * trade_executed 이벤트 핸들러
 *
 * 백엔드의 _status_broadcaster가 PaperExecutor.trade_history에서 새 거래를
 * 감지할 때 발생합니다. 이 이벤트를 받으면:
 * 1. 토스트 알림으로 "매수/매도 체결" 표시
 * 2. 거래 탭이 열려있으면 자동으로 테이블 새로고침
 * 3. 활동 피드에도 자동 추가됨 (bot_activity 이벤트를 통해)
 */
socket.on("trade_executed", (trade) => {
    console.log("[WS] 거래 체결:", trade);

    const isBuy = trade.side === "BUY";
    const sideText = currentLang === "ko"
        ? (isBuy ? "매수 체결" : "매도 체결")
        : (isBuy ? "BUY Executed" : "SELL Executed");
    const toastType = isBuy ? "trade" : "trade-sell";

    // 거래 체결 토스트 알림 (종목명 포함)
    const tradeName = trade.name || trade.symbol;
    showToast(
        `${sideText}: ${tradeName} ${trade.quantity}주 @ ${Number(trade.price).toLocaleString()}`,
        toastType
    );

    // 거래 탭이 현재 보이고 있으면 자동 새로고침
    const tradesSection = document.getElementById("tab-trades");
    if (tradesSection && tradesSection.style.display !== "none") {
        loadRecentTrades();
    }

    // 메인 대시보드의 "최근 거래" 위젯 갱신 (항상 갱신)
    if (typeof loadDashboardRecentTrades === "function") {
        loadDashboardRecentTrades();
    }

    // ★ 오늘 손익도 즉시 갱신 (매도 체결 시 KPI/거래이력 카드 동시 업데이트)
    if (typeof loadTodayPnl === "function") {
        loadTodayPnl();
    }
});

/**
 * bot_activity 이벤트 핸들러
 *
 * 봇의 모든 주요 활동(분석 시작, 매수, 매도, 에러 등)을 수신합니다.
 * Overview 탭의 활동 피드에 실시간으로 항목을 추가합니다.
 */
socket.on("bot_activity", (activity) => {
    addActivityToFeed(activity);
});

socket.on("connect", () => {
    console.log("[WS] Connected");
    showToast(currentLang === "ko" ? "대시보드 연결됨" : "Dashboard connected", "success");
    // 연결 시 기존 활동 로그 로드
    loadActivityLog();
});

socket.on("disconnect", () => {
    document.getElementById("statusDot").className = "status-dot";
    document.getElementById("statusText").textContent = currentLang === "ko" ? "연결 끊김" : "Disconnected";
});


// ═══════════════════════════════════════════════════════════════════════════
// 3. 탭 네비게이션
// ═══════════════════════════════════════════════════════════════════════════

document.querySelectorAll(".tab").forEach(tab => {
    tab.addEventListener("click", () => {
        document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
        document.querySelectorAll("section[id^='tab-']").forEach(s => s.style.display = "none");
        tab.classList.add("active");
        document.getElementById("tab-" + tab.dataset.tab).style.display = "block";
        if (tab.dataset.tab === "trades") { loadRecentTrades(); loadReportsList(); }
        if (tab.dataset.tab === "charts") { loadChartsTab(); }
    });
});


// ═══════════════════════════════════════════════════════════════════════════
// 4. UI 업데이트 함수들
// ═══════════════════════════════════════════════════════════════════════════

function updateStatusUI(status) {
    const dot = document.getElementById("statusDot");
    const text = document.getElementById("statusText");
    const badge = document.getElementById("modeBadge");

    const btnStart = document.getElementById("btnStart");
    const btnStop = document.getElementById("btnStop");
    const btnReset = document.getElementById("btnReset");

    if (status.running) {
        dot.className = status.live ? "status-dot live" : "status-dot active";
        text.textContent = status.live
            ? (currentLang === "ko" ? "실거래 중" : "LIVE")
            : (currentLang === "ko" ? "실행 중" : "Running");
        btnStart.disabled = true;
        // ★ 버튼 텍스트도 "시작 중..." → "봇 시작됨"으로 갱신
        btnStart.textContent = currentLang === "ko" ? "봇 시작됨" : "Bot Running";
        btnStop.disabled = false;
        btnReset.disabled = true;
    } else {
        dot.className = "status-dot";
        text.textContent = currentLang === "ko" ? "중지됨" : "Stopped";
        btnStart.disabled = false;
        // ★ 중지 시 버튼 텍스트 원복
        btnStart.textContent = currentLang === "ko" ? "봇 시작" : "Start Bot";
        btnStop.disabled = true;
        btnReset.disabled = false;
    }

    if (status.live) {
        badge.className = "badge badge-live";
        badge.textContent = "LIVE";
    } else {
        badge.className = "badge badge-paper";
        badge.textContent = "PAPER";
    }

    // 모드 패널
    const modeIcon = document.getElementById("modeIcon");
    const modeTitle = document.getElementById("modeTitle");
    const modeDesc = document.getElementById("modeDesc");

    if (status.live) {
        modeIcon.className = "mode-icon live";
        modeIcon.textContent = "🔴";
        modeTitle.textContent = i18n.mode_live_title[currentLang];
        modeDesc.textContent = i18n.mode_live_desc[currentLang];
    } else {
        modeIcon.className = "mode-icon paper";
        modeIcon.textContent = "📊";
        modeTitle.textContent = i18n.mode_paper_title[currentLang];
        modeDesc.textContent = i18n.mode_paper_desc[currentLang];
    }

    // KPI — 통화 기호는 브로커/설정 기준으로 결정 (live/paper와 무관)
    // - KIS (한국투자증권)  → ₩
    // - Alpaca (미국)       → $
    // - paper (내부 시뮬)   → status.cash가 native 단위로 저장됨, settings.currency 사용
    // - dual                → 자산 합산이 USD로 통일됨 (run_bot.py 참고)
    let currency = "₩";
    const broker = (status.mode || "").toLowerCase();
    if (broker === "alpaca" || broker === "dual") {
        currency = "$";
    } else if (broker === "kis") {
        currency = "₩";
    } else {
        // paper 또는 미설정: 설정 탭의 통화 사용
        const settingsCurrency = (status.currency || "").toUpperCase();
        currency = settingsCurrency === "USD" ? "$" : "₩";
    }
    document.getElementById("totalEquity").textContent = formatCurrency(status.total_equity, currency);
    document.getElementById("cashValue").textContent = formatCurrency(status.cash, currency);

    const pnl = status.total_pnl || 0;
    const pnlEl = document.getElementById("totalPnl");
    pnlEl.textContent = (pnl >= 0 ? "+" : "") + pnl.toFixed(2) + "%";
    pnlEl.className = pnl >= 0 ? "card-sub text-success" : "card-sub text-danger";
    document.getElementById("totalEquity").className = pnl >= 0 ? "card-value positive" : "card-value negative";

    if (status.win_rate > 0) {
        document.getElementById("winRate").textContent = (status.win_rate * 100).toFixed(1) + "%";
    }
    document.getElementById("totalTrades").textContent = (status.total_trades || 0) +
        (currentLang === "ko" ? " 거래" : " trades");

    // ── 수수료 표시 ──
    const feeEl = document.getElementById("totalFees");
    if (feeEl && status.commission) {
        const fees = status.commission.total_fees_paid || 0;
        feeEl.textContent = (currentLang === "ko" ? "수수료: " : "Fees: ") +
            "₩" + Math.round(fees).toLocaleString();
        feeEl.title = status.commission.rates
            ? `KR 왕복: ${status.commission.rates.kr_roundtrip} | US 왕복: ${status.commission.rates.us_roundtrip} | 슬리피지: ${status.commission.rates.slippage}`
            : "";
    }

    updatePositions(status.positions || {});
    updateSignals(status.signals_today || []);

    // ── 종목 발굴 상태 ──
    if (status.discovery) {
        updateDiscoveryBar(status.discovery);
    }

    // ── 자산 차트 자동 갱신 (스로틀: 30초마다 1회) ──
    const _now = Date.now();
    if (!window._lastEquityRefresh || (_now - window._lastEquityRefresh) > 30000) {
        window._lastEquityRefresh = _now;
        if (typeof loadEquityData === "function" && equityChart) {
            loadEquityData();
        }
    }
}

// ── 포지션 전역 상태 (시장 필터용) ──
let _positionsData = {};
let _positionsFilter = "all";  // "all" | "KR" | "US"

function updatePositions(positions) {
    _positionsData = positions;
    _renderPositions();
}

/**
 * filterPositions() - 시장별 포지션 필터 전환
 */
function filterPositions(market) {
    _positionsFilter = market;
    // 탭 활성화 토글
    document.querySelectorAll("#positionMarketFilter .market-filter-btn").forEach(btn => {
        btn.classList.toggle("active", btn.dataset.market === market);
    });
    _renderPositions();
}

/**
 * _renderPositions() - 포지션 렌더링 (필터 적용)
 */
function _renderPositions() {
    const container = document.getElementById("positionsList");
    const allKeys = Object.keys(_positionsData);

    // 시장 필터 적용
    const filteredKeys = allKeys.filter(symbol => {
        if (_positionsFilter === "all") return true;
        const p = _positionsData[symbol];
        const isUs = p.currency === "USD";
        return _positionsFilter === "US" ? isUs : !isUs;
    });

    // 전체 수 / 필터 수 표시
    const countEl = document.getElementById("positionCount");
    if (_positionsFilter === "all") {
        countEl.textContent = allKeys.length + (currentLang === "ko" ? " 보유" : " open");
    } else {
        countEl.textContent = `${filteredKeys.length}/${allKeys.length}` + (currentLang === "ko" ? " 보유" : " open");
    }

    if (filteredKeys.length === 0) {
        const emptyMsg = allKeys.length === 0
            ? (i18n.empty_positions[currentLang])
            : (currentLang === "ko"
                ? `${_positionsFilter === "KR" ? "한국" : "미국"} 시장 보유 종목이 없습니다`
                : `No ${_positionsFilter} positions`);
        container.innerHTML = `<div class="empty-state"><div class="empty-state-icon">📭</div><div>${emptyMsg}</div></div>`;
        return;
    }

    // "전체" 모드일 때는 시장별 그룹 헤더로 구분
    if (_positionsFilter === "all" && allKeys.length > 0) {
        const krKeys = allKeys.filter(s => _positionsData[s].currency !== "USD");
        const usKeys = allKeys.filter(s => _positionsData[s].currency === "USD");
        let html = "";
        if (krKeys.length > 0) {
            html += `<div class="market-group-header">🇰🇷 ${currentLang === "ko" ? "한국" : "Korea"} (${krKeys.length})</div>`;
            html += krKeys.map(s => _buildPositionCard(s, _positionsData[s])).join("");
        }
        if (usKeys.length > 0) {
            html += `<div class="market-group-header">🇺🇸 ${currentLang === "ko" ? "미국" : "US"} (${usKeys.length})</div>`;
            html += usKeys.map(s => _buildPositionCard(s, _positionsData[s])).join("");
        }
        container.innerHTML = html;
    } else {
        container.innerHTML = filteredKeys.map(s => _buildPositionCard(s, _positionsData[s])).join("");
    }
}

/**
 * _buildPositionCard() - 단일 포지션 카드 HTML 생성
 */
function _buildPositionCard(symbol, p) {
    const pnlClass = p.pnl >= 0 ? "text-success" : "text-danger";
    const pnlSign = p.pnl >= 0 ? "+" : "";
    const displayName = p.name || symbol;
    const isUsd = p.currency === "USD";
    const pricePrefix = isUsd ? "$" : "₩";
    const fmtPrice = (v) => isUsd
        ? v.toLocaleString(undefined, {minimumFractionDigits:2, maximumFractionDigits:2})
        : v.toLocaleString(undefined, {maximumFractionDigits:0});
    const mktValKrw = (p.market_value_krw || (p.current_price * p.shares)).toLocaleString(undefined, {maximumFractionDigits:0});

    // 포지션 유형 배지
    const posType = p.position_type || "";
    const posTypeEn = p.position_type_en || "swing";
    const typeBadge = posType
        ? `<span class="position-type-badge position-type-${posTypeEn}">${posType}</span>`
        : "";

    // 손절가/목표가 + 진행률 바 (ExitManager)
    let targetStopHtml = "";
    const stop = p.current_stop || p.stop_price || 0;
    const target1 = p.target_1 || p.target_price || 0;
    const target2 = p.target_2 || 0;

    if (stop && target1) {
        // 진입가→현재가→목표가 진행률 (current_price - avg_price) / (target1 - avg_price)
        const range = target1 - p.avg_price;
        const progress = range > 0
            ? Math.max(0, Math.min(1, (p.current_price - p.avg_price) / range)) * 100
            : 0;
        // 손절선 가까운 정도 (0=진입가, 100=손절가)
        const stopRange = p.avg_price - stop;
        const stopProgress = stopRange > 0
            ? Math.max(0, Math.min(1, (p.avg_price - p.current_price) / stopRange)) * 100
            : 0;

        const partialBadge = (p.partial_sold_pct >= 0.5)
            ? `<span style="background:rgba(46,204,113,0.2);color:#2ecc71;padding:1px 6px;border-radius:4px;font-size:9px;font-weight:600;">1차 익절완료</span>`
            : "";

        targetStopHtml = `
        <div class="position-targets" style="display:flex;flex-wrap:wrap;gap:6px;align-items:center;font-size:11px;">
            <span class="text-danger" title="손절선 (현재 ${stopProgress.toFixed(0)}% 도달)">▼ ${pricePrefix}${fmtPrice(stop)}</span>
            <span class="text-success" title="1차 목표 (현재 ${progress.toFixed(0)}% 도달)">▲ ${pricePrefix}${fmtPrice(target1)}</span>
            ${target2 ? `<span class="text-success" style="opacity:0.6;" title="2차 목표 (전량 매도)">▲▲ ${pricePrefix}${fmtPrice(target2)}</span>` : ""}
            ${partialBadge}
        </div>
        <div style="position:relative;height:4px;background:rgba(255,255,255,0.08);border-radius:2px;margin-top:4px;overflow:hidden;">
            <div style="position:absolute;left:0;top:0;height:100%;width:${progress}%;background:linear-gradient(90deg,rgba(46,204,113,0.3),rgba(46,204,113,0.7));"></div>
            <div style="position:absolute;left:0;top:0;height:100%;width:${stopProgress}%;background:linear-gradient(90deg,rgba(255,85,85,0.7),rgba(255,85,85,0.3));mix-blend-mode:normal;"></div>
        </div>`;
    }

    // 매매 이유
    let reasonsHtml = "";
    if (p.reasons && p.reasons.length > 0) {
        const reasonsText = p.reasons.slice(0, 3).join(" · ");
        reasonsHtml = `<div class="position-reasons">${wrapTermTooltips(reasonsText)}</div>`;
    }

    // 보유 기간
    const holdingPeriod = p.holding_period
        ? `<span style="font-size:10px;opacity:0.4;margin-left:4px;">${p.holding_period}</span>`
        : "";

    return `<div class="position-item clickable" onclick="showStockLinks('${symbol}', '${displayName.replace(/'/g, "\\'")}')">
        <div style="flex:1;min-width:0;">
            <div class="position-symbol">${displayName}${isUsd ? ' <span style="font-size:10px;opacity:0.5;">USD</span>' : ''}${typeBadge}${holdingPeriod}</div>
            <div class="position-detail">${p.shares}${currentLang === "ko" ? "주" : " shares"} @ ${pricePrefix}${fmtPrice(p.avg_price)}</div>
            <div class="position-detail" style="font-size:11px;opacity:0.5;">≈ ₩${mktValKrw}</div>
            ${targetStopHtml}
            ${reasonsHtml}
        </div>
        <div style="text-align:right;">
            <div class="${pnlClass}" style="font-weight:600;">${pnlSign}${(p.pnl_pct * 100).toFixed(2)}%</div>
            <div class="position-detail">${pricePrefix}${fmtPrice(p.current_price)}</div>
        </div>
    </div>`;
}

function updateSignals(signals) {
    const container = document.getElementById("signalsList");
    document.getElementById("signalCount").textContent = signals.length +
        (currentLang === "ko" ? " 오늘" : " today");

    if (signals.length === 0) {
        container.innerHTML = `<div class="empty-state"><div class="empty-state-icon">📡</div><div>${i18n.empty_signals[currentLang]}</div></div>`;
        return;
    }

    const recent = signals.slice(-10).reverse();
    container.innerHTML = recent.map(s => {
        const badgeClass = s.signal === "BUY" ? "badge-buy" : s.signal === "SELL" ? "badge-sell" : "badge-hold";
        const strengthLabel = currentLang === "ko" ? "강도" : "Strength";
        // 종목명이 있으면 "이름 (코드)" 형태로, 없으면 symbol 그대로 표시
        const sigName = s.name || s.symbol;
        // 매매 이유가 있으면 툴팁과 함께 표시
        const reasonsText = (s.reasons && s.reasons.length > 0)
            ? `<div style="font-size:11px;color:rgba(255,255,255,0.4);margin-top:2px;">${wrapTermTooltips(s.reasons.slice(0, 2).join(" · "))}</div>`
            : "";
        return `<div class="signal-item" style="flex-wrap:wrap;">
            <span class="badge ${badgeClass}">${s.signal}</span>
            <span style="font-weight:600;">${sigName}</span>
            <span class="text-mute">${strengthLabel}: ${(s.strength * 100).toFixed(0)}%</span>
            <span class="text-mute" style="margin-left:auto; font-size:12px;">${formatTime(s.timestamp)}</span>
            ${reasonsText}
        </div>`;
    }).join("");
}

function updateSettingsUI(settings) {
    document.getElementById("settingBroker").value = settings.broker || "paper";
    document.getElementById("settingCapital").value = settings.capital || 10000000;
    document.getElementById("settingCurrency").value = settings.currency || "KRW";
    document.getElementById("settingSizing").value = settings.sizing_method || "kelly";
    document.getElementById("settingRiskPerTrade").value = (settings.risk_per_trade || 0.02) * 100;
    document.getElementById("settingMaxPosition").value = (settings.max_position_size || 0.10) * 100;
    document.getElementById("settingMaxDrawdown").value = (settings.max_drawdown || 0.15) * 100;
    document.getElementById("settingStopLoss").value = settings.stop_loss_atr_multiplier || 2.0;
    document.getElementById("settingRR").value = settings.risk_reward_ratio || 2.0;
    document.getElementById("settingKelly").value = settings.kelly_fraction || 0.5;
    document.getElementById("settingMaxDailyLoss").value = (settings.max_daily_loss || 0.03) * 100;
    document.getElementById("settingTelegramToken").value = settings.telegram_token || "";
    document.getElementById("settingTelegramChat").value = settings.telegram_chat_id || "";
    const discordEl = document.getElementById("settingDiscordWebhook");
    if (discordEl) discordEl.value = settings.discord_webhook_url || "";
    const dartEl = document.getElementById("settingDartApiKey");
    if (dartEl) dartEl.value = settings.dart_api_key || "";

    // ── 디스코드 봇 설정 반영 ──
    const dcBotTokenEl = document.getElementById("settingDiscordBotToken");
    if (dcBotTokenEl) dcBotTokenEl.value = settings.discord_bot_token || "";
    const dcBotChannelEl = document.getElementById("settingDiscordBotChannel");
    if (dcBotChannelEl) dcBotChannelEl.value = settings.discord_bot_channel_id || "";
    const dcBotAutoEl = document.getElementById("settingDiscordBotAutostart");
    if (dcBotAutoEl) dcBotAutoEl.checked = settings.discord_bot_autostart || false;
    // 봇 연결 상태 갱신
    refreshDiscordBotStatus();

    // ── 관심 섹터 설정 반영 ──
    if (settings.interest_sectors) {
        selectedSectors = settings.interest_sectors;
        // 섹터 칩 UI 동기화
        document.querySelectorAll(".sector-chip").forEach(chip => {
            const key = chip.dataset.sector;
            chip.classList.toggle("active", selectedSectors.includes(key));
        });
    }

    // ── 엄격 화이트리스트 모드 반영 ──
    const strictEl = document.getElementById("watchlistStrictMode");
    if (strictEl) strictEl.checked = settings.watchlist_strict_mode === true;

    // ── 포지션 유형 토글 반영 (단타/스윙/장기) ──
    const posShort = document.getElementById("posShortEnabled");
    if (posShort) posShort.checked = settings.position_type_short_enabled !== false;
    const posSwing = document.getElementById("posSwingEnabled");
    if (posSwing) posSwing.checked = settings.position_type_swing_enabled !== false;
    const posLong = document.getElementById("posLongEnabled");
    if (posLong) posLong.checked = settings.position_type_long_enabled !== false;

    // ── 분석 모듈 토글 반영 ──
    const modTech = document.getElementById("modTechnicalEnabled");
    if (modTech) modTech.checked = settings.module_technical_enabled !== false;
    const modFactor = document.getElementById("modFactorEnabled");
    if (modFactor) modFactor.checked = settings.module_factor_enabled !== false;
    const modSent = document.getElementById("modSentimentEnabled");
    if (modSent) modSent.checked = settings.module_sentiment_enabled !== false;

    // ── 종목 발굴 설정 반영 ──
    const discoveryEnabledEl = document.getElementById("discoveryEnabled");
    if (discoveryEnabledEl) discoveryEnabledEl.value = settings.discovery_enabled !== false ? "true" : "false";
    const discCycleEl = document.getElementById("discoveryCycleMultiplier");
    if (discCycleEl) discCycleEl.value = settings.discovery_cycle_multiplier || 4;
    const discMaxPerEl = document.getElementById("discoveryMaxPerMarket");
    if (discMaxPerEl) discMaxPerEl.value = settings.discovery_max_per_market || 10;
    const discMaxWlEl = document.getElementById("discoveryMaxWatchlist");
    if (discMaxWlEl) discMaxWlEl.value = settings.discovery_max_watchlist || 35;
    const discMoversEl = document.getElementById("discoveryIncludeMovers");
    if (discMoversEl) discMoversEl.value = settings.discovery_include_movers !== false ? "true" : "false";

    // ── 거래 스케줄 설정 반영 ──
    // analysis_interval: 분 단위 숫자 문자열 (예: "5", "15", "30", "60")
    // 레거시 포맷("15m", "1h", "4h", "1d")도 호환 처리
    const intervalEl = document.getElementById("settingInterval");
    if (intervalEl) {
        let iv = settings.analysis_interval || "60";
        // 레거시 문자열 포맷 → 분 단위 숫자로 변환
        if (typeof iv === "string") {
            const s = iv.trim().toLowerCase();
            if (s.endsWith("m")) iv = String(parseInt(s));       // "15m" → "15"
            else if (s.endsWith("h")) iv = String(parseInt(s) * 60);  // "1h" → "60"
            else if (s.endsWith("d")) iv = String(parseInt(s) * 1440); // "1d" → "1440"
        }
        intervalEl.value = iv;
    }

    // 한국/미국 시장 거래 시간 범위
    const krStartEl = document.getElementById("settingKrStart");
    const krEndEl = document.getElementById("settingKrEnd");
    const usStartEl = document.getElementById("settingUsStart");
    const usEndEl = document.getElementById("settingUsEnd");
    if (krStartEl) krStartEl.value = settings.schedule_kr_start || "09:00";
    if (krEndEl) krEndEl.value = settings.schedule_kr_end || "15:30";
    if (usStartEl) usStartEl.value = settings.schedule_us_start || "23:30";
    if (usEndEl) usEndEl.value = settings.schedule_us_end || "06:00";

    // ── 브로커 API 키 반영 ──
    const kisKeyEl = document.getElementById("settingKisAppKey");
    if (kisKeyEl) kisKeyEl.value = settings.kis_app_key || "";
    const kisSecretEl = document.getElementById("settingKisAppSecret");
    if (kisSecretEl) kisSecretEl.value = settings.kis_app_secret || "";
    const kisAccountEl = document.getElementById("settingKisAccount");
    if (kisAccountEl) kisAccountEl.value = settings.kis_account || "";
    const alpacaKeyEl = document.getElementById("settingAlpacaApiKey");
    if (alpacaKeyEl) alpacaKeyEl.value = settings.alpaca_api_key || "";
    const alpacaSecretEl = document.getElementById("settingAlpacaSecretKey");
    if (alpacaSecretEl) alpacaSecretEl.value = settings.alpaca_secret_key || "";

    const toggle = document.getElementById("toggleLive");
    if (settings.live_mode) { toggle.classList.add("active"); }
    else { toggle.classList.remove("active"); }

    renderWatchlist("us", settings.us_watchlist || []);
    renderWatchlist("kr", settings.kr_watchlist || []);

    // 브로커 선택에 따라 API 키 섹션 표시/숨김
    toggleBrokerApiFields();
}


// ═══════════════════════════════════════════════════════════════════════════
// 5. API 호출 함수들
// ═══════════════════════════════════════════════════════════════════════════

async function startBot() {
    /**
     * 봇 시작 API 호출
     *
     * 흐름:
     * 1. /api/bot/start POST 요청
     * 2. 서버에서 QuantBot 인스턴스 생성 + 스레드 시작
     * 3. 성공 시 UI 상태 갱신 (WebSocket status_update로 자동 반영)
     *
     * console.log를 추가하여 브라우저 개발자 도구(F12)에서
     * 버튼 클릭 → API 호출 → 응답 흐름을 추적할 수 있습니다.
     */
    console.log("[startBot] 버튼 클릭됨 - API 호출 시작");

    // 버튼 시각적 피드백: 클릭 즉시 "시작 중..." 텍스트로 변경
    const btn = document.getElementById("btnStart");
    if (btn) {
        btn.disabled = true;
        btn.textContent = currentLang === "ko" ? "시작 중..." : "Starting...";
    }

    try {
        const res = await fetch("/api/bot/start", { method: "POST" });
        console.log("[startBot] API 응답 상태:", res.status);
        const data = await res.json();
        console.log("[startBot] API 응답 데이터:", data);
        if (data.success) {
            showToast(currentLang === "ko" ? "봇이 시작되었습니다" : "Bot started", "success");
            // 버튼 상태: Start 비활성화, Stop 활성화
            if (btn) btn.disabled = true;
            const stopBtn = document.getElementById("btnStop");
            if (stopBtn) stopBtn.disabled = false;
        } else {
            showToast((currentLang === "ko" ? "실패: " : "Failed: ") + (data.error || "알 수 없는 오류"), "error");
            // 실패 시 버튼 복원
            if (btn) {
                btn.disabled = false;
                btn.textContent = currentLang === "ko" ? "봇 시작" : "Start Bot";
            }
        }
    } catch (e) {
        console.error("[startBot] 에러:", e);
        showToast((currentLang === "ko" ? "연결 오류: " : "Connection error: ") + e.message, "error");
        // 에러 시 버튼 복원
        if (btn) {
            btn.disabled = false;
            btn.textContent = currentLang === "ko" ? "봇 시작" : "Start Bot";
        }
    }
}

async function stopBot() {
    const msg = currentLang === "ko" ? "봇을 중지하시겠습니까?" : "Stop the bot?";
    if (!confirm(msg)) return;
    try {
        const res = await fetch("/api/bot/stop", { method: "POST" });
        const data = await res.json();
        if (data.success) showToast(currentLang === "ko" ? "봇이 중지되었습니다" : "Bot stopped", "info");
        else showToast((currentLang === "ko" ? "실패: " : "Failed: ") + data.error, "error");
    } catch (e) { showToast(currentLang === "ko" ? "연결 오류" : "Connection error", "error"); }
}

async function triggerDiscovery() {
    const btn = document.getElementById("btnDiscovery");
    if (btn) {
        btn.disabled = true;
        btn.textContent = currentLang === "ko" ? "🔍 탐색 중..." : "🔍 Scanning...";
    }
    try {
        const res = await fetch("/api/discovery/trigger", { method: "POST" });
        const data = await res.json();
        if (data.success) {
            // 발굴은 백그라운드에서 실행됨 — 결과는 discovery_update WebSocket으로 수신
            showToast(
                currentLang === "ko"
                    ? "🔍 종목 발굴을 시작했습니다. 완료 시 알림됩니다."
                    : "🔍 Discovery started. You'll be notified when done.",
                "info"
            );
        } else {
            showToast(
                (currentLang === "ko" ? "발굴 실패: " : "Discovery failed: ") + data.error,
                "error"
            );
        }
    } catch (e) {
        showToast(currentLang === "ko" ? "연결 오류" : "Connection error", "error");
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = currentLang === "ko" ? "🔍 종목 발굴" : "🔍 Discover";
        }
    }
}

function updateDiscoveryBar(discovery) {
    const bar = document.getElementById("discoveryBar");
    const statusEl = document.getElementById("discoveryStatus");
    const stocksEl = document.getElementById("discoveryStocks");
    if (!bar || !discovery) return;

    const usCount = (discovery.discovered_us || []).length;
    const krCount = (discovery.discovered_kr || []).length;
    const total = usCount + krCount;

    if (total === 0 && !discovery.enabled) {
        bar.style.display = "none";
        return;
    }

    bar.style.display = "block";

    if (!discovery.enabled) {
        statusEl.textContent = currentLang === "ko" ? "종목 발굴 비활성화" : "Discovery disabled";
        stocksEl.textContent = "";
        return;
    }

    const nextIn = discovery.next_discovery_in || 0;
    const nextText = nextIn > 0
        ? (currentLang === "ko" ? ` | 다음 발굴: ${nextIn}주기 후` : ` | Next: in ${nextIn} cycles`)
        : "";
    statusEl.textContent = currentLang === "ko"
        ? `발굴 종목: US ${usCount}개 · KR ${krCount}개${nextText}`
        : `Discovered: US ${usCount} · KR ${krCount}${nextText}`;

    // 종목 목록 표시 (이름 정보가 있으면 이름 포함)
    const usDetails = discovery.discovered_us_details || [];
    const krDetails = discovery.discovered_kr_details || [];
    const allStocks = [];

    if (usDetails.length > 0) {
        usDetails.forEach(d => allStocks.push(`🇺🇸${d.name || d.symbol}`));
    } else {
        (discovery.discovered_us || []).forEach(s => allStocks.push(`🇺🇸${s}`));
    }
    if (krDetails.length > 0) {
        krDetails.forEach(d => allStocks.push(`🇰🇷${d.name || d.symbol}`));
    } else {
        (discovery.discovered_kr || []).forEach(s => allStocks.push(`🇰🇷${s}`));
    }
    stocksEl.textContent = allStocks.length > 0 ? allStocks.join("  ") : "";
}

async function resetBot() {
    // ── 2단계 확인: 실수로 누르는 것을 방지 ──
    const msg1 = currentLang === "ko"
        ? "⚠️ 모든 포지션, 거래 이력, 자산 기록이 삭제됩니다.\n정말 초기화하시겠습니까?"
        : "⚠️ All positions, trades, and equity history will be deleted.\nAre you sure?";
    if (!confirm(msg1)) return;

    const msg2 = currentLang === "ko"
        ? "⚠️ 최종 확인: 이 작업은 되돌릴 수 없습니다.\n'확인'을 누르면 즉시 초기화됩니다."
        : "⚠️ Final confirmation: This cannot be undone.\nClick OK to proceed.";
    if (!confirm(msg2)) return;

    try {
        const btn = document.getElementById("btnReset");
        btn.disabled = true;
        btn.textContent = currentLang === "ko" ? "초기화 중..." : "Resetting...";

        const res = await fetch("/api/bot/reset", { method: "POST" });
        const data = await res.json();

        if (data.success) {
            showToast(
                currentLang === "ko"
                    ? `✅ ${data.message}`
                    : `✅ Reset complete`,
                "success"
            );
            // 화면 데이터 갱신
            setTimeout(() => location.reload(), 1500);
        } else {
            showToast(
                (currentLang === "ko" ? "실패: " : "Failed: ") + data.error,
                "error"
            );
        }
    } catch (e) {
        showToast(currentLang === "ko" ? "연결 오류" : "Connection error", "error");
    } finally {
        const btn = document.getElementById("btnReset");
        btn.disabled = false;
        btn.textContent = currentLang === "ko" ? "초기화" : "Reset";
    }
}

async function saveSettings() {
    const settings = {
        broker: document.getElementById("settingBroker").value,
        live_mode: document.getElementById("toggleLive").classList.contains("active"),
        capital: parseFloat(document.getElementById("settingCapital").value),
        currency: document.getElementById("settingCurrency").value,
        sizing_method: document.getElementById("settingSizing").value,
        risk_per_trade: parseFloat(document.getElementById("settingRiskPerTrade").value) / 100,
        max_position_size: parseFloat(document.getElementById("settingMaxPosition").value) / 100,
        max_drawdown: parseFloat(document.getElementById("settingMaxDrawdown").value) / 100,
        max_daily_loss: parseFloat(document.getElementById("settingMaxDailyLoss").value) / 100,
        stop_loss_atr_multiplier: parseFloat(document.getElementById("settingStopLoss").value),
        risk_reward_ratio: parseFloat(document.getElementById("settingRR").value),
        kelly_fraction: parseFloat(document.getElementById("settingKelly").value),
        telegram_token: document.getElementById("settingTelegramToken").value,
        telegram_chat_id: document.getElementById("settingTelegramChat").value,
        discord_webhook_url: document.getElementById("settingDiscordWebhook") ?
            document.getElementById("settingDiscordWebhook").value : "",
        discord_bot_token: document.getElementById("settingDiscordBotToken") ?
            document.getElementById("settingDiscordBotToken").value : "",
        discord_bot_channel_id: document.getElementById("settingDiscordBotChannel") ?
            document.getElementById("settingDiscordBotChannel").value : "",
        discord_bot_autostart: document.getElementById("settingDiscordBotAutostart") ?
            document.getElementById("settingDiscordBotAutostart").checked : false,
        dart_api_key: document.getElementById("settingDartApiKey") ?
            document.getElementById("settingDartApiKey").value : "",

        // ── 브로커 API 키 (실거래용) ──
        kis_app_key: document.getElementById("settingKisAppKey") ?
            document.getElementById("settingKisAppKey").value : "",
        kis_app_secret: document.getElementById("settingKisAppSecret") ?
            document.getElementById("settingKisAppSecret").value : "",
        kis_account: document.getElementById("settingKisAccount") ?
            document.getElementById("settingKisAccount").value : "",
        alpaca_api_key: document.getElementById("settingAlpacaApiKey") ?
            document.getElementById("settingAlpacaApiKey").value : "",
        alpaca_secret_key: document.getElementById("settingAlpacaSecretKey") ?
            document.getElementById("settingAlpacaSecretKey").value : "",

        us_watchlist: watchlists.us,
        kr_watchlist: watchlists.kr,

        // ── 관심 분야 (선택된 섹터 키 배열) ──
        interest_sectors: selectedSectors,

        // ── 엄격 화이트리스트 모드 ──
        watchlist_strict_mode: document.getElementById("watchlistStrictMode") ?
            document.getElementById("watchlistStrictMode").checked : false,

        // ── 포지션 유형 토글 (단타/스윙/장기) ──
        position_type_short_enabled: document.getElementById("posShortEnabled") ?
            document.getElementById("posShortEnabled").checked : true,
        position_type_swing_enabled: document.getElementById("posSwingEnabled") ?
            document.getElementById("posSwingEnabled").checked : true,
        position_type_long_enabled: document.getElementById("posLongEnabled") ?
            document.getElementById("posLongEnabled").checked : true,

        // ── 분석 모듈 토글 (technical/factor/sentiment) ──
        module_technical_enabled: document.getElementById("modTechnicalEnabled") ?
            document.getElementById("modTechnicalEnabled").checked : true,
        module_factor_enabled: document.getElementById("modFactorEnabled") ?
            document.getElementById("modFactorEnabled").checked : true,
        module_sentiment_enabled: document.getElementById("modSentimentEnabled") ?
            document.getElementById("modSentimentEnabled").checked : true,

        // ── 종목 발굴 설정 ──
        discovery_enabled: document.getElementById("discoveryEnabled") ?
            document.getElementById("discoveryEnabled").value === "true" : true,
        discovery_cycle_multiplier: document.getElementById("discoveryCycleMultiplier") ?
            parseInt(document.getElementById("discoveryCycleMultiplier").value) : 4,
        discovery_max_per_market: document.getElementById("discoveryMaxPerMarket") ?
            parseInt(document.getElementById("discoveryMaxPerMarket").value) : 10,
        discovery_max_watchlist: document.getElementById("discoveryMaxWatchlist") ?
            parseInt(document.getElementById("discoveryMaxWatchlist").value) : 35,
        discovery_include_movers: document.getElementById("discoveryIncludeMovers") ?
            document.getElementById("discoveryIncludeMovers").value === "true" : true,

        // ── 거래 스케줄 설정 ──
        // 분석 주기: 봇이 시장을 체크하는 간격
        analysis_interval: document.getElementById("settingInterval") ?
            document.getElementById("settingInterval").value : "1h",
        // 한국 시장 거래 시간 (KST)
        schedule_kr_start: document.getElementById("settingKrStart") ?
            document.getElementById("settingKrStart").value : "09:00",
        schedule_kr_end: document.getElementById("settingKrEnd") ?
            document.getElementById("settingKrEnd").value : "15:30",
        // 미국 시장 거래 시간 (KST 기준)
        schedule_us_start: document.getElementById("settingUsStart") ?
            document.getElementById("settingUsStart").value : "23:30",
        schedule_us_end: document.getElementById("settingUsEnd") ?
            document.getElementById("settingUsEnd").value : "06:00",
    };

    try {
        const res = await fetch("/api/settings", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(settings)
        });
        const data = await res.json();
        if (data.success) {
            showToast(currentLang === "ko" ? "설정이 저장되었습니다" : "Settings saved", "success");
            document.getElementById("settingsSavedMsg").textContent =
                (currentLang === "ko" ? "저장 완료: " : "Saved: ") + new Date().toLocaleTimeString();
        } else {
            showToast((currentLang === "ko" ? "저장 실패: " : "Save failed: ") + data.error, "error");
        }
    } catch (e) { showToast(currentLang === "ko" ? "연결 오류" : "Connection error", "error"); }
}

// ═══════════════════════════════════════════════════════════════════════════
// 거래 결정 상세 모달
// ═══════════════════════════════════════════════════════════════════════════

function closeTradeDecisionModal() {
    const modal = document.getElementById("tradeDecisionModal");
    if (modal) modal.style.display = "none";
}

function showTradeDecisionModal(tradeIdx) {
    const trades = window._tradesCache || [];
    const trade = trades[tradeIdx];
    if (!trade) {
        showToast("거래 정보를 찾을 수 없습니다", "error");
        return;
    }

    const modal = document.getElementById("tradeDecisionModal");
    const titleEl = document.getElementById("tdmTitle");
    const subtitleEl = document.getElementById("tdmSubtitle");
    const bodyEl = document.getElementById("tdmBody");
    if (!modal || !bodyEl) return;

    // decision이 없으면 빈 객체로 (fallback 렌더링)
    const d = trade.decision || {};
    const hasDecision = trade.decision && Object.keys(trade.decision).length > 0;
    const isBuy = (trade.side === "BUY");
    const sideColor = isBuy ? "#2ecc71" : "#ff5555";
    const sideText = isBuy ? "매수" : "매도";

    titleEl.textContent = `${trade.name || trade.symbol} ${sideText} 상세`;
    subtitleEl.textContent = `${formatTime(trade.timestamp)} · ${trade.quantity}주 @ ${trade.price.toFixed(2)}`;

    let html = "";

    // ── 기본 거래 정보 (항상 표시) ──
    const totalKrw = Number(trade.total_value || 0);
    const pnlKrw = Number(trade.pnl || 0);
    const pnlColor = pnlKrw >= 0 ? "#2ecc71" : "#ff5555";
    const pnlSign = pnlKrw >= 0 ? "+" : "";
    html += `
    <div style="background:rgba(255,255,255,0.04);border-radius:10px;padding:14px;margin-bottom:12px;">
        <div style="font-size:13px;font-weight:600;margin-bottom:10px;">📋 거래 정보</div>
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px;font-size:11px;">
            <div><span style="color:rgba(255,255,255,0.5);">종목</span><br><b>${trade.name || trade.symbol}</b></div>
            <div><span style="color:rgba(255,255,255,0.5);">매매 방향</span><br><b style="color:${sideColor};">${sideText}</b></div>
            <div><span style="color:rgba(255,255,255,0.5);">수량</span><br><b style="font-family:monospace;">${trade.quantity}주</b></div>
            <div><span style="color:rgba(255,255,255,0.5);">체결가</span><br><b style="font-family:monospace;">${trade.price.toFixed(2)}</b></div>
            <div><span style="color:rgba(255,255,255,0.5);">총액 (KRW)</span><br><b style="font-family:monospace;">₩${Math.round(totalKrw).toLocaleString()}</b></div>
            <div><span style="color:rgba(255,255,255,0.5);">전략 / 청산사유</span><br><b>${trade.strategy || "-"}</b></div>
            ${pnlKrw !== 0 ? `<div><span style="color:rgba(255,255,255,0.5);">실현 손익</span><br><b style="color:${pnlColor};font-family:monospace;">${pnlSign}₩${Math.round(pnlKrw).toLocaleString()}</b></div>` : ""}
            ${trade.position_type ? `<div><span style="color:rgba(255,255,255,0.5);">포지션 유형</span><br><b>${trade.position_type}</b></div>` : ""}
        </div>
    </div>`;

    // ── 매매 이유 (reasons - 구버전부터 있음) ──
    if (trade.reasons && trade.reasons.length > 0) {
        html += `<div style="background:rgba(255,255,255,0.04);border-radius:10px;padding:14px;margin-bottom:12px;">
            <div style="font-size:13px;font-weight:600;margin-bottom:8px;">💡 매매 이유</div>
            <ul style="margin:0;padding-left:20px;font-size:12px;line-height:1.7;color:rgba(255,255,255,0.85);">
            ${trade.reasons.map(r => `<li>${r}</li>`).join("")}
            </ul>
        </div>`;
    }

    // ── 구버전 거래 안내 ──
    if (!hasDecision) {
        html += `
        <div style="background:rgba(255,193,7,0.10);border-left:3px solid rgba(255,193,7,0.6);border-radius:8px;padding:12px 14px;margin-bottom:12px;font-size:12px;color:rgba(255,255,255,0.7);">
            ℹ️ 이 거래는 풍부한 의사결정 데이터가 저장되기 전에 체결되었습니다.<br>
            <span style="opacity:0.7;">새로 체결되는 거래부터 앙상블 점수, 모듈별 기여도, 임계값, 손익 분석 등이 자세히 표시됩니다.</span>
        </div>`;
        // 구버전 거래는 여기서 종료
        if (d.timestamp) {
            html += `<div style="text-align:right;font-size:10px;color:rgba(255,255,255,0.3);margin-top:8px;">결정 시각: ${d.timestamp}</div>`;
        }
        bodyEl.innerHTML = html;
        modal.style.display = "flex";
        return;
    }

    // ── 결정 요약 배너 ──
    const triggerKr = _exitReasonKr(d.trigger);
    html += `
    <div style="background:${isBuy ? 'rgba(46,204,113,0.10)' : 'rgba(255,85,85,0.10)'};border-left:4px solid ${sideColor};border-radius:8px;padding:14px 16px;margin-bottom:16px;">
        <div style="font-size:12px;color:rgba(255,255,255,0.6);margin-bottom:4px;">발동 사유</div>
        <div style="font-size:15px;font-weight:600;color:${sideColor};">${d.exit_summary || triggerKr || (isBuy ? "📊 매수 신호" : "📊 매도 신호")}</div>
    </div>`;

    // ── 매수: 앙상블 점수 + 모듈 기여도 ──
    if (isBuy && d.ensemble) {
        const e = d.ensemble;
        const scoreColor = e.score > 0.3 ? "#2ecc71" : e.score > 0.15 ? "#ffd700" : "#ff9500";
        html += `
        <div style="background:rgba(255,255,255,0.04);border-radius:10px;padding:14px;margin-bottom:12px;">
            <div style="font-size:13px;font-weight:600;margin-bottom:10px;">🧠 앙상블 판단</div>
            <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;font-size:12px;">
                <div><span style="color:rgba(255,255,255,0.5);">최종 점수</span><br><span style="color:${scoreColor};font-weight:700;font-size:16px;">${e.score >= 0 ? '+' : ''}${e.score.toFixed(3)}</span></div>
                <div><span style="color:rgba(255,255,255,0.5);">신뢰도</span><br><span style="font-weight:700;font-size:16px;">${(e.confidence * 100).toFixed(1)}%</span></div>
                <div><span style="color:rgba(255,255,255,0.5);">액션</span><br><span style="font-weight:700;font-size:16px;color:${sideColor};">${e.action}</span></div>
            </div>
            <div style="font-size:11px;color:rgba(255,255,255,0.4);margin-top:8px;font-family:monospace;">
                수식: Σ (score_i × weight_i × confidence_i) / Σ weight_i &gt; ${(d.thresholds && d.thresholds.buy_threshold) || 0.2}
            </div>
        </div>`;

        // 모듈별 기여도 막대그래프
        if (e.components && Object.keys(e.components).length > 0) {
            html += `<div style="background:rgba(255,255,255,0.04);border-radius:10px;padding:14px;margin-bottom:12px;">
                <div style="font-size:13px;font-weight:600;margin-bottom:10px;">📊 모듈별 기여도</div>`;
            Object.entries(e.components).forEach(([name, score]) => {
                const pct = Math.abs(score) * 100;
                const color = score > 0 ? "#2ecc71" : "#ff5555";
                const sign = score >= 0 ? "+" : "";
                html += `
                <div style="margin-bottom:8px;">
                    <div style="display:flex;justify-content:space-between;font-size:11px;margin-bottom:3px;">
                        <span>${name}</span>
                        <span style="color:${color};font-weight:600;font-family:monospace;">${sign}${score.toFixed(3)}</span>
                    </div>
                    <div style="height:6px;background:rgba(255,255,255,0.08);border-radius:3px;overflow:hidden;">
                        <div style="width:${Math.min(pct, 100)}%;height:100%;background:${color};opacity:0.7;"></div>
                    </div>
                </div>`;
            });
            html += `</div>`;
        }

        // 매매 이유 (자연어)
        if (e.reasons && e.reasons.length > 0) {
            html += `<div style="background:rgba(255,255,255,0.04);border-radius:10px;padding:14px;margin-bottom:12px;">
                <div style="font-size:13px;font-weight:600;margin-bottom:8px;">💡 판단 근거</div>
                <ul style="margin:0;padding-left:20px;font-size:12px;line-height:1.7;color:rgba(255,255,255,0.85);">
                ${e.reasons.map(r => `<li>${r}</li>`).join("")}
                </ul>
            </div>`;
        }
    }

    // ── 매수: 임계값 + 시장 체제 ──
    if (isBuy && d.thresholds && Object.keys(d.thresholds).length > 0) {
        const th = d.thresholds;
        const vixColor = th.vix < 15 ? "#2ecc71" : th.vix < 20 ? "#ffd700" : th.vix < 30 ? "#ff9500" : "#ff5555";
        html += `
        <div style="background:rgba(255,255,255,0.04);border-radius:10px;padding:14px;margin-bottom:12px;">
            <div style="font-size:13px;font-weight:600;margin-bottom:10px;">⚙️ 적응형 임계값 (시장 체제 반영)</div>
            <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px;font-size:11px;">
                <div><span style="color:rgba(255,255,255,0.5);">매수 임계값</span><br><b style="font-family:monospace;">${th.buy_threshold >= 0 ? '+' : ''}${th.buy_threshold.toFixed(3)}</b></div>
                <div><span style="color:rgba(255,255,255,0.5);">최소 신뢰도</span><br><b style="font-family:monospace;">${(th.min_confidence * 100).toFixed(1)}%</b></div>
                <div><span style="color:rgba(255,255,255,0.5);">VIX</span><br><b style="color:${vixColor};font-family:monospace;">${th.vix.toFixed(2)}</b></div>
                <div><span style="color:rgba(255,255,255,0.5);">변동성 체제</span><br><b>${th.regime_volatility}</b></div>
                <div><span style="color:rgba(255,255,255,0.5);">시장 체제</span><br><b>${th.regime_market}</b></div>
                <div><span style="color:rgba(255,255,255,0.5);">사이즈 배수</span><br><b style="font-family:monospace;">×${th.position_size_multiplier.toFixed(2)}</b></div>
            </div>
        </div>`;
    }

    // ── 매수: 기술적 지표 + 손절/목표 ──
    if (isBuy && d.indicators) {
        const ind = d.indicators;
        const stopMult = 2.0; // 표시용
        html += `
        <div style="background:rgba(255,255,255,0.04);border-radius:10px;padding:14px;margin-bottom:12px;">
            <div style="font-size:13px;font-weight:600;margin-bottom:10px;">📈 진입 시점 지표</div>
            <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:8px;font-size:11px;">
                <div><span style="color:rgba(255,255,255,0.5);">진입가</span><br><b style="font-family:monospace;">${ind.price ? ind.price.toFixed(4) : '-'}</b></div>
                <div><span style="color:rgba(255,255,255,0.5);">ATR (변동성)</span><br><b style="font-family:monospace;">${ind.atr ? ind.atr.toFixed(4) : '-'}</b></div>
                ${ind.rsi !== null && ind.rsi !== undefined ? `<div><span style="color:rgba(255,255,255,0.5);">RSI</span><br><b style="font-family:monospace;">${ind.rsi.toFixed(2)}</b></div>` : ""}
                ${ind.macd !== null && ind.macd !== undefined ? `<div><span style="color:rgba(255,255,255,0.5);">MACD</span><br><b style="font-family:monospace;">${ind.macd.toFixed(4)}</b></div>` : ""}
                ${ind.sma_20 ? `<div><span style="color:rgba(255,255,255,0.5);">SMA 20</span><br><b style="font-family:monospace;">${ind.sma_20.toFixed(2)}</b></div>` : ""}
                ${ind.sma_50 ? `<div><span style="color:rgba(255,255,255,0.5);">SMA 50</span><br><b style="font-family:monospace;">${ind.sma_50.toFixed(2)}</b></div>` : ""}
            </div>
            <div style="margin-top:10px;padding-top:10px;border-top:1px solid rgba(255,255,255,0.06);font-size:11px;color:rgba(255,255,255,0.6);font-family:monospace;">
                손절가 = 진입가 - ATR × 2.0 &nbsp;&nbsp;|&nbsp;&nbsp; 목표가 = 진입가 + ATR × 4.0 (RR 1:2)
            </div>
        </div>`;
    }

    // ── 매수: 포지션 사이징 ──
    if (isBuy && d.sizing) {
        const s = d.sizing;
        html += `
        <div style="background:rgba(255,255,255,0.04);border-radius:10px;padding:14px;margin-bottom:12px;">
            <div style="font-size:13px;font-weight:600;margin-bottom:10px;">💰 포지션 사이징</div>
            <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px;font-size:11px;">
                <div><span style="color:rgba(255,255,255,0.5);">방식</span><br><b>${s.method}</b></div>
                <div><span style="color:rgba(255,255,255,0.5);">매수 수량</span><br><b style="font-family:monospace;">${s.shares}주</b></div>
                <div><span style="color:rgba(255,255,255,0.5);">투입 금액</span><br><b style="font-family:monospace;">${s.value_native.toLocaleString()}</b></div>
                <div><span style="color:rgba(255,255,255,0.5);">사이즈 배수</span><br><b style="font-family:monospace;">×${s.size_multiplier.toFixed(2)}</b></div>
            </div>
        </div>`;
    }

    // ── 매도: 손익 분석 ──
    if (!isBuy && d.prices) {
        const p = d.prices;
        const pnlColor = p.pnl_pct >= 0 ? "#2ecc71" : "#ff5555";
        const pnlSign = p.pnl_pct >= 0 ? "+" : "";
        html += `
        <div style="background:rgba(255,255,255,0.04);border-radius:10px;padding:14px;margin-bottom:12px;">
            <div style="font-size:13px;font-weight:600;margin-bottom:10px;">💸 손익 분석</div>
            <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px;font-size:11px;">
                <div><span style="color:rgba(255,255,255,0.5);">진입가</span><br><b style="font-family:monospace;">${p.entry_price.toFixed(4)}</b></div>
                <div><span style="color:rgba(255,255,255,0.5);">청산가</span><br><b style="font-family:monospace;">${p.current_price.toFixed(4)}</b></div>
                <div><span style="color:rgba(255,255,255,0.5);">손익률</span><br><b style="color:${pnlColor};font-family:monospace;font-size:14px;">${pnlSign}${p.pnl_pct.toFixed(2)}%</b></div>
                <div><span style="color:rgba(255,255,255,0.5);">매도 수량</span><br><b style="font-family:monospace;">${p.quantity}주</b></div>
                ${d.partial_ratio ? `<div><span style="color:rgba(255,255,255,0.5);">매도 비율</span><br><b style="font-family:monospace;">${(d.partial_ratio * 100).toFixed(0)}%</b></div>` : ""}
                ${d.holding_days !== undefined ? `<div><span style="color:rgba(255,255,255,0.5);">보유 기간</span><br><b>${d.holding_days}일</b></div>` : ""}
            </div>
        </div>`;
    }

    // ── 매도: ExitManager 상태 ──
    if (!isBuy && d.exit_state && Object.keys(d.exit_state).length > 0) {
        const es = d.exit_state;
        html += `
        <div style="background:rgba(255,255,255,0.04);border-radius:10px;padding:14px;margin-bottom:12px;">
            <div style="font-size:13px;font-weight:600;margin-bottom:10px;">🛡️ ExitManager 상태</div>
            <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px;font-size:11px;">
                ${es.entry_atr ? `<div><span style="color:rgba(255,255,255,0.5);">진입 ATR</span><br><b style="font-family:monospace;">${es.entry_atr.toFixed(4)}</b></div>` : ""}
                ${es.current_stop ? `<div><span style="color:rgba(255,255,255,0.5);">활성 손절선</span><br><b style="color:#ff5555;font-family:monospace;">${es.current_stop.toFixed(4)}</b></div>` : ""}
                ${es.initial_stop ? `<div><span style="color:rgba(255,255,255,0.5);">최초 손절선</span><br><b style="font-family:monospace;opacity:0.7;">${es.initial_stop.toFixed(4)}</b></div>` : ""}
                ${es.target_1 ? `<div><span style="color:rgba(255,255,255,0.5);">1차 목표</span><br><b style="color:#2ecc71;font-family:monospace;">${es.target_1.toFixed(4)}</b></div>` : ""}
                ${es.target_2 ? `<div><span style="color:rgba(255,255,255,0.5);">2차 목표</span><br><b style="color:#2ecc71;font-family:monospace;opacity:0.7;">${es.target_2.toFixed(4)}</b></div>` : ""}
                ${es.highest_since_entry ? `<div><span style="color:rgba(255,255,255,0.5);">최고가</span><br><b style="font-family:monospace;">${es.highest_since_entry.toFixed(4)}</b></div>` : ""}
                ${es.partial_sold_pct !== undefined ? `<div><span style="color:rgba(255,255,255,0.5);">기 매도 비중</span><br><b style="font-family:monospace;">${(es.partial_sold_pct * 100).toFixed(0)}%</b></div>` : ""}
            </div>
        </div>`;
    }

    // ── 타임스탬프 ──
    if (d.timestamp) {
        html += `<div style="text-align:right;font-size:10px;color:rgba(255,255,255,0.3);margin-top:8px;">결정 시각: ${d.timestamp}</div>`;
    }

    bodyEl.innerHTML = html;
    modal.style.display = "flex";
}

// 청산 사유 한국어 변환 (run_bot.py의 _exit_reason_kr와 동일)
function _exitReasonKr(reason) {
    const map = {
        "stop_loss": "🔴 하드 스탑 (손절선 도달)",
        "take_profit_1": "🟢 1차 익절 (50% 매도, 손절선 본전 상향)",
        "take_profit_2": "🟢 2차 익절 (전량 매도)",
        "trailing_stop": "📉 트레일링 스탑 (Chandelier Exit)",
        "time_stop": "⏰ 보유기간 초과",
        "signal_sell": "📊 앙상블 매도 신호",
        "ensemble": "📊 앙상블 매수 신호",
        "close_position": "📊 일반 청산",
        "close_partial": "📊 부분 청산",
        "ensemble_signal": "📊 앙상블 신호",
    };
    return map[reason] || reason || "";
}


async function loadTrades() {
    try {
        const res = await fetch("/api/trades?limit=200");
        const trades = await res.json();
        const tbody = document.getElementById("tradesTable");

        if (!trades || trades.length === 0) {
            tbody.innerHTML = `<tr><td colspan="8" class="text-mute" style="text-align:center; padding:48px;">${i18n.empty_trades[currentLang]}</td></tr>`;
            window._tradesCache = [];
            updateTradeFilterStatus(0, 0);
            return;
        }

        // 거래 데이터를 전역에 저장 (모달 렌더링용 + 필터링용)
        window._tradesCache = trades;

        // 필터 적용해서 렌더링
        applyTradeFilters();
        return;
    } catch (e) {
        showToast(currentLang === "ko" ? "거래 이력 로드 실패" : "Failed to load trades", "error");
    }
}

/**
 * applyTradeFilters() - 검색어 + 매수/매도 + 손익 필터를 적용하여
 * 거래 이력 테이블을 다시 렌더링합니다.
 * (서버 재요청 없이 _tradesCache에서 필터링)
 */
function applyTradeFilters() {
    const allTrades = window._tradesCache || [];
    const tbody = document.getElementById("tradesTable");
    if (!tbody) return;

    // 필터 값 읽기
    const searchEl = document.getElementById("tradeSearchInput");
    const sideEl = document.getElementById("tradeSideFilter");
    const pnlEl = document.getElementById("tradePnlFilter");
    const searchText = (searchEl ? searchEl.value : "").trim().toLowerCase();
    const sideFilter = sideEl ? sideEl.value : "all";
    const pnlFilter = pnlEl ? pnlEl.value : "all";

    // 검색 ✕ 버튼 표시 토글
    const clearBtn = document.getElementById("tradeSearchClear");
    if (clearBtn) clearBtn.style.display = searchText ? "block" : "none";

    // 필터링
    const filtered = allTrades.filter(t => {
        // 종목 검색 (코드 또는 이름 부분 일치, 대소문자 무시)
        if (searchText) {
            const sym = (t.symbol || "").toLowerCase();
            const name = (t.name || "").toLowerCase();
            if (!sym.includes(searchText) && !name.includes(searchText)) {
                return false;
            }
        }
        // 매수/매도 필터
        if (sideFilter !== "all" && t.side !== sideFilter) {
            return false;
        }
        // 손익 필터 (매도 거래에만 적용, 매수는 pnl=0이라 손실로 잡힐 수 있음)
        if (pnlFilter !== "all") {
            const pnl = Number(t.pnl || 0);
            if (pnlFilter === "profit" && pnl <= 0) return false;
            if (pnlFilter === "loss" && pnl >= 0) return false;
        }
        return true;
    });

    // 상태 메시지 업데이트
    updateTradeFilterStatus(filtered.length, allTrades.length);

    if (filtered.length === 0) {
        const emptyMsg = allTrades.length === 0
            ? i18n.empty_trades[currentLang]
            : (currentLang === "ko" ? "필터 조건에 맞는 거래가 없습니다" : "No trades match filters");
        tbody.innerHTML = `<tr><td colspan="8" class="text-mute" style="text-align:center; padding:48px;">${emptyMsg}</td></tr>`;
        return;
    }

    // ── 손익 요약 계산 (필터링된 거래 기준) ──
    updateTradePnlSummary(filtered);

    // 렌더링 (원래 인덱스 보존 - 모달 클릭 시 _tradesCache 인덱스 사용)
    tbody.innerHTML = filtered.map(t => {
        const idx = allTrades.indexOf(t);  // 원본 캐시에서의 인덱스 (모달용)
        const sideClass = t.side === "BUY" ? "badge-buy" : "badge-sell";
        const sideText = currentLang === "ko" ? (t.side === "BUY" ? "매수" : "매도") : t.side;
        // 종목명 표시: name 필드가 있으면 이름, 없으면 symbol
        const tradeName = t.name || t.symbol;
        // 포지션 유형 배지
        const posType = t.position_type || "";
        const posTypeEn = posType === "단타" ? "short" : posType === "장기" ? "long" : "swing";
        const typeBadge = posType ? ` <span class="position-type-badge position-type-${posTypeEn}">${posType}</span>` : "";
        // 매매 이유
        const reasons = t.reasons || [];
        const reasonsHtml = reasons.length > 0
            ? `<div style="font-size:10px;color:rgba(255,255,255,0.4);padding:2px 0 0 0;">${wrapTermTooltips(reasons.slice(0, 2).join(" · "))}</div>`
            : "";
        // 전략은 항상 클릭 가능 (decision 없으면 기본 정보로 fallback)
        const strategyCell = `<span class="strategy-clickable" onclick="showTradeDecisionModal(${idx})" style="cursor:pointer;color:#0099ff;text-decoration:underline;text-decoration-style:dotted;text-underline-offset:3px;" title="클릭하여 매매 상세 보기">${t.strategy || "-"} 🔍</span>`;
        // ── 손익 셀 ──
        // 매수: '—' / 매도: 색상 + 금액 + (가능하면) %
        const pnlCell = renderPnlCell(t);
        return `<tr>
            <td>${formatTime(t.timestamp)}</td>
            <td style="font-weight:600;">${tradeName}${typeBadge}</td>
            <td><span class="badge ${sideClass}">${sideText}</span></td>
            <td>${t.quantity}</td>
            <td>${t.price.toFixed(2)}</td>
            <td>${formatCurrency(t.total_value)}</td>
            <td>${pnlCell}</td>
            <td>${strategyCell}${reasonsHtml}</td>
        </tr>`;
    }).join("");
}

/**
 * renderPnlCell() - 손익 셀 HTML 생성
 *
 * - 매수 거래: '—' (손익은 매도 시점에 확정됨)
 * - 매도 거래: ₩금액 (녹색/빨강) + 가능하면 % 함께 표시
 */
function renderPnlCell(t) {
    const isBuy = (t.side === "BUY");
    if (isBuy) {
        return `<span style="color:rgba(255,255,255,0.3);">—</span>`;
    }
    const pnl = Number(t.pnl || 0);
    if (pnl === 0) {
        return `<span style="color:rgba(255,255,255,0.4);font-family:monospace;">₩0</span>`;
    }
    const color = pnl > 0 ? "#2ecc71" : "#ff5555";
    const sign = pnl > 0 ? "+" : "";
    const formatted = `${sign}₩${Math.round(pnl).toLocaleString()}`;

    // % 표시 (decision_json에서 가져오기, 없으면 생략)
    let pctHtml = "";
    if (t.decision && t.decision.prices && t.decision.prices.pnl_pct !== undefined) {
        const pct = Number(t.decision.prices.pnl_pct);
        const pctSign = pct >= 0 ? "+" : "";
        pctHtml = `<div style="font-size:10px;opacity:0.7;font-family:monospace;">${pctSign}${pct.toFixed(2)}%</div>`;
    }
    return `<div style="color:${color};font-weight:600;font-family:monospace;">${formatted}</div>${pctHtml}`;
}

/**
 * updateTradePnlSummary() - 거래 손익 요약 카드 갱신
 *
 * 매도 거래의 pnl을 합산하여 총 손익, 승률, 손익비 등 계산.
 * 필터링된 거래 기준이라 사용자가 종목 검색 시 해당 종목 손익만 표시됨.
 */
function updateTradePnlSummary(trades) {
    const summaryEl = document.getElementById("tradePnlSummary");
    if (!summaryEl) return;

    // 매도 거래만 추출 (손익은 매도 시점에 확정)
    const sells = trades.filter(t => t.side === "SELL");
    if (sells.length === 0) {
        summaryEl.style.display = "none";
        return;
    }
    summaryEl.style.display = "block";

    // 합산 + 카운트
    let totalPnl = 0;
    let totalWin = 0;
    let totalLoss = 0;
    let winCount = 0;
    let lossCount = 0;
    sells.forEach(t => {
        const pnl = Number(t.pnl || 0);
        totalPnl += pnl;
        if (pnl > 0) { totalWin += pnl; winCount++; }
        else if (pnl < 0) { totalLoss += Math.abs(pnl); lossCount++; }
    });

    // 승률 = 수익 거래 / 손익 거래 (보합 제외)
    const decided = winCount + lossCount;
    const winRate = decided > 0 ? (winCount / decided * 100) : 0;
    // 손익비 (Profit Factor) = 총 수익 / 총 손실
    const profitFactor = totalLoss > 0 ? (totalWin / totalLoss) : (totalWin > 0 ? 999.99 : 0);

    // 색상 및 표시
    const totalColor = totalPnl >= 0 ? "#2ecc71" : "#ff5555";
    const totalSign = totalPnl >= 0 ? "+" : "";
    const wrColor = winRate >= 50 ? "#2ecc71" : winRate >= 30 ? "#ffd700" : "#ff5555";
    const pfColor = profitFactor >= 1.5 ? "#2ecc71" : profitFactor >= 1.0 ? "#ffd700" : "#ff5555";

    document.getElementById("pnlTotalAmount").textContent =
        `${totalSign}₩${Math.round(totalPnl).toLocaleString()}`;
    document.getElementById("pnlTotalAmount").style.color = totalColor;
    document.getElementById("pnlSellCount").textContent = `${sells.length}건`;
    document.getElementById("pnlWinCount").textContent = `${winCount}건`;
    document.getElementById("pnlLossCount").textContent = `${lossCount}건`;
    const wrEl = document.getElementById("pnlWinRate");
    wrEl.textContent = decided > 0 ? `${winRate.toFixed(1)}%` : "—";
    wrEl.style.color = decided > 0 ? wrColor : "rgba(255,255,255,0.5)";
    const pfEl = document.getElementById("pnlProfitFactor");
    pfEl.textContent = totalLoss > 0
        ? profitFactor.toFixed(2)
        : (totalWin > 0 ? "∞" : "—");
    pfEl.style.color = (totalLoss > 0 || totalWin > 0) ? pfColor : "rgba(255,255,255,0.5)";
}

/**
 * 필터 결과 상태 메시지 갱신
 */
function updateTradeFilterStatus(filteredCount, totalCount) {
    const el = document.getElementById("tradeFilterStatus");
    if (!el) return;
    if (filteredCount === totalCount) {
        // 필터 없이 전체 보는 중
        el.style.display = "none";
    } else {
        el.style.display = "block";
        el.innerHTML = `📋 <b>${filteredCount}</b>개 거래 표시 중 (전체 ${totalCount}개) · <a href="#" onclick="clearAllTradeFilters();return false;" style="color:#0099ff;text-decoration:underline;">필터 초기화</a>`;
    }
}

/**
 * 종목 검색 입력 초기화
 */
function clearTradeSearch() {
    const el = document.getElementById("tradeSearchInput");
    if (el) {
        el.value = "";
        applyTradeFilters();
    }
}

/**
 * 모든 거래 필터 초기화 (검색 + 매수매도 + 손익)
 */
function clearAllTradeFilters() {
    const search = document.getElementById("tradeSearchInput");
    const side = document.getElementById("tradeSideFilter");
    const pnl = document.getElementById("tradePnlFilter");
    if (search) search.value = "";
    if (side) side.value = "all";
    if (pnl) pnl.value = "all";
    applyTradeFilters();
}

/**
 * runAnalysis() - 분석 실행
 * 현재 선택된 시장(market selector)과 입력된 심볼을 사용하여
 * 백엔드 /api/analyze 엔드포인트를 호출합니다.
 */
async function runAnalysis() {
    const symbol = document.getElementById("analyzeSymbol").value.trim().toUpperCase();
    if (!symbol) { showToast(currentLang === "ko" ? "종목을 입력하세요" : "Enter a symbol", "error"); return; }

    // 현재 선택된 시장 가져오기 (all / us / kr)
    const marketSelector = document.getElementById("marketSelector");
    const market = marketSelector ? marketSelector.value : "all";

    showToast(`${currentLang === "ko" ? "분석 중" : "Analyzing"}: ${symbol}...`, "info");
    document.getElementById("analysisResults").innerHTML = `<div class="empty-state"><div class="empty-state-icon">⏳</div><div>${symbol} ${currentLang === "ko" ? "분석 중..." : "analyzing..."}</div></div>`;

    try {
        await fetch("/api/analyze", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ symbol, market })
        });
    } catch (e) { showToast(currentLang === "ko" ? "분석 요청 실패" : "Analysis request failed", "error"); }
}

/**
 * displayAnalysisResult() - 분석 결과 표시 (향상된 버전)
 *
 * 백엔드에서 받는 result 객체 구조:
 * {
 *   symbol: "AAPL",
 *   name: "Apple Inc.",           ← 종목명 (새로 추가)
 *   market: "US",                 ← 시장 구분 (새로 추가)
 *   sector: "Technology",         ← 섹터 (새로 추가)
 *   signal: "BUY" | "SELL" | "HOLD",
 *   strength: 0.0~1.0,
 *   price: 현재가,
 *   price_change_1d: 1일 등락률,  ← 새로 추가
 *   price_change_5d: 5일 등락률,  ← 새로 추가
 *   price_change_20d: 20일 등락률, ← 새로 추가
 *   rsi: RSI 지표값,              ← 새로 추가
 *   atr: ATR 변동폭,             ← 새로 추가
 *   volume: 거래량,               ← 새로 추가
 *   high_52w: 52주 최고,          ← 새로 추가
 *   low_52w: 52주 최저,           ← 새로 추가
 *   reasons: [분석 사유 배열]
 * }
 */
function displayAnalysisResult(result) {
    const container = document.getElementById("analysisResults");

    // 에러 처리
    if (result.error) {
        container.innerHTML = `<div class="analysis-card" style="border-left: 3px solid var(--danger);">
            <div style="font-weight:600;">${result.symbol}</div>
            <div class="text-mute mt-sm">${currentLang === "ko" ? "오류" : "Error"}: ${result.error}</div>
        </div>`;
        return;
    }

    // 신호에 따른 색상/배지 결정
    const badgeClass = result.signal === "BUY" ? "badge-buy" : result.signal === "SELL" ? "badge-sell" : "badge-hold";
    const signalColor = result.signal === "BUY" ? "var(--success)" : result.signal === "SELL" ? "var(--danger)" : "var(--warning)";

    // 시장 배지 (🇺🇸 US / 🇰🇷 KR)
    const marketFlag = result.market === "KR" ? "🇰🇷" : "🇺🇸";
    const marketLabel = result.market === "KR" ? "KOSPI/KOSDAQ" : "NYSE/NASDAQ";

    // 등락률 표시 헬퍼: 양수면 초록+, 음수면 빨강
    const pctHtml = (val) => {
        if (val === undefined || val === null) return '<span class="text-mute">-</span>';
        const cls = val >= 0 ? "text-success" : "text-danger";
        const sign = val >= 0 ? "+" : "";
        return `<span class="${cls}">${sign}${val.toFixed(2)}%</span>`;
    };

    // 기존 empty-state 제거
    const existing = container.querySelector(".empty-state");
    if (existing) container.innerHTML = "";

    // 분석 카드 생성
    const div = document.createElement("div");
    div.className = "analysis-card";
    div.style.borderLeft = `3px solid ${signalColor}`;

    // ── 카드 HTML 구성 ──
    div.innerHTML = `
        <!-- 헤더: 종목명 + 심볼 + 신호 배지 + 현재가 -->
        <div class="flex-between" style="align-items:flex-start;">
            <div>
                <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
                    <span style="font-size:18px; font-weight:600;">${result.name || result.symbol}</span>
                    <span class="text-mute" style="font-size:13px;">${result.symbol}</span>
                    <span class="badge ${badgeClass}">${result.signal}</span>
                    <span style="font-size:12px; opacity:0.7;">${marketFlag} ${marketLabel}</span>
                </div>
                ${result.sector ? `<div class="text-mute" style="font-size:12px; margin-top:4px;">${currentLang === "ko" ? "섹터" : "Sector"}: ${result.sector}</div>` : ""}
            </div>
            <div style="text-align:right;">
                <div style="font-size:20px; font-weight:300;">${result.price ? result.price.toFixed(2) : "-"}</div>
                <div style="font-size:12px;">${pctHtml(result.change_1d)} ${currentLang === "ko" ? "오늘" : "today"}</div>
            </div>
        </div>

        <!-- 메트릭 그리드: RSI, ATR, 등락률, 거래량, 52주 범위 -->
        <div class="analysis-metrics">
            <div class="analysis-metric">
                <div class="analysis-metric-label">RSI</div>
                <div class="analysis-metric-value" style="color:${result.rsi > 70 ? 'var(--danger)' : result.rsi < 30 ? 'var(--success)' : 'inherit'}">
                    ${result.rsi ? result.rsi.toFixed(1) : "-"}
                </div>
            </div>
            <div class="analysis-metric">
                <div class="analysis-metric-label">ATR</div>
                <div class="analysis-metric-value">${result.atr ? result.atr.toFixed(2) : "-"}</div>
            </div>
            <div class="analysis-metric">
                <div class="analysis-metric-label">${currentLang === "ko" ? "5일" : "5D"}</div>
                <div class="analysis-metric-value">${pctHtml(result.change_5d)}</div>
            </div>
            <div class="analysis-metric">
                <div class="analysis-metric-label">${currentLang === "ko" ? "20일" : "20D"}</div>
                <div class="analysis-metric-value">${pctHtml(result.change_20d)}</div>
            </div>
            <div class="analysis-metric">
                <div class="analysis-metric-label">${currentLang === "ko" ? "거래량" : "Vol"}</div>
                <div class="analysis-metric-value">${result.volume ? formatVolume(result.volume) : "-"}</div>
            </div>
            <div class="analysis-metric">
                <div class="analysis-metric-label">${currentLang === "ko" ? "52주 범위" : "52W"}</div>
                <div class="analysis-metric-value" style="font-size:11px;">
                    ${result.low_52w ? result.low_52w.toFixed(0) : "?"} ~ ${result.high_52w ? result.high_52w.toFixed(0) : "?"}
                </div>
            </div>
        </div>

        <!-- 강도 바 -->
        <div style="margin-top:12px;">
            <div class="text-mute" style="font-size:12px; margin-bottom:4px;">
                ${currentLang === "ko" ? "신호 강도" : "Signal Strength"}: ${(result.strength * 100).toFixed(0)}%
            </div>
            <div style="background:rgba(255,255,255,0.1); border-radius:4px; height:6px; overflow:hidden;">
                <div style="width:${result.strength * 100}%; height:100%; background:${signalColor}; border-radius:4px; transition:width 0.5s;"></div>
            </div>
        </div>

        <!-- 분석 사유 -->
        ${(result.reasons && result.reasons.length > 0) ? `
        <div class="mt-sm" style="font-size:13px; color: var(--text-secondary); margin-top:12px;">
            ${result.reasons.map(r => "• " + r).join("<br>")}
        </div>` : ""}
    `;

    container.prepend(div);
    showToast(`${result.symbol}: ${result.signal} (${(result.strength * 100).toFixed(0)}%)`, "success");
}

/**
 * formatVolume() - 거래량을 읽기 쉬운 형태로 변환
 * 예: 1234567 → "1.23M", 45600 → "45.6K"
 */
function formatVolume(vol) {
    if (vol >= 1000000000) return (vol / 1000000000).toFixed(1) + "B";
    if (vol >= 1000000) return (vol / 1000000).toFixed(1) + "M";
    if (vol >= 1000) return (vol / 1000).toFixed(1) + "K";
    return vol.toString();
}


// ═══════════════════════════════════════════════════════════════════════════
// 5.5 종목 검색 자동완성 (Search Autocomplete)
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 디바운스(debounce) 유틸리티
 * 사용자가 타이핑을 멈춘 후 일정 시간(delay ms) 후에 함수를 실행합니다.
 * 매 키 입력마다 API를 호출하면 서버에 부담이 되므로, 300ms 대기 후 호출합니다.
 */
function debounce(fn, delay = 300) {
    let timer = null;
    return (...args) => {
        clearTimeout(timer);
        timer = setTimeout(() => fn(...args), delay);
    };
}

/**
 * 검색 자동완성 핸들러
 * #analyzeSymbol 입력 필드에 타이핑하면:
 * 1. 300ms 디바운스 후 /api/stock/search?q=검색어&market=선택시장 호출
 * 2. 결과를 #searchDropdown에 렌더링
 * 3. 결과 항목 클릭 시 심볼을 입력 필드에 채움
 */
const handleSearchInput = debounce(async (query) => {
    const dropdown = document.getElementById("searchDropdown");
    if (!dropdown) return;

    // 2글자 미만이면 드롭다운 숨기기
    if (!query || query.length < 2) {
        dropdown.style.display = "none";
        return;
    }

    // 현재 선택된 시장 필터
    const marketSelector = document.getElementById("marketSelector");
    const market = marketSelector ? marketSelector.value : "all";

    try {
        const res = await fetch(`/api/stock/search?q=${encodeURIComponent(query)}&market=${market}`);
        const results = await res.json();

        if (!results || results.length === 0) {
            dropdown.style.display = "none";
            return;
        }

        // 검색 결과 렌더링 (최대 10개)
        dropdown.innerHTML = results.slice(0, 10).map(item => `
            <div class="search-item" data-symbol="${item.symbol}" data-name="${item.name || ''}">
                <span class="search-item-symbol">${item.symbol}</span>
                <span class="search-item-name">${item.name || ''}</span>
                <span class="search-item-market">${item.market === "KR" ? "🇰🇷" : "🇺🇸"}</span>
            </div>
        `).join("");

        dropdown.style.display = "block";

        // 각 결과 항목에 클릭 이벤트 바인딩
        dropdown.querySelectorAll(".search-item").forEach(el => {
            el.addEventListener("click", () => {
                const symbol = el.dataset.symbol;
                document.getElementById("analyzeSymbol").value = symbol;
                dropdown.style.display = "none";

                // 선택 후 종목 정보 배너 업데이트 (선택사항)
                loadStockInfo(symbol);
            });
        });
    } catch (e) {
        dropdown.style.display = "none";
    }
}, 300);

/**
 * loadStockInfo() - 선택한 종목의 간략 정보를 상단 배너에 표시
 * /api/stock/info?symbol=XXX 호출 후 #stockInfoBanner에 렌더링
 */
async function loadStockInfo(symbol) {
    const banner = document.getElementById("stockInfoBanner");
    if (!banner) return;

    try {
        const res = await fetch(`/api/stock/info?symbol=${encodeURIComponent(symbol)}`);
        const info = await res.json();

        if (info.error) {
            banner.style.display = "none";
            return;
        }

        // 배너에 종목명, 시장, 현재가 표시
        const flag = info.market === "KR" ? "🇰🇷" : "🇺🇸";
        banner.innerHTML = `
            <span style="font-weight:600;">${flag} ${info.name || symbol}</span>
            <span class="text-mute" style="margin-left:8px;">${symbol}</span>
            ${info.price ? `<span style="margin-left:auto; font-weight:300;">${info.price.toFixed(2)}</span>` : ""}
            ${info.sector ? `<span class="text-mute" style="margin-left:12px; font-size:12px;">${info.sector}</span>` : ""}
        `;
        banner.style.display = "flex";
    } catch (e) {
        if (banner) banner.style.display = "none";
    }
}


// ═══════════════════════════════════════════════════════════════════════════
// 6. Live 모드 & 워치리스트
// ═══════════════════════════════════════════════════════════════════════════

// ═══════════════════════════════════════════════════════════════════════════
// 브로커 API 키 관리
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 브로커 선택에 따라 API 키 입력 섹션을 표시/숨김
 *
 * - paper: API 키 섹션 전체 숨김
 * - alpaca: Alpaca 키만 표시
 * - kis: KIS 키만 표시
 * - dual: 양쪽 모두 표시
 */
function toggleBrokerApiFields() {
    const broker = document.getElementById("settingBroker").value;
    const section = document.getElementById("brokerApiSection");
    const kisFields = document.getElementById("kisApiFields");
    const alpacaFields = document.getElementById("alpacaApiFields");

    if (!section) return;

    if (broker === "paper") {
        section.style.display = "none";
    } else {
        section.style.display = "block";
        // 브로커별 필드 표시
        if (kisFields) kisFields.style.display = (broker === "kis" || broker === "dual") ? "block" : "none";
        if (alpacaFields) alpacaFields.style.display = (broker === "alpaca" || broker === "dual") ? "block" : "none";
    }
}

/**
 * 브로커 API 연결 테스트
 *
 * 서버에 /api/broker/test POST 요청을 보내
 * 현재 입력된 API 키로 연결이 되는지 확인합니다.
 */
async function testBrokerConnection() {
    const resultEl = document.getElementById("brokerTestResult");
    if (resultEl) resultEl.textContent = "연결 테스트 중...";

    try {
        // 먼저 현재 설정을 저장 (API 키가 서버에 반영되어야 함)
        await saveSettings();

        const res = await fetch("/api/broker/test", { method: "POST" });
        const data = await res.json();

        if (data.success) {
            const parts = [];
            if (data.kr_status) parts.push("KIS: " + (data.kr_connected ? "✅ 연결 성공" : "❌ " + (data.kr_error || "실패")));
            if (data.us_status) parts.push("Alpaca: " + (data.us_connected ? "✅ 연결 성공" : "❌ " + (data.us_error || "실패")));
            if (resultEl) {
                resultEl.textContent = parts.join(" | ") || data.message || "테스트 완료";
                resultEl.style.color = (data.kr_connected || data.us_connected) ? "var(--success)" : "var(--danger)";
            }
            showToast(parts.join(" | ") || "연결 테스트 완료", (data.kr_connected || data.us_connected) ? "success" : "error");
        } else {
            if (resultEl) {
                resultEl.textContent = data.error || "테스트 실패";
                resultEl.style.color = "var(--danger)";
            }
            showToast(data.error || "연결 테스트 실패", "error");
        }
    } catch (e) {
        if (resultEl) {
            resultEl.textContent = "연결 오류: " + e.message;
            resultEl.style.color = "var(--danger)";
        }
    }
}

function toggleLiveMode() {
    const toggle = document.getElementById("toggleLive");
    const isActive = toggle.classList.contains("active");
    if (!isActive) {
        const msg = currentLang === "ko"
            ? "⚠️ 경고: Live 모드는 실제 돈을 사용합니다.\n정말 전환하시겠습니까?"
            : "⚠️ WARNING: Live mode uses REAL MONEY.\nAre you sure?";
        if (!confirm(msg)) return;
    }
    toggle.classList.toggle("active");
}

let watchlists = { us: [], kr: [] };

function renderWatchlist(market, list) {
    watchlists[market] = list;
    const container = document.getElementById(market + "Watchlist");
    // 한국 종목(.KS/.KQ)이면 서버에서 이름을 비동기 조회하여 태그에 표시
    container.innerHTML = list.map(symbol => {
        const tagId = "wl_" + symbol.replace(/\./g, "_");
        return `<div class="watchlist-tag" id="${tagId}"><span>${symbol}</span><span class="remove" onclick="removeSymbol('${market}', '${symbol}')">×</span></div>`;
    }).join("");

    // 한국 종목 이름 비동기 로드
    if (market === "kr") {
        list.forEach(symbol => {
            if (symbol.endsWith(".KS") || symbol.endsWith(".KQ")) {
                fetch(`/api/stock/name?symbol=${encodeURIComponent(symbol)}`)
                    .then(r => r.json())
                    .then(data => {
                        if (data.name && data.name !== symbol) {
                            const tagId = "wl_" + symbol.replace(/\./g, "_");
                            const el = document.getElementById(tagId);
                            if (el) {
                                const span = el.querySelector("span:first-child");
                                if (span) span.textContent = data.name;
                            }
                        }
                    })
                    .catch(() => {});
            }
        });
    }
}

function addSymbol(market) {
    const input = document.getElementById(market === "us" ? "addUsSymbol" : "addKrSymbol");
    const symbol = input.value.trim().toUpperCase();
    if (!symbol) return;
    if (!watchlists[market].includes(symbol)) {
        watchlists[market].push(symbol);
        renderWatchlist(market, watchlists[market]);
    }
    input.value = "";
}

function removeSymbol(market, symbol) {
    watchlists[market] = watchlists[market].filter(s => s !== symbol);
    renderWatchlist(market, watchlists[market]);
}

/**
 * testDiscord() - 디스코드 웹훅 연결 테스트
 * Settings에서 "테스트" 버튼 클릭 시 호출됩니다.
 */
async function testDiscord() {
    const url = document.getElementById("settingDiscordWebhook").value.trim();
    if (!url) {
        showToast(currentLang === "ko" ? "웹훅 URL을 입력하세요" : "Enter webhook URL", "error");
        return;
    }
    try {
        const res = await fetch("/api/discord/test", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ webhook_url: url })
        });
        const data = await res.json();
        if (data.success) {
            showToast(currentLang === "ko" ? "디스코드 연결 성공! 채널을 확인하세요" : "Discord connected! Check your channel", "success");
        } else {
            showToast((currentLang === "ko" ? "실패: " : "Failed: ") + (data.error || "Unknown error"), "error");
        }
    } catch (e) {
        showToast(currentLang === "ko" ? "연결 오류" : "Connection error", "error");
    }
}


// ═══════════════════════════════════════════════════════════════════════════
// 6.45 디스코드 봇 (양방향 명령) 제어
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 디스코드 봇 초대 링크 생성
 *
 * Application ID만으로 봇 초대 URL을 자동 생성합니다.
 * OAuth2 URL Generator의 Redirect URI 설정 없이 바로 사용 가능합니다.
 *
 * 권한 비트: Send Messages(2048) + Embed Links(16384) + Read Message History(65536)
 *            + Use Slash Commands(2147483648) = 2147567616
 */
function generateDiscordInvite() {
    const appId = document.getElementById("settingDiscordBotAppId").value.trim();
    if (!appId) {
        showToast(currentLang === "ko" ? "Application ID를 입력하세요" : "Enter Application ID", "error");
        return;
    }

    // 권한: Send Messages + Embed Links + Read Message History + Use Slash Commands
    const permissions = 2147567616;
    const url = `https://discord.com/oauth2/authorize?client_id=${appId}&permissions=${permissions}&scope=bot+applications.commands`;

    const linkDiv = document.getElementById("discordInviteLink");
    const linkEl = document.getElementById("discordInviteUrl");
    if (linkDiv && linkEl) {
        linkEl.href = url;
        linkEl.textContent = url;
        linkDiv.style.display = "block";
    }

    // 클립보드에도 복사
    navigator.clipboard.writeText(url).then(() => {
        showToast(currentLang === "ko"
            ? "초대 링크가 클립보드에 복사되었습니다! 브라우저에 붙여넣기하세요."
            : "Invite link copied! Paste in browser.", "success");
    }).catch(() => {
        showToast(currentLang === "ko"
            ? "링크가 생성되었습니다. 아래 링크를 클릭하세요."
            : "Link generated. Click the link below.", "info");
    });
}

/**
 * 디스코드 봇 연결 시작
 */
async function startDiscordBot() {
    const token = document.getElementById("settingDiscordBotToken").value.trim();
    if (!token) {
        showToast(currentLang === "ko" ? "봇 토큰을 입력하세요" : "Enter bot token", "error");
        return;
    }

    const channelId = document.getElementById("settingDiscordBotChannel").value.trim();
    const statusEl = document.getElementById("discordBotStatus");
    if (statusEl) statusEl.textContent = "● 연결 중...";

    try {
        const res = await fetch("/api/discord/bot/start", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ token: token, channel_id: channelId })
        });
        const data = await res.json();
        if (data.success) {
            showToast(currentLang === "ko" ? "디스코드 봇 연결 시작!" : "Discord bot connecting!", "success");
            // 3초 후 상태 갱신 (연결 시간 대기)
            setTimeout(refreshDiscordBotStatus, 3000);
        } else {
            showToast((currentLang === "ko" ? "실패: " : "Failed: ") + (data.error || ""), "error");
            if (statusEl) statusEl.textContent = "● 연결 실패";
            if (statusEl) statusEl.style.color = "var(--danger)";
        }
    } catch (e) {
        showToast(currentLang === "ko" ? "연결 오류" : "Connection error", "error");
        if (statusEl) statusEl.textContent = "● 오류";
    }
}

/**
 * 디스코드 봇 해제
 */
async function stopDiscordBot() {
    try {
        const res = await fetch("/api/discord/bot/stop", { method: "POST" });
        const data = await res.json();
        if (data.success) {
            showToast(currentLang === "ko" ? "디스코드 봇 해제됨" : "Discord bot disconnected", "info");
        }
    } catch (e) {
        // 무시
    }
    refreshDiscordBotStatus();
}

/**
 * 디스코드 봇 연결 상태 갱신
 */
async function refreshDiscordBotStatus() {
    const statusEl = document.getElementById("discordBotStatus");
    if (!statusEl) return;

    try {
        const res = await fetch("/api/discord/bot/status");
        const data = await res.json();
        if (data.connected) {
            statusEl.textContent = `● 연결됨 (${data.username || "Bot"} · 서버 ${data.guilds}개)`;
            statusEl.style.color = "var(--success)";
        } else if (data.running) {
            statusEl.textContent = "● 연결 중...";
            statusEl.style.color = "var(--warning)";
            // 아직 연결 안 됐으면 5초 후 재확인
            setTimeout(refreshDiscordBotStatus, 5000);
        } else {
            statusEl.textContent = "● 미연결";
            statusEl.style.color = "var(--text-muted)";
        }
    } catch (e) {
        statusEl.textContent = "● 상태 확인 불가";
        statusEl.style.color = "var(--text-muted)";
    }
}


// ═══════════════════════════════════════════════════════════════════════════
// 6.5 시장 스캐너 & 섹터 관심 분야
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 선택된 관심 섹터 목록
 * Settings에서 체크/해제하면 이 배열이 업데이트되고,
 * saveSettings()에 포함되어 서버에 저장됩니다.
 */
let selectedSectors = [];

/**
 * loadSectors() - 서버에서 섹터 목록을 가져와 Settings 탭에 렌더링
 * 페이지 로드 시 호출됩니다.
 */
async function loadSectors() {
    try {
        const res = await fetch("/api/sectors");
        const data = await res.json();
        const grid = document.getElementById("sectorGrid");
        if (!grid) return;

        selectedSectors = data.selected || [];

        grid.innerHTML = data.sectors.map(s => {
            const isActive = selectedSectors.includes(s.key);
            const name = currentLang === "ko" ? s.name_ko : s.name_en;
            return `
                <div class="sector-chip ${isActive ? 'active' : ''}" data-sector="${s.key}" onclick="toggleSector('${s.key}')">
                    <span class="sector-icon">${s.icon}</span>
                    <span class="sector-name">${name}</span>
                    <span class="sector-count">${s.stock_count}</span>
                    <span class="sector-check">✓</span>
                </div>
            `;
        }).join("");
    } catch (e) {
        console.error("[섹터] 로드 실패:", e);
    }
}

/**
 * toggleSector() - 섹터 칩 클릭 시 선택/해제 토글
 */
function toggleSector(sectorKey) {
    const idx = selectedSectors.indexOf(sectorKey);
    if (idx >= 0) {
        selectedSectors.splice(idx, 1);
    } else {
        selectedSectors.push(sectorKey);
    }

    // UI 업데이트
    document.querySelectorAll(".sector-chip").forEach(chip => {
        if (chip.dataset.sector === sectorKey) {
            chip.classList.toggle("active");
        }
    });
}

/**
 * runScanner() - 시장 스캐너 실행
 * Overview 탭의 "스캔" 버튼 클릭 시 호출됩니다.
 * /api/scanner를 호출하고, 결과가 아직 없으면 폴링합니다.
 */
async function runScanner() {
    const container = document.getElementById("scannerResults");
    const statusEl = document.getElementById("scannerStatus");

    // 로딩 표시
    container.innerHTML = `<div class="scanner-loading"><div class="spinner"></div><span>${i18n.scanner_scanning[currentLang]}</span></div>`;
    if (statusEl) statusEl.textContent = "";

    try {
        const res = await fetch("/api/scanner");
        const data = await res.json();

        if (data.scanning && (!data.results || data.results.length === 0)) {
            // 아직 스캔 중 → 5초 후 재시도
            setTimeout(runScanner, 5000);
            return;
        }

        renderScannerResults(data);
    } catch (e) {
        container.innerHTML = `<div class="empty-state"><div class="empty-state-icon">⚠️</div><div>${currentLang === "ko" ? "스캔 실패" : "Scan failed"}</div></div>`;
    }
}

/**
 * renderScannerResults() - 스캐너 결과를 카드 그리드로 렌더링
 *
 * 기본 결과(상위 N개)를 표시하고, 전체 결과가 더 있으면 "더보기" 버튼 표시.
 * 봇이 자동 발굴한 종목이 있으면 별도 섹션으로 함께 표시.
 */
// 전체 스캐너 데이터를 전역에 보관 (더보기 토글용)
let _scannerFullData = null;
let _scannerExpanded = false;

function renderScannerResults(data) {
    const container = document.getElementById("scannerResults");
    const statusEl = document.getElementById("scannerStatus");
    const showMoreEl = document.getElementById("scannerShowMore");
    const discoveredSection = document.getElementById("discoveredSection");

    _scannerFullData = data;
    _scannerExpanded = false;

    if (!data.results || data.results.length === 0) {
        container.innerHTML = `<div class="empty-state"><div class="empty-state-icon">🔍</div><div data-i18n="empty_scanner">${i18n.empty_scanner[currentLang]}</div></div>`;
        if (showMoreEl) showMoreEl.style.display = "none";
        if (discoveredSection) discoveredSection.style.display = "none";
        return;
    }

    // 스캔 시각 표시
    if (statusEl && data.scanned_at) {
        const scanTime = new Date(data.scanned_at);
        statusEl.textContent = `${i18n.scanner_scanned_at[currentLang]}: ${scanTime.toLocaleTimeString()}`;
    }

    // 기본 결과 렌더링 (상위 N개)
    container.innerHTML = _buildScannerGrid(data.results);

    // ── 더보기 버튼 제어 ──
    const allResults = data.all_results || [];
    const extraCount = allResults.length - data.results.length;
    if (showMoreEl) {
        if (extraCount > 0) {
            showMoreEl.style.display = "block";
            document.getElementById("scannerExtraCount").textContent = extraCount;
            document.getElementById("scannerToggleBtn").textContent =
                (currentLang === "ko" ? "더보기" : "Show More") + ` (${extraCount}${currentLang === "ko" ? "개" : ""})`;
        } else {
            showMoreEl.style.display = "none";
        }
    }

    // ── 자동 발굴 종목 표시 (이름 포함) ──
    if (discoveredSection) {
        const disc = data.discovered || {};
        const usList = disc.us || [];
        const krList = disc.kr || [];
        const total = usList.length + krList.length;

        if (disc.enabled && total > 0) {
            discoveredSection.style.display = "block";
            document.getElementById("discoveredCount").textContent =
                `US ${usList.length}${currentLang === "ko" ? "개" : ""} · KR ${krList.length}${currentLang === "ko" ? "개" : ""}`;

            const listEl = document.getElementById("discoveredList");
            let html = '<div style="display:flex; flex-wrap:wrap; gap:6px;">';
            usList.forEach(item => {
                // item은 {symbol, name} 객체 또는 문자열(하위호환)
                const sym = typeof item === "string" ? item : item.symbol;
                const name = typeof item === "string" ? item : (item.name || item.symbol);
                const label = name !== sym ? `${name} (${sym})` : sym;
                html += `<span class="badge badge-hold" style="cursor:pointer;font-size:11px;" onclick="showStockLinks('${sym}','${sym}')">🇺🇸 ${label}</span>`;
            });
            krList.forEach(item => {
                const sym = typeof item === "string" ? item : item.symbol;
                const name = typeof item === "string" ? sym.replace(".KS","").replace(".KQ","") : (item.name || sym);
                const clean = sym.replace(".KS","").replace(".KQ","");
                // name이 이미 "삼성전자 (005930)" 형태면 그대로, 아니면 코드 추가
                const label = name.includes("(") ? name : (name !== clean ? `${name} (${clean})` : clean);
                html += `<span class="badge badge-hold" style="cursor:pointer;font-size:11px;" onclick="showStockLinks('${sym}','${clean}')">🇰🇷 ${label}</span>`;
            });
            html += '</div>';
            listEl.innerHTML = html;
        } else {
            discoveredSection.style.display = "none";
        }
    }
}

/**
 * _buildScannerGrid() - 스캐너 카드 배열을 HTML 그리드로 변환
 */
function _buildScannerGrid(items) {
    return `<div class="scanner-grid">${items.map(item => {
        const badgeClass = item.signal === "BUY" ? "badge-buy" : item.signal === "SELL" ? "badge-sell" : "badge-hold";
        const changeClass = item.change_1d >= 0 ? "text-success" : "text-danger";
        const changeSign = item.change_1d >= 0 ? "+" : "";
        const flag = item.market === "KR" ? "KR" : "US";

        // ── 종목명 + 코드 표시 로직 ──
        const rawName = item.name || item.symbol;
        const cleanSymbol = item.symbol.replace(".KS", "").replace(".KQ", "");
        const hasRealName = rawName !== item.symbol && rawName !== cleanSymbol;
        const displayName = hasRealName ? rawName : cleanSymbol;
        const displayCode = hasRealName ? cleanSymbol : "";

        return `
            <div class="scanner-card" onclick="showStockLinks('${item.symbol}', '${(rawName).replace(/'/g, "\\'")}')" style="cursor:pointer;">
                <div class="scanner-card-header">
                    <div>
                        <div class="scanner-card-name">
                            <span style="font-size:10px;opacity:0.5;margin-right:4px;">${flag}</span>
                            <strong>${displayName}</strong>
                            ${displayCode ? `<span style="opacity:0.5;font-size:12px;margin-left:4px;">${displayCode}</span>` : ""}
                        </div>
                        <div class="scanner-card-symbol">${item.symbol} · ${item.sector}</div>
                    </div>
                    <span class="badge ${badgeClass}">${item.signal}</span>
                </div>
                <div class="scanner-card-metrics">
                    <span>${currentLang === "ko" ? "현재가" : "Price"}: <span class="metric-value">${item.price.toLocaleString()}</span></span>
                    <span class="${changeClass}">${changeSign}${item.change_1d}%</span>
                    <span>RSI: <span class="metric-value" style="color:${item.rsi > 70 ? 'var(--danger)' : item.rsi < 30 ? 'var(--success)' : 'inherit'}">${item.rsi}</span></span>
                    <span>${currentLang === "ko" ? "강도" : "Str"}: ${(item.strength * 100).toFixed(0)}%</span>
                </div>
            </div>
        `;
    }).join("")}</div>`;
}

/**
 * toggleScannerAll() - 더보기/접기 토글
 */
function toggleScannerAll() {
    if (!_scannerFullData) return;
    _scannerExpanded = !_scannerExpanded;

    // 토글 후 현재 활성 시장 필터를 그대로 적용하여 다시 렌더링
    filterScanner(_scannerMarketFilter);

    // 버튼 텍스트 갱신
    const btn = document.getElementById("scannerToggleBtn");
    if (btn) {
        if (_scannerExpanded) {
            btn.textContent = currentLang === "ko" ? "접기" : "Show Less";
        } else {
            const allResults = _scannerFullData.all_results || [];
            const topResults = _scannerFullData.results || [];
            // 필터 적용된 추가 개수 계산
            const allFiltered = _scannerMarketFilter === "all"
                ? allResults
                : allResults.filter(item => item.market === _scannerMarketFilter);
            const topFiltered = _scannerMarketFilter === "all"
                ? topResults
                : topResults.filter(item => item.market === _scannerMarketFilter);
            const extraCount = allFiltered.length - topFiltered.length;
            btn.textContent = (currentLang === "ko" ? "더보기" : "Show More") + ` (${extraCount}${currentLang === "ko" ? "개" : ""})`;
        }
    }
}

// ── 스캐너 시장 필터 상태 ──
let _scannerMarketFilter = "all";

/**
 * filterScanner() - 스캐너 시장 필터 전환
 */
function filterScanner(market) {
    _scannerMarketFilter = market;

    // 탭 활성화 토글
    document.querySelectorAll("#scannerMarketFilter .market-filter-btn").forEach(btn => {
        btn.classList.toggle("active", btn.dataset.market === market);
    });

    if (!_scannerFullData) return;

    const container = document.getElementById("scannerResults");
    const showMoreEl = document.getElementById("scannerShowMore");

    // 필터 적용할 소스 결정 (더보기가 펼쳐져있으면 전체, 아니면 상위)
    const sourceResults = _scannerExpanded
        ? (_scannerFullData.all_results || [])
        : _scannerFullData.results;

    // 시장 필터 적용
    const filtered = market === "all"
        ? sourceResults
        : sourceResults.filter(item => item.market === market);

    if (filtered.length === 0) {
        const label = market === "KR"
            ? (currentLang === "ko" ? "한국" : "Korea")
            : (currentLang === "ko" ? "미국" : "US");
        container.innerHTML = `<div class="empty-state"><div class="empty-state-icon">🔍</div><div>${label} ${currentLang === "ko" ? "시장 종목이 없습니다" : "market stocks not found"}</div></div>`;
    } else {
        container.innerHTML = _buildScannerGrid(filtered);
    }

    // 더보기 버튼도 필터 반영
    if (showMoreEl && _scannerExpanded) {
        showMoreEl.style.display = "none";
    } else if (showMoreEl && !_scannerExpanded) {
        const allFiltered = market === "all"
            ? (_scannerFullData.all_results || [])
            : (_scannerFullData.all_results || []).filter(item => item.market === market);
        const extraCount = allFiltered.length - filtered.length;
        showMoreEl.style.display = extraCount > 0 ? "block" : "none";
        if (extraCount > 0) {
            document.getElementById("scannerExtraCount").textContent = extraCount;
        }
    }

    // 발굴 종목도 필터에 맞게 표시 (이름 포함)
    const discoveredSection = document.getElementById("discoveredSection");
    if (discoveredSection && _scannerFullData.discovered) {
        const disc = _scannerFullData.discovered;
        if (!disc.enabled) { discoveredSection.style.display = "none"; return; }

        const usList = market === "KR" ? [] : (disc.us || []);
        const krList = market === "US" ? [] : (disc.kr || []);
        const total = usList.length + krList.length;

        if (total > 0) {
            discoveredSection.style.display = "block";
            const listEl = document.getElementById("discoveredList");
            let html = '<div style="display:flex; flex-wrap:wrap; gap:6px;">';
            usList.forEach(item => {
                const sym = typeof item === "string" ? item : item.symbol;
                const name = typeof item === "string" ? item : (item.name || item.symbol);
                const label = name !== sym ? `${name} (${sym})` : sym;
                html += `<span class="badge badge-hold" style="cursor:pointer;font-size:11px;" onclick="showStockLinks('${sym}','${sym}')">🇺🇸 ${label}</span>`;
            });
            krList.forEach(item => {
                const sym = typeof item === "string" ? item : item.symbol;
                const name = typeof item === "string" ? sym.replace(".KS","").replace(".KQ","") : (item.name || sym);
                const clean = sym.replace(".KS","").replace(".KQ","");
                const label = name.includes("(") ? name : (name !== clean ? `${name} (${clean})` : clean);
                html += `<span class="badge badge-hold" style="cursor:pointer;font-size:11px;" onclick="showStockLinks('${sym}','${clean}')">🇰🇷 ${label}</span>`;
            });
            html += '</div>';
            listEl.innerHTML = html;
        } else {
            discoveredSection.style.display = "none";
        }
    }
}

/**
 * quickAnalyze() - 스캐너 카드 클릭 시 분석 탭으로 이동하여 상세 분석
 */
function quickAnalyze(symbol) {
    // 분석 탭으로 전환
    document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
    document.querySelectorAll("section[id^='tab-']").forEach(s => s.style.display = "none");
    document.querySelector('[data-tab="analyze"]').classList.add("active");
    document.getElementById("tab-analyze").style.display = "block";

    // 심볼 입력 및 분석 실행
    document.getElementById("analyzeSymbol").value = symbol;
    runAnalysis();
}


// ═══════════════════════════════════════════════════════════════════════════
// 6.6 종목 링크 모달 & 뉴스 로드
// ═══════════════════════════════════════════════════════════════════════════

/**
 * showStockLinks() - 종목 클릭 시 외부 링크 + 뉴스 모달 표시
 *
 * 한국 주식: 네이버증권, Investing.com, Google 뉴스 검색
 * 미국 주식: Yahoo Finance, Investing.com, Google Finance, MarketWatch
 *
 * Parameters:
 *   symbol: 종목 코드 (예: "005930.KS", "AAPL")
 *   displayName: 화면에 표시할 이름 (예: "삼성전자 (005930)")
 */
function showStockLinks(symbol, displayName) {
    const modal = document.getElementById("stockLinkModal");
    const titleEl = document.getElementById("modalStockName");
    const symbolEl = document.getElementById("modalStockSymbol");
    const linksEl = document.getElementById("modalLinks");
    const newsEl = document.getElementById("modalNewsContent");

    titleEl.textContent = displayName || symbol;
    symbolEl.textContent = symbol;

    // 시장 판별
    const isKR = symbol.endsWith(".KS") || symbol.endsWith(".KQ");
    const pureCode = symbol.replace(".KS", "").replace(".KQ", "");

    // ── 외부 링크 생성 ──
    let links = [];

    if (isKR) {
        // 한국 주식 링크
        links = [
            {
                icon: "📈", label: currentLang === "ko" ? "네이버증권" : "Naver Finance",
                desc: currentLang === "ko" ? "차트, 재무제표, 뉴스" : "Charts, financials, news",
                url: `https://finance.naver.com/item/main.naver?code=${pureCode}`
            },
            {
                icon: "🌐", label: "TradingView",
                desc: currentLang === "ko" ? "글로벌 차트, 기술적 분석" : "Global charts, technical analysis",
                url: `https://www.tradingview.com/symbols/KRX-${pureCode}/`
            },
            {
                icon: "🔍", label: currentLang === "ko" ? "Google 검색" : "Google Search",
                desc: currentLang === "ko" ? "최신 뉴스, 분석 글" : "Latest news, analysis",
                url: `https://www.google.com/search?q=${encodeURIComponent(displayName + " 주식 뉴스")}`
            },
            {
                icon: "📰", label: currentLang === "ko" ? "Google 뉴스" : "Google News",
                desc: currentLang === "ko" ? "종목 관련 뉴스 모음" : "Stock-related news feed",
                url: `https://news.google.com/search?q=${encodeURIComponent(displayName + " 주식")}&hl=ko`
            },
            {
                icon: "📊", label: currentLang === "ko" ? "네이버 토론" : "Naver Discussion",
                desc: currentLang === "ko" ? "투자자 의견, 게시판" : "Investor opinions, forum",
                url: `https://finance.naver.com/item/board.naver?code=${pureCode}`
            },
            {
                icon: "🔬", label: currentLang === "ko" ? "즉시 분석" : "Quick Analyze",
                desc: currentLang === "ko" ? "이 봇의 기술적 분석" : "Technical analysis by this bot",
                url: "#",
                onclick: `closeStockModal(); quickAnalyze('${symbol}');`
            },
        ];
    } else {
        // 미국 주식 링크
        links = [
            {
                icon: "📈", label: "Yahoo Finance",
                desc: currentLang === "ko" ? "차트, 재무, 뉴스 (영문)" : "Charts, financials, news",
                url: `https://finance.yahoo.com/quote/${symbol}`
            },
            {
                icon: "🌐", label: "TradingView",
                desc: currentLang === "ko" ? "글로벌 차트, 기술적 분석" : "Global charts, technical analysis",
                url: `https://www.tradingview.com/symbols/${symbol}/`
            },
            {
                icon: "💹", label: "Google Finance",
                desc: currentLang === "ko" ? "구글 금융 (간편 차트)" : "Simple charts & overview",
                url: `https://www.google.com/finance/quote/${symbol}:${symbol.length <= 4 ? 'NASDAQ' : 'NYSE'}`
            },
            {
                icon: "📰", label: currentLang === "ko" ? "Google 뉴스" : "Google News",
                desc: currentLang === "ko" ? "최신 뉴스 검색" : "Latest news search",
                url: `https://news.google.com/search?q=${encodeURIComponent(symbol + " stock")}&hl=en`
            },
            {
                icon: "📊", label: "MarketWatch",
                desc: currentLang === "ko" ? "시세, 뉴스, 분석 (영문)" : "Quotes, news, analysis",
                url: `https://www.marketwatch.com/investing/stock/${symbol.toLowerCase()}`
            },
            {
                icon: "🔬", label: currentLang === "ko" ? "즉시 분석" : "Quick Analyze",
                desc: currentLang === "ko" ? "이 봇의 기술적 분석" : "Technical analysis by this bot",
                url: "#",
                onclick: `closeStockModal(); quickAnalyze('${symbol}');`
            },
        ];
    }

    // 링크 카드 렌더링
    linksEl.innerHTML = links.map(link => {
        const onclickAttr = link.onclick
            ? `onclick="${link.onclick}; return false;"`
            : `target="_blank" rel="noopener"`;
        return `
            <a href="${link.url}" ${onclickAttr} class="link-card">
                <span class="link-card-icon">${link.icon}</span>
                <div>
                    <div class="link-card-label">${link.label}</div>
                    <div class="link-card-desc">${link.desc}</div>
                </div>
            </a>
        `;
    }).join("");

    // 뉴스 로딩
    newsEl.innerHTML = `<div class="text-mute" style="font-size:13px; padding:8px;">
        ${currentLang === "ko" ? "뉴스 로딩 중..." : "Loading news..."}
    </div>`;

    // 모달 표시
    modal.style.display = "flex";

    // 뉴스 비동기 로드
    loadStockNews(symbol, displayName, isKR);
}

/**
 * closeStockModal() - 종목 모달 닫기
 */
function closeStockModal(event) {
    // event가 있으면 오버레이 클릭 시에만 닫기 (내용 클릭은 무시)
    if (event && event.target !== event.currentTarget) return;
    document.getElementById("stockLinkModal").style.display = "none";
}

/**
 * loadStockNews() - 종목 관련 뉴스를 백엔드에서 가져와 모달에 표시
 *
 * /api/stock/news 엔드포인트를 호출하여:
 * 1. 최신 뉴스 5개를 링크로 표시
 * 2. LLM이 분석한 감성 요약(긍정/부정/중립)을 표시
 */
async function loadStockNews(symbol, displayName, isKR) {
    const newsEl = document.getElementById("modalNewsContent");
    try {
        const res = await fetch(`/api/stock/news?symbol=${encodeURIComponent(symbol)}&name=${encodeURIComponent(displayName)}`);
        const data = await res.json();

        if (data.error) {
            newsEl.innerHTML = `<div class="text-mute" style="font-size:13px; padding:8px;">
                ${data.error}
            </div>`;
            return;
        }

        let html = "";

        // AI 감성 요약 (있으면)
        if (data.ai_summary) {
            html += `<div class="news-ai-summary">
                <strong>AI ${currentLang === "ko" ? "분석" : "Analysis"}:</strong> ${data.ai_summary}
            </div>`;
        }

        // 뉴스 목록
        if (data.news && data.news.length > 0) {
            html += data.news.map(n => `
                <div class="news-item">
                    <a href="${n.url}" target="_blank" rel="noopener">${n.title}</a>
                    <div class="news-source">${n.source || ""} · ${n.date || ""}</div>
                </div>
            `).join("");
        } else {
            html += `<div class="text-mute" style="font-size:13px; padding:8px;">
                ${currentLang === "ko" ? "관련 뉴스를 찾지 못했습니다" : "No related news found"}
            </div>`;
        }

        newsEl.innerHTML = html;
    } catch (e) {
        newsEl.innerHTML = `<div class="text-mute" style="font-size:13px; padding:8px;">
            ${currentLang === "ko" ? "뉴스 로드 실패" : "Failed to load news"}
        </div>`;
    }
}


// ═══════════════════════════════════════════════════════════════════════════
// 6.7 실시간 활동 피드 & 거래 이벤트
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 활동 종류별 아이콘 매핑
 * 각 action 타입에 대응하는 이모지를 반환하여 피드에서 시각적으로 구분
 */
const ACTIVITY_ICONS = {
    "buy":          "💰",   // 매수 체결
    "sell":         "📤",   // 매도 체결
    "analyzing":    "🔍",   // 분석 중
    "signal":       "📡",   // 신호 발견
    "start":        "▶️",   // 봇 시작
    "stop":         "⏹️",   // 봇 중지
    "risk_check":   "🛡️",  // 리스크 체크
};

/**
 * addActivityToFeed() - 활동 피드에 새 항목 추가
 *
 * WebSocket의 bot_activity 이벤트 수신 시 호출됩니다.
 * 피드 상단에 새 항목을 삽입하고, 최대 30개까지만 유지합니다.
 *
 * Parameters:
 *   activity: {
 *     action: "buy" | "sell" | "analyzing" | "signal" | "start" | "stop" | "risk_check"
 *     detail: "매수: AAPL 10주 @ 180.50"
 *     level: "info" | "success" | "warning" | "danger"
 *     timestamp: "2026-05-06T14:30:00"
 *   }
 */
function addActivityToFeed(activity) {
    const feed = document.getElementById("activityFeed");
    if (!feed) return;

    // 빈 상태(empty-state) 제거
    const empty = feed.querySelector(".empty-state");
    if (empty) empty.remove();

    // 활동 아이템 생성
    const item = document.createElement("div");
    item.className = `activity-item ${activity.action || "info"}`;

    const icon = ACTIVITY_ICONS[activity.action] || "📋";
    const timeStr = activity.timestamp
        ? new Date(activity.timestamp).toLocaleTimeString(currentLang === "ko" ? "ko-KR" : "en-US",
            { hour: "2-digit", minute: "2-digit", second: "2-digit" })
        : "";

    item.innerHTML = `
        <span class="activity-icon">${icon}</span>
        <span class="activity-detail">${activity.detail || ""}</span>
        <span class="activity-time">${timeStr}</span>
    `;

    // 피드 맨 위에 삽입 (최신 활동이 위에)
    feed.prepend(item);

    // 최대 30개 유지
    const items = feed.querySelectorAll(".activity-item");
    if (items.length > 30) {
        items[items.length - 1].remove();
    }

    // 활동 카운트 업데이트
    const countEl = document.getElementById("activityCount");
    if (countEl) {
        countEl.textContent = items.length + (currentLang === "ko" ? "개" : " items");
    }
}

/**
 * loadActivityLog() - 서버에서 기존 활동 로그 불러오기
 * 페이지 로드 또는 WebSocket 재연결 시 호출하여
 * 이전에 쌓인 활동 기록을 피드에 표시합니다.
 */
async function loadActivityLog() {
    try {
        const res = await fetch("/api/activity");
        const activities = await res.json();

        if (activities && activities.length > 0) {
            const feed = document.getElementById("activityFeed");
            if (!feed) return;

            // 기존 내용 초기화
            feed.innerHTML = "";

            // 최신 순으로 정렬하여 표시 (서버는 오래된 순 → reverse)
            const recent = activities.slice(-30).reverse();
            recent.forEach(a => addActivityToFeed(a));
        }
    } catch (e) {
        console.error("[활동 로그] 로드 실패:", e);
    }
}

/**
 * loadRecentTrades() - PaperExecutor의 메모리 내 최근 거래를 직접 로드
 *
 * DB 기반 /api/trades와 달리 /api/trades/recent를 호출하여
 * 모의매매기의 실시간 거래 이력을 가져옵니다.
 * trade_executed 이벤트 수신 시 거래 탭 자동 갱신에 사용됩니다.
 */
/**
 * switchTab() - 프로그래밍적으로 다른 탭으로 전환
 */
function switchTab(tabName) {
    document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
    document.querySelectorAll("section[id^='tab-']").forEach(s => s.style.display = "none");
    const tabBtn = document.querySelector(`[data-tab="${tabName}"]`);
    const tabSection = document.getElementById(`tab-${tabName}`);
    if (tabBtn) tabBtn.classList.add("active");
    if (tabSection) tabSection.style.display = "block";

    // 거래 탭 전환 시 자동 새로고침
    if (tabName === "trades") {
        loadTrades();
    }
}

/**
 * loadDashboardRecentTrades() - 메인 대시보드 "최근 거래" 위젯에 최근 5건 표시
 *
 * - 시간(상대), 종목명, 매수/매도 뱃지, 수량/가격, 총액, 손익
 * - 클릭 시 종목 모달 (showStockLinks)
 * - 매도 시 PnL 색상 표시
 */
async function loadDashboardRecentTrades() {
    const container = document.getElementById("dashboardRecentTrades");
    const countEl = document.getElementById("recentTradeCount");
    if (!container) return;

    try {
        const res = await fetch("/api/trades?limit=10");
        if (!res.ok) {
            container.innerHTML = '<div class="empty-state"><div>거래 조회 실패</div></div>';
            return;
        }
        const trades = await res.json();

        if (!Array.isArray(trades) || trades.length === 0) {
            container.innerHTML = '<div class="empty-state"><div class="empty-state-icon">💼</div><div>아직 거래가 없습니다</div></div>';
            if (countEl) countEl.textContent = "0건";
            return;
        }

        if (countEl) countEl.textContent = `${trades.length}건`;

        // 가장 최신 5건만 카드 형태로 표시
        const recent = trades.slice(0, 5);

        container.innerHTML = recent.map(t => {
            const isBuy = (t.side || "").toUpperCase() === "BUY";
            const sideText = currentLang === "ko" ? (isBuy ? "매수" : "매도") : (isBuy ? "BUY" : "SELL");
            const sideColor = isBuy ? "#2ecc71" : "#ff5555";
            const sideBg = isBuy ? "rgba(46,204,113,0.12)" : "rgba(255,85,85,0.12)";

            const displayName = t.name || t.symbol;
            const isUsd = (t.symbol || "").match(/^[A-Z]+$/) !== null;  // .KS/.KQ 없으면 미국
            const pricePrefix = isUsd ? "$" : "₩";
            const priceStr = Number(t.price || 0).toLocaleString(undefined, {
                minimumFractionDigits: isUsd ? 2 : 0,
                maximumFractionDigits: isUsd ? 2 : 0,
            });
            const totalKrw = Number(t.total_value || (t.price * t.quantity) || 0);
            const totalStr = "₩" + Math.round(totalKrw).toLocaleString();

            // 손익 표시 (매도시 실제 금액, 매수는 '—')
            const pnl = Number(t.pnl || 0);
            let pnlHtml = "";
            if (isBuy) {
                // 매수는 손익이 매도 시점에 확정되므로 표시 안 함
                pnlHtml = `<span style="color:rgba(255,255,255,0.3);font-size:11px;min-width:40px;text-align:right;">—</span>`;
            } else if (Math.abs(pnl) > 0.01) {
                const pnlColor = pnl >= 0 ? "#2ecc71" : "#ff5555";
                const sign = pnl >= 0 ? "+" : "";
                pnlHtml = `<span style="color:${pnlColor};font-size:11px;font-weight:700;min-width:40px;text-align:right;" title="실현 손익">${sign}₩${Math.round(pnl).toLocaleString()}</span>`;
            } else {
                pnlHtml = `<span style="color:rgba(255,255,255,0.4);font-size:11px;min-width:40px;text-align:right;">₩0</span>`;
            }

            // 시간 표시 (상대시간 또는 HH:MM)
            const timeStr = formatTime(t.timestamp);

            return `<div class="signal-item clickable" style="cursor:pointer;" onclick="showStockLinks('${t.symbol}', '${displayName.replace(/'/g, "\\'")}')">
                <span style="background:${sideBg};color:${sideColor};padding:2px 8px;border-radius:6px;font-size:11px;font-weight:600;flex-shrink:0;">${sideText}</span>
                <span style="font-weight:600;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${displayName}</span>
                <span class="text-mute" style="font-size:11px;">${t.quantity}주 @ ${pricePrefix}${priceStr}</span>
                <span style="font-weight:600;font-size:12px;">${totalStr}</span>
                ${pnlHtml}
                <span class="text-mute" style="font-size:11px;margin-left:auto;flex-shrink:0;">${timeStr}</span>
            </div>`;
        }).join("");
    } catch (e) {
        console.warn("[최근거래] 로드 실패:", e);
        container.innerHTML = '<div class="empty-state"><div>거래 조회 오류</div></div>';
    }
}

async function loadRecentTrades() {
    try {
        const res = await fetch("/api/trades/recent");
        const trades = await res.json();
        const tbody = document.getElementById("tradesTable");

        if (!trades || trades.length === 0) {
            // DB 기반 거래도 시도
            loadTrades();
            return;
        }

        // 최신순으로 역정렬
        const sorted = [...trades].reverse();

        tbody.innerHTML = sorted.map(t => {
            const sideClass = t.side === "BUY" ? "badge-buy" : "badge-sell";
            const sideText = currentLang === "ko" ? (t.side === "BUY" ? "매수" : "매도") : t.side;
            const total = (t.total || t.price * t.quantity);
            const displayName = t.name || t.symbol;
            // 포지션 유형 배지
            const posType = t.position_type || "";
            const posTypeEn = posType === "단타" ? "short" : posType === "장기" ? "long" : "swing";
            const typeBadge = posType ? ` <span class="position-type-badge position-type-${posTypeEn}">${posType}</span>` : "";
            return `<tr>
                <td>${formatTime(t.timestamp)}</td>
                <td class="clickable" style="font-weight:600;" onclick="showStockLinks('${t.symbol}', '${displayName.replace(/'/g, "\\'")}')">${displayName}${typeBadge}</td>
                <td><span class="badge ${sideClass}">${sideText}</span></td>
                <td>${t.quantity}</td>
                <td>${Number(t.price).toFixed(2)}</td>
                <td>${formatCurrency(total)}</td>
                <td class="text-mute">${t.strategy || "-"}</td>
            </tr>`;
        }).join("");
    } catch (e) {
        // 실패 시 기존 DB 기반 로드로 폴백
        loadTrades();
    }
}


// ═══════════════════════════════════════════════════════════════════════════
// 7. 차트
// ═══════════════════════════════════════════════════════════════════════════

let equityChart = null;

function initChart() {
    const ctx = document.getElementById("equityChart").getContext("2d");
    equityChart = new Chart(ctx, {
        type: "line",
        data: {
            labels: [],
            datasets: [{
                label: "Equity",
                data: [],
                borderColor: "#0070d1",
                backgroundColor: "rgba(0, 112, 209, 0.1)",
                borderWidth: 2,
                fill: true,
                tension: 0.3,
                pointRadius: 0,
                pointHoverRadius: 4,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: "#181818",
                    titleColor: "#fff",
                    bodyColor: "rgba(255,255,255,0.7)",
                    borderColor: "rgba(229,229,229,0.2)",
                    borderWidth: 1,
                    cornerRadius: 8,
                }
            },
            scales: {
                x: { grid: { color: "rgba(229,229,229,0.1)" }, ticks: { color: "rgba(229,229,229,0.55)", maxTicksLimit: 8 } },
                y: { grid: { color: "rgba(229,229,229,0.1)" }, ticks: { color: "rgba(229,229,229,0.55)" } }
            },
            interaction: { intersect: false, mode: "index" }
        }
    });
    loadEquityData();
}

async function loadEquityData() {
    if (!equityChart) return;
    try {
        const res = await fetch("/api/equity");
        if (!res.ok) {
            console.warn("[Equity] API 실패:", res.status);
            return;
        }
        const data = await res.json();
        if (data && Array.isArray(data) && data.length > 0) {
            // 시간 라벨 - HH:MM 또는 날짜 표시
            equityChart.data.labels = data.map(d => {
                const ts = d.timestamp || d.date || "";
                // ISO 형식이면 HH:MM 추출, 아니면 그대로
                if (ts.includes("T")) {
                    return ts.slice(11, 16);  // HH:MM
                }
                return ts.slice(0, 10);  // YYYY-MM-DD
            });
            // equity_history는 total_equity, portfolio_snapshots는 total_value 키 사용
            equityChart.data.datasets[0].data = data.map(d =>
                Number(d.total_equity || d.total_value || 0)
            );
            equityChart.update("none");  // 애니메이션 없이 즉시 업데이트
        }
    } catch (e) {
        console.warn("[Equity] 로드 실패:", e);
    }
}


// ═══════════════════════════════════════════════════════════════════════════
// 8. 토스트 & 유틸리티
// ═══════════════════════════════════════════════════════════════════════════

function showToast(message, type = "info") {
    const container = document.getElementById("toastContainer");
    const toast = document.createElement("div");
    toast.className = `toast ${type}`;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => {
        toast.style.opacity = "0";
        toast.style.transform = "translateX(100%)";
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

function formatCurrency(value, symbol = "₩") {
    if (!value) return symbol + "0";
    return symbol + Math.round(value).toLocaleString();
}

function formatTime(isoString) {
    if (!isoString) return "-";
    const d = new Date(isoString);
    return d.toLocaleDateString(currentLang === "ko" ? "ko-KR" : "en-US", {
        month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"
    });
}


// ═══════════════════════════════════════════════════════════════════════════
// 9. 초기화
// ═══════════════════════════════════════════════════════════════════════════

document.addEventListener("DOMContentLoaded", () => {
    // 언어 초기 적용
    setLang(currentLang);

    // 차트 초기화
    initChart();

    // 초기 설정 로드
    fetch("/api/settings").then(res => res.json()).then(data => updateSettingsUI(data)).catch(() => {});

    // 관심 분야 섹터 칩 렌더링
    loadSectors();

    // Overview 진입 시 스캐너 자동 호출 (캐시된 결과가 있으면 즉시 표시)
    runScanner();

    // 활동 로그 초기 로드 (이전 세션의 활동 기록)
    loadActivityLog();

    // 메인 대시보드 "최근 거래" 위젯 초기 로드 + 60초마다 자동 갱신
    loadDashboardRecentTrades();
    setInterval(loadDashboardRecentTrades, 60000);

    // ── 분석 탭: 검색 자동완성 바인딩 ──
    // null 체크: 요소가 없으면 addEventListener 호출 시 에러 → 전체 초기화 실패 방지
    const analyzeInput = document.getElementById("analyzeSymbol");

    if (analyzeInput) {
        // 타이핑 시 디바운스된 검색 API 호출
        analyzeInput.addEventListener("input", (e) => {
            handleSearchInput(e.target.value.trim());
        });

        // Enter 키: 분석 실행 + 드롭다운 닫기
        analyzeInput.addEventListener("keypress", (e) => {
            if (e.key === "Enter") {
                const dropdown = document.getElementById("searchDropdown");
                if (dropdown) dropdown.style.display = "none";
                runAnalysis();
            }
        });

        // 검색 드롭다운 외부 클릭 시 닫기
        document.addEventListener("click", (e) => {
            const dropdown = document.getElementById("searchDropdown");
            const searchWrap = document.querySelector(".search-input-wrap") || analyzeInput.parentElement;
            if (dropdown && !searchWrap.contains(e.target)) {
                dropdown.style.display = "none";
            }
        });
    }

    // Watchlist Enter 키 바인딩 (null 체크 포함)
    const addUs = document.getElementById("addUsSymbol");
    const addKr = document.getElementById("addKrSymbol");
    if (addUs) addUs.addEventListener("keypress", (e) => { if (e.key === "Enter") addSymbol("us"); });
    if (addKr) addKr.addEventListener("keypress", (e) => { if (e.key === "Enter") addSymbol("kr"); });
});


// ═══════════════════════════════════════════════════════════════════════════
// Section 6.8: 일일 보고서 관리
// ═══════════════════════════════════════════════════════════════════════════
// 보고서 목록 조회, 수동 생성, 보고서 열기 기능

/**
 * 저장된 보고서 목록을 로드하여 UI에 표시
 *
 * /api/reports 엔드포인트에서 reports/ 폴더의 HTML 파일 목록을 가져옵니다.
 * 각 보고서는 클릭 시 새 탭에서 열립니다.
 */
async function loadReportsList() {
    const container = document.getElementById("reportsList");
    if (!container) return;

    try {
        const res = await fetch("/api/reports");
        const data = await res.json();

        if (!data.reports || data.reports.length === 0) {
            container.innerHTML = `
                <div class="text-mute" style="text-align:center;padding:24px;">
                    아직 생성된 보고서가 없습니다.<br>
                    <span style="font-size:12px;">봇 중지 시 자동 생성되거나, '보고서 생성' 버튼을 눌러 수동으로 생성할 수 있습니다.</span>
                </div>`;
            return;
        }

        container.innerHTML = data.reports.map(r => {
            // 파일 크기를 KB로 변환
            const sizeKB = (r.size / 1024).toFixed(1);
            return `
                <a href="/reports/${r.filename}" target="_blank"
                   style="display:flex;align-items:center;gap:12px;padding:12px 16px;
                          background:rgba(255,255,255,0.05);border-radius:8px;
                          text-decoration:none;color:inherit;transition:background 0.2s;"
                   onmouseover="this.style.background='rgba(0,112,209,0.15)'"
                   onmouseout="this.style.background='rgba(255,255,255,0.05)'">
                    <div style="font-size:24px;">📊</div>
                    <div style="flex:1;">
                        <div style="font-weight:600;font-size:14px;">${r.date}</div>
                        <div style="font-size:12px;color:rgba(255,255,255,0.5);">
                            ${sizeKB}KB · ${new Date(r.created).toLocaleString("ko-KR")}
                        </div>
                    </div>
                    <div style="color:var(--ps-blue);font-size:12px;">열기 →</div>
                </a>`;
        }).join("");

    } catch (e) {
        container.innerHTML = `<div class="text-mute" style="text-align:center;padding:24px;">보고서 목록 로드 실패</div>`;
    }
}

/**
 * 수동으로 일일 보고서를 생성
 *
 * /api/report/generate 엔드포인트를 호출하여 현재 봇 상태의 스냅샷으로
 * 보고서를 생성합니다. 생성 후 자동으로 새 탭에서 열립니다.
 */
async function generateReport() {
    try {
        showToast("보고서 생성 중...", "info");

        const res = await fetch("/api/report/generate", { method: "POST" });
        const data = await res.json();

        if (data.success) {
            showToast("일일 보고서가 생성되었습니다!", "success");
            // 보고서 목록 새로고침
            loadReportsList();
        } else {
            showToast(data.error || "보고서 생성 실패", "danger");
        }
    } catch (e) {
        showToast("보고서 생성 중 오류 발생", "danger");
    }
}


// ═══════════════════════════════════════════════════════════════════════════
// 7. Plotly 인터랙티브 차트 (Charts 탭)
// ═══════════════════════════════════════════════════════════════════════════

/**
 * Plotly 차트의 공통 레이아웃 설정
 * - 다크 테마 배경 (PlayStation Design 기반)
 * - 그리드 라인 반투명 처리
 * - 여백 최소화
 *
 * @returns {Object} Plotly layout 기본 객체
 */
function plotlyBaseLayout() {
    return {
        paper_bgcolor: "rgba(0,0,0,0)",     // 전체 배경 투명
        plot_bgcolor: "rgba(24,24,24,0.8)", // 차트 영역 반투명 다크
        font: { color: "rgba(255,255,255,0.8)", family: "Inter, sans-serif" },
        margin: { l: 50, r: 20, t: 30, b: 40 },
        xaxis: {
            gridcolor: "rgba(255,255,255,0.06)",
            linecolor: "rgba(255,255,255,0.1)"
        },
        yaxis: {
            gridcolor: "rgba(255,255,255,0.06)",
            linecolor: "rgba(255,255,255,0.1)"
        },
        hovermode: "x unified"
    };
}

/**
 * Charts 탭 초기 로드
 * 탭 전환 시 호출되어 모든 차트를 로드합니다.
 */
function loadChartsTab() {
    loadPerformanceStats();
    loadEquityChart(30);    // 기본 1개월
    loadPositionHeatmap();
}

/**
 * 성과 통계 카드 업데이트
 * /api/performance 에서 가져온 데이터를 7개 카드에 표시합니다.
 *
 * 지표 설명:
 * - 승률: 수익 거래 / 전체 청산 거래 (높을수록 좋음)
 * - 손익비: 총 수익 / 총 손실 (1 이상이면 기대값 양)
 * - 샤프비: 위험 대비 수익 (1 이상이면 괜찮음, 2 이상 우수)
 * - MDD: 최고점 대비 최대 하락폭 (낮을수록 안전)
 * - 칼마비: 연환산수익률 / MDD (높을수록 효율적)
 */
async function loadPerformanceStats() {
    try {
        const res = await fetch("/api/performance");
        const d = await res.json();
        if (d.error) return;

        // 승률 → 색상 분기 (50% 이상이면 초록, 이하면 빨강)
        const wrEl = document.getElementById("perfWinRate");
        wrEl.textContent = d.win_rate.toFixed(1) + "%";
        wrEl.style.color = d.win_rate >= 50 ? "var(--success)" : "var(--danger)";

        // 손익비 (Profit Factor) → 1 이상이면 초록
        const pfEl = document.getElementById("perfProfitFactor");
        pfEl.textContent = d.profit_factor.toFixed(2);
        pfEl.style.color = d.profit_factor >= 1 ? "var(--success)" : "var(--danger)";

        // 샤프비 → 1 이상 초록, 0~1 노랑, 0 이하 빨강
        const shEl = document.getElementById("perfSharpe");
        shEl.textContent = d.sharpe_ratio.toFixed(2);
        shEl.style.color = d.sharpe_ratio >= 1 ? "var(--success)"
            : d.sharpe_ratio >= 0 ? "var(--commerce)" : "var(--danger)";

        // MDD → 항상 빨간색 (낙폭이므로)
        document.getElementById("perfMDD").textContent =
            "-" + d.max_drawdown_pct.toFixed(2) + "%";

        // 칼마비
        const cmEl = document.getElementById("perfCalmar");
        cmEl.textContent = d.calmar_ratio.toFixed(2);
        cmEl.style.color = d.calmar_ratio >= 1 ? "var(--success)" : "var(--commerce)";

        // 평균 수익 / 평균 손실
        document.getElementById("perfAvgWinLoss").textContent =
            "$" + Number(d.avg_win).toLocaleString() + " / $" + Number(d.avg_loss).toLocaleString();

        // 총 거래 (승/패)
        document.getElementById("perfTotalTrades").textContent =
            d.total_sells + " (" + d.win_count + "W / " + d.loss_count + "L)";

    } catch (e) {
        console.error("성과 통계 로드 실패:", e);
    }
}

/**
 * Equity Curve (자산 곡선) - Plotly 인터랙티브 차트
 *
 * equity_history 테이블에서 시간별 자산 데이터를 가져와
 * 줌/팬/호버가 가능한 인터랙티브 라인 차트를 그립니다.
 *
 * @param {number} days - 조회 기간 (기본 30일)
 *
 * 차트 구성:
 * - 메인 라인: 총 자산 (total_equity)
 * - 서브 라인: 현금 (cash)
 * - fill 영역: 자산과 현금 사이 = 주식 포지션 가치
 */
async function loadEquityChart(days) {
    days = days || 30;

    // 기간 버튼 활성화 표시
    const btns = document.querySelectorAll("#tab-charts .card-header .btn-sm");
    btns.forEach(b => b.classList.remove("btn-primary"));
    // days에 따라 활성화
    const dayMap = { 7: 0, 30: 1, 90: 2, 365: 3 };
    if (dayMap[days] !== undefined && btns[dayMap[days]]) {
        btns[dayMap[days]].classList.add("btn-primary");
    }

    try {
        const res = await fetch("/api/equity?days=" + days);
        const data = await res.json();

        if (!data || data.length === 0) {
            Plotly.purge("plotlyEquityChart");
            document.getElementById("plotlyEquityChart").innerHTML =
                '<div style="text-align:center;padding:80px 0;color:rgba(255,255,255,0.4);">아직 자산 데이터가 없습니다</div>';

            // 드로우다운 차트와 분포 차트도 비움
            Plotly.purge("plotlyDrawdownChart");
            document.getElementById("plotlyDrawdownChart").innerHTML =
                '<div style="text-align:center;padding:60px 0;color:rgba(255,255,255,0.4);">데이터 없음</div>';
            Plotly.purge("plotlyReturnDist");
            document.getElementById("plotlyReturnDist").innerHTML =
                '<div style="text-align:center;padding:60px 0;color:rgba(255,255,255,0.4);">데이터 없음</div>';
            return;
        }

        // X축: 타임스탬프, Y축: 총 자산 / 현금
        const timestamps = data.map(d => d.timestamp || d.date);
        const equities = data.map(d => d.total_equity || d.total_value || 0);
        const cashArr = data.map(d => d.cash || 0);

        // ── 자산 곡선 (메인) ──
        const traceEquity = {
            x: timestamps,
            y: equities,
            type: "scatter",
            mode: "lines",
            name: "총 자산",
            line: { color: "#0070d1", width: 2.5 },
            fill: "tozeroy",
            fillcolor: "rgba(0,112,209,0.08)"
        };

        // ── 현금 라인 ──
        const traceCash = {
            x: timestamps,
            y: cashArr,
            type: "scatter",
            mode: "lines",
            name: "현금",
            line: { color: "rgba(255,255,255,0.3)", width: 1, dash: "dot" }
        };

        const layout = Object.assign(plotlyBaseLayout(), {
            yaxis: Object.assign(plotlyBaseLayout().yaxis, {
                title: "자산 ($)",
                tickprefix: "$",
                tickformat: ",.0f"
            }),
            legend: { x: 0, y: 1.12, orientation: "h" },
            showlegend: true
        });

        Plotly.newPlot("plotlyEquityChart", [traceEquity, traceCash], layout,
            { responsive: true, displayModeBar: false });

        // ── 드로우다운 차트도 같이 그리기 ──
        drawDrawdownChart(timestamps, equities);

        // ── 일별 수익률 분포 히스토그램 ──
        drawReturnDistribution(equities);

    } catch (e) {
        console.error("Equity 차트 로드 실패:", e);
    }
}

/**
 * 드로우다운(Drawdown) 차트
 *
 * 최고점 대비 하락폭을 시각화합니다.
 * 넓고 깊은 빨간 영역 = 큰 낙폭 → 위험 구간
 * 0에 가까운 영역 = 최고점 근처 → 안전 구간
 *
 * @param {Array<string>} timestamps - X축 시간 배열
 * @param {Array<number>} equities  - Y축 자산 배열
 *
 * 드로우다운 계산:
 *   peak = max(equity_so_far)
 *   drawdown[i] = (equity[i] - peak) / peak × 100
 */
function drawDrawdownChart(timestamps, equities) {
    // 드로우다운 계산: (현재 - 최고점) / 최고점 * 100
    let peak = equities[0] || 1;
    const drawdowns = equities.map(eq => {
        if (eq > peak) peak = eq;
        return peak > 0 ? ((eq - peak) / peak) * 100 : 0;
    });

    const trace = {
        x: timestamps,
        y: drawdowns,
        type: "scatter",
        mode: "lines",
        name: "Drawdown",
        line: { color: "#d53b00", width: 1.5 },
        fill: "tozeroy",
        fillcolor: "rgba(213,59,0,0.15)"
    };

    const layout = Object.assign(plotlyBaseLayout(), {
        yaxis: Object.assign(plotlyBaseLayout().yaxis, {
            title: "Drawdown (%)",
            ticksuffix: "%",
            range: [Math.min(...drawdowns) * 1.2, 1]
        }),
        showlegend: false
    });

    Plotly.newPlot("plotlyDrawdownChart", [trace], layout,
        { responsive: true, displayModeBar: false });
}

/**
 * 일별 수익률 분포 히스토그램
 *
 * 수익률이 정규분포에 가까울수록 안정적인 전략입니다.
 * 왼쪽 꼬리(fat tail)가 길면 큰 손실 위험이 있음을 의미합니다.
 *
 * @param {Array<number>} equities - 자산 배열 (수익률 계산용)
 */
function drawReturnDistribution(equities) {
    // 일별 수익률 계산
    const returns = [];
    for (let i = 1; i < equities.length; i++) {
        if (equities[i - 1] > 0) {
            returns.push(((equities[i] / equities[i - 1]) - 1) * 100);
        }
    }

    if (returns.length === 0) {
        Plotly.purge("plotlyReturnDist");
        document.getElementById("plotlyReturnDist").innerHTML =
            '<div style="text-align:center;padding:60px 0;color:rgba(255,255,255,0.4);">수익률 데이터 부족</div>';
        return;
    }

    const trace = {
        x: returns,
        type: "histogram",
        name: "수익률 분포",
        marker: {
            color: returns.map(r => r >= 0
                ? "rgba(0,153,0,0.7)"   // 수익 → 초록
                : "rgba(213,59,0,0.7)") // 손실 → 빨강
        },
        nbinsx: 30,
        opacity: 0.85
    };

    const layout = Object.assign(plotlyBaseLayout(), {
        xaxis: Object.assign(plotlyBaseLayout().xaxis, {
            title: "수익률 (%)",
            ticksuffix: "%"
        }),
        yaxis: Object.assign(plotlyBaseLayout().yaxis, { title: "빈도" }),
        showlegend: false,
        bargap: 0.05
    });

    Plotly.newPlot("plotlyReturnDist", [trace], layout,
        { responsive: true, displayModeBar: false });
}

/**
 * 포지션 히트맵 (Treemap)
 *
 * 현재 보유 종목을 트리맵 형태로 시각화합니다.
 * - 면적 = 포지션 금액 (많이 투자한 종목이 크게 표시)
 * - 색상 = 수익률 (초록 = 수익, 빨강 = 손실)
 *
 * Plotly Treemap 사용:
 *   labels: 종목명
 *   parents: 모두 "" (단일 레벨)
 *   values: 포지션 금액 (주가 × 수량)
 *   marker.colors: PnL% 기반 색상
 */
async function loadPositionHeatmap() {
    try {
        const res = await fetch("/api/status");
        const status = await res.json();
        const positions = status.positions || {};
        const symbols = Object.keys(positions);

        if (symbols.length === 0) {
            Plotly.purge("positionHeatmap");
            document.getElementById("positionHeatmap").style.display = "none";
            document.getElementById("heatmapEmpty").style.display = "block";
            return;
        }

        document.getElementById("positionHeatmap").style.display = "block";
        document.getElementById("heatmapEmpty").style.display = "none";

        // 트리맵 데이터 구성
        const labels = [];
        const parents = [];
        const values = [];
        const colors = [];
        const texts = [];

        symbols.forEach(sym => {
            const p = positions[sym];
            const posValue = (p.current_price || p.avg_price) * p.shares;
            const pnlPct = p.pnl_pct || 0;
            const displayName = p.name || sym;

            labels.push(displayName + "<br>" + sym);
            parents.push("");
            values.push(Math.abs(posValue) || 1);

            // 색상: PnL% 에 따라 초록~빨강 그라데이션
            // -10% 이하: 진한 빨강, +10% 이상: 진한 초록
            const clampedPnl = Math.max(-10, Math.min(10, pnlPct));
            colors.push(clampedPnl);

            texts.push(
                "$" + posValue.toLocaleString(undefined, { maximumFractionDigits: 0 }) +
                "<br>PnL: " + pnlPct.toFixed(2) + "%" +
                "<br>" + p.shares + "주 × $" + (p.current_price || 0).toFixed(2)
            );
        });

        const trace = {
            type: "treemap",
            labels: labels,
            parents: parents,
            values: values,
            text: texts,
            textinfo: "label+text",
            hoverinfo: "text",
            marker: {
                colors: colors,
                colorscale: [
                    [0, "#d53b00"],      // 빨강 (손실)
                    [0.5, "#333333"],     // 중립 (0%)
                    [1, "#009900"]        // 초록 (수익)
                ],
                cmid: 0,
                line: { width: 2, color: "rgba(0,0,0,0.3)" }
            },
            textfont: { color: "white", size: 13, family: "Inter" }
        };

        const layout = Object.assign(plotlyBaseLayout(), {
            margin: { l: 0, r: 0, t: 0, b: 0 }
        });

        Plotly.newPlot("positionHeatmap", [trace], layout,
            { responsive: true, displayModeBar: false });

    } catch (e) {
        console.error("포지션 히트맵 로드 실패:", e);
    }
}


// ═══════════════════════════════════════════════════════════════════════════
// 8. 시장 AI 브리핑 + DART 공시
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 시장 AI 브리핑 로드
 *
 * /api/briefing 에서 DART + 뉴스 + 매크로 + 포트폴리오를
 * 종합한 브리핑을 가져옵니다.
 *
 * 브리핑 포함 항목:
 * 1. DART 공시 요약 (호재/악재 분류)
 * 2. 주요 뉴스 헤드라인 + 감성 분석
 * 3. 거시경제 지표 (Fear & Greed, VIX)
 * 4. 포트폴리오 상태
 * 5. 종합 시장 전망 (BULLISH/BEARISH/NEUTRAL)
 */
async function loadMarketBriefing() {
    const container = document.getElementById("briefingContent");
    container.innerHTML = '<div style="text-align:center;padding:40px;color:rgba(255,255,255,0.5);">' +
        '<div style="font-size:24px;margin-bottom:8px;">⏳</div>데이터 수집 중... (30초~1분 소요)</div>';

    try {
        const res = await fetch("/api/briefing");
        const data = await res.json();

        if (data.error) {
            container.innerHTML = '<div style="text-align:center;padding:40px;color:var(--danger);">' +
                '브리핑 생성 실패: ' + data.error + '</div>';
            return;
        }

        // 감성 배지 색상
        const sentColors = { BULLISH: "#009900", BEARISH: "#d53b00", NEUTRAL: "#888" };
        const sentColor = sentColors[data.sentiment] || "#888";
        const sentEmoji = { BULLISH: "🟢", BEARISH: "🔴", NEUTRAL: "🟡" };

        let html = '<div style="margin-bottom:16px;text-align:center;">';
        html += '<span style="display:inline-block;padding:6px 20px;border-radius:9999px;' +
            'background:' + sentColor + ';color:#fff;font-weight:600;font-size:14px;">';
        html += (sentEmoji[data.sentiment] || "🟡") + " " + data.sentiment +
            " (감성지수: " + (data.sentiment_score >= 0 ? "+" : "") +
            data.sentiment_score.toFixed(2) + ")";
        html += '</span></div>';

        // 텍스트 요약 표시
        if (data.summary) {
            html += '<div style="background:rgba(255,255,255,0.04);border-radius:8px;' +
                'padding:16px;margin-bottom:12px;font-size:13px;line-height:1.8;' +
                'color:rgba(255,255,255,0.75);white-space:pre-line;">';
            html += data.summary.replace(/</g, "&lt;").replace(/>/g, "&gt;");
            html += '</div>';
        }

        // 생성 시간
        html += '<div style="text-align:right;font-size:11px;color:rgba(255,255,255,0.3);">';
        html += '생성: ' + new Date(data.generated_at).toLocaleString("ko-KR");
        html += '</div>';

        container.innerHTML = html;

    } catch (e) {
        container.innerHTML = '<div style="text-align:center;padding:40px;color:var(--danger);">' +
            '브리핑 로드 실패: ' + e.message + '</div>';
        console.error("브리핑 로드 실패:", e);
    }
}

/**
 * DART 전자공시 목록 로드
 *
 * /api/dart 에서 최근 공시를 가져와 호재/악재를 표시합니다.
 * 각 공시에는 기업명, 공시 제목, 접수일, 영향 판단이 표시됩니다.
 *
 * 호재/악재 판단 기준:
 * - 키워드 기반 (예: "유상증자" → 악재, "자기주식취득" → 호재)
 * - impact_score > 0.2: 호재 (🟢)
 * - impact_score < -0.2: 악재 (🔴)
 * - 그 외: 중립 (⚪)
 *
 * @param {number} days - 조회 기간 (기본 7일)
 */
async function loadDartDisclosures(days) {
    days = days || 7;
    const container = document.getElementById("dartContent");
    container.innerHTML = '<div style="text-align:center;padding:40px;color:rgba(255,255,255,0.5);">' +
        '⏳ DART 공시 로딩 중...</div>';

    try {
        const res = await fetch("/api/dart?days=" + days);
        const data = await res.json();

        if (data.error) {
            container.innerHTML = '<div style="text-align:center;padding:40px;color:var(--commerce);">' +
                '⚠️ ' + data.error + '<br><span style="font-size:12px;color:rgba(255,255,255,0.4);">' +
                'Settings에서 DART API 키를 설정해주세요</span></div>';
            return;
        }

        if (!data.disclosures || data.disclosures.length === 0) {
            container.innerHTML = '<div style="text-align:center;padding:40px;color:rgba(255,255,255,0.4);">' +
                '해당 기간에 관련 공시가 없습니다</div>';
            return;
        }

        // 요약 헤더
        let html = '<div style="margin-bottom:12px;font-size:13px;color:rgba(255,255,255,0.6);">' +
            '총 ' + data.total_count + '건 · ' +
            '<span style="color:var(--success);">호재 ' + data.positive_count + '</span> / ' +
            '<span style="color:var(--danger);">악재 ' + data.negative_count + '</span> / ' +
            '중립 ' + data.neutral_count +
            '</div>';

        // 공시 목록
        html += '<div style="display:flex;flex-direction:column;gap:6px;">';
        data.disclosures.forEach(function(d) {
            var icon = d.impact_label === "호재" ? "🟢" :
                       d.impact_label === "악재" ? "🔴" : "⚪";
            var labelColor = d.impact_label === "호재" ? "var(--success)" :
                             d.impact_label === "악재" ? "var(--danger)" : "rgba(255,255,255,0.4)";

            html += '<div style="display:flex;align-items:center;gap:10px;padding:10px 12px;' +
                'background:rgba(255,255,255,0.03);border-radius:6px;">';
            html += '<span style="font-size:16px;">' + icon + '</span>';
            html += '<div style="flex:1;min-width:0;">';
            html += '<div style="font-size:13px;font-weight:500;white-space:nowrap;' +
                'overflow:hidden;text-overflow:ellipsis;">';
            html += (d.corp_name || "?") + ' - ' + (d.report_nm || "?");
            html += '</div>';
            html += '<div style="font-size:11px;color:rgba(255,255,255,0.4);margin-top:2px;">';
            html += (d.rcept_dt || "") + ' · 점수: ' + (d.impact_score || 0).toFixed(1);
            html += '</div></div>';
            html += '<span style="font-size:11px;font-weight:600;color:' + labelColor + ';">' +
                (d.impact_label || "중립") + '</span>';
            html += '</div>';
        });
        html += '</div>';

        container.innerHTML = html;

    } catch (e) {
        container.innerHTML = '<div style="text-align:center;padding:40px;color:var(--danger);">' +
            'DART 데이터 로드 실패</div>';
        console.error("DART 로드 실패:", e);
    }
}


// ═══════════════════════════════════════════════════════════════════════════
// 9. 헬스체크 + 주간/월간 보고서
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 헬스체크 실행
 *
 * /api/healthcheck 에서 6가지 항목을 점검합니다:
 * 1. 봇 프로세스 상태 (실행 중 / 중지)
 * 2. DB 연결 (읽기/쓰기 테스트)
 * 3. 분석 주기 (마지막 분석으로부터 경과 시간)
 * 4. 디스크 사용량 (DB + 로그 파일 크기)
 * 5. 에러 빈도 (최근 1시간 에러 수)
 * 6. 데이터 신선도 (최근 equity 스냅샷 시간)
 *
 * 결과 상태:
 * - 🟢 healthy: 정상
 * - 🟡 warning: 주의 (동작 하지만 점검 필요)
 * - 🔴 critical: 위험 (즉시 조치 필요)
 */
async function loadHealthCheck() {
    var container = document.getElementById("healthCheckContent");
    container.innerHTML = '<div style="text-align:center;padding:16px;color:rgba(255,255,255,0.5);">점검 중...</div>';

    try {
        var res = await fetch("/api/healthcheck");
        var data = await res.json();

        // 전체 상태 아이콘
        var statusIcon = { healthy: "🟢", warning: "🟡", critical: "🔴" };
        var statusText = { healthy: "정상", warning: "주의", critical: "위험" };

        var html = '<div style="text-align:center;margin-bottom:12px;">';
        html += '<span style="font-size:20px;">' + (statusIcon[data.status] || "🟡") + '</span> ';
        html += '<span style="font-weight:600;">' + (statusText[data.status] || "알 수 없음") + '</span>';
        html += ' <span style="font-size:12px;color:rgba(255,255,255,0.4);">(' +
            data.healthy_count + '✓ / ' + data.warning_count + '⚠ / ' +
            data.critical_count + '✗)</span>';
        html += '</div>';

        // 개별 항목
        if (data.checks) {
            data.checks.forEach(function(c) {
                var icon = statusIcon[c.status] || "⚪";
                html += '<div style="display:flex;align-items:center;gap:8px;padding:6px 0;' +
                    'border-bottom:1px solid rgba(255,255,255,0.04);font-size:13px;">';
                html += '<span>' + (c.icon || "") + '</span>';
                html += '<span style="flex:1;">' + c.name + '</span>';
                html += '<span style="color:rgba(255,255,255,0.5);font-size:11px;max-width:180px;' +
                    'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' +
                    c.message + '</span>';
                html += '</div>';
            });
        }

        container.innerHTML = html;
    } catch (e) {
        container.innerHTML = '<div style="text-align:center;padding:16px;color:var(--danger);">헬스체크 실패</div>';
    }
}

/**
 * 주간/월간 보고서 생성
 *
 * @param {string} period - "weekly" 또는 "monthly"
 *
 * 생성 과정:
 * 1. /api/report/weekly POST 호출
 * 2. 서버에서 DB 데이터 기반 HTML 보고서 생성
 * 3. reports/ 폴더에 저장
 * 4. 생성 완료 시 새 탭에서 열기
 */
async function generatePeriodReport(period) {
    var label = period === "weekly" ? "주간" : "월간";
    var statusEl = document.getElementById("reportGenStatus");
    statusEl.textContent = label + " 보고서 생성 중...";
    showToast(label + " 보고서 생성 중...", "info");

    try {
        var res = await fetch("/api/report/weekly", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ period: period })
        });
        var data = await res.json();

        if (data.success) {
            showToast(label + " 보고서가 생성되었습니다!", "success");
            statusEl.textContent = "✓ " + data.filename + " 생성 완료";
            // 새 탭에서 열기
            window.open("/reports/" + data.filename, "_blank");
            // 보고서 목록 새로고침
            if (typeof loadReportsList === "function") loadReportsList();
        } else {
            showToast(data.error || "보고서 생성 실패", "danger");
            statusEl.textContent = "✗ 생성 실패: " + (data.error || "");
        }
    } catch (e) {
        showToast("보고서 생성 중 오류", "danger");
        statusEl.textContent = "✗ 오류 발생";
    }
}


// ═══════════════════════════════════════════════════════════════════════════
// Section 6.9: 시장 동향 보고서 생성
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 시장 동향 보고서 생성
 *
 * /api/report/market 엔드포인트를 호출하여
 * 주요 지수 + 섹터 + 거시경제 + 뉴스를 종합 분석한
 * 독립 시장 보고서 HTML을 생성합니다.
 *
 * 데이터 수집에 시간이 걸리므로 (yfinance 여러 종목 조회)
 * 로딩 상태를 표시합니다.
 */
async function generateMarketReport() {
    var statusEl = document.getElementById("reportGenStatus");
    statusEl.textContent = "📊 시장 보고서 생성 중... (지수/섹터 데이터 수집에 30초~1분 소요)";
    showToast("시장 보고서 생성 중...", "info");

    try {
        var res = await fetch("/api/report/market", {
            method: "POST",
            headers: { "Content-Type": "application/json" }
        });
        var data = await res.json();

        if (data.success) {
            showToast("시장 동향 보고서가 생성되었습니다!", "success");
            statusEl.textContent = "✓ " + data.filename + " 생성 완료";
            // 새 탭에서 열기
            window.open("/reports/" + data.filename, "_blank");
            // 보고서 목록 새로고침
            if (typeof loadReportsList === "function") loadReportsList();
        } else {
            showToast(data.error || "시장 보고서 생성 실패", "danger");
            statusEl.textContent = "✗ 생성 실패: " + (data.error || "");
        }
    } catch (e) {
        showToast("시장 보고서 생성 중 오류", "danger");
        statusEl.textContent = "✗ 오류 발생";
    }
}


// ═══════════════════════════════════════════════════════════════════════════
// Section 6.10: 장 운영 상태 + 거래일 달력
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 한국/미국 시장 개장 상태를 로드하여 UI에 표시
 *
 * /api/market/status 엔드포인트에서 현재 장 상태를 가져옵니다.
 * - 개장 중: 초록 점 + "거래 중" + 폐장까지 남은 시간
 * - 폐장: 빨간 점 + "폐장" + 다음 개장 시간
 * - 주말: 회색 점 + "주말 휴장"
 *
 * 30초마다 자동 갱신됩니다.
 */
async function loadMarketStatus() {
    try {
        var res = await fetch("/api/market/status");
        var data = await res.json();

        // 한국 시장
        var krDot = document.getElementById("krStatusDot");
        var krText = document.getElementById("krStatusText");
        var krNext = document.getElementById("krStatusNext");
        if (krDot && data.kr) {
            krDot.style.background = data.kr.is_open ? "#2ecc71" : "#e74c3c";
            if (data.kr.status.includes("주말") || data.kr.status === "휴장") {
                krDot.style.background = "#666";
            }
            krText.textContent = data.kr.status + " · " + data.kr.hours;
            krNext.textContent = data.kr.next;
        }

        // 미국 시장
        var usDot = document.getElementById("usStatusDot");
        var usText = document.getElementById("usStatusText");
        var usNext = document.getElementById("usStatusNext");
        if (usDot && data.us) {
            usDot.style.background = data.us.is_open ? "#2ecc71" : "#e74c3c";
            if (data.us.status.includes("주말") || data.us.status === "휴장") {
                usDot.style.background = "#666";
            }
            usText.textContent = data.us.status + " · " + data.us.hours;
            usNext.textContent = data.us.next;
        }

        // 달력 데이터 저장 (토글 시 사용)
        if (data.calendar) {
            window._marketCalendar = data.calendar;
        }
    } catch (e) {
        console.debug("[marketStatus] 로드 실패:", e);
    }
}

/**
 * 거래일 달력 표시/숨기기 토글
 *
 * 이번 달의 거래일(평일)과 휴장일(주말)을 색상으로 구분합니다.
 * 오늘 날짜는 파란 테두리로 강조됩니다.
 */
function toggleMarketCalendar() {
    var cal = document.getElementById("marketCalendar");
    if (!cal) return;

    if (cal.style.display === "none") {
        cal.style.display = "block";
        renderMarketCalendar();
    } else {
        cal.style.display = "none";
    }
}

/**
 * 달력 그리드 렌더링
 *
 * 7열(월~일) 그리드에 이번 달 날짜를 배치합니다.
 * - 초록: 거래일 (평일)
 * - 회색: 휴장 (주말)
 * - 파란 테두리: 오늘
 * - 반투명: 지난 날짜
 */
function renderMarketCalendar() {
    var data = window._marketCalendar;
    if (!data) return;

    var title = document.getElementById("calendarTitle");
    var grid = document.getElementById("calendarGrid");
    if (!title || !grid) return;

    title.textContent = "📅 " + data.month_name + " 거래일 달력";

    // 요일 헤더
    var html = ["월","화","수","목","금","토","일"].map(function(d) {
        return '<div style="font-weight:600;color:rgba(255,255,255,0.4);padding:4px;">' + d + '</div>';
    }).join("");

    // 첫 날의 요일에 맞춰 빈 칸 삽입
    if (data.days.length > 0) {
        var firstWeekday = ["월","화","수","목","금","토","일"].indexOf(data.days[0].weekday);
        for (var i = 0; i < firstWeekday; i++) {
            html += '<div></div>';
        }
    }

    // 날짜 셀
    data.days.forEach(function(d) {
        var bg = d.is_trading ? "rgba(46,204,113,0.15)" : "rgba(255,255,255,0.03)";
        var color = d.is_trading ? "#2ecc71" : "#666";
        var border = d.is_today ? "2px solid #0070d1" : "2px solid transparent";
        var opacity = d.is_past && !d.is_today ? "0.4" : "1";

        html += '<div style="padding:6px 2px;border-radius:6px;background:' + bg +
            ';color:' + color + ';border:' + border + ';opacity:' + opacity + ';">' +
            d.day + '</div>';
    });

    grid.innerHTML = html;
}

// 페이지 로드 시 + 30초마다 장 상태 갱신
loadMarketStatus();
setInterval(loadMarketStatus, 30000);


// ═══════════════════════════════════════════════════════════════════════════
// 주요 시장 지수 (KOSPI, NASDAQ, S&P500, USD/KRW 등)
// ═══════════════════════════════════════════════════════════════════════════

async function loadMarketIndices() {
    try {
        const res = await fetch("/api/market/indices");
        if (!res.ok) return;
        const data = await res.json();

        // 데이터 소스 라벨 (사용자가 hover로 확인 가능)
        const SOURCE_LABELS = {
            "naver": "네이버 금융 (실시간, ~1-5초 지연)",
            "kis":   "한국투자증권 (실거래 API, 실시간)",
            "yfinance": "Yahoo Finance (15-20분 지연)",
        };

        // 카드 업데이트
        document.querySelectorAll(".market-index-card").forEach(card => {
            const sym = card.dataset.symbol;
            const idx = data[sym];
            const valEl = card.querySelector(".idx-value");
            const chgEl = card.querySelector(".idx-change");

            if (!idx || !idx.price) {
                valEl.textContent = "—";
                chgEl.textContent = "";
                card.title = "데이터를 가져오지 못했습니다";
                return;
            }

            // 가격 포맷: USD/KRW는 소수점 1자리, 지수는 소수점 2자리
            const isFx = sym === "USDKRW=X";
            const priceStr = isFx
                ? idx.price.toFixed(1)
                : idx.price.toLocaleString(undefined, {
                    minimumFractionDigits: 2,
                    maximumFractionDigits: 2,
                });
            valEl.textContent = priceStr;

            // 변동률 색상
            const isUp = idx.change >= 0;
            const sign = isUp ? "+" : "";
            const arrow = isUp ? "▲" : "▼";
            chgEl.textContent = `${arrow} ${sign}${idx.change_pct.toFixed(2)}%`;
            chgEl.style.color = isUp ? "#2ecc71" : "#ff5555";

            // 데이터 소스 + 갱신 시각 툴팁
            const srcLabel = SOURCE_LABELS[idx.source] || idx.source || "알 수 없음";
            let tooltip = `${idx.name}\n출처: ${srcLabel}`;
            if (idx.traded_at) {
                // ISO 형식 → 로컬 시간으로 표시
                try {
                    const t = new Date(idx.traded_at);
                    tooltip += `\n시각: ${t.toLocaleString("ko-KR", { hour12: false })}`;
                } catch (_) {}
            }
            card.title = tooltip;
        });
    } catch (e) {
        console.warn("[지수] 로드 실패:", e);
    }
}

// 페이지 로드 시 + 30초마다 갱신 (Naver Finance는 가볍고 실시간)
loadMarketIndices();
setInterval(loadMarketIndices, 30000);


// ═══════════════════════════════════════════════════════════════════════════
// KIS API 연결 상태
// ═══════════════════════════════════════════════════════════════════════════

async function loadKisStatus() {
    try {
        const res = await fetch("/api/kis/status");
        if (!res.ok) return;
        const data = await res.json();

        const dot = document.getElementById("kisStatusDot");
        const text = document.getElementById("kisStatusText");
        const badge = document.getElementById("kisStatusBadge");
        if (!dot || !text) return;

        const modeKr = data.mode === "paper" ? "모의" : "실거래";

        if (!data.configured) {
            dot.style.background = "#666";
            text.textContent = ".env 설정 필요";
            text.style.color = "rgba(255,255,255,0.4)";
        } else if (data.balance_ok) {
            // ✅ 완전 정상: 토큰 + 시세 + 잔고 모두 통과
            dot.style.background = "#2ecc71";
            text.textContent = `${modeKr} · 매수가능 ₩${Number(data.balance_krw).toLocaleString()}`;
            text.style.color = "rgba(46,204,113,0.9)";
        } else if (data.connected && data.test_price && !data.balance_ok) {
            // ⚠️ 시세는 되는데 잔고 안 됨 (EGW02007 등) — 가장 헷갈리는 상태
            dot.style.background = "#ff5555";
            const errMsg = data.balance_error
                ? `잔고 실패 [${data.balance_error.code}]`
                : "잔고 조회 실패";
            text.textContent = `${modeKr} · ${errMsg}`;
            text.style.color = "rgba(255,85,85,0.95)";
        } else if (data.connected) {
            // 토큰은 발급됐으나 시세도 안 됨
            dot.style.background = "#ffc107";
            text.textContent = "토큰 OK / 시세·잔고 모두 실패";
            text.style.color = "rgba(255,193,7,0.9)";
        } else {
            // 자격증명은 있으나 토큰 발급 실패
            dot.style.background = "#ff5555";
            text.textContent = data.message || "연결 실패";
            text.style.color = "rgba(255,85,85,0.9)";
        }

        // ── 툴팁: 전체 진단 + 경고 모음 ──
        if (badge) {
            const tooltipParts = [`모드: ${modeKr}`];
            if (data.account_masked) tooltipParts.push(`계좌: ${data.account_masked}`);
            if (data.test_price) tooltipParts.push(`삼성전자 시세: ₩${Number(data.test_price).toLocaleString()}`);
            if (data.balance_krw !== null && data.balance_krw !== undefined)
                tooltipParts.push(`매수가능: ₩${Number(data.balance_krw).toLocaleString()}`);
            if (data.balance_error)
                tooltipParts.push(`잔고 오류: [${data.balance_error.code}] ${data.balance_error.message}`);
            if (data.warnings && data.warnings.length > 0) {
                tooltipParts.push("");
                tooltipParts.push("경고:");
                data.warnings.forEach(w => tooltipParts.push(`  • ${w}`));
            }
            if (data.message) {
                tooltipParts.push("");
                tooltipParts.push(data.message);
            }
            badge.title = tooltipParts.join("\n");
        }
    } catch (e) {
        console.warn("[KIS] 상태 조회 실패:", e);
    }
}

// 페이지 로드 시 + 5분마다 갱신
loadKisStatus();
setInterval(loadKisStatus, 5 * 60 * 1000);


// ═══════════════════════════════════════════════════════════════════════════
// 시장 정지 (서킷브레이커/사이드카/VI) 경고 배너
// ═══════════════════════════════════════════════════════════════════════════

async function loadMarketHaltStatus() {
    try {
        const res = await fetch("/api/market/halt");
        if (!res.ok) return;
        const data = await res.json();

        const banner = document.getElementById("haltBanner");
        if (!banner) return;

        // 정상 상태면 배너 숨김
        if (!data.checked || (data.can_trade_new && data.can_trade_exit && (!data.warnings || data.warnings.length === 0))) {
            banner.style.display = "none";
            return;
        }

        // 심각도에 따라 색상 결정
        let bgColor, borderColor, iconText, titleText;
        if (!data.can_trade_new && !data.can_trade_exit) {
            // CB 1/2/3단계: 빨강
            bgColor = "rgba(255, 0, 0, 0.15)";
            borderColor = "rgba(255, 85, 85, 0.7)";
            iconText = "🚨";
            titleText = "🚨 시장 정지 — 모든 매매 차단";
        } else if (!data.can_trade_new) {
            // 사이드카 or 경고: 주황
            bgColor = "rgba(255, 165, 0, 0.12)";
            borderColor = "rgba(255, 165, 0, 0.6)";
            iconText = "⚠️";
            titleText = "⚠️ 신규 매수 차단 (매도 허용)";
        } else {
            // VI 발동 등: 노랑
            bgColor = "rgba(255, 215, 0, 0.10)";
            borderColor = "rgba(255, 215, 0, 0.5)";
            iconText = "⚡";
            titleText = "⚡ 변동성 경고";
        }

        banner.style.background = bgColor;
        banner.style.border = `2px solid ${borderColor}`;
        banner.style.display = "block";
        document.getElementById("haltIcon").textContent = iconText;
        document.getElementById("haltTitle").textContent = titleText;
        document.getElementById("haltDetail").textContent = data.message || "";

        // 시장별 상태 (KOSPI/S&P500)
        const markets = document.getElementById("haltMarkets");
        markets.innerHTML = "";
        if (data.kr_pct !== undefined) {
            const krColor = data.kr_pct < -3 ? "#ff5555" : data.kr_pct < 0 ? "#ffa500" : "#2ecc71";
            markets.innerHTML += `<span style="background:rgba(255,255,255,0.1);padding:4px 10px;border-radius:6px;color:${krColor};">🇰🇷 KOSPI ${data.kr_pct >= 0 ? '+' : ''}${data.kr_pct.toFixed(2)}%</span>`;
        }
        if (data.us_pct !== undefined) {
            const usColor = data.us_pct < -3 ? "#ff5555" : data.us_pct < 0 ? "#ffa500" : "#2ecc71";
            markets.innerHTML += `<span style="background:rgba(255,255,255,0.1);padding:4px 10px;border-radius:6px;color:${usColor};">🇺🇸 S&P ${data.us_pct >= 0 ? '+' : ''}${data.us_pct.toFixed(2)}%</span>`;
        }
        if (data.vi_symbols && data.vi_symbols.length > 0) {
            markets.innerHTML += `<span style="background:rgba(255,215,0,0.2);padding:4px 10px;border-radius:6px;color:#ffd700;">VI: ${data.vi_symbols.length}개</span>`;
        }
    } catch (e) {
        console.warn("[Halt] 상태 조회 실패:", e);
    }
}

// 페이지 로드 시 + 1분마다 갱신 (긴급 상황이라 자주 체크)
loadMarketHaltStatus();
setInterval(loadMarketHaltStatus, 60 * 1000);


// ═══════════════════════════════════════════════════════════════════════════
// 오늘의 실현 손익 (메인 대시보드 KPI + 거래 이력 페이지 양쪽 갱신)
// ═══════════════════════════════════════════════════════════════════════════

async function loadTodayPnl() {
    try {
        const res = await fetch("/api/pnl/today");
        if (!res.ok) return;
        const data = await res.json();

        // ── 메인 대시보드 KPI 카드 갱신 ──
        const kpiAmount = document.getElementById("dailyPnl");
        const kpiPct = document.getElementById("dailyPnlPct");
        if (kpiAmount) {
            const pnl = Number(data.total_pnl || 0);
            const sign = pnl >= 0 ? "+" : "";
            const color = pnl >= 0 ? "var(--success)" : "var(--danger)";
            kpiAmount.textContent = `${sign}₩${Math.round(pnl).toLocaleString()}`;
            kpiAmount.style.color = color;
            if (kpiPct) {
                const count = data.sell_count || 0;
                const winRate = data.win_rate || 0;
                kpiPct.textContent = count > 0
                    ? `매도 ${count}건 · 승률 ${winRate.toFixed(1)}%`
                    : "오늘 매도 없음";
                kpiPct.style.color = "rgba(255,255,255,0.5)";
            }
        }

        // ── 거래 이력 페이지 "오늘" 카드 갱신 ──
        const dateEl = document.getElementById("pnlTodayDate");
        if (dateEl) dateEl.textContent = data.date || "";

        const todayAmountEl = document.getElementById("pnlTodayAmount");
        if (todayAmountEl) {
            const pnl = Number(data.total_pnl || 0);
            const sign = pnl >= 0 ? "+" : "";
            const color = pnl > 0 ? "#2ecc71" : pnl < 0 ? "#ff5555" : "rgba(255,255,255,0.7)";
            todayAmountEl.textContent = `${sign}₩${Math.round(pnl).toLocaleString()}`;
            todayAmountEl.style.color = color;
        }

        const sellEl = document.getElementById("pnlTodaySellCount");
        if (sellEl) sellEl.textContent = `${data.sell_count || 0}건`;
        const winEl = document.getElementById("pnlTodayWinCount");
        if (winEl) winEl.textContent = data.win_count || 0;
        const lossEl = document.getElementById("pnlTodayLossCount");
        if (lossEl) lossEl.textContent = data.loss_count || 0;
        const totalCntEl = document.getElementById("pnlTodayTotalCount");
        if (totalCntEl) totalCntEl.textContent = `${(data.sell_count || 0) + (data.buy_count || 0)}건`;

        const wrEl = document.getElementById("pnlTodayWinRate");
        if (wrEl) {
            const wr = data.win_rate || 0;
            const decided = (data.win_count || 0) + (data.loss_count || 0);
            wrEl.textContent = decided > 0 ? `${wr.toFixed(1)}%` : "—";
            wrEl.style.color = decided === 0 ? "rgba(255,255,255,0.5)"
                : wr >= 50 ? "#2ecc71"
                : wr >= 30 ? "#ffd700" : "#ff5555";
        }
    } catch (e) {
        console.warn("[오늘 손익] 조회 실패:", e);
    }
}

// 페이지 로드 시 + 1분마다 갱신 + 새 거래 체결 시 즉시 갱신
loadTodayPnl();
setInterval(loadTodayPnl, 60 * 1000);


// ═══════════════════════════════════════════════════════════════════════════
// Section 6.11: 서버 재시작
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 서버 재시작
 *
 * confirm 확인 후 /api/server/restart POST 호출.
 * 서버가 재시작되면 연결이 끊어지므로, 3초 후 자동 새로고침합니다.
 */
async function restartServer() {
    var msg = currentLang === "ko"
        ? "서버를 재시작하시겠습니까?\n(봇이 실행 중이면 자동으로 중지됩니다)"
        : "Restart the server?\n(Bot will be stopped automatically if running)";
    if (!confirm(msg)) return;

    try {
        showToast(currentLang === "ko" ? "서버 재시작 중..." : "Restarting server...", "info");
        await fetch("/api/server/restart", { method: "POST" });

        // 서버가 재시작되면 연결이 끊어지므로 잠시 후 새로고침
        setTimeout(function() {
            showToast(currentLang === "ko" ? "페이지를 새로고침합니다..." : "Refreshing page...", "info");
            setTimeout(function() { location.reload(); }, 2000);
        }, 3000);
    } catch (e) {
        // 서버 재시작으로 연결 끊김 = 정상
        setTimeout(function() { location.reload(); }, 4000);
    }
}
