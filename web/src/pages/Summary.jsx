import { Suspense, lazy, useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'

import api from '@/api/client'
import { qk } from '@/lib/queryKeys'
import { useToast } from '@/hooks/use-toast'
import { useSettings } from '@/hooks/useSettings'
import { useFinalizePoller } from '@/hooks/useSSE'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'

// SSE events from POST /summarize:
//   ai_waiting:   { elapsed_s }
//   prompt_ready: { copyText }  — no-AI-CLI path; treated as error state
//   complete:     { summaryPath }
//   error:        { message, canRetry }

const SummaryMarkdown = lazy(() => import('@/components/SummaryMarkdown'))
const PROMPT_PREFIX = '다음 전사 내용을 한국어 회의록으로 정리해주세요'

function PromptSelect({ prompts, value, onChange }) {
  if (!prompts || prompts.length === 0) return null
  return (
    <select
      value={value ?? ''}
      onChange={(e) => onChange(Number(e.target.value))}
      className="h-9 rounded border bg-background px-3 text-sm"
    >
      {prompts.map((p) => (
        <option key={p.id} value={p.id}>
          {p.name || '(이름 없음)'}
        </option>
      ))}
    </select>
  )
}

function InitialOrErrorBlock({ message, onStart, onCopy, tone, prompts, selectedPromptId, onSelectPrompt }) {
  return (
    <Card>
      <CardContent className="py-8 space-y-4">
        <p className={`text-sm text-center ${tone === 'error' ? 'text-destructive' : 'text-muted-foreground'}`}>
          {message}
        </p>
        <div className="flex flex-wrap items-center justify-center gap-2">
          <PromptSelect prompts={prompts} value={selectedPromptId} onChange={onSelectPrompt} />
          <Button onClick={onStart}>AI로 요약</Button>
          <Button variant="outline" onClick={onCopy}>요약 프롬프트 복사</Button>
        </div>
      </CardContent>
    </Card>
  )
}

function FailedTranscriptionBlock({ id }) {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const { toast } = useToast()
  const [deleting, setDeleting] = useState(false)

  const handleDelete = async () => {
    if (deleting) return
    setDeleting(true)
    try {
      await api.deleteNote(id, { deleteAudio: true })
      qc.invalidateQueries({ queryKey: qk.notes() })
      qc.removeQueries({ queryKey: qk.note(id), exact: true })
      toast({ description: '삭제되었습니다' })
      navigate('/notes')
    } catch (err) {
      setDeleting(false)
      toast({
        description: err?.message || '삭제에 실패했습니다',
        variant: 'destructive',
      })
    }
  }

  return (
    <Card>
      <CardContent className="py-8 space-y-4">
        <p className="text-sm text-center text-destructive">
          전사가 실패했습니다
        </p>
        <div className="flex justify-center">
          <Button variant="destructive" disabled={deleting} onClick={handleDelete}>
            삭제
          </Button>
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
  const [finalizePollIssue, setFinalizePollIssue] = useState(null)
  const [inProgress, setInProgress] = useState(false)
  const [activeTab, setActiveTab] = useState(null)
  const disposeRef = useRef(null)
  const serverPromptRef = useRef(null)

  const { data: settings } = useSettings()
  const preferredAi = settings?.preferredAi ?? 'auto'

  const noteQ = useQuery({
    queryKey: qk.note(id),
    queryFn: () => api.getNote(id),
    enabled: !!id,
    retry: false,
  })

  const isFailed = noteQ.data?.status === 'transcription_failed'
  const isServerFinalizing = noteQ.data?.status === 'finalizing'

  const disposeSummarizeStream = useCallback(() => {
    if (disposeRef.current) {
      disposeRef.current()
      disposeRef.current = null
    }
  }, [])

  // 폴링: 서버에서 finalizing 상태인 노트는 transcribed/transcription_failed가 될 때까지 폴링
  const fetchNoteForPoll = useCallback(() => api.getNote(id), [id])
  useFinalizePoller({
    enabled: isServerFinalizing && !finalizePollIssue,
    fetchNote: fetchNoteForPoll,
    onSettled: (note) => {
      setFinalizePollIssue(null)
      qc.setQueryData(qk.note(id), note)
      qc.invalidateQueries({ queryKey: qk.notes() })
    },
    onTimeout: () => {
      setFinalizePollIssue({
        message: '처리가 예상보다 오래 걸리고 있어요. 노트 상태를 다시 확인해 주세요.',
      })
    },
    onError: () => {
      setFinalizePollIssue({
        message: '노트 상태를 다시 확인해 주세요.',
      })
    },
  })

  const summaryQ = useQuery({
    queryKey: qk.summary(id),
    queryFn: () => api.getSummary(id),
    enabled: !!id && noteQ.isSuccess && !isServerFinalizing,
    retry: false,
  })

  const shouldFetchTranscript =
    !!id &&
    (
      activeTab === 'transcript' ||
      summaryQ.isError ||
      (summaryQ.isSuccess && !summaryQ.data?.content)
    )

  const transcriptQ = useQuery({
    queryKey: qk.transcript(id),
    queryFn: () => api.getTranscript(id),
    enabled: noteQ.isSuccess && !isServerFinalizing && shouldFetchTranscript,
    retry: false,
  })

  const promptsQ = useQuery({
    queryKey: qk.prompts(),
    queryFn: api.listPrompts,
  })

  const [selectedPromptId, setSelectedPromptId] = useState(null)

  // E2: 첫 로드 시 목록 맨 위 프리셋을 초기값으로
  useEffect(() => {
    if (selectedPromptId == null && promptsQ.data?.length > 0) {
      setSelectedPromptId(promptsQ.data[0].id)
    }
  }, [promptsQ.data, selectedPromptId])

  // Set default tab once summaryQ resolves (one-shot).
  useEffect(() => {
    if (isServerFinalizing) return
    if (activeTab !== null) return
    if (summaryQ.isSuccess && summaryQ.data?.content) setActiveTab('summary')
    else if (summaryQ.isError || summaryQ.isSuccess) setActiveTab('transcript')
  }, [summaryQ.isSuccess, summaryQ.isError, activeTab, isServerFinalizing])

  // Cleanup SSE stream on unmount.
  useEffect(() => {
    return disposeSummarizeStream
  }, [disposeSummarizeStream])

  useEffect(() => {
    if (!isServerFinalizing) {
      setFinalizePollIssue(null)
    }
  }, [isServerFinalizing])

  const summaryState =
    inProgress ? 'in_progress' :
    (summaryQ.isSuccess && summaryQ.data?.content) ? 'completed' :
    streamError ? 'error' :
    'initial'

  const onStartSummarize = () => {
    if (inProgress) return
    disposeSummarizeStream()
    setStreamError(null)
    setAiWaiting(null)
    setInProgress(true)
    serverPromptRef.current = null
    setActiveTab('summary') // one-shot tab switch on click (AC-5)
    disposeRef.current = api.postSse(
      `/api/notes/${encodeURIComponent(id)}/summarize`,
      { ai: preferredAi, prompt_id: selectedPromptId ?? undefined },
      {
        ai_waiting: (evt) => setAiWaiting(evt.payload?.elapsed_s ?? 0),
        complete: () => {
          disposeSummarizeStream()
          setInProgress(false)
          qc.invalidateQueries({ queryKey: qk.note(id) })
          qc.invalidateQueries({ queryKey: qk.summary(id) })
          qc.invalidateQueries({ queryKey: qk.notes() })
          // Intentionally no setActiveTab — respects user tab choice (AC-6).
        },
        error: (evt) => {
          disposeSummarizeStream()
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
          disposeSummarizeStream()
          setInProgress(false)
          serverPromptRef.current = evt.payload?.copyText ?? null
          setStreamError({
            message: '요약 생성에 실패했어요. 다시 시도하거나 프롬프트를 복사해서 직접 요약해보세요.',
            canRetry: true,
          })
        },
        transportError: (err) => {
          disposeSummarizeStream()
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
    if (serverPromptRef.current) {
      try {
        await navigator.clipboard.writeText(serverPromptRef.current)
        toast({ description: '복사되었습니다' })
      } catch {
        toast({ description: '클립보드 접근 권한이 필요합니다', variant: 'destructive' })
      }
      return
    }

    const transcript = transcriptQ.data?.content || ''
    let text
    try {
      const result = await api.getPrompt(id, selectedPromptId)
      text = result?.prompt ?? null
    } catch {
      text = null
    }
    if (!text) {
      if (!transcript) {
        toast({ description: '전사를 불러오는 중입니다', variant: 'destructive' })
        return
      }
      text = serverPromptRef.current || `${PROMPT_PREFIX}\n\n---\n전사:\n${transcript}`
    }
    try {
      await navigator.clipboard.writeText(text)
      toast({ description: '복사되었습니다' })
    } catch {
      toast({ description: '클립보드 접근 권한이 필요합니다', variant: 'destructive' })
    }
  }

  const onRetryFinalizePoll = () => {
    setFinalizePollIssue(null)
    qc.invalidateQueries({ queryKey: qk.note(id) })
  }

  return (
    <section className="mx-auto max-w-3xl p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">요약</h1>
        <Button variant="outline" asChild>
          <Link to="/notes">목록</Link>
        </Button>
      </div>

      {isServerFinalizing && finalizePollIssue ? (
        <Card>
          <CardContent className="py-8 space-y-4">
            <p className="text-sm text-center text-destructive">
              {finalizePollIssue.message}
            </p>
            <div className="flex justify-center">
              <Button variant="outline" onClick={onRetryFinalizePoll}>다시 확인</Button>
            </div>
          </CardContent>
        </Card>
      ) : isServerFinalizing ? (
        <Card>
          <CardContent className="py-8 text-center text-sm text-muted-foreground">
            <p>녹음을 처리하고 있어요. 잠시 기다려 주세요...</p>
          </CardContent>
        </Card>
      ) : isFailed ? (
        <FailedTranscriptionBlock id={id} /> // AC8
      ) : (
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
                  <Suspense fallback={<p className="text-sm text-muted-foreground">요약을 불러오는 중...</p>}>
                    <SummaryMarkdown content={summaryQ.data?.content || ''} />
                  </Suspense>
                </CardContent>
                <CardContent className="flex flex-wrap items-center justify-center gap-2 border-t pt-4">
                  <PromptSelect
                    prompts={promptsQ.data}
                    value={selectedPromptId}
                    onChange={setSelectedPromptId}
                  />
                  <Button variant="outline" onClick={onStartSummarize}>
                    다른 프리셋으로 다시 요약
                  </Button>
                </CardContent>
              </Card>
            )}
            {summaryState === 'initial' && (
              <InitialOrErrorBlock
                message="아직 요약이 생성되지 않았어요"
                onStart={onStartSummarize}
                onCopy={onCopyPrompt}
                prompts={promptsQ.data}
                selectedPromptId={selectedPromptId}
                onSelectPrompt={setSelectedPromptId}
              />
            )}
            {summaryState === 'error' && (
              <InitialOrErrorBlock
                message={streamError?.message || '요약 생성에 실패했어요.'}
                onStart={onStartSummarize}
                onCopy={onCopyPrompt}
                tone="error"
                prompts={promptsQ.data}
                selectedPromptId={selectedPromptId}
                onSelectPrompt={setSelectedPromptId}
              />
            )}
          </TabsContent>
        </Tabs>
      )}
    </section>
  )
}
