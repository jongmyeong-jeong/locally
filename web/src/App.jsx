import { Suspense, lazy, useEffect, useState } from 'react'
import { Route, Routes } from 'react-router-dom'
import useSystemInfo from '@/hooks/useSystemInfo'
import ErrorModal from '@/components/ErrorModal'

const Recording = lazy(() => import('@/pages/Recording'))

function RouteLoading() {
  return (
    <section style={{ padding: '24px', fontSize: '14px', color: '#4d4d4d' }}>
      화면을 불러오는 중...
    </section>
  )
}

// Shows a one-time modal when GROQ_API_KEY is not configured.
// User can dismiss and still see Recording page, but the start button will
// enforce the same check when clicked (done in Recording.jsx).
function GroqKeyGate({ children }) {
  const { data: sysInfo, isLoading } = useSystemInfo()
  const [dismissed, setDismissed] = useState(false)

  const keyMissing = !isLoading && sysInfo && sysInfo.groqConfigured === false

  if (isLoading) {
    return (
      <section style={{ padding: '24px', fontSize: '14px', color: '#4d4d4d' }}>
        불러오는 중...
      </section>
    )
  }

  return (
    <>
      {children}
      <ErrorModal
        open={keyMissing && !dismissed}
        errorType="api_key_missing"
        onClose={() => setDismissed(true)}
      />
    </>
  )
}

export default function App() {
  return (
    <GroqKeyGate>
      <Suspense fallback={<RouteLoading />}>
        <Routes>
          <Route path="/" element={<Recording />} />
          <Route path="*" element={<Recording />} />
        </Routes>
      </Suspense>
    </GroqKeyGate>
  )
}
