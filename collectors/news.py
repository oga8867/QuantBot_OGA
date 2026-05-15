"""
=============================================================================
collectors/news.py - 뉴스 수집기
=============================================================================

Google News RSS를 통해 종목 관련 뉴스 헤드라인을 수집합니다.

RSS(Really Simple Syndication)란?
- 웹사이트의 새 콘텐츠를 XML 형식으로 배포하는 표준
- API 키 없이 무료로 사용 가능
- Google News RSS URL 형식:
  https://news.google.com/rss/search?q={키워드}&hl=en-US

감성 분석에서의 활용:
- 긍정적 뉴스가 많으면 → 매수 심리 강화
- 부정적 뉴스가 많으면 → 매도 심리 강화
- 뉴스 감성은 앙상블의 10% 가중치로 반영 (과신 금지)
=============================================================================
"""

import pandas as pd
from typing import Optional, List, Dict
from datetime import datetime
from .base import BaseCollector

try:
    import feedparser
    FEEDPARSER_AVAILABLE = True
except ImportError:
    FEEDPARSER_AVAILABLE = False


# 간단한 감성 사전 (키워드 기반)
# 실전에서는 NLP 모델로 교체 가능하지만, 키워드 방식도 의외로 잘 동작함
POSITIVE_KEYWORDS = [
    "surge", "soar", "rally", "gain", "jump", "beat", "record",
    "upgrade", "bullish", "growth", "profit", "outperform",
    "breakout", "strong", "up", "high", "positive", "buy",
    "상승", "급등", "호재", "흑자", "매수", "성장", "신고가",
    "돌파", "상향", "기대", "호실적", "반등",
]

NEGATIVE_KEYWORDS = [
    "crash", "plunge", "drop", "fall", "decline", "miss", "cut",
    "downgrade", "bearish", "loss", "recession", "warning",
    "sell", "weak", "low", "negative", "risk", "fear",
    "하락", "급락", "악재", "적자", "매도", "위기", "신저가",
    "폭락", "하향", "우려", "실적부진", "리스크",
]


class NewsCollector(BaseCollector):
    """
    뉴스 수집 + 간단한 감성 분석기

    Google News RSS를 통해 헤드라인을 수집하고,
    키워드 기반으로 감성 점수를 계산합니다.
    """

    def __init__(self):
        super().__init__(name="news")

    def collect(self, symbol: str, **kwargs) -> Optional[pd.DataFrame]:
        """
        종목 관련 뉴스 수집

        Parameters:
            symbol: 종목 코드 또는 기업명 (예: "AAPL", "Apple")
            **kwargs:
                max_items: 최대 수집 건수 (기본 20)
                language: "en" 또는 "ko"

        Returns:
            DataFrame: title, published, link, sentiment_score
        """
        if not FEEDPARSER_AVAILABLE:
            self.logger.warning("feedparser 미설치: pip install feedparser")
            return None

        max_items = kwargs.get("max_items", 20)
        language = kwargs.get("language", "en")

        # Google News RSS URL 구성
        if language == "ko":
            url = (
                f"https://news.google.com/rss/search?"
                f"q={symbol}&hl=ko&gl=KR&ceid=KR:ko"
            )
        else:
            url = (
                f"https://news.google.com/rss/search?"
                f"q={symbol}+stock&hl=en-US&gl=US&ceid=US:en"
            )

        # RSS 파싱
        feed = feedparser.parse(url)

        if not feed.entries:
            self.logger.warning(f"'{symbol}' 관련 뉴스를 찾을 수 없습니다.")
            return None

        # 뉴스 항목 추출
        news_list = []
        for entry in feed.entries[:max_items]:
            title = entry.get("title", "")
            published = entry.get("published", "")
            link = entry.get("link", "")

            # 감성 점수 계산
            score = self._analyze_sentiment(title)

            news_list.append({
                "title": title,
                "published": published,
                "link": link,
                "sentiment_score": score,
            })

        df = pd.DataFrame(news_list)
        return df

    def _analyze_sentiment(self, text: str) -> float:
        """
        키워드 기반 간단한 감성 분석

        원리:
        - 텍스트에서 긍정/부정 키워드 개수를 세고
        - (긍정 - 부정) / (긍정 + 부정)으로 정규화
        - 결과: -1.0(매우 부정) ~ +1.0(매우 긍정), 0=중립

        한계:
        - 문맥을 이해하지 못함 ("not good"을 긍정으로 볼 수 있음)
        - 추후 Phase 2에서 NLP 모델(BERT 등)로 교체 가능

        Parameters:
            text: 분석할 텍스트

        Returns:
            감성 점수 (-1.0 ~ +1.0)
        """
        text_lower = text.lower()

        # 키워드 카운트
        pos_count = sum(1 for kw in POSITIVE_KEYWORDS if kw in text_lower)
        neg_count = sum(1 for kw in NEGATIVE_KEYWORDS if kw in text_lower)

        total = pos_count + neg_count
        if total == 0:
            return 0.0  # 키워드 없으면 중립

        # 정규화: -1 ~ +1
        return (pos_count - neg_count) / total

    def get_sentiment_summary(self, symbol: str, **kwargs) -> Dict:
        """
        종목의 뉴스 감성 요약

        Parameters:
            symbol: 종목 코드

        Returns:
            {
                "avg_sentiment": 평균 감성 (-1~+1),
                "positive_ratio": 긍정 뉴스 비율 (0~1),
                "news_count": 수집된 뉴스 수,
                "top_positive": 가장 긍정적인 헤드라인,
                "top_negative": 가장 부정적인 헤드라인,
            }
        """
        df = self.safe_collect(symbol, **kwargs)

        if df is None or df.empty:
            return {
                "avg_sentiment": 0.0,
                "positive_ratio": 0.5,
                "news_count": 0,
                "top_positive": None,
                "top_negative": None,
            }

        scores = df["sentiment_score"]

        # 가장 긍정/부정 뉴스
        top_pos_idx = scores.idxmax()
        top_neg_idx = scores.idxmin()

        return {
            "avg_sentiment": scores.mean(),
            "positive_ratio": (scores > 0).mean(),
            "news_count": len(df),
            "top_positive": df.loc[top_pos_idx, "title"] if scores.max() > 0 else None,
            "top_negative": df.loc[top_neg_idx, "title"] if scores.min() < 0 else None,
        }
