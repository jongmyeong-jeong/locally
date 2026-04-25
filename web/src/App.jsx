import { Suspense, lazy } from 'react'
import { Link, Navigate, Route, Routes } from 'react-router-dom'
import useSystemInfo from '@/hooks/useSystemInfo'
import { useAppStore } from '@/stores/app'

const DocumentList = lazy(() => import('@/pages/DocumentList'))
const Upload = lazy(() => import('@/pages/Upload'))
const Recording = lazy(() => import('@/pages/Recording'))
const Transcribing = lazy(() => import('@/pages/Transcribing'))
const Summary = lazy(() => import('@/pages/Summary'))
const Glossary = lazy(() => import('@/pages/Glossary'))
const ModelSetup = lazy(() => import('@/pages/ModelSetup'))
const Settings = lazy(() => import('@/pages/Settings'))
const PromptList = lazy(() => import('@/pages/PromptList'))
const PromptDetail = lazy(() => import('@/pages/PromptDetail'))

function NotFound() {
  return (
    <section className="mx-auto max-w-3xl p-6">
      <h1 className="text-2xl font-semibold">페이지를 찾을 수 없습니다</h1>
    </section>
  )
}

// First-run gate: `/` routes to ModelSetup until a Whisper model is installed,
// then falls through to DocumentList. Plan §1 AC-3/4 + Phase E landing.
function LandingGate() {
  const { data: sysInfo, isLoading } = useSystemInfo()

  if (isLoading) {
    return (
      <section className="mx-auto max-w-3xl p-6 text-sm text-muted-foreground">
        불러오는 중...
      </section>
    )
  }

  if (!sysInfo?.modelReady) return <ModelSetup />
  return <Navigate to="/documents" replace />
}

function RouteLoading() {
  return (
    <section className="mx-auto max-w-3xl p-6 text-sm text-muted-foreground">
      화면을 불러오는 중...
    </section>
  )
}

function Shell({ children }) {
  const recordingStatus = useAppStore((s) => s.recording.status)
  // AC1: 녹음 중/마이크 요청/finalize 단계에서만 헤더 숨김. error/idle은 표시.
  const hideHeader = ['requestingMic', 'recording', 'finalizing'].includes(recordingStatus)

  return (
    <div className="min-h-screen bg-background text-foreground">
      {!hideHeader && (
        <header className="border-b">
          <nav className="mx-auto max-w-3xl p-4 flex gap-4 text-sm">
            <Link to="/" className="font-semibold">Locally</Link>
            <Link to="/documents" className="text-muted-foreground hover:text-foreground">기록</Link>
            <Link to="/upload" className="text-muted-foreground hover:text-foreground">업로드</Link>
            <Link to="/recording" className="text-muted-foreground hover:text-foreground">녹음</Link>
            <Link to="/glossary" className="text-muted-foreground hover:text-foreground">용어집</Link>
            <Link to="/settings" className="text-muted-foreground hover:text-foreground">설정</Link>
          </nav>
        </header>
      )}
      <main>{children}</main>
    </div>
  )
}

export default function App() {
  return (
    <Shell>
      <Suspense fallback={<RouteLoading />}>
        <Routes>
          <Route path="/" element={<LandingGate />} />
          <Route path="/documents" element={<DocumentList />} />
          <Route path="/documents/:id" element={<Summary />} />
          <Route path="/documents/:id/transcribing" element={<Transcribing />} />
          <Route path="/documents/:id/summary" element={<Summary />} />
          <Route path="/upload" element={<Upload />} />
          <Route path="/recording" element={<Recording />} />
          <Route path="/glossary" element={<Glossary />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/settings/model-setup" element={<ModelSetup />} />
          <Route path="/settings/prompts" element={<PromptList />} />
          <Route path="/settings/prompts/:id" element={<PromptDetail />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </Suspense>
    </Shell>
  )
}
