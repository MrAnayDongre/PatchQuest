/**
 * The workflow definition exactly as the backend stores it (docs/workflows.md). The editor works on this plain
 * object and never on a private model; every field it does not know about is carried through unchanged.
 */
export type NodeType = 'agent' | 'action' | 'condition' | 'approval' | 'wait_event' | 'timer' | 'end'

export const NODE_TYPES: NodeType[] = ['agent', 'action', 'condition', 'approval', 'wait_event', 'timer', 'end']

export type Json = string | number | boolean | null | Json[] | { [key: string]: Json }

export interface WfNode {
  id: string
  type: string
  config?: Record<string, unknown>
  max_visits?: number
  [extra: string]: unknown
}

export interface WfEdge {
  from: string
  to: string
  when?: string
  [extra: string]: unknown
}

export interface WfVariable {
  default?: unknown
  required?: boolean
  [extra: string]: unknown
}

export interface WfTrigger {
  type?: string
  filter?: Record<string, unknown>
  [extra: string]: unknown
}

export interface WfDef {
  schema?: number
  name?: string
  description?: string
  trigger?: WfTrigger
  variables?: Record<string, WfVariable>
  nodes: WfNode[]
  edges: WfEdge[]
  [extra: string]: unknown
}

export interface Problem {
  code: string
  message: string
  node?: string | null
  /** 'server' problems are authoritative; 'local' ones are instant hints. */
  source?: 'server' | 'local'
}

export interface Pos {
  x: number
  y: number
}

export interface Rect {
  x: number
  y: number
  w: number
  h: number
}

export function isNodeType(t: string): t is NodeType {
  return (NODE_TYPES as string[]).includes(t)
}

export function asRecord(v: unknown): Record<string, unknown> {
  return v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : {}
}

/** Deep copy of plain JSON data. */
export function clone<T>(v: T): T {
  return v === undefined ? v : (JSON.parse(JSON.stringify(v)) as T)
}
