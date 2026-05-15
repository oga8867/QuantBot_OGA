# 🚀 새 PC 설치 가이드

다른 PC에서 퀀트봇을 똑같이 사용하려면 이 가이드를 따라하세요.
**대부분 자동화되어 있어서 5분 안에 끝납니다.**

---

## ⚡ 빠른 시작 (3단계)

### 1️⃣ 폴더 복사
USB/외장하드/네트워크 드라이브에 `quant-bot` 폴더를 통째로 복사.

> **단, `venv` 폴더는 빼고 복사하세요.** (수백 MB짜리 가상환경, 새 PC에서 자동 재생성됨)
>
> 복사 전:
> ```powershell
> Remove-Item -Recurse X:\QQQ\quant-bot\venv
> ```

### 2️⃣ 새 PC에서 setup.bat 실행
```
quant-bot 폴더 → setup.bat 더블클릭
```

자동으로 진행되는 작업:
- ✅ Python 설치 확인 (없으면 자동 설치)
- ✅ 가상환경(venv) 생성
- ✅ 모든 의존성 패키지 설치
- ✅ `.env` 템플릿 생성 (없을 때만)
- ✅ 대시보드 자동 실행
- ✅ 브라우저 자동 열기 → http://localhost:5000

### 3️⃣ 대시보드에서 API 키 입력
브라우저가 열리면 **"설정" 탭**으로 이동 → API 키 입력 → "저장" 버튼:

| 항목 | 어디서 받음 | 용도 |
|------|-----------|------|
| **KIS App Key** | apiportal.koreainvestment.com | 한국 실시간 시세 + 매매 |
| **KIS App Secret** | (App Key와 함께) | (위와 동일) |
| **KIS 계좌번호** | 한국투자 앱 | (위와 동일) |
| **Alpaca API Key** | alpaca.markets | 미국 매매 (선택) |
| **Telegram Bot Token** | @BotFather | 텔레그램 알림 (선택) |
| **Discord Webhook** | 디스코드 채널 설정 | 디스코드 알림 (선택) |

저장하면 자동으로 `.env` 파일에 기록되고 봇이 사용합니다.

> 💡 KIS만 있어도 한국 주식 매매는 충분합니다. 다른 키는 나중에 필요할 때 입력해도 됩니다.

---

## 🔧 상세 절차

### Python이 자동 설치 안 될 때

`setup.bat`은 두 가지 방법으로 Python 설치를 시도합니다:
1. `winget` (Windows 10 1809+ 기본 탑재)
2. python.org에서 직접 다운로드

둘 다 실패하면 **수동 설치**:
1. https://www.python.org/downloads/ 접속
2. **Python 3.11.9** 다운로드 (3.10~3.12 권장, 3.14는 일부 라이브러리 호환 이슈 가능)
3. 설치 시 **"Add Python to PATH" 반드시 체크** ⚠️
4. 설치 완료 후 `setup.bat` 다시 실행

### 의존성 설치가 너무 느림

numpy, pandas, scipy 같은 큰 패키지는 처음 설치 시 5~10분 걸립니다. 정상입니다.

만약 진행 안 되면:
```powershell
cd quant-bot
.\venv\Scripts\pip.exe install -r requirements.txt
```
(--quiet 빼면 진행 상황 보임)

### 봇 시작은 어떻게?

설치 후에는 **`start.bat`** 더블클릭만 하면 됩니다:
- venv 자동 활성화
- 대시보드 시작
- 브라우저 자동 열기

`setup.bat`은 **처음 한 번만** 실행하면 됩니다.

---

## 📦 어떤 폴더/파일을 옮길지

### ✅ 반드시 옮길 것
```
quant-bot/
├── analyzers/         코드
├── backtest/          코드
├── collectors/        코드
├── config/            코드
├── dashboard/         코드 + UI
├── database/          코드
├── executor/          코드
├── notifier/          코드
├── reporter/          코드
├── risk/              코드
├── scheduler/         코드
├── scripts/           유틸 스크립트
├── strategy/          코드
├── tests/             테스트
├── utils/             코드
├── skill/             학습 노트 (선택)
├── requirements.txt   ★ 의존성 목록
├── setup.bat          ★ 자동 설치
├── start.bat          ★ 일상 실행
├── run_bot.py         메인 진입점
├── main.py            CLI 도구
└── README.md / SETUP_GUIDE.md
```

### 🟡 선택적 (옮기면 기존 데이터 유지됨)
```
data/quantbot.db       기존 거래 기록 + 보유 포지션 + 자산 히스토리
.env                   API 키 (USB 안전하면 같이, 아니면 새 PC에서 입력)
reports/               생성된 HTML 보고서
logs/                  로그 파일
user_settings.json     사용자 워치리스트 등
```

### ❌ 절대 옮기지 말 것
```
venv/                  가상환경 (OS/Python 버전 의존, 새 PC에서 재생성)
__pycache__/           파이썬 캐시 (자동 재생성)
.pytest_cache/         pytest 캐시 (자동 재생성)
```

---

## 🔐 보안 주의사항

