# Locally

groq Whisper API 기반 음성 전사 → Markdown 다운로드

## 빠른 시작

```bash
make setup                       # 의존성 설치
cp .env.example .env             # GROQ_API_KEY 입력
make start                       # 서버 실행 + 브라우저 오픈
```

[console.groq.com](https://console.groq.com)에서 API Key 발급

## make 명령어

| 명령 | 동작 |
|---|---|
| `make help` | 명령어 목록 |
| `make setup` | 의존성 설치 (Python + Web) |
| `make start` | 서버 실행 + 브라우저 자동 오픈 |
| `make smoke` | 실제 Groq API 호출 검증 |

## 환경 변수

| 변수 | 필수 | 기본값 |
|---|---|---|
| `GROQ_API_KEY` | ✅ | — |
| `LOCALLY_LANG` | | `ko` (`ko`/`en`) |
