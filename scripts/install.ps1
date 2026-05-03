#Requires -Version 5.1
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

# TODO: 최종 GitHub 주소 확정 후 교체
$RepoUrl = 'https://github.com/jongmyeong-jeong/lonta.git'

function ok($msg)   { Write-Host "✓ $msg" }
function step($msg) { Write-Host "▸ $msg" }
function warn($msg) { Write-Host "⚠ $msg" }
function die($msg, $hint) { Write-Host "✗ $msg — $hint"; exit 1 }

ok "OS 감지: Windows"

# ── uv ────────────────────────────────────────────────────────────────────────
if (Get-Command uv -ErrorAction SilentlyContinue) {
    ok "이미 설치됨: uv"
} else {
    step "uv 설치 중..."
    try {
        irm https://astral.sh/uv/install.ps1 | iex
        $env:PATH = "$env:USERPROFILE\.local\bin;$env:PATH"
        ok "uv 설치 완료"
    } catch {
        die "uv 설치 실패" "네트워크 확인 후 재시도: .\scripts\install.ps1"
    }
}

# ── 소스 위치 결정 ─────────────────────────────────────────────────────────────
if (Test-Path "pyproject.toml") {
    ok "소스 감지: 현재 디렉토리 사용"
    $ProjectDir = (Get-Location).Path
} else {
    $ProjectDir = Join-Path $env:USERPROFILE ".lonta\source"
    if (Test-Path (Join-Path $ProjectDir ".git")) {
        ok "이미 클론됨: $ProjectDir"
        try { git -C $ProjectDir pull --ff-only 2>$null } catch {
            warn "최신 버전 확인 실패 — 기존 소스로 진행합니다"
        }
    } else {
        step "소스 클론 중..."
        try {
            git clone $RepoUrl $ProjectDir
            ok "클론 완료: $ProjectDir"
        } catch {
            die "소스 클론 실패" "네트워크 확인 후 재시도: .\scripts\install.ps1"
        }
    }
}

# ── Node.js ───────────────────────────────────────────────────────────────────
if (Get-Command node -ErrorAction SilentlyContinue) {
    $nodeVer = node --version 2>$null
    ok "이미 설치됨: Node.js $nodeVer"
} else {
    step "Node.js 설치 중..."
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        try {
            winget install -e --id OpenJS.NodeJS.LTS --accept-package-agreements --accept-source-agreements
            $env:PATH = [System.Environment]::GetEnvironmentVariable('PATH','Machine') + ';' + [System.Environment]::GetEnvironmentVariable('PATH','User')
            ok "Node.js 설치 완료"
        } catch {
            die "Node.js 설치 실패" "'winget install OpenJS.NodeJS.LTS' 수동 실행 후 재시도"
        }
    } else {
        die "winget을 찾지 못했습니다" "https://nodejs.org 에서 Node.js 수동 설치 후 재시도"
    }
}

# ── ffmpeg ────────────────────────────────────────────────────────────────────
if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
    ok "이미 설치됨: ffmpeg"
} else {
    step "ffmpeg 설치 중..."
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        try {
            winget install -e --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
            $env:PATH = [System.Environment]::GetEnvironmentVariable('PATH','Machine') + ';' + [System.Environment]::GetEnvironmentVariable('PATH','User')
            ok "ffmpeg 설치 완료"
        } catch {
            die "ffmpeg 설치 실패" "'winget install Gyan.FFmpeg' 수동 실행 후 재시도"
        }
    } else {
        die "winget을 찾지 못했습니다" "https://ffmpeg.org 에서 ffmpeg 수동 설치 후 재시도"
    }
}

# ── pnpm ──────────────────────────────────────────────────────────────────────
if (Get-Command pnpm -ErrorAction SilentlyContinue) {
    ok "이미 설치됨: pnpm"
} else {
    step "pnpm 활성화 중..."
    try {
        corepack enable pnpm
        ok "pnpm 활성화 완료"
    } catch {
        try {
            npm install -g pnpm
            ok "pnpm 설치 완료"
        } catch {
            die "pnpm 활성화 실패" "'npm install -g pnpm' 수동 실행 후 재시도"
        }
    }
}

# ── lonta 설치 ──────────────────────────────────────────────────────────────
if (Get-Command lonta -ErrorAction SilentlyContinue) {
    ok "이미 설치됨: lonta"
} else {
    step "lonta 설치 중 (프론트엔드 빌드 포함, 수 분 소요)..."
    try {
        uv tool install $ProjectDir
        $uvBin = uv tool dir --bin 2>$null
        if ($uvBin) { $env:PATH = "$uvBin;$env:PATH" }
        ok "lonta 설치 완료"
    } catch {
        die "lonta 설치 실패" "오류 메시지 확인 후 재시도: .\scripts\install.ps1"
    }
}

# ── 실행 ─────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "http://127.0.0.1:54787"
lonta start
