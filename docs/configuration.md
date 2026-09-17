# Configuration

Every setting is an environment variable prefixed `LABS_`. All have defaults
except where marked. Settings are read fresh on startup, so changing one means
restarting the process — there is no second place to look.

Booleans accept `1`, `true`, `yes`, `on` (case-insensitive); anything else is
false. A malformed integer falls back to its default rather than stopping the
service booting.

---

## Server

| Variable | Default | Notes |
|---|---|---|
| `LABS_HOST` | `0.0.0.0` | Bind address |
| `LABS_PORT` | `8000` | |
| `LABS_WORKERS` | `1` | Leave at 1. Each worker holds its own copy of the weights. |
| `LABS_LOG_LEVEL` | `INFO` | |
| `LABS_API_VERSION` | `1.1.0` | Reported in every response, so a verdict traces to a build |
| `LABS_EAGER_LOAD` | `true` | Load weights at startup rather than on first request |

## Authentication

| Variable | Default | Notes |
|---|---|---|
| `LABS_REQUIRE_AUTH` | `false` | **Set to `true` in production** |
| `LABS_API_KEYS` | — | Static keys, comma separated. Bootstrap path. |
| `LABS_CORS_ORIGINS` | — | Exact origins, comma separated |
| `LABS_RATE_LIMIT_PER_MIN` | `60` | Per key. `0` disables. |
| `LABS_MAX_INFLIGHT_PER_KEY` | `4` | Concurrent analyses per key |

Auth is enforced when `LABS_REQUIRE_AUTH=true` or any static key is set. With
neither the deployment is open.

**On `LABS_CORS_ORIGINS=*`:** credentials are disabled automatically when it is
a wildcard, and a warning is logged. This is not a formality. Starlette does
not send `Access-Control-Allow-Origin: *` alongside credentials — the spec
forbids it — so it reflects the requesting origin instead. The effect of `*`
plus credentials is therefore not "no CORS restriction" but "every origin on
the internet is allow-listed, with credentials, and may read the responses".
Name your caller's exact origin in production.

### Tier gating

| Variable | Default | Notes |
|---|---|---|
| `LABS_GATE_DEEP` | `true` | `mode=ai` and `mode=full` require the `deep` scope. |

**Breaking change.** Keys carrying `analyze` get `screen` but **not** `deep`,
and must be granted it explicitly:

```bash
labs-keys create --name "upload loop" --scopes analyze,read,deep
```

`analyze` itself is unchanged, so `/v1/tools` and `mode=audio` are untouched.
`LABS_GATE_DEEP=0` restores the previous behaviour deployment-wide, which is
right for local development and for a deployment already behind its own
gateway.

## Storage

| Variable | Default | Notes |
|---|---|---|
| `LABS_MONGO_URI` | — | **Required for deduplication and retention** |
| `LABS_MONGO_DB` | `labs` | |
| `LABS_RESULT_TTL_DAYS` | `0` | **0 = keep forever.** Positive values let a TTL index drop analyses |
| `LABS_STORE_AUDIO` | `true` | Inert until a bucket is set |
| `LABS_AUDIO_BUCKET` | — | Object storage for analysed audio |
| `LABS_AUDIO_PREFIX` | `uploads` | Key prefix within the bucket |
| `LABS_AUDIO_TTL_DAYS` | `0` | **0 = keep forever.** Drives the bucket lifecycle rule |
| `LABS_S3_REGION` | `AWS_REGION` | |

### Retention defaults to keeping everything

All three retention settings — `LABS_RESULT_TTL_DAYS`, `LABS_AUDIO_TTL_DAYS`
and `LABS_SCREEN_RETAIN_S` — default to `0`, meaning **keep forever**.

The audio, the reports and the features derived from them are the dataset.
They are what deduplication reads, what an appeal is re-analysed from, and the
only corpus that exists if these models are ever retrained. A report costs
60–90 s of CPU to produce and a few kilobytes to store, so expiring one trades
something expensive for something cheap.

The mechanism is worth understanding: a MongoDB TTL index **ignores documents
that have no `expires_at` field**. The writers only set that field when a
positive retention is configured, so the indexes are created but stay inert.
Two consequences:

- Turning retention on later is a config change, not an online index build on
  a large collection.
- It applies only to documents written *after* the change. Existing records
  have no `expires_at` and survive.

Set a positive value only when a retention or privacy policy requires
deletion. A TTL index deletes; it does not archive.

