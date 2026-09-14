# End-to-end flow

One audio file, from the browser to a verdict, at three zoom levels: what the
user sees, what AWS does, and what the code does line by line.

- [1. The whole thing, at a glance](#1-the-whole-thing-at-a-glance)
- [2. AWS: where the bytes physically go](#2-aws-where-the-bytes-physically-go)
- [3. Level 1, function by function](#3-level-1-function-by-function)
- [4. The decision: return or escalate](#4-the-decision-return-or-escalate)
- [5. Level 2, function by function](#5-level-2-function-by-function)
- [6. The appeal loop](#6-the-appeal-loop)
- [7. Where the time actually goes](#7-where-the-time-actually-goes)
- [8. What happens when things go wrong](#8-what-happens-when-things-go-wrong)

---

## 1. The whole thing, at a glance

```
                              ┌──────────┐
                              │  BROWSER │  user drops track.mp3
                              └────┬─────┘
                                   │  multipart POST
                                   ▼
╔══════════════════════════════════════════════════════════════════════════╗
║  STEP 1   POST /v1/screen          FREE · SYNCHRONOUS · ~1.5 s           ║
║           scope: screen (anonymous holds it)                             ║
╚══════════════════════════════════════════════════════════════════════════╝
                                   │
                    ┌──────────────┴──────────────┐
                    ▼                             ▼
          ┌───────────────────┐         ┌───────────────────┐
          │  next_step        │         │  next_step        │
          │  = "return"       │         │  = "escalate"     │
          │                   │         │                   │
          │  decisive AI,     │         │  human-made, or   │
          │  survived the     │         │  inconclusive, or │
          │  exit gate        │         │  gate said no     │
          └─────────┬─────────┘         └─────────┬─────────┘
                    │                             │
        ┌───────────┴──────────┐                  │
        │                      │                  │
        ▼                      ▼                  ▼
   DONE. Verdict          user disagrees   ╔═══════════════════════════════╗
   returned. Backbone     ("appeal")       ║ STEP 2  POST /v1/analyses     ║
   never ran.                  │           ║   mode=ai | full              ║
   Retained 1 h for            │           ║   GATED · ASYNC · 25-70 s     ║
   appeal.                     │           ║   scope: deep                 ║
        │                      │           ╚═══════════════╤═══════════════╝
        │                      ▼                           │
        │        ╔═══════════════════════════╗             │
        │        ║ POST /v1/escalations      ║             │
        │        ║   screen_id → mode=full   ║─────────────┤
        │        ║   scope: deep             ║             │
        │        ╚═══════════════════════════╝             │
        │                      │                           ▼
        │                      │                  ┌─────────────────┐
        │                      │                  │  202 + job id   │
        │                      │                  │  poll or webhook│
        │                      │                  └────────┬────────┘
        │                      │                           ▼
        │                      │                  ┌─────────────────┐
        │                      └─────────────────▶│  LEVEL 2 runs   │
        │                          outcome        │  verdict + full │
        │                          recorded:      │  report         │
        │                          upheld /       └─────────────────┘
        │                          overturned
        ▼
   ┌─────────────────────────────────────────────────────────┐
   │ feedback collection: the only labelled data this system │
   │ ever produces about its own mistakes                    │
   └─────────────────────────────────────────────────────────┘
```

**The one rule that shapes everything:** only a *decisive AI* verdict may end
a request at Level 1. A confident `human-made` always escalates, because both
Level-1 models recognise only the generators they were trained on — their
silence is not evidence, so Level 1 never publishes an exoneration alone.

---

## 2. AWS: where the bytes physically go

```
   ┌────────┐
   │ client │
   └───┬────┘
       │ HTTPS
       ▼
   ┌───────────────────────────────────────────────────────────┐
   │  ALB   (public subnets, TLS terminated, idle_timeout 120) │
   │                                                           │
   │   listener rule priority 10:  path == /v1/screen  ────┐   │
   │   default action:             everything else  ───┐   │   │
   └───────────────────────────────────────────────────┼───┼───┘
                                                       │   │
        ┌──────────────────────────────────────────────┘   │
        │                                                  │
        ▼  target group "api"                              ▼  target group "screen"
        │  health: /health                                 │  health: /v1/ready
        │  (never loads the backbone, so a strict          │  (ready in <1 s, so the
        │   readiness probe would hold it out forever)     │   strict probe is correct)
        │                                                  │
   ┌────┴──────────────────────┐                  ┌────────┴────────────────┐
   │ ECS SERVICE  api          │                  │ ECS SERVICE  screen     │
   │ Fargate ARM 1 vCPU/2 GiB  │                  │ Fargate ARM 1 vCPU/2GiB │
   │ image: labs-screen        │                  │ image: labs-screen      │
   │ 2 → 20 tasks              │                  │ 2 → 40 tasks            │
   │ scales: req/target = 300  │                  │ scales: req/target = 24 │
   │ IAM: sqs:SendMessage      │                  │ IAM: nothing.           │
   │      s3:PutObject         │                  │      no queue, no       │
   │      NO ReceiveMessage    │                  │      bucket, no db      │
   └────┬──────────────┬───────┘                  └─────────────────────────┘
        │              │                             answers inline, ~1.5 s
        │ 1. PUT       │ 2. insert           the tier strangers reach holds the least
        ▼              ▼
   ┌─────────┐   ┌──────────────┐
   │   S3    │   │   MongoDB    │
   │ audio   │   │  jobs (state)│
   │ 30d TTL │   │  screens 1h  │
   └────┬────┘   │  feedback    │
        │        │  analyses    │
        │        └──────▲───────┘
        │               │
        │  3. send      │
        ▼               │
   ┌──────────────┐     │
   │     SQS      │     │
   │ visibility   │     │
   │   900 s      │     │
   │ redrive → DLQ│     │
   │   after 3    │     │
   └──────┬───────┘     │
          │ long poll 20 s
          ▼             │
   ┌─────────────────────────────────────────────────┐
   │ ECS SERVICE  worker      1 → 20 tasks           │
   │ ─────────────────────────────────────────────── │
   │ base: EC2 Graviton (c7g.2xlarge, 2 tasks each)  │
   │       weights cached on the host EBS volume     │
   │ burst: FARGATE, weight 3:1, no instance launch  │
   │                                                 │
   │ scales on BACKLOG PER WORKER > 0                │
   │   (Visible + NotVisible) / RunningTaskCount     │
   │                                                 │
   │ WARM POOL: 2 stopped-but-initialised instances  │
   │   resume ~30 s   vs   3-5 min cold              │
   │                                                 │
   │ IAM: sqs:Receive/Delete, s3:GetObject           │
   │      NO SendMessage, NO PutObject               │
   └─────────────────────────────────────────────────┘
```

### Ordering that is load-bearing

In `_dispatch_remote` ([`api/v1/analyses.py`](../src/labs/api/v1/analyses.py)) the
three writes happen in exactly this order, and each one matters:

1. **S3 first.** The worker is a different container and cannot read this
   one's temp directory. A message published before the upload lands is a job
   that fails on fetch.
2. **MongoDB second.** Published first, a fast worker can finish and update a
   job document that does not exist yet — the upsert recreates it without an
   owner and the submitter is locked out of their own result.
3. **SQS last.** Once the message exists, the work is real.

---

## 3. Level 1, function by function

`POST /v1/screen` → [`api/v1/screen.py`](../src/labs/api/v1/screen.py)

```
create_screen(file, reference)
│
├─ settings.screen.enabled?                      no → 503 screen_disabled
├─ suffix in allowed_suffixes?                   no → 415
│
├─ _SLOTS.acquire(blocking=False)                ── ADMISSION CONTROL
│     BoundedSemaphore(min(cpu_count, 8))
│     full → 429 screen_busy  (load shedding, NOT a rate limit)
│
│     Why: FastAPI runs a sync handler on anyio's 40-thread pool. Level 1 is
│     CPU-bound, so 40 concurrent screens on 4 cores do not run faster - they
│     run at the same throughput with 40x the latency. The queue belongs in
│     FRONT of the work, where a caller can be told to retry.
│
├─ stage_upload()  → /tmp/labs-<request_id>.mp3  ── magic-byte + size check
│
└─ tiers.analyse(path, mode="screen")            → labs/tiers.py
   │
   └─ screen.pipeline.run(path, cfg)             → labs/screen/pipeline.py
      │
      │  every stage below is wrapped in timed(), which records its cost and
      │  ISOLATES its failure. Level 1 is the ungated tier, so a screen that
      │  throws must degrade to "that screen is unavailable", never to a 500.
      │
      ├─ [1] decode.load()                              ~0.3-2.5 s
      │      librosa.load(sr=16000, mono, duration=300)
      │      ONE decode. Both models train at 16 kHz, so one read feeds both.
      │      (The reference implementation decoded 3x — per model, and again
      │       for bandwidth. That is why it took >20 s.)
      │
      ├─ [2] decode.probe()                             ~0.05-0.2 s
      │      ffprobe → codec, sample rate, bitrate, encoder string, tags
      │
      ├─ [3] models.score_both(y, cfg)                  ~0.3-0.8 s
      │      │
      │      ├─ score_fakeprint(y)
      │      │    features.mean_spectrum_db()
      │      │      librosa.stft(n_fft=8192, hop=4096, hann, reflect)
      │      │      → 10·log10(|S|²) → mean over time
      │      │    frequency_mask()   1-8 kHz → exactly 3585 bins
      │      │    fakeprint_from_band()
      │      │      minimum_filter1d(size=10)  ← the "lower hull"
      │      │      clip(hull, -45 dB)         ← ABSOLUTE floor (see note)
      │      │      residue = clip(spectrum - hull, 0, 5) / max
      │      │    ORT run → [1, 3585] → probability   (graph ends in sigmoid)
      │      │
      │      │    Reads: exact peak LOCATIONS. A vocoder leaves a periodic
      │      │    comb in 1-8 kHz; subtracting a running minimum removes the
      │      │    broadband shape (a property of the MUSIC) and leaves the
      │      │    narrowband residue (a property of the SIGNAL CHAIN).
      │      │
      │      └─ score_cepstrum(y)
      │           features.plan_windows()  16 windows, intro/outro trimmed 5 s
      │           per window:
      │             librosa.cqt(fmin=500, 48 bins, 12/oct, hop=512)
      │             log → dct(type=2, norm=ortho) → keep 24 coeffs
      │           stack → [16, 1, 24, T] → ORT → 16 logits → sigmoid
      │           MEDIAN pool
      │
      │           Reads: learned TEXTURE on a log-frequency axis. A pitch
      │           shift is a translation there, so this survives processing
      │           the fakeprint cannot.
      │
      │           Median, not max: max is an OR gate over however many windows
      │           you asked for. Not mean: one pathological window drags the
      │           track. The median asks whether the artifact is a property of
      │           the TRACK.
      │
      ├─ [4] ensemble.combine()                         <1 ms
      │      NEVER max(). max is an OR gate: at 2% FPR each with partly
      │      independent errors the union approaches ~4%, and you have doubled
      │      the false-accusation rate while feeling like you improved things.
      │
      │        both ≥ 0.6   → agree-ai      confidence × 1.15
      │        both ≤ 0.4   → agree-human   confidence × 1.15
      │        split        → disagree      confidence × 0.55, AND RECORDED:
      │                       cepstrum-only  → processed generator output
      │                                        (resampling smeared the comb)
      │                       fakeprint-only → bitcrushed HUMAN track
      │                                        (same mechanism, other cause)
      │        one missing  → single        confidence × 0.8
      │
      ├─ [5] screens.c2pa(path)                         ~0.1 s (optional dep)
      │      A signed manifest naming a generative tool is DECISIVE - a
      │      declaration, not an inference. Absence means nothing: credentials
      │      are trivially stripped.
      │
      ├─ [6] screens.container(meta)                    <1 ms
      │      Encoder strings, duration clustering, tag sparsity.
      │      Explicitly WEAK. This repo's own audio/ folder is the proof:
      │      most ai*.mp3 carry encoded_by="LAME in FL Studio", a DAW.
      │
      ├─ [7] decode.load_wideband() + screens.bandwidth()   ~0.3-0.5 s
      │      Its own 44.1 kHz / 90 s read. It CANNOT share the 16 kHz decode:
      │      at 16 kHz every file appears to stop at Nyquist.
      │
      ├─ [8] policy.decide()                            <1 ms
      │      c2pa.ai_declared          → AI, RETURN, decided_by=c2pa
      │      confidence < 0.45         → inconclusive
      │      score ≥ 0.80              → ai-generated
      │      score ≤ 0.20              → human-made
      │      otherwise                 → inconclusive
      │
      │      next_step = RETURN only if:
      │        exit_on_ai AND verdict==AI AND no vetoes AND conf ≥ 0.85
      │
      └─ [9] robustness.check()          ~0.7 s, ONLY on the RETURN path
             ── THE EXIT GATE
             60 s centred excerpt, 6 CNN windows, re-scored under:
               band_limit_12k · gain -6 dB · dither -45 dB
             numpy-domain, NO ffmpeg: this is the ungated endpoint, and every
             subprocess on attacker-supplied input is attack surface.

             sigma > 0.15 or ANY flip → downgrade confidence, force ESCALATE

             Not theoretical: ai1.mp3 scores a saturated 1.0 and its
             confidence collapses to 0.23 under band limiting.
```

> **The −45 dB floor is not level-relative.** `clip(hull, min_db)` is an
> absolute clip, so the fakeprint is level-invariant for ordinary content and
> *not* for very quiet content. That is inherited from training and must not be
> "fixed" — the weights were fitted against exactly this behaviour. Pinned in
> `test_the_min_db_floor_breaks_invariance_for_quiet_content`.

---

## 4. The decision: return or escalate

```
                      policy.decide() sets next_step
                                  │
              ┌───────────────────┴───────────────────┐
              ▼                                       ▼
        next_step = RETURN                      next_step = ESCALATE
              │                                       │
              ▼                                       │
     ┌────────────────────┐                           │
     │  EXIT GATE         │                           │
     │  robustness.check  │                           │
     └─────────┬──────────┘                           │
               │                                      │
        ┌──────┴──────┐                               │
        ▼             ▼                               │
     stable      NOT stable ───────────────────────▶  │
        │                                             │
        ▼                                             ▼
   ╔═════════════════════════╗            ╔═══════════════════════════╗
   ║ tiers._screen_only(     ║            ║ mode=screen → return as-is║
   ║   early_exit=True)      ║            ║ mode=ai/full → Level 2    ║
   ╚═════════════════════════╝            ╚═══════════════════════════╝
               │
               ▼
   _attach_appeal()  ── retain for 1 h
     S3.put(audio)  +  mongo.save_screen()
     response gains: screen_id, appeal{...}
```

Mode decides what a RETURN means:

| mode | Level 1 decisive | Level 1 escalates |
|---|---|---|
| `screen` | return | return (with `next_step: escalate`) |
| `ai` | **return, backbone never loads** | run Level 2 |
| `full` | **run Level 2 anyway** | run Level 2 |
| `audio` | Level 1 never runs at all | — |

`full` never short-circuits: a full report is bought for its evidence, and
silently omitting the deep layers because a cheap model was confident would
deliver a thinner document than the one requested.

---

## 5. Level 2, function by function

`POST /v1/analyses` → [`api/v1/analyses.py`](../src/labs/api/v1/analyses.py)

```
create_analysis(file, mode, verify, ...)
│
├─ require_scope("analyze")                      → 401/403
├─ mode in DEEP_MODES and not who.can("deep")    → 403 deep_tier_forbidden
├─ verify = verify or LABS_VERIFY_DEFAULT
├─ stage_upload()
├─ _dedup_hit(sha256, mode)                      → 200 with the stored report
│     Marketplaces re-upload constantly. Byte-identical audio in the same mode
│     returns the stored result rather than re-running minutes of CPU.
├─ limiter.acquire(bucket, max_inflight_per_key) → 429 too_many_inflight
├─ store.create(job)
│
└─ distributed()?  (SQS_QUEUE_URL AND MONGO_URI)
   │
   ├─ YES → _dispatch_remote()  S3 → Mongo → SQS   (order matters, §2)
   │        release the slot immediately: it bounds work in THIS process, and
   │        this process is about to do none
   │        → 202 {id, poll_url}
   │
   └─ NO  → runner.submit()  in-process ThreadPoolExecutor
            → 202 {id, poll_url}

────────────────────────── the worker side ───────────────────────────

worker.run()                                     → labs/worker.py
│
├─ warm()   screen models + Detector.load()      ~12 s, BEFORE polling
│           A cold worker that pulled first would hold a message invisible for
│           the model load on top of the analysis.
│
└─ loop: queue.receive(WaitTimeSeconds=20)
   │
   └─ _handle(job, attempts)
      │
      ├─ mongo.job_is_terminal(job.id)?  → delete, drop
      │     SQS is at-least-once. A finished job WILL sometimes arrive again,
      │     and re-running costs 30-90 s for an answer already stored.
      │
      ├─ attempts > 3?                   → fail, delete
      │     Deterministically-broken file. Without a ceiling it recirculates
      │     forever, occupying a worker each time.
      │
      ├─ _start_heartbeat()   every 120 s, extend visibility to 900 s
      ├─ _fetch(job)          S3 → /tmp
      │
      └─ tiers.analyse(mode="ai"|"full")
         │
         ├─ Level 1 first (see §3)  ~1.5 s
         │    mode=ai and decisive → RETURN, backbone never loads
         │
         └─ detector.predict()                   → labs/ml/detector.py
            │
            ├─ load_segments()                   → labs/analysis/audio.py
            │  │
            │  ├─ get_segments_from_wav()   beat_this  ~8 s
            │  │     → beats[], downbeats[]
            │  ├─ find_optimal_segment_length()
            │  │     keep downbeats within 10% of the modal bar interval
            │  ├─ torchaudio.load → mono → resample 24 kHz
            │  ├─ eligible = downbeats where start + 10 s fits in the file
            │  │
            │  └─ plan_starts(eligible, max_segments=48, spread=True)
            │        ── THE COVERAGE FIX
            │        Upstream took the FIRST 48. Downbeats arrive ~2.5 s
            │        apart and windows are 10 s, so the 48 slots were spent
            │        long before the song ended:
            │            mean coverage 0.77, 7/10 tracks at the cap
            │            1.mp3: window 48 ended at 144 s of a 186 s track
            │        Now: 48 indices INTERPOLATED across the whole grid.
            │            coverage → 0.898-0.999 (mean 0.981), same cost,
            │            same tensor shape Stage-2 was trained on.
            │        NOT a constant step: ceil(54/48)=2 selects 27 windows,
            │        half the budget, and that cost real accuracy.
            │        Cap stays 48 — Stage-2 was trained on exactly that length.
            │
            ├─ stage1(audio, cfg)                ── THE CASCADE
            │  │
            │  │  first = full[::3]              16 of 48 windows
            │  ├─ embed_indices(first, cache)    MERT forward, batch 4
            │  ├─ classify_subset(cache, first)  Stage-2 → logit
            │  │
            │  ├─ |logit| ≥ 5.0 ?
            │  │    YES → DONE. 1 pass, 16 windows.
            │  │          Measured: |logit| ≥ 5 moves ≤ 0.01 between stride
            │  │          1 and 4. These tracks are immune to segment count.
            │  │
            │  │    NO  → embed_indices(full, cache)
            │  │          computes ONLY the 32 skipped windows; the first
            │  │          pass's embeddings are reused, because Stage-1
            │  │          embeddings are per-segment INDEPENDENT — nothing
            │  │          couples them until Stage-2's SSM.
            │  │          So escalating costs 1.0x of a full pass, not 1.33x.
            │  │          (|logit| < 2 tracks FLIP under decimation. That is
            │  │           why this is a cascade and not just fewer segments.)
            │  │
            │  └─ → logit, cache, used_indices, cascade_info
            │
            ├─ scaled_sigmoid(logit)             → probability
            ├─ _band(raw_logit)                  ── THE INCONCLUSIVE BAND
            │     |logit| < 2.0 → verdict = "inconclusive", decisive = False
            │     ai1.mp3 sits at |0.4| and lands on either side of zero
            │     depending only on which windows were analysed. The sign of
            │     a coin flip is not a finding.
            │
            ├─ mode=full → AudioCache: features + musical + production ~19 s
            │
            └─ build_report()
               starts narrowed to `used` — on an early exit the embeddings
               cover a SUBSET, so the full list would mislabel every row

      ├─ attach_detection()   escalation policy → ACR → consensus → _promote
      │     A positive catalogue match is stronger evidence than statistical
      │     inference, so it takes priority AND is lifted to the headline
      │     `prediction`/`verdict`. The primary survives under
      │     detection.primary; verdict_source says which is on top.
      │
      ├─ _persist()           report → Mongo, audio → S3
      ├─ feedback.resolve()   if this was an appeal (see §6)
      ├─ mongo.update_job(succeeded)
      └─ queue.delete(receipt)
```

---

## 6. The appeal loop

The case the user asked for: Level 1 said AI, exited, and the user disagrees.

```
  ┌──────────────────────────────────────────────────────────────────┐
  │ POST /v1/screen                                                  │
  │   → verdict: ai-generated, next_step: return                     │
  │   → _attach_appeal():  S3.put(audio) + mongo.save_screen(1 h TTL)│
  │   → response carries screen_id + appeal{available, endpoint, ...}│
  └───────────────────────────────┬──────────────────────────────────┘
                                  │
                    user clicks "I disagree"
                                  │
                                  ▼
  ┌──────────────────────────────────────────────────────────────────┐
  │ POST /v1/escalations   {screen_id, reason?}     scope: deep      │
  │                                                                  │
  │  ├─ mongo.get_screen(screen_id)                                  │
  │  │    missing/expired → 404 (same answer as never-existed:       │
  │  │    distinguishing them leaks other people's traffic)          │
  │  ├─ already escalated → 200 duplicate, the SAME job id           │
  │  │    a double-clicked button must not start a second 60 s run   │
  │  ├─ _materialise()  S3 → /tmp   (or the attached re-upload)      │
  │  │    neither available → 410 Gone                               │
  │  ├─ _dispatch()  mode=FULL, escalated_from=screen_id             │
  │  │    full, not ai: someone disputing a verdict is owed the       │
  │  │    evidence — and full is the one mode that cannot            │
  │  │    short-circuit back to Level 1 and return the very answer   │
  │  │    being appealed                                             │
  │  ├─ mongo.mark_screen_escalated()                                │
  │  └─ mongo.save_feedback({kind: escalation, outcome: PENDING})    │
  │       written BEFORE the analysis, or a fast job has nothing to  │
  │       write its outcome onto                                     │
  └───────────────────────────────┬──────────────────────────────────┘
                                  │  202 + job id
                                  ▼
                     Level 2 runs (§5), 25-70 s
                                  │
                                  ▼
  ┌──────────────────────────────────────────────────────────────────┐
  │ feedback.resolve(screen_id, report)                              │
  │   called from BOTH job paths: JobRunner._run and Worker._handle  │
  │                                                                  │
  │   L1 = ai-generated,  L2 = human-made     → OVERTURNED  ⚠ warn   │
  │   L1 = ai-generated,  L2 = ai-generated   → UPHELD               │
  │   L2 = inconclusive                        → INCONCLUSIVE        │
  │        (NOT an overturn: it neither confirms nor clears, and     │
  │         counting it either way corrupts the one metric that      │
  │         measures the detector rather than the UI)                │
  └───────────────────────────────┬──────────────────────────────────┘
                                  ▼
              GET /v1/feedback/summary   (admin scope)
              overturn_rate = overturned / (overturned + upheld)
              null until something is decided — a rate over zero
              samples reads as "the detector is never wrong"
```

**Why this is worth more than fairness.** An overturned appeal is a confirmed
Level-1 false positive *with the audio and the disputing human's reason
attached*. That is the only labelled data this system generates about its own
mistakes, on exactly the population where it matters — tracks confident enough
for Level 1 to have stopped, and wrong enough for a person to complain. No
offline evaluation set gives you that.

---

## 7. Where the time actually goes

Measured, Apple M1, 4 threads, uncontended.

```
LEVEL 1                                      mean 1.54 s
├── decode (16 kHz, ≤300 s)          0.18-2.52  ████████████
├── models (fakeprint + cepstrum)    0.30-0.82  ████
├── bandwidth (44.1 kHz partial)     0.24-0.83  ███
├── ffprobe                          0.05-0.23  █
├── c2pa / container / ensemble      < 0.01     ·
└── exit gate (RETURN path only)     0.69       ███

LEVEL 2                        mean 41.2 s · median 28.6 s
├── beat tracking (beat_this)        ~8 s       ████
├── Stage-1 MERT                     14-55 s    ████████████████████████████
│     16 windows (early exit)        ~14 s
│     48 windows (escalated)         ~50 s
├── Stage-2                          < 0.5 s    ·
└── mode=full adds DSP + musical     ~19 s      ██████████

INFRASTRUCTURE
├── ALB + API accept + enqueue       < 0.2 s
├── SQS long-poll pickup             0-20 s     (WaitTimeSeconds=20)
├── S3 round trip (5 MB)             ~0.5 s
├── worker warm start                ~12 s      model load
├── worker from warm pool            ~30 s      ← was 3-5 min
└── worker cold (new instance)       3-5 min    launch + 2.5 GB + 1.3 GB
```

### What the user actually waits

| Path | Wait |
|---|---|
| Screen, slot free | **~1.5 s** |
| Screen, at capacity | `429`, retry now |
| Deep, warm worker free | ~30-70 s |
| Deep, warm pool resume | +~30 s |
| Deep, fully cold fleet | +3-5 min |

> **Autoscaling removes QUEUE time, not PROCESSING time.** Level 2 is 25-70 s
> of CPU for one track however many workers exist. Level 1 at 1.5 s is already
> negligible; Level 2 becomes negligible only with INT8 (~10 s) or a GPU
> (~3-5 s). See [deployment.md](deployment.md#instance-choice-and-the-intelgraviton-trade-off).

---

## 8. What happens when things go wrong

| Failure | Behaviour | Where |
|---|---|---|
| One Level-1 screen throws | That screen reports unavailable; the rest continue | `pipeline.timed()` |
| A Level-1 model file is missing | `agreement: single`, confidence × 0.8 | `models.score_both` |
| Decode fails entirely | `verdict: unavailable`, `next_step: escalate` — the deep tier decodes differently and may succeed | `pipeline.run` |
| ffprobe absent | Container screen unavailable; nothing else affected | `decode.probe` |
| c2pa not installed | Reported as not installed; not treated as "no credentials" | `screens.c2pa` |
| Screen at concurrency limit | `429 screen_busy` before the upload is staged | `screen._SLOTS` |
| torch not installed (screen image) | Deep modes `501`; Level 1 unaffected | `get_detector → None` |
| Backbone still loading | `503 model_loading` (single-container only) | `create_analysis` |
| SQS receive fails | Empty list, 2 s sleep, keep polling — a raise would exit the loop and roll the whole fleet | `SqsQueue.receive` |
| Unparseable SQS message | Deleted, so it cannot poison the queue | `SqsQueue.receive` |
| SQS delete fails | Message redelivered; the terminal-state check makes that cheap | `Worker._handle` |
| Analysis fails on bad audio | Terminal — fails identically on every retry | `Worker._classify` |
| Analysis fails internally | Left for redelivery, up to 3, then DLQ | `Worker._handle` |
| Worker gets SIGTERM | Finishes the current analysis, returns any held message | `install_signal_handlers` |
| Persistence fails | Analysis still succeeds; the caller has their result | `_persist` |
| Feedback resolution fails | Analysis still succeeds; a lost data point, not a lost result | `feedback.resolve` |
| Appeal audio expired | `410 Gone` with instructions to re-upload | `_materialise` |
| Webhook fails | 3 retries with backoff; polling remains the fallback | `JobRunner._notify` |

The pattern throughout: **a failure in something supporting the answer must
never destroy the answer.** Persistence, feedback, webhooks and individual
screens all degrade; only a failure to decode or to run the model itself fails
the request.
