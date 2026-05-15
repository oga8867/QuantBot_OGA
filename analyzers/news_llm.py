"""
=============================================================================
analyzers/news_llm.py - 뉴스 수집 + LLM 감성 분석 모듈
=============================================================================

종목 관련 뉴스를 수집하고, LLM(OpenAI/Anthropic)으로 감성 분석을 수행합니다.

뉴스 소스:
- Google News RSS (무료, API 키 불필요)
- 필요 시 Alpha Vantage, NewsAPI 등 추가 가능

LLM 분석:
- 뉴스 제목들을 종합하여 투자 감성 요약 (긍정/부정/중립)
- OpenAI GPT 또는 Anthropic Claude API 사용 (키가 있을 때만)
- API 키가 없으면 규칙 기반 간단 분석으로 폴백

주의:
- 뉴스 기반 투자는 항상 기술적 분석과 함께 사용해야 합니다.
- LLM 분석은 참고용이며, 매매 신호로 직접 사용하지 않습니다.
=============================================================================
"""

import os
import re
import json
import logging
from typing import List, Dict, Optional
from datetime import datetime
from urllib.parse import quote_plus

logger = logging.getLogger(__name__)


class NewsCollector:
    """
    뉴스 수집기

    Google News RSS를 파싱하여 종목 관련 뉴스를 가져옵니다.
    XML 파싱에 feedparser를 사용하며, 없으면 regex 폴백합니다.
    """

    def __init__(self):
        # Google News RSS 기본 URL
        # 한국어: hl=ko&gl=KR&ceid=KR:ko
        # 영어: hl=en&gl=US&ceid=US:en
        self.base_url_ko = "https://news.google.com/rss/search?q={query}&hl=ko&gl=KR&ceid=KR:ko"
        self.base_url_en = "https://news.google.com/rss/search?q={query}&hl=en&gl=US&ceid=US:en"

    def search_news(self, query: str, lang: str = "ko", max_results: int = 5) -> List[Dict]:
        """
        Google News RSS에서 뉴스 검색

        Parameters:
            query: 검색어 (예: "삼성전자 주식", "AAPL stock")
            lang: "ko" 또는 "en"
            max_results: 최대 결과 수

        Returns:
            [{ "title": 제목, "url": 링크, "source": 출처, "date": 날짜 }, ...]
        """
        import urllib.request

        base_url = self.base_url_ko if lang == "ko" else self.base_url_en
        url = base_url.format(query=quote_plus(query))

        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/120.0.0.0 Safari/537.36"
            })
            with urllib.request.urlopen(req, timeout=10) as response:
                xml_data = response.read().decode("utf-8")

            return self._parse_rss(xml_data, max_results)

        except Exception as e:
            logger.warning(f"[뉴스] 검색 실패 ({query}): {e}")
            return []

    def _parse_rss(self, xml_data: str, max_results: int) -> List[Dict]:
        """
        RSS XML을 파싱하여 뉴스 리스트로 변환

        feedparser 라이브러리가 있으면 사용하고,
        없으면 정규식으로 기본적인 파싱을 합니다.
        """
        results = []

        try:
            # feedparser가 있으면 사용 (더 안정적)
            import feedparser
            feed = feedparser.parse(xml_data)
            for entry in feed.entries[:max_results]:
                # Google News RSS는 <source> 태그에 출처가 있음
                source = ""
                if hasattr(entry, "source") and hasattr(entry.source, "title"):
                    source = entry.source.title
                elif " - " in entry.title:
                    # 제목 끝에 " - 출처명" 형태로 붙어있는 경우
                    parts = entry.title.rsplit(" - ", 1)
                    if len(parts) == 2:
                        source = parts[1]

                results.append({
                    "title": entry.title,
                    "url": entry.link,
                    "source": source,
                    "date": entry.get("published", ""),
                })
            return results

        except ImportError:
            pass

        # feedparser 없으면 정규식 폴백
        # <item> 블록 안에서 <title>, <link>, <pubDate>, <source> 추출
        items = re.findall(r"<item>(.*?)</item>", xml_data, re.DOTALL)
        for item_xml in items[:max_results]:
            title_match = re.search(r"<title>(.*?)</title>", item_xml)
            link_match = re.search(r"<link>(.*?)</link>", item_xml)
            date_match = re.search(r"<pubDate>(.*?)</pubDate>", item_xml)
            source_match = re.search(r'<source[^>]*>(.*?)</source>', item_xml)

            if title_match and link_match:
                title = title_match.group(1).strip()
                # CDATA 제거
                title = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", title)

                results.append({
                    "title": title,
                    "url": link_match.group(1).strip(),
                    "source": source_match.group(1).strip() if source_match else "",
                    "date": date_match.group(1).strip() if date_match else "",
                })

        return results

    def get_stock_news(self, symbol: str, name: str = "", is_kr: bool = False,
                       max_results: int = 5) -> List[Dict]:
        """
        종목 관련 뉴스 가져오기 (편의 메서드)

        Parameters:
            symbol: 종목 코드 (예: "AAPL", "005930.KS")
            name: 종목명 (예: "Apple", "삼성전자"). 검색 품질 향상용.
            is_kr: 한국 주식 여부
            max_results: 최대 결과 수
        """
        if is_kr:
            # 한국 주식: 종목명으로 검색 (코드보다 뉴스 검색 품질이 높음)
            query = f"{name} 주식" if name and name != symbol else f"{symbol} 주식"
            return self.search_news(query, lang="ko", max_results=max_results)
        else:
            # 미국 주식: 티커 + "stock" 으로 검색
            query = f"{symbol} stock"
            return self.search_news(query, lang="en", max_results=max_results)


