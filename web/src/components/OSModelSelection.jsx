import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Progress } from '@/components/ui/progress'
import { cn } from '@/lib/utils'

function formatEta(seconds) {
  if (seconds == null) return '남은 시간 계산 중...'
  if (seconds < 60) return `${seconds}초 남음`
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return `${m}분 ${s}초 남음`
}
export default function OSModelSelection({
  catalog,
  selectedId,
  onSelect,
  onDownload,
  progress,
  error,
  modelReady = false,
}) {
  if (!catalog || catalog.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        이 OS에서 사용할 수 있는 모델이 없습니다.
      </p>
    )
  }
  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2">
        {catalog.map((m) => {
          const active = m.id === selectedId
          return (
            <button
              key={m.id}
              type="button"
              onClick={() => onSelect(m.id)}
              className="text-left focus:outline-none"
            >
              <Card
                className={cn(
                  'transition-shadow',
                  active && 'ring-2 ring-ring',
                )}
              >
                <CardContent className="p-4">
                  <div className="flex items-center justify-between">
                    <div className="font-medium">{m.displayName}</div>
                    <div className="flex items-center gap-2">
                      {modelReady && (
                        <span className="rounded border border-green-600 px-2 py-0.5 text-xs text-green-600">
                          설치됨
                        </span>
                      )}
                      <span className="rounded border px-2 py-0.5 text-xs uppercase text-muted-foreground">
                        {m.format}
                      </span>
                    </div>
                  </div>
                  <div className="mt-1 text-sm text-muted-foreground">
                    {m.size_mb} MB
                  </div>
                </CardContent>
              </Card>
            </button>
          )
        })}
      </div>
      {selectedId && (
        <div className="space-y-2">
          <Button
            onClick={() => onDownload(selectedId)}
            disabled={modelReady || (progress !== null && !error)}
          >
            {modelReady
              ? '이미 설치됨'
              : progress !== null && !error
              ? '다운로드 중...'
              : '모델 다운로드'}
          </Button>
          {progress && (
            <div className="space-y-1">
              <Progress value={Math.round((progress.percent || 0) * 100)} />
              <div className="text-xs text-muted-foreground">
                {progress.downloaded_mb} / {progress.total_mb} MB (
                {Math.round((progress.percent || 0) * 100)}%)
              </div>
              {(progress.speed_mbps > 0 || progress.eta_seconds != null) && (
                <div className="text-xs text-muted-foreground">
                  {progress.speed_mbps > 0 && `${progress.speed_mbps.toFixed(1)} MB/s · `}
                  {formatEta(progress.eta_seconds)}
                </div>
              )}
            </div>
          )}
          {error && (
            <div className="rounded border border-destructive bg-destructive/10 p-3 text-sm text-destructive">
              <p>{error.message}</p>
              {error.canRetry && (
                <Button
                  size="sm"
                  variant="outline"
                  className="mt-2"
                  onClick={() => onDownload(selectedId)}
                >
                  다시 시도
                </Button>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
