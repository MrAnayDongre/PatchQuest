import { normalize } from './model'
import type { WfDef } from './types'

export function toJson(def: WfDef): string {
  return `${JSON.stringify(def, null, 2)}\n`
}

// ------------------------------------------------------------------ YAML (the subset this schema needs)
const PLAIN = /^[A-Za-z_][A-Za-z0-9_.\-/]*$/

function scalar(v: unknown): string {
  if (typeof v === 'string') return PLAIN.test(v) && !/^(true|false|null|yes|no|on|off)$/i.test(v) ? v : JSON.stringify(v)
  return JSON.stringify(v)
}

function key(k: string): string {
  return PLAIN.test(k) || /^[A-Za-z0-9_.\-]+$/.test(k) ? (/^\d+$/.test(k) ? JSON.stringify(k) : k) : JSON.stringify(k)
}

function emit(value: unknown, indent: number): string[] {
  const pad = ' '.repeat(indent)
  if (Array.isArray(value)) {
    if (!value.length) return [`${pad}[]`]
    const lines: string[] = []
    for (const item of value) {
      if (item && typeof item === 'object' && !(Array.isArray(item) && !item.length) && !(!Array.isArray(item) && !Object.keys(item).length)) {
        const inner = emit(item, indent + 2)
        lines.push(`${pad}- ${inner[0].trimStart()}`, ...inner.slice(1))
      } else lines.push(`${pad}- ${Array.isArray(item) ? '[]' : item && typeof item === 'object' ? '{}' : scalar(item)}`)
    }
    return lines
  }
  if (value && typeof value === 'object') {
    const entries = Object.entries(value as Record<string, unknown>)
    if (!entries.length) return [`${pad}{}`]
    const lines: string[] = []
    for (const [k, v] of entries) {
      if (Array.isArray(v) ? v.length : v && typeof v === 'object' && Object.keys(v).length) {
        lines.push(`${pad}${key(k)}:`, ...emit(v, indent + 2))
      } else lines.push(`${pad}${key(k)}: ${Array.isArray(v) ? '[]' : v && typeof v === 'object' ? '{}' : scalar(v)}`)
    }
    return lines
  }
  return [`${pad}${scalar(value)}`]
}

export function toYaml(def: WfDef): string {
  return `${emit(def, 0).join('\n')}\n`
}

// ---- parsing
class YamlError extends Error {}

interface Line {
  indent: number
  text: string
  no: number
}

function stripComment(s: string): string {
  let q: string | null = null
  for (let i = 0; i < s.length; i++) {
    const c = s[i]
    if (q) {
      if (c === '\\' && q === '"') i++
      else if (c === q) q = null
    } else if (c === '"' || c === "'") q = c
    else if (c === '#' && (i === 0 || /\s/.test(s[i - 1]))) return s.slice(0, i)
  }
  return s
}

function parseScalar(raw: string): unknown {
  const s = raw.trim()
  if (s === '') return null
  if (s.startsWith('"')) return JSON.parse(s)
  if (s.startsWith("'")) return s.slice(1, -1).replace(/''/g, "'")
  if (s === 'true') return true
  if (s === 'false') return false
  if (s === 'null' || s === '~') return null
  if (/^-?\d+(\.\d+)?([eE][-+]?\d+)?$/.test(s)) return Number(s)
  return s
}

/** Flow collections: {a: 1, b: [x, y]} with plain or quoted scalars. */
function parseFlow(src: string): unknown {
  let i = 0
  const ws = () => {
    while (i < src.length && /\s/.test(src[i])) i++
  }
  const quoted = (): string => {
    const q = src[i]
    let j = i + 1
    while (j < src.length && src[j] !== q) j += src[j] === '\\' && q === '"' ? 2 : 1
    const str = src.slice(i, j + 1)
    i = j + 1
    return q === '"' ? (JSON.parse(str) as string) : str.slice(1, -1)
  }
  const value = (): unknown => {
    ws()
    if (src[i] === '{') {
      i++
      const obj: Record<string, unknown> = {}
      ws()
      if (src[i] === '}') return i++, obj
      for (;;) {
        ws()
        const k = src[i] === '"' || src[i] === "'" ? quoted() : plainUntil(':,}').trim()
        ws()
        if (src[i] !== ':') throw new YamlError('expected ":" in a { } value')
        i++
        obj[k] = value()
        ws()
        if (src[i] === ',') {
          i++
          continue
        }
        if (src[i] === '}') return i++, obj
        throw new YamlError('expected "," or "}"')
      }
    }
    if (src[i] === '[') {
      i++
      const arr: unknown[] = []
      ws()
      if (src[i] === ']') return i++, arr
      for (;;) {
        arr.push(value())
        ws()
        if (src[i] === ',') {
          i++
          continue
        }
        if (src[i] === ']') return i++, arr
        throw new YamlError('expected "," or "]"')
      }
    }
    if (src[i] === '"' || src[i] === "'") return quoted()
    return parseScalar(plainUntil(',]}'))
  }
  const plainUntil = (stops: string): string => {
    const start = i
    while (i < src.length && !stops.includes(src[i])) i++
    return src.slice(start, i)
  }
  const v = value()
  ws()
  if (i < src.length) throw new YamlError('unexpected text after a { } or [ ] value')
  return v
}

