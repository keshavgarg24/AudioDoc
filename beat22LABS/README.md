# beat22LABS — web interface

The front end for this repository's API. Next.js 16, React 19, Tailwind 4.

```bash
./run.sh                       # http://localhost:5173, proxying to :8000
```

The backend must be running separately, from the repo root:

```bash
PYTHONPATH=src .venv/bin/python -m uvicorn labs.application:app --port 8000
```

**Level 2 is gated even with auth off.** `LABS_REQUIRE_AUTH` defaults to
`false` locally, so no key is needed — but `LABS_GATE_DEEP` defaults to
`true`, and the anonymous principal carries `screen`, `analyze` and `read`
without `deep`. So `mode=ai` and `mode=full` answer `403
deep_tier_forbidden` out of the box, exactly as they do for a public browser
key. To exercise the deep tier locally:

```bash
LABS_GATE_DEEP=0 LABS_EAGER_LOAD=true \
  PYTHONPATH=src .venv/bin/python -m uvicorn labs.application:app --port 8000
```

`LABS_EAGER_LOAD=true` loads the 1.3 GB backbone at startup. Left at
`false` it loads lazily, and the first `mode=ai` request arrives before it is
resident and gets `503 model_loading`.

The venv must be **Python 3.11**, matching the image. `transformers==4.44.2`
pulls a `tokenizers` wheel that does not build on 3.14, so a 3.14 venv can
serve Level 1 and the tools but not the deep tier.

## How it talks to the API

Every call goes to `/api/*` on this app's own origin, and `next.config.mjs`
rewrites that to `API_TARGET`. Staying same-origin is what keeps CORS out of
the picture and means no API key is ever exposed to a cross-origin request.

| Variable | When | Purpose |
| --- | --- | --- |
| `API_TARGET` | build time | Backend origin the `/api/*` rewrite forwards to. Baked into the build, so changing it needs a rebuild. |
| `NEXT_PUBLIC_API_URL` | build time | Absolute backend URL, bypassing the proxy. Only for hosting that cannot rewrite; the backend must then allow this origin. |
| `NEXT_PUBLIC_SITE_URL` | build time | Canonical origin for metadata, sitemap and robots. |

A production build **fails** if `API_TARGET` is missing or private. That is
deliberate: the rewrite is evaluated at the edge, not in the browser, so a
`localhost` target resolves to the loopback address of the serving
infrastructure and every API call 404s — a failure invisible at build time and
total at runtime. Set `ALLOW_LOCALHOST_API_TARGET=1` for a deliberate local
production build.

## Deploying to Vercel

Import the repository, set **Root Directory** to `beat22LABS`, and add the
environment variables below. The framework preset, build command and headers
come from `vercel.json`.

| Variable | Value |
| --- | --- |
| `API_TARGET` | your backend's public HTTPS origin |
| `NEXT_PUBLIC_LABS_API_KEY` | a key scoped `analyze,read` — see below |
| `NEXT_PUBLIC_SITE_URL` | the deployed origin, for metadata and sitemap |

All three are read at **build** time. Changing one in the dashboard does
nothing until you redeploy.

### The API key is public, and that is a decision

The backend deploys with `LABS_REQUIRE_AUTH=true`, so every route needs an
`X-API-Key` header. `NEXT_PUBLIC_` variables are compiled into the browser
bundle, so this key is readable by anyone who opens devtools.

That is the only option that also carries a 50 MB upload. Injecting the
header server-side would mean proxying the body through a serverless
function, and those cap request bodies well below what the API accepts; a
rewrite has no such cap but cannot add headers. So the key has to come from
the browser, which means it has to be one you can afford to publish.

Mint it with `analyze,read` and nothing more:

```bash
python -m labs.cli.manage_keys create \
  --name beat22labs-web --scopes analyze,read --quota 5000
```

`analyze` grants the free Level-1 screen and the tools. It deliberately does
**not** grant `deep`, so the billable tier cannot be spent by strangers who
read your bundle. Give it a daily quota, and rotate it like any other public
credential.

**What that key can and cannot do**, verified against the deployed settings:

