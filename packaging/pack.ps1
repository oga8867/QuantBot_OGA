# =============================================================================
# pack.ps1 - 다른 PC로 옮길 파일만 골라서 zip 압축 (PowerShell 본체)
# =============================================================================
# pack.bat에서 호출되는 PowerShell 스크립트.
# Batch에서 멀티라인 PowerShell 호출 시 변수 확장이 깨지는 문제를 회피.
# =============================================================================

param(
    [int]$Choice = 0  # 0이면 메뉴 표시, 1/2/3이면 자동 선택
)

# 작업 디렉토리를 프로젝트 루트로 (이 스크립트는 packaging/ 하위에 있음)
# packaging/ 안에 있어도 프로젝트 전체를 올바르게 압축하기 위해 상위 폴더로 이동.
Set-Location -Path (Split-Path -Parent $PSScriptRoot)

# UTF-8 출력
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

Write-Host "============================================================"
Write-Host "                  퀀트봇 이전용 압축"
Write-Host "============================================================"
Write-Host ""

# ─── 메뉴 (Choice 미지정 시) ────────────────────────────────────────────
if ($Choice -eq 0) {
    Write-Host "옵션을 선택하세요:"
    Write-Host ""
    Write-Host "  [1] 코드만 (가장 작음, ~1MB)"
    Write-Host "      - API 키, DB, 보고서 모두 제외"
    Write-Host "      - 새 PC에서 처음부터 시작"
    Write-Host ""
    Write-Host "  [2] 코드 + 거래기록 (중간, ~3-5MB)  ★권장"
    Write-Host "      - DB 포함 (기존 포지션, 거래 이력 유지)"
    Write-Host "      - API 키 자동 제거 (user_settings.json + .env + KIS 토큰)"
    Write-Host "      - 새 PC에서 대시보드 → 설정 탭에서 직접 입력"
    Write-Host ""
    Write-Host "  [3] 코드 + 거래기록 + API 키 (큼, ~3-5MB)"
    Write-Host "      - 전부 포함 → 즉시 작동"
    Write-Host "      - user_settings.json + .env에 KIS Key/Secret 평문 포함 ⚠️ 분실 주의"
    Write-Host ""

    $input = Read-Host "번호 입력 (1/2/3, 기본 2)"
    if ([string]::IsNullOrWhiteSpace($input)) { $input = "2" }
    $Choice = [int]$input
}

# ─── 옵션별 포함 항목 결정 ──────────────────────────────────────────────
$baseItems = @(
    # 소스코드 폴더 (15개)
    'analyzers', 'backtest', 'collectors', 'config', 'dashboard',
    'database', 'executor', 'notifier', 'reporter', 'risk',
    'scheduler', 'scripts', 'strategy', 'tests', 'utils',

    # 학습 노트 + 문서
    'skill',

    # 루트 파일
    'run_bot.py', 'main.py', 'requirements.txt',
    # 실행 런처(.bat) — 새 환경에서 더블클릭으로 동일하게 실행하기 위해 전부 포함
    'setup.bat', 'start.bat', 'dashboard.bat', 'install.bat',
    'run_bot.bat', 'analyze.bat',
    # 패키징 도구 폴더 (pack.bat/pack.ps1/패키지 사용법) — 공유 zip에도 포함하여
    # 받은 사람이 다시 재배포할 수 있게 한다.
    'packaging',
    '.gitignore', '.env.example',
    'API_KEYS.txt.example',  # ★ 친화적 API 키 입력 템플릿 (항상 포함)
    'repair_positions.py',   # 깨진 positions 행 1회성 복구 도구 (필요 시만 사용)
    '사용법.md',             # 한글 사용법 + 코드 구조 설명
    'SETUP_GUIDE.md', 'README.md', 'USER_GUIDE.md', 'CLAUDE.md'
)

switch ($Choice) {
    1 {
        $items = $baseItems
        $mode = "코드만"
    }
    2 {
        $items = $baseItems + @('data', 'user_settings.json')
        $mode = "코드 + 거래기록"
    }
    3 {
        $items = $baseItems + @('data', 'user_settings.json', '.env', 'API_KEYS.txt')
        $mode = "전부 (API 키 포함)"
    }
    default {
        Write-Host "[오류] 잘못된 선택: $Choice (1/2/3 중 하나여야 함)" -ForegroundColor Red
        exit 1
    }
}

# ─── 실제 존재하는 항목만 필터링 ────────────────────────────────────────
$existing = $items | Where-Object { Test-Path -Path $_ }

