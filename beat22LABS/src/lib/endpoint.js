// Where the API is, and what to send with every request.
//
// Shared by api.js (detection) and tools.js (tool runs) so the two cannot
// drift on the base URL or the auth header - which is exactly the kind of
// difference that makes one half of a site work and the other 401.

// Default: call /api on our own origin and let the rewrite in
// next.config.mjs forward it to API_TARGET. Same-origin means no CORS and no
// preflight on a 50 MB upload.
//
// NEXT_PUBLIC_API_URL overrides that with an absolute backend URL, for
// hosting that cannot rewrite. That path is cross-origin, so the backend has
// to name this site in LABS_CORS_ORIGINS.
export const BASE = process.env.NEXT_PUBLIC_API_URL
  ? process.env.NEXT_PUBLIC_API_URL.replace(/\/$/, '')
  : '/api'

// The deployed backend runs with LABS_REQUIRE_AUTH=true, so every route
// needs an X-API-Key header. Without one it answers 401 `missing_api_key` and
// nothing on the site works.
//
// This is NEXT_PUBLIC_, so it is compiled into the bundle and any visitor can
// read it. That is not an oversight, it is the only option that also carries
// a 50 MB upload: injecting the header server-side would mean proxying the
// body through a serverless function, and those cap request bodies far below
// the 50 MB the API accepts. A rewrite has no such cap but cannot add
// headers.
//
// So: the key put here MUST be one you are willing to publish.
//
//   - Give it `screen` and `analyze` only, never `deep` or `admin`. The deep
//     tier is billable and a published key would be spent by strangers.
//   - Give it a daily quota and a per-minute rate limit.
//   - Rotate it like any other public credential.
//
// A deep-tier key belongs in a server you control, called from your own
// backend, not in a browser bundle.
const KEY = process.env.NEXT_PUBLIC_LABS_API_KEY || ''

/** Headers for an API call. Never sets Content-Type: every request that has
 *  a body sends FormData, and the browser has to set that boundary itself. */
export function authHeaders(extra) {
  const h = { ...(extra || {}) }
  if (KEY) h['X-API-Key'] = KEY
  return h
}

export const HAS_KEY = Boolean(KEY)
