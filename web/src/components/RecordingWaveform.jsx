import { useEffect, useRef } from 'react'

/**
 * Real-time waveform for an active MediaStream.
 *
 * Plan §4.6 WaveformProps:
 *   stream: MediaStream | null
 *   width?: number   (default 640)
 *   height?: number  (default 96)
 *   fftSize?: number (default 2048)
 *   color?: string   (default 'rgb(239,68,68)')
 *
 * Uses a Web Audio AnalyserNode with time-domain byte data drawn on a canvas
 * inside a requestAnimationFrame loop. Source is connected to the analyser
 * only (NOT to destination) to avoid speaker feedback.
 */
export default function RecordingWaveform({
  stream,
  width = 640,
  height = 96,
  fftSize = 2048,
  color = 'rgb(239,68,68)',
}) {
  const canvasRef = useRef(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return undefined
    const ctx = canvas.getContext('2d')
    if (!ctx) return undefined

    if (!stream) {
      ctx.clearRect(0, 0, canvas.width, canvas.height)
      return undefined
    }

    const AudioCtx = window.AudioContext || window.webkitAudioContext
    if (!AudioCtx) {
      ctx.clearRect(0, 0, canvas.width, canvas.height)
      return undefined
    }

    const audioContext = new AudioCtx()
    const source = audioContext.createMediaStreamSource(stream)
    const analyser = audioContext.createAnalyser()
    analyser.fftSize = fftSize
    source.connect(analyser)

    const bufferLength = analyser.fftSize
    const dataArray = new Uint8Array(bufferLength)
    let rafId = 0

    const draw = () => {
      rafId = requestAnimationFrame(draw)
      analyser.getByteTimeDomainData(dataArray)
      ctx.clearRect(0, 0, canvas.width, canvas.height)
      ctx.lineWidth = 2
      ctx.strokeStyle = color
      ctx.beginPath()
      const sliceWidth = canvas.width / bufferLength
      let x = 0
      for (let i = 0; i < bufferLength; i += 1) {
        const v = dataArray[i] / 128.0
        const y = (v * canvas.height) / 2
        if (i === 0) ctx.moveTo(x, y)
        else ctx.lineTo(x, y)
        x += sliceWidth
      }
      ctx.lineTo(canvas.width, canvas.height / 2)
      ctx.stroke()
    }

    rafId = requestAnimationFrame(draw)

    return () => {
      cancelAnimationFrame(rafId)
      try {
        source.disconnect()
      } catch {
        // already disconnected
      }
      audioContext.close().catch(() => {})
    }
  }, [stream, fftSize, color])

  return (
    <canvas
      ref={canvasRef}
      width={width}
      height={height}
      className="w-full rounded-md border bg-muted"
      style={{ maxWidth: width }}
    />
  )
}