function inlineValue(raw: string): unknown {
  const s = raw.trim()
  if (s.startsWith('{') || s.startsWith('[')) return parseFlow(s)
  return parseScalar(s)
}

function splitKey(text: string): [string, string] | null {
  let q: string | null = null
  for (let i = 0; i < text.length; i++) {
    const c = text[i]
    if (q) {
      if (c === '\\' && q === '"') i++
      else if (c === q) q = null
    } else if (c === '"' || c === "'") q = c
    else if (c === ':' && (i === text.length - 1 || text[i + 1] === ' ')) {
      const k = text.slice(0, i).trim()
      return [k.startsWith('"') ? (JSON.parse(k) as string) : k, text.slice(i + 1)]
    } else if (c === '{' || c === '[') return null
  }
  return null
}

function parseBlock(lines: Line[], pos: { i: number }, indent: number): unknown {
  const first = lines[pos.i]
  if (first.text.startsWith('- ') || first.text === '-') {
    const arr: unknown[] = []
    while (pos.i < lines.length && lines[pos.i].indent === indent && (lines[pos.i].text.startsWith('- ') || lines[pos.i].text === '-')) {
      const line = lines[pos.i]
      const rest = line.text === '-' ? '' : line.text.slice(2)
      if (rest === '') {
        pos.i++
        arr.push(pos.i < lines.length && lines[pos.i].indent > indent ? parseBlock(lines, pos, lines[pos.i].indent) : null)
      } else if (splitKey(rest)) {
        // a mapping starting on the dash line: re-read it as a block indented past the dash
        lines[pos.i] = { indent: indent + 2, text: rest, no: line.no }
        arr.push(parseBlock(lines, pos, indent + 2))
      } else {
        pos.i++
        arr.push(inlineValue(rest))
      }
    }
    return arr
  }
  const obj: Record<string, unknown> = {}
  while (pos.i < lines.length && lines[pos.i].indent === indent) {
    const line = lines[pos.i]
    const kv = splitKey(line.text)
    if (!kv) throw new YamlError(`line ${line.no}: expected "name: value"`)
    const [k, rest] = kv
    pos.i++
    if (rest.trim() === '') {
      obj[k] = pos.i < lines.length && lines[pos.i].indent > indent ? parseBlock(lines, pos, lines[pos.i].indent) : pos.i < lines.length && lines[pos.i].indent === indent && lines[pos.i].text.startsWith('- ') ? parseBlock(lines, pos, indent) : null
    } else obj[k] = inlineValue(rest)
  }
  if (pos.i < lines.length && lines[pos.i].indent > indent) throw new YamlError(`line ${lines[pos.i].no}: unexpected indentation`)
  return obj
}

export function parseYaml(text: string): unknown {
  const lines: Line[] = []
  text.replace(/\r\n/g, '\n').split('\n').forEach((raw, idx) => {
    if (/^\t/.test(raw)) throw new YamlError(`line ${idx + 1}: use spaces, not tabs`)
    const noComment = stripComment(raw).replace(/\s+$/, '')
    if (!noComment.trim() || noComment.trim() === '---') return
    lines.push({ indent: noComment.length - noComment.trimStart().length, text: noComment.trim(), no: idx + 1 })
  })
  if (!lines.length) throw new YamlError('the file is empty')
  const pos = { i: 0 }
  const result = parseBlock(lines, pos, lines[0].indent)
  if (pos.i < lines.length) throw new YamlError(`line ${lines[pos.i].no}: unexpected content`)
  return result
}

export type ImportResult = { ok: true; def: WfDef } | { ok: false; error: string }

/** JSON first (it is the reference format), then YAML. Anything that is not an object is refused. */
export function parseImport(text: string): ImportResult {
  const trimmed = text.trim()
  if (!trimmed) return { ok: false, error: 'The file is empty.' }
  let value: unknown
  try {
    value = JSON.parse(trimmed)
  } catch (jsonErr) {
    if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
      try {
        value = parseYaml(trimmed) // flow-style YAML also starts with a brace
      } catch {
        return { ok: false, error: `That isn't valid JSON: ${(jsonErr as Error).message}` }
      }
    } else {
      try {
        value = parseYaml(trimmed)
      } catch (e) {
        return { ok: false, error: `That isn't valid JSON or YAML: ${(e as Error).message}` }
      }
    }
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) return { ok: false, error: 'A workflow file must describe an object with a name, trigger, nodes and edges.' }
  return { ok: true, def: normalize(value) }
}