class LLMAnalyzer:
    """
    LLM 기반 뉴스 감성 분석기

    뉴스 제목 목록을 LLM에 보내서 투자 관점의 감성 요약을 받습니다.

    모델 우선순위:
    1. llama-cpp-python (로컬 GGUF 모델, Gemma 4 등)
       → pip install llama-cpp-python
       → .env에 LLM_MODEL_PATH=모델파일경로.gguf 설정
    2. Ollama (로컬 서버): OLLAMA_HOST 환경변수
    3. OpenAI (GPT-4o-mini 등): OPENAI_API_KEY 환경변수
    4. Anthropic (Claude): ANTHROPIC_API_KEY 환경변수
    5. 규칙 기반 폴백 (API 없이 키워드 매칭)

    llama-cpp-python + Gemma 4 사용법:
        1. pip install llama-cpp-python
        2. GGUF 모델 파일을 QQQ 폴더에 배치
        3. .env에 LLM_MODEL_PATH=../gemma-4-E4B-it-Q4_K_M.gguf 설정
           (quant-bot 기준 상대경로 또는 절대경로)
        4. GPU 가속: pip install llama-cpp-python --extra-index-url ... (CUDA)
    """

    # ── 싱글톤 모델 인스턴스 (메모리에 한 번만 로드) ──
    _llama_model = None
    _llama_model_path = None

    def __init__(self):
        self.openai_key = os.environ.get("OPENAI_API_KEY", "")
        self.anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
        # Ollama 설정
        self.ollama_host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        self.ollama_model = os.environ.get("OLLAMA_MODEL", "gemma4:4b")

        # llama-cpp-python 모델 경로 결정
        # 우선순위: 환경변수 → QQQ 폴더에서 자동 탐색
        self.model_path = os.environ.get("LLM_MODEL_PATH", "")
        if not self.model_path:
            self.model_path = self._find_gguf_model()

    def _find_gguf_model(self) -> str:
        """
        QQQ 폴더(프로젝트 상위)에서 .gguf 모델 파일을 자동 탐색

        Returns:
            str: 발견된 .gguf 파일의 절대 경로. 없으면 빈 문자열.
        """
        import glob
        # quant-bot 기준 상위 폴더(QQQ)에서 탐색
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        parent_dir = os.path.dirname(project_root)

        # QQQ 폴더와 quant-bot 폴더 모두에서 .gguf 파일 탐색
        search_dirs = [parent_dir, project_root]
        for search_dir in search_dirs:
            gguf_files = glob.glob(os.path.join(search_dir, "*.gguf"))
            if gguf_files:
                # 가장 최근 파일 선택
                gguf_files.sort(key=os.path.getmtime, reverse=True)
                logger.info(f"[LLM] GGUF 모델 발견: {gguf_files[0]}")
                return gguf_files[0]

        return ""

    def _get_llama_model(self):
        """
        llama-cpp-python 모델을 싱글톤으로 로드

        모델은 메모리에 한 번만 올라가며, 이후 요청에서 재사용됩니다.
        GGUF 파일 크기에 따라 첫 로드에 수 초~수십 초 소요될 수 있습니다.
        """
        if LLMAnalyzer._llama_model is not None and LLMAnalyzer._llama_model_path == self.model_path:
            return LLMAnalyzer._llama_model

        if not self.model_path or not os.path.exists(self.model_path):
            return None

        try:
            from llama_cpp import Llama

            logger.info(f"[LLM] 모델 로딩 중: {os.path.basename(self.model_path)} ...")

            # n_ctx: 컨텍스트 윈도우 크기 (뉴스 분석에는 2048이면 충분)
            # n_gpu_layers: GPU 사용 레이어 수 (-1 = 전체 GPU, 0 = CPU only)
            #   → GPU가 있으면 -1, 없으면 0으로 자동 설정
            n_gpu = -1  # GPU 가속 시도 (CUDA 빌드가 아니면 자동으로 CPU 폴백)
            try:
                LLMAnalyzer._llama_model = Llama(
                    model_path=self.model_path,
                    n_ctx=2048,
                    n_gpu_layers=n_gpu,
                    verbose=False,  # 로딩 로그 숨김
                )
            except Exception:
                # GPU 실패 시 CPU로 재시도
                LLMAnalyzer._llama_model = Llama(
                    model_path=self.model_path,
                    n_ctx=2048,
                    n_gpu_layers=0,
                    verbose=False,
                )

            LLMAnalyzer._llama_model_path = self.model_path
            logger.info(f"[LLM] 모델 로드 완료: {os.path.basename(self.model_path)}")
            return LLMAnalyzer._llama_model

        except ImportError:
            logger.warning("[LLM] llama-cpp-python 미설치. pip install llama-cpp-python 실행 필요.")
            return None
        except Exception as e:
            logger.error(f"[LLM] 모델 로드 실패: {e}")
            return None

    def analyze_sentiment(self, symbol: str, news_titles: List[str],
                          lang: str = "ko") -> Optional[str]:
        """
        뉴스 제목들을 분석하여 투자 감성 요약 반환

        Parameters:
            symbol: 종목 코드
            news_titles: 뉴스 제목 리스트
            lang: 응답 언어

        Returns:
            str: AI 분석 요약 텍스트 (1~2문장)
            None: 분석 실패 시
        """
        if not news_titles:
            return None

        if len(news_titles) < 2:
            return None

        # LLM 우선순위: 로컬GGUF → Ollama → OpenAI → Anthropic → 규칙기반
        result = self._analyze_with_llamacpp(symbol, news_titles, lang)
        if result:
            return result

        result = self._analyze_with_ollama(symbol, news_titles, lang)
        if result:
            return result

        if self.openai_key:
            result = self._analyze_with_openai(symbol, news_titles, lang)
            if result:
                return result

        if self.anthropic_key:
            result = self._analyze_with_anthropic(symbol, news_titles, lang)
            if result:
                return result

        return self._rule_based_analysis(news_titles, lang)

    def _build_prompt(self, symbol: str, titles: List[str], lang: str) -> str:
        """감성 분석 프롬프트 생성 (모든 LLM에서 공통 사용)"""
        titles_text = "\n".join(f"- {t}" for t in titles[:10])
        lang_instruction = "한국어로 답변해주세요." if lang == "ko" else "Answer in English."

        return (
            f"다음은 {symbol} 종목 관련 최신 뉴스 제목들입니다:\n\n"
            f"{titles_text}\n\n"
            f"이 뉴스들을 종합하여 투자자 관점에서 감성 분석을 1~2문장으로 "
            f"요약해주세요. (긍정적/부정적/중립적 + 핵심 이유)\n"
            f"{lang_instruction}"
        )

    def _analyze_with_llamacpp(self, symbol: str, titles: List[str],
                                lang: str) -> Optional[str]:
        """
        llama-cpp-python으로 로컬 GGUF 모델 추론

        Gemma 4 등의 GGUF 모델을 Python 프로세스 내에서 직접 실행합니다.
        별도 서버 프로세스 없이 작동하며, 모델은 싱글톤으로 메모리에 유지됩니다.
        """
        model = self._get_llama_model()
        if model is None:
            return None

        try:
            prompt = self._build_prompt(symbol, titles, lang)

            # Gemma 4 instruction format (chat template)
            # <start_of_turn>user\n{prompt}<end_of_turn>\n<start_of_turn>model\n
            formatted_prompt = (
                f"<start_of_turn>user\n{prompt}<end_of_turn>\n"
                f"<start_of_turn>model\n"
            )

            output = model(
                formatted_prompt,
                max_tokens=200,
                temperature=0.3,
                stop=["<end_of_turn>", "<start_of_turn>"],
                echo=False,
            )

            response_text = output["choices"][0]["text"].strip()
            if response_text:
                logger.info(f"[LLM] Gemma4 로컬 분석 완료: {symbol}")
                return response_text
            return None

        except Exception as e:
            logger.warning(f"[LLM] llama-cpp 추론 오류: {e}")
            return None

    def _analyze_with_ollama(self, symbol: str, titles: List[str],
                              lang: str) -> Optional[str]:
        """Ollama (로컬 LLM 서버)로 감성 분석"""
        try:
            import urllib.request

            prompt = self._build_prompt(symbol, titles, lang)

            payload = json.dumps({
                "model": self.ollama_model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.3,
                    "num_predict": 200,
                }
            }).encode("utf-8")

            req = urllib.request.Request(
                f"{self.ollama_host}/api/generate",
                data=payload,
                headers={"Content-Type": "application/json"},
            )

            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                response_text = result.get("response", "").strip()
                if response_text:
                    logger.info(f"[LLM] Ollama ({self.ollama_model}) 분석 완료: {symbol}")
                    return response_text
                return None

        except Exception as e:
            logger.debug(f"[LLM] Ollama 사용 불가: {e}")
            return None

    def _analyze_with_openai(self, symbol: str, titles: List[str],
                              lang: str) -> Optional[str]:
        """OpenAI GPT로 감성 분석"""
        try:
            import urllib.request

            titles_text = "\n".join(f"- {t}" for t in titles[:10])
            lang_instruction = "한국어로 답변해주세요." if lang == "ko" else "Answer in English."

            prompt = (
                f"다음은 {symbol} 종목 관련 최신 뉴스 제목들입니다:\n\n"
                f"{titles_text}\n\n"
                f"이 뉴스들을 종합하여 투자자 관점에서 감성 분석을 1~2문장으로 "
                f"요약해주세요. (긍정적/부정적/중립적 + 핵심 이유)\n"
                f"{lang_instruction}"
            )

            payload = json.dumps({
                "model": "gpt-4o-mini",
                "messages": [
                    {"role": "system", "content": "당신은 주식 시장 뉴스 분석 전문가입니다. 간결하게 핵심만 전달합니다."},
                    {"role": "user", "content": prompt}
                ],
                "max_tokens": 150,
                "temperature": 0.3,
            }).encode("utf-8")

            req = urllib.request.Request(
                "https://api.openai.com/v1/chat/completions",
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.openai_key}",
                },
            )

            with urllib.request.urlopen(req, timeout=15) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                return result["choices"][0]["message"]["content"].strip()

        except Exception as e:
            logger.warning(f"[LLM] OpenAI 분석 실패: {e}")
            return self._rule_based_analysis(titles, lang)

    def _analyze_with_anthropic(self, symbol: str, titles: List[str],
                                 lang: str) -> Optional[str]:
        """Anthropic Claude로 감성 분석"""
        try:
            import urllib.request

            titles_text = "\n".join(f"- {t}" for t in titles[:10])
            lang_instruction = "한국어로 답변해주세요." if lang == "ko" else "Answer in English."

            prompt = (
                f"다음은 {symbol} 종목 관련 최신 뉴스 제목들입니다:\n\n"
                f"{titles_text}\n\n"
                f"이 뉴스들을 종합하여 투자자 관점에서 감성 분석을 1~2문장으로 "
                f"요약해주세요. (긍정적/부정적/중립적 + 핵심 이유)\n"
                f"{lang_instruction}"
            )

            payload = json.dumps({
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 200,
                "messages": [
                    {"role": "user", "content": prompt}
                ],
            }).encode("utf-8")

            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": self.anthropic_key,
                    "anthropic-version": "2023-06-01",
                },
            )

            with urllib.request.urlopen(req, timeout=15) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                return result["content"][0]["text"].strip()

        except Exception as e:
            logger.warning(f"[LLM] Anthropic 분석 실패: {e}")
            return self._rule_based_analysis(titles, lang)

    def _rule_based_analysis(self, titles: List[str], lang: str) -> Optional[str]:
        """
        규칙 기반 간단 감성 분석 (LLM API 키 없을 때 폴백)

        긍정/부정 키워드 매칭으로 대략적인 감성을 판단합니다.
        정확도는 LLM보다 낮지만, API 비용 없이 작동합니다.
        """
        # 긍정 키워드
        positive_ko = ["상승", "급등", "호재", "최고", "성장", "돌파", "반등", "강세",
                       "매수", "추천", "목표가", "실적", "호실적", "수주", "기대",
                       "surge", "rally", "buy", "upgrade", "growth", "high", "beat"]
        # 부정 키워드
        negative_ko = ["하락", "급락", "악재", "최저", "위기", "매도", "하향",
                       "리스크", "우려", "폭락", "약세", "손실", "적자",
                       "sell", "downgrade", "decline", "fall", "risk", "loss", "miss"]

        combined = " ".join(titles).lower()
        pos_count = sum(1 for kw in positive_ko if kw in combined)
        neg_count = sum(1 for kw in negative_ko if kw in combined)

        total = pos_count + neg_count
        if total == 0:
            if lang == "ko":
                return "뉴스 기반 판단: 중립적. 특별한 호재/악재 없이 평이한 뉴스입니다."
            return "News-based sentiment: Neutral. No significant positive or negative catalysts detected."

        if pos_count > neg_count * 1.5:
            if lang == "ko":
                return f"뉴스 기반 판단: 긍정적 (긍정 {pos_count}건, 부정 {neg_count}건). 호재성 뉴스가 우세합니다."
            return f"News sentiment: Positive ({pos_count} positive, {neg_count} negative signals). Bullish catalysts dominate."
        elif neg_count > pos_count * 1.5:
            if lang == "ko":
                return f"뉴스 기반 판단: 부정적 (긍정 {pos_count}건, 부정 {neg_count}건). 악재성 뉴스에 주의하세요."
            return f"News sentiment: Negative ({pos_count} positive, {neg_count} negative signals). Watch for bearish catalysts."
        else:
            if lang == "ko":
                return f"뉴스 기반 판단: 혼재 (긍정 {pos_count}건, 부정 {neg_count}건). 방향성 불확실, 기술적 분석 병행 권장."
            return f"News sentiment: Mixed ({pos_count} positive, {neg_count} negative signals). Direction uncertain."


def get_news_with_analysis(symbol: str, name: str = "",
                           is_kr: bool = False, lang: str = "ko") -> Dict:
    """
    종목 뉴스 수집 + LLM 감성 분석 통합 함수

    대시보드 API에서 이 함수를 호출하면 뉴스와 AI 분석을 한 번에 받을 수 있습니다.

    Parameters:
        symbol: 종목 코드
        name: 종목명
        is_kr: 한국 주식 여부
        lang: 응답 언어

    Returns:
        {
            "news": [{ title, url, source, date }],
            "ai_summary": "AI 감성 분석 요약 텍스트",
            "sentiment": "positive" | "negative" | "neutral" | "mixed"
        }
    """
    collector = NewsCollector()
    analyzer = LLMAnalyzer()

    # 뉴스 수집
    news = collector.get_stock_news(
        symbol=symbol, name=name, is_kr=is_kr, max_results=5
    )

    # LLM 감성 분석
    titles = [n["title"] for n in news]
    ai_summary = analyzer.analyze_sentiment(symbol, titles, lang)

    return {
        "news": news,
        "ai_summary": ai_summary,
    }
