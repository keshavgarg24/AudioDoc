# Architecture

How the service is put together, and the reasoning behind the parts that are
not obvious.

---

## Shape

Detection is **tiered**. The cheap level runs first and the expensive one runs
only when it has to, which is the single most important structural fact about
the service.

```
                  ┌──────────┐
   client ──TLS──▶│  nginx   │──▶ uvicorn (1 worker)
                  └──────────┘         │
                                       ▼
                              ┌─────────────────┐
                              │  FastAPI app    │
                              │  auth + limits  │
                              └───┬─────────┬───┘
                                  │         │
              POST /v1/screen ────┘         └──── POST /v1/analyses
                     │                                    │
                     ▼                                    ▼
        ┌────────────────────────┐              ┌──────────────────┐
        │ LEVEL 1  (screen)      │              │   job queue      │
        │ ────────────────────── │              │  in-process, or  │
        │ one 16 kHz decode      │              │  SQS + MongoDB   │
        │  ├ fakeprint  (16 KB)  │              └────────┬─────────┘
        │  ├ cepstrum   (1.2 MB) │                       │
        │  ├ ensemble            │                       ▼
        │  ├ C2PA / container    │              ┌──────────────────┐
        │  │   / bandwidth       │              │ tiers.analyse()  │
        │  ├ policy              │              │  Level 1 first   │
        │  └ exit gate           │◀─────────────│  ───────────────  │
        │ ~1.5 s, no torch       │   decisive?  │  then LEVEL 2    │
        └───────────┬────────────┘   ──yes──▶ return
                    │ escalate                 │
                    ▼                          ▼
        ┌───────────────────────────────────────────────┐
        │ LEVEL 2  (deep)                               │
        │ ───────────────────────────────────────────── │
        │ beat track ─▶ plan 48 windows across the      │
        │               WHOLE track (spread)            │
        │   ├ Stage-1 MERT, stride-3 first pass         │
        │   ├ |logit| >= 5 ? ──yes──▶ done              │
        │   └ else: compute the skipped windows,        │
        │           reusing the first pass's embeddings │
        │ Stage-2 ─▶ logit ─▶ inconclusive band         │
        │ 22-90 s, 1.34 GB of weights                   │
        └───────────────────┬───────────────────────────┘
                            │
              ┌─────────────┼─────────────┐
              ▼             ▼             ▼
          MongoDB     object store     webhook
```

**Level 1 imports no torch.** That is deliberate and load-bearing: it is what
lets the free tier ship as a ~350 MB image rather than ~2.5 GB, which on an
autoscaling fleet is the difference between a few seconds of cold start and
tens of seconds. `labs/verdicts.py` exists for the same reason — the verdict
vocabulary lives in a module that imports nothing, so reading a constant does
not pull in transformers.

---

## Layers

| Package | Responsibility | Depends on |
|---|---|---|
| `api/` | HTTP only: parse, validate, delegate, serialise | `core`, `services` |
| `core/` | Configuration, authentication, upload staging, outbound URL vetting | nothing in the app |
| `screen/` | **Level 1**: the free tier. Two ONNX detectors, fusion, screens, policy, exit gate | `core` (deliberately not `ml/` or torch) |
| `tiers.py` | Routes a request through Level 1, then Level 2. Owns the short-circuit rules | `screen`, `ml` |
| `verdicts.py` | The shared verdict vocabulary. Imports nothing, on purpose | — |
| `ml/` | **Level 2**: model architecture, checkpoint resolution, the detector, the cascade | `core`, `analysis` |
| `worker.py` | The distributed analysis consumer. Serves no HTTP | everything |
| `audio/` | Beat tracking and segmentation | — |
| `analysis/` | Measurement passes that compose a report | `audio` |
| `artist/` | Genre models and derived commercial insight | `analysis` |
| `tools/` | One module per tool, plus the registry | `analysis` |
| `services/` | Jobs, the SQS queue, persistence, caching, verification | `core` |
| `cli/` | Key management, scheduled corpus jobs | `core`, `services` |

The rule is that `api/` holds no measurement logic and `analysis/` knows
nothing about HTTP. A route that grows a calculation, or an analysis module
that starts raising HTTP errors, is the signal that something belongs
somewhere else.

---

## Why analysis is asynchronous

An analysis is 40 seconds to 3 minutes on CPU. Load balancers and proxies cut
idle connections at 30 or 60 seconds by default, so a synchronous endpoint
fails in production in a way it never does in development.

