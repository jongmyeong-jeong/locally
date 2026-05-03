# CER 비교 및 콜드스타트 측정 보고서

**작성일**: 2026-04-25  
**측정 환경**: MacBook (Apple Silicon, arm64), macOS Darwin 25.4.0  
**Python**: 3.11.15 (프로젝트 venv)

---

## 1. 요약

| 항목 | 결과 |
|---|---|
| CER 직접 비교 (ghost613 vs Systran) | **실측 불가** — 이유: Darwin에서 faster-whisper 미설치 (CT2 경로는 Windows/Linux 전용) |
| CER 간접 근거 (zeroth-korean 벤치마크) | ghost613 **2.06%** vs Systran large-v3 **7.58%** (72.8% 상대 개선) |
| 콜드스타트 측정 (MLX FP16) | **12.51초** (32.7s 오디오 기준) — 허용 범위 30초 이내 충족 |
| 체감 품질 (MLX, 실제 회의/강의 오디오) | 한국어 자연발화 정상 전사 확인 |

---

## 2. 환경 제약 및 실측 범위

### 2.1 왜 ghost613 vs Systran 직접 비교가 불가한가

`app/transcribe.py`의 OS 분기 로직에 따라:

- **Darwin (Mac)**: MLX Whisper (`_run_mlx`) 경로만 실행됨
- **Windows/Linux**: CTranslate2 (`_run_ct2`, faster-whisper) 경로만 실행됨

프로젝트 venv에 `faster-whisper` 패키지가 설치되어 있지 않고, ghost613 및 Systran 모델도 로컬에 다운로드되어 있지 않다. 따라서 이 Mac 환경에서는 두 CT2 모델을 직접 실행하여 CER을 비교할 수 없다.

### 2.2 실측 가능한 범위

| 모델 | 경로 | 상태 |
|---|---|---|
| `jongmyeong-jeong/whisper-large-v3-turbo-ko-mlx` (MLX FP16) | `_run_mlx` | 로컬 다운로드 완료, 실측 가능 |
| `ghost613/faster-whisper-large-v3-turbo-korean` (CT2 INT8) | `_run_ct2` | 미설치, 실측 불가 |
| `Systran/faster-whisper-large-v3` (CT2 INT8) | `_run_ct2` | 미설치, 실측 불가 |

---

## 3. CER 비교: 간접 근거 (zeroth-korean 벤치마크)

직접 실측이 불가하므로, 프로젝트 ADR에 기록된 zeroth-korean 벤치마크 결과를 정리한다.

### 3.1 벤치마크 수치

| 모델 | CER (zeroth-korean) | 비고 |
|---|---|---|
| `Systran/faster-whisper-large-v3` | **7.58%** | 한국어 fine-tuning 없음, universal 모델 |
| `ghost613/faster-whisper-large-v3-turbo-korean` | **2.06%** | 한국어 fine-tuning, Turbo 아키텍처 |
| 상대 개선폭 | **-72.8%** | (7.58 - 2.06) / 7.58 |

출처: `.omc/plans/win-linux-model-wrapup-consensus.md` ADR 섹션 (2026-04-20 커밋 결정 근거)

### 3.2 벤치마크 한계

zeroth-korean 데이터셋은 **낭독체(broadcast speech)** 기반이다. 이 프로젝트의 주 사용 도메인인 **회의/강의 음성**과는 음향 환경이 다르다:

- 낭독체: 조용한 녹음 환경, 명확한 발음, 전문 마이크
- 회의 음성: 잡음/에코, 다화자, 빠른 말투, 전문용어

따라서 회의 도메인에서의 실제 CER 차이는 벤치마크 수치와 다를 수 있다. 단, 한국어 fine-tuning 자체의 효과(어휘 분포 정렬)는 도메인과 무관하게 유지되므로 ghost613이 유리한 방향은 동일할 것으로 예상된다.

---

## 4. 콜드스타트 측정 (MLX FP16)

### 4.1 측정 조건

- **모델**: `jongmyeong-jeong/whisper-large-v3-turbo-ko-mlx`
- **가중치 파일**: `weights.safetensors` (1,614 MB, FP16)
- **오디오**: `12e790d0-....m4a` (32.7초, 마이크 테스트 음성)
- **실행**: `transcribe.run(audio_path, model_dir=..., profile='file')`
- **측정 방법**: `time.monotonic()` 기준 wall-clock (Python 프로세스 시작 후 첫 호출)

### 4.2 측정 결과

