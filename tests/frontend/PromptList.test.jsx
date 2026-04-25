import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

const { apiMock } = vi.hoisted(() => ({
  apiMock: {
    listPrompts: vi.fn(),
    createPrompt: vi.fn(),
    deletePrompt: vi.fn(),
    reorderPrompts: vi.fn(),
  },
}))

vi.mock('@/api/client', () => ({ default: apiMock }))
vi.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: vi.fn() }),
}))

import PromptList from '@/pages/PromptList'

function renderWith(initialPath = '/settings/prompts') {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const router = createMemoryRouter(
    [{ path: '/settings/prompts', element: <PromptList /> }],
    { initialEntries: [initialPath] },
  )
  return render(
    <QueryClientProvider client={qc}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

describe('PromptList', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('shows empty state when no prompts', async () => {
    apiMock.listPrompts.mockResolvedValue([])
    renderWith()
    await waitFor(() => {
      expect(
        screen.getByText('프리셋이 없습니다. 새로 만들어 주세요.'),
      ).toBeInTheDocument()
    })
  })

  it('lists prompts in array order', async () => {
    apiMock.listPrompts.mockResolvedValue([
      { id: 1, name: 'A', template: '' },
      { id: 2, name: 'B', template: '' },
    ])
    renderWith()
    await waitFor(() => {
      const a = screen.getByText('A')
      const b = screen.getByText('B')
      expect(a).toBeInTheDocument()
      expect(b).toBeInTheDocument()
      // Order check: A comes before B in DOM
      expect(a.compareDocumentPosition(b)).toBe(Node.DOCUMENT_POSITION_FOLLOWING)
    })
  })
})
