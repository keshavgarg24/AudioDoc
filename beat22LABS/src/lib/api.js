// Client for the detection API.
//
// Detection runs in two levels and they are two different endpoints, because
// they have two different shapes:
//
//   Stage 1  POST /v1/screen    synchronous, 1-3 s, free
//   Stage 2  POST /v1/analyses  202 + poll, 22-90 s, billable
//
// Stage 1 answers inside a normal HTTP timeout, so the browser calls it
// directly and shows a verdict almost immediately. Stage 2 cannot: any hosted
// proxy in front of this closes an idle connection long before 90 s, so it is
// submit-and-poll with every individual request under a second.
//
// `next_step` on the Stage-1 response is the field that decides whether Stage
// 2 runs. It is not a confidence heuristic of ours - the service computes it,
// and a Stage-1 `human-made` always says `escalate` however confident it
// looks, because both Level-1 models only recognise generators they were
// trained on. Their silence is not evidence.

import { BASE, authHeaders } from './endpoint.js'

const POLL_INTERVAL_MS = 2000
const POLL_CEILING_MS = 10 * 60 * 1000

async function detail(res, fallback) {
  try {
    const b = await res.json()
    // Current envelope: {"error": {"code", "message"}}.
    if (b.error?.message) return b.error.message
    // Older shape, kept as a fallback for safety.
    if (typeof b.detail === 'string') return b.detail
    if (Array.isArray(b.detail)) return b.detail[0]?.msg || fallback
  } catch { /* non-JSON error body */ }
  return fallback
}

const STATUS_MESSAGES = {
  400: 'That file appears to be empty.',
  401: 'Authentication failed - check the configured API key.',
  403: 'This key does not carry the deep scope, so the full model is not '
    + 'available to it.',
  413: 'That file is too large.',
  415: 'That file type is not supported.',
  422: 'That audio could not be analysed.',
  429: 'Too many analyses in flight. Wait for one to finish.',
  503: 'The model is still loading. Try again shortly.',
}

/** An API failure that still knows its HTTP status.
 *
 * `detect` needs to tell "the deep tier is not available to this key" (403,
 * recoverable - the Stage-1 answer still stands) apart from "the file was
 * rejected" (415, not recoverable). A bare Error cannot carry that.
 */
