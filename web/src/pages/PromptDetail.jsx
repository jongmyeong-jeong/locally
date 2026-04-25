import { useEffect, useRef, useState } from 'react'
import {
  Link,
  useBlocker,
  useNavigate,
  useParams,
} from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import api from '@/api/client'
import { mk, qk } from '@/lib/queryKeys'
import { useToast } from '@/hooks/use-toast'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'

export default function PromptDetail() {
  const { id } = useParams()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const { toast } = useToast()
  const numericId = Number(id)

  const promptsQ = useQuery({
    queryKey: qk.prompts(),
    queryFn: api.listPrompts,
  })
  const preset = promptsQ.data?.find((p) => p.id === numericId)

  const [name, setName] = useState('')
  const [template, setTemplate] = useState('')
  const [initialName, setInitialName] = useState('')
  const [initialTemplate, setInitialTemplate] = useState('')
  const [pendingDelete, setPendingDelete] = useState(false)
  const nameRef = useRef(null)

  // 프리셋 로드 시 폼 초기화 (한 번만, 즉 preset이 객체로 바뀌는 시점)
  useEffect(() => {
    if (preset) {
      setName(preset.name)
      setTemplate(preset.template)
      setInitialName(preset.name)
      setInitialTemplate(preset.template)
    }
  }, [preset])

  const isDirty = name !== initialName || template !== initialTemplate

  // D4 — 내부 navigation 가드
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      isDirty && currentLocation.pathname !== nextLocation.pathname,
  )

  // D4 — 새로고침/탭닫기 가드
  useEffect(() => {
    if (!isDirty) return
    const handler = (ev) => {
      ev.preventDefault()
      ev.returnValue = ''
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [isDirty])

  const updateMut = useMutation({
    mutationKey: [mk.updatePrompt],
    mutationFn: ({ id: pid, name: n, template: t }) =>
      api.updatePrompt(pid, { name: n, template: t }),
    onSuccess: (_data, variables) => {
      // initial 동기화 — 저장 후 isDirty=false
      setInitialName(variables.name)
      setInitialTemplate(variables.template)
      qc.invalidateQueries({ queryKey: qk.prompts() })
      toast({ description: '저장되었습니다' })
      navigate('/settings/prompts')
    },
    onError: (err) => {
      toast({
        description: err?.message || '저장에 실패했습니다',
        variant: 'destructive',
      })
    },
  })

  const deleteMut = useMutation({
    mutationKey: [mk.deletePrompt],
    mutationFn: (pid) => api.deletePrompt(pid),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.prompts() })
      toast({ description: '삭제되었습니다' })
      navigate('/settings/prompts')
    },
    onError: (err) => {
      toast({
        description: err?.message || '삭제에 실패했습니다',
        variant: 'destructive',
      })
    },
  })

  const onSave = () => {
    if (name.trim() === '') {
      toast({ description: '이름을 입력해주세요' })
      nameRef.current?.focus()
      return
    }
    updateMut.mutate({ id: numericId, name, template })
  }

  if (promptsQ.isLoading) {
    return (
      <section className="mx-auto max-w-3xl p-6">
        <p className="text-sm text-muted-foreground">불러오는 중...</p>
      </section>
    )
  }

  if (!preset) {
    return (
      <section className="mx-auto max-w-3xl p-6 space-y-4">
        <p className="text-sm text-muted-foreground">
          프리셋을 찾을 수 없습니다.
        </p>
        <Button variant="outline" asChild>
          <Link to="/settings/prompts">← 목록</Link>
        </Button>
      </section>
    )
  }

  return (
    <section className="mx-auto max-w-3xl p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">프리셋 편집</h1>
        <Button variant="outline" asChild>
          <Link to="/settings/prompts">← 목록</Link>
        </Button>
      </div>

      <div className="space-y-2">
        <label className="text-sm font-medium" htmlFor="prompt-name">
          이름
        </label>
        <Input
          id="prompt-name"
          ref={nameRef}
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="예: 회의록"
        />
      </div>

      <div className="space-y-2">
        <label className="text-sm font-medium" htmlFor="prompt-template">
          템플릿
        </label>
        <Textarea
          id="prompt-template"
          value={template}
          onChange={(e) => setTemplate(e.target.value)}
          rows={15}
          className="font-mono text-sm"
        />
        <p className="text-xs text-muted-foreground">
          사용 가능한 변수:{' '}
          <code className="rounded bg-muted px-1 py-0.5">{'{transcript}'}</code>
          ,{' '}
          <code className="rounded bg-muted px-1 py-0.5">{'{title}'}</code>
          ,{' '}
          <code className="rounded bg-muted px-1 py-0.5">{'{glossary}'}</code>
        </p>
      </div>

      <div className="flex gap-2">
        <Button onClick={onSave} disabled={updateMut.isPending}>
          저장
        </Button>
        <Button
          variant="destructive"
          onClick={() => setPendingDelete(true)}
          disabled={deleteMut.isPending}
        >
          삭제
        </Button>
      </div>

      {/* D3: 삭제 확인 Dialog */}
      <Dialog
        open={pendingDelete}
        onOpenChange={(o) => {
          if (!o) setPendingDelete(false)
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>프리셋을 삭제할까요?</DialogTitle>
            <DialogDescription>
              이 작업은 되돌릴 수 없습니다.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setPendingDelete(false)}>
              취소
            </Button>
            <Button
              variant="destructive"
              disabled={deleteMut.isPending}
              onClick={() => {
                setPendingDelete(false)
                deleteMut.mutate(numericId)
              }}
            >
              삭제
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* D4: unsaved-changes blocker Dialog */}
      <Dialog
        open={blocker?.state === 'blocked'}
        onOpenChange={(o) => {
          if (!o && blocker?.state === 'blocked') blocker.reset()
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>저장하지 않은 변경이 있습니다</DialogTitle>
            <DialogDescription>
              정말 나가시겠어요? 변경 내용이 사라집니다.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => blocker?.state === 'blocked' && blocker.reset()}
            >
              계속 편집
            </Button>
            <Button
              variant="destructive"
              onClick={() =>
                blocker?.state === 'blocked' && blocker.proceed()
              }
            >
              나가기
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  )
}