So every submission returns a job id immediately, and the result is collected
by polling or delivered by webhook. `wait` exists as a convenience for a simple
client — the server holds the response for up to 25 seconds and returns the
result inline if the work finishes — but it falls back to the job id rather
than blocking longer, so a slow file can never turn into a timed-out request.

Uniform asynchrony is deliberate. Having one tool behave differently from the
rest is how clients end up with two code paths and one of them untested.

## Why the job queue has two backends

For a single container, an in-process `ThreadPoolExecutor` with a bounded,
TTL-evicting store is the right call: no extra infrastructure and no new
failure mode. It is still the default.

Its cost is not subtle, though, and it is specifically a *scaling* cost. Jobs
do not survive a restart and do not fan out across replicas, so "scale out"
adds capacity the load balancer can only reach for NEW submissions — work
already accepted by an overloaded container stays there, and a scale-IN event
destroys it. Neither is fixable inside one process.

So `services/queue.py` adds an SQS backend. The queue carries the WORK and a
MongoDB `jobs` collection carries the STATE, and **both** are required:
`distributed()` checks for each, because a job accepted by one API container
and finished by a worker is unreportable without shared state, and the
submitter would poll forever.

The store is bounded either way. An unbounded dict of finished jobs holding
full report payloads is a slow memory leak in a long-lived process.

## Why the worker is a separate process when distributed

An analysis is 30–90 s of saturated CPU. Inside the API process that has three
consequences:

- **The health check competes with the work.** Two analyses on a 4-core box and
  `/health` starts missing its deadline, so the load balancer removes a
  container that is working perfectly.
- **The scaling signal is wrong.** API containers should scale on request count
  and workers on queue depth. One process must pick one, and whichever it
  picks is wrong for the other half of its job.
- **A deploy is destructive.** Replacing an API container mid-analysis discards
  work the caller was told had been accepted.

So API containers validate, store and enqueue and never analyse; workers
analyse and never serve.

## Why one worker process

Each uvicorn worker would hold its own copy of the weights. A second process
doubles resident memory for no throughput gain, because the model is the
bottleneck, not the event loop. Concurrency is bounded inside the application
by `LABS_MAX_CONCURRENCY` and the job pool instead.

## Why threads and not processes for jobs

The work is dominated by torch operations that release the GIL. Threads share
the loaded model; processes would each need their own copy.

---

## Why Level 1 exists, and what it is allowed to decide

Level 1 is 1.2 MB of ONNX against Level 2's 1.34 GB, and ~1.5 s against
22-90 s. The economic argument is obvious. The interesting part is the
asymmetry in what it may conclude.

**It may end a request that is decisively AI.** Two independent
representations agreeing, no dissent, no veto, and the verdict surviving
perturbation is a conclusion the backbone would restate rather than revise.

**It may never end a request that looks human.** Both Level-1 models recognise
only the generators they were trained on, so their silence is not evidence — a
generator neither has seen is indistinguishable from human audio *to them*.
`human-made` therefore always escalates, and Level 1 never publishes an
exoneration on its own.

That asymmetry mirrors the cost asymmetry it was built for: on a marketplace,
wrongly flagging a human producer costs a customer and a public complaint;
missing one AI track costs very little.

### Fusion is not `max()`

`final = max(p_fakeprint, p_cepstrum)` is an OR gate. If either model
false-positives, the ensemble does. At 2% FPR each with partly independent
errors the union approaches ~4% — the false-accusation rate has doubled while
the change felt like an improvement.

Instead, agreement raises confidence and disagreement lowers it **and is
recorded**, because which model dissented says which failure mode you are in.
A CQT texture flagging without a spectral comb fits processed generator output
whose comb was smeared by resampling. A comb without the texture fits a
bitcrushed *human* track, since bitcrushing manufactures combs by the same
mechanism as vocoder upsampling. A `max()` reports those two identically.

### The exit gate

A decisive-looking verdict measured on one rendering of one file is not a
claim about the track. `ai1.mp3` scores a saturated 1.0 and its confidence
collapses to 0.23 under band limiting. So before Level 1 is allowed to end a
request, the same models re-score perturbed excerpts; anything that moves is
downgraded and escalated.

It runs *only* on the exit path, so the cost falls on the requests that were
about to skip the expensive tier anyway — measured at 0.69 s. The
perturbations are computed in the numpy domain rather than through ffmpeg,
because this is the ungated endpoint and every subprocess spawned on
attacker-supplied input is attack surface.

---

## Why Stage-1 spreads its windows, and cascades

Two separate defects, both inherited from upstream and both measured on this
repo's `audio/` folder.

**Coverage.** Upstream cut a fixed 10 s window at every surviving downbeat and
stopped at 48. Downbeats arrive ~2.5 s apart, so consecutive windows overlap
~75% and the 48 slots were spent long before the song ended:

