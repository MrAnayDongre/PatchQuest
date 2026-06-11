import type { RunEvent } from './types'

/** All SSE event types emitted by the PatchQuest backend. */
export const SSE_EVENT_TYPES = [
  'run_created',
  'phase_started',
  'phase_completed',
  'phase_failed',
  'phase_skipped',
  'phase_blocked',
  'run_completed',
  'run_failed',
  'permission_required',
  'permission_approved',
  'permission_rejected',
  'repo_scan_started',
  'repo_scan_completed',
  'plan_created',
  'context_selected',
  'analysis_generated',
  'patch_proposed',
  'patch_applied',
  'patch_rejected',
  'secret_detected',
  'tests_started',
  'tests_completed',
  'security_scan_completed',
  'report_generated',
  'ping',
] as const

function parseEventData(raw: string): RunEvent | null {
  try {
    const data = JSON.parse(raw)
    return data as RunEvent
  } catch {
    return null
  }
}

export function subscribeToRunEvents(
  runId: string,
  onEvent: (event: RunEvent) => void,
  onError?: (error: Event) => void,
): () => void {
  const url = `/api/runs/${runId}/stream`
  const eventSource = new EventSource(url)

  const dispatch = (raw: string) => {
    const data = parseEventData(raw)
    if (data && data.type !== 'ping') onEvent(data)
  }

  eventSource.onmessage = (e) => dispatch(e.data)

  for (const type of SSE_EVENT_TYPES) {
    eventSource.addEventListener(type, (e) => {
      dispatch((e as MessageEvent).data)
    })
  }

  eventSource.onerror = (e) => {
    if (onError) onError(e)
  }

  return () => eventSource.close()
}
