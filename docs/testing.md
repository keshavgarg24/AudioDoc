# Testing

Two suites with different costs. The fast one runs on every change; the slow
one runs when the model or its numerics could have moved.

---

## Running

```bash
pip install -r requirements-dev.txt

pytest                       # fast: no weights, no network, no database
pytest -m slow               # real inference against checkpoints
pytest -m integration        # the ASGI app through TestClient
pytest --cov=labs            # with coverage
pytest tests/unit/test_net.py -v
```

The default deselects `slow`. Those tests load multi-gigabyte checkpoints and
may fetch the backbone on first run, which takes minutes — too slow to sit in
an edit-run loop, so they are opt-in.

## Layout

```
tests/
  conftest.py           fixtures: synthetic audio, TestClient, in-memory Mongo
  unit/                 fast, isolated
    test_config.py      environment parsing
    test_net.py         webhook URL validation, SSRF
    test_persistence.py dedup, storage tiering, audio records
    test_security.py    keys, fingerprints, scopes, limits
    test_tools.py       registry, cache keys, digests
    test_uploads.py     staging, magic bytes, size caps, sweeper
  integration/
    test_api.py         the HTTP surface end to end
```

Nothing in the fast suite touches the network, a real database or the model.
`conftest.py` sets that up before any `labs` module is imported — settings are
read from the environment at import time, so a `setdefault` after the first
import would be silently ignored and the suite would run against whatever the
developer happened to have exported.

MongoDB is stubbed with `mongomock`. Audio is a WAV built byte by byte in
`conftest.make_wav`, so the upload tests have a real container to push through
the magic-byte check without a checked-in binary fixture.

---

## What the fast suite is actually protecting

Most of it is ordinary. These are the ones that exist because the failure they
describe is silent:

**`test_sha256_is_stored_without_object_storage`** — deduplication reads
`audio.sha256` on the stored analysis. If that field is ever written only as a
by-product of the object-storage upload, a deployment without a bucket stops
deduplicating: no error, no log line, just every submission paying for a full
pipeline run. This test fails the moment that happens.

**`test_fingerprint_exposes_only_a_few_characters_of_the_secret`** — the
fingerprint is stored, logged and displayed as a non-secret identifier. Because
`token_urlsafe` emits underscores, an unbounded `split("_")` cuts the secret
apart and publishes most of it.

**`test_static_key_comparison_is_constant_time`** and
**`test_every_candidate_key_is_compared`** — assert against the source rather
than trying to time anything, which is hopelessly flaky in CI. A plain `in`
test, or a `break` on match, reintroduces a timing oracle.

**`test_ipv6_wrappers_are_judged_on_the_embedded_v4_address`** — `is_global`
reports some transition addresses as public even when they wrap a private v4
target. That is a real SSRF bypass.

**`test_prefix_helper_matches_the_sweeper_glob`** — staged uploads are found by
prefix. If the prefix and the glob drift, orphans accumulate until the disk
fills, with no symptom before that.

**`test_health_does_not_disclose_infrastructure`** — `/health` is
unauthenticated. This asserts no bucket name, connection string, model
repository or checkpoint path appears in it.

**`test_options_participate_in_the_key`** — a cache key that ignored options
would return a result computed for a different question.

---

## Measured performance

Baseline for any future change. 10 tracks from `audio/`, Apple M1, 4 threads,
uncontended (a concurrent test run inflates these badly — measure alone).
`base` is the pre-tiering pipeline at full density.

| Track | L1 (s) | L2 (s) | base (s) | logit | windows | escalated | coverage |
|---|---:|---:|---:|---:|---:|:---:|---:|
| 1.mp3 | 3.25 | 28.75 | 83.8 | −7.211 | 16 | no | 0.992 |
| 2.mp3 | 1.59 | 41.93 | 77.4 | −7.021 | 16 | no | 0.987 |
| 3.mp3 | 1.27 | 28.32 | 73.2 | −7.211 | 16 | no | 0.995 |
| 4.mp3 | 1.16 | 28.52 | 61.0 | −6.086 | 16 | no | 0.991 |
| 5.mp3 | 1.07 | 24.51 | 50.6 | −7.209 | 13 | no | 0.898 |
| ai1.mp3 | 2.27 | 71.31 | 63.8 | −0.421 | 48 | yes | 0.982 |
| ai2.mp3 | 1.40 | 28.17 | 75.8 | +7.664 | 16 | no | 0.994 |
| ai3.mp3 | 1.15 | 70.27 | 84.1 | +1.390 | 48 | yes | 0.999 |
| ai4.mp3 | 1.09 | 65.03 | 70.5 | +4.574 | 47 | yes | 0.993 |
| ai5.mp3 | 1.12 | 24.88 | 62.5 | −7.209 | 14 | no | 0.980 |

```
L1     mean 1.54 s   median 1.21 s   max 3.25 s
L2     mean 41.2 s   median 28.6 s
base   mean 70.3 s   median 71.8 s
       mean 1.71x    median 2.51x    coverage 0.77 -> 0.981 (mean)
```

Read the distribution, not the mean: **2.0–2.7× on the seven tracks whose
first pass was confident, break-even to ~12% slower on the three that
escalate.** The cascade never buys speed by giving up accuracy, so tracks
that cannot take decimation get no speedup — that is the design working, not
failing.

