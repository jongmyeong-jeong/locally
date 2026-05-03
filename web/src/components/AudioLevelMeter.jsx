import { useEffect, useRef, useState } from 'react'
import './AudioLevelMeter.css'

const FALLBACK_HEIGHTS = [16, 24, 42, 12, 20, 32, 20, 24, 36, 24, 26]
const NUM_BARS = 11
const ALPHA = 0.4
const SILENCE_THRESHOLD = 5

/**
 * AudioLevelMeter
 *
 * Renders 11 vertical bars driven by Web Audio AnalyserNode FFT data.
 * When audioStream is absent or silent, falls back to static Figma heights.
 *
 * Props:
 *   audioStream?: MediaStream | null
 */
export default function AudioLevelMeter({ audioStream }) {
  const [barHeights, setBarHeights] = useState(FALLBACK_HEIGHTS)
  const rafRef = useRef(null)
  const smoothedRef = useRef(new Array(NUM_BARS).fill(0))

  useEffect(() => {
    if (!audioStream) {
      setBarHeights(FALLBACK_HEIGHTS)
      return undefined
    }

    const AudioCtx = window.AudioContext || window.webkitAudioContext
    if (!AudioCtx) {
      setBarHeights(FALLBACK_HEIGHTS)
      return undefined
    }

    const audioContext = new AudioCtx()
    const source = audioContext.createMediaStreamSource(audioStream)
    const analyser = audioContext.createAnalyser()
    analyser.fftSize = 256
    // Connect only to analyser — not to destination — to avoid mic feedback
    source.connect(analyser)

    const binCount = analyser.frequencyBinCount // 128
    const buffer = new Uint8Array(binCount)
    const smoothed = new Array(NUM_BARS).fill(0)
    smoothedRef.current = smoothed

    // Pre-compute segment boundaries for 11 bands across 128 bins
    const segments = []
    for (let i = 0; i < NUM_BARS; i++) {
      const start = Math.floor((i * binCount) / NUM_BARS)
      const end = Math.floor(((i + 1) * binCount) / NUM_BARS)
      segments.push({ start, end })
    }

    const tick = () => {
      rafRef.current = requestAnimationFrame(tick)
      analyser.getByteFrequencyData(buffer)

      // Downsample: mean of each band segment
      for (let i = 0; i < NUM_BARS; i++) {
        const { start, end } = segments[i]
        let sum = 0
        for (let j = start; j < end; j++) sum += buffer[j]
        const raw = sum / (end - start)
        // EMA smoothing
        smoothed[i] = ALPHA * raw + (1 - ALPHA) * smoothed[i]
      }

      // Silence detection: mean of all smoothed values
      const mean = smoothed.reduce((a, b) => a + b, 0) / NUM_BARS
      if (mean < SILENCE_THRESHOLD) {
        setBarHeights(FALLBACK_HEIGHTS)
        return
      }

      // Map 0-255 → 4-50px
      setBarHeights(smoothed.map(v => 4 + (v / 255) * 46))
    }

    rafRef.current = requestAnimationFrame(tick)

    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current)
      try {
        source.disconnect()
        analyser.disconnect()
      } catch {
        // already disconnected
      }
      audioContext.close().catch(() => {})
    }
  }, [audioStream])

  return (
    <div className="audio-level-meter">
      {barHeights.map((h, i) => (
        <div
          key={i}
          className="audio-level-meter__bar"
          style={{ height: `${h}px` }}
        />
      ))}
    </div>
  )
}