Without `LABS_MONGO_URI` the service runs in memory and writes nothing —
including no deduplication, so every submission runs the full pipeline even for
byte-identical audio.

## Checkpoints

| Variable | Default | Notes |
|---|---|---|
| `LABS_MODELS_DIR` | `/models` | Parent of every model cache |
| `LABS_CKPT_DIR` | `checkpoints` | |
| `LABS_MODELS_S3_URI` | — | Mirror; fetched in one pass at startup |
| `LABS_CKPT_REVISION` | — | **Pin this.** Otherwise the model can change under you. |
| `LABS_OFFLINE` | `false` | **Set to `true` in production** once weights are mirrored |
| `LABS_STAGE1_PATH` | — | Explicit override |
| `LABS_STAGE2_PATH` | — | Explicit override |
| `LABS_HF_TOKEN` | — | Only if the weights repository is private |

Resolution order per stage: explicit path → checkpoint directory → object-store
mirror → model hub at the pinned revision.

### Checkpoint provenance

| Variable | Default | Notes |
|---|---|---|
| `LABS_STAGE1_SHA256` | *(unset)* | Expected digest. Unset means no check. |
| `LABS_STAGE2_SHA256` | *(unset)* | |
| `LABS_ENFORCE_CKPT_DIGEST` | `true` | Refuse to start on a mismatch. |

Not the same as pinning `LABS_CKPT_REVISION`. A revision pins what we **ask
for**; a digest pins what we **got**, and they cover different failures — a
revision cannot detect a corrupted download, a truncated S3 copy, a stale file
on a reused volume, or a mirror bucket somebody else can write to.

A silently different checkpoint does not error. It answers confidently and
wrongly, which is why hashing 1.29 GB once at startup is worth the few seconds.

```bash
shasum -a 256 checkpoints/Stage-*.ckpt
```

The pair this repo was measured against:

```
Stage-1  f9099df5c618a2f92bcd8f4ba48d1c6606f2e4610385b8eea4a03f1a7319629f
Stage-2  ed133c261c5d367fc6adf53813a5c93b62a59de5bef546cf5899a5c157eba7a0
```

## Model

| Variable | Default | Notes |
|---|---|---|
| `LABS_DEVICE` | `auto` | `auto`, `cpu`, `cuda` |
| `LABS_TORCH_THREADS` | `0` | **Physical** cores, not vCPUs. `0` leaves torch alone. |
| `LABS_MAX_CONCURRENCY` | `2` | Concurrent inferences |
| `LABS_BACKBONE_BATCH` | `8` | Segments per forward pass |
| `LABS_MAX_SEGMENTS` | `48` | Ceiling on analysed windows |
| `LABS_AUTOCAST_DTYPE` | — | **Leave empty.** See below. |
| `LABS_SIGNAL_ANALYSIS` | `true` | Disable for a verdict-only deployment |

**`LABS_TORCH_THREADS`.** Torch defaults to one thread per logical CPU. The
backbone is GEMM-bound and two hyperthreads on one core contend rather than
scale, so the right value is the physical core count — half the vCPU count on
every current x86 instance type. Set `OMP_NUM_THREADS` and `MKL_NUM_THREADS` to
match.

**`LABS_AUTOCAST_DTYPE`.** Off by default, and **turning it on changes
verdicts.** bfloat16 has float32's exponent range with fewer mantissa bits, so
results are close but not identical — and the output is a calibrated
probability with a threshold applied to it. Tracks near the boundary flip.
Treat enabling it as a model change: validate against labelled audio first
(see [testing.md](testing.md)). `float16` is the worse choice on CPU — narrower
exponent range, no accelerated path, and it overflows where bfloat16 does not.

## Level 1 — the screen tier

Two small ONNX detectors, ~1.2 MB of weights, no torch. Free and ungated, so
its cost is a product decision as much as an engineering one.

