// useSSE.js — finalize SSE 재구독 / 폴링 훅
//
// 사용 시나리오:
//   Recording.jsx에서 finalize SSE 도중 탭이 닫히거나 페이지가 새로고침되면
//   노트 상태가 'finalizing'으로 남을 수 있다.
//   같은 노트 URL로 복귀하면 Summary.jsx가 이 훅을 통해
//   상태가 'transcribed' 또는 'transcription_failed'로 바뀔 때까지 폴링한다.

import { useCallback, useEffect, useRef } from 'react'

const POLL_INTERVAL_MS = 3000
const MAX_POLLS = 100 // 최대 5분 (3s × 100)

/**
 * useFinalizePoller — 노트 상태가 'finalizing'일 때 완료를 기다리는 폴링 훅.
 *
 * @param {object} options
 * @param {boolean} options.enabled       - true일 때만 폴링 시작
 * @param {() => Promise<{status: string}>} options.fetchNote - 노트를 다시 가져오는 함수
 * @param {(status: string) => void} options.onSettled - 'transcribed'|'transcription_failed'에 도달하면 호출
 * @param {number} [options.intervalMs]   - 폴링 간격 (기본 3000ms, 테스트에서 오버라이드 가능)
 * @param {number} [options.maxPolls]     - 최대 폴링 횟수 (기본 100)
 */
export function useFinalizePoller({
  enabled,
  fetchNote,
  onSettled,
  onTimeout,
  onError,
  intervalMs = POLL_INTERVAL_MS,
  maxPolls = MAX_POLLS,
}) {
  const activeRef = useRef(false)
  const pollCountRef = useRef(0)
  const timerRef = useRef(null)
  const onSettledRef = useRef(onSettled)
  const onTimeoutRef = useRef(onTimeout)
  const onErrorRef = useRef(onError)

  // onSettled 최신 참조 유지 (stale closure 방지)
  useEffect(() => {
    onSettledRef.current = onSettled
  }, [onSettled])

  useEffect(() => {
    onTimeoutRef.current = onTimeout
  }, [onTimeout])

  useEffect(() => {
    onErrorRef.current = onError
  }, [onError])

  const stop = useCallback(() => {
    activeRef.current = false
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
  }, [])

  useEffect(() => {
    if (!enabled) {
      stop()
      return
    }

    // 이미 폴링 중이면 재시작하지 않음 (중복 호출 방어)
    if (activeRef.current) return

    activeRef.current = true
    pollCountRef.current = 0

    const poll = async () => {
      if (!activeRef.current) return

      if (pollCountRef.current >= maxPolls) {
        // 타임아웃 — 폴링 중단 (서버 측에서 stuck된 경우)
        stop()
        onTimeoutRef.current?.()
        return
      }

      pollCountRef.current += 1

      let note
      try {
        note = await fetchNote()
      } catch (error) {
        // 네트워크 오류 — 재시도 예정
        if (!activeRef.current) return
        const status = error?.status
        if (status >= 400 && status < 500 && status !== 408 && status !== 429) {
          stop()
          onErrorRef.current?.(error)
          return
        }
        timerRef.current = setTimeout(poll, intervalMs)
        return
      }

      if (!activeRef.current) return

      const { status } = note
      if (status === 'transcribed' || status === 'transcription_failed') {
        stop()
        onSettledRef.current(note)
        return
      }

      // 아직 finalizing 중 — 다음 폴링 예약
      timerRef.current = setTimeout(poll, intervalMs)
    }

    // 첫 폴링 즉시 시작
    poll()

    return stop
  }, [enabled, fetchNote, intervalMs, maxPolls, stop])
}
