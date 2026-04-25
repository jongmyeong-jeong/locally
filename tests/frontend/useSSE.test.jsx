import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import Summary from '@/pages/Summary'
import api from '@/api/client'

vi.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: vi.fn() }),
}))

vi.mock('@/hooks/useSettings', () => ({
  useSettings: () => ({ data: { preferredAi: 'auto' } }),
}))

// Capture postSse handlers for summarize tests
const postSseHandlers = {}

vi.mock('@/api/client', () => ({
  default: {
    postSse: vi.fn((path, body, handlers) => {
      Object.assign(postSseHandlers, handlers)
      return () => {}
    }),
    getNote: vi.fn(),
    getSummary: vi.fn().mockRejectedValue(Object.assign(new Error('not found'), { status: 404 })),
    getTranscript: vi.fn().mockResolvedValue({ content: '' }),
    getPrompt: vi.fn().mockRejectedValue(Object.assign(new Error('not found'), { status: 404 })),
    listPrompts: vi.fn().mockResolvedValue([]),
    deleteNote: vi.fn(),
  },
}))

// useFinalizePoller의 intervalMs를 0으로 오버라이드하여 테스트 속도 향상
vi.mock('@/hooks/useSSE', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    useFinalizePoller: (opts) =>
      actual.useFinalizePoller({ ...opts, intervalMs: 0, maxPolls: 3 }),
  }
})

function renderSummary(noteId = 'doc-1') {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return {
    qc,
    ...render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={[`/notes/${noteId}/summary`]}>
          <Routes>
            <Route path="/notes/:id/summary" element={<Summary />} />
            <Route path="/notes" element={<div>note list</div>} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    ),
  }
}

