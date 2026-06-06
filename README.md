# Lonta

A local web app for speech transcription powered by the groq Whisper API.

## Quick start

```bash
make setup                       # Install dependencies
cp .env.example .env             # Set your GROQ_API_KEY
make start                       # Run the server and open the browser
```

Get an API key at [console.groq.com](https://console.groq.com).

## How recording and transcription work

```
Recording  ─  audio is uploaded to server temp storage in 10-second chunks
Stop       ─  audio file is finalized → re-encoded → transcribed via groq → saved
Done       ─  transcript is ready to download
```

No API calls happen while recording. Transcription runs once, after you press stop.

On stop, the recording is re-encoded to 16 kHz mono (about 11 MB per hour) before
upload. If the re-encoded file exceeds 20 MB — roughly two hours of audio — it is
split at silence boundaries, transcribed piece by piece, and merged back with
adjusted timestamps. The output format is the same either way.

### Files

| Output | Location | Notes |
|---|---|---|
| Audio original (`.webm`) | `~/.lonta/data/audio/` | Kept whether transcription succeeds or fails |
| Transcript (`.md`) | `~/.lonta/data/transcripts/` | Saved automatically on stop |
| Downloaded `.md` | Browser download | Per-sentence timestamps. Filename: `date-hhmmss-title.md` |

### Processing time

| Recording length | Expected wait |
|---|---|
| Up to 5 min | A few seconds |
| 30 min | Around 30 seconds |
| 1.5 h | Around 1 minute |
| Over 2 h | A few minutes (split path) |

The transcribing screen shows a spinner without a progress bar. Each processing
stage has an internal 30-minute limit; past that, the run is treated as failed.

### Errors and edge cases

| Situation | Behavior |
|---|---|
| Recording under 1 second | Rejected with a notice |
| Some segments fail to transcribe | Successful parts are saved; failed ranges are marked `[전사 실패 구간]` in the file, and the done screen shows a notice |
| Transcription fails entirely (network, API quota) | A failure modal appears. The audio original is preserved, so nothing is lost |
| Browser tab closed during transcription | The server finishes and saves the transcript. Only the done screen is missed |
| Server shut down during transcription | Shutdown waits up to 120 seconds for the run. If it is still cut off, the note is marked failed on next startup |
| Browser closed while recording | The tab warns before closing. A recording that never reached stop is not recovered |
| Silent recording | An empty transcript is the expected result |

### Limits

- One recording session at a time.
- Audio is sent to the groq API for transcription. `GROQ_API_KEY` is required.
- Sized for the groq free tier: 25 MB per request (handled by splitting), plus an
  hourly transcription quota. Quota errors surface as failures — retry later.

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
