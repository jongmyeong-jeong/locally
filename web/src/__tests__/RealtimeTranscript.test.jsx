import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import RealtimeTranscript from '@/components/RealtimeTranscript'

const makeLine = (seq, text) => ({ seq, startMs: seq * 1000, endMs: (seq + 1) * 1000, text })

describe('RealtimeTranscript', () => {
  // C2: empty array → null (no DOM output)
  it('renders nothing when lines is empty', () => {
    const { container } = render(<RealtimeTranscript lines={[]} />)
    expect(container.firstChild).toBeNull()
  })

  // C2: undefined lines → null
  it('renders nothing when lines is undefined', () => {
    const { container } = render(<RealtimeTranscript lines={undefined} />)
    expect(container.firstChild).toBeNull()
  })

  // C1: after slice(-4) only 4 lines visible, oldest (seq=0) gone
  it('shows only 4 lines when 5 are pushed via slice(-4)', () => {
    const allLines = [
      makeLine(0, 'First line'),
      makeLine(1, 'Second line'),
      makeLine(2, 'Third line'),
      makeLine(3, 'Fourth line'),
      makeLine(4, 'Fifth line'),
    ]
    // Simulate the slice(-4) done in Recording.jsx before passing to component
    const visibleLines = allLines.slice(-4)

    render(<RealtimeTranscript lines={visibleLines} />)

    // Oldest line (seq=0) must NOT be in the DOM
    expect(screen.queryByText('First line')).toBeNull()

    // Lines 1-4 must be visible
    expect(screen.getByText('Second line')).toBeInTheDocument()
    expect(screen.getByText('Third line')).toBeInTheDocument()
    expect(screen.getByText('Fourth line')).toBeInTheDocument()
    expect(screen.getByText('Fifth line')).toBeInTheDocument()

    // Exactly 4 line elements
    const lineEls = document.querySelectorAll('.realtime-transcript-line')
    expect(lineEls).toHaveLength(4)
  })

  // Renders text content without timestamps
  it('renders text content only, no timestamp markup', () => {
    const lines = [makeLine(0, 'Hello world')]
    render(<RealtimeTranscript lines={lines} />)
    expect(screen.getByText('Hello world')).toBeInTheDocument()
    // Verify no time-like patterns rendered (startMs / endMs not shown)
    expect(screen.queryByText(/0ms/)).toBeNull()
  })
})
