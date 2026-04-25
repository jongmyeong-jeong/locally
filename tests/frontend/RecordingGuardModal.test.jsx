import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import RecordingGuardModal from '@/components/RecordingGuardModal'

describe('RecordingGuardModal', () => {
  it('does not render when open=false', () => {
    render(<RecordingGuardModal open={false} onLeave={vi.fn()} onStay={vi.fn()} />)
    expect(screen.queryByText('녹음을 중단할까요?')).toBeNull()
  })

  it('shows leave/stay buttons when open', () => {
    render(<RecordingGuardModal open={true} onLeave={vi.fn()} onStay={vi.fn()} />)
    expect(screen.getByText('녹음된 내용이 삭제됩니다.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '계속 녹음' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '나가기' })).toBeInTheDocument()
  })

  it('calls onLeave when 나가기 clicked', () => {
    const onLeave = vi.fn()
    render(<RecordingGuardModal open={true} onLeave={onLeave} onStay={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: '나가기' }))
    expect(onLeave).toHaveBeenCalledTimes(1)
  })

  it('calls onStay when 계속 녹음 clicked', () => {
    const onStay = vi.fn()
    render(<RecordingGuardModal open={true} onLeave={vi.fn()} onStay={onStay} />)
    fireEvent.click(screen.getByRole('button', { name: '계속 녹음' }))
    expect(onStay).toHaveBeenCalledTimes(1)
  })

  it('disables both buttons when busy', () => {
    render(
      <RecordingGuardModal open={true} onLeave={vi.fn()} onStay={vi.fn()} busy={true} />,
    )
    expect(screen.getByRole('button', { name: '계속 녹음' })).toBeDisabled()
    // busy일 때 나가기 버튼은 "정리 중..." 텍스트로 변경되며 disabled.
    expect(screen.getByRole('button', { name: '정리 중...' })).toBeDisabled()
  })
})