| Variable | Default | Notes |
|---|---|---|
| `LABS_SCREEN` | `true` | The whole tier on/off. |
| `LABS_SCREEN_MODELS_DIR` | *(empty)* | Override directory for the Level-1 graphs. Empty means the weights that ship inside the package at `labs/screen/weights/`, which is the normal answer — the free tier must work from a bare `pip install` and from a container with no volume. |
| `LABS_SCREEN_CONCURRENCY` | vCPUs, capped at 8 | In-flight screens before `429 screen_busy`. Load shedding, not a rate limit. |
| `LABS_SCREEN_ONNX_THREADS` | `0` (ORT default) | Set when packing several containers onto one box; otherwise ORT's default is right. |
| `LABS_SCREEN_CNN` | `true` | The second detector. Disabling it leaves one model and no cross-check, and confidence is reduced to say so. |
| `LABS_SCREEN_CNN_SEGMENTS` | `16` | Windows the CNN median-pools over. Upstream used 40; the pooling is a median, which converges quickly, and CQT is the dominant cost in this tier. |
| `LABS_SCREEN_MAX_DURATION_S` | `300` | Bounds the STFT on a long file. The fakeprint is a time-average, so extending it changes little. |

### Fusion and policy

| Variable | Default | Notes |
|---|---|---|
| `LABS_SCREEN_FAKEPRINT_WEIGHT` | `0.55` | |
| `LABS_SCREEN_CNN_WEIGHT` | `0.45` | |
| `LABS_SCREEN_AI_THRESHOLD` | `0.80` | Asymmetric on purpose. Wrongly flagging a human producer is the expensive error. |
| `LABS_SCREEN_HUMAN_THRESHOLD` | `0.20` | The gap between the two is reported as `inconclusive`, not rounded. |
| `LABS_SCREEN_MIN_CONFIDENCE` | `0.45` | Below this, no verdict is published. Enforced **twice** — once when the policy bands, and again after the robustness penalty, which can move confidence after the fact. See `policy.reband`. |
| `LABS_UNCERTAIN_MARGIN` | `0.15` | Half-width of the `uncertain` band in `assessment.band`. Shared with Level 2 so one legend fits both tiers. Reporting only: it never changes `verdict` and never suppresses `label`. |

These thresholds move `verdict`. **They do not move `label`**, which is always
the side of 0.5 and is computed in `assessment.py` from the score alone. An
operator retuning the bars changes how cautious the service is about
committing, not what it thinks.

### Early exit

| Variable | Default | Notes |
|---|---|---|
| `LABS_SCREEN_EXIT_ON_AI` | `true` | Whether Level 1 may end an `ai` request. Set `false` to force every request to reach Level 2. |
| `LABS_SCREEN_EXIT_CONFIDENCE` | `0.85` | The bar. |

Only the AI side can exit. A confident `human-made` always escalates — both
Level-1 models recognise only the generators they were trained on, so their
silence is not evidence, and Level 1 never publishes an exoneration alone.

Anything that proposes to exit is re-scored under perturbation first, and
anything whose verdict moves escalates instead. Not optional: `ai1.mp3` scores
a saturated 1.0 and its confidence collapses to 0.23 under band limiting.

### Screens

| Variable | Default | Notes |
|---|---|---|
| `LABS_SCREEN_C2PA` | `true` | Needs `c2pa-python`; reports "not installed" otherwise. A signed manifest naming a generative tool is decisive on its own. |
| `LABS_SCREEN_CONTAINER` | `true` | Encoder strings, duration clustering, tag sparsity. Weak priors, recorded to explain a verdict rather than drive one. |
| `LABS_SCREEN_BANDWIDTH` | `true` | Needs its own short wideband read: at 16 kHz every file appears to stop at Nyquist. |

---

## Level 2 — the deep tier

### Segment planning

| Variable | Default | Notes |
|---|---|---|
| `LABS_MAX_SEGMENTS` | `48` | **Do not raise.** Stage-2 was trained on exactly this sequence length; more is out of distribution, not merely slower. |
| `LABS_SEGMENT_SPREAD` | `true` | Distribute the 48 windows across the WHOLE track. Fixes measured coverage from 0.77 to ~0.98 at identical cost. `false` restores upstream's first-48 behaviour for A/B work. |
| `LABS_SEGMENT_STRIDE` | `1` | Extra decimation. Genuinely trades accuracy for speed — borderline tracks move — so leave it at 1 and use the cascade instead. |

### The cascade

| Variable | Default | Notes |
|---|---|---|
| `LABS_CASCADE` | `true` | Two-pass Stage-1. |
| `LABS_CASCADE_STRIDE` | `3` | First-pass decimation. |
| `LABS_CASCADE_ESCALATE_BELOW` | `5.0` | `\|logit\|` below which full density is computed. Measured: at or above 5 the verdict is immune to segment count; below 2 it can flip. |
| `LABS_CASCADE_MIN_SEGMENTS` | `12` | Below this the first pass saves nothing and still costs a Stage-2 call. |

