import { Link } from 'react-router-dom'

export default function Settings() {
  return (
    <section className="mx-auto max-w-3xl p-6 space-y-4">
      <h1 className="text-2xl font-semibold">설정</h1>
      <div className="grid gap-3">
        <Link
          to="/settings/model-setup"
          className="block rounded border p-4 transition-colors hover:bg-accent"
        >
          <div className="font-medium">모델 선택</div>
          <div className="mt-1 text-sm text-muted-foreground">
            요약에 사용할 AI CLI 선택
          </div>
        </Link>
        <Link
          to="/settings/prompts"
          className="block rounded border p-4 transition-colors hover:bg-accent"
        >
          <div className="font-medium">요약 프롬프트</div>
          <div className="mt-1 text-sm text-muted-foreground">
            요약 프리셋 관리 (생성·편집·순서 변경)
          </div>
        </Link>
      </div>
    </section>
  )
}
