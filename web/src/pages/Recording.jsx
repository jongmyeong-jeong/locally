import { useCallback, useEffect, useRef, useState } from 'react'
import { useBlocker, useNavigate } from 'react-router-dom'

import api from '@/api/client'
import { useAppStore } from '@/stores/app'
import { useToast } from '@/hooks/use-toast'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import RecordingWaveform from '@/components/RecordingWaveform'
import RecordingGuardModal from '@/components/RecordingGuardModal'

// Plan §4.2 + AC-7. Chunk cadence is 10s; MediaRecorder mimeType is fixed.
const CHUNK_MS = 10000
const MIME_TYPE = 'audio/webm;codecs=opus'

function formatHMS(totalSec) {
  const s = Math.max(0, Math.floor(totalSec))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = s % 60
  const pad = (n) => String(n).padStart(2, '0')
  return `${pad(h)}:${pad(m)}:${pad(sec)}`
}

export default function Recording() {
  const navigate = useNavigate()
  const { toast } = useToast()
  const recording = useAppStore((s) => s.recording)
  const setRecording = useAppStore((s) => s.setRecording)
  const resetRecording = useAppStore((s) => s.resetRecording)

  const [title, setTitle] = useState('')
  const [stream, setStream] = useState(null)
  const [elapsedSec, setElapsedSec] = useState(0)
  const [leaving, setLeaving] = useState(false)

  // Refs hold non-React lifecycle state. `seqRef` is monotonically ascending.
  const mediaRecorderRef = useRef(null)
  const streamRef = useRef(null)
  const seqRef = useRef(0)
  const sessionIdRef = useRef(null)
  const startedAtRef = useRef(null)
  const documentIdRef = useRef(null)
  const timerRef = useRef(null)
  const statusRef = useRef('idle')
  const pendingUploadsRef = useRef(new Set())
  const stopInFlightRef = useRef(false)

  // Keep a ref mirror of status so async callbacks (ondataavailable, beforeunload)
  // see the latest value without stale closures.
  useEffect(() => {
    statusRef.current = recording.status
  }, [recording.status])

  // AC2/AC3: 녹음/마이크 요청/finalize 중 모든 내부 navigation을 인터셉트.
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      currentLocation.pathname !== nextLocation.pathname &&
      ['requestingMic', 'recording', 'finalizing'].includes(recording.status),
  )

  const stopTracks = useCallback(() => {
    const s = streamRef.current
    if (s) {
      s.getTracks().forEach((t) => t.stop())
    }
    streamRef.current = null
    setStream(null)
  }, [])

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      clearInterval(timerRef.current)
      timerRef.current = null
    }
  }, [])

  const trackPendingUpload = useCallback((promise) => {
    pendingUploadsRef.current.add(promise)
    promise.finally(() => {
      pendingUploadsRef.current.delete(promise)
    })
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
      if (res?.documentId) documentIdRef.current = res.documentId
    } catch (err) {
      if (err?.status === 409) {
        // Duplicate seq — backend already has it. Log and skip.
        console.warn('duplicate chunk seq', seq)
        return
      }
      toast({
        description: `청크 업로드 실패 (seq=${seq}): ${err?.message ?? '알 수 없는 오류'}`,
        variant: 'destructive',
      })
    }
  }, [toast])

  const handleStart = useCallback(async () => {
    if (statusRef.current !== 'idle' && statusRef.current !== 'error') return
    stopInFlightRef.current = false

    setRecording({ status: 'requestingMic', error: null })
    let micStream
    try {
      micStream = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch (err) {
      console.error('getUserMedia error:', err.name, err.message)
      const name = err?.name ?? ''
      let description = '마이크 권한이 거부되었습니다'
      if (!navigator.mediaDevices) {
        description = 'HTTPS 또는 localhost 환경에서만 마이크를 사용할 수 있습니다'
      } else if (name === 'NotFoundError' || name === 'DevicesNotFoundError') {
        description = '마이크 장치를 찾을 수 없습니다. 시스템 설정 > 사운드 > 입력에서 입력 장치를 확인하세요'
      } else if (name === 'NotReadableError' || name === 'TrackStartError') {
        description = '마이크에 접근할 수 없습니다. 다른 앱이 사용 중이거나 장치 오류입니다'
      } else if (name === 'NotAllowedError' || name === 'PermissionDeniedError') {
        description = '마이크 권한이 거부되었습니다. 브라우저 주소창에서 마이크 권한을 허용하세요'
      } else if (name === 'OverconstrainedError') {
        description = '마이크 장치가 요구 조건을 충족하지 않습니다'
      } else {
        description = `마이크 오류 (${name || 'Unknown'}): ${err?.message}`
      }
      setRecording({ status: 'error', error: description })
      toast({ description, variant: 'destructive' })
      return
    }

    let session
    try {
      session = await api.createRecording(title ? { title } : {})
    } catch (err) {
      micStream.getTracks().forEach((t) => t.stop())
      setRecording({ status: 'error', error: err?.message ?? '세션 생성 실패' })
      toast({
        description: `녹음 세션 생성 실패: ${err?.message ?? ''}`,
        variant: 'destructive',
      })
      return
    }

    let mr
    try {
      mr = new MediaRecorder(micStream, { mimeType: MIME_TYPE })
    } catch (err) {
      micStream.getTracks().forEach((t) => t.stop())
      setRecording({ status: 'error', error: err?.message ?? 'MediaRecorder 생성 실패' })
      toast({
        description: '이 브라우저는 webm/opus 녹음을 지원하지 않습니다',
        variant: 'destructive',
      })
      return
    }

    seqRef.current = 0
    sessionIdRef.current = session.id
    mediaRecorderRef.current = mr
    streamRef.current = micStream
    pendingUploadsRef.current.clear()
    setStream(micStream)

    mr.ondataavailable = (ev) => {
      if (!ev.data || ev.data.size === 0) return
      const seq = seqRef.current
      seqRef.current = seq + 1
      trackPendingUpload(uploadChunk(session.id, ev.data, seq))
    }

    mr.onerror = (ev) => {
      toast({
        description: `녹음 오류: ${ev?.error?.message ?? '알 수 없는 오류'}`,
        variant: 'destructive',
      })
    }

    const startedAt = Date.now()
    startedAtRef.current = startedAt
    setElapsedSec(0)
    clearTimer()
    timerRef.current = setInterval(() => {
      const sec = Math.floor((Date.now() - startedAt) / 1000)
      setElapsedSec(sec)
    }, 1000)

    setRecording({
      sessionId: session.id,
      startedAt,
      elapsedSec: 0,
      status: 'recording',
      error: null,
    })

    mr.start(CHUNK_MS)
  }, [title, toast, setRecording, uploadChunk, clearTimer, trackPendingUpload])

  const handleStop = useCallback(async () => {
    if (stopInFlightRef.current) return
    const mr = mediaRecorderRef.current
    const sessionId = sessionIdRef.current
    const startedAt = startedAtRef.current
    if (!mr || !sessionId || startedAt === null) return
    stopInFlightRef.current = true

    clearTimer()

    // Wait for MediaRecorder to flush remaining data via onstop.
    const stopped = new Promise((resolve) => {
      const prev = mr.ondataavailable
      // Collect any final chunk before stop fires.
      mr.ondataavailable = (ev) => {
        if (prev) prev(ev)
      }
      mr.onstop = () => resolve()
    })
    if (mr.state !== 'inactive') {
      try {
        mr.stop()
      } catch {
        // ignore — state may already be inactive
      }
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
      documentIdRef.current = null
      pendingUploadsRef.current.clear()
      stopInFlightRef.current = false
      setElapsedSec(0)
      return
    }

    setRecording({ status: 'finalizing' })

    const cleanupRefs = () => {
      resetRecording()
      sessionIdRef.current = null
      startedAtRef.current = null
      documentIdRef.current = null
      pendingUploadsRef.current.clear()
      stopInFlightRef.current = false
      setElapsedSec(0)
    }

    api.finalizeRecording(
      sessionId,
      {
        ...(title ? { title } : {}),
        durationSec,
      },
      {
        complete: (evt) => {
          cleanupRefs()
          navigate(`/documents/${evt.payload.documentId}`)
        },
        error: (evt) => {
          const errVal = evt.payload?.error
          if (errVal === 'missing chunks') {
            toast({
              description: `누락된 청크가 있어 저장에 실패했습니다 (${(evt.payload.missing ?? []).length}개)`,
              variant: 'destructive',
            })
          } else if (errVal === 'recording too short') {
            alert('녹음 길이가 1초 미만입니다')
          } else {
            toast({
              description: `녹음 저장 실패: ${errVal ?? '알 수 없는 오류'}`,
              variant: 'destructive',
            })
          }
          stopInFlightRef.current = false
          setRecording({ status: 'error', error: errVal ?? '저장 실패' })
        },
        transportError: (err) => {
          const body = err?.body
          if (err?.status === 400 && body?.error === 'recording too short') {
            alert('녹음 길이가 1초 미만입니다')
          } else if (err?.status === 400 && body?.error === 'missing chunks') {
            toast({
              description: `누락된 청크가 있어 저장에 실패했습니다 (${(body.missing ?? []).length}개)`,
              variant: 'destructive',
            })
          } else {
            toast({
              description: `녹음 저장 실패: ${err?.message ?? ''}`,
              variant: 'destructive',
            })
          }
          stopInFlightRef.current = false
          setRecording({ status: 'error', error: err?.message ?? '저장 실패' })
        },
      },
    )
  }, [title, toast, clearTimer, stopTracks, resetRecording, setRecording, navigate, waitForPendingUploads])

  // beforeunload: if actively recording, ask `ondataavailable` to flush a final
  // chunk synchronously (best-effort; browser may still terminate) and show a
  // confirm prompt so the user has a chance to cancel navigation.
  useEffect(() => {
    const handler = (ev) => {
      if (statusRef.current !== 'recording') return
      const mr = mediaRecorderRef.current
      if (mr && mr.state === 'recording') {
        try {
          mr.requestData()
        } catch {
          // ignore
        }
      }
      ev.preventDefault()
      ev.returnValue = ''
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [])

  // Unmount cleanup: release mic tracks and stop timers. The RecordingWaveform
  // component handles its own AudioContext teardown via its useEffect cleanup.
  useEffect(() => {
    return () => {
      clearTimer()
      const mr = mediaRecorderRef.current
      if (mr && mr.state !== 'inactive') {
        try {
          mr.stop()
        } catch {
          // ignore
        }
      }
      const s = streamRef.current
      if (s) s.getTracks().forEach((t) => t.stop())
      pendingUploadsRef.current.clear()
      stopInFlightRef.current = false
      documentIdRef.current = null
    }
  }, [clearTimer])

  // AC2/AC3/AC5: 이탈 확정 핸들러 — in-flight 청크 대기 → DELETE → 정리 → 이동.
  const handleConfirmLeave = useCallback(async () => {
    if (leaving) return
    if (!blocker || blocker.state !== 'blocked') return
    setLeaving(true)
    try {
      // ① in-flight 청크 업로드 완료 대기 (레이스 방지)
      await waitForPendingUploads()

      // ② docId가 있으면 DELETE — 없으면(첫 청크 도달 전) skip
      const docId = documentIdRef.current
      if (docId) {
        try {
          await api.deleteDocument(docId, { deleteAudio: true })
        } catch (err) {
          // best-effort: 네트워크 오류여도 사용자 이탈을 막지 않음
          console.warn('delete failed during leave', err)
        }
      }

      // ③ 마이크 스트림 정리 + Zustand reset + ref 정리
      const mr = mediaRecorderRef.current
      if (mr && mr.state !== 'inactive') {
        try { mr.stop() } catch { /* ignore */ }
      }
      stopTracks()
      clearTimer()
      mediaRecorderRef.current = null
      sessionIdRef.current = null
      startedAtRef.current = null
      documentIdRef.current = null
      pendingUploadsRef.current.clear()
      stopInFlightRef.current = false
      setElapsedSec(0)
      resetRecording()

      // ④ 이동 진행
      blocker.proceed()
    } finally {
      setLeaving(false)
    }
  }, [leaving, blocker, waitForPendingUploads, stopTracks, clearTimer, resetRecording])

  const handleStay = useCallback(() => {
    if (leaving) return
    if (blocker && blocker.state === 'blocked') {
      blocker.reset()
    }
  }, [leaving, blocker])

  // AC2(취소 버튼): requestingMic 상태에서 stream/Zustand 정리 후 navigate.
  const handleCancel = useCallback(() => {
    const mr = mediaRecorderRef.current
    if (mr && mr.state !== 'inactive') {
      try { mr.stop() } catch { /* ignore */ }
    }
    stopTracks()
    clearTimer()
    mediaRecorderRef.current = null
    sessionIdRef.current = null
    startedAtRef.current = null
    documentIdRef.current = null
    pendingUploadsRef.current.clear()
    setElapsedSec(0)
    resetRecording()
    navigate('/documents')
  }, [stopTracks, clearTimer, resetRecording, navigate])

  const isIdle = recording.status === 'idle' || recording.status === 'error'
  const isRecording = recording.status === 'recording'
  const isFinalizing = recording.status === 'finalizing'
  const isRequesting = recording.status === 'requestingMic'

  return (
    <section className="mx-auto max-w-3xl p-6 space-y-4">
      <h1 className="text-2xl font-semibold">녹음</h1>

      <div className="space-y-2">
        <label className="text-sm font-medium">제목 (선택)</label>
        <Input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="제목을 입력하세요"
          disabled={!isIdle}
        />
      </div>

      <Card>
        <CardContent className="space-y-4 p-6">
          <div className="flex items-center justify-between">
            <span className="font-mono text-3xl tabular-nums">
              {formatHMS(elapsedSec)}
            </span>
            <span className="text-sm text-muted-foreground">
              {isRecording && '녹음 중'}
              {isRequesting && '마이크 권한 요청 중...'}
              {isFinalizing && '저장 중...'}
              {isIdle && '대기'}
            </span>
          </div>

          <RecordingWaveform stream={isRecording ? stream : null} />

          <div className="flex gap-2">
            {!isRecording ? (
              <Button
                onClick={handleStart}
                disabled={!isIdle || isRequesting}
              >
                {isRequesting ? '준비 중...' : '녹음 시작'}
              </Button>
            ) : (
              <Button
                variant="destructive"
                onClick={handleStop}
                disabled={isFinalizing}
              >
                정지
              </Button>
            )}
            <Button
              variant="outline"
              onClick={handleCancel}
              disabled={isRecording || isFinalizing}
            >
              취소
            </Button>
          </div>
        </CardContent>
      </Card>

      <RecordingGuardModal
        open={blocker?.state === 'blocked'}
        onLeave={handleConfirmLeave}
        onStay={handleStay}
        busy={leaving}
      />
    </section>
  )
}
