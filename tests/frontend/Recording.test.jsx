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

vi.mock('@/api/client', () => ({
  default: apiMock,
  transcriptStream: vi.fn(() => () => {}),
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

  it('warns before unload while finalizing (status=finalizing)', async () => {
    const router = createMemoryRouter([{ path: '/', element: <Recording /> }], {
      initialEntries: ['/'],
    })
    render(<RouterProvider router={router} />)

    // Force the store into finalizing state so statusRef.current === 'finalizing'
    useAppStore.setState({ recording: { sessionId: 'session-1', startedAt: null, elapsedSec: 0, status: 'finalizing', error: null } })

    // Wait for the component to reflect the new status
    await waitFor(() => {
      expect(useAppStore.getState().recording.status).toBe('finalizing')
    })

    const event = new Event('beforeunload', { cancelable: true })
    const preventDefaultSpy = vi.spyOn(event, 'preventDefault')
    window.dispatchEvent(event)
    expect(preventDefaultSpy).toHaveBeenCalled()
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
