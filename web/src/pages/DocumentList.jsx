import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import api from '@/api/client'
import { qk, mk } from '@/lib/queryKeys'
import { useToast } from '@/hooks/use-toast'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'

// Status → Korean label for the list view.
const STATUS_LABEL = {
  recording: '진행 중',
  pending: '대기 중',
  transcribing: '텍스트로 변환 중',
  transcribed: '텍스트로 변환 완료',
  summarizing: '요약 중',
  completed: '완료',
  error: '오류',
}

const DATE_FORMATTER = new Intl.DateTimeFormat('ko-KR', {
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
})

function StatusBadge({ status }) {
  const label = STATUS_LABEL[status] || status
  // N1 recovery: `status:"recording"` docs get a distinct "진행 중" badge.
  const recording = status === 'recording'
  return (
    <span
      className={
        'inline-flex items-center rounded px-2 py-0.5 text-xs ' +
        (recording
          ? 'bg-destructive/10 text-destructive border border-destructive/30'
          : 'bg-muted text-muted-foreground')
      }
    >
      {label}
    </span>
  )
}

function formatDate(iso) {
  if (!iso) return ''
  try {
    return DATE_FORMATTER.format(new Date(iso))
  } catch {
    return iso
  }
}

export default function DocumentList() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const { toast } = useToast()
  const [pendingDelete, setPendingDelete] = useState(null)

  const { data: docs = [], isLoading } = useQuery({
    queryKey: qk.documents(),
    queryFn: api.listDocuments,
  })

  const deleteMut = useMutation({
    mutationKey: [mk.deleteDocument],
    mutationFn: (id) => api.deleteDocument(id, { deleteAudio: true }),
    onMutate: async (id) => {
      await qc.cancelQueries({ queryKey: qk.documents() })
      const previousDocs = qc.getQueryData(qk.documents())
      qc.setQueryData(qk.documents(), (current = []) =>
        current.filter((doc) => doc.id !== id),
      )
      return { previousDocs }
    },
    onSuccess: (_, id) => {
      qc.removeQueries({ queryKey: qk.document(id), exact: true })
      toast({ description: '삭제되었습니다' })
    },
    onError: (err, _id, context) => {
      if (context?.previousDocs) {
        qc.setQueryData(qk.documents(), context.previousDocs)
      }
      toast({
        description: err?.message || '삭제에 실패했습니다',
        variant: 'destructive',
      })
    },
  })

  const onRowClick = (doc) => {
    if (doc.status === 'completed' || doc.status === 'transcribed') {
      navigate(`/documents/${doc.id}/summary`)
    } else if (doc.status === 'transcribing' || doc.status === 'summarizing') {
      navigate(`/documents/${doc.id}/transcribing`)
    } else {
      navigate(`/documents/${doc.id}/summary`)
    }
  }

  return (
    <section className="mx-auto max-w-3xl p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">기록</h1>
        <div className="flex gap-2">
          <Button variant="outline" asChild>
            <Link to="/upload">업로드</Link>
          </Button>
          <Button asChild>
            <Link to="/recording">녹음 시작</Link>
          </Button>
        </div>
      </div>

      {isLoading ? (
        <p className="text-sm text-muted-foreground">불러오는 중...</p>
      ) : docs.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center text-muted-foreground">
            녹음을 시작하거나 파일을 업로드하세요.
          </CardContent>
        </Card>
      ) : (
        <ul className="space-y-2">
          {docs.map((doc) => (
            <li key={doc.id}>
              <Card className="hover:shadow-md transition-shadow">
                <CardHeader className="flex flex-row items-start justify-between gap-4 pb-2">
                  <button
                    type="button"
                    onClick={() => onRowClick(doc)}
                    className="flex-1 text-left focus:outline-none"
                  >
                    <CardTitle className="text-base">{doc.title}</CardTitle>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {formatDate(doc.createdAt)}
                    </p>
                  </button>
                  <div className="flex items-center gap-2">
                    <StatusBadge status={doc.status} />
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setPendingDelete(doc)}
                    >
                      삭제
                    </Button>
                  </div>
                </CardHeader>
              </Card>
            </li>
          ))}
        </ul>
      )}

      <Dialog
        open={pendingDelete !== null}
        onOpenChange={(o) => {
          if (!o) setPendingDelete(null)
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>기록을 삭제할까요?</DialogTitle>
            <DialogDescription>
              {pendingDelete?.title} · 원본 음성 파일도 함께 삭제됩니다. 이
              작업은 되돌릴 수 없습니다.
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
