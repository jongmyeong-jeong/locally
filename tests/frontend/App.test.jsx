import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

// Mock the API client so App's refreshSystemInfo call doesn't hit the network.
vi.mock('@/api/client', () => ({
  default: {
    getSystemInfo: vi.fn().mockResolvedValue({
      os: 'Darwin',
      arch: 'arm64',
      modelCatalog: [{ id: 'x/m', displayName: 'm', format: 'mlx' }],
      modelReady: true,
      aiAvailable: { claude: true, codex: false },
      ffmpegAvailable: true,
    }),
    listNotes: vi.fn().mockResolvedValue([]),
    getGlossary: vi.fn().mockResolvedValue([]),
  },
}))

import App from '@/App'

function renderApp(initialEntries = ['/']) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={initialEntries}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('App router smoke', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders Lonta brand link', async () => {
    renderApp(['/notes'])
    await waitFor(() => {
      expect(screen.getByText('Lonta')).toBeInTheDocument()
    })
  })

  it('renders nav links', async () => {
    renderApp(['/notes'])
    await waitFor(() => {
      // "노트" appears multiple times (nav + page header); assert ≥1 match.
      expect(screen.getAllByText('노트').length).toBeGreaterThanOrEqual(1)
      expect(screen.getByText('용어집')).toBeInTheDocument()
    })
  })

  it('renders updated glossary placeholder copy', async () => {
    renderApp(['/glossary'])
    await waitFor(() => {
      expect(
        screen.getByPlaceholderText('용어를 입력하고 Enter를 눌러주세요.'),
      ).toBeInTheDocument()
    })
  })

  it('renders NotFound for unknown route', async () => {
    renderApp(['/definitely-not-a-real-route'])
    await waitFor(() => {
      expect(
        screen.getByText(/페이지를 찾을 수 없습니다/),
      ).toBeInTheDocument()
    })
  })
})
