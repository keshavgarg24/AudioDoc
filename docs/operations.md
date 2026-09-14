# Operations

What to check, in what order, when something is wrong.

---

## Health

| Endpoint | Auth | Answers |
|---|---|---|
| `GET /health` | none | Is the process up? |
| `GET /v1/ready` | none | Can it take an analysis? |
| `GET /v1/diagnostics` | `admin` | Everything else |

`/health` is always `200` if the process is alive. It is deliberately thin
because it is unauthenticated: it says whether the service is up and whether
the model is ready, and nothing about how the detector is built or where its
artifacts live.

`/v1/diagnostics` carries checkpoint provenance, the mirror URI, storage
reachability and queue detail. It requires `admin` rather than merely being
authenticated, because an `analyze` key belongs to an ordinary API consumer and
the model's provenance is not theirs to read.

```bash
curl -fsS https://host/v1/diagnostics -H "X-API-Key: $ADMIN_KEY" | jq
```

---

## Key management

```bash
labs-keys create --name "partner" --scopes analyze,read --quota-per-day 5000
labs-keys list
labs-keys revoke --fingerprint labs_live_AbC123...
```

Inside a container, prefix with
`docker compose -f deploy/docker-compose.yml exec api`.

A key is printed once, at creation. Only a SHA-256 digest is stored, so a
leaked database does not leak usable keys — and a lost key cannot be recovered,
only replaced.

Revocation takes effect on the next request. There is no cached allow-list to
wait out.

---

## Rotating a leaked key

1. `labs-keys create` a replacement and give it to the integration.
2. Confirm the new key is in use — `GET /v1/usage` with it, or watch the logs.
3. `labs-keys revoke --fingerprint <old>`.

In that order. Revoking first takes the integration down until it redeploys.

If `LABS_WEBHOOK_SECRET` leaks, rotate it and tell every receiver, because
their signature check starts failing the moment it changes. If `LABS_API_KEYS`
(static, from the environment) leaks, edit `.env` and restart — those are not
revocable individually.

---

## Tier-specific symptoms

### `/v1/screen` returns 429 `screen_busy`

Load shedding, not a rate limit — the instance is at
`LABS_SCREEN_CONCURRENCY` (default: vCPUs, capped at 8). Retry immediately;
the queue is deliberately in FRONT of the work rather than inside it.

Sustained, it means the screen fleet is undersized. Scale on
`ALBRequestCountPerTarget`, not CPU: Level 1 saturates a core for ~1.5 s then
idles, so average CPU over a 60 s window badly under-reports a container that
is at its concurrency limit.

### `/v1/health` shows `model.installed: false`

A screen-only container (`--target screen`). Healthy, not degraded — it has no
torch by design. `/v1/ready` returns `{"ready": true, "tiers": ["screen"]}`.
Deep modes answer `501 deep_tier_unavailable`.

If you did *not* deploy the screen image, the deep dependencies failed to
install and the container is serving the free tier only.

### Appeals return 503 `appeals_unavailable`

Appeals need `LABS_MONGO_URI`. Without it there is nowhere to retain a screen,
so `/v1/screen` issues no `screen_id` either. Callers can still submit the
file to `/v1/analyses`.

### The overturn rate is climbing

`GET /v1/feedback/summary` (admin). This is a measurement of the detector, not
of the UI: it is the fraction of *disputed* Level-1 exits that Level 2
reversed.

A rising rate means Level 1 is exiting early on tracks it should not. The lever
is `LABS_SCREEN_EXIT_CONFIDENCE` (default 0.85) — raise it, or set
`LABS_SCREEN_EXIT_ON_AI=0` to stop early exit entirely while you investigate.
Neither change loses any accuracy; both cost throughput, because more requests
reach the backbone.

Watch the log line `Appeal OVERTURNED for screen ...`. It is a warning rather
than info precisely so a cluster of them is visible without a dashboard.

### Deep analyses are queued but no workers appear

Check in this order:

1. `ApproximateAgeOfOldestMessage` — the actual SLO. Depth says how much work
   there is; this says how long the unluckiest caller has waited.
2. The worker ASG is at `max_size`. Raise `worker_max_size`.
3. The warm pool is empty (`warm_pool_size` too small for the burst), so every
   scale-out is paying a 3-5 minute cold start.
4. Workers are crash-looping. A worker exits `2` when
   `LABS_SQS_QUEUE_URL` or `LABS_MONGO_URI` is missing — it refuses to start
   rather than silently dropping the queue.

### Messages in the DLQ

Something about those files breaks the pipeline deterministically, so they
failed three times and were parked. They need a human, not a retry. Pull one
and run it locally through `mode=full`.

---

## Common symptoms

### Every request returns 503 `model_loading`

