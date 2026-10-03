export class ApiError extends Error {
  readonly status: number
  readonly code: string | null
  /** Server-provided extra data, e.g. the resume plan on a 409. */
  readonly data: Record<string, unknown> | null

  constructor(status: number, message: string, code: string | null = null, data: Record<string, unknown> | null = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.data = data
  }
}

function isObj(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === 'object' && !Array.isArray(v)
}

/** Turn any failed response body into an ApiError. Handles FastAPI's `detail` as string, object or list. */
export function normalizeError(status: number, body: unknown, statusText = ''): ApiError {
  const detail = isObj(body) ? body.detail : undefined
  if (typeof detail === 'string') return new ApiError(status, detail)
  if (Array.isArray(detail)) {
    const msg = detail
      .map(d => {
        if (!isObj(d)) return String(d)
        const loc = Array.isArray(d.loc) ? d.loc.filter(x => x !== 'body').join('.') : ''
        return `${loc ? `${loc}: ` : ''}${typeof d.msg === 'string' ? d.msg : 'invalid value'}`
      })
      .join('; ')
    return new ApiError(status, msg || 'The request was invalid', 'validation')
  }
  if (isObj(detail)) {
    const code = typeof detail.code === 'string' ? detail.code : null
    const message = typeof detail.message === 'string' ? detail.message : statusText || `Request failed (${status})`
    return new ApiError(status, message, code, detail)
  }
  return new ApiError(status, statusText || `Request failed (${status})`)
}

export interface FriendlyError {
  title: string
  message: string
  hint?: string
}

/** What happened, what is affected, what to do next, in plain words. */
export function friendlyError(err: unknown, subject = 'that'): FriendlyError {
  if (!(err instanceof ApiError)) {
    const msg = err instanceof Error ? err.message : String(err)
    return { title: 'Something went wrong', message: msg || 'An unexpected error occurred.', hint: 'Try again in a moment.' }
  }
  const { status, code } = err
  if (status === 0) {
    return {
      title: "Can't reach the PatchQuest server",
      message: 'The request never got an answer, so nothing was changed.',
      hint: 'Check that the backend is running, then try again.',
    }
  }
  switch (code) {
    case 'approval_already_decided':
      return {
        title: 'Already answered',
        message: 'This request was decided a moment ago, possibly by someone else. The first decision stands.',
        hint: 'The run will continue based on that decision.',
      }
    case 'approval_not_found':
      return {
        title: 'Approval no longer exists',
        message: 'This request has expired or belongs to a different run, so your decision was not recorded.',
        hint: 'Refresh the run to see what it is doing now.',
      }
    case 'decision_not_allowed':
      return {
        title: "That choice isn't available here",
        message: err.message || 'This request does not allow that decision.',
        hint: 'Pick one of the other options.',
      }
    case 'confirmation_required':
      return {
        title: 'Needs your confirmation',
        message: err.message,
        hint: 'Review what changed and confirm to continue.',
      }
    case 'not_resumable':
      return {
        title: "This run can't be resumed",
        message: err.message,
        hint: 'You can fork it from a checkpoint instead.',
      }
    case 'not_replayable':
      return {
        title: "This run can't be replayed",
        message: err.message,
        hint: 'Replays need the model answers recorded during the original run.',
      }
    case 'not_tenant_scoped':
      return {
        title: 'Not available in a shared workspace',
        message: 'This feature is not yet scoped to individual workspaces, so it is turned off on this server.',
        hint: 'It works on a single-user local server.',
      }
    case 'forbidden':
      return { title: "You don't have permission", message: err.message, hint: 'Ask a workspace owner for access.' }
    case 'validation':
      return { title: 'Check your input', message: err.message, hint: 'Fix the highlighted fields and try again.' }
    default:
  }
  if (status === 401) {
    return {
      title: 'Access token needed',
      message: 'This server requires an access token and yours is missing or no longer valid.',
      hint: 'Enter a valid token to continue.',
    }
  }
  if (status === 403) {
    return { title: "You don't have permission", message: err.message || `You can't do ${subject} in this workspace.`, hint: 'Ask a workspace owner for access.' }
  }
  if (status === 404) {
    return { title: 'Not found', message: err.message && err.message !== 'Not Found' ? err.message : `We couldn't find ${subject}. It may have been removed.` }
  }
  if (status === 409) {
    const text = /not active/i.test(err.message) ? 'This run has already finished, so there is nothing to stop.' : err.message
    return { title: 'That conflicts with the current state', message: text, hint: 'Refresh to see the latest state.' }
  }
  if (status === 422 || status === 400) {
    return { title: 'The request was rejected', message: err.message, hint: 'Adjust the details and try again.' }
  }
  if (status >= 500) {
    return { title: 'The server hit a problem', message: err.message || 'It returned an unexpected error.', hint: 'Nothing was changed on your side. Try again shortly.' }
  }
  return { title: 'Request failed', message: err.message }
}