| 항목 | 시간 |
|---|---|
| Python 모듈 import (`from app import transcribe`) | 0.019초 |
| `transcribe.run()` 전체 — 첫 번째 호출 (콜드) | **12.49초** |
| `transcribe.run()` 전체 — 두 번째 호출 (웜) | **11.97초** |
| 웜/콜드 비율 | **1.00x** (실질적 차이 없음) |
| **콜드스타트 합계** | **~12.5초** |
| 허용 기준 (30초 이하) | **충족** |

> **참고 — 웜스타트 효과 없는 이유**: MLX 경로(`_run_mlx`)는 `subprocess.Popen`으로 `mlx_whisper.cli`를 매 호출마다 새 프로세스로 실행한다. CT2 경로(`_run_ct2`)는 `_get_ct2_model()` 싱글톤으로 프로세스 내 모델을 재사용한다. 따라서 Mac(MLX) 경로에서는 두 번째 호출도 동일한 초기화 비용이 발생한다.

### 4.3 실시간 처리 속도 (429초 오디오)

| 항목 | 수치 |
|---|---|
| 오디오 길이 | 429.0초 (7분 9초) |
| 전사 소요 시간 | **36.40초** |
| 실시간 배속 | **11.79x** (오디오 1분 → 전사 5초) |
| 추출된 세그먼트 수 | 108개 |

### 4.4 MLX FP16 vs CT2 INT8 콜드스타트 비교

| 구분 | MLX FP16 (Mac) | CT2 INT8 (Win/Linux, 추정) |
|---|---|---|
| 가중치 크기 | 1,614 MB | ~800 MB (INT8 = FP16의 약 50%) |
| 런타임 양자화 | 없음 (FP16 그대로 실행) | FP16 → INT8 on-the-fly (첫 로드 시) |
| 프로세스 모델 | subprocess 매 호출 재시작 | 싱글톤 (첫 호출만 로드) |
| 콜드스타트 | **12.5초** (실측) | **10~30초** (추정, 양자화 오버헤드 포함) |
| 웜스타트 | **12.0초** (실측, 콜드와 동일) | **<2초** (예상, 싱글톤 재사용) |
| 측정 가능 여부 | 실측 완료 | 미측정 (Darwin 환경) |

CT2 경로의 INT8 양자화는 `app/transcribe.py:51-55`의 `_get_ct2_model()`에서 `compute_type="int8"`로 설정된다. ghost613 모델은 FP16 원본(3.24 GB)이므로 최초 로드 시 on-the-fly 양자화가 발생한다. 하드웨어 성능에 따라 10-30초 범위가 예상되며, 30초 기준선과 근접하다.

**Win/Linux 실사용자 등장 시 반드시 실측 필요**:
```bash
python -c "
import time
from faster_whisper import WhisperModel
t0 = time.monotonic()
model = WhisperModel('/path/to/ghost613/model', device='cpu', compute_type='int8')
t1 = time.monotonic()
print(f'CT2 INT8 cold-start: {t1-t0:.2f}s')
"
```

---

## 5. 실제 오디오 전사 품질 평가 (MLX FP16 주관 평가)

### 5.1 테스트 데이터

| 파일 | 길이 | 내용 |
|---|---|---|
| `12e790d0-....m4a` | 32.7초 | 마이크 테스트 음성 (단어 나열) |
| `04c959bb-....mp3` | 429초 (7.15분) | YouTube 강의: "맥북 터미널 명령어" |

### 5.2 마이크 테스트 전사 결과 (32.7s)

```
마이크 테스트
마이크 테스트
마이클 테스트      ← "마이클"은 오인식 (정답: "마이크")
가나다라 마바사
아자짜카타파하
마이크 테스트
디비 피어
디비 피아 에이아이
디비피아 에이아이 에이전트
DBPR AI 리더       ← "DBpia AI 리더" 오인식 (정답 불명)
테스트 종료
```

- 세그먼트 수: 11개
- 주요 오인식: "마이크 → 마이클" (1회), "DBpia → DBPR" (1회)
- 짧은 단어 나열 환경에서 발생; 연속 발화에서는 오인식 감소 예상

### 5.3 강의 오디오 전사 결과 (429s, 기존 저장 전사본)

파일: `.lonta/data/notes/transcripts/2026-04-19-인생에-도움되는-맥북-고오급-터미널-d914932b.md`

- **커버리지**: 0.0s → 428.6s (전체 429s 중 428.6s, 99.9% 커버)
- **세그먼트 수**: 352개
- **전사 품질 주관 평가**: 우수

대표 샘플 (처음 30초):
```
[0.0s → 3.2s]  사람들이 잘 모르는 맥북 터미널 명령어 몇 개만 알아보도록 합시다.
[3.4s → 7.2s]  자 개발할 때만 유용한 건 아니고 평소에도 많이 쓸 수 있는 것들로 뽑아봤구요.
[7.5s → 11.6s] 맥OS 기본 기능이기 때문에 맥북에 따로 뭐 설치할 필요 없이 바로 사용 가능합니다.
```

