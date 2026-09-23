// Client for the /v1/tools API.
//
// Every tool is submitted and polled identically, so this file does not grow
// when a tool is added: the catalogue is fetched from the server and drives
// the UI. Individual requests stay short, which is what keeps the whole thing
// working behind a hosted proxy that caps response time.

const BASE = process.env.NEXT_PUBLIC_API_URL
  ? process.env.NEXT_PUBLIC_API_URL.replace(/\/$/, '')
  : '/api'

const POLL_INTERVAL_MS = 1500
const POLL_CEILING_MS = 10 * 60 * 1000

const STATUS_MESSAGES = {
  400: 'One of the uploaded files is empty.',
  401: 'Authentication failed - check the configured API key.',
  // Not "that tool does not exist". A 404 here almost never means the slug is
  // wrong - the catalogue that produced it came from this same API moments
  // earlier. It means the request never reached the service: the /api/*
  // rewrite is pointing somewhere that does not answer, which is what an
  // unset or private API_TARGET produces. Saying "no such tool" sent people
  // looking for a frontend bug that was not there.
  404: 'The analysis service could not be reached. If this persists it is a '
    + 'configuration problem rather than something you did.',
  413: 'That file is too large.',
  415: 'That file type is not supported.',
  422: 'That audio could not be analysed.',
  429: 'Too many jobs in flight. Wait for one to finish.',
  503: 'The service is still starting up. Try again shortly.',
}

// How many consecutive failed polls to absorb before giving up. Transient
// blips happen and should not kill a job that is still running; a backend that
// is simply gone should not hold a spinner for the full ceiling.
const MAX_CONSECUTIVE_POLL_FAILURES = 8

async function detail(res, fallback) {
  try {
    const b = await res.json()
    if (b.error?.message) return b.error.message
    if (typeof b.detail === 'string') return b.detail
  } catch { /* non-JSON body */ }
  return fallback
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

/** The tool catalogue, including each tool's stated accuracy and limits. */
export async function getTools() {
  const res = await fetch(`${BASE}/v1/tools`)
  if (!res.ok) throw new Error('Could not load the tool list.')
  return res.json()
}

/** Run a tool and resolve with its result envelope.
 *
 * `files` maps the tool's declared input names to File objects, e.g.
 * `{ file }` or `{ beat, vocal }`.
 */
export async function runTool(slug, files, {
  genre = null, signal, onProgress, noCache = false,
} = {}) {
  const form = new FormData()
  for (const [name, file] of Object.entries(files)) {
    if (file) form.append(name, file)
  }
  if (genre) form.append('genre', genre)
  if (noCache) form.append('no_cache', 'true')
  // Ask the server to hold briefly: a fast tool then returns inline and the
  // user never sees a polling delay for something that took four seconds.
  form.append('wait', '8')

  let res
  try {
    res = await fetch(`${BASE}/v1/tools/${slug}`, {
      method: 'POST', body: form, signal,
    })
  } catch (e) {
    if (e.name === 'AbortError') throw e
    throw new Error('Could not reach the service. Is the backend running?')
  }

  if (!res.ok) {
    throw new Error(await detail(res, STATUS_MESSAGES[res.status] || 'That tool failed.'))
  }

  const body = await res.json()

  // The wait window covered it.
  if (body.status === 'succeeded') return unwrap(body)
  if (body.status === 'failed') throw new Error(body.error || 'That tool failed.')

  const id = body.id
  if (!id) throw new Error('The service accepted the file but returned no job id.')

  const deadline = Date.now() + POLL_CEILING_MS
  let lastStage = null
  let consecutiveFailures = 0

  while (Date.now() < deadline) {
    await sleep(POLL_INTERVAL_MS, signal)

    let poll
    try {
      poll = await fetch(`${BASE}/v1/tools/results/${id}`, { signal })
    } catch (e) {
      if (e.name === 'AbortError') throw e
      // A transient blip should not kill a running job, but an unreachable
      // backend previously spun here for the full ten minutes with nothing
      // shown to the user. Bail once it is clearly not coming back.
      if (++consecutiveFailures >= MAX_CONSECUTIVE_POLL_FAILURES) {
        throw new Error('Lost contact with the analysis service while the run '
          + 'was in progress.')
      }
      continue
    }
    if (!poll.ok) {
      if (poll.status === 404) throw new Error('That run is no longer available.')
      if (++consecutiveFailures >= MAX_CONSECUTIVE_POLL_FAILURES) {
        throw new Error('The analysis service stopped responding while the run '
          + 'was in progress.')
      }
      continue
    }

    const p = await poll.json().catch(() => null)
    if (!p) {
      if (++consecutiveFailures >= MAX_CONSECUTIVE_POLL_FAILURES) {
        throw new Error('The analysis service returned an unreadable response.')
      }
      continue
    }

    consecutiveFailures = 0

    if (p.progress && p.progress !== lastStage) {
      lastStage = p.progress
      onProgress?.(p.progress)
    }
    if (p.status === 'succeeded') return unwrap(p)
    if (p.status === 'failed') throw new Error(p.error || 'That tool failed.')
  }

  throw new Error('This is taking longer than expected. Try a shorter file.')
}

/** Flatten the job envelope into the tool payload the pages render. */
function unwrap(body) {
  const outer = body.result || {}
  return {
    tool: outer.tool,
    name: outer.name,
    cached: !!outer.cached,
    durationSeconds: outer.duration_seconds,
    inputs: outer.inputs || [],
    data: outer.result || {},
    jobId: body.id,
  }
}
