import type { Tone } from './eventCopy'

/** Plain-words answer to "did this change my repository, and if not, why not?" */
export function appliedExplanation(input: {
  status: string | null
  outcome: string | null
  verdict: string | null
  rejectionReason: string | null
  dryRun?: boolean
}): { tone: Tone; title: string; body: string } {
  const { status, outcome, verdict, rejectionReason } = input
  if (outcome === 'applied') return { tone: 'success', title: 'Applied to your repository', body: 'These are the files PatchQuest wrote.' }
  if (status === 'running' || status === 'created' || status === 'waiting_approval' || status === 'cancel_requested') {
    return { tone: 'info', title: 'Nothing applied yet', body: 'Your repository is only touched once, at the very end, after the checks pass.' }
  }
  if (outcome === 'read_only') return { tone: 'muted', title: 'Nothing to apply', body: 'This was a read-only task, so no changes were proposed.' }
  if (outcome === 'no_changes') return { tone: 'muted', title: 'Nothing to apply', body: 'The repository already satisfies the task.' }
  if (outcome === 'no_patch') return { tone: 'warning', title: 'No patch was produced', body: 'The model did not propose any edits for a task that needs them. Try a more specific task or another model.' }
  if (outcome === 'conflict') return { tone: 'danger', title: 'Your repository was not changed', body: rejectionReason ?? 'The patch could not be written safely, for example because a file changed underneath it.' }
  if (outcome === 'rejected') {
    const why = rejectionReason ?? (verdict ? `The checks ended with: ${verdict.replace('_', ' ')}.` : 'The patch did not meet the bar for applying.')
    return { tone: 'warning', title: 'Proposed, but not applied', body: `${why} The diff is kept under Proposed for review.` }
  }
  if (status === 'failed' || status === 'cancelled' || status === 'interrupted') {
    return { tone: 'muted', title: 'Nothing applied', body: 'The run stopped before it reached the point where changes are written.' }
  }
  return { tone: 'muted', title: 'Nothing applied', body: rejectionReason ?? 'No changes were written to your repository.' }
}
