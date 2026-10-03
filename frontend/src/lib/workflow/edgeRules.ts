import { asRecord, type WfDef, type WfNode } from './types'

export interface LabelOption {
  /** null means an unlabelled edge ("always"). */
  value: string | null
  label: string
}

export const LABEL_COPY: Record<string, string> = {
  true: 'If true',
  false: 'If false',
  approved: 'Approved',
  denied: 'Denied',
  timeout: 'No answer in time',
  failed: 'On failure',
}

export function labelText(when: string | null | undefined): string {
  return when ? LABEL_COPY[when] ?? when : ''
}

/** The connection labels a node may use on its outgoing edges, given its type and config. */
export function labelOptions(node: WfNode): LabelOption[] {
  switch (node.type) {
    case 'condition':
      return [
        { value: 'true', label: LABEL_COPY.true },
        { value: 'false', label: LABEL_COPY.false },
      ]
    case 'approval':
      return [
        { value: null, label: 'Always' },
        { value: 'approved', label: LABEL_COPY.approved },
        { value: 'denied', label: LABEL_COPY.denied },
        { value: 'timeout', label: LABEL_COPY.timeout },
      ]
    case 'agent':
    case 'action': {
      const opts: LabelOption[] = [{ value: null, label: 'Next' }]
      if (asRecord(node.config).on_failure === 'continue') opts.push({ value: 'failed', label: LABEL_COPY.failed })
      return opts
    }
    case 'end':
      return []
    default:
      return [{ value: null, label: 'Next' }]
  }
}

export type Verdict = { ok: true } | { ok: false; reason: string }

/** Can this connection be added? Mirrors the server's rules so the canvas can refuse early and say why. */
export function canConnect(def: WfDef, from: string, to: string, when: string | null): Verdict {
  const source = def.nodes.find(n => n.id === from)
  const target = def.nodes.find(n => n.id === to)
  if (!source || !target) return { ok: false, reason: 'Both ends of a connection must be steps in this workflow.' }
  if (source.type === 'end') return { ok: false, reason: 'An end step finishes the workflow, so nothing can follow it.' }
  const options = labelOptions(source)
  if (!options.some(o => o.value === when)) {
    if (when === 'failed') return { ok: false, reason: 'Turn on "continue if this step fails" in its settings to use an on-failure connection.' }
    return { ok: false, reason: `A ${source.type.replace('_', ' ')} step can't use the "${labelText(when) || 'plain'}" connection.` }
  }
  const outgoing = def.edges.filter(e => e.from === from)
  if (outgoing.some(e => e.to === to && (e.when ?? null) === when)) return { ok: false, reason: 'These two steps are already connected that way.' }
  if ((source.type === 'condition' || source.type === 'approval') && when !== null && outgoing.some(e => e.when === when)) {
    return { ok: false, reason: `"${labelText(when)}" already leads somewhere. Remove that connection first.` }
  }
  return { ok: true }
}

/** The label a freshly drawn connection should get: the first unused branch for branching steps. */
export function nextLabel(def: WfDef, from: string): string | null {
  const node = def.nodes.find(n => n.id === from)
  if (!node) return null
  const used = new Set(def.edges.filter(e => e.from === from).map(e => e.when ?? null))
  if (node.type === 'condition') return ['true', 'false'].find(l => !used.has(l)) ?? 'true'
  if (node.type === 'approval') return ['approved', 'denied'].find(l => !used.has(l)) ?? null
  return null
}

/** Does this connection need the user to choose a label (more than one sensible choice)? */
export function needsLabelChoice(def: WfDef, from: string): boolean {
  const node = def.nodes.find(n => n.id === from)
  return !!node && labelOptions(node).length > 1
}
