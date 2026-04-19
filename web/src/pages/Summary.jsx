import { useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import api from '@/api/client'
import { qk } from '@/lib/queryKeys'
import { useToast } from '@/hooks/use-toast'
import { useSettings } from '@/hooks/useSettings'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'

// SSE events from POST /summarize:
//   ai_waiting:   { elapsed_s }
//   prompt_ready: { copyText }  — no-AI-CLI path; treated as error state
//   complete:     { summaryPath }
//   error:        { message, canRetry }

const PROMPT_PREFIX = '다음 전사 내용을 한국어 회의록으로 정리해주세요'

function InitialOrErrorBlock({ message, onStart, onCopy, tone }) {
  return (
    <Card>
      <CardContent className="py-8 space-y-4">
        <p className={`text-sm text-center ${tone === 'error' ? 'text-destructive' : 'text-muted-foreground'}`}>
          {message}
        </p>
        <div className="flex justify-center gap-2">
          <Button onClick={onStart}>AI로 요약</Button>
          <Button variant="outline" onClick={onCopy}>요약 프롬프트 복사</Button>
        </div>
      </CardContent>
    </Card>
  )
}

export default function Summary() {
  const { id } = useParams()
  const qc = useQueryClient()
  const { toast } = useToast()
  const [aiWaiting, setAiWaiting] = useState(null)
  const [streamError, setStreamError] = useState(null)
  const [inProgress, setInProgress] = useState(false)
  const [activeTab, setActiveTab] = useState(null)
  const disposeRef = useRef(null)
  const serverPromptRef = useRef(null)

  const { data: settings } = useSettings()
  const preferredAi = settings?.preferredAi ?? 'auto'

  const transcriptQ = useQuery({
    queryKey: qk.transcript(id),
    queryFn: () => api.getTranscript(id),
    enabled: !!id,
    retry: false,
  })

  const summaryQ = useQuery({
    queryKey: qk.summary(id),
    queryFn: () => api.getSummary(id),
    enabled: !!id,
    retry: false,
  })

  // Set default tab once summaryQ resolves (one-shot).
  useEffect(() => {
    if (activeTab !== null) return
    if (summaryQ.isSuccess && summaryQ.data?.content) setActiveTab('summary')
    else if (summaryQ.isError || summaryQ.isSuccess) setActiveTab('transcript')
  }, [summaryQ.isSuccess, summaryQ.isError, activeTab])

  // Cleanup SSE stream on unmount.
  useEffect(() => {
    return () => { if (disposeRef.current) disposeRef.current() }
  }, [])

  const summaryState =
    inProgress ? 'in_progress' :
    (summaryQ.isSuccess && summaryQ.data?.content) ? 'completed' :
    streamError ? 'error' :
    'initial'

  const onStartSummarize = () => {
    if (inProgress) return
    setStreamError(null)
    setAiWaiting(null)
    setInProgress(true)
    setActiveTab('summary') // one-shot tab switch on click (AC-5)
    disposeRef.current = api.postSse(
      `/api/documents/${encodeURIComponent(id)}/summarize`,
      { ai: preferredAi },
      {
        ai_waiting: (evt) => setAiWaiting(evt.payload?.elapsed_s ?? 0),
        complete: () => {
          setInProgress(false)
          qc.invalidateQueries({ queryKey: qk.document(id) })
          qc.invalidateQueries({ queryKey: qk.summary(id) })
          qc.invalidateQueries({ queryKey: qk.documents() })
          // Intentionally no setActiveTab — respects user tab choice (AC-6).
        },
        error: (evt) => {
          setInProgress(false)
          const p = evt.payload || {}
          setStreamError({
            message: p.message || '오류가 발생했습니다',
            canRetry: Boolean(p.canRetry),
          })
        },
        prompt_ready: (evt) => {
          // No AI CLI on server: backend emits prompt_ready then closes stream.
          // Route to error branch so both buttons are visible (AC-8).
          setInProgress(false)
          serverPromptRef.current = evt.payload?.copyText ?? null
          setStreamError({
            message: '요약 생성에 실패했어요. 다시 시도하거나 프롬프트를 복사해서 직접 요약해보세요.',
            canRetry: true,
          })
        },
        transportError: (err) => {
          setInProgress(false)
          if (err?.status === 409) {
            toast({ description: '이미 다른 탭에서 요약 중입니다' })
            return
          }
          setStreamError({ message: '요청 중 오류가 발생했습니다', canRetry: true })
        },
      },
    )
  }

  const onCopyPrompt = async () => {
    const transcript = transcriptQ.data?.content || ''
    // If the server has supplied an authoritative prompt via prompt_ready, use it
    // even when the transcript query is still loading or empty.
    if (!transcript && !serverPromptRef.current) {
      toast({ description: '전사를 불러오는 중입니다', variant: 'destructive' })
      return
    }
    let text
    try {
      const result = await api.getPrompt(id)
      text = result?.prompt ?? null
    } catch {
      text = null
    }
    if (!text) {
      text = serverPromptRef.current || `${PROMPT_PREFIX}\n\n---\n전사:\n${transcript}`
    }
    try {
      await navigator.clipboard.writeText(text)
      toast({ description: '복사되었습니다' })
    } catch {
      toast({ description: '클립보드 접근 권한이 필요합니다', variant: 'destructive' })
    }
  }

  return (
    <section className="mx-auto max-w-3xl p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">요약</h1>
        <Button variant="outline" asChild>
          <Link to="/documents">목록</Link>
        </Button>
      </div>

      <Tabs value={activeTab ?? 'transcript'} onValueChange={setActiveTab}>
        <TabsList>
          <TabsTrigger value="transcript">원본</TabsTrigger>
          <TabsTrigger value="summary">요약</TabsTrigger>
        </TabsList>
        <TabsContent value="transcript" forceMount className="data-[state=inactive]:hidden">
          <pre className="whitespace-pre-wrap rounded bg-muted p-4 text-sm">
            {transcriptQ.data?.content || ''}
          </pre>
        </TabsContent>
        <TabsContent value="summary" forceMount className="data-[state=inactive]:hidden">
          {summaryState === 'in_progress' && (
            <Card>
              <CardContent className="py-8 text-center text-sm text-muted-foreground">
                AI로 요약을 만들고 있어요...
                {aiWaiting !== null && <p className="mt-2 text-xs">경과 {aiWaiting}초</p>}
              </CardContent>
            </Card>
          )}
          {summaryState === 'completed' && (
            <Card>
              <CardContent className="prose prose-sm max-w-none py-6 dark:prose-invert">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {summaryQ.data?.content || ''}
                </ReactMarkdown>
              </CardContent>
            </Card>
          )}
          {summaryState === 'initial' && (
            <InitialOrErrorBlock
              message="아직 요약이 생성되지 않았어요"
              onStart={onStartSummarize}
              onCopy={onCopyPrompt}
            />
          )}
          {summaryState === 'error' && (
            <InitialOrErrorBlock
              message={streamError?.message || '요약 생성에 실패했어요.'}
              onStart={onStartSummarize}
              onCopy={onCopyPrompt}
              tone="error"
            />
          )}
        </TabsContent>
      </Tabs>
    </section>
  )
}
