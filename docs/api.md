<p align="center">
  <img src="../assets/labs.svg" alt="LABS" width="360">
</p>

# API reference

Base path `/v1`. Every documented endpoint is versioned; `/health` is
additionally exposed unversioned so a load balancer can be pointed at a path
that outlives any API version.

Interactive documentation is served at `/docs`, the OpenAPI schema at
`/openapi.json`.

---

## Contents

- [Conventions](#conventions)
- [Authentication](#authentication)
- [Rate limits](#rate-limits)
- [The two tiers](#the-two-tiers)
- [**`assessment` — read this one**](#assessment--read-this-one)
- [Screen (Level 1, free)](#screen-level-1-free)
- [Analyses](#analyses)
- [Appeals and feedback](#appeals-and-feedback)
- [Tools](#tools)
- [System](#system)
- [Webhooks](#webhooks)
- [Errors](#errors)
- [Caching](#caching)

---

## Conventions

**Content type.** Submissions are `multipart/form-data`, because they carry a
file. Everything else is JSON.

**Asynchronous by default.** A submission returns `202 Accepted` with a job id.
Poll it, or supply `webhook_url`. Pass `wait` to have the server hold the
response briefly and return the result inline if the work finishes in time —
convenience for a simple client, not a different code path.

**Every response carries:**

| Header | Meaning |
|---|---|
| `X-Request-Id` | Echoes yours if you sent one, otherwise generated. Quote it in a support request. |
| `X-API-Version` | The build that answered. |
| `X-Response-Time-ms` | Server-side handling time. |

**Timestamps** are ISO-8601 UTC. **Durations** are seconds. **Probabilities**
are ratios in `[0, 1]`, never percentages.

---

## Authentication

Send the key in a header:

```
X-API-Key: labs_live_xxxxxxxxxxxxxxxxxxxx
```

Authentication is enforced when `LABS_REQUIRE_AUTH=true` or any key is
configured. A deployment with neither is open, which is correct for local
development and nothing else.

Keys carry scopes:

| Scope | Grants |
|---|---|
| `screen` | `POST /v1/screen` — the free Level-1 tier |
| `analyze` | Submitting work: `/v1/analyses`, `/v1/tools`. Implies `screen`. |
| `deep` | `mode=ai` and `mode=full`, which run the 1.29 GB backbone. **Billable.** |
| `read` | Reading results and usage |
| `admin` | All of the above, plus `/v1/diagnostics` |

`analyze` implies `screen`, because a key that may submit an analysis may
certainly submit the cheap version of one. It does **not** imply `deep`: that
is the whole point of the split, and a key that inherited the billable tier
would give it away.

An open deployment (no `LABS_REQUIRE_AUTH`, no keys) grants anonymous callers
`screen`, `analyze` and `read` — everything it granted before — but **not**
`deep`. Set `LABS_GATE_DEEP=0` to restore the fully open behaviour.

A key is displayed once, at creation. Only a SHA-256 digest is stored, so a
leaked database does not leak usable keys.

```bash
# A paying API customer: both tiers.
labs-keys create --name "partner integration" --scopes analyze,read,deep

# A frontend key: free tier only. Cannot reach the backbone.
labs-keys create --name "web frontend" --scopes screen,read
labs-keys list
labs-keys revoke --fingerprint labs_live_AbC123...
```

---

## Rate limits

Two independent limits, both per key:

| Limit | Default | Exceeded |
|---|---|---|
| Requests per minute | 60 | `429` with `Retry-After` |
| Concurrent analyses | 4 | `429`, code `too_many_inflight` |

The second is the one integrations hit. It is a concurrency bound, not a rate:
wait for a job to finish rather than backing off on a timer.

---

## The two tiers

Detection runs in two levels, and which one answers changes the latency by a
factor of twenty.

| | Level 1 | Level 2 |
|---|---|---|
| Endpoint | `POST /v1/screen` | `POST /v1/analyses` |
| Transport | **synchronous** | asynchronous (`202` + poll) |
| Measured | **1.07–3.20 s** | 22–90 s |
| Models | 2 ONNX graphs, 1.2 MB | MERT-v1-95M + transformer, 1.34 GB |
| Scope | `screen` (free) | `deep` (billable) |

Both report the same verdict vocabulary, so one frontend code path renders
either:

`ai-generated` · `human-made` · `inconclusive` · `unavailable`

`inconclusive` is a real answer, not a failure. Both levels have an explicit
middle band rather than rounding a borderline score to whichever side is
nearer.

**The field to branch on is `next_step`:**

| Value | Meaning |
|---|---|
| `return` | Decisive. The deep model would restate this, not revise it. |
| `escalate` | Level 1 could not settle it. `POST /v1/analyses` for the answer. |

A Level-1 `human-made` **always** returns `escalate`, however confident it
looks. Both Level-1 models only recognise generators they were trained on, so
their silence is not evidence — a generator neither has seen is
indistinguishable from human audio to them. Level 1 never publishes an
exoneration on its own.

---

## `assessment` — read this one

Every detection response carries an `assessment` block, in the same shape, from
whichever tier answered. **This is the block to integrate against.** The
top-level `prediction`, `confidence`, `fake_probability` and `real_probability`
fields are kept for older callers and documented below, but they do not mean
the same thing across the two tiers and `assessment` does.

```json
"assessment": {
  "score": 0.8005,
  "label": "ai-generated",
  "verdict": "inconclusive",
  "band": "likely-ai",
  "confidence": 0.601,
  "margin": 0.3005,
  "threshold": 0.5,
  "decided_by": "level_2_deep"
}
```

| Field | Type | Meaning |
|---|---|---|
| `score` | 0–1 | P(AI-generated). **0.5 is the decision boundary.** `null` only when no model could score the file. |
| `label` | string | `score >= 0.5 → ai-generated`, else `human-made`. **Always populated.** Never `inconclusive`. |
| `verdict` | string | The same score after an uncertainty band. May be `inconclusive` or `unavailable`. |
| `band` | string | `strong-ai` · `likely-ai` · `uncertain` · `likely-human` · `strong-human`. |
| `confidence` | 0–1 | `\|score − 0.5\| × 2`, times the tier's reliability evidence. **0.0 means "on the boundary"**, not "half sure". |
| `margin` | 0–0.5 | Raw distance from the boundary, before any multiplier. |
| `threshold` | 0.5 | Stated in the payload so it is never inferred. |
| `decided_by` | string | `level_1_policy`, `level_1_c2pa`, `level_2_deep`, … |

### Which field should you use?

`label` and `verdict` answer different questions and the service publishes
both on purpose.

| You are… | Use | Because |
|---|---|---|
| Ranking a catalogue, or feeding a review queue | `label` + `band` | You need a side for every row, and `band` tells you which rows deserve a human minute. |
| Taking an action with a cost — delisting, refusing a payout, emailing a producer | `verdict` | It abstains rather than guess, and a wrong call here is expensive. |
| Building your own threshold | `score` | It is monotone and continuous. Set your own cutoff. |

Rounding a coin flip to the nearer side and publishing it as a finding is how a
detector acquires a false-accusation rate it cannot see. Refusing to answer at
all is useless to someone running bulk triage. Both are reported, from the same
number, and you pick by what being wrong costs you.

### Why `label` and `verdict` can disagree

They disagree exactly when the score is real but not strong enough to clear the
tier's own bar. A track at `score = 0.80` from Level 2 gets
`label: ai-generated` and `verdict: inconclusive`, because Level 2's decisive
point is 0.88. Nothing is contradictory: one field is reporting the side, the
other is reporting whether the side is safe to act on.

| Tier | AI decisive at | Human decisive at |
|---|---|---|
| Level 1 | `score ≥ 0.80` | `score ≤ 0.20` |
| Level 2 | `score ≥ 0.8808` (`\|logit\| ≥ 2`) | `score ≤ 0.1192` |

### Cross-tier agreement

When both tiers run, `detection.level_agreement` reports whether they concur:

```json
"level_agreement": {
  "state": "disagree",
  "note": "The deep model flags this track and Level 1 does not...",
  "labels": {"level_1": "human-made", "level_2": "ai-generated", "match": false,
             "note": "The tiers fall on opposite sides of 0.5..."}
}
```

`state` compares the banded verdicts and can be `level-1-inconclusive` or
`level-2-inconclusive`, which tells you nothing about which way each was
leaning. `labels` always compares, because `label` always exists. **Two tiers
on opposite sides of 0.5 is the single most useful thing to sort a review queue
on**, whether or not either cleared its own bar.

---

## Screen (Level 1, free)

### `POST /v1/screen`

Two small models over two different representations of the audio, fused so
that agreement raises confidence and disagreement lowers it, plus content
credentials and container forensics.

**Synchronous.** The answer is in the response — no job id, no polling. This
is the endpoint to call from a browser on upload.

| Field | Type | Default | Notes |
|---|---|---|---|
| `file` | file | — | **Required.** Same formats and 50 MB cap as `/v1/analyses`. |
| `reference` | string | — | Your own identifier, echoed back. |

```bash
curl -s -F file=@track.mp3 https://api.example.com/v1/screen | jq
```

```json
{
  "tier": "screen",
  "levels_run": ["level_1_screen"],
  "assessment": {
    "score": 0.0007,
    "label": "human-made",
    "verdict": "human-made",
    "band": "strong-human",
    "confidence": 0.9986,
    "margin": 0.4993,
    "threshold": 0.5,
    "decided_by": "level_1_policy"
  },
  "verdict": "human-made",
  "prediction": "Real",
  "fake_probability": 0.0007,
  "confidence": 100.0,
  "next_step": "escalate",
  "escalation_note": "Level 1 could not settle this track. A Level-1 'human-made' or 'inconclusive' result is not an exoneration: run mode=ai or mode=full for the deep model.",
  "caveat": "A Level-1 'human-made' result means neither small model found an artifact it recognises...",
  "duration": 216.1,
  "elapsed_seconds": 1.59,
  "detection": {
    "level_1": {
      "models": {
        "fakeprint": {"probability": 0.000906, "reads": "averaged linear spectrum 1-8 kHz; exact peak locations"},
        "cepstrum":  {"probability": 0.000497, "reads": "CQT / cepstrum on a log-frequency axis; learned texture",
                      "windows": [{"index": 0, "start_s": 5.0, "end_s": 15.0, "probability": 0.0004}]}
      },
      "ensemble": {
        "agreement": "agree-human",
        "agreement_score": 0.9996,
        "note": "Neither the comb nor the texture shows generation artifacts."
      },
      "screens": {"c2pa": {...}, "container": {...}, "bandwidth": {...}},
      "timings": {"decode": 0.48, "models": 0.42, "bandwidth": 0.08}
    },
    "level_2": null
  }
}
```

**Reading it.** `ensemble.agreement` is the interesting field:

| Value | Interpretation |
|---|---|
| `agree-ai` | Two representations, two methods, one conclusion. Confident. |
| `agree-human` | The same, in the other direction. |
| `disagree` | Recorded, not resolved. `ensemble.note` says which failure mode you are in — a smeared comb from processed generator output reads differently from a comb manufactured by bitcrushing a human track. |
| `single` | One model was unavailable. Degraded, and confidence is reduced to say so. |

`robustness` appears only when Level 1 proposed to end the request, and shows
whether the verdict survived benign perturbation. A verdict that moves is
downgraded and escalated rather than published.

**Errors**

| Status | Code | Meaning |
|---|---|---|
| `415` | `unsupported_media_type` | Extension or magic bytes rejected. |
| `429` | `screen_busy` | At the instance concurrency limit. Retry immediately; this is load shedding, not a rate limit. |
| `503` | `screen_disabled` | `LABS_SCREEN=0` on this deployment. |

---

## Analyses

### `POST /v1/analyses`

Submit a track.

| Field | Type | Default | Notes |
|---|---|---|---|
| `file` | file | — | **Required.** wav, mp3, flac, m4a, aac, ogg, opus. 50 MB cap. |
| `mode` | string | `ai` | `ai`, `audio` or `full`. See below. |
| `verify` | string | `never` | `never`, `auto`, `always`. Secondary verification. |
| `genre` | string | — | Target genre for the transformation guide. See `/v1/genres`. |
| `webhook_url` | string | — | HTTPS URL POSTed on completion. |
| `reference` | string | — | Your own identifier, echoed back untouched. |
| `fields` | string | — | Comma-separated sections to return. |
| `no_cache` | bool | `false` | Force a fresh run instead of a stored result. |
| `wait` | float | `0` | Seconds to hold the response, max 25. |

`Idempotency-Key` header: safe retries. A repeat with the same key returns the
original job rather than starting a second one.

**Modes**

| Mode | Runs | Scope | Use when |
|---|---|---|---|
| `screen` | Level 1 only | `screen` | Prefer `POST /v1/screen` — it finishes inside an HTTP timeout, so queueing it buys nothing. |
| `ai` | Level 1, then Level 2 if needed | `deep` | You want a verdict. |
| `audio` | Signal analysis only | `analyze` | You want measurements. Works while weights are still loading, and never touches the backbone. |
| `full` | Level 1 + Level 2 + everything | `deep` | You want the whole report. |

`mode=ai` short-circuits: if Level 1 is decisively AI and the verdict survives
the exit gate, the backbone never runs and the response carries `early_exit`.

`mode=full` **never** short-circuits. A full report is bought for its evidence
— the per-window timeline, the embeddings, the musicological pass — and
silently omitting the deep layers because a cheap model was confident would
deliver a thinner document than the one requested.

**Fields added by the tiering:**

| Field | Meaning |
|---|---|
| `tier` | The mode that ran. |
| `levels_run` | `["level_1_screen"]` or `["level_1_screen", "level_2_deep"]`. How much was actually computed, so a cheap answer is distinguishable from an expensive one. |
| `verdict` | The banded verdict. **Prefer this over `prediction`.** |
| `decisive` | `false` when the logit sits in the inconclusive band. |
| `band_note` | Why. |
| `early_exit` | Present only when Level 1 ended the request. |
| `segment_plan` | Which windows Stage-1 used, its `coverage` of the track, and its `redundancy`. |
| `cascade` | Whether the two-pass Stage-1 escalated, and how many windows it scored. |
| `detection.level_1` | The full Level-1 result. |
| `detection.level_agreement` | Whether the two levels agree, and what a disagreement means. Recorded, never resolved into one number. |

**`prediction` vs `verdict`.** `prediction` is the raw sign of Stage-2's logit
and is kept for existing callers. `verdict` applies an inconclusive band at
`|logit| < 2.0`. Prefer `verdict`: a track at `|logit| 0.4` lands on either
side of zero depending only on which windows were analysed, so the sign alone
turns a coin flip into a finding.

**Field groups** for `fields`: `verdict`, `reliability`, `timeline`,
`structure`, `findings`, `detection`, `musical`, `production`, `character`,
`industry`, `features`, `artist`. `mode`, `source`, `runtime` and `model` are
always returned so a narrowed response stays self-describing.

```bash
curl -X POST https://api.example.com/v1/analyses \
  -H "X-API-Key: $LABS_API_KEY" \
  -H "Idempotency-Key: $(uuidgen)" \
  -F "file=@track.mp3" \
  -F "mode=full" \
  -F "fields=verdict,musical,production"
```

**`202 Accepted`**

```json
{
  "id": "9f2c1a4e8b7d",
  "status": "queued",
  "mode": "full",
  "filename": "track.mp3",
  "poll_url": "/v1/analyses/9f2c1a4e8b7d",
  "created_at": "2026-01-15T09:41:22Z"
}
```

**`200 OK`** when a stored result already exists for byte-identical audio in
the same mode. The body is the full result with `"cached": true`.

### `GET /v1/analyses/{id}`

Poll. Falls back to the stored record once the in-memory job is evicted.

```json
{
  "id": "9f2c1a4e8b7d",
  "status": "succeeded",
  "progress": "complete",
  "duration_seconds": 47.2,
  "result": {
    "mode": "full",
    "prediction": "Real",
    "confidence": 0.94,
    "fake_probability": 0.06,
    "musical": { "rhythm": { "bpm": 128.0 }, "harmony": { "key": "A min" } },
    "production": { "loudness": { "integrated_lufs": -8.4 } }
  }
}
```

`status` is `queued`, `running`, `succeeded` or `failed`. Poll about every 2
seconds; a typical analysis is 40 seconds to 3 minutes depending on length.

### `GET /v1/analyses`

List stored analyses for the calling key, newest first.

| Query | Default | Notes |
|---|---|---|
| `limit` | 50 | Max 200 |
| `skip` | 0 | Pagination offset |
| `is_ai` | — | Filter on the verdict |
| `genre` | — | Filter on detected primary genre |

---

## Appeals and feedback

When Level 1 ends a request on its own, the response carries a `screen_id` and
an `appeal` block. That is the human feedback loop: the caller can ask for the
deep model that was skipped.

```json
{
  "verdict": "ai-generated",
  "next_step": "return",
  "screen_id": "scr_8f3a...",
  "appeal": {
    "available": true,
    "endpoint": "/v1/escalations",
    "screen_id": "scr_8f3a...",
    "expires_in_seconds": null,
    "requires_reupload": false,
    "note": "Level 1 decided this on its own and did not run the deep model..."
  }
}
```

`expires_in_seconds` is **`null` when the appeal window does not expire**,
which is the default (`LABS_SCREEN_RETAIN_S=0`). A number means the window
closes that many seconds after the screen. It is never `0` — that would read
as "already expired".

Only *decisive* results are retained. Every other path already escalates to a
stored analysis, so retaining them here would duplicate rather than preserve.

### `POST /v1/escalations`

Runs Level 2 on a track Level 1 decided alone. Scope: `deep`.

| Field | Type | Notes |
|---|---|---|
| `screen_id` | string | **Required.** From the screen response. |
| `reason` | string | Why you disagree. Recorded verbatim. |
| `file` | file | Only needed if the retention window expired. |
| `webhook_url` | string | POSTed on completion. |

Runs `mode=full`, not `mode=ai` — someone disputing a verdict is owed the
evidence, and `full` is the one mode that cannot short-circuit back to Level 1
and hand back the very answer being appealed.

Returns `202` with a job id; poll `GET /v1/analyses/{id}` as usual.

**Idempotent.** A second appeal for the same `screen_id` returns `200` with the
existing job, so a double-clicked "I disagree" does not start a second 60 s run.

| Status | Code | Meaning |
|---|---|---|
| `404` | `screen_not_found` | Unknown or expired. Re-submit to `/v1/analyses`. |
| `410` | `audio_unavailable` | Retained record exists, audio does not. Attach the file. |
| `503` | `appeals_unavailable` | Persistence not configured on this deployment. |

### `GET /v1/feedback/summary`

Appeal and **overturn** rates. Scope: `admin` — an overturn rate measures the
detector's error rate, which is not an ordinary caller's to read.

```json
{"enabled": true, "days": 30, "decided": 42,
 "overturn_rate": 0.071,
 "counts": {"escalation": {"upheld": 39, "overturned": 3, "pending": 5}}}
```

`overturn_rate` is `null` until something has been decided. A rate over zero
samples is not a rate, and rendering it as 0% reads as "the detector is never
wrong". A deep `inconclusive` counts as neither.

---

## Tools

Six tools, one interface. Submit and poll identically, so an integration
written once gains each new tool without changes.

### `GET /v1/tools`

The catalogue: inputs, expected runtime, stated accuracy and stated
limitations. The accuracy and limitations strings are part of the contract —
they are what the product claims, returned here so you can surface them rather
than inventing your own.

| Slug | Inputs | Answers |
|---|---|---|
| `master-check` | `file` | Is this master ready for release? |
| `tempo-lab` | `file` | Tempo, grid stability, groove |
| `key-lab` | `file` | Key, mode, harmonic neighbours |
| `reference-match` | `file`, `reference` | How does this differ from a reference? |
| `vocal-lab` | `file` | Pitch, timing and tuning of the vocal |
| `beat-vocal-fit` | `beat`, `vocal` | Do these two sit together? |

### `POST /v1/tools/{slug}`

Field names come from the tool's declared `inputs`.

```bash
curl -X POST https://api.example.com/v1/tools/beat-vocal-fit \
  -H "X-API-Key: $LABS_API_KEY" \
  -F "beat=@instrumental.wav" \
  -F "vocal=@vocal.wav" \
  -F "wait=20"
```

Shared fields: `genre`, `wait`, `no_cache`, and the `Idempotency-Key` header.

### `GET /v1/tools/results/{id}`

```json
{
  "id": "3c7f1b09",
  "status": "succeeded",
  "result": {
    "tool": "master-check",
    "name": "Master Check",
    "cached": false,
    "duration_seconds": 6.4,
    "inputs": [
      { "input": "file", "sha256": "a3f1…", "duration_seconds": 184.2,
        "channels": 2, "sample_rate": 44100 }
    ],
    "result": { }
  }
}
```

---

## System

| Endpoint | Auth | Returns |
|---|---|---|
| `GET /health` | none | Liveness, model readiness, queue depth |
| `GET /v1/health` | none | Identical |
| `GET /v1/ready` | none | `200` when weights are resident, `503` otherwise |
| `GET /v1/diagnostics` | `admin` | Checkpoint provenance, storage reachability, queue detail |
| `GET /v1/genres` | none | Genres available for fit and transformation |
| `GET /v1/usage` | `read` | Usage for the calling key |
| `GET /v1/metrics` | `admin` | Prometheus exposition: request rate, error rate, latency histogram, queue depth |

`/health` is deliberately thin. It is unauthenticated, so it carries only what
an anonymous caller legitimately needs: is the service up, and can it take work
yet. Everything an operator wants during an incident is behind `admin` on
`/v1/diagnostics`, because the fields involved name the weight repository, the
model architecture and the storage layout.

Point orchestrator liveness at `/health` and readiness at `/v1/ready`.

**Reading `queue`.** `depth` is outstanding work — queued plus running — and
is the number to alert on. `retained` counts everything still held in memory
including finished jobs inside their retention window, so it climbs to the
store's ceiling under entirely healthy traffic and means nothing on its own.

```json
{ "queue": { "depth": 3, "queued": 1, "running": 2, "retained": 128 } }
```

**Correlation.** Every response carries `X-Request-Id`, echoing the caller's
if one was sent. That same id appears on every log line the request produces,
including from the background worker that runs the analysis, so a caller
quoting the header can be traced end to end. Send your own to stitch our logs
to yours.

---

## Errors that are worth handling

Two responses are load-bearing for an integration and are worth special-casing
rather than treating as generic failures.

**`429 too_many_inflight`** is a concurrency bound, not a rate. Backing off on
a timer does not help; wait for one of your own jobs to finish. Default is 4
concurrent analyses per key.

**`429 rate_limited`** and **`429 quota_exceeded`** both carry `Retry-After`
in seconds. Honour it.

---

## Webhooks

Supply `webhook_url` and the finished job is POSTed to it. The body is the same
JSON `GET /v1/analyses/{id}` would return.

| Header | Contents |
|---|---|
| `X-Webhook-Id` | The job id |
| `X-Webhook-Timestamp` | Unix seconds when signed |
| `X-Webhook-Signature` | `HMAC-SHA256(secret, "{timestamp}.{raw_body}")`, hex |

Verify before trusting the body:

```python
import hashlib, hmac

def verify(secret: str, timestamp: str, raw_body: bytes, signature: str) -> bool:
    expected = hmac.new(
        secret.encode(),
        f"{timestamp}.".encode() + raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
```

Compare in constant time, and reject a timestamp older than a few minutes so a
captured delivery cannot be replayed.

**What the URL must be.** HTTPS, resolving to a public address. The hostname is
resolved and every address it resolves to is checked, at submission and again
immediately before delivery. Redirects are not followed. A URL naming a
loopback, private or link-local address is refused with `422` at submission,
where you can still act on it.

Delivery retries up to three times with backoff. A failed webhook never fails
the job — the result is always readable by polling.

---

## Errors

Every failure, from every endpoint, has the same shape:

```json
{ "error": { "code": "payload_too_large", "message": "File exceeds the 50 MB limit." } }
```

Branch on `code`; it is stable. `message` is for humans and may change.

| Status | Code | Cause |
|---|---|---|
| 400 | `empty_file` | The upload contained no bytes |
| 401 | `missing_api_key` | No `X-API-Key` header |
| 401 | `invalid_api_key` | Key not recognised |
| 401 | `revoked_api_key` | Key was revoked |
| 403 | `forbidden` | Key lacks the required scope |
| 404 | `unknown_tool` | No tool with that slug |
| 404 | `not_found` | No job with that id, or it has aged out |
| 413 | `payload_too_large` | Above the upload cap |
| 415 | `unsupported_media_type` | Extension not allowed, or contents are not audio |
| 422 | `invalid_request` | A parameter failed validation |
| 422 | `missing_input` | A tool input was not supplied |
| 422 | `invalid_mode` | `mode` was not `ai`, `audio` or `full` |
| 422 | `invalid_genre` | Unknown genre |
| 422 | `webhook_destination_not_allowed` | Webhook URL resolves somewhere it may not |
| 422 | `webhook_requires_https` | Webhook URL was plain HTTP |
| 429 | `rate_limited` | Requests-per-minute exceeded; see `Retry-After` |
| 429 | `too_many_inflight` | Concurrent analysis limit reached |
| 429 | `quota_exceeded` | Daily quota for the key exhausted |
| 503 | `model_loading` | Weights not resident; retry, or use `mode=audio` |

A `415` on a file with a valid extension means the contents are not audio. The
leading bytes are checked against known container signatures before the file
reaches the decoder, because the extension is a claim by the caller.

---

## Caching

Byte-identical audio submitted again returns the stored result rather than
re-running the pipeline. Matching is exact, on the SHA-256 of the upload, and
scoped to the mode or tool and its options — a cached result is never returned
for a different question.

A cached response carries `"cached": true`. Pass `no_cache=true` to force a
fresh run.

**The limit is worth being precise about.** This matches byte-identical files
only. A re-export at a different bitrate, a re-encode, or a trim of one sample
is a different file and will be analysed again. Catching those needs acoustic
fingerprinting, which is a different technique with its own error rates and is
not what this does.

Deduplication requires MongoDB. Without `LABS_MONGO_URI` configured nothing is
stored and every submission runs in full.

---

## Integration guide

### Frontend (JavaScript / TypeScript)

Drop-in function that submits a file, polls until done, and returns the result:

```typescript
const BASE = "https://your-labs-host/v1";
const API_KEY = import.meta.env.VITE_LABS_API_KEY; // never hard-code

async function analyseTrack(
  file: File,
  mode: "ai" | "audio" | "full" = "ai",
  onProgress?: (stage: string) => void
): Promise<AnalysisResult> {
  // 1. Submit
  const form = new FormData();
  form.append("file", file);
  form.append("mode", mode);

  const sub = await fetch(`${BASE}/analyses`, {
    method: "POST",
    headers: { "X-API-Key": API_KEY },
    body: form,
  });
  if (!sub.ok) {
    const err = await sub.json();
    throw new Error(err.error?.code ?? "submit_failed");
  }

  const job = await sub.json();
  if (job.status === "succeeded") return job; // inline result (wait hit)

  // 2. Poll
  const pollUrl = `${BASE}/analyses/${job.id}`;
  while (true) {
    await new Promise(r => setTimeout(r, 2000));
    const poll = await fetch(pollUrl, { headers: { "X-API-Key": API_KEY } });
    const data = await poll.json();
    onProgress?.(data.progress ?? data.status);
    if (data.status === "succeeded") return data;
    if (data.status === "failed") throw new Error(data.error ?? "job_failed");
  }
}
```

**Error codes** you should handle on the client:

| Code | What to show |
|---|---|
| `model_loading` | "Warming up — try again in a moment" |
| `too_many_inflight` | "Too many requests in flight — wait for one to finish" |
| `quota_exceeded` | "Daily limit reached" |
| `rate_limited` | Retry after `Retry-After` seconds |
| `unsupported_media_type` | "That file type is not supported" |
| `payload_too_large` | "File is too large (50 MB max)" |

**Displaying results from `mode=ai`:**

```typescript
const { prediction, confidence, fake_probability } = result.result;
// prediction: "Real" | "Fake"
// confidence: 0–100 (percentage)
// fake_probability: 0–1
```

**Displaying results from `mode=audio`:**

```typescript
const { tempo, key, vocal, production } = result.result;
const bpm   = tempo?.bpm;
const keyName = key?.name;       // e.g. "A minor"
const lufs  = production?.loudness?.integrated_lufs;
const hasVocals = vocal?.has_vocals;
```

---

### Beat-upload workflow integration

Add AI detection to an existing beat-upload pipeline without changing the upload itself. The API accepts the audio after it is stored and runs analysis asynchronously.

**Pattern: fire-and-forget with webhook callback**

```python
# In your existing beat upload handler (Django/Flask/FastAPI/etc.)
import httpx

LABS_BASE = "https://your-labs-host/v1"
LABS_KEY  = os.environ["LABS_API_KEY"]

def on_beat_uploaded(beat_id: str, audio_path: str, genre: str | None = None) -> str:
    """
    Call after the beat is stored. Returns the LABS job id for status tracking.
    Raises on network or validation error; let the caller decide whether to retry.
    """
    with open(audio_path, "rb") as f:
        resp = httpx.post(
            f"{LABS_BASE}/analyses",
            headers={"X-API-Key": LABS_KEY,
                     "Idempotency-Key": beat_id},   # safe to retry
            files={"file": (os.path.basename(audio_path), f, "audio/mpeg")},
            data={
                "mode":        "full",
                "genre":       genre or "",
                "webhook_url": f"https://your-api.example/webhooks/labs/{beat_id}",
            },
            timeout=30,
        )
    resp.raise_for_status()
    return resp.json()["id"]
```

**Webhook receiver** (verify the signature before trusting the body):

```python
import hashlib, hmac, json
from fastapi import Request, HTTPException

LABS_SECRET = os.environ["LABS_WEBHOOK_SECRET"]

async def handle_labs_webhook(request: Request, beat_id: str):
    ts  = request.headers.get("X-Webhook-Timestamp", "")
    sig = request.headers.get("X-Webhook-Signature", "")
    body = await request.body()

    expected = hmac.new(
        LABS_SECRET.encode(),
        f"{ts}.".encode() + body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise HTTPException(403, "bad signature")

    job = json.loads(body)
    if job["status"] != "succeeded":
        return  # handle failure if needed

    result = job["result"]
    # Write to your DB
    await beats.update_one(
        {"_id": beat_id},
        {"$set": {
            "ai_detection": {
                "prediction":    result["prediction"],
                "confidence":    result["confidence"],
                "fake_probability": result["fake_probability"],
                "bpm":   result.get("musical", {}).get("rhythm", {}).get("bpm"),
                "key":   result.get("musical", {}).get("harmony", {}).get("key"),
                "lufs":  result.get("production", {}).get("loudness", {}).get("integrated_lufs"),
            },
            "labs_job_id": job["id"],
        }},
    )
```

**Idempotency.** Pass `Idempotency-Key: <your_beat_id>`. If your upload handler is called twice — on retry, on re-queue, on timeout — the API returns the original job rather than creating a second one.

**No webhook? Poll instead:**

```python
def wait_for_result(job_id: str, timeout_s: int = 600) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        resp = httpx.get(f"{LABS_BASE}/analyses/{job_id}",
                         headers={"X-API-Key": LABS_KEY})
        data = resp.json()
        if data["status"] in ("succeeded", "failed"):
            return data
        time.sleep(5)
    raise TimeoutError(f"job {job_id} did not complete in {timeout_s}s")
```

---

<p align="center"><sub>LABS is a product and property of Beat22.</sub></p>
