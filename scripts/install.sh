#!/bin/sh
set -e

# TODO: 최종 GitHub 주소 확정 후 교체
REPO_URL="https://github.com/jongmyeong-jeong/locally.git"

ok()   { printf "✓ %s\n" "$*"; }
step() { printf "▸ %s\n" "$*"; }
warn() { printf "⚠ %s\n" "$*"; }
die()  { printf "✗ %s — %s\n" "$1" "$2"; exit 1; }

# ── OS detection ─────────────────────────────────────────────────────────────
OS=$(uname -s)
case "$OS" in
  Darwin) ok "OS: macOS" ;;
  Linux)  ok "OS: Linux" ;;
  *)      die "Unsupported OS: $OS" "Run on macOS or Linux" ;;
esac

# ── uv ───────────────────────────────────────────────────────────────────────
if command -v uv >/dev/null 2>&1; then
  ok "uv: already installed"
else
  step "Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh \
    || die "uv install failed" "Check network and retry: sh scripts/install.sh"
  . "$HOME/.local/bin/env" 2>/dev/null || export PATH="$HOME/.local/bin:$PATH"
  ok "uv: installed"
fi

# ── source location ───────────────────────────────────────────────────────────
if [ -f "$(pwd)/pyproject.toml" ]; then
  ok "Source: current directory"
  PROJECT_DIR="$(pwd)"
else
  PROJECT_DIR="$HOME/.locally/source"
  if [ -d "$PROJECT_DIR/.git" ]; then
    ok "Source: already cloned at $PROJECT_DIR"
    git -C "$PROJECT_DIR" pull --ff-only 2>/dev/null \
      || warn "Could not pull latest — using existing source"
  else
    step "Cloning source..."
    git clone "$REPO_URL" "$PROJECT_DIR" \
      || die "Clone failed" "Check network and retry: sh <(curl -fsSL ...)"
    ok "Cloned: $PROJECT_DIR"
  fi
fi

# ── Node.js ───────────────────────────────────────────────────────────────────
if command -v node >/dev/null 2>&1; then
  ok "Node.js: already installed ($(node --version))"
else
  step "Installing Node.js..."
  if [ "$OS" = "Darwin" ]; then
    command -v brew >/dev/null 2>&1 \
      || die "Homebrew required" "Install from https://brew.sh then retry"
    brew install node \
      || die "Node.js install failed" "Run 'brew install node' manually then retry: make setup"
  elif command -v apt-get >/dev/null 2>&1; then
    sudo apt-get install -y nodejs npm \
      || die "Node.js install failed" "Run 'sudo apt-get install nodejs' manually then retry"
  elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y nodejs \
      || die "Node.js install failed" "Run 'sudo dnf install nodejs' manually then retry"
  else
    die "No package manager found" "Install Node.js manually: https://nodejs.org"
  fi
  ok "Node.js: installed"
fi

# ── ffmpeg ────────────────────────────────────────────────────────────────────
if command -v ffmpeg >/dev/null 2>&1; then
  ok "ffmpeg: already installed"
else
  step "Installing ffmpeg..."
  if [ "$OS" = "Darwin" ]; then
    brew install ffmpeg \
      || die "ffmpeg install failed" "Run 'brew install ffmpeg' manually then retry: make setup"
  elif command -v apt-get >/dev/null 2>&1; then
    sudo apt-get install -y ffmpeg \
      || die "ffmpeg install failed" "Run 'sudo apt-get install ffmpeg' manually then retry"
  elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y ffmpeg \
      || die "ffmpeg install failed" "Run 'sudo dnf install ffmpeg' manually then retry"
  else
    die "No package manager found" "Install ffmpeg manually: https://ffmpeg.org"
  fi
  ok "ffmpeg: installed"
fi

# ── pnpm ──────────────────────────────────────────────────────────────────────
if command -v pnpm >/dev/null 2>&1; then
  ok "pnpm: already installed"
else
  step "Enabling pnpm..."
  corepack enable pnpm 2>/dev/null \
    || npm install -g pnpm \
    || die "pnpm activation failed" "Run 'npm install -g pnpm' manually then retry: make setup"
  ok "pnpm: enabled"
fi

# ── locally ───────────────────────────────────────────────────────────────────
if command -v locally >/dev/null 2>&1; then
  step "Updating locally..."
  uv tool install --reinstall "$PROJECT_DIR" \
    || die "locally update failed" "Check error message and retry: make setup"
  ok "locally: updated"
else
  step "Installing locally..."
  uv tool install "$PROJECT_DIR" \
    || die "locally install failed" "Check error message and retry: make setup"
  UV_BIN="$(uv tool dir --bin 2>/dev/null || echo "$HOME/.local/bin")"
  export PATH="$UV_BIN:$PATH"
  ok "locally: installed"
fi

# ── run ───────────────────────────────────────────────────────────────────────
printf "\nhttp://127.0.0.1:54787\n"
locally start