```
mean redundancy   3.77x    (480 s of MERT input per ~127 s of unique audio)
mean coverage     0.77
at the 48 cap     7 of 10 tracks
```

For `1.mp3` the 48th window ended at 144 s of a 186 s track — Stage-1 rendered
its verdict having never heard the final 42 s. Nothing failed and nothing
logged; the report even said `coverage: 1.0`, because that field measured a
different pipeline.

`plan_starts` now distributes the same 48 windows across the whole track by
interpolating indices across the downbeat grid. Coverage went to **0.898–0.999
(mean 0.981)**, at identical cost and with the identical tensor shape Stage-2
was trained on.

The cap stays at **48**. Stage-2 was trained on exactly that sequence length,
so raising it feeds the SSM something longer than it ever saw. (This reverses
an earlier recommendation to raise it to 64.)

Note that the index selection is interpolated, not a constant step. For 54
downbeats into 48 slots, `ceil(54/48) = 2` silently selects 27 windows — half
the budget — and that cost real accuracy before it was caught.

**Redundancy.** The cascade exploits a measured fact: sensitivity to segment
count is purely a function of confidence.

```
|logit| >= 5   immune.     1.mp3 moves 0.004 between stride 1 and stride 4
|logit| <  2   unstable.   ai1 FLIPS at stride 2; ai4 collapses +4.57 -> +0.94
```

So Stage-1 runs at stride 3 first and escalates to full density only below the
threshold. Escalation is free of recompute because Stage-1 embeddings are
per-segment and **independent** — nothing couples them until Stage-2's
self-similarity matrix — so the second pass computes only the skipped windows
and reuses the rest. Escalating costs 1.0x of a full pass, not 1.33x.

Blanket decimation would buy the same time and is *not* equivalent: it is an
accuracy trade that happens to be invisible on the easy majority.

---

## Why the verdict has an inconclusive band

Stage-2 emits an unbounded logit that saturates near ±7.21. Publishing its
sign as a verdict gives a track the model has no opinion about the same
grammar as one it is certain of.

`ai1.mp3` forces the issue. It sits at `|logit| 0.4` and lands on opposite
sides of zero depending only on which windows Stage-1 was handed: **+0.393**
on upstream's first-48 plan, **−0.421** once the windows span the whole track.
Both readings are honest and neither is a finding — but the old output turned
that into "Fake" one day and "Real" the next.

`|logit| < 2.0` now reports `verdict: inconclusive`. `prediction` is unchanged
for existing callers. This is also why the coverage fix is not a regression
despite moving that track across zero: no published claim changes.

The threshold is where the pre-existing reliability heuristic already docked
20 points for sitting "near the decision boundary" — this makes that judgement
binding rather than advisory. Six of ten measured tracks saturate past
`|7.2|`, so the band costs very little coverage.

---

## Storage tiers

A full report is 200 KB to 2 MB of JSON, most of it numeric series that exist
only to draw charts. Storing that verbatim, per analysis, is what turns a
working database into an expensive one. So each analysis is written as three
tiers:

**Tier 1 — flat scalars**, at the top of the document, indexed. "Every
AI-flagged track above 130 BPM last month" is an index scan, never a scan of
compressed blobs.

**Tier 2 — the trimmed report**, zlib-compressed into one binary field. Heavy
numeric series are dropped first: the self-similarity matrix, spectrum curves,
RMS and BPM series, per-beat deviation histograms. Typically 200 KB–1 MB of
JSON becomes 15–60 KB stored.

**Tier 3 — the audio itself**, in object storage, referenced by key. Never in
MongoDB. Off unless a bucket is configured.

Everything degrades. With no `LABS_MONGO_URI` the service runs in memory and
writes nothing.

---

## Deduplication

Two independent caches, both keyed on the SHA-256 of the uploaded bytes.

**Analyses** are matched on `(sha256, mode)`. Mode matters: an `audio` run
carries no verdict, so returning one to a caller who asked for `ai` would
answer a different question than the one asked.

**Tool results** are matched on `(tool, sorted digests, options fingerprint)`.
That combination is exact — the same file with the same options always produces
the same key, and any change to either produces a different one — so a hit can
return a stored result with no risk of answering the wrong question.

The tool name is part of the key on purpose. The same audio measured by a
different tool is a different question and must run.

Alongside both, an `audio_files` record is upserted per unique file, carrying
its duration, sample rate, channel count and how many times it has been seen.
That makes "have we processed this before, by anything?" a single indexed
lookup.

