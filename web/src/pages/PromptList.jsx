import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
} from '@dnd-kit/core'
import {
  SortableContext,
  arrayMove,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'

import api from '@/api/client'
import { mk, qk } from '@/lib/queryKeys'
import { useToast } from '@/hooks/use-toast'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'

function SortableItem({ preset, onClickBody, onAskDelete }) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: preset.id })
  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  }
  return (
    <div
      ref={setNodeRef}
      style={style}
      className="flex items-center gap-2 rounded border bg-background p-3"
    >
      <button
        type="button"
        {...attributes}
        {...listeners}
        aria-label="드래그하여 순서 변경"
        className="cursor-grab select-none px-2 text-muted-foreground hover:text-foreground"
      >
        ≡
      </button>
      <button
        type="button"
        onClick={() => onClickBody(preset.id)}
        className="flex-1 text-left focus:outline-none"
      >
        {preset.name || (
          <span className="italic text-muted-foreground">(이름 없음)</span>
        )}
      </button>
      <Button
        variant="ghost"
        size="sm"
        onClick={() => onAskDelete(preset)}
      >
        삭제
      </Button>
    </div>
  )
}

export default function PromptList() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const { toast } = useToast()

  const promptsQ = useQuery({
    queryKey: qk.prompts(),
    queryFn: api.listPrompts,
  })

  const [items, setItems] = useState([])
  const prevItemsRef = useRef([])
  const [pendingDelete, setPendingDelete] = useState(null)

  // 서버 응답을 로컬 상태로 미러 (드래그 시 optimistic 업데이트용)
  useEffect(() => {
    if (promptsQ.data) setItems(promptsQ.data)
  }, [promptsQ.data])

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    }),
  )

  const reorderMut = useMutation({
    mutationKey: [mk.reorderPrompts],
    mutationFn: (order) => api.reorderPrompts(order),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.prompts() }),
    onError: () => {
      // C2 rollback
      setItems(prevItemsRef.current)
      toast({
        description: '순서 저장에 실패했습니다',
        variant: 'destructive',
      })
    },
  })

  const onDragEnd = (event) => {
    const { active, over } = event
    if (!over || active.id === over.id) return
    const oldIndex = items.findIndex((p) => p.id === active.id)
    const newIndex = items.findIndex((p) => p.id === over.id)
    if (oldIndex < 0 || newIndex < 0) return
    const newItems = arrayMove(items, oldIndex, newIndex)
    prevItemsRef.current = items
    setItems(newItems)
    reorderMut.mutate(newItems.map((p) => p.id))
  }

  const createMut = useMutation({
    mutationKey: [mk.createPrompt],
    mutationFn: () => api.createPrompt({ name: '', template: '' }),
    onSuccess: (created) => {
      qc.invalidateQueries({ queryKey: qk.prompts() })
      navigate(`/settings/prompts/${created.id}`)
    },
    onError: (err) => {
      toast({
        description: err?.message || '프리셋 생성에 실패했습니다',
        variant: 'destructive',
      })
    },
  })

  const deleteMut = useMutation({
    mutationKey: [mk.deletePrompt],
    mutationFn: (id) => api.deletePrompt(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.prompts() })
      toast({ description: '삭제되었습니다' })
    },
    onError: (err) => {
      toast({
        description: err?.message || '삭제에 실패했습니다',
        variant: 'destructive',
      })
    },
  })

  const isEmpty = !promptsQ.isLoading && items.length === 0

  return (
    <section className="mx-auto max-w-3xl p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">요약 프롬프트</h1>
        <Button variant="outline" asChild>
          <Link to="/settings">← 설정</Link>
        </Button>
      </div>

      {promptsQ.isLoading ? (
        <p className="text-sm text-muted-foreground">불러오는 중...</p>
      ) : isEmpty ? (
        <Card>
          <CardContent className="py-12 text-center">
            <p className="text-sm text-muted-foreground">
              프리셋이 없습니다. 새로 만들어 주세요.
            </p>
            <Button
              className="mt-4"
              onClick={() => createMut.mutate()}
              disabled={createMut.isPending}
            >
              새 프리셋
            </Button>
          </CardContent>
        </Card>
      ) : (
        <>
          <DndContext
            sensors={sensors}
            collisionDetection={closestCenter}
            onDragEnd={onDragEnd}
          >
            <SortableContext
              items={items.map((p) => p.id)}
              strategy={verticalListSortingStrategy}
            >
              <ul className="space-y-2">
                {items.map((p) => (
                  <li key={p.id}>
                    <SortableItem
                      preset={p}
                      onClickBody={(id) =>
                        navigate(`/settings/prompts/${id}`)
                      }
                      onAskDelete={setPendingDelete}
                    />
                  </li>
                ))}
              </ul>
            </SortableContext>
          </DndContext>
          <Button
            onClick={() => createMut.mutate()}
            disabled={createMut.isPending}
          >
            + 새 프리셋
          </Button>
        </>
      )}

      <Dialog
        open={pendingDelete !== null}
        onOpenChange={(o) => {
          if (!o) setPendingDelete(null)
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>프리셋을 삭제할까요?</DialogTitle>
            <DialogDescription>
              {pendingDelete?.name || '(이름 없음)'} · 이 작업은 되돌릴 수
              없습니다.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setPendingDelete(null)}>
              취소
            </Button>
            <Button
              variant="destructive"
              disabled={deleteMut.isPending}
              onClick={() => {
                if (!pendingDelete) return
                const id = pendingDelete.id
                setPendingDelete(null)
                deleteMut.mutate(id)
              }}
            >
              삭제
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  )
}