class ApiError extends Error {
  constructor(message, status) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

// 429 from /v1/screen is load shedding at the instance concurrency limit, not
// a rate limit, and the documented response is to retry immediately rather
// than to back off. Saying "too many in flight" there sends people away from
// a request that would have succeeded on the next attempt.
const SCREEN_STATUS_MESSAGES = {
  ...STATUS_MESSAGES,
  429: 'Every screening slot is busy. Try again in a moment.',
  503: 'The Level-1 screen is not enabled on this deployment.',
}

export async function getReady() {
  try {
    const res = await fetch(`${BASE}/v1/ready`, { headers: authHeaders() })
    const body = await res.json().catch(() => ({}))
    return { ok: res.ok, ...body }
  } catch {
    return { ok: false, ready: false, status: 'offline' }
  }
}

function sleep(ms, signal) {
  return new Promise((resolve, reject) => {
    const t = setTimeout(resolve, ms)
    signal?.addEventListener('abort', () => {
      clearTimeout(t)
      reject(new DOMException('Aborted', 'AbortError'))
    }, { once: true })
  })
}

/** Stage 1. Two small models, synchronous, no job id.
 *
 * Resolves with the full screen response. `next_step` is `return` when the
 * verdict is decisive and `escalate` when only the deep model can settle it.
 */
export async function screen(file, { signal } = {}) {
  const form = new FormData()
  form.append('file', file)

  let res
  try {
    res = await fetch(`${BASE}/v1/screen`,
                      { method: 'POST', body: form, signal, headers: authHeaders() })
  } catch (e) {
    if (e.name === 'AbortError') throw e
    throw new Error('Could not reach the detection service. Is the backend running?')
  }

  if (!res.ok) {
    throw new ApiError(
      await detail(res, SCREEN_STATUS_MESSAGES[res.status] || 'Screening failed.'),
      res.status)
  }
  return res.json()
}

/** Stage 2. Submit a track and resolve with the finished report.
 *
 * `onProgress` receives the backend's own stage label ("analysing",
 * "verification", "storing", ...) so the UI can reflect real work rather than
 * a guessed timeline.
 */
export async function analyse(file, {
  mode = 'ai', verify = false, genre = null, signal, onProgress,
} = {}) {
  const form = new FormData()
  form.append('file', file)
  form.append('mode', mode)
  // The v1 API takes an escalation policy, not a boolean.
  form.append('verify', verify ? 'always' : 'never')
  if (genre) form.append('genre', genre)

  let res
  try {
    res = await fetch(`${BASE}/v1/analyses`,
                      { method: 'POST', body: form, signal, headers: authHeaders() })
  } catch (e) {
    if (e.name === 'AbortError') throw e
    throw new Error('Could not reach the detection service. Is the backend running?')
  }

  if (!res.ok) {
    throw new ApiError(
      await detail(res, STATUS_MESSAGES[res.status] || 'Analysis failed.'),
      res.status)
  }

  const body = await res.json()

  // 200 rather than 202 means a stored result for byte-identical audio in the
  // same mode already existed. There is no job to poll: the body IS the
  // result, and treating it as an acceptance would hang waiting for an id
  // that is never coming.
  if (res.status === 200 && !body.id) return body
  if (body.cached && body.mode) return body

  const { id } = body
  if (!id) throw new Error('The service accepted the file but returned no job id.')

  const deadline = Date.now() + POLL_CEILING_MS
  let lastStage = null

  while (Date.now() < deadline) {
    await sleep(POLL_INTERVAL_MS, signal)

    let poll
    try {
      poll = await fetch(`${BASE}/v1/analyses/${id}`,
                         { signal, headers: authHeaders() })
    } catch (e) {
      if (e.name === 'AbortError') throw e
      continue // a transient network blip should not kill a running job
    }

    if (!poll.ok) {
      if (poll.status === 404) throw new Error('That analysis is no longer available.')
      continue
    }

    const polled = await poll.json().catch(() => null)
    if (!polled) continue

    if (polled.progress && polled.progress !== lastStage) {
      lastStage = polled.progress
      onProgress?.(polled.progress)
    }

    if (polled.status === 'succeeded') return polled.result
    if (polled.status === 'failed') {
      throw new Error(polled.error?.message || 'Analysis failed.')
    }
  }

  throw new Error('The analysis is taking longer than expected. Try a shorter track.')
}

/** Take a track the quick check could not settle to the closer pass.
 *
 * The same call `detect` makes when it escalates on its own, exposed so the
 * result page can offer it as a choice. The file is re-uploaded because the
 * quick check keeps nothing server-side: it answers in one round trip and
 * holds no job.
 */
export async function escalate(file, {
  mode = 'ai', verify = false, genre = null, signal, onStage, onProgress,
} = {}) {
  onStage?.({ stage: 2, status: 'running' })
  onProgress?.('starting')
  const report = await analyse(file, { mode, verify, genre, signal, onProgress })
  return {
    report,
    levels: report?.levels_run || ['level_1_screen', 'level_2_deep'],
  }
}

/** A Stage-1 response, shaped so the report renderer can read it.
 *
 * The two tiers do not describe the file the same way: a Stage-2 report
 * carries `source.filename` and `source.duration_seconds`, while the screen
 * carries `filename` and `duration` at the top level. That difference is API
 * knowledge, so it is reconciled here rather than taught to every component
 * that renders a result.
 */
export function asReport(s) {
  if (!s) return null
  return {
    ...s,
    source: s.source || {
      filename: s.filename,
      duration_seconds: s.duration,
    },
    runtime: s.runtime || { elapsed_seconds: s.elapsed_seconds },
  }
}

/** The whole detection flow, both stages, in the order the service intends.
 *
 * `onStage` is called as the run moves through the levels so the UI can show
 * a Stage-1 answer while Stage 2 is still running, rather than a spinner for
 * the full 90 seconds:
 *
 *   onStage({ stage: 1, status: 'running' })
 *   onStage({ stage: 1, status: 'done', result, escalating: true })
 *   onStage({ stage: 2, status: 'running' })
 *
 * Returns `{ screen, report, levels }` - `screen` is the Stage-1 response (or
 * null when Stage 1 was skipped), `report` the Stage-2 result (or null when
 * Stage 1 settled it), and `levels` the list of what actually ran.
 */
export async function detect(file, {
  mode = 'screen', verify = false, genre = null, signal, onStage, onProgress,
} = {}) {
  // `audio` is measurement only - no detection model runs at all, so a
  // Stage-1 screen would be a wasted round trip and a verdict the caller did
  // not ask for.
  if (mode === 'audio') {
    onStage?.({ stage: 2, status: 'running' })
    onProgress?.('starting')
    const report = await analyse(file, { mode, verify, genre, signal, onProgress })
    return { screen: null, report, levels: ['audio'] }
  }

  onStage?.({ stage: 1, status: 'running' })
  onProgress?.('screening')

  let first = null
  try {
    first = await screen(file, { signal })
  } catch (e) {
    if (e.name === 'AbortError') throw e
    // A deployment with LABS_SCREEN=0 has no Stage 1. That is a valid
    // configuration rather than a failure, so fall through to Stage 2 instead
    // of refusing to analyse a file the deep model can still answer for.
    onStage?.({ stage: 1, status: 'skipped', reason: e.message })
  }

  // `screen` is the user asking for the fast read and nothing more. It stops
  // here even when the service says the track needs a closer look - that is
  // reported back as `canEscalate` so they can choose it from the result,
  // rather than being charged a minute of analysis they did not ask for.
  if (mode === 'screen') {
    if (first) onStage?.({ stage: 1, status: 'done', result: first, escalating: false })
    return {
      screen: first,
      report: null,
      levels: first?.levels_run || ['level_1_screen'],
      canEscalate: first?.next_step === 'escalate',
    }
  }

  // `full` is bought for its evidence - the per-window timeline, the
  // musicological pass - so it never short-circuits, however decisive Stage 1
  // was. `ai` stops as soon as the service says the answer is settled.
  const settled = first?.next_step === 'return'
  const escalating = mode === 'full' || !settled

  if (first) {
    onStage?.({ stage: 1, status: 'done', result: first, escalating })
  }

  if (!escalating) {
    return { screen: first, report: null, levels: first?.levels_run || ['level_1_screen'] }
  }

  onStage?.({ stage: 2, status: 'running' })

  let report
  try {
    report = await analyse(file, { mode, verify, genre, signal, onProgress })
  } catch (e) {
    // The deep tier is billable and gated on the `deep` scope, which a
    // public browser key deliberately does not carry. That is a expected
    // configuration rather than a fault, and throwing here would discard a
    // Stage-1 verdict that is already on screen and still perfectly valid.
    // Anything else - a rejected file, a dead backend - is a real failure and
    // still propagates.
    if (e.status === 403 && first) {
      onStage?.({ stage: 2, status: 'unavailable', reason: e.message })
      return {
        screen: first,
        report: null,
        levels: first.levels_run || ['level_1_screen'],
        deepUnavailable: e.message,
      }
    }
    throw e
  }

  return {
    screen: first,
    report,
    levels: report?.levels_run || ['level_1_screen', 'level_2_deep'],
  }
}
