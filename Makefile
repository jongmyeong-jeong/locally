.DEFAULT_GOAL := help
.PHONY: help setup start smoke

help:
	@echo ""
	@echo "Locally — Groq 음성 전사"
	@echo ""
	@echo "  make setup        의존성 설치 (Python + Web)"
	@echo "  make start        서버 실행 + 브라우저 자동 오픈"
	@echo "  make smoke        실제 Groq API 호출 검증 (.env 키 필요)"
	@echo ""

setup:
	@sh scripts/install.sh

start:
	@uv run locally start

smoke:
	@uv run python -c "from pathlib import Path; from dotenv import load_dotenv; load_dotenv(Path('.env')); import time; from app.groq_client import transcribe_audio; f = Path('tests/fixtures/test_30s.mp3'); print(f'fixture: {f} ({f.stat().st_size} bytes)'); t0 = time.time(); r = transcribe_audio(f, language='ko'); dt = time.time() - t0; print(f'OK in {dt:.2f}s — text={r[\"text\"]!r}, segments={len(r[\"segments\"])}')"
