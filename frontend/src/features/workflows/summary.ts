import { formatDuration, truncate } from '../../lib/format'
import type { ActionMeta } from '../../lib/workflow/clientCheck'
import { describeCondition } from '../../lib/workflow/condition'
import { asRecord, type WfNode } from '../../lib/workflow/types'

/** One line that says what a step does, for the card on the canvas and the list view. */
export function nodeSummary(n: WfNode, actions?: Map<string, ActionMeta>): string {
  const c = asRecord(n.config)
  switch (n.type) {
    case 'agent': return c.task ? truncate(String(c.task), 60) : 'No task yet'
    case 'action': return c.action ? String(c.action) : 'No action chosen'
    case 'condition': return c.if ? truncate(describeCondition(asRecord(c.if)), 60) : 'No condition yet'
    case 'approval': return c.message ? truncate(String(c.message), 60) : 'Asks a person to approve'
    case 'wait_event': return c.event ? `Waits for ${String(c.event)}` : 'No event chosen'
    case 'timer': return typeof c.seconds === 'number' ? `Waits ${formatDuration(c.seconds * 1000)}` : 'No duration yet'
    case 'end': return c.result === 'failure' ? 'Finishes as failed' : 'Finishes as success'
    default: return n.type
  }
}

export function actionOf(n: WfNode, actions?: Map<string, ActionMeta>): ActionMeta | undefined {
  return n.type === 'action' ? actions?.get(String(asRecord(n.config).action)) : undefined
}
