import './RealtimeTranscript.css'

// Displays the most recent 4 transcript lines during live recording.
// props:
//   lines — array of { seq, startMs, endMs, text }, already sliced to max 4
// Returns null when lines is empty (no empty state rendered).
export default function RealtimeTranscript({ lines }) {
  if (!lines || lines.length === 0) return null

  return (
    <div className="realtime-transcript">
      {lines.map((line) => (
        <p key={line.seq} className="realtime-transcript-line">
          {line.text}
        </p>
      ))}
    </div>
  )
}
