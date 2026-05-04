import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'

import { useAppStore } from '@/stores/app'
import Recording from '@/pages/Recording'

const { apiMock } = vi.hoisted(() => ({
  apiMock: {
    createRecording: vi.fn(),
    postRecordingChunk: vi.fn(),
    finalizeRecording: vi.fn(),
  },
}))

const { transcriptStreamMock } = vi.hoisted(() => ({
  transcriptStreamMock: vi.fn(() => () => {}),
}))

vi.mock('@/api/client', () => ({
  default: apiMock,
  transcriptStream: transcriptStreamMock,
}))

vi.mock('@/hooks/useSystemInfo', () => ({
  default: () => ({ data: { groqConfigured: true } }),
  useSystemInfo: () => ({ data: { groqConfigured: true } }),
}))

vi.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: vi.fn() }),
}))

vi.mock('@/components/RecordingWaveform', () => ({
  default: () => <div data-testid="waveform" />,
}))

function resetRecordingStore() {
  useAppStore.setState({
    recording: {
      sessionId: null,
      startedAt: null,
      elapsedSec: 0,
      status: 'idle',
      error: null,
    },
  })
}

describe('Recording', () => {
  let now
  let uploadResolve
  let originalMediaRecorder
  let originalGetUserMedia
  let originalAlert

  beforeEach(() => {
    vi.clearAllMocks()
    resetRecordingStore()

    now = 0
    vi.spyOn(Date, 'now').mockImplementation(() => now)

    apiMock.createRecording.mockResolvedValue({ id: 'session-1' })
    apiMock.postRecordingChunk.mockImplementation(
      () =>
        new Promise((resolve) => {
          uploadResolve = resolve
        }),
    )
    apiMock.finalizeRecording.mockImplementation((_id, _body, handlers) => {
      if (handlers) {
        Promise.resolve().then(() =>
          handlers.complete({ payload: { noteId: 'doc-1' } }),
        )
        return () => {}
      }
      return Promise.resolve({ noteId: 'doc-1' })
    })

    const fakeStream = {
      getTracks: () => [{ stop: vi.fn() }],
    }

    originalGetUserMedia = navigator.mediaDevices?.getUserMedia
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: {
        getUserMedia: vi.fn().mockResolvedValue(fakeStream),
      },
    })

    class FakeMediaRecorder {
      constructor() {
        this.state = 'inactive'
        this.ondataavailable = null
        this.onstop = null
      }

      start() {
        this.state = 'recording'
      }

      stop() {
        this.state = 'inactive'
        setTimeout(() => {
          this.ondataavailable?.({ data: new Blob(['chunk']) })
          this.onstop?.()
        }, 0)
      }

      requestData() {}
    }

    originalMediaRecorder = globalThis.MediaRecorder
    globalThis.MediaRecorder = FakeMediaRecorder

    originalAlert = globalThis.alert
    globalThis.alert = vi.fn()
  })

  afterEach(() => {
    vi.restoreAllMocks()
    resetRecordingStore()

    if (originalMediaRecorder === undefined) {
      delete globalThis.MediaRecorder
    } else {
      globalThis.MediaRecorder = originalMediaRecorder
    }

    if (originalGetUserMedia) {
      Object.defineProperty(navigator, 'mediaDevices', {
        configurable: true,
        value: { getUserMedia: originalGetUserMedia },
      })
    }

    globalThis.alert = originalAlert
  })

  it('waits for pending chunk uploads before finalize', async () => {
    const router = createMemoryRouter([{ path: '/', element: <Recording /> }], {
      initialEntries: ['/'],
    })
    render(<RouterProvider router={router} />)

    fireEvent.click(screen.getByRole('button', { name: '녹음 시작' }))

    await waitFor(() => {
      expect(apiMock.createRecording).toHaveBeenCalledTimes(1)
    })

    now = 1500
    fireEvent.click(await screen.findByRole('button', { name: '정지' }))

    await waitFor(() => {
      expect(apiMock.postRecordingChunk).toHaveBeenCalledTimes(1)
    })

    expect(apiMock.finalizeRecording).not.toHaveBeenCalled()

    uploadResolve()

    await waitFor(() => {
      expect(apiMock.finalizeRecording).toHaveBeenCalledWith(
        'session-1',
        { durationSec: 1.5 },
        expect.objectContaining({ complete: expect.any(Function) }),
      )
    })
  })

  it('attaches beforeunload handler while recording (AC4 regression)', async () => {
    const router = createMemoryRouter([{ path: '/', element: <Recording /> }], {
      initialEntries: ['/'],
    })
    render(<RouterProvider router={router} />)

    fireEvent.click(screen.getByRole('button', { name: '녹음 시작' }))
    await waitFor(() => {
      expect(apiMock.createRecording).toHaveBeenCalledTimes(1)
    })
    await screen.findByRole('button', { name: '정지' })

    // beforeunload event should be cancelled while recording is active.
    const ev = new Event('beforeunload', { cancelable: true })
    Object.defineProperty(ev, 'returnValue', { writable: true, value: null })
    window.dispatchEvent(ev)
    // Handler sets returnValue (non-null) which triggers the browser dialog;
    // jsdom reports defaultPrevented=true when preventDefault() is called.
    expect(ev.defaultPrevented).toBe(true)
  })

  // Per spec §3.5 (ralplan decision #5): beforeunload guard fires ONLY in
  // recordingState === 'recording'. transcribing/done are safe — the server
  // completes the .md in the background. Warning the user there would be a lie.
  it('does NOT warn before unload while transcribing (per spec §3.5)', async () => {
    const router = createMemoryRouter([{ path: '/', element: <Recording /> }], {
      initialEntries: ['/'],
    })
    render(<RouterProvider router={router} />)

    // Force the store into finalizing state — even so, beforeunload must not fire
    // because the component is not in recordingState === 'recording'.
    useAppStore.setState({ recording: { sessionId: 'session-1', startedAt: null, elapsedSec: 0, status: 'finalizing', error: null } })

    await waitFor(() => {
      expect(useAppStore.getState().recording.status).toBe('finalizing')
    })

    const event = new Event('beforeunload', { cancelable: true })
    const preventDefaultSpy = vi.spyOn(event, 'preventDefault')
    window.dispatchEvent(event)
    expect(preventDefaultSpy).not.toHaveBeenCalled()
  })

  it('prevents duplicate finalize requests on repeated stop clicks', async () => {
    const router = createMemoryRouter([{ path: '/', element: <Recording /> }], {
      initialEntries: ['/'],
    })
    render(<RouterProvider router={router} />)

    fireEvent.click(screen.getByRole('button', { name: '녹음 시작' }))

    await waitFor(() => {
      expect(apiMock.createRecording).toHaveBeenCalledTimes(1)
    })

    now = 2000
    const stopButton = await screen.findByRole('button', { name: '정지' })
    fireEvent.click(stopButton)
    fireEvent.click(stopButton)

    await waitFor(() => {
      expect(apiMock.postRecordingChunk).toHaveBeenCalledTimes(1)
    })

    uploadResolve()

    await waitFor(() => {
      expect(apiMock.finalizeRecording).toHaveBeenCalledTimes(1)
    })
  })
})

