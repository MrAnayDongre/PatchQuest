import { describe, expect, it } from 'vitest'
import { extractRunId, shortRunId } from './runId'

describe('runId helpers', () => {
  it('extracts run id from PatchQuest calendar description', () => {
    const id = 'c4eaa6d8-f816-4642-9f07-3785472d17f9'
    const text = `PatchQuest run ${id} for: Evaluate quality of my code`
    expect(extractRunId(text)).toBe(id)
  })

  it('returns null when no uuid present', () => {
    expect(extractRunId('no run here')).toBeNull()
  })

  it('shortens run id for display', () => {
    expect(shortRunId('c4eaa6d8-f816-4642-9f07-3785472d17f9')).toBe('c4eaa6d8…')
  })
})