if ($existing.Count -eq 0) {
    Write-Host "[오류] 포함할 파일을 찾지 못했습니다." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "포함 항목: $($existing.Count)개" -ForegroundColor Cyan
$existing | ForEach-Object { Write-Host "  - $_" -ForegroundColor Gray }

# ─── 출력 파일명 (타임스탬프) ───────────────────────────────────────────
$timestamp = Get-Date -Format "yyyyMMdd_HHmm"
$output = "quant-bot_$timestamp.zip"

Write-Host ""
Write-Host "압축 중... (출력: $output)" -ForegroundColor Yellow

# ─── 스테이징(임시 폴더) → 민감정보 스크럽 → 압축 ─────────────────────
# 모드 1: user_settings.json/data 자체가 포함 안 됨 → 스크럽 불필요
# 모드 2: user_settings.json의 API 키 필드 비움 + KIS 토큰 캐시 제거 (안전 디폴트)
# 모드 3: 사용자가 명시적으로 "API 포함"을 선택 → 그대로 둠
#
# 원본 파일은 절대 건드리지 않음 (임시 폴더에 복사 후 그곳에서 스크럽).
$staging = Join-Path $env:TEMP "quantbot_pack_$($timestamp)_$PID"
if (Test-Path $staging) { Remove-Item $staging -Recurse -Force }
New-Item -ItemType Directory -Path $staging | Out-Null

try {
    # 1) 모든 항목을 스테이징으로 복사
    foreach ($item in $existing) {
        $dest = Join-Path $staging $item
        $parent = Split-Path -Parent $dest
        if ($parent -and -not (Test-Path $parent)) {
            New-Item -ItemType Directory -Path $parent -Force | Out-Null
        }
        if (Test-Path $item -PathType Container) {
            Copy-Item -Path $item -Destination $dest -Recurse -Force
        } else {
            Copy-Item -Path $item -Destination $dest -Force
        }
    }

    # 2) 모드 2면 민감정보 자동 스크럽
    if ($Choice -eq 2) {
        Write-Host ""
        Write-Host "민감정보 스크럽 중..." -ForegroundColor Yellow

        # 2a) user_settings.json의 API 키 필드 비우기
        $stagedSettings = Join-Path $staging "config\user_settings.json"
        if (Test-Path $stagedSettings) {
            try {
                $raw = [System.IO.File]::ReadAllText($stagedSettings)
                $cfg = $raw | ConvertFrom-Json
                $sensitive = @(
                    'kis_app_key', 'kis_app_secret', 'kis_account',
                    'discord_bot_token', 'discord_bot_channel_id', 'discord_bot_app_id',
                    'discord_webhook_url',
                    'telegram_token', 'telegram_chat_id',
                    'alpaca_api_key', 'alpaca_secret_key',
                    'dart_api_key'
                )
                $cleared = 0
                foreach ($key in $sensitive) {
                    if ($cfg.PSObject.Properties.Name -contains $key) {
                        if ($cfg.$key -and ($cfg.$key -ne "")) { $cleared++ }
                        $cfg.$key = ""
                    }
                }
                $cleanedJson = $cfg | ConvertTo-Json -Depth 10
                # UTF-8 BOM 없이 쓰기 (Python json.load 호환)
                $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
                [System.IO.File]::WriteAllText($stagedSettings, $cleanedJson, $utf8NoBom)
                Write-Host ("  + user_settings.json: {0}개 키 필드 비움" -f $cleared) -ForegroundColor Cyan
            } catch {
                Write-Host "  [경고] user_settings.json 스크럽 실패: $_" -ForegroundColor Yellow
                Write-Host "         → 새 PC에서 수동으로 키 필드를 비우거나 재입력하세요" -ForegroundColor Yellow
            }
        }

        # 2b) KIS 토큰 캐시 제거 (유효 동안 계좌 조회/주문 가능 → 절대 공유 금지)
        $stagedData = Join-Path $staging "data"
        if (Test-Path $stagedData) {
            $tokens = Get-ChildItem -Path $stagedData -Filter "kis_token_*.json" -ErrorAction SilentlyContinue
            foreach ($tk in $tokens) {
                Remove-Item $tk.FullName -Force
                Write-Host "  + $($tk.Name) 제거" -ForegroundColor Cyan
            }
        }
    }

    # 3) 압축 (스테이징의 최상위 항목을 zip 루트에 그대로 배치)
    if (Test-Path $output) { Remove-Item $output -Force }
    Compress-Archive `
        -Path (Join-Path $staging "*") `
        -DestinationPath $output `
        -CompressionLevel Optimal `
        -Force `
        -ErrorAction Stop

    $size = (Get-Item $output).Length / 1MB
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host "                       압축 완료!" -ForegroundColor Green
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host ""
    Write-Host ("  파일: {0}" -f $output)
    Write-Host ("  크기: {0:N2} MB" -f $size)
    Write-Host ("  모드: {0}" -f $mode)
    Write-Host ""
    Write-Host "  새 PC에서:"
    Write-Host "   1. 이 zip 파일을 원하는 위치에 복사 후 압축 해제"
    Write-Host "   2. setup.bat 더블클릭"
    Write-Host "   3. 5분 기다리면 자동으로 대시보드 실행됨"
    if ($Choice -ne 3) {
        Write-Host "   4. 대시보드 → 설정 탭에서 API 키 입력"
    }
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Green
}
catch {
    Write-Host ""
    Write-Host "[오류] 압축 실패: $_" -ForegroundColor Red
    exit 1
}
finally {
    # 스테이징은 항상 정리 (원본은 그대로)
    if (Test-Path $staging) {
        Remove-Item $staging -Recurse -Force -ErrorAction SilentlyContinue
    }
}
