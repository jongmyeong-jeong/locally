# Lonta

A local web app for speech transcription powered by the groq Whisper API.

## Quick start

```bash
make setup                       # Install dependencies
cp .env.example .env             # Set your GROQ_API_KEY
make start                       # Run the server and open the browser
```

Get an API key at [console.groq.com](https://console.groq.com).

## make commands

| Command | What it does |
|---|---|
| `make help` | Show the command list |
| `make setup` | Install dependencies (Python + Web) |
| `make start` | Run the server and auto-open the browser |
| `make smoke` | Verify a real groq API call |

## Environment variables

| Variable | Required | Default |
|---|---|---|
| `GROQ_API_KEY` | ✅ | — |
| `TRANSCRIPTION_LANG` | | `ko` |
