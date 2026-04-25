import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'

import api from '@/api/client'
import { qk } from '@/lib/queryKeys'
import { useToast } from '@/hooks/use-toast'
import useSystemInfo from '@/hooks/useSystemInfo'
import { useSettings, useUpdateSettings } from '@/hooks/useSettings'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import OSModelSelection from '@/components/OSModelSelection'

// Plan §4.2 POST /api/models/download → SSE:
//   progress: { percent, downloaded_mb, total_mb, speed_mbps }
//   complete: { modelId, path }
//   error:    { message, canRetry }
// `.incomplete/` sentinel is reflected server-side by modelReady=false.
export default function ModelSetup() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const { toast } = useToast()
  const [selectedId, setSelectedId] = useState(null)
  const [progress, setProgress] = useState(null)
  const [error, setError] = useState(null)
  const disposeRef = useRef(null)

  const { data: sysInfo, isLoading } = useSystemInfo()
  const { data: settings } = useSettings()
  const updateSettings = useUpdateSettings()

  const preferredAi = settings?.preferredAi ?? 'auto'
  const aiAvail = sysInfo?.aiAvailable ?? { claude: false, codex: false }

  const AI_OPTIONS = [
    { value: 'claude', label: 'Claude Code', key: 'claude' },
    { value: 'codex', label: 'Codex CLI', key: 'codex' },
    { value: 'none', label: '없음 (직접 복사)', key: null },
  ]

  const effectiveAi =
    preferredAi === 'auto'
      ? (aiAvail.claude ? 'claude' : aiAvail.codex ? 'codex' : 'none')
      : preferredAi

  const catalog = sysInfo?.modelCatalog || []

  useEffect(() => {
    if (!selectedId && catalog.length > 0) {
      setSelectedId(catalog[0].id)
    }
  }, [catalog, selectedId])

  useEffect(() => {
    // Close the active SSE stream on unmount.
    return () => disposeRef.current?.()
  }, [])

  const onDownload = (modelId) => {
    disposeRef.current?.()
    setError(null)
    setProgress({ percent: 0, downloaded_mb: 0, total_mb: 0 })
    const d = api.postSse(
      '/api/models/download',
      { modelId },
      {
        progress: (evt) => {
          const p = evt.payload || {}
          setProgress({
            percent: p.percent || 0,
            downloaded_mb: p.downloaded_mb || 0,
            total_mb: p.total_mb || 0,
            speed_mbps: p.speed_mbps ?? 0,
            eta_seconds: p.eta_seconds ?? null,
          })
        },
        complete: () => {
          setProgress(null)
          setError(null)
          toast({ description: '모델 다운로드 완료' })
          qc.invalidateQueries({ queryKey: qk.systemInfo() })
          navigate('/notes')
        },
        error: (evt) => {
          const p = evt.payload || {}
          setError({
            message: p.message || '다운로드에 실패했습니다',
            canRetry: Boolean(p.canRetry),
          })
          setProgress(null)
        },
        transportError: () => {
          setError({ message: '연결이 끊어졌습니다', canRetry: true })
          setProgress(null)
        },
      },
    )
    disposeRef.current = d
  }

  if (isLoading) {
    return (
      <section className="mx-auto max-w-3xl p-6 text-sm text-muted-foreground">
        불러오는 중...
      </section>
    )
  }

  return (
    <section className="mx-auto max-w-3xl p-6 space-y-4">
      <h1 className="text-2xl font-semibold">{sysInfo?.modelReady === false ? '모델 다운로드' : '모델 설정'}</h1>
      {sysInfo && (
        <p className="text-sm text-muted-foreground">
          {sysInfo.os} · {sysInfo.arch} ·
          {sysInfo.modelReady ? ' 준비됨' : ' 설치되지 않음'}
        </p>
      )}

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">음성 인식 모델 선택</CardTitle>
        </CardHeader>
        <CardContent>
          <OSModelSelection
            catalog={catalog}
            selectedId={selectedId}
            onSelect={setSelectedId}
            onDownload={onDownload}
            progress={progress}
            error={error}
            modelReady={sysInfo?.modelReady ?? false}
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">요약 AI 선택</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          <p className="text-xs text-muted-foreground mb-3">
            요약에 사용할 AI를 선택해 주세요. 설치된 AI만 활성화돼요.
          </p>
          <div className="flex flex-col gap-2">
            {AI_OPTIONS.map(({ value, label, key }) => {
              const installed = key === null ? true : aiAvail[key]
              const selected = effectiveAi === value
              return (
                <button
                  key={value}
                  disabled={!installed}
                  onClick={() => installed && updateSettings.mutate({ preferredAi: value })}
                  className={[
                    'flex items-center justify-between rounded-md border px-4 py-2 text-sm transition-colors text-left',
                    selected
                      ? 'border-primary bg-primary/10 font-medium'
                      : 'border-border',
                    installed
                      ? 'hover:bg-muted cursor-pointer'
                      : 'opacity-40 cursor-not-allowed',
                  ].join(' ')}
                >
                  <span>{label}</span>
                  <span className="text-xs text-muted-foreground">
                    {key === null ? '' : installed ? '설치됨' : '미설치'}
                  </span>
                </button>
              )
            })}
          </div>
        </CardContent>
      </Card>
    </section>
  )
}
