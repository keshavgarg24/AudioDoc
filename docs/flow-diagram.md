# Flow diagrams

Every tool, every step, every decision. Nine diagrams, from the AWS topology
down to the arithmetic that turns two model outputs into a published verdict.

`flow.md` tells the same story in prose with ASCII art and is the better read
for understanding *why*. This file is the reference for *what happens where*,
and every box names the module or AWS resource that does the work.

1. [System topology — every AWS service](#1-system-topology)
2. [Request lifecycle — the four modes](#2-request-lifecycle)
3. [Level 1 internals](#3-level-1-internals)
4. [Level 1 decision logic](#4-level-1-decision-logic)
5. [The exit gate](#5-the-exit-gate)
6. [Level 2 internals](#6-level-2-internals)
7. [Level 2 decision logic](#7-level-2-decision-logic)
8. [The assessment envelope](#8-the-assessment-envelope)
9. [Async job lifecycle and failure paths](#9-async-job-lifecycle)

---

## 1. System topology

Every AWS service in the stack, what it holds, and which component touches it.

```mermaid
flowchart TB
    subgraph client["Caller"]
        C["Browser / CLI / SDK<br/><i>multipart POST + X-API-Key</i>"]
    end

    subgraph edge["Edge — TLS terminates here"]
        CF["<b>CloudFront</b><br/>*.cloudfront.net cert, AWS-managed<br/>CachingDisabled · AllViewerExceptHost<br/><i>free tier: 1 TB/mo</i>"]
    end

    subgraph vpc["VPC — 2 AZs, ap-south-1"]
        subgraph pub["Public subnets"]
            ALB["<b>Application Load Balancer</b><br/>HTTP:80 · /v1/screen split at priority 10"]
            NAT["<b>NAT Gateway</b><br/>egress 35.154.189.62<br/><i>allowlisted in Atlas</i>"]
        end

        subgraph priv["Private subnets"]
            SCR["<b>ECS Fargate — screen</b><br/>ARM64 · 2 tasks · ~350 MB image<br/>Level 1 only, torch-free"]
            API["<b>ECS Fargate — api</b><br/>ARM64 · 2 tasks<br/>accepts uploads, serves results"]
            WRK["<b>ECS EC2 — worker</b><br/>m7i-flex.large x86_64<br/>2048 CPU / 6144 MiB<br/>fix-perms init + worker"]
            ASG["<b>Auto Scaling Group</b><br/>min 1 · max 4 · warm pool 1<br/>scales on SQS backlog"]
        end
    end

    subgraph data["Managed state"]
        S3A["<b>S3</b> audio bucket<br/>uploads, keyed by content hash"]
        S3W["<b>S3</b> weights mirror<br/>Stage-1 1.29 GB · Stage-2 47 MB<br/>SHA-256 pinned"]
        S3T["<b>S3</b> tfstate<br/>versioned + encrypted"]
        DDB["<b>DynamoDB</b><br/>terraform lock table"]
        SQS["<b>SQS</b> analyses<br/>+ DLQ after 3 attempts"]
        SM["<b>Secrets Manager</b><br/>mongo-uri<br/><i>never enters tf state</i>"]
        ECR["<b>ECR</b><br/>labs-test-screen:arm64<br/>labs-test-worker:amd64"]
        CW["<b>CloudWatch Logs</b><br/>one group, 4 stream prefixes"]
    end

    ATLAS[("<b>MongoDB Atlas</b><br/>API keys (SHA-256)<br/>jobs · results · appeals")]

    C -->|HTTPS| CF
    CF -->|HTTP, X-Origin-Verify| ALB
    ALB -->|"/v1/screen"| SCR
    ALB -->|everything else| API

    API -->|"PutObject"| S3A
    API -->|"SendMessage"| SQS
    SQS -->|"ReceiveMessage"| WRK
    WRK -->|"GetObject audio"| S3A
    WRK -->|"sync weights once"| S3W
    ASG -.->|"places tasks"| WRK
    SQS -.->|"backlog metric"| ASG

    SCR --> NAT
    API --> NAT
    WRK --> NAT
    NAT --> ATLAS

    SM -.->|"injected by ECS agent"| SCR
    SM -.->|"injected by ECS agent"| API
    SM -.->|"injected by ECS agent"| WRK
    ECR -.->|"image pull"| SCR
    ECR -.->|"image pull"| API
    ECR -.->|"image pull"| WRK
    SCR --> CW
    API --> CW
    WRK --> CW
    S3T -.- DDB

    classDef aws fill:#fff4e6,stroke:#d18c30,color:#14161a
    classDef ecs fill:#eaf1fb,stroke:#4a7ec1,color:#14161a
    classDef ext fill:#eef7f0,stroke:#3f8a5f,color:#14161a
    class CF,ALB,NAT,S3A,S3W,S3T,DDB,SQS,SM,ECR,CW,ASG aws
    class SCR,API,WRK ecs
    class ATLAS,C ext
```

**Which resources exist and how many run at once**

| Resource | Steady state | Why |
|---|---|---|
| Fargate `screen` | 2 tasks | One per AZ. Ungated route, so it absorbs anonymous bursts. |
| Fargate `api` | 2 tasks | One per AZ for the ALB to have two healthy targets. |
| EC2 worker | 1 instance, 1 task | One analysis per container — the pipeline is CPU-bound, so two concurrent runs each go at half speed. |
| Warm pool | 1 stopped instance | Billed for its EBS volume only; resumes in ~30 s instead of the 3–5 min a cold launch takes. |
| NAT Gateway | 1 | Single egress IP, which is what Atlas allowlists. |
| CloudFront | 1 distribution | TLS. |

---

## 2. Request lifecycle

Four modes. The difference between them is which tiers run and whether the
short-circuit is allowed.

```mermaid
flowchart TB
    START(["POST with X-API-Key"]) --> AUTH{"key valid?<br/><i>core/security.py</i><br/>SHA-256 lookup in Atlas"}
    AUTH -->|no| E401["401 · invalid_api_key"]
    AUTH -->|yes| SCOPE{"scope covers<br/>the mode?"}
    SCOPE -->|no| E403["403 · insufficient_scope"]
    SCOPE -->|yes| RL{"under the<br/>rate limit?"}
    RL -->|no| E429["429 · retry_after"]
    RL -->|yes| MODE{"mode"}

    MODE -->|screen| M1["Level 1 only<br/><b>free · sync · ~1.5 s</b>"]
    MODE -->|ai| M2["Level 1, then Level 2<br/><b>gated · async</b>"]
    MODE -->|audio| M3["DSP + musicology only<br/><b>no detection at all</b>"]
    MODE -->|full| M4["Level 1 + Level 2 + everything<br/><b>never short-circuits</b>"]

    M1 --> L1A["screen/pipeline.run()"]
    M2 --> L1B["screen/pipeline.run()"]
    M4 --> L1C["screen/pipeline.run()"]

    L1A --> R1(["return · assessment + level_1 detail"])

    L1B --> DEC{"next_step"}
    DEC -->|"return<br/>decisive AI, survived<br/>the exit gate"| EXIT["early_exit<br/><b>backbone never loaded</b><br/>saves 60-90 s of CPU"]
    DEC -->|escalate| L2A["ml/detector.predict()"]
    EXIT --> R2(["return"])
    L2A --> R3(["return · both levels + agreement"])

    L1C --> L2B["ml/detector.predict()<br/><i>runs regardless</i>"]
    M3 --> L2C["analysis passes only"]
    L2B --> R4(["return · full report"])
    L2C --> R5(["return"])

    classDef err fill:#fbeeed,stroke:#b4453a,color:#14161a
    classDef win fill:#eef7f0,stroke:#3f8a5f,color:#14161a
    class E401,E403,E429 err
    class EXIT win
```

> `full` never short-circuits on purpose. A full report is bought for its
> evidence — the per-window timeline, the embeddings, the musicological pass —
> and omitting the deep layers because a cheap model was confident would
> deliver a thinner document than the one that was paid for.

---

## 3. Level 1 internals

One decode, shared by every stage below it. Every stage is
exception-isolated: a screen that throws degrades to "that screen is
unavailable" and never to a failed request, because Level 1 is the ungated tier
and therefore the one most exposed to whatever a stranger uploads.

```mermaid
flowchart TB
    IN(["file path"]) --> DEC["<b>decode.load()</b><br/>16 kHz mono, one pass<br/><i>librosa</i>"]
    DEC -->|fails| UNAVAIL["verdict=unavailable<br/>next_step=escalate<br/><i>the deep tier decodes<br/>differently and may succeed</i>"]
    DEC --> PROBE["<b>decode.probe()</b><br/>container metadata<br/><i>ffprobe</i>"]

    DEC --> M1["<b>models.fakeprint()</b><br/>averaged linear spectrum<br/>→ peak locations<br/><i>ONNX · spectral comb</i>"]
    DEC --> M2["<b>models.cepstrum()</b><br/>CQT → DCT texture<br/>median-pooled over 16 windows<br/><i>ONNX · learned CNN</i>"]

    M1 --> ENS["<b>ensemble.combine()</b>"]
    M2 --> ENS

    PROBE --> SC["<b>screens.container()</b><br/>encoder tags, bitrate<br/><i>weak signal</i>"]
    IN --> C2PA["<b>screens.c2pa()</b><br/>signed manifest<br/><i>DECISIVE if present</i>"]
    IN --> BW["<b>decode.load_wideband()</b><br/>→ <b>screens.bandwidth()</b><br/>spectral ceiling, signal chain"]

    ENS --> POL["<b>policy.decide()</b>"]
    SC --> POL
    C2PA --> POL
    BW --> POL

    POL --> GATE{"next_step<br/>== return?"}
    GATE -->|no| OUT
    GATE -->|yes| ROB["<b>robustness.check()</b><br/>3 benign perturbations<br/>re-score, measure drift"]
    ROB --> REBAND["<b>policy.reband()</b><br/><i>re-ask the verdict after<br/>the confidence penalty</i>"]
    REBAND --> OUT["<b>assessment.build()</b><br/>score · label · band · confidence"]
    OUT --> RES(["ScreenResult"])

    classDef model fill:#eaf1fb,stroke:#4a7ec1,color:#14161a
    classDef dec fill:#fdf6e3,stroke:#b08324,color:#14161a
    class M1,M2,ENS model
    class POL,GATE,REBAND dec
```

### Why fusion is not `max()`

```
    final = max(p_fakeprint, p_cepstrum)
```

is an OR gate. If **either** model false-positives, the ensemble
false-positives. At 2% FPR each with partly independent errors the union
approaches ~4%: the false-accusation rate has doubled while the change felt
like an improvement.

| Pattern | Reading |
|---|---|
| both high | Two representations, two methods, one conclusion. Confident. |
| both low | The same, in the other direction. Confident. |
| cepstrum only | Texture present, comb absent. Consistent with **processed** generator output — a pitch shift or resample smears the comb while the texture survives on a log axis. |
| fakeprint only | Comb present, texture absent. Bitcrushing and sample-rate reduction manufacture a comb by the same mechanism as vocoder upsampling, so this also fits a heavily processed **human** track. |

---

## 4. Level 1 decision logic

```mermaid
flowchart TB
    IN(["ensemble result"]) --> C2{"C2PA manifest<br/>names a generator?"}
    C2 -->|yes| CDONE["<b>ai-generated</b> · confidence 0.99<br/>next_step = return<br/>decided_by = c2pa<br/><i>a signed declaration outranks<br/>every inference below it</i>"]
    C2 -->|no| AVAIL{"any detector<br/>produced a score?"}
    AVAIL -->|no| UN["<b>unavailable</b><br/>next_step = escalate"]

    AVAIL -->|yes| FUSE["score = 0.55·fakeprint + 0.45·cepstrum<br/>then a <b>continuous</b> agreement bonus"]
    FUSE --> CONF["confidence = |score − 0.5| × 2 × multiplier<br/><i>multiplier ramps 0.55 → 1.15<br/>with how deep the agreement runs</i>"]

    CONF --> G1{"confidence <br/>&lt; 0.45?"}
    G1 -->|yes| INC1["<b>inconclusive</b><br/>review_recommended"]
    G1 -->|no| G2{"score ≥ 0.80?"}
    G2 -->|yes| AI["<b>ai-generated</b>"]
    G2 -->|no| G3{"score ≤ 0.20?"}
    G3 -->|yes| HU["<b>human-made</b>"]
    G3 -->|no| INC2["<b>inconclusive</b><br/><i>0.5 is not 'slightly AI'</i>"]

    AI --> EX{"exit_on_ai<br/>AND no vetoes<br/>AND confidence ≥ 0.85?"}
    EX -->|yes| RET["next_step = <b>return</b><br/>→ exit gate"]
    EX -->|no| ESC["next_step = escalate"]
    HU --> ESCH["next_step = <b>escalate</b><br/><i>always</i>"]
    INC1 --> ESC
    INC2 --> ESC

    classDef ai fill:#fbeeed,stroke:#b4453a,color:#14161a
    classDef hu fill:#eef7f0,stroke:#3f8a5f,color:#14161a
    classDef un fill:#fdf6e3,stroke:#b08324,color:#14161a
    class AI,CDONE ai
    class HU hu
    class INC1,INC2,UN un
```

**The asymmetry is the whole design.** On a marketplace, wrongly flagging a
human producer costs a customer and a public complaint; missing one AI track
costs very little. So:

- the AI bar sits at **0.80** and the human bar at **0.20**, and the gap is
  reported as `inconclusive` rather than rounded to whichever side is nearer;
- a confident **AI** may end the request;
- a confident **human** *never* does. Both Level-1 models know only the
  generators they were trained on, so their silence is not evidence. Level 1
  alone never publishes an exoneration.

---

## 5. The exit gate

Runs **only** for tracks the policy already wants to return on — a small
fraction of traffic, and exactly the fraction that was about to skip the
expensive tier anyway.

```mermaid
flowchart TB
    IN(["decision.next_step == return"]) --> P["Re-score 3 benign perturbations<br/>in the numpy domain<br/><i>no ffmpeg, no subprocess —<br/>this is an ungated public endpoint</i>"]
    P --> M["stability = 1 − spread<br/>flipped = how many crossed the bar"]
    M --> Q{"verdict holds?"}
    Q -->|yes| KEEP["keep next_step = return<br/>reason: 'stable under<br/>benign perturbation'"]
    Q -->|no| PEN["next_step → <b>escalate</b><br/>veto recorded<br/>confidence ×= max(stability, 0.15)"]
    PEN --> RB["<b>policy.reband()</b>"]
    RB --> RQ{"confidence still<br/>≥ 0.45?"}
    RQ -->|yes| HOLD["verdict stands"]
    RQ -->|no| DEMOTE["verdict → <b>inconclusive</b><br/>score preserved in <b>probability</b>"]

    classDef fix fill:#eef7f0,stroke:#3f8a5f,color:#14161a
    class RB,DEMOTE fix
```

> **The bug this closes.** The penalty used to be applied *after* banding, and
> the verdict was left standing on a confidence that no longer cleared the bar
> which produced it. Measured on `ai1.mp3`: `ai-generated` published at
> confidence **0.231** against a `min_confidence` of **0.45**. The number and
> the verdict disagreed, and the number was the honest one.

---

## 6. Level 2 internals

```mermaid
flowchart TB
    IN(["file path"]) --> V["<b>audio.validate()</b><br/>size, duration, format"]
    V --> SEG["<b>audio.load_segments()</b><br/>beat tracking → grid<br/>48 × 10 s beat-aligned windows<br/>padded to max_segments"]

    SEG --> C1["<b>stage1()</b> first pass<br/>stride 3 → 16 windows<br/>MERT-v1-95M backbone"]
    C1 --> EMB["embeddings cached per index<br/><i>this is what makes the<br/>cascade free</i>"]
    EMB --> CL1["<b>classify_subset()</b><br/>Stage-2 transformer<br/>masks every padded row"]
    CL1 --> CASC{"|logit| ≥ 5.0?"}

    CASC -->|yes| STOP["<b>stop</b> · 16 of 48 windows<br/>measured immune to segment count:<br/>1.mp3 moves 0.004 across strides 1-4"]
    CASC -->|no| C2["<b>escalate</b> · embed the other 32<br/><i>first pass reused, not recomputed —<br/>costs the same as one full pass</i>"]
    C2 --> CL2["re-classify at full density"]

    STOP --> SIG
    CL2 --> SIG["<b>scaled_sigmoid()</b><br/>clamp(σ(logit), 0.011, 0.989)"]
    SIG --> BAND["<b>_band()</b>"]
    BAND --> ASM["<b>assessment.build()</b><br/>decisive bounds derived from<br/>inconclusive_below via the same sigmoid"]
    ASM --> REP["<b>build_report()</b><br/>timeline · structure · reliability<br/>findings · musical · production"]
    REP --> OUT(["report"])

    classDef model fill:#eaf1fb,stroke:#4a7ec1,color:#14161a
    classDef dec fill:#fdf6e3,stroke:#b08324,color:#14161a
    class C1,CL1,CL2,SIG model
    class CASC,BAND dec
```

### Why a cascade and not just fewer segments

Measured on the real checkpoints over 10 tracks at strides 1–4, sensitivity to
segment count is purely a function of confidence:

| Confidence | Behaviour |
|---|---|
| `\|logit\| ≥ 5` | Immune. `1.mp3` moves 0.004 between stride 1 and stride 4. |
| `\|logit\| < 2` | Unstable. `ai1` **flips** Fake→Real at stride 2; `ai4` collapses +4.574 → +0.937. |

Blanket decimation is therefore not a free speedup — it is an accuracy trade
that happens to be invisible on the easy majority. The cascade buys the same
time from only the tracks that provably do not need it.

---

## 7. Level 2 decision logic

```mermaid
flowchart TB
    IN(["raw_logit, unbounded<br/>saturates near ±7.21"]) --> EN{"LABS_DEEP_BANDING<br/>enabled?"}
    EN -->|no| SIGN["verdict = sign(logit)<br/><i>decisive always</i>"]
    EN -->|yes| B{"abs(logit) &lt; 2.0?"}
    B -->|yes| INC["<b>inconclusive</b> · decisive = false<br/><br/>'tracks in this band move to the<br/>other side of zero on a different<br/>but equally valid choice of<br/>analysis windows'"]
    B -->|no| DEC{"logit &gt; 0?"}
    DEC -->|yes| AI["<b>ai-generated</b> · decisive = true"]
    DEC -->|no| HU["<b>human-made</b> · decisive = true"]

    INC --> PRED["<b>prediction</b> still reports the sign<br/><b>assessment.label</b> still commits<br/><i>the uncertainty is added, not<br/>substituted for the answer</i>"]

    classDef ai fill:#fbeeed,stroke:#b4453a,color:#14161a
    classDef hu fill:#eef7f0,stroke:#3f8a5f,color:#14161a
    classDef un fill:#fdf6e3,stroke:#b08324,color:#14161a
    class AI ai
    class HU hu
    class INC un
```

`ai1.mp3` is the case that forces the band. It sits at `|logit| 0.4` — a coin
flip — and lands on opposite sides of zero depending on which windows Stage-1
was given: **+0.393** on upstream's first-48 plan, **−0.421** once the windows
are spread across the whole track. Both readings are honest and neither is a
verdict, but publishing the sign turned that into "Fake" one day and "Real"
the next.

---

## 8. The assessment envelope

One definition of score, label, band and confidence, built the same way by both
tiers. `assessment.py` owns all of it.

```mermaid
flowchart TB
    S(["score ∈ [0,1]<br/>P(AI-generated)"]) --> L["<b>label</b><br/>score ≥ 0.5 → ai-generated<br/>score &lt; 0.5 → human-made<br/><i>always populated, never abstains</i>"]
    S --> CF["<b>confidence</b><br/>|score − 0.5| × 2 × multiplier<br/><i>0.0 means 'on the boundary',<br/>not 'half sure'</i>"]
    S --> BD{"|score − 0.5|<br/>&lt; 0.15?"}
    BD -->|yes| U["band = <b>uncertain</b>"]
    BD -->|no| D{"past the tier's<br/>decisive threshold?"}
    D -->|"yes, high"| SA["band = <b>strong-ai</b>"]
    D -->|"yes, low"| SH["band = <b>strong-human</b>"]
    D -->|no| LK{"score ≥ 0.5?"}
    LK -->|yes| LA["band = <b>likely-ai</b>"]
    LK -->|no| LH["band = <b>likely-human</b>"]

    V(["verdict<br/><i>from the tier's own policy</i>"]) --> OUT
    L --> OUT["<b>assessment</b> block<br/>score · label · verdict · band<br/>confidence · margin · threshold<br/>decided_by"]
    CF --> OUT
    U --> OUT
    SA --> OUT
    SH --> OUT
    LA --> OUT
    LH --> OUT
```

**`label` and `verdict` answer different questions, and the service owes both.**

| Field | Question | Abstains? |
|---|---|---|
| `label` | "If you had to pick a side, which?" | Never |
| `verdict` | "Is this worth acting on?" | Yes — `inconclusive` |
| `band` | "How strong, regardless?" | No — always one of five |

Rounding a coin flip to the nearer side and publishing it as a finding is how a
detector acquires a false-accusation rate it cannot see. But refusing to say
anything is useless to a caller running a bulk triage queue who has their own
review step. So both are reported, computed from the same score, and a caller
picks by risk appetite instead of by whatever the service decided for them.

**Decisive thresholds per tier** — passed in, never hardcoded, so `band` and
`verdict` cannot disagree about the same track:

| Tier | AI decisive at | Human decisive at | Source |
|---|---|---|---|
| Level 1 | `score ≥ 0.80` | `score ≤ 0.20` | `ai_threshold` / `human_threshold` |
| Level 2 | `score ≥ 0.8808` | `score ≤ 0.1192` | `σ(±inconclusive_below)` |

---

## 9. Async job lifecycle

```mermaid
sequenceDiagram
    autonumber
    participant C as Caller
    participant CF as CloudFront
    participant A as api (Fargate)
    participant S3 as S3 audio
    participant Q as SQS
    participant W as worker (EC2)
    participant M as Atlas

    C->>CF: POST /v1/analyses (multipart)
    CF->>A: forward
    A->>A: validate · hash content
    A->>M: dedup lookup by hash
    alt already analysed
        M-->>A: existing job
        A-->>C: 200 {id, status: succeeded}
    else new
        A->>S3: PutObject
        A->>M: insert job {status: queued}
        A->>Q: SendMessage {job_id}
        A-->>C: 202 {id, status: queued}
    end

    Note over W: poll loop, one analysis at a time
    W->>Q: ReceiveMessage
    W->>M: status → running
    W->>S3: GetObject audio
    W->>W: tiers.analyse() — L1 then L2
    alt success
        W->>M: status → succeeded + report
        W->>Q: DeleteMessage
    else failure
        W->>M: status → failed + reason
        Note over Q: visibility timeout expires,<br/>redelivered - 3 strikes sends it to the DLQ
    end

    loop until terminal
        C->>CF: GET /v1/analyses/{id}
        CF->>A: forward
        A->>M: read job
        A-->>C: {status, result?}
    end
```

### Failure paths

| Failure | Where it surfaces | Behaviour |
|---|---|---|
| Decode fails at Level 1 | `pipeline.run` | `verdict=unavailable`, `next_step=escalate`. Never raises — the deep tier decodes differently and may succeed. |
| One Level-1 screen throws | `timed()` wrapper | Recorded in `errors{}`, other screens continue. |
| Worker cannot write `/models` | container start | `fix-perms` init container chowns the host path first; `dependsOn: SUCCESS` means the worker cannot start before it exits. |
| Checkpoint digest mismatch | `ml/checkpoints` | Refuses to load. `LABS_OFFLINE=true` blocks the HuggingFace fallback. |
| Job fails 3× | SQS redrive | Lands in the DLQ. Anything there failed *deterministically*, not transiently. |
| Atlas read lag | status polling | A job can report `queued` immediately after `succeeded` — Atlas serves reads from a secondary. Clients must confirm a terminal status twice. |
| Instance scale-in mid-analysis | ASG lifecycle hook | `ECS_CONTAINER_STOP_TIMEOUT=3m` + draining: the task finishes rather than being killed. |

---

## Where the time goes

| Step | Cost | Notes |
|---|---|---|
| Decode (16 kHz mono) | ~0.3 s | Shared by every Level-1 stage. |
| fakeprint | ~0.2 s | 1.2 MB ONNX. |
| cepstrum | ~0.6 s | CQT dominates. |
| Level 1 total | **~1.5 s** | Synchronous. |
| Exit gate | ~1.5 s | Only on early-exit candidates. |
| Beat tracking + segmentation | ~5 s | |
| MERT backbone, 16 windows | ~25 s | Cascade first pass. |
| MERT backbone, 48 windows | ~75 s | Only when the first pass is borderline. |
| Level 2 total | **50–115 s** | CPU. |

The cascade is the whole economic argument: it removes ~60 s of backbone CPU
from every track the first pass already settled, and Level 1's early exit
removes the backbone entirely from the tracks that need it least.
