import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'

import api from '@/api/client'
import { qk } from '@/lib/queryKeys'
import { useToast } from '@/hooks/use-toast'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Progress } from '@/components/ui/progress'

// Plan §4.2 transcribe SSE payload:
//   progress: { percent: [0..1], segment_count, elapsed_sec }
//   complete: { status, transcriptPath }
//   error:    { message, canRetry }
export default function Transcribing() {
  const { id } = useParams()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const { toast } = useToast()
  const [percent, setPercent] = useState(0)
  const [segCount, setSegCount] = useState(0)
  const [elapsed, setElapsed] = useState(0)
  const [error, setError] = useState(null)
  const [done, setDone] = useState(false)

  useEffect(() => {
    if (!id) return
    let disposed = false
    const dispose = api.postSse(
      `/api/notes/${encodeURIComponent(id)}/transcribe`,
      undefined,
      {
        progress: (evt) => {
          const p = evt.payload || {}
          setPercent(Math.max(0, Math.min(1, p.percent || 0)))
          setSegCount(p.segment_count || 0)
          setElapsed(p.elapsed_sec || 0)
        },
        complete: () => {
          if (disposed) return
          setDone(true)
          qc.invalidateQueries({ queryKey: qk.notes() })
          qc.invalidateQueries({ queryKey: qk.note(id) })
          // Auto-start summarize on completion per Phase E brief.
          navigate(`/notes/${id}/summary`, { replace: true })
        },
        error: (evt) => {
          if (disposed) return
          setError(evt.payload || { message: '오류가 발생했습니다', canRetry: true })
        },
      },
    )
    return () => {
      disposed = true
      dispose()
    }
  }, [id, navigate, qc])

  const onCancel = async () => {
    try {
      await api.cancelJob(id)
    } catch (err) {
      toast({
        description: err?.message || '취소에 실패했습니다',
        variant: 'destructive',
      })
    }
  }

  return (
    <section className="mx-auto max-w-3xl p-6 space-y-4">
      <h1 className="text-2xl font-semibold">전사 중</h1>
      <Card>
        <CardHeader>
          <CardTitle className="text-base">음성을 텍스트로 변환하고 있어요</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <Progress value={Math.round(percent * 100)} />
          <div className="flex justify-between text-sm text-muted-foreground">
            <span>{Math.round(percent * 100)}%</span>
            <span>
              세그먼트 {segCount}개 · {elapsed.toFixed(1)}초
            </span>
          </div>
          {error && (
            <div className="rounded border border-destructive bg-destructive/10 p-3 text-sm text-destructive">
              <p>{error.message}</p>
              <div className="mt-2 flex gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => navigate('/notes')}
                >
                  목록으로
                </Button>
                {error.canRetry && (
                  <Button
                    size="sm"
                    onClick={() => window.location.reload()}
                  >
                    다시 시도
                  </Button>
                )}
              </div>
            </div>
          )}
          {!error && !done && (
            <div className="flex justify-end">
              <Button variant="outline" size="sm" onClick={onCancel}>
                취소
              </Button>
            </div>
          )}
        </CardContent>
      </Card>
    </section>
  )
}
