import { useEffect, useRef, useState } from 'react'
import './AudioLevelMeter.css'

const NUM_BARS = 30
const ALPHA = 0.4
// Silence cutoffs — calibrated for typical mic noise floors.
// If the mean across all bars is below GLOBAL_SILENCE, every bar collapses
// to the flat resting height (a uniform row of dots). Otherwise each bar
// that falls below PER_BAR_FLOOR also rests at FLAT_HEIGHT.
const GLOBAL_SILENCE = 4
const PER_BAR_FLOOR = 14
const MAX_BAR_HEIGHT = 48
const FLAT_HEIGHT = 6 // resting height — matches bar width so each bar is a circle

// Stable per-bar opacity (Figma node 492:160) — gives visual variety
// without re-randomising every render.
const BAR_OPACITIES = [
  0.78, 0.72, 0.79, 0.6, 0.92, 0.65, 0.98, 0.6, 0.75, 0.63,
  0.85, 0.6, 0.64, 0.61, 0.69, 0.87, 0.74, 0.73, 0.72, 0.77,
  0.97, 0.9, 0.67, 0.61, 0.64, 0.65, 0.61, 0.68, 0.67, 0.86,
]

const FLAT_HEIGHTS = new Array(NUM_BARS).fill(FLAT_HEIGHT)

/**
 * AudioLevelMeter
 *
 * 30 vermilion bars driven by Web Audio AnalyserNode FFT data.
 * Each bar collapses to 0 height when its band is below the silence
 * floor, so quiet rooms render as completely empty (no stray tall bars).
 *
 * Props:
 *   audioStream?: MediaStream | null
 */
export default function AudioLevelMeter({ audioStream }) {
  const [barHeights, setBarHeights] = useState(FLAT_HEIGHTS)
  const rafRef = useRef(null)

  useEffect(() => {
    if (!audioStream) {
      setBarHeights(FLAT_HEIGHTS)
      return undefined
    }

    const AudioCtx = window.AudioContext || window.webkitAudioContext
    if (!AudioCtx) {
      setBarHeights(FLAT_HEIGHTS)
      return undefined
    }

    const audioContext = new AudioCtx()
    let source
    let analyser
    try {
      source = audioContext.createMediaStreamSource(audioStream)
      analyser = audioContext.createAnalyser()
      analyser.fftSize = 256
      source.connect(analyser)
    } catch (err) {
      console.warn('AudioLevelMeter: cannot attach stream', err)
      setBarHeights(FLAT_HEIGHTS)
      audioContext.close().catch(() => {})
      return undefined
    }

    const binCount = analyser.frequencyBinCount
    const buffer = new Uint8Array(binCount)
    const smoothed = new Array(NUM_BARS).fill(0)

    const segments = []
    for (let i = 0; i < NUM_BARS; i++) {
      const start = Math.floor((i * binCount) / NUM_BARS)
      const end = Math.floor(((i + 1) * binCount) / NUM_BARS)
      segments.push({ start, end })
    }

    const tick = () => {
      rafRef.current = requestAnimationFrame(tick)
      analyser.getByteFrequencyData(buffer)

      for (let i = 0; i < NUM_BARS; i++) {
        const { start, end } = segments[i]
        let sum = 0
        for (let j = start; j < end; j++) sum += buffer[j]
        const raw = sum / (end - start)
        smoothed[i] = ALPHA * raw + (1 - ALPHA) * smoothed[i]
      }

      const mean = smoothed.reduce((acc, n) => acc + n, 0) / NUM_BARS
      if (mean < GLOBAL_SILENCE) {
        setBarHeights(FLAT_HEIGHTS)
        return
      }

      const heights = smoothed.map((v) => {
        if (v < PER_BAR_FLOOR) return FLAT_HEIGHT
        return FLAT_HEIGHT + (v / 255) * (MAX_BAR_HEIGHT - FLAT_HEIGHT)
      })
      setBarHeights(heights)
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
          style={{ height: `${h}px`, opacity: BAR_OPACITIES[i] }}
        />
      ))}
    </div>
  )
}