Weights are not resident. Check `/v1/diagnostics` for the model error, then the
logs.

Usual causes: `LABS_OFFLINE=true` with the mirror not populated; checkpoint
paths pointing outside the mounted volume; the volume not mounted at all.

`mode=audio` keeps working throughout — it never touches the detector — so
signal analysis is available while this is being fixed.

### The same audio is analysed twice

Deduplication needs MongoDB. Confirm `LABS_MONGO_URI` is set and reachable:

```bash
curl -fsS https://host/v1/diagnostics -H "X-API-Key: $ADMIN_KEY" | jq .storage
```

`{"enabled": false}` means nothing is being stored and every submission runs in
full. Startup logs a warning for exactly this case.

If storage is healthy but repeats still re-run, check that the stored analyses
carry the hash:

```javascript
db.analyses.findOne({}, { "audio.sha256": 1 })
```

A null there means dedup cannot match. Every analysis should have it,
regardless of whether object storage is configured.

Note that matching is byte-exact. A re-encode or a re-export of the same song
is a different file and will be analysed again — that is the design, not a
fault.

### Analyses are slow

Expect 40 seconds to 3 minutes depending on length. Slower than that:

- Check `LABS_TORCH_THREADS` is the **physical** core count. Left at the
  default, torch spawns one thread per logical CPU and hyperthreads contend on
  the matrix multiplications the backbone is bound by.
- Check `OMP_NUM_THREADS` and `MKL_NUM_THREADS` match it.
- Check the host is not oversubscribed — `docker stats` during a run.
- Long files cost proportionally more, bounded by `LABS_MAX_SEGMENTS`.

### 429 `too_many_inflight`

The key is at its concurrent-analysis limit. This is a concurrency bound, not a
rate: the caller should wait for a job to finish rather than backing off on a
timer. Raise `LABS_MAX_INFLIGHT_PER_KEY` if the workload genuinely needs it and
the host has the cores.

### Webhooks are not arriving

Check delivery in the logs — search the job id. Common causes, in order:

- The URL was refused. Submission returns `422` for a destination that resolves
  somewhere it may not; if that was ignored, nothing was ever sent.
- The receiver returned non-2xx. Three attempts with backoff, then it stops.
- The receiver's signature check is failing because `LABS_WEBHOOK_SECRET`
  changed.

A failed webhook never fails the job. The result is always readable by polling,
which is the fallback to point an integration at.

### Memory climbing

The model is a fixed resident cost. Growth beyond it usually means retained
jobs. `LABS_JOB_MAX` and `LABS_JOB_TTL_S` bound the store; lower them if
results are large and traffic is high.

Check for staged uploads that were never cleaned up:

```bash
docker compose exec api sh -c 'ls -la /tmp/labs-* 2>/dev/null | head'
```

Files older than an hour are swept at startup. A pile of fresh ones means jobs
are failing before their cleanup runs.

### Disk filling

Logs first — the compose file caps them at 20 MB × 5 per service. Then the
weights volume, which should be around 2 GB and stable. Then `/tmp`.

---

## Logs

```bash
docker compose -f deploy/docker-compose.yml logs -f api
docker compose -f deploy/docker-compose.yml logs -f --since 15m api
```

Every response carries `X-Request-Id`, echoing the caller's if they sent one.
It is the handle for tracing one request end to end, and the thing to ask a
caller for.

Worth watching at startup:

- model load time and the device chosen
- checkpoint provenance
- warm-up completion
- `Persistence configured but unreachable` — dedup and retention are off
- `LABS_MONGO_URI is not set` — same, deliberately

---

## What to monitor

### Tiered deployment

| Signal | Where | Why |
|---|---|---|
| `ApproximateAgeOfOldestMessage` | SQS | The user-facing SLO. Alarm at 600 s. |
| Backlog per worker | metric math | The scale-out trigger. High is normal; *stuck* high means `max_capacity`. |
| DLQ depth | SQS | Any message here needs a human. |
| `screen_busy` 429 rate | ALB / app logs | Screen fleet undersized. |
| Overturn rate | `/v1/feedback/summary` | Level 1 exiting early when it should not. |
| Warm pool size | ASG | Empty during bursts means every scale-out is a cold start. |


| Signal | Source | Alert when |
|---|---|---|
| Liveness | `GET /health` | Non-200 |
| Readiness | `GET /v1/ready` | 503 for more than ~10 minutes |
| Queue depth | `/v1/diagnostics` → `queue.total` | Climbing without draining |
| Storage | `/v1/diagnostics` → `storage.ok` | False |
| 5xx rate | Proxy logs | Any sustained level |
| Memory | Host | Above ~80% of the limit |

Queue depth is the useful early warning. A queue that grows and does not drain
means work is arriving faster than the cores can process it, and the answer is
more cores rather than more processes.