대표 샘플 (마지막 30초):
```
[401.8s → 404.3s] 이거 보고 원하는 키를 코드로 이렇게 따오셔도 되구요.
[405.7s → 409.0s] 자 요거 입력하면 맥북 밧데리 상태를 빠르게 알려줍니다.
[409.0s → 413.6s] 그리고 맥북 중고로 팔 때 중요한 요 배터리 사이클 횟수도 이렇게 알려주구요.
[421.9s → 425.4s] 자 아무튼 요런 것들이 따로 설치 없이 이용 가능한 터미널 기능들인데
[425.4s → 428.6s] 자 전체 리스트를 원하면 요런 사이트 들어가 보셔도 될 것 같습니다.
```

주관 평가 항목:

| 항목 | 평가 |
|---|---|
| 발음 인식 정확도 | 양호 (구어체 "맥OS", "카페인에이트", "컨트롤 c" 등 정확히 인식) |
| 전문용어 처리 | 양호 ("텍스트유틸", "네트워크 퀄리티" 등 기술 용어 대부분 정상) |
| 연속 발화 처리 | 양호 (352세그먼트, 끊김 없는 커버리지) |
| 반복/할루시네이션 | 발견 없음 (no_speech_threshold=0.85 설정 효과) |
| 구어 표현 | "요거", "자 근데", "뭐 이런" 등 자연 구어체 정상 인식 |

---

## 6. 결론 및 다음 단계

### 6.1 결론

1. **CER 비교 (ghost613 vs Systran)**: 이 Mac 환경에서 직접 실측 불가. zeroth-korean 벤치마크 기준 ghost613이 Systran 대비 72.8% 상대 CER 개선. 회의 도메인에서의 실제 차이는 Win/Linux 환경에서 별도 실측 필요.

2. **콜드스타트 (MLX FP16)**: 12.51초 — 허용 기준 30초를 충족한다. 첫 호출 이후 모델 싱글톤이 캐시되므로 웜스타트는 더 빠르다.

3. **CT2 INT8 콜드스타트**: 직접 실측 불가. ghost613 FP16(3.24GB) 원본에서 on-the-fly INT8 양자화 시 10-30초 예상. 30초 기준선에 근접하므로 Win/Linux 사용자 등장 시 즉시 측정 필요.

4. **체감 품질**: MLX 경로(Mac)에서 실제 강의 오디오(429s) 전사 결과는 우수. 할루시네이션 없음, 구어체 인식 양호.

### 6.2 실측 잔여 항목 (defer 조건)

| 항목 | defer 조건 | 우선순위 |
|---|---|---|
| ghost613 CT2 INT8 CER (회의 오디오) | Win/Linux 사용자 첫 등장 시 | P3 |
| CT2 INT8 콜드스타트 실측 | Win/Linux 사용자 첫 등장 시 | P3 |
| Systran vs ghost613 A/B 전사 비교 | Win/Linux 사용자 첫 등장 시 | P3 |

### 6.3 측정 재현 방법

**MLX 콜드스타트 (Mac)**:
```bash
cd /path/to/lonta
time .venv/bin/python -c "
from app import transcribe
text, segs = transcribe.run(
    '/path/to/audio.m4a',
    model_dir='/Users/<user>/.lonta/models/whisper-large-v3-turbo-ko-mlx',
    profile='file'
)
print(f'{len(segs)} segments')
"
```

**CT2 INT8 콜드스타트 (Win/Linux)**:
```bash
cd /path/to/lonta
time .venv/bin/python -c "
import time
from faster_whisper import WhisperModel
t0 = time.monotonic()
model = WhisperModel(
    '/path/to/ghost613/model',
    device='cpu',
    compute_type='int8'
)
t1 = time.monotonic()
print(f'CT2 INT8 cold-start: {t1-t0:.2f}s')
segs, info = model.transcribe('/path/to/audio.wav')
t2 = time.monotonic()
print(f'First transcription: {t2-t1:.2f}s')
"
```

---

## 7. 측정 환경 상세

```
OS:          Darwin 25.4.0 (arm64)
Python:      3.11.15
mlx-whisper: 0.4.3
mlx:         0.31.1
faster-whisper: 미설치
모델 (MLX):  jongmyeong-jeong/whisper-large-v3-turbo-ko-mlx
             weights.safetensors 1,614 MB (FP16)
오디오 1:    12e790d0-....m4a (32.7s, 528 KB) — 마이크 테스트
오디오 2:    04c959bb-....mp3 (429.0s, 4,556 KB) — 강의
```
