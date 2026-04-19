import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import api from '@/api/client'
import { qk, mk } from '@/lib/queryKeys'
import { useToast } from '@/hooks/use-toast'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Textarea } from '@/components/ui/textarea'

// AC-8: GET /api/glossary returns string[]; PUT /api/glossary accepts string[].
// The payload MUST be a JSON array of strings (not an object).
export default function Glossary() {
  const qc = useQueryClient()
  const { toast } = useToast()
  const [text, setText] = useState('')

  const { data = [], isLoading } = useQuery({
    queryKey: qk.glossary(),
    queryFn: api.getGlossary,
  })

  // Sync server state → textarea on load.
  useEffect(() => {
    if (Array.isArray(data)) setText(data.join('\n'))
  }, [data])

  const saveMut = useMutation({
    mutationKey: [mk.saveGlossary],
    mutationFn: async (terms) => api.putGlossary(terms),
    onSuccess: (_res, terms) => {
      qc.setQueryData(qk.glossary(), terms)
      toast({ description: '저장되었습니다' })
    },
    onError: (err) => {
      toast({
        description: err?.message || '저장에 실패했습니다',
        variant: 'destructive',
      })
    },
  })

  const onSave = () => {
    const terms = text
      .split('\n')
      .map((t) => t.trim())
      .filter((t) => t.length > 0)
    // JSON array of strings — api.putGlossary already stringifies this.
    saveMut.mutate(terms)
  }

  return (
    <section className="mx-auto max-w-3xl p-6 space-y-4">
      <h1 className="text-2xl font-semibold">용어집</h1>
      <p className="text-sm text-muted-foreground">
        고유명사나 자주 쓰는 용어를 적어두면 요약할 때 정확도가 높아져요.
      </p>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">용어 목록</CardTitle>
        </CardHeader>
        <CardContent>
          <Textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            disabled={isLoading}
            rows={12}
            placeholder="용어를 입력하고 Enter를 눌러주세요."
            className="font-mono text-sm"
          />
        </CardContent>
      </Card>

      <div className="flex justify-end">
        <Button onClick={onSave} disabled={saveMut.isPending}>
          {saveMut.isPending ? '저장 중...' : '저장'}
        </Button>
      </div>
    </section>
  )
}
