import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import api from '@/api/client'
import { qk, mk } from '@/lib/queryKeys'
import { useToast } from '@/hooks/use-toast'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'

// Plan §4.2 + backend /app/server.py _ALLOWED_EXT.
const ALLOWED_EXT = ['m4a', 'wav', 'mp3', 'webm', 'aac', 'ogg', 'flac', 'm4r']

function getExt(name) {
  const i = name.lastIndexOf('.')
  return i >= 0 ? name.slice(i + 1).toLowerCase() : ''
}

export default function Upload() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const { toast } = useToast()
  const inputRef = useRef(null)
  const [dragOver, setDragOver] = useState(false)
  const [file, setFile] = useState(null)
  const [title, setTitle] = useState('')

  const createMut = useMutation({
    mutationKey: [mk.createDocument],
    mutationFn: async ({ file, title }) => {
      const form = new FormData()
      form.append('file', file)
      if (title) form.append('title', title)
      return api.createDocumentMultipart(form)
    },
    onSuccess: (doc) => {
      qc.invalidateQueries({ queryKey: qk.documents() })
      // Transcribing.jsx opens the POST-SSE stream and drives progress.
      navigate(`/documents/${doc.id}/transcribing`)
    },
    onError: (err) => {
      // AC-5 415 handling: backend returns `{error, ext}`.
      if (err?.status === 415) {
        const ext = err.body?.ext || ''
        toast({
          description: `지원하지 않는 파일 형식${ext ? ` (.${ext})` : ''}`,
          variant: 'destructive',
        })
        return
      }
      toast({
        description: err?.message || '업로드에 실패했습니다',
        variant: 'destructive',
      })
    },
  })

  const pickFile = (f) => {
    if (!f) return
    const ext = getExt(f.name)
    if (!ALLOWED_EXT.includes(ext)) {
      toast({
        description: `지원하지 않는 파일 형식 (.${ext || '없음'})`,
        variant: 'destructive',
      })
      return
    }
    setFile(f)
    if (!title) setTitle(f.name.replace(/\.[^.]+$/, ''))
  }

  const onDrop = (e) => {
    e.preventDefault()
    setDragOver(false)
    const f = e.dataTransfer?.files?.[0]
    pickFile(f)
  }

  const submit = () => {
    if (!file) return
    createMut.mutate({ file, title: title.trim() || undefined })
  }

  return (
    <section className="mx-auto max-w-3xl p-6 space-y-4">
      <h1 className="text-2xl font-semibold">업로드</h1>
      <p className="text-sm text-muted-foreground">
        지원 형식: {ALLOWED_EXT.map((e) => `.${e}`).join(', ')}
      </p>

      <Card>
        <CardContent
          onDragOver={(e) => {
            e.preventDefault()
            setDragOver(true)
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={onDrop}
          className={cn(
            'flex min-h-[200px] cursor-pointer flex-col items-center justify-center gap-3 rounded-xl border-2 border-dashed p-8 text-center transition-colors',
            dragOver
              ? 'border-ring bg-accent/30'
              : 'border-muted-foreground/30',
          )}
          onClick={() => inputRef.current?.click()}
        >
          <input
            ref={inputRef}
            type="file"
            accept={ALLOWED_EXT.map((e) => `.${e}`).join(',')}
            className="hidden"
            onChange={(e) => pickFile(e.target.files?.[0])}
          />
          {file ? (
            <>
              <p className="font-medium">{file.name}</p>
              <p className="text-xs text-muted-foreground">
                {(file.size / (1024 * 1024)).toFixed(1)} MB
              </p>
            </>
          ) : (
            <>
              <p className="text-sm">파일을 이곳에 끌어다 놓거나 클릭해서 선택</p>
              <Button type="button" variant="outline" size="sm">
                파일 선택
              </Button>
            </>
          )}
        </CardContent>
      </Card>

      <div className="space-y-2">
        <label className="text-sm font-medium">제목 (선택)</label>
        <Input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="제목을 입력하세요"
        />
      </div>

      <div className="flex justify-end gap-2">
        <Button variant="outline" onClick={() => navigate('/documents')}>
          취소
        </Button>
        <Button
          onClick={submit}
          disabled={!file || createMut.isPending}
        >
          {createMut.isPending ? '업로드 중...' : '텍스트로 변환'}
        </Button>
      </div>
    </section>
  )
}