**One field carries all of this: `audio.sha256` on the stored analysis.** It is
written unconditionally, whether or not object storage is configured. Writing
it only as a by-product of an upload is how deduplication silently stops
working — no error, no log line, just every submission paying full price.

---

## Configuration

Settings are dataclasses built fresh from the environment on each
`get_settings()`, and callers hold onto the result. Nothing is cached at module
scope, so changing an environment variable and restarting is sufficient; there
is no second place to look.

`authenticate()` and `stage_upload()` take an optional `settings`. A caller
holding its own instance is authoritative. Falling back to a fresh
`get_settings()` inside them would silently ignore the caller's configuration
and re-read the process environment — which is exactly the bug that makes a
test pass while production behaves differently.

---

## Untrusted input

Uploads are the one place untrusted bytes enter, and they are handed to ffmpeg
and libsndfile, which are large C parsers with a history of memory-safety bugs.

Two cheap filters run before the file reaches them:

- a **size cap**, applied as the stream is consumed rather than after, so an
  oversized upload is refused without ever being written in full;
- a **container check** on the leading bytes, because the extension is a claim
  by the caller.

Neither substitutes for the decoder's own validation. They keep obvious junk
away from the expensive, riskier code. The container check runs on the first
chunk, so a malformed 50 MB upload costs one buffer rather than a full disk
write.

Staged files carry a shared prefix and the startup sweeper globs for it, so
uploads orphaned by a crash are cleaned up. The prefix and the glob are defined
in one place because a change to one and not the other stops the sweep from
finding anything, with no visible symptom until the disk fills.

---

## Outbound requests

`webhook_url` is a caller-supplied URL that this service fetches from inside
the VPC, holding an instance role. That is a server-side request forgery
primitive unless it is guarded.

What is enforced, in `core/net.py`:

- scheme must be `https` (or `http` behind an explicit opt-in);
- the hostname is **resolved**, and every address it resolves to must be
  globally routable — checking the literal string is not enough, since a
  hostname under the caller's control can point at loopback;
- IPv6 wrappers are unwrapped before judging, because a v4-mapped or 6to4
  address embeds a v4 target that `is_global` alone reports incorrectly;
- redirects are not followed, since a permitted host can redirect to a blocked
  one;
- known service ports are refused outright.

Validation runs twice: at submission, so the caller gets a `422` they can act
on, and again immediately before delivery, because minutes pass in between and
that is ample time to repoint a DNS record.

Rejections deliberately do not echo the resolved address. Naming it would turn
the endpoint into an internal network scanner with a clean oracle.

One residual gap is documented rather than papered over: the request keeps the
original hostname rather than substituting the resolved IP, because swapping in
a literal address breaks TLS certificate verification. That leaves a narrow
re-resolution window. Closing it needs a pinning transport adapter. The
exposure is a millisecond race for the privilege of receiving one report, while
the attack this actually defends against — naming an internal host outright —
is fully blocked.

---

## Model loading

Weights resolve in order, first hit wins: an explicit path, then the checkpoint
directory, then the object-store mirror, then the model hub pinned to a
revision.

Pinning the revision matters. Without it, an upstream change silently swaps the
model under a running deployment. `LABS_OFFLINE=true` in production refuses the
hub entirely, so a missing file fails loudly at boot instead of quietly pulling
gigabytes mid-deploy.

Three caches must all point inside the mounted volume — the checkpoints, the
backbone's cache, and the beat tracker's `torch.hub` cache. If any points
outside, that artifact is re-downloaded every time the container is recreated.
The Dockerfile sets all three.

Both models are warmed at startup with correctly shaped dummy input, so the
first real request does not pay for lazy kernel initialisation.

### Numerics are load-bearing

`LABS_AUTOCAST_DTYPE` is off by default and **changing it changes verdicts**.
bfloat16 keeps float32's exponent range and loses mantissa bits, so the
arithmetic is close but not identical — and the output is a calibrated
probability with a threshold applied to it. A small shift near the boundary
flips the answer for tracks that sit there.

Treat enabling it as a model change: run the labelled set, compare verdicts and
logits against fp32, and only then turn it on. Same for the pinned versions of
torch and transformers, which is why they are pinned exactly rather than to a
floor.

---

## Threading

`LABS_TORCH_THREADS` should be the **physical** core count, not the vCPU count.
Torch defaults to one thread per logical CPU. The backbone is GEMM-bound, and
two hyperthreads sharing one core's vector units contend rather than scale, so
on every current x86 instance type the right value is half the vCPU count.

It defaults to `0` — leave torch's default alone — because the correct number
is a property of the host, not of the code. The deployment sets it.
