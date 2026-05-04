import { useCallback, useEffect, useRef, useState } from 'react'

import api, { transcriptStream } from '@/api/client'
import { useAppStore } from '@/stores/app'
import useSystemInfo from '@/hooks/useSystemInfo'
import FlipCardTimer from '@/components/FlipCardTimer'
import RecIndicator from '@/components/RecIndicator'
import AudioLevelMeter from '@/components/AudioLevelMeter'
import ErrorModal from '@/components/ErrorModal'

// Plan §4.2 + AC-7. Chunk cadence is 10s; MediaRecorder mimeType is fixed.
const CHUNK_MS = 10000
const MIME_TYPE = 'audio/webm;codecs=opus'

// Inline SVG icons — avoids external icon dependency
function MicIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
      <rect x="5" y="1" width="6" height="9" rx="3" fill="currentColor" />
      <path d="M2.5 8C2.5 11.0376 5.13401 13.5 8 13.5C10.866 13.5 13.5 11.0376 13.5 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      <line x1="8" y1="13.5" x2="8" y2="15" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  )
}

function StopIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
      <rect x="3" y="3" width="10" height="10" rx="2" fill="currentColor" />
    </svg>
  )
}

// Inject CSS keyframe for spinner animation (idempotent — only injected once)
if (typeof document !== 'undefined' && !document.getElementById('bada-spin-keyframe')) {
  const style = document.createElement('style')
  style.id = 'bada-spin-keyframe'
  style.textContent = '@keyframes bada-spin { to { transform: rotate(360deg); } }'
  document.head.appendChild(style)
}

// Page background and layout styles (raw hex/px — no CSS vars per spec)
const PAGE_STYLE = {
  minHeight: '100vh',
  background: '#f8f9fa',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
}

const CONTAINER_BASE = {
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
}

const IDLE_CONTAINER = {
  ...CONTAINER_BASE,
  gap: '80px',
}

const RECORDING_CONTAINER = {
  ...CONTAINER_BASE,
  gap: '40px',
}

const INNER_SECTION = {
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  gap: '24px',
}

const BTN_START = {
  background: '#dc2626',
  height: '48px',
  padding: '0 18px',
  borderRadius: '6px',
  gap: '8px',
  color: '#ffffff',
  fontFamily: 'Inter, sans-serif',
  fontWeight: 600,
  fontSize: '16px',
  letterSpacing: '-0.16px',
  cursor: 'pointer',
  border: 'none',
  display: 'flex',
  alignItems: 'center',
}

const BTN_STOP = {
  ...BTN_START,
  background: '#dc2626',
}

const BTN_DOWNLOAD = {
  height: '48px',
  padding: '0 18px',
  borderRadius: '6px',
  color: '#171717',
  fontFamily: 'Inter, sans-serif',
  fontWeight: 600,
  fontSize: '16px',
  letterSpacing: '-0.16px',
  cursor: 'pointer',
  border: '1.5px solid #e4e4e7',
  background: '#f4f4f5',
  display: 'flex',
  alignItems: 'center',
  gap: '8px',
}

function CheckIcon({ color }) {
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
      <path d="M5 13l4 4L19 7" stroke={color} strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function TranscribingView() {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '24px' }}>
      <div style={{
        width: 40, height: 40, borderRadius: '50%',
        border: '3px solid #fecaca', borderTopColor: '#dc2626',
        animation: 'bada-spin 1s linear infinite',
      }} />
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '8px' }}>
        <span style={{ fontFamily: 'Inter, sans-serif', fontWeight: 600, fontSize: '18px', color: '#171717' }}>
          전사하는 중이에요
        </span>
        <span style={{ fontFamily: 'Inter, sans-serif', fontWeight: 400, fontSize: '14px', color: '#71717a' }}>
          잠시만 기다려 주세요
        </span>
      </div>
    </div>
  )
}