### Caveats that must travel with these numbers

**Early exit fired 0/10.** Every track reached Level 2, so *none* of the 1.71×
comes from the tier ordering — it is all from the Level-2 spread and cascade.
On this sample Level 1 is ~1.5 s of pure overhead. That is a property of the
sample: `ffprobe` shows four of five `ai*.mp3` files are tagged
`encoded_by="LAME in FL Studio"`, a DAW, so the set contains essentially one
confidently-AI track, and the exit gate correctly refused it. **The
early-exit hit rate on real generator output is unmeasured.**

**No accuracy claim is possible from this set.** Only `ai2.mp3` carries AI
provenance in its tags (`AHA Music AI`). The filenames are not labels, so any
accuracy figure computed against them would be fiction.

**Two verdicts moved, and both are explained.** `ai1` went +0.393 → −0.421
(the coverage fix changed which windows were analysed) and now reports
`inconclusive` in both directions, so no published claim changed. `4.mp3`
moved −3.238 → −6.086 for the same reason; it was and remains `human-made`.

**The `base` column was measured under CPU contention** and is therefore
somewhat inflated. The one clean isolated baseline datapoint is 1.mp3 at
67.8 s, against 83.8 s here — so treat the speedup ratios as
optimistic by roughly 20% and the floor as ~2.0× rather than 2.5×.

### Reproducing

```bash
# Level 1 only, fast, needs no checkpoints
python -c "
import glob, time
from labs.core.config import get_settings
from labs.screen import models, pipeline
cfg = get_settings().screen; models.warm(cfg)
for f in sorted(glob.glob('audio/*.mp3')):
    t = time.time(); r = pipeline.run(f, cfg)
    print(f'{f} {r.verdict} {time.time()-t:.2f}s')
"
```

For the full tiered path use `labs.tiers.analyse(path, mode='ai', ...)` with a
loaded `Detector`, and run it with nothing else on the machine.

---

## Validating a model change

Some changes are not code changes in any meaningful sense. Treat all of the
following as model changes:

- `LABS_AUTOCAST_DTYPE` — enabling bfloat16 or float16
- bumping `torch`, `torchaudio` or `transformers`
- changing `LABS_NORMALIZE_WAVEFORM`
- changing `LABS_STAGE1_FAKE_INDEX`
- moving `LABS_CKPT_REVISION`

Each moves the arithmetic, and the output is a calibrated probability with a
threshold applied to it. A track sitting near the boundary flips, and no unit
test will notice.

### Procedure

Assemble a labelled set — at minimum a dozen tracks per class, ideally
including genres and production styles the service actually sees. Put them
somewhere outside the repository.

Record a baseline before touching anything:

```bash
for f in labelled/*.mp3; do
  curl -sS http://localhost:8000/v1/analyses \
    -H "X-API-Key: $LABS_API_KEY" \
    -F "file=@$f" -F "mode=ai" -F "wait=25" \
  | jq -c --arg f "$f" '{file:$f, prediction, fake_probability, raw_logit}'
done | tee baseline.jsonl
```

Apply the change, restart, run the identical loop into `candidate.jsonl`, then
compare:

```bash
diff <(jq -c '{file, prediction}' baseline.jsonl) \
     <(jq -c '{file, prediction}' candidate.jsonl)
```

**Any flipped verdict is a stop.** Investigate before shipping.

If no verdict flipped, look at the logits. Movement under about 0.1 is
numerical noise. Larger, consistent movement in one direction means the
calibration has shifted even though this particular set stayed on the right
side of the threshold — the next set may not.

### The two settings with measured history

`LABS_NORMALIZE_WAVEFORM` applies per-segment zero-mean/unit-variance before
the backbone. It is on by default because it pairs with the checkpoints this
service loads. Measured logits per track, on with the default checkpoints
versus off:

```
1.mp3   +7.67 / -2.56      22.mp3  +0.39 / -3.21
3.mp3   -7.21 / -7.21       9.mp3  -7.21 / -7.21
```

With it off, every track tested lands on "Real" — a detector that has stopped
detecting. Do not change it without repeating this measurement.

`LABS_STAGE1_FAKE_INDEX` selects which column of the two-class head means
"AI-generated". It is `0`, measured rather than assumed: with index `1` the
per-window scores are perfectly anti-correlated with the second stage, so the
head orders its classes `[fake, real]`.

---

## Adding a tool

A tool is a module with a `SPEC` and a `run()`, listed in the registry in
`tools/__init__.py`. The API, the catalogue endpoint and the result cache all
read from the registry, so nothing else changes.

`tests/unit/test_tools.py` iterates the registry, so a new tool is covered for
completeness automatically — every spec must carry a name, summary, inputs,
stated accuracy, basis and limitations. Those strings are the product's public
claims and the test refuses a tool that ships without them.

Write the tool's own behavioural tests alongside that.

---

## Continuous integration

The fast suite needs no weights, no network and no database, so it runs
anywhere:

```yaml
- run: pip install -r requirements-dev.txt
- run: ruff check .
- run: pytest --cov=labs --cov-report=term-missing
```

Do not run `-m slow` in CI on every commit. It downloads gigabytes. Run it on a
schedule, or on changes that touch `ml/`, `audio/`, `analysis/` or the pinned
dependencies.
