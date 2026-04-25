import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

const { apiMock } = vi.hoisted(() => ({
  apiMock: {
    listPrompts: vi.fn(),
    updatePrompt: vi.fn(),
    deletePrompt: vi.fn(),
  },
}))

vi.mock('@/api/client', () => ({ default: apiMock }))
vi.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: vi.fn() }),
}))

import PromptDetail from '@/pages/PromptDetail'

function renderWith(presets) {
  apiMock.listPrompts.mockResolvedValue(presets)
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const router = createMemoryRouter(
    [
      { path: '/settings/prompts/:id', element: <PromptDetail /> },
      { path: '/settings/prompts', element: <div>목록</div> },
    ],
    { initialEntries: ['/settings/prompts/1'] },
  )
  return render(
    <QueryClientProvider client={qc}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

describe('PromptDetail', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders form populated from preset', async () => {
    renderWith([{ id: 1, name: 'My', template: 'hello {transcript}' }])
    await waitFor(() => {
      expect(screen.getByDisplayValue('My')).toBeInTheDocument()
    })
    expect(screen.getByDisplayValue('hello {transcript}')).toBeInTheDocument()
  })

  it('calls updatePrompt on save with edited values', async () => {
    apiMock.updatePrompt.mockResolvedValue({})
    renderWith([{ id: 1, name: 'Old', template: 'T' }])
    const nameInput = await screen.findByDisplayValue('Old')
    fireEvent.change(nameInput, { target: { value: 'New' } })
    fireEvent.click(screen.getByRole('button', { name: '저장' }))
    await waitFor(() => {
      expect(apiMock.updatePrompt).toHaveBeenCalledWith(1, {
        name: 'New',
        template: 'T',
      })
    })
  })
})
