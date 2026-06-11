/** Extract PatchQuest run UUID from title/description text. */
const RUN_ID_RE = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i

export function extractRunId(...sources: (string | null | undefined)[]): string | null {
  for (const src of sources) {
    if (!src) continue
    const match = src.match(RUN_ID_RE)
    if (match) return match[0]
  }
  return null
}

export function shortRunId(runId: string): string {
  return `${runId.slice(0, 8)}…`
}

export function formatRunWhen(iso: string | null | undefined): string {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}