### `.env` 파일 옮기기
| 방식 | 장점 | 단점 |
|------|------|------|
| 같이 옮김 | 즉시 작동 | 분실 시 키 노출 |
| 새 PC에서 입력 | 안전 | 키 다시 입력 필요 |

**추천**: USB가 분실 위험 있으면 `.env` 빼고 옮긴 후, 새 PC에서 대시보드 → 설정 탭 → 직접 입력.

### API 키가 노출됐다면?
즉시 [apiportal.koreainvestment.com](https://apiportal.koreainvestment.com) 에서 **"앱 키 재발급"**.
기존 키는 자동 무효화되고, 새 키를 `.env` 또는 대시보드에 입력.

---

## 🆘 문제 해결

### Q. setup.bat이 즉시 닫힘
- **원인**: 줄바꿈이 LF로 되어있음 (Linux/Mac에서 편집됨)
- **해결**: 메모장으로 열어 다시 저장 (또는 VS Code에서 CRLF로 변환)

### Q. "Python이 설치되어 있지 않습니다" 무한 반복
- **원인**: PATH 환경변수에 Python이 없음
- **해결**:
  1. Python 설치 시 **"Add Python to PATH"** 체크 누락
  2. 재설치 후 cmd 새로 열어서 `python --version` 확인

### Q. 의존성 설치 실패 (Microsoft Visual C++ 14 필요)
- **원인**: numpy/pandas 컴파일을 위해 Visual C++ Build Tools 필요
- **해결**:
  1. https://visualstudio.microsoft.com/visual-cpp-build-tools/ 다운로드
  2. 설치 시 "C++ build tools" 선택
  3. setup.bat 재실행

### Q. 대시보드 http://localhost:5000 연결 안 됨
- **원인**: 포트 차단 또는 다른 프로그램이 사용 중
- **해결**:
  1. Windows 방화벽 → 5000 포트 허용
  2. 다른 포트 사용: `dashboard/app.py` 마지막 줄 `port=5000` → `port=8080`

### Q. 봇이 한국 시간을 못 인식 (장 마감 판별 오류)
- **원인**: PC 시간대가 UTC 또는 다른 시간대
- **해결**: Windows 설정 → 시간 → 시간대 → "(UTC+09:00) 서울"

### Q. KIS API 연결됐는데 "계좌가 비어있다"고 표시
- **원인**: 모의투자 계좌 활성화 안 됨
- **해결**: 한국투자 모바일 앱 → "모의투자" → 신청 → 즉시 1억원 자동 입금

### Q. 새 PC에서 기존 보유 포지션이 안 보임
- **원인**: `data/quantbot.db` 파일을 안 옮김
- **해결**: 원래 PC에서 `data/quantbot.db` 복사 → 새 PC의 `data/` 폴더에 붙여넣기 → 봇 재시작

---

## 🌐 GitHub로 관리하면 더 편함 (선택)

매번 USB 안 들고 다니고 싶다면 GitHub Private Repository 사용:

```bash
# 현재 PC에서 (한 번만)
cd quant-bot
git init
git add .
git commit -m "Initial commit"
# GitHub.com → New Private Repository 만든 후
git remote add origin https://github.com/본인계정/quant-bot.git
git push -u origin main

# 새 PC에서 (PC 바꿀 때마다)
git clone https://github.com/본인계정/quant-bot.git
cd quant-bot
.\setup.bat
```

`.gitignore` 덕분에 `.env`, `data/`, `venv/`는 자동 제외됩니다.
- ✅ 코드는 깃허브로 안전하게
- ⚠️ `.env`(API 키)는 별도로 옮겨야 함
- ⚠️ `data/quantbot.db`(거래 기록)도 별도로

---

## 📊 24/7 운영을 원한다면

PC를 끄지 않고 봇을 24시간 돌리려면 클라우드 서버가 필요합니다.

| 서비스 | 사양 | 가격 | 추천 |
|-------|-----|-----|------|
| Oracle Cloud Free | ARM 4코어 24GB | 무료 영구 | ⭐⭐⭐⭐⭐ |
| AWS Lightsail | 1GB | $3.5/월 | ⭐⭐⭐⭐ |
| 카페24/가비아 VPS | - | 1만원~/월 | ⭐⭐⭐ |

세팅 가이드는 별도로 만들어드릴 수 있습니다. (요청 시)

---

## ✅ 최종 체크리스트

새 PC 설치가 잘 됐는지 확인:

```
[ ] setup.bat 실행 후 에러 없이 완료
[ ] http://localhost:5000 접속됨
[ ] 대시보드에 KOSPI/NASDAQ 지수 카드가 보임
[ ] 설정 탭에서 KIS API 키 입력 후 저장 → 페이지 상단 KIS 배지 🟢
[ ] "봇 시작" 버튼 누르면 즉시 분석 사이클 시작
[ ] (선택) 기존 거래 기록이 거래 이력 탭에 보임
[ ] (선택) 자산 변화 그래프가 정상 표시됨
[ ] pytest tests/test_regression_bugs.py → 18 passed
```

전부 통과하면 새 PC 설치 완료! 🎉

---

*작성일: 2026-05-08*
*최종 검증 환경: Windows 10/11, Python 3.11.9*
