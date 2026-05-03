import { useEffect, useRef, useState } from 'react'
import './FlipCardTimer.css'

/**
 * FlipCard — single digit that 3D-flips when its value changes.
 *
 * The top half shows the current digit. On change the top half
 * rotates down (rotateX -180deg, 400ms ease-in-out) while the
 * bottom half already holds the incoming digit.
 */
function FlipCard({ digit }) {
  const prevDigitRef = useRef(digit)
  const [flipping, setFlipping] = useState(false)
  const [displayCurrent, setDisplayCurrent] = useState(digit)
  const [displayIncoming, setDisplayIncoming] = useState(digit)

  useEffect(() => {
    if (digit === prevDigitRef.current) return

    // Stage the incoming digit on the bottom half, then trigger flip
    setDisplayIncoming(digit)
    setFlipping(true)

    const timer = setTimeout(() => {
      // After animation completes, snap current to the new digit
      setDisplayCurrent(digit)
      setFlipping(false)
    }, 400)

    prevDigitRef.current = digit
    return () => clearTimeout(timer)
  }, [digit])

  return (
    <span className="flip-card" aria-hidden="true">
      <span className="flip-card__inner">
        {/* Top half: current digit, flips down on change */}
        <span className={`flip-card__top${flipping ? ' flip-card__top--flipping' : ''}`}>
          <span className="flip-card__digit">{displayCurrent}</span>
        </span>
        {/* Bottom half: incoming digit (revealed as top flips away) */}
        <span className="flip-card__bottom">
          <span className="flip-card__digit">{displayIncoming}</span>
        </span>
      </span>
    </span>
  )
}

/**
 * Format elapsedMs as a human-readable Korean string for aria-label.
 * Updated only every minute to avoid screen-reader spam.
 */
function formatHumanReadable(elapsedMs) {
  const totalSeconds = Math.floor(elapsedMs / 1000)
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)

  if (hours > 0) {
    return `${hours}시간 ${minutes}분`
  }
  if (minutes > 0) {
    return `${minutes}분`
  }
  return '0분'
}

/**
 * FlipCardTimer — elapsed time display with per-digit 3D flip animation.
 *
 * Props:
 *   elapsedMs    — elapsed milliseconds (number)
 *   forceHours   — if true, always show HH:MM:SS even under 60min (boolean, optional)
 */
export default function FlipCardTimer({ elapsedMs = 0, forceHours = false }) {
  const totalSeconds = Math.floor(elapsedMs / 1000)
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60

  const showHours = forceHours || elapsedMs >= 3_600_000

  // Digit breakdown
  const hh0 = String(Math.floor(hours / 10))
  const hh1 = String(hours % 10)
  const mm0 = String(Math.floor(minutes / 10))
  const mm1 = String(minutes % 10)
  const ss0 = String(Math.floor(seconds / 10))
  const ss1 = String(seconds % 10)

  // Track whether HH segment has become visible for smooth fade-in
  const hhVisibleRef = useRef(showHours)
  const [hhSegmentVisible, setHhSegmentVisible] = useState(showHours)

  useEffect(() => {
    if (showHours && !hhVisibleRef.current) {
      hhVisibleRef.current = true
      setHhSegmentVisible(true)
    }
  }, [showHours])

  // Aria-label updates only every minute (keyed on total minutes elapsed)
  const totalMinutes = Math.floor(elapsedMs / 60_000)
  const [ariaLabel, setAriaLabel] = useState(() => `경과 시간 ${formatHumanReadable(elapsedMs)}`)

  useEffect(() => {
    setAriaLabel(`경과 시간 ${formatHumanReadable(elapsedMs)}`)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [totalMinutes])

  return (
    <div
      className="flip-timer"
      role="timer"
      aria-live="polite"
      aria-label={ariaLabel}
    >
      {/* HH segment — fades in when transitioning past 60min */}
      {(showHours || hhSegmentVisible) && (
        <span
          className={`flip-timer__hh-segment${hhSegmentVisible ? ' flip-timer__hh-segment--visible' : ''}`}
        >
          <FlipCard digit={hh0} />
          <FlipCard digit={hh1} />
          <span className="flip-timer__sep">:</span>
        </span>
      )}

      {/* MM segment */}
      <FlipCard digit={mm0} />
      <FlipCard digit={mm1} />

      <span className="flip-timer__sep">:</span>

      {/* SS segment */}
      <FlipCard digit={ss0} />
      <FlipCard digit={ss1} />
    </div>
  )
}
