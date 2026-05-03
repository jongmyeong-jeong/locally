import { useEffect, useRef } from 'react'
import './ErrorModal.css'

const CONTENT = {
  rate_limit: {
    title: '오늘 Groq 무료 한도 소진',
    body: '내일 다시 시도하세요.',
    buttons: (onClose, onStopRecording) => [
      <button
        key="stop"
        className="error-modal__btn error-modal__btn--primary"
        onClick={onStopRecording}
        autoFocus
      >
        녹음 종료 (권장)
      </button>,
    ],
  },
  server_error: {
    title: 'Groq 일시 장애',
    body: '잠시 후 다시 시도하세요. 진행 중인 녹음은 계속됩니다.',
    buttons: (onClose, onStopRecording) => [
      <button
        key="continue"
        className="error-modal__btn error-modal__btn--secondary"
        onClick={onClose}
        autoFocus
      >
        계속 시도
      </button>,
      <button
        key="stop"
        className="error-modal__btn error-modal__btn--primary"
        onClick={onStopRecording}
      >
        녹음 종료
      </button>,
    ],
  },
  api_key_missing: {
    title: 'GROQ_API_KEY 미설정',
    body: '환경변수 GROQ_API_KEY를 설정한 뒤 서버를 재시작하세요.',
    buttons: (onClose) => [
      <button
        key="ok"
        className="error-modal__btn error-modal__btn--secondary"
        onClick={onClose}
        autoFocus
      >
        확인
      </button>,
    ],
  },
}

function getContent(errorType) {
  if (CONTENT[errorType]) return CONTENT[errorType]
  // Fallback for client_error, concat_error, unexpected_error
  return {
    title: '전사 오류',
    body: `기술적 문제가 발생했습니다. (오류 코드: ${errorType})`,
    buttons: (onClose, onStopRecording) => [
      <button
        key="stop"
        className="error-modal__btn error-modal__btn--primary"
        onClick={onStopRecording}
        autoFocus
      >
        녹음 종료
      </button>,
      <button
        key="continue"
        className="error-modal__btn error-modal__btn--secondary"
        onClick={onClose}
      >
        계속
      </button>,
    ],
  }
}

/**
 * ErrorModal — stateless presentational modal.
 *
 * Props:
 *   open: boolean
 *   errorType: "rate_limit" | "server_error" | "api_key_missing" | "client_error" | "concat_error" | "unexpected_error"
 *   onClose: () => void
 *   onStopRecording?: () => void
 */
export default function ErrorModal({ open, errorType, onClose, onStopRecording }) {
  const cardRef = useRef(null)

  // ESC key closes
  useEffect(() => {
    if (!open) return
    const handler = (e) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [open, onClose])

  // Focus first button when opening
  useEffect(() => {
    if (!open) return
    const timer = setTimeout(() => {
      const btn = cardRef.current?.querySelector('button[autofocus], button')
      btn?.focus()
    }, 10)
    return () => clearTimeout(timer)
  }, [open])

  if (!open) return null

  const content = getContent(errorType)
  const handleStopRecording = onStopRecording ?? onClose

  return (
    <div
      className="error-modal__backdrop"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
      role="dialog"
      aria-modal="true"
      aria-labelledby="error-modal-title"
    >
      <div className="error-modal__card" ref={cardRef}>
        <p id="error-modal-title" className="error-modal__title">{content.title}</p>
        <p className="error-modal__body">{content.body}</p>
        <div className="error-modal__actions">
          {content.buttons(onClose, handleStopRecording)}
        </div>
      </div>
    </div>
  )
}