### Verdict banding

| Variable | Default | Notes |
|---|---|---|
| `LABS_DEEP_BANDING` | `true` | Report `inconclusive` near the decision boundary. |
| `LABS_INCONCLUSIVE_BELOW` | `2.0` | `\|logit\|` band. Tracks inside it land on either side of zero depending only on which windows were analysed, so the sign is not a verdict. |
| `LABS_UNCERTAIN_MARGIN` | `0.15` | Shared with Level 1. See above. |

`LABS_INCONCLUSIVE_BELOW` is expressed in logit space, and
`assessment.band` grades the score. The two are kept consistent by pushing the
configured threshold through the **same sigmoid the score uses**
(`_decisive_bounds` in `ml/detector.py`) rather than hardcoding 0.88 — so
retuning this variable moves the band with it instead of leaving them
disagreeing about the same track.

### Turning the band off

Setting `LABS_DEEP_BANDING=false` makes `verdict` the bare sign of the logit,
with no `inconclusive`. **You almost certainly do not need to do this.**
`assessment.label` already gives you the unconditional binary call on every
response, with the band intact alongside it, so the usual reason for reaching
for this switch is already served. Disabling it only removes your ability to
tell a coin flip from a finding.

---

## Distributed mode (SQS)

Enabled when `LABS_SQS_QUEUE_URL` **and** `LABS_MONGO_URI` are both set. Both
are required: SQS carries the work, MongoDB carries the state, and with only
the first a job accepted by one container and finished by another is
unreportable.

| Variable | Default | Notes |
|---|---|---|
| `LABS_SQS_QUEUE_URL` | — | Set to enable. |
| `LABS_SQS_REGION` | falls back to `LABS_S3_REGION` / `AWS_REGION` | |
| `LABS_SQS_VISIBILITY_S` | `900` | **Must exceed the slowest analysis**, or SQS hands the message to a second worker mid-run and the track is analysed twice at full cost. |
| `LABS_AUDIO_BUCKET` | — | **Required** when distributed. The worker is a different container and cannot read the API's temp directory. |

---

## Uploads

| Variable | Default | Notes |
|---|---|---|
| `LABS_MAX_UPLOAD_MB` | `50` | Keep the proxy's limit above this |
| `LABS_MAX_DURATION_S` | `900` | |
| `LABS_MIN_DURATION_S` | `2.0` | |
| `LABS_ALLOWED_SUFFIXES` | `.wav,.mp3,.flac,.m4a,.aac,.ogg,.opus` | |

If the reverse proxy's body limit is below `LABS_MAX_UPLOAD_MB`, the proxy
rejects oversized uploads with a bare HTML `413` and the caller never sees the
JSON error envelope.

## Jobs

| Variable | Default | Notes |
|---|---|---|
| `LABS_JOB_WORKERS` | `2` | Pool size |
| `LABS_JOB_MAX` | `500` | Retained jobs before eviction |
| `LABS_JOB_TTL_S` | `3600` | Age at which a finished job is evicted |

## Webhooks

| Variable | Default | Notes |
|---|---|---|
| `LABS_WEBHOOK_SECRET` | — | **Required if callers use webhooks.** Without it deliveries are unsigned. |
| `LABS_WEBHOOK_RETRIES` | `3` | |
| `LABS_WEBHOOK_ALLOW_HTTP` | `false` | Permits plain HTTP destinations |
| `LABS_WEBHOOK_ALLOW_PRIVATE` | `false` | Permits private/loopback destinations |

The last two are escape hatches, off by default and deliberately awkward. A
service-mesh sidecar on loopback is the only case that has justified the
second. Turning it on disables the SSRF address check.

---

## Production checklist

```bash
LABS_REQUIRE_AUTH=true
LABS_CORS_ORIGINS=https://your-caller.example   # never *
LABS_MONGO_URI=mongodb+srv://…                  # or no dedup, no retention
LABS_WEBHOOK_SECRET=$(openssl rand -hex 32)     # if webhooks are used
LABS_CKPT_REVISION=<commit sha>                 # pin the model
LABS_OFFLINE=true                               # after mirroring weights
LABS_TORCH_THREADS=<physical cores>
LABS_AUTOCAST_DTYPE=                            # empty unless validated
```
