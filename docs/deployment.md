# Deployment

CPU only. Two tiers with very different cost shapes, and the whole point of
the architecture is to stop paying for the expensive one when the cheap one
is sufficient.

- [The two tiers](#the-two-tiers)
- [Choosing a topology](#choosing-a-topology)
- [Single container](#single-container)
- [AWS, autoscaling](#aws-autoscaling)
- [Cost](#cost)
- [Concurrency and queueing: what a user actually experiences](#concurrency-and-queueing-what-a-user-actually-experiences)
- [Instance choice, and the Intel/Graviton trade-off](#instance-choice-and-the-intelgraviton-trade-off)
- [Configuration reference](#configuration-reference)
- [Operational runbook](#operational-runbook)

---

## The two tiers

| | Level 1 (`screen`) | Level 2 (`deep`) |
|---|---|---|
| Models | 2 ONNX graphs, **1.2 MB** | MERT-v1-95M + FusionSegmentTransformer, **1.29 GB + 47 MB** |
| Dependencies | numpy, scipy, librosa, ORT | + torch, transformers, pytorch-lightning |
| Image | **~350 MB** | ~2.5 GB |
| Measured cost | **1.07–3.25 s/track** (mean 1.54) | 24.5–41.9 s when the first pass is confident, 65–71 s escalated |
| Startup | < 1 s | ~10–12 s model load |
| Transport | **synchronous** — answer in the response | asynchronous — job id, then poll |
| Gating | free, `screen` scope (anonymous holds it) | billable, `deep` scope |

Level 1 needs no torch, which is not a detail: it is what makes the free tier
affordable to serve and fast to scale. A 350 MB image pull is a few seconds;
2.5 GB is tens of seconds of cold start on every scale-out event.

### Why a confident Level-1 "human" does not end the request

Only the AI side of Level 1 can short-circuit. Both Level-1 models recognise
only the generators they were trained on, so their silence is not evidence: a
generator neither has seen looks exactly like human audio *to them*. Level 1
therefore never publishes an exoneration on its own, and `human-made` always
escalates. See [`screen/policy.py`](../src/labs/screen/policy.py).

The AI side can exit, but only after the exit gate
([`screen/robustness.py`](../src/labs/screen/robustness.py)) re-scores
perturbed excerpts and confirms the verdict holds. This is not theoretical:
`ai1.mp3` scores a saturated 1.0 and its confidence collapses to 0.23 under
band limiting, so it escalates rather than skipping the model that could
settle it.

---

## Choosing a topology

| Volume | Topology | Why |
|---|---|---|
| < ~500 tracks/day, one box | **Single container** | No SQS, no worker fleet, no new failure modes. The in-process queue is genuinely the right answer here. |
| Bursty, or more than one instance | **AWS autoscaling** | The in-process queue cannot fan out and loses accepted work on scale-in. |
| Free tier is the bulk of traffic | **Autoscaling, screen fleet separate** | A burst of anonymous screens scales a 350 MB service instead of a 2.5 GB one. |

The single-container path is unchanged and still supported. The service picks
its own mode: distributed the moment `LABS_SQS_QUEUE_URL` **and**
`LABS_MONGO_URI` are both set, in-process otherwise. Both are required —
SQS carries the work, MongoDB carries the state, and with only the first a job
accepted by one container and finished by another is unreportable, so the
submitter polls forever. See
[`services/queue.distributed()`](../src/labs/services/queue.py).

---

## Single container

```bash
cp deploy/.env.example deploy/.env    # then fill it in
docker compose -f deploy/docker-compose.yml up -d
```

Weights live on a named volume so a container replacement does not re-download
1.3 GB. Set `LABS_OFFLINE=true` once they are mirrored, so a missing file
fails loudly instead of quietly pulling from a third-party mirror mid-deploy.

### Screen-only container

For a free-tier-only deployment, or an edge fleet in front of a regional deep
fleet:

```bash
docker build --target screen -t labs-screen .
docker run -p 8000:8000 labs-screen
```

No weights to mirror, no volume, no `LABS_OFFLINE`. `/v1/health` reports
`model.installed: false` and `/v1/ready` returns
`{"ready": true, "tiers": ["screen"]}`. Deep modes return `501
deep_tier_unavailable` rather than crashing.

---

## AWS, autoscaling

```
                        ┌─────────────────────────────┐
   /v1/screen  ────────▶│ screen  Fargate ARM  1 vCPU │  scales on requests/target
                        │ ~350 MB image, no torch     │  2 → 40 tasks
                        └─────────────────────────────┘
                        ┌─────────────────────────────┐
   everything   ───────▶│ api     Fargate ARM  1 vCPU │  scales on requests/target
   else                 │ validates, stores, enqueues │  2 → 20 tasks
                        └──────┬───────────────┬──────┘
                               │               │
                          S3 (audio)      SQS (work)
                               │               │
                        ┌──────┴───────────────┴──────┐
                        │ worker  EC2 Graviton        │  scales on BACKLOG
                        │ 4 vCPU / 8 GiB per task     │  PER WORKER, 1 → 20
                        │ one analysis at a time      │
                        └──────────────┬──────────────┘
                                       │
                              MongoDB (state + reports)
```

Terraform: [`deploy/aws/main.tf`](../deploy/aws/main.tf).

> **Not validated.** Terraform was not available in the environment this was
> written in, so it has had no `terraform validate`, `fmt` or `plan` run
> against it. Review before applying.

```bash
cd deploy/aws
terraform init
terraform apply \
  -var vpc_id=vpc-... \
  -var 'private_subnet_ids=["subnet-a","subnet-b"]' \
  -var 'public_subnet_ids=["subnet-c","subnet-d"]' \
  -var mongo_uri_secret_arn=arn:aws:secretsmanager:... \
  -var certificate_arn=arn:aws:acm:...
```

Build and push both images:

```bash
docker build --target screen -t $ECR-screen:$TAG . && docker push $ECR-screen:$TAG
docker build --target worker -t $ECR-worker:$TAG . && docker push $ECR-worker:$TAG
```

The **api** service deliberately runs the *screen* image. It validates, stores
and enqueues, and runs Level 1 inline; it never loads the backbone. That is
what keeps a scale-out event a 350 MB pull.

### Four design decisions worth understanding

**Workers are EC2, not Fargate.** Fargate has no persistent local cache, so
every task start re-fetches 1.29 GB of weights. An EC2 host keeps them on EBS
across task replacements, so only a genuinely new instance pays. The API and
screen tiers *are* Fargate, because they are bursty, stateless and tiny —
which is the shape Fargate is priced for.

**Workers scale on backlog per worker, not queue depth.** A target on depth
alone cannot express "enough capacity": 10 messages is fine with 10 workers
and a crisis with one. The CloudWatch metric math divides
`(Visible + NotVisible)` by `RunningTaskCount`. Counting `NotVisible` is not
optional — once every worker is busy the visible count falls toward zero while
the backlog is at its worst, so an alarm on visible alone would **scale in
during a pile-up**.

**Scaling is asymmetric.** Out in steps of 2/5/10 after one minute; in by 1
after fifteen minutes of an empty queue. An idle worker costs ~$0.09/hr. A
worker killed mid-analysis costs the caller their result and makes SQS
redeliver the message after the visibility timeout. Those are not symmetric
mistakes, so the thresholds are not symmetric either.

**Health check paths differ per tier, deliberately.** Screen uses
`/v1/ready` — it is ready in under a second, so the strict probe is correct
and fast. API uses `/health`, because an API container in this topology never
loads the backbone and a strict readiness probe would hold it out of the
target group forever waiting for weights it will never have.

### IAM is split so neither side can do the other's job

- **api**: `sqs:SendMessage` but **not** `ReceiveMessage`. A container that
  could consume would steal work from the fleet and then run a 90 s analysis
  inside the process serving health checks.
- **worker**: `s3:GetObject` but **not** `PutObject`. It does not accept
  submissions.
- **screen**: no queue, no bucket, no database. This is the tier strangers
  reach, so it holds the least.

---

## Cost

All prices **us-east-1, on-demand, September 2026**, from AWS's own pages.
Verify before budgeting — they change, and your region differs.

| Resource | Price | Source |
|---|---|---|
| Fargate Linux/ARM | $0.03238 /vCPU-hr, $0.003560 /GB-hr | [Fargate pricing](https://aws.amazon.com/fargate/pricing/) |
| Fargate Linux/x86 | $0.04048 /vCPU-hr, $0.004445 /GB-hr | [Fargate pricing](https://aws.amazon.com/fargate/pricing/) |
| c7g.2xlarge (8 vCPU, 16 GiB) | $0.29 /hr | [EC2 on-demand](https://aws.amazon.com/ec2/pricing/on-demand/) |
| c7g.xlarge (4 vCPU, 8 GiB) | $0.145 /hr | [EC2 on-demand](https://aws.amazon.com/ec2/pricing/on-demand/) |
| c7i.2xlarge (8 vCPU, 16 GiB) | $0.357 /hr | [EC2 on-demand](https://aws.amazon.com/ec2/pricing/on-demand/) |
| SQS standard | $0.40 /M requests, first 1 M/month free | [SQS pricing](https://aws.amazon.com/sqs/pricing/) |
| ALB | ~$0.0225–0.0281 /hr + $0.008 /LCU-hr | [ELB pricing](https://aws.amazon.com/elasticloadbalancing/pricing/) |
| S3 Standard | $0.023 /GB-month, $0.005 /1k PUT | [S3 pricing](https://aws.amazon.com/s3/pricing/) |
| Fargate Spot | up to 70% off, ARM supported | [Fargate pricing](https://aws.amazon.com/fargate/pricing/) |

### Measured throughput

Measured on this repo's `audio/` folder (10 tracks, Apple M1, 4 threads,
uncontended). `base` is the pre-change pipeline at full density.

| Track | Level 1 | Level 2 | base | windows | escalated | coverage |
|---|---:|---:|---:|---:|:---:|---:|
| 1.mp3 | 3.25 | 28.75 | 83.8 | 16 | no | 0.992 |
| 2.mp3 | 1.59 | 41.93 | 77.4 | 16 | no | 0.987 |
| 3.mp3 | 1.27 | 28.32 | 73.2 | 16 | no | 0.995 |
| 4.mp3 | 1.16 | 28.52 | 61.0 | 16 | no | 0.991 |
| 5.mp3 | 1.07 | 24.51 | 50.6 | 13 | no | 0.898 |
| ai1.mp3 | 2.27 | 71.31 | 63.8 | 48 | **yes** | 0.982 |
| ai2.mp3 | 1.40 | 28.17 | 75.8 | 16 | no | 0.994 |
| ai3.mp3 | 1.15 | 70.27 | 84.1 | 48 | **yes** | 0.999 |
| ai4.mp3 | 1.09 | 65.03 | 70.5 | 47 | **yes** | 0.993 |
| ai5.mp3 | 1.12 | 24.88 | 62.5 | 14 | no | 0.980 |

```
Level 1   mean 1.54 s   median 1.21 s   max 3.25 s
Level 2   mean 41.2 s   median 28.6 s
base      mean 70.3 s   median 71.8 s
          mean 1.71x    median 2.51x     coverage 0.77 -> 0.981
```

**The distribution is bimodal, and the mean hides it.** The seven tracks whose
first pass was confident run at **2.0–2.7×**. The three that escalate are
**break-even to ~12% slower** than before (ai1: 71.3 s against 63.8 s) — they
pay a second Stage-2 call for a Stage-1 pass that was always going to run in
full. That is the deliberate trade: the cascade never buys speed by giving up
accuracy, so the tracks that cannot take decimation get no speedup.

> **Early exit fired 0/10 times on this sample, so none of the 1.71× comes
> from the tier ordering.** Every track escalated to Level 2: five are human,
> two were inconclusive at Level 1, and `ai1` — the only confidently-AI track —
> correctly failed the exit gate. So the measured speedup is entirely from the
> Level-2 work (spread + cascade), and on this sample Level 1 adds ~1.5 s of
> pure overhead.
>
> That is a property of the sample, not a bug: `ffprobe` shows four of the five
> `ai*.mp3` files are tagged `encoded_by="LAME in FL Studio"`, a DAW, so they
> are not confidently-AI material. The early-exit path is implemented and
> tested, but **its hit rate on real generator output is unmeasured**, and so
> is the throughput win that depends on it. Measure it against known Suno/Udio
> output before assuming it.

### Per-track marginal cost

8 vCPU Graviton, 4 vCPU per worker task, so **2 tasks per c7g.2xlarge**. Using
the measured mean of 41.2 s:

```
2 tasks × 3600/41.2  ≈  175 tracks/hr/instance
```

At $0.29/hr:

| | Per 1 000 tracks |
|---|---|
| Worker compute, measured mix | **$1.66** |
| Worker compute, all escalated (worst case, 70 s) | $2.82 |
| SQS (2 requests/track: send + delete) | $0.0008 |
| S3 PUT + 30 days of a 5 MB file | ~$0.12 |
| **Deep tier total** | **≈ $1.80 / 1 000 tracks** |

Level 1 on Fargate ARM at 1 vCPU / 2 GiB = $0.03950/hr, at 1.54 s/track and
one in flight per task ≈ 2 340 tracks/hr:

| | Per 1 000 tracks |
|---|---|
| **Level 1 total** | **≈ $0.017** |

**Level 1 is ~105× cheaper per track than Level 2.** That ratio is the entire
business case for the tier split, and it holds whether or not the early exit
ever fires — it is what makes the free tier affordable to give away.

### Monthly, three volumes

Fixed costs: ALB ~$20/mo, one warm worker $0.29 × 730 = $212/mo, two screen
tasks $57.7/mo, two api tasks $57.7/mo. MongoDB is excluded — it is usually
pre-existing, and Atlas M10 is roughly $60/mo if it is not.

| Monthly volume | Screen-only traffic | Deep traffic | Est. AWS total |
|---|---|---|---|
| 10 k screens, 1 k deep | $0.17 | $1.80 | **≈ $350/mo** (dominated by the warm floor + ALB) |
| 100 k screens, 20 k deep | $1.70 | $36 | **≈ $420/mo** |
| 1 M screens, 200 k deep | $17 | $360 | **≈ $1 100/mo** |

The striking thing is how flat the low end is: at small volume you are paying
almost entirely for **availability**, not for work. Two levers if that matters:

- **Drop the warm worker to 0.** Saves $212/mo, costs the first caller after
  an idle period several minutes of cold start (instance launch + image pull +
  ~12 s model load). Reasonable if deep analysis is a background batch job;
  not if a user is waiting.
- **Fargate Spot for the screen fleet.** Up to 70% off, ARM supported, and
  Level 1 is stateless and 1.5 s long — an interruption costs one retry.

Do **not** put workers on Spot without thought: an interruption mid-analysis
means SQS redelivers after the visibility timeout, so the caller waits up to
15 minutes extra. If you do, shorten `visibility_timeout`.

### Reserved capacity

The worker floor is the one predictable, always-on cost, which makes it the
right thing to commit. A [Compute Savings Plan](https://aws.amazon.com/savingsplans/compute-pricing/)
covers Fargate vCPU/GB **and** EC2, up to ~52% for 1–3 years. On the $270/mo
of floor compute in the table above that is ~$140/mo saved for a commitment
you were going to spend anyway.

---

## Concurrency and queueing: what a user actually experiences

This is the question the architecture exists to answer, so it is worth being
precise.

**Nobody waits for anybody else's job at the HTTP layer.** `POST /v1/analyses`
returns `202` with a job id in milliseconds. The caller polls. There is no
synchronous deep path to be blocked on.

**`POST /v1/screen` is synchronous and answers in ~1.5 s.** It is bounded by a
semaphore (`LABS_SCREEN_CONCURRENCY`, default = vCPU count capped at 8). Past
that it returns `429 screen_busy` rather than queueing. That is deliberate:
Level 1 is CPU-bound and FastAPI would run it on a 40-thread pool, where 40
concurrent screens on 4 cores do not run faster — they run at the same
aggregate throughput with 40× the latency, turning a 1.5 s promise into a
minute. The queue belongs in front of the work, where a caller can be told to
retry, not inside it where they can only wait.

**Within one worker, analyses are serial.** One at a time, and there is no
setting to change it. The pipeline is CPU-bound, so two concurrent runs on a
box each go at half speed: identical throughput, double the latency, double
the peak memory.

**Across workers, they are fully parallel.** Ten submissions with ten warm
workers is ten simultaneous analyses. The cost is cold start, which is why
`min_capacity` is 1 rather than 0.

So the honest answer to *"will users wait for the previous task?"*:

| | Waits? |
|---|---|
| Free screen, under the concurrency limit | No. ~1.5 s, synchronous. |
| Free screen, over the limit | No — refused with `429`, retry immediately. |
| Deep submission | No. `202` immediately. |
| Deep **result** | Only on queue depth. With warm capacity, ~30–90 s. With a backlog and cold scale-out, add ~2–5 min for an instance to launch and warm. |

### What the frontend should do

1. `POST /v1/screen` on upload. Show the verdict in ~1.5 s. **Free, no key
   needed.**
2. If `next_step == "escalate"`, offer the deep analysis. This is the natural
   paywall boundary and it is already in the response.
3. `POST /v1/analyses` with `mode=ai` or `mode=full` for the deep pass.
4. Poll `GET /v1/analyses/{id}` and render the **`progress` field as a
   timeline**, not a spinner. The pipeline emits per-stage progress
   (`screening` → `analysing` → `storing` → `complete`). A 60 s wait with
   visible progress reads as working; a spinner reads as broken.

### What the upload loop should do

Both tiers, since it is your own platform:

1. Level 1 inline on upload — 1.5 s is inside an upload handler's budget.
2. If Level 1 is decisive AI, flag and stop. No deep analysis, no cost.
3. Otherwise enqueue `mode=ai` with a key holding the `deep` scope.
4. Use the `Idempotency-Key` header so a retry after a client timeout does not
   re-analyse.

Content dedup is already in place: byte-identical audio in the same mode
returns the stored result without re-running. Marketplaces re-upload
constantly, so this is a real saving.

---

## Instance choice, and the Intel/Graviton trade-off

The Terraform defaults to **Graviton (c7g)** on price. There is a real
argument for Intel instead, and it is worth stating because it may be worth
more than everything else in this document.

| | Graviton (c7g/c8g) | Intel (c7i/c8i) |
|---|---|---|
| Price, 8 vCPU | $0.29/hr | $0.357/hr (+23%) |
| Fastest CPU precision | FP16 (armv8.2-a+fp16) | INT8 via AMX |
| ONNX Runtime INT8 | MMLA QGEMM, ~2.7–3.4× measured on BERT-class | AVX-512/VNNI via MLAS; **no AMX** without oneDNN/OpenVINO EP |
| OpenVINO INT8 | **partial** — int8 MatMul is routed to FullyConnected only, because `jit_gemm_i8` is *slower* than FP16 ACL MatMul | **~5.3× on BERT-class** (sentence-transformers benchmark, < 0.5% accuracy cost) |

So: **if the INT8 work below gets done, Intel is probably the better host**,
because the documented OpenVINO INT8 speedup (~5.3×) is larger than the
Graviton price advantage (~23%) by a wide margin. If it does not, Graviton
wins on price today.

This corrects an earlier recommendation of mine. I previously said static
INT8 on Graviton3+ was the path; the OpenVINO ARM work
([PR #27861](https://github.com/openvinotoolkit/openvino/pull/27861),
[PR #28870](https://github.com/openvinotoolkit/openvino/pull/28870))
shows INT8 MatMul is deliberately restricted on ARM because it is slower than
FP16 there. **Measure on your target instance before committing** — this is
the single highest-leverage unvalidated item in the whole plan.

### Thread settings

`LABS_TORCH_THREADS` and `OMP_NUM_THREADS` should be the **physical** core
count. On x86 that is half the vCPU count, because two hyperthreads sharing
one core's vector units contend rather than scale on a GEMM-bound workload.
Graviton has no SMT, so vCPU = physical core and the two are equal — one fewer
way to get it wrong, and a further small argument for it.

---

## Configuration reference

New in the tiered release. See [configuration.md](configuration.md) for the
full list.

### Tiers and gating

| Variable | Default | Notes |
|---|---|---|
| `LABS_SCREEN` | `true` | Level 1 on/off. |
| `LABS_GATE_DEEP` | `true` | Deep modes require the `deep` scope. **Breaking change** — see below. |
| `LABS_SCREEN_CONCURRENCY` | vCPUs, max 8 | In-flight screens before `429`. |
| `LABS_SCREEN_ONNX_THREADS` | `0` (ORT default) | Set when packing several workers per box. |
| `LABS_SCREEN_CNN_SEGMENTS` | `16` | Windows the CNN median-pools over. Was 40 upstream. |
| `LABS_SCREEN_EXIT_ON_AI` | `true` | Allow Level 1 to end a request. |
| `LABS_SCREEN_EXIT_CONFIDENCE` | `0.85` | Bar for that. |
| `LABS_SCREEN_AI_THRESHOLD` | `0.80` | Asymmetric on purpose. |
| `LABS_SCREEN_HUMAN_THRESHOLD` | `0.20` | |

### Level-2 performance

| Variable | Default | Notes |
|---|---|---|
| `LABS_SEGMENT_SPREAD` | `true` | Spread 48 windows across the whole track. Fixes coverage 0.77 → ~0.98. `false` restores upstream. |
| `LABS_CASCADE` | `true` | Two-pass Stage-1. |
| `LABS_CASCADE_STRIDE` | `3` | First-pass decimation. |
| `LABS_CASCADE_ESCALATE_BELOW` | `5.0` | `\|logit\|` under which full density is recomputed. |
| `LABS_DEEP_BANDING` | `true` | Report `inconclusive` near the boundary. |
| `LABS_INCONCLUSIVE_BELOW` | `2.0` | `\|logit\|` band. |
| `LABS_TORCH_THREADS` | `0` | **Set this.** Physical cores. |

### Distributed mode

| Variable | Notes |
|---|---|
| `LABS_SQS_QUEUE_URL` | Set with `LABS_MONGO_URI` to enable. |
| `LABS_SQS_VISIBILITY_S` | `900`. Must exceed the slowest analysis. |
| `LABS_AUDIO_BUCKET` | **Required** when distributed — the worker cannot read the API's temp dir. |

### Breaking change: `LABS_GATE_DEEP`

`mode=ai` and `mode=full` now require the `deep` scope. Keys carrying
`analyze` get `screen` but **not** `deep`, and must be granted it:

```bash
labs-keys create --name "upload loop" --scopes analyze,read,deep
```

`analyze` itself is unchanged, so the tools and `mode=audio` routes are
untouched. `LABS_GATE_DEEP=0` restores the previous behaviour
deployment-wide, which is the right setting for local development and for a
deployment already behind its own gateway.

---

## Operational runbook

### Alarms that matter

| Alarm | Means | Do |
|---|---|---|
| `queue-age > 600 s` | The unluckiest caller has waited 10 min. | Check `max_capacity` and whether workers are failing to start. This is the user-facing SLO, not depth. |
| `dlq > 0` | A file failed 3× and was parked. | Inspect it. It is a file the pipeline cannot handle; retrying will not help. |
| `backlog-per-worker > 2` | Normal — this *is* the scale-out trigger. | Nothing, unless it stays high, which means you are at `max_capacity`. |

### Verifying a deployment

```bash
curl -s $ALB/v1/health | jq '{screen, model, queue}'
curl -s $ALB/v1/ready
curl -s -F file=@track.mp3 $ALB/v1/screen | jq '{verdict, confidence, next_step, elapsed_seconds}'
```

`screen.ready: true` with `model.installed: false` is a healthy screen-only
container, not a degraded one.

### Cold-start budget

| | Time |
|---|---|
| Screen task, warm image | ~5 s |
| Screen task, cold pull (350 MB) | ~15 s |
| Worker, warm instance + cached weights | ~25 s |
| Worker, new instance (launch + 2.5 GB pull + 1.3 GB weights) | **~3–5 min** |

The last row is why `min_capacity` is 1. It is also why the worker ASG has
`default_instance_warmup = 300`: judging an instance before that would kill
it while it was still loading.
