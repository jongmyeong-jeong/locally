import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, render, screen, waitFor, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
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

const { notFound } = vi.hoisted(() => ({
  notFound: () => Object.assign(new Error('not found'), { status: 404 }),
}))

// postSse is only called after the user clicks AI로 요약.
const postSseHandlers = {}

vi.mock('@/api/client', () => ({
  default: {
    postSse: vi.fn((path, body, handlers) => {
      Object.assign(postSseHandlers, handlers)
      return () => {}
    }),
    getDocument: vi.fn().mockResolvedValue({ id: 'doc-123', status: 'completed' }),
    getSummary: vi.fn().mockRejectedValue(notFound()),
    getTranscript: vi.fn().mockResolvedValue({ content: '' }),
    getPrompt: vi.fn().mockRejectedValue(notFound()),
    listPrompts: vi.fn().mockResolvedValue([{ id: 1, name: '기본', template: '' }]),
  },
}))

function renderSummary() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/documents/doc-123/summary']}>
        <Routes>
          <Route path="/documents/:id/summary" element={<Summary />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

function renderSummaryWithExistingSummary() {
  api.getSummary.mockResolvedValue({ content: '**Bold** text' })
  return renderSummary()
}

describe('Summary - copy button', () => {
  let writeText

  beforeEach(() => {
    Object.keys(postSseHandlers).forEach((k) => delete postSseHandlers[k])
    writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    })
  })

  afterEach(() => {
    vi.clearAllMocks()
    // Reset getSummary back to default rejection after each test
    api.getSummary.mockRejectedValue(notFound())
  })

  it('calls navigator.clipboard.writeText(copyText) when copy clicked after prompt_ready', async () => {
    renderSummary()

    // Wait for initial state to render (AI로 요약 button is in the summary tab, forceMount).
    const startBtn = await screen.findByRole('button', { name: 'AI로 요약' })
    fireEvent.click(startBtn)

    // postSse is now called; wait for prompt_ready handler to be registered.
    const copyText =
      '다음 전사 내용을 한국어 회의록으로 정리해주세요...\n\n---\n전사:\nsome transcript'
    await waitFor(() => {
      expect(postSseHandlers.prompt_ready).toBeTypeOf('function')
    })

    // Server emits prompt_ready (no-AI-CLI path).
    act(() => {
      postSseHandlers.prompt_ready({
        payload: {
          prompt: '다음 전사 내용을 한국어 회의록으로 정리해주세요...',
          transcript: 'some transcript',
          copyText,
        },
      })
    })

    // After prompt_ready, UI is in error state — copy button should be visible.
    const copyBtn = await screen.findByRole('button', { name: '요약 프롬프트 복사' })
    fireEvent.click(copyBtn)

    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith(copyText)
    })
  })
})

describe('Summary - state machine', () => {
  beforeEach(() => {
    Object.keys(postSseHandlers).forEach((k) => delete postSseHandlers[k])
  })

  afterEach(() => {
    vi.clearAllMocks()
    api.getSummary.mockRejectedValue(notFound())
  })

  it('default tab: no summary → transcript tab active', async () => {
    renderSummary()

    // Wait for summaryQ to settle (404 → isError → activeTab='transcript')
    await waitFor(() => {
      const transcriptTrigger = screen.getByRole('tab', { name: '원본' })
      expect(transcriptTrigger).toHaveAttribute('data-state', 'active')
    })
  })

  it('default tab: has summary → summary tab active', async () => {
    renderSummaryWithExistingSummary()

    await waitFor(() => {
      const summaryTrigger = screen.getByRole('tab', { name: '요약' })
      expect(summaryTrigger).toHaveAttribute('data-state', 'active')
    })
  })

  it('initial state: shows AI로 요약 and 요약 프롬프트 복사 buttons', async () => {
    renderSummary()

    // Wait for query to settle
    await waitFor(() => {
      expect(screen.getByRole('tab', { name: '원본' })).toHaveAttribute('data-state', 'active')
    })

    expect(screen.getByRole('button', { name: 'AI로 요약' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '요약 프롬프트 복사' })).toBeInTheDocument()
  })

  it('in-progress state: shows loading text, hides buttons', async () => {
    renderSummary()

    // Wait for initial render
    const startBtn = await screen.findByRole('button', { name: 'AI로 요약' })
    fireEvent.click(startBtn)

    // After click, inProgress=true → summaryState='in_progress'
    await waitFor(() => {
      expect(screen.getByText('AI로 요약을 만들고 있어요...')).toBeInTheDocument()
    })

    expect(screen.queryByRole('button', { name: 'AI로 요약' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '요약 프롬프트 복사' })).not.toBeInTheDocument()
  })

  it('completed state: shows markdown, hides buttons', async () => {
    renderSummaryWithExistingSummary()

    // Wait for completed state — summary tab active, markdown rendered
    await waitFor(() => {
      expect(screen.getByRole('tab', { name: '요약' })).toHaveAttribute('data-state', 'active')
    })

    // The markdown **Bold** renders as a <strong> element containing "Bold"
    await waitFor(() => {
      expect(screen.getByText('Bold', { exact: false })).toBeInTheDocument()
    })

    expect(screen.queryByRole('button', { name: 'AI로 요약' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '요약 프롬프트 복사' })).not.toBeInTheDocument()
  })

  it('error state: shows error message and both buttons', async () => {
    renderSummary()

    const startBtn = await screen.findByRole('button', { name: 'AI로 요약' })
    fireEvent.click(startBtn)

    await waitFor(() => {
      expect(postSseHandlers.error).toBeTypeOf('function')
    })

    act(() => {
      postSseHandlers.error({ payload: { message: '오류', canRetry: true } })
    })

    await waitFor(() => {
      expect(screen.getByText('오류')).toBeInTheDocument()
    })

    expect(screen.getByRole('button', { name: 'AI로 요약' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '요약 프롬프트 복사' })).toBeInTheDocument()
  })

  it('AC-6: complete SSE does NOT auto-switch tab', async () => {
    const user = userEvent.setup()
    renderSummary()

    // Wait for transcript tab to be default (no summary → transcript)
    await waitFor(() => {
      expect(screen.getByRole('tab', { name: '원본' })).toHaveAttribute('data-state', 'active')
    })

    // Click AI로 요약 → switches to summary tab, registers SSE handlers
    const startBtn = screen.getByRole('button', { name: 'AI로 요약' })
    await user.click(startBtn)

    await waitFor(() => {
      expect(screen.getByRole('tab', { name: '요약' })).toHaveAttribute('data-state', 'active')
    })

    await waitFor(() => {
      expect(postSseHandlers.complete).toBeTypeOf('function')
    })

    // Manually switch back to transcript tab using userEvent (fires full pointer event sequence)
    await user.click(screen.getByRole('tab', { name: '원본' }))

    await waitFor(() => {
      expect(screen.getByRole('tab', { name: '원본' })).toHaveAttribute('data-state', 'active')
    })

    // Fire complete SSE — should NOT switch tab
    act(() => {
      postSseHandlers.complete({})
    })

    // Give React time to process any state updates from complete handler
    // Transcript tab must still be active — complete does not call setActiveTab (AC-6)
    await waitFor(() => {
      expect(screen.getByRole('tab', { name: '원본' })).toHaveAttribute('data-state', 'active')
    })
  })
})