function DoneView({ noteId, onDownload, onGoHome }) {
  const canDownload = noteId !== null
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '24px' }}>
      <div style={{
        width: 64, height: 64, borderRadius: '50%',
        background: '#dcfce7', display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}>
        <CheckIcon color="#16a34a" />
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '8px' }}>
        <span style={{ fontFamily: 'Inter, sans-serif', fontWeight: 600, fontSize: '18px', color: '#171717' }}>
          전사가 완료됐어요
        </span>
        <span style={{ fontFamily: 'Inter, sans-serif', fontWeight: 400, fontSize: '14px', color: '#71717a' }}>
          파일이 자동으로 저장되었습니다
        </span>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '12px' }}>
        <button
          style={{
            ...BTN_DOWNLOAD,
            ...(canDownload ? {} : { opacity: 0.4, cursor: 'not-allowed' }),
          }}
          onClick={canDownload ? onDownload : undefined}
          disabled={!canDownload}
          type="button"
        >
          ↓ 파일로 내려받기
        </button>
        <button
          style={{
            background: 'none', border: 'none', color: '#71717a',
            fontSize: '14px', cursor: 'pointer', fontFamily: 'Inter, sans-serif',
            textDecoration: 'underline',
          }}
          onClick={onGoHome}
          type="button"
        >
          ← 시작 화면으로
        </button>
      </div>
    </div>
  )
}

