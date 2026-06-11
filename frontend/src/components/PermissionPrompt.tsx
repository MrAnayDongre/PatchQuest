import PixelButton from './PixelButton'

interface PermissionPromptProps {
  command: string
  reason: string
  approvalId: string
  onApprove: (id: string) => void
  onReject: (id: string) => void
}

export default function PermissionPrompt({ command, reason, approvalId, onApprove, onReject }: PermissionPromptProps) {
  return (
    <div className="permission-prompt" role="alertdialog" aria-labelledby="perm-title">
      <div className="permission-prompt__title" id="perm-title">⚠ Permission Required</div>
      <div className="permission-prompt__command">
        <code>{command}</code>
      </div>
      <div className="permission-prompt__reason">
        Reason: {reason}
      </div>
      <div className="permission-prompt__actions">
        <PixelButton variant="danger" onClick={() => onReject(approvalId)}>
          Reject
        </PixelButton>
        <PixelButton variant="primary" onClick={() => onApprove(approvalId)}>
          Approve
        </PixelButton>
      </div>
    </div>
  )
}
