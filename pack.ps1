# =============================================================================
# pack.ps1 - 다른 PC로 옮길 파일만 골라서 zip 압축 (PowerShell 본체)
# =============================================================================
# pack.bat에서 호출되는 PowerShell 스크립트.
# Batch에서 멀티라인 PowerShell 호출 시 변수 확장이 깨지는 문제를 회피.
# =============================================================================

param(
    [int]$Choice = 0  # 0이면 메뉴 표시, 1/2/3이면 자동 선택
)

# 작업 디렉토리를 스크립트 위치로
Set-Location -Path $PSScriptRoot

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
    Write-Host "      - .env(API 키) 제외 → 새 PC에서 직접 입력"
    Write-Host ""
    Write-Host "  [3] 코드 + 거래기록 + API 키 (큼, ~3-5MB)"
    Write-Host "      - 전부 포함 → 즉시 작동"
    Write-Host "      - .env에 KIS App Key/Secret 포함 ⚠️ 분실 주의"
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
    'setup.bat', 'start.bat', 'pack.bat', 'pack.ps1',
    '.gitignore', '.env.example',
    'API_KEYS.txt.example',  # ★ 친화적 API 키 입력 템플릿 (항상 포함)
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

# ─── 압축 실행 ─────────────────────────────────────────────────────────
try {
    # 기존 파일이 있으면 덮어쓰기
    if (Test-Path $output) {
        Remove-Item $output -Force
    }

    Compress-Archive `
        -Path $existing `
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
