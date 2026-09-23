# beat22LABS — web interface

The front end for this repository's API. Next.js 16, React 19, Tailwind 4.

```bash
./run.sh                       # http://localhost:5173, proxying to :8000
```

The backend must be running separately:

```bash
uvicorn labs.application:app --port 8000   # from the repo root, PYTHONPATH=src
```

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
