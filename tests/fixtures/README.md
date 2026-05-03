# Test Fixtures

Audio fixtures used by the Groq transcription test suite.

## Files

| File | Duration | Purpose |
|------|----------|---------|
| `test_30s.mp3` | 30 s | Primary unit test fixture — exercises single-chunk transcription path (< 60 s, no batching) |
| `test_90s.mp3` | 90 s | Batching test fixture — exercises the multi-chunk split path (> 60 s) |
| `test_silence_5s.mp3` | 5 s | Silence detection — verifies VAD / empty-response handling |
| `test_noise_5s.mp3` | 5 s | White-noise input — verifies robustness against non-speech audio |

## Synthetic audio note

All files are **synthetic** (generated with `ffmpeg` sine/noise sources at 16 kHz mono, 16 kbps).
Groq API calls are mocked in tests, so audio content does not affect unit test results.

For **real Korean audio** used in F1 manual smoke testing, replace `test_30s.mp3` with an
actual Korean speech recording before running end-to-end tests. The file must be a valid MP3,
WAV, or M4A accepted by the Groq Whisper API.

## Regeneration

```bash
ffmpeg -f lavfi -i "sine=frequency=440:duration=30"  -ar 16000 -ac 1 -b:a 16k test_30s.mp3 -y
ffmpeg -f lavfi -i "sine=frequency=440:duration=90"  -ar 16000 -ac 1 -b:a 16k test_90s.mp3 -y
ffmpeg -f lavfi -i "anullsrc=r=16000:cl=mono" -t 5   -ar 16000 -ac 1 -b:a 64k test_silence_5s.mp3 -y
ffmpeg -f lavfi -i "anoisesrc=color=white:duration=5" -ar 16000 -ac 1 -b:a 64k test_noise_5s.mp3 -y
```
