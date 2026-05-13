import { ArrowLeft, Check, Download } from 'lucide-react'
import './DoneView.css'

export default function DoneView({ noteId, onDownload, onGoHome }) {
  const canDownload = noteId !== null

  return (
    <div className="done-view">
      <div className="done-view__icon">
        <Check size={28} strokeWidth={2.5} />
      </div>

      <div className="done-view__text">
        <span className="done-view__title">전사가 완료됐어요</span>
        <span className="done-view__subtitle">파일이 자동으로 저장되었습니다</span>
      </div>

      <div className="done-view__actions">
        <button
          type="button"
          className="done-view__btn done-view__btn--primary"
          onClick={canDownload ? onDownload : undefined}
          disabled={!canDownload}
        >
          <Download size={16} strokeWidth={1.75} />
          파일로 내려받기
        </button>
        <button
          type="button"
          className="done-view__btn done-view__btn--ghost"
          onClick={onGoHome}
        >
          <ArrowLeft size={14} strokeWidth={1.75} />
          시작 화면으로
        </button>
      </div>
    </div>
  )
}