describe('Summary — finalizing state recovery', () => {
  beforeEach(() => {
    Object.keys(postSseHandlers).forEach((k) => delete postSseHandlers[k])
    api.getSummary.mockRejectedValue(Object.assign(new Error('not found'), { status: 404 }))
  })

  afterEach(() => {
    vi.clearAllMocks()
    api.getNote.mockReset()
    api.getSummary.mockRejectedValue(Object.assign(new Error('not found'), { status: 404 }))
  })

  it('(a) status=finalizing — shows 처리 중 안내 and polls until transcribed', async () => {
    // 첫 호출(TanStack Query 초기 fetch): finalizing 반환
    // 두 번째 호출(폴링): never resolve — 처리 중 UI가 충분히 오래 표시됨
    // 이후: transcribed 반환
    let resolveSecond
    const secondCall = new Promise((r) => { resolveSecond = r })

    let callCount = 0
    api.getNote.mockImplementation(() => {
      callCount += 1
      if (callCount === 1) return Promise.resolve({ id: 'doc-1', status: 'finalizing' })
      if (callCount === 2) return secondCall
      return Promise.resolve({ id: 'doc-1', status: 'transcribed' })
    })

    renderSummary('doc-1')

    // "처리 중" 안내 문구가 렌더링되어야 함
    await waitFor(() => {
      expect(screen.getByText(/녹음을 처리하고 있어요/)).toBeInTheDocument()
    })

    expect(api.getSummary).not.toHaveBeenCalled()
    expect(api.getTranscript).not.toHaveBeenCalled()

    // finalizing 상태에서는 탭 UI가 보이면 안 됨
    expect(screen.queryAllByRole('tab')).toHaveLength(0)
    expect(screen.queryByRole('button', { name: 'AI로 요약' })).not.toBeInTheDocument()

    // 두 번째 폴링을 transcribed로 해제 → TanStack Query 무효화 → UI 전환
    act(() => {
      resolveSecond({ id: 'doc-1', status: 'transcribed' })
    })

    // "처리 중" 안내가 사라짐
    await waitFor(() => {
      expect(screen.queryByText(/녹음을 처리하고 있어요/)).not.toBeInTheDocument()
    }, { timeout: 3000 })
  })

  it('(d) finalizing poll timeout — shows recovery UI and stops hidden fetches', async () => {
    api.getNote.mockResolvedValue({ id: 'doc-1', status: 'finalizing' })

    renderSummary('doc-1')

    await waitFor(() => {
      expect(screen.getByText(/녹음을 처리하고 있어요/)).toBeInTheDocument()
    })

    expect(api.getSummary).not.toHaveBeenCalled()
    expect(api.getTranscript).not.toHaveBeenCalled()

    await waitFor(() => {
      expect(screen.getByText(/처리가 예상보다 오래 걸리고 있어요/)).toBeInTheDocument()
    })
  })

  it('(e) finalizing poll 404 — stops retrying and shows recovery UI', async () => {
    api.getNote
      .mockResolvedValueOnce({ id: 'doc-1', status: 'finalizing' })
      .mockRejectedValue(Object.assign(new Error('gone'), { status: 404 }))

    renderSummary('doc-1')

    await waitFor(() => {
      expect(screen.getByText(/녹음을 처리하고 있어요/)).toBeInTheDocument()
    })

    await waitFor(() => {
      expect(screen.getByText(/노트 상태를 다시 확인해 주세요/)).toBeInTheDocument()
    })

    const callCount = api.getNote.mock.calls.length
    await new Promise((r) => setTimeout(r, 50))
    expect(api.getNote.mock.calls.length).toBe(callCount)
  })

  it('(b) status=transcription_failed — FailedTranscriptionBlock 표시', async () => {
    api.getNote.mockResolvedValue({ id: 'doc-1', status: 'transcription_failed' })

    renderSummary('doc-1')

    await waitFor(() => {
      expect(screen.getByText('전사가 실패했습니다')).toBeInTheDocument()
    })

    expect(screen.getByRole('button', { name: '삭제' })).toBeInTheDocument()
    expect(screen.queryByRole('tab')).not.toBeInTheDocument()
  })

  it('(c) status=transcribed — 정상 탭 UI 표시', async () => {
    api.getNote.mockResolvedValue({ id: 'doc-1', status: 'transcribed' })

    renderSummary('doc-1')

    await waitFor(() => {
      expect(screen.getByRole('tab', { name: '원본' })).toBeInTheDocument()
    })

    expect(screen.queryByText(/녹음을 처리하고 있어요/)).not.toBeInTheDocument()
  })
})

describe('useFinalizePoller — 중복 호출 방어', () => {
  afterEach(() => {
    vi.clearAllMocks()
    api.getNote.mockReset()
    api.getSummary.mockRejectedValue(Object.assign(new Error('not found'), { status: 404 }))
  })

  it('finalizing→transcribed 전환 시 폴링이 중단됨', async () => {
    // finalizing → transcribed
    api.getNote
      .mockResolvedValueOnce({ id: 'doc-1', status: 'finalizing' })
      .mockResolvedValue({ id: 'doc-1', status: 'transcribed' })

    api.getSummary.mockRejectedValue(Object.assign(new Error('not found'), { status: 404 }))

    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={['/notes/doc-1/summary']}>
          <Routes>
            <Route path="/notes/:id/summary" element={<Summary />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    )

    // finalizing 상태에서 처리 중 표시
    await waitFor(() => {
      expect(screen.getByText(/녹음을 처리하고 있어요/)).toBeInTheDocument()
    })

    // transcribed로 전환 후 폴링 중단
    await waitFor(() => {
      expect(screen.queryByText(/녹음을 처리하고 있어요/)).not.toBeInTheDocument()
    }, { timeout: 3000 })

    // transcribed 이후에는 getNote 호출이 더 이상 증가하지 않음
    const callCount = api.getNote.mock.calls.length
    // 100ms 대기 후에도 추가 호출 없음 (폴링이 중단됨)
    await new Promise((r) => setTimeout(r, 100))
    expect(api.getNote.mock.calls.length).toBe(callCount)
  })
})
