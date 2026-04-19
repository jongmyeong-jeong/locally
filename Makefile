.PHONY: setup start dev dev-backend dev-web build build-web test test-web lint

setup:
	@sh scripts/install.sh

start:
	@command -v locally >/dev/null 2>&1 || { \
	  printf "\n"; \
	  printf "  ⚠ locally 명령어가 아직 설치되지 않았어요.\n"; \
	  printf "\n"; \
	  printf "    설치하려면 먼저 아래 명령을 실행해주세요:\n"; \
	  printf "\n"; \
	  printf "      make setup\n"; \
	  printf "\n"; \
	  printf "    자세한 내용은 README.md 를 참고하세요.\n"; \
	  printf "\n"; \
	  exit 1; \
	}
	@locally start

dev-backend:
	@uv run uvicorn app.server:app --reload --host 0.0.0.0 --port 8000

dev-web:
	@cd web && pnpm dev

dev:
	@trap 'kill 0' INT; \
	  $(MAKE) dev-backend & \
	  $(MAKE) dev-web & \
	  wait

build:
	@uv build

build-web:
	@cd web && pnpm build

test:
	@uv run pytest tests/ -v

test-web:
	@cd web && pnpm test

lint:
	@uv run ruff check app/ tests/
