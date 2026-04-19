import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render } from '@testing-library/react'

import RecordingWaveform from '@/components/RecordingWaveform'

// Canvas getContext returns a stub whose calls we can observe.
function makeCanvasCtxStub() {
  const calls = {
    stroke: 0,
    beginPath: 0,
    moveTo: 0,
    lineTo: 0,
    clearRect: 0,
  }
  const ctx = {
    clearRect: () => {
      calls.clearRect += 1
    },
    beginPath: () => {
      calls.beginPath += 1
    },
    moveTo: () => {
      calls.moveTo += 1
    },
    lineTo: () => {
      calls.lineTo += 1
    },
    stroke: () => {
      calls.stroke += 1
    },
    set lineWidth(v) {},
    set strokeStyle(v) {},
  }
  return { ctx, calls }
}

describe('RecordingWaveform', () => {
  let origRAF
  let origCAF
  let origAudioCtx

  beforeEach(() => {
    origRAF = window.requestAnimationFrame
    origCAF = window.cancelAnimationFrame
    origAudioCtx = window.AudioContext

    // Fire a single rAF tick synchronously.
    window.requestAnimationFrame = (cb) => {
      // Schedule asynchronously so React can attach the canvas first.
      setTimeout(() => cb(performance.now()), 0)
      return 1
    }
    window.cancelAnimationFrame = () => {}

    const fakeAnalyser = {
      fftSize: 2048,
      getByteTimeDomainData: (buf) => {
        // Fill with a sine-ish signal so lineTo/stroke work normally.
        for (let i = 0; i < buf.length; i += 1) buf[i] = 128
      },
    }
    const fakeSource = {
      connect: () => {},
      disconnect: () => {},
    }
    window.AudioContext = vi.fn().mockImplementation(() => ({
      createMediaStreamSource: () => fakeSource,
      createAnalyser: () => fakeAnalyser,
      close: () => Promise.resolve(),
    }))
  })

  afterEach(() => {
    window.requestAnimationFrame = origRAF
    window.cancelAnimationFrame = origCAF
    window.AudioContext = origAudioCtx
  })

  it('calls stroke() at least once after one rAF tick', async () => {
    const { ctx, calls } = makeCanvasCtxStub()
    const origGetContext = HTMLCanvasElement.prototype.getContext
    HTMLCanvasElement.prototype.getContext = () => ctx

    try {
      const fakeStream = {}
      render(<RecordingWaveform stream={fakeStream} />)
      // Wait for the setTimeout(cb, 0) + React effect cycle.
      await new Promise((r) => setTimeout(r, 20))
      expect(calls.stroke).toBeGreaterThanOrEqual(1)
      expect(calls.beginPath).toBeGreaterThanOrEqual(1)
    } finally {
      HTMLCanvasElement.prototype.getContext = origGetContext
    }
  })

  it('renders a canvas element', () => {
    const { container } = render(<RecordingWaveform stream={null} />)
    expect(container.querySelector('canvas')).not.toBeNull()
  })

  it('clears canvas when stream is null (no AudioContext needed)', () => {
    const { ctx, calls } = makeCanvasCtxStub()
    const origGetContext = HTMLCanvasElement.prototype.getContext
    HTMLCanvasElement.prototype.getContext = () => ctx
    try {
      render(<RecordingWaveform stream={null} />)
      expect(calls.clearRect).toBeGreaterThanOrEqual(1)
    } finally {
      HTMLCanvasElement.prototype.getContext = origGetContext
    }
  })
})