| Call | With this key |
| --- | --- |
| `POST /v1/screen` — Stage 1 | works |
| `GET /v1/tools`, `POST /v1/tools/{slug}` | works |
| `POST /v1/analyses` `mode=audio` | works |
| `POST /v1/analyses` `mode=ai` / `mode=full` | **403 `deep_tier_forbidden`** |

The 403 is expected, not a bug. `detect()` treats it as recoverable: the
Stage-1 verdict is already on screen and stays there, with Level 2 marked
"not available to this key", rather than the whole run failing. To enable the
deep tier for real users, call it from a server you control with a
`deep`-scoped key — not from the browser.

### Two things to check before launch

**Rate limiting is per key, and this key is shared by every visitor.** The
deployed screen task sets `LABS_RATE_LIMIT_PER_MIN=30`, and the window is
per container rather than per fleet. Thirty requests a minute across your
whole audience is low for a public site: raise it for this key's deployment,
or move to per-user keys, before you send traffic.

**Confirm the upload path carries 50 MB.** The `/api/*` rewrite is resolved
by Vercel's proxy rather than by a function, which is why it is used for
uploads instead of a route handler. Upload a large file to the deployed site
once and confirm it succeeds rather than assuming it — if it fails, the
fallback is `NEXT_PUBLIC_API_URL` pointing straight at the backend, which
then needs your origin in `LABS_CORS_ORIGINS`.

## The detection flow

Detection is two levels and they are two different endpoints, because they
have two different shapes. `src/lib/api.js` implements the whole thing in
`detect()`:

| | Stage 1 | Stage 2 |
| --- | --- | --- |
| Endpoint | `POST /v1/screen` | `POST /v1/analyses` |
| Transport | synchronous | `202` + poll |
| Measured | 1–3 s | 22–90 s |
| Scope | `screen` (free) | `deep` (billable) |

Stage 1 answers inside a normal HTTP timeout, so the browser calls it
directly and a verdict is on screen in about two seconds. Stage 2 cannot —
any proxy in front of this closes an idle connection long before 90 s — so it
is submit-and-poll with every individual request under a second.

**`next_step` decides whether Stage 2 runs.** The service computes it; this
app does not second-guess it. A Stage-1 `human-made` always says `escalate`
however confident it looks, because both Level-1 models only recognise
generators they were trained on — their silence is not evidence.

By mode:

- **`ai`** — Stage 1, then Stage 2 only if `next_step` is `escalate`.
- **`full`** — Stage 1, then Stage 2 **always**. A full report is bought for
  its evidence, so it never short-circuits.
- **`audio`** — measurement only. No detection model runs, so Stage 1 is
  skipped entirely rather than producing a verdict nobody asked for.

If the deployment has `LABS_SCREEN=0` there is no Stage 1. That is a valid
configuration, so `detect()` falls through to Stage 2 instead of refusing a
file the deep model can still answer for.

`Processing` shows which level is running and what Stage 1 concluded, and
switches to a short phase list when Stage 1 settles the track — otherwise a
decisive two-second answer would sit behind a 77-second animation.

## Tools

Six tools, and the list is not hardcoded in the UI: `src/lib/tools.js`
fetches `GET /v1/tools` and the catalogue drives the interface, so a tool
added to the backend needs no change here.

`src/toolConfig.js` holds the page copy, routes and SEO text for each one.
The slugs there must exist in the backend — a slug with no tool behind it
renders a page that 404s on submit. The six are `master-check`, `tempo-lab`,
`key-lab`, `reference-match`, `vocal-lab` and `beat-vocal-fit`.

Each tool submits with `wait=8`: if it finishes inside that window the result
comes back inline, otherwise a job id is returned and polled. Runtimes in
`typicalSeconds` are the values the backend publishes in `typical_seconds`,
measured on the deployment rather than estimated — keep them in step.

## Layout

```
src/app/            routes. /[slug] is the keyword URL for each tool;
                    /tools/<slug> permanently redirects to it.
src/components/     UI. pages/ are route-level, tools/ render tool results,
                    visuals/ and ui/ are presentational.
src/lib/            api.js (detection), tools.js (tool runs), format, export,
                    seo, headline.
src/toolConfig.js   per-tool page content and routing.
src/styles.css      the design system. Three levels of dark, purple as the
                    single interactive accent; green means success, never
                    "click me".
```