// ─── T1–T7: transcribing/done screens + liveTranscriptionFailed ───────────────

function renderRecording() {
  const router = createMemoryRouter([{ path: '/', element: <Recording /> }], {
    initialEntries: ['/'],
  })
  return render(<RouterProvider router={router} />)
}

async function startAndStop({ uploadResolve, now, nowRef }) {
  // Start recording
  fireEvent.click(screen.getByRole('button', { name: '녹음 시작' }))
  await waitFor(() => expect(apiMock.createRecording).toHaveBeenCalledTimes(1))
  await screen.findByRole('button', { name: '정지' })

  // Advance time so durationSec > 1
  if (nowRef) nowRef.current = 2000
  fireEvent.click(screen.getByRole('button', { name: '정지' }))

  // Wait for chunk upload call, then resolve it
  await waitFor(() => expect(apiMock.postRecordingChunk).toHaveBeenCalledTimes(1))
  uploadResolve()
}

describe('Recording — transcribing/done screens', () => {
  let nowValue
  let uploadResolve
  let originalMediaRecorder
  let originalGetUserMedia
  let originalAlert
  const nowRef = { current: 0 }

  beforeEach(() => {
    vi.clearAllMocks()
    nowRef.current = 0
    useAppStore.setState({
      recording: {
        sessionId: null,
        startedAt: null,
        elapsedSec: 0,
        status: 'idle',
        error: null,
      },
    })

    vi.spyOn(Date, 'now').mockImplementation(() => nowRef.current)

    apiMock.createRecording.mockResolvedValue({ id: 'session-1' })
    apiMock.postRecordingChunk.mockImplementation(
      () => new Promise((resolve) => { uploadResolve = resolve }),
    )
    // Default: finalize succeeds with noteId
    apiMock.finalizeRecording.mockImplementation((_id, _body, handlers) => {
      if (handlers) {
        Promise.resolve().then(() =>
          handlers.complete({ payload: { noteId: 'doc-1' } }),
        )
        return () => {}
      }
      return Promise.resolve({ noteId: 'doc-1' })
    })

    transcriptStreamMock.mockReturnValue(() => {})

    const fakeStream = { getTracks: () => [{ stop: vi.fn() }] }
    originalGetUserMedia = navigator.mediaDevices?.getUserMedia
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia: vi.fn().mockResolvedValue(fakeStream) },
    })

    class FakeMediaRecorder {
      constructor() {
        this.state = 'inactive'
        this.ondataavailable = null
        this.onstop = null
      }
      start() { this.state = 'recording' }
      stop() {
        this.state = 'inactive'
        setTimeout(() => {
          this.ondataavailable?.({ data: new Blob(['chunk']) })
          this.onstop?.()
        }, 0)
      }
      requestData() {}
    }
    originalMediaRecorder = globalThis.MediaRecorder
    globalThis.MediaRecorder = FakeMediaRecorder

    originalAlert = globalThis.alert
    globalThis.alert = vi.fn()
  })

  afterEach(() => {
    vi.restoreAllMocks()
    if (originalMediaRecorder === undefined) {
      delete globalThis.MediaRecorder
    } else {
      globalThis.MediaRecorder = originalMediaRecorder
    }
    if (originalGetUserMedia) {
      Object.defineProperty(navigator, 'mediaDevices', {
        configurable: true,
        value: { getUserMedia: originalGetUserMedia },
      })
    }
    globalThis.alert = originalAlert
  })

  // T1: handleStop normal path → recordingState === 'transcribing'
  it('T1: handleStop enters transcribing state before finalize completes', async () => {
    // Make finalize hang so we can observe the intermediate state
    apiMock.finalizeRecording.mockImplementation((_id, _body, handlers) => {
      // Never resolves — intentionally hangs
      return () => {}
    })

    renderRecording()
    fireEvent.click(screen.getByRole('button', { name: '녹음 시작' }))
    await waitFor(() => expect(apiMock.createRecording).toHaveBeenCalledTimes(1))
    await screen.findByRole('button', { name: '정지' })

    nowRef.current = 2000
    fireEvent.click(screen.getByRole('button', { name: '정지' }))

    await waitFor(() => expect(apiMock.postRecordingChunk).toHaveBeenCalledTimes(1))
    uploadResolve()

    await waitFor(() => {
      expect(screen.getByText('전사하는 중이에요')).toBeInTheDocument()
    })

    // beforeunload should NOT trigger in transcribing state
    const ev = new Event('beforeunload', { cancelable: true })
    Object.defineProperty(ev, 'returnValue', { writable: true, value: null })
    window.dispatchEvent(ev)
    expect(ev.defaultPrevented).toBe(false)
  })

  // T2: SSE complete → recordingState === 'done', DoneView shown
  it('T2: finalize SSE complete shows DoneView with 전사가 완료됐어요', async () => {
    renderRecording()
    fireEvent.click(screen.getByRole('button', { name: '녹음 시작' }))
    await waitFor(() => expect(apiMock.createRecording).toHaveBeenCalledTimes(1))
    await screen.findByRole('button', { name: '정지' })

    nowRef.current = 2000
    fireEvent.click(screen.getByRole('button', { name: '정지' }))
    await waitFor(() => expect(apiMock.postRecordingChunk).toHaveBeenCalledTimes(1))
    uploadResolve()

    await waitFor(() => {
      expect(screen.getByText('전사가 완료됐어요')).toBeInTheDocument()
    })
    expect(screen.getByText('파일이 자동으로 저장되었습니다')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '↓ 파일로 내려받기' })).toBeInTheDocument()
  })

  // T3: SSE error → done screen + ErrorModal
  it('T3: finalize SSE error shows done screen with error modal', async () => {
    apiMock.finalizeRecording.mockImplementation((_id, _body, handlers) => {
      if (handlers) {
        Promise.resolve().then(() =>
          handlers.error({ payload: { noteId: 'doc-1', error: 'groq_failed' } }),
        )
        return () => {}
      }
      return Promise.resolve()
    })

    renderRecording()
    fireEvent.click(screen.getByRole('button', { name: '녹음 시작' }))
    await waitFor(() => expect(apiMock.createRecording).toHaveBeenCalledTimes(1))
    await screen.findByRole('button', { name: '정지' })

    nowRef.current = 2000
    fireEvent.click(screen.getByRole('button', { name: '정지' }))
    await waitFor(() => expect(apiMock.postRecordingChunk).toHaveBeenCalledTimes(1))
    uploadResolve()

    await waitFor(() => {
      expect(screen.getByText('전사가 완료됐어요')).toBeInTheDocument()
    })
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  // T4: SSE transportError → idle
  it('T4: finalize SSE transportError returns to idle', async () => {
    apiMock.finalizeRecording.mockImplementation((_id, _body, handlers) => {
      if (handlers) {
        Promise.resolve().then(() =>
          handlers.transportError(new Error('network fail')),
        )
        return () => {}
      }
      return Promise.resolve()
    })

    renderRecording()
    fireEvent.click(screen.getByRole('button', { name: '녹음 시작' }))
    await waitFor(() => expect(apiMock.createRecording).toHaveBeenCalledTimes(1))
    await screen.findByRole('button', { name: '정지' })

    nowRef.current = 2000
    fireEvent.click(screen.getByRole('button', { name: '정지' }))
    await waitFor(() => expect(apiMock.postRecordingChunk).toHaveBeenCalledTimes(1))
    uploadResolve()

    await waitFor(() => {
      expect(screen.getByRole('button', { name: '녹음 시작' })).toBeInTheDocument()
    })
    expect(screen.queryByText('전사하는 중이에요')).toBeNull()
    expect(screen.queryByText('전사가 완료됐어요')).toBeNull()
  })

  // T5: liveTranscriptionFailed=true → stop sends skipTranscribe:true → idle
  it('T5: liveTranscriptionFailed stop path calls finalizeRecording with skipTranscribe:true and returns to idle', async () => {
    let capturedGroqErrorHandler
    transcriptStreamMock.mockImplementation((_id, handlers) => {
      capturedGroqErrorHandler = handlers.onGroqError
      return () => {}
    })

    apiMock.finalizeRecording.mockImplementation((_id, _body, handlers) => {
      if (handlers) {
        Promise.resolve().then(() =>
          handlers.complete({ payload: { status: 'audio_only', noteId: null, audioPath: '/audio.webm', transcriptPath: null } }),
        )
        return () => {}
      }
      return Promise.resolve()
    })

    renderRecording()
    fireEvent.click(screen.getByRole('button', { name: '녹음 시작' }))
    await waitFor(() => expect(apiMock.createRecording).toHaveBeenCalledTimes(1))
    await screen.findByRole('button', { name: '정지' })

    // Simulate live transcription failure before stopping
    await waitFor(() => expect(capturedGroqErrorHandler).toBeDefined())
    capturedGroqErrorHandler({ errorType: 'network_failed_max_retries' })

    // Wait for the modal to confirm liveTranscriptionFailed state has propagated
    await waitFor(() => expect(screen.getByRole('dialog')).toBeInTheDocument())
    // Close the modal so it doesn't interfere
    fireEvent.keyDown(window, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())

    nowRef.current = 2000
    fireEvent.click(screen.getByRole('button', { name: '정지' }))
    await waitFor(() => expect(apiMock.postRecordingChunk).toHaveBeenCalledTimes(1))
    uploadResolve()

    await waitFor(() => {
      expect(apiMock.finalizeRecording).toHaveBeenCalledWith(
        'session-1',
        expect.objectContaining({ skipTranscribe: true }),
        expect.objectContaining({ complete: expect.any(Function) }),
      )
    })

    await waitFor(() => {
      expect(screen.getByRole('button', { name: '녹음 시작' })).toBeInTheDocument()
    })
    expect(screen.queryByText('전사하는 중이에요')).toBeNull()
    expect(screen.queryByText('전사가 완료됐어요')).toBeNull()
  })

  // T6: onGroqError(network_failed_max_retries) → ErrorModal shown; rate_limit camelCase regression guard
  it('T6: onGroqError network_failed_max_retries shows modal; rate_limit also works via camelCase errorType', async () => {
    let capturedHandlers
    transcriptStreamMock.mockImplementation((_id, handlers) => {
      capturedHandlers = handlers
      return () => {}
    })

    renderRecording()
    fireEvent.click(screen.getByRole('button', { name: '녹음 시작' }))
    await waitFor(() => expect(apiMock.createRecording).toHaveBeenCalledTimes(1))
    await screen.findByRole('button', { name: '정지' })

    await waitFor(() => expect(capturedHandlers).toBeDefined())

    // network_failed_max_retries → modal
    capturedHandlers.onGroqError({ errorType: 'network_failed_max_retries' })
    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeInTheDocument()
    })

    // Close modal via Escape
    fireEvent.keyDown(window, { key: 'Escape' })
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).toBeNull()
    })

    // rate_limit via camelCase (regression guard: if it were snake_case error_type this would not fire)
    capturedHandlers.onGroqError({ errorType: 'rate_limit' })
    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeInTheDocument()
    })
    // Verify the exact rate_limit modal title — fallback unexpected_error modal would
    // show '전사 오류' instead, so this guards against snake_case regression.
    expect(screen.getByText('오늘 groq 무료 한도 소진')).toBeInTheDocument()
  })

  // T7: done → "← 시작 화면으로" → idle + state reset
  it('T7: clicking 시작 화면으로 from done returns to idle', async () => {
    renderRecording()
    fireEvent.click(screen.getByRole('button', { name: '녹음 시작' }))
    await waitFor(() => expect(apiMock.createRecording).toHaveBeenCalledTimes(1))
    await screen.findByRole('button', { name: '정지' })

    nowRef.current = 2000
    fireEvent.click(screen.getByRole('button', { name: '정지' }))
    await waitFor(() => expect(apiMock.postRecordingChunk).toHaveBeenCalledTimes(1))
    uploadResolve()

    await waitFor(() => {
      expect(screen.getByText('전사가 완료됐어요')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: '← 시작 화면으로' }))

    await waitFor(() => {
      expect(screen.getByRole('button', { name: '녹음 시작' })).toBeInTheDocument()
    })
    expect(screen.queryByText('전사가 완료됐어요')).toBeNull()
    expect(screen.queryByText('전사하는 중이에요')).toBeNull()
  })
})