export default function Recording() {
  const { data: sysInfo } = useSystemInfo()
  const setRecording = useAppStore((s) => s.setRecording)
  const resetRecording = useAppStore((s) => s.resetRecording)

  // UI state
  const [recordingState, setRecordingState] = useState('idle') // 'idle' | 'recording' | 'transcribing' | 'done'
  const [liveTranscriptionFailed, setLiveTranscriptionFailed] = useState(false)
  const [elapsedMs, setElapsedMs] = useState(0)
  const [stream, setStream] = useState(null)
  const [noteId, setNoteId] = useState(null)

  // Error modal state
  const [errorModal, setErrorModal] = useState({ open: false, errorType: null })

  // Refs for async callbacks / lifecycle
  const mediaRecorderRef = useRef(null)
  const streamRef = useRef(null)
  const seqRef = useRef(0)
  const sessionIdRef = useRef(null)
  const startedAtRef = useRef(null)
  const noteIdRef = useRef(null)
  const timerRef = useRef(null)
  const pendingUploadsRef = useRef(new Set())
  const stopInFlightRef = useRef(false)
  const esRef = useRef(null)

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      clearInterval(timerRef.current)
      timerRef.current = null
    }
  }, [])

  const stopTracks = useCallback(() => {
    const s = streamRef.current
    if (s) s.getTracks().forEach((t) => t.stop())
    streamRef.current = null
    setStream(null)
  }, [])

  const trackPendingUpload = useCallback((promise) => {
    pendingUploadsRef.current.add(promise)
    promise.finally(() => pendingUploadsRef.current.delete(promise))
    return promise
  }, [])

  const waitForPendingUploads = useCallback(async () => {
    const pending = Array.from(pendingUploadsRef.current)
    if (pending.length === 0) return
    await Promise.allSettled(pending)
  }, [])

  const uploadChunk = useCallback(async (sessionId, blob, seq) => {
    try {
      const res = await api.postRecordingChunk(sessionId, blob, seq)
      if (res?.noteId) {
        noteIdRef.current = res.noteId
        setNoteId(res.noteId)
      }
    } catch (err) {
      if (err?.status === 409) {
        // Duplicate seq — backend already has it. Skip silently.
        return
      }
      // Non-critical upload failure — log and continue; don't interrupt recording
      console.warn('chunk upload failed', seq, err?.message)
    }
  }, [])

  const openErrorModal = useCallback((errorType) => {
    setErrorModal({ open: true, errorType })
  }, [])

  const closeErrorModal = useCallback(() => {
    setErrorModal({ open: false, errorType: null })
  }, [])

  const handleStart = useCallback(async () => {
    if (recordingState !== 'idle') return
    stopInFlightRef.current = false

    // GROQ_API_KEY guard — check before acquiring mic
    if (sysInfo?.groqConfigured === false) {
      openErrorModal('api_key_missing')
      return
    }

    // Acquire mic
    let micStream
    try {
      micStream = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch (err) {
      const name = err?.name ?? ''
      let description = '마이크 권한이 거부되었습니다'
      if (!navigator.mediaDevices) {
        description = 'HTTPS 또는 localhost 환경에서만 마이크를 사용할 수 있습니다'
      } else if (name === 'NotFoundError' || name === 'DevicesNotFoundError') {
        description = '마이크 장치를 찾을 수 없습니다'
      } else if (name === 'NotAllowedError' || name === 'PermissionDeniedError') {
        description = '마이크 권한이 거부되었습니다. 브라우저 주소창에서 허용하세요'
      } else {
        description = `마이크 오류 (${name || 'Unknown'}): ${err?.message}`
      }
      alert(description)
      return
    }

    // Create recording session
    let session
    try {
      session = await api.createRecording({})
    } catch (err) {
      micStream.getTracks().forEach((t) => t.stop())
      // Check if server returned groq_api_key_missing
      if (err?.status === 503 && err?.body?.error === 'groq_api_key_missing') {
        openErrorModal('api_key_missing')
      } else {
        alert(`녹음 세션 생성 실패: ${err?.message ?? ''}`)
      }
      return
    }

    // Create MediaRecorder
    let mr
    try {
      mr = new MediaRecorder(micStream, { mimeType: MIME_TYPE })
    } catch (err) {
      micStream.getTracks().forEach((t) => t.stop())
      alert('이 브라우저는 webm/opus 녹음을 지원하지 않습니다')
      return
    }

    seqRef.current = 0
    sessionIdRef.current = session.id
    noteIdRef.current = null
    mediaRecorderRef.current = mr
    streamRef.current = micStream
    pendingUploadsRef.current.clear()
    setStream(micStream)
    setNoteId(null)
    setLiveTranscriptionFailed(false)

    mr.ondataavailable = (ev) => {
      if (!ev.data || ev.data.size === 0) return
      const seq = seqRef.current
      seqRef.current = seq + 1
      trackPendingUpload(uploadChunk(session.id, ev.data, seq))
    }

    mr.onerror = (ev) => {
      console.warn('MediaRecorder error', ev?.error?.message)
    }

    const startedAt = Date.now()
    startedAtRef.current = startedAt
    setElapsedMs(0)
    clearTimer()
    timerRef.current = setInterval(() => {
      setElapsedMs(Date.now() - startedAt)
    }, 1000)

    setRecordingState('recording')
    setRecording({ sessionId: session.id, startedAt, elapsedSec: 0, status: 'recording', error: null })

    mr.start(CHUNK_MS)

    // SSE: subscribe to transcript stream to listen for groq_error events.
    // We do NOT render live transcript text (RealtimeTranscript unmounted per spec C-UI).
    esRef.current = transcriptStream(session.id, {
      onGroqError: (data) => {
        const errorType = data?.errorType  // camelCase — F2 fix (was data?.error_type)
        if (errorType === 'network_failed_max_retries') {
          setLiveTranscriptionFailed(true)
          if (esRef.current) { esRef.current(); esRef.current = null }
          openErrorModal('network_failed_max_retries')
          return
        }
        // Map other error types to modal
        const mapped =
          errorType === 'rate_limit' ? 'rate_limit'
          : errorType === 'server_error' ? 'server_error'
          : errorType === 'api_key_missing' ? 'api_key_missing'
          : errorType === 'client_error' ? 'client_error'
          : errorType === 'concat_error' ? 'concat_error'
          : 'unexpected_error'
        openErrorModal(mapped)
      },
      onEnd: () => {
        if (esRef.current) { esRef.current(); esRef.current = null }
      },
    })
  }, [recordingState, sysInfo, uploadChunk, trackPendingUpload, clearTimer, setRecording, openErrorModal])

  const handleStop = useCallback(async () => {
    if (stopInFlightRef.current) return
    const mr = mediaRecorderRef.current
    const sessionId = sessionIdRef.current
    const startedAt = startedAtRef.current
    if (!mr || !sessionId || startedAt === null) return
    stopInFlightRef.current = true

    clearTimer()
    setElapsedMs(0)

    // Close SSE stream (may already be null if liveTranscriptionFailed)
    if (esRef.current) { esRef.current(); esRef.current = null }

    // Flush final MediaRecorder chunk
    const stopped = new Promise((resolve) => {
      const prev = mr.ondataavailable
      mr.ondataavailable = (ev) => { if (prev) prev(ev) }
      mr.onstop = () => resolve()
    })
    if (mr.state !== 'inactive') {
      try { mr.stop() } catch { /* ignore */ }
    }
    await stopped
    await waitForPendingUploads()

    stopTracks()
    mediaRecorderRef.current = null

    const durationSec = (Date.now() - startedAt) / 1000

    if (durationSec < 1) {
      alert('녹음 길이가 1초 미만입니다')
      resetRecording()
      sessionIdRef.current = null
      startedAtRef.current = null
      pendingUploadsRef.current.clear()
      stopInFlightRef.current = false
      setRecordingState('idle')
      return
    }

    const _resetState = () => {
      resetRecording()
      sessionIdRef.current = null
      startedAtRef.current = null
      pendingUploadsRef.current.clear()
      stopInFlightRef.current = false
    }
    const _captureNoteId = (evt) => {
      const nid = evt?.payload?.noteId ?? noteIdRef.current
      if (nid) {
        noteIdRef.current = nid
        setNoteId(nid)
      }
    }
    const _transitionTo = (next) => {
      _resetState()
      setRecordingState(next)
    }

    // Live transcription failed branch: skip transcription, go straight to idle.
    // All three SSE outcomes (complete/error/transportError) collapse to the
    // same idle transition because no transcript is expected on this path.
    if (liveTranscriptionFailed) {
      setLiveTranscriptionFailed(false)
      const _toIdle = () => _transitionTo('idle')
      api.finalizeRecording(
        sessionId,
        { durationSec, skipTranscribe: true },
        { complete: _toIdle, error: _toIdle, transportError: _toIdle },
      )
      return
    }

    // Normal finalize path: show transcribing screen, then done.
    //
    // The disposer returned by api.finalizeRecording is intentionally NOT
    // captured. The server treats finalize as detached from the client's
    // connection lifetime — the .md file is written even if the user closes
    // the tab mid-transcribe (see app/server.py finalize producer, which only
    // logs disconnects). Aborting the in-flight POST here would cancel that
    // work, so we let the request run to completion in the background.
    setRecordingState('transcribing')
    api.finalizeRecording(
      sessionId,
      { durationSec },
      {
        complete: (evt) => {
          _captureNoteId(evt)
          _transitionTo('done')
        },
        error: (evt) => {
          // Partial .md may exist — show done screen with error modal
          _captureNoteId(evt)
          openErrorModal('finalize_partial')
          _transitionTo('done')
        },
        transportError: () => {
          openErrorModal('transport_error')
          _transitionTo('idle')
        },
      },
    )
  }, [liveTranscriptionFailed, clearTimer, stopTracks, resetRecording, waitForPendingUploads, openErrorModal])

  const handleStopFromModal = useCallback(() => {
    closeErrorModal()
    handleStop()
  }, [closeErrorModal, handleStop])

  const handleDownload = useCallback(() => {
    const nid = noteIdRef.current ?? noteId
    if (!nid) return
    api.downloadNote(nid)
  }, [noteId])

  // beforeunload: warn user and try to flush a final chunk
  useEffect(() => {
    const handler = (ev) => {
      if (recordingState !== 'recording') return
      const mr = mediaRecorderRef.current
      if (mr && mr.state === 'recording') {
        try { mr.requestData() } catch { /* ignore */ }
      }
      ev.preventDefault()
      ev.returnValue = ''
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [recordingState])

  // Unmount cleanup
  useEffect(() => {
    return () => {
      clearTimer()
      const mr = mediaRecorderRef.current
      if (mr && mr.state !== 'inactive') {
        try { mr.stop() } catch { /* ignore */ }
      }
      const s = streamRef.current
      if (s) s.getTracks().forEach((t) => t.stop())
      pendingUploadsRef.current.clear()
      stopInFlightRef.current = false
      if (esRef.current) { esRef.current(); esRef.current = null }
    }
  }, [clearTimer])

  return (
    <div style={PAGE_STYLE}>
      {/* Idle state — Figma node 465:7092 */}
      {recordingState === 'idle' && (
        <div style={IDLE_CONTAINER}>
          <FlipCardTimer elapsedMs={0} />
          <button
            style={BTN_START}
            onClick={handleStart}
            type="button"
          >
            <MicIcon />
            녹음 시작
          </button>
        </div>
      )}

      {/* Recording state — Figma node 465:7219 / 60min+ 468:7315 */}
      {recordingState === 'recording' && (
        <div style={RECORDING_CONTAINER}>
          <div style={INNER_SECTION}>
            <RecIndicator pulsing />
            <FlipCardTimer elapsedMs={elapsedMs} />
          </div>
          <AudioLevelMeter audioStream={stream} />
          <button
            style={BTN_STOP}
            onClick={handleStop}
            type="button"
          >
            <StopIcon />
            정지
          </button>
        </div>
      )}

      {/* Transcribing state */}
      {recordingState === 'transcribing' && <TranscribingView />}

      {/* Done state */}
      {recordingState === 'done' && (
        <DoneView
          noteId={noteId}
          onDownload={handleDownload}
          onGoHome={() => {
            setRecordingState('idle')
            setNoteId(null)
            setLiveTranscriptionFailed(false)
          }}
        />
      )}

      <ErrorModal
        open={errorModal.open}
        errorType={errorModal.errorType}
        onClose={closeErrorModal}
        onStopRecording={handleStopFromModal}
      />
    </div>
  )
}
