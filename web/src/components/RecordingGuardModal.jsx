import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'

// AC2/AC3/AC5: 녹음 중 이탈 시도를 인터셉트하는 확인 모달.
export default function RecordingGuardModal({
  open,
  onLeave,
  onStay,
  busy = false,
}) {
  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o && !busy) onStay()
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>녹음을 중단할까요?</DialogTitle>
          <DialogDescription>
            녹음된 내용이 삭제됩니다.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={onStay} disabled={busy}>
            계속 녹음
          </Button>
          <Button variant="destructive" onClick={onLeave} disabled={busy}>
            {busy ? '정리 중...' : '나가기'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
