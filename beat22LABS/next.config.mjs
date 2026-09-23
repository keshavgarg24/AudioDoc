/** @type {import('next').NextConfig} */

// Where the FastAPI service lives. In development this is the local backend;
// in production it is the deployed host, injected at build time.
//
// The rewrite is what keeps the browser same-origin, so CORS never enters the
// picture and the API key never has to be exposed to a cross-origin request.
// This replaces the `vercel.json` rewrite the Vite build relied on, and unlike
// that one it also works in `next dev`.
//
// API_TARGET is read HERE, at build time, and baked into the compiled output.
// Changing it in a hosting dashboard therefore does nothing until the next
// build - it is not read at request time.
const IS_PRODUCTION_BUILD =
  process.env.NODE_ENV === 'production' && process.env.NEXT_PHASE !== 'phase-development-server'

// The localhost default is correct for `next dev` and catastrophic in a
// deployed build: the rewrite is evaluated at the edge, not in the browser, so
// it resolves `localhost` to the loopback address of the serving infrastructure.
// Vercel refuses to proxy there and answers every single API call with a 404
// carrying DNS_HOSTNAME_RESOLVED_PRIVATE, which surfaces in the UI as
// "That tool does not exist" on every tool.
//
// That failure is invisible at build time - the build succeeds, all routes
// render, and the site is only broken once someone tries to use it. So the
// build is failed here instead, with the fix in the message. Set
// ALLOW_LOCALHOST_API_TARGET=1 for the rare case of a deliberate local
// production build against a local backend.
const rawTarget = process.env.API_TARGET
const allowLocal = process.env.ALLOW_LOCALHOST_API_TARGET === '1'

if (IS_PRODUCTION_BUILD && !allowLocal) {
  const isMissing = !rawTarget || !rawTarget.trim()
  const isPrivate = rawTarget && /^https?:\/\/(localhost|127\.|0\.0\.0\.0|\[::1\]|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/i.test(rawTarget.trim())

  if (isMissing || isPrivate) {
    throw new Error(
      [
        '',
        '  API_TARGET is ' + (isMissing ? 'not set' : `a private address (${rawTarget})`) + '.',
        '',
        '  Every /api/* call is rewritten to this host at the edge. A missing or',
        '  private value makes the platform refuse the proxy and return 404 for',
        '  the whole API, which the UI reports as "That tool does not exist".',
        '',
        '  Set it to your backend\'s public HTTPS origin, then redeploy:',
        '',
        '    API_TARGET=https://api.your-domain.com',
        '',
        '  On Vercel: Settings -> Environment Variables. It is read at BUILD',
        '  time, so a redeploy is required for a change to take effect.',
        '',
        '  For a deliberate local production build: ALLOW_LOCALHOST_API_TARGET=1',
        '',
      ].join('\n'),
    )
  }
}

const API_TARGET = rawTarget?.trim() || 'http://localhost:8000'

const nextConfig = {
  reactStrictMode: true,

  // Next writes an AGENTS.md and CLAUDE.md into the project root on `dev`
  // otherwise. They describe Next itself rather than this codebase, and a
  // generated CLAUDE.md silently becomes instructions for anyone running an
  // agent in this directory, so it is not something to leave lying around.
  agentRules: false,

  // Pin the workspace root. Without this Turbopack walks up looking for a
  // lockfile, finds an unrelated one in the home directory and warns on every
  // build.
  turbopack: {
    root: import.meta.dirname,
  },

  async rewrites() {
    return [
      // NOTE: there must be no `src/app/api/` directory. A route handler there
      // would shadow this rewrite and every API call would 404.
      {
        source: '/api/:path*',
        destination: `${API_TARGET}/:path*`,
      },
    ]
  },

  // Next already sets immutable caching on /_next/static; overriding it only
  // earns a build warning. The Vite build needed the vercel.json rule because
  // nothing set it otherwise.
}

export default nextConfig
