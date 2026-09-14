<p align="center">
  <img src="assets/labs.svg" alt="LABS" width="520">
</p>

<p align="center">
  <strong>Audio intelligence API.</strong><br>
  AI-generated music detection, full signal analysis, and a suite of focused measurement tools.
</p>

<p align="center">
  <em>A product of Beat22.</em>
</p>

---

## What this is

A backend service that takes an audio file and returns measurements about it.
Three things, behind one HTTP interface:

| | |
|---|---|
| **Detection** | Whether a track was machine-generated, with a per-window timeline and a calibrated confidence rather than a bare label. |
| **Analysis** | Tempo, key, loudness, stereo image, arrangement, spectral balance and the derived catalogue features, from the signal itself. |
| **Tools** | Seven focused products — mastering check, tempo, key, reference match, vocal, beat-and-vocal fit, and commercial benchmarking — each answering one question well. |

### Detection runs in two tiers

| | Level 1 — `POST /v1/screen` | Level 2 — `POST /v1/analyses` |
|---|---|---|
| Models | 2 ONNX graphs, **1.2 MB** | MERT-v1-95M + transformer, **1.34 GB** |
| Measured | **1.07-3.20 s** (mean 1.54) | 22-90 s |
| Transport | **synchronous** - answer in the response | asynchronous - job id, then poll |
| Access | free, ungated | `deep` scope, billable |
| Image | ~350 MB, **no torch** | ~2.5 GB |

Level 1 screens; Level 2 is the deep model. `mode=ai` runs Level 1 first and
skips the backbone entirely when Level 1 is decisive, which is what makes the
free tier ~110x cheaper per track rather than merely faster.

A Level-1 `human-made` is **not** an exoneration and always escalates: both
Level-1 models only recognise generators they were trained on, so their
silence is not evidence. Branch on the `next_step` field (`return` or
`escalate`), which is in every Level-1 response.

Level 2 is asynchronous. Submit a file, get a job id, poll it or supply a
webhook. A long-running analysis never holds an HTTP connection open, which is
what keeps the service working behind a load balancer.

## Quick start

```bash
git clone git@github.com-work:Audio-Cognition/LABS.git
cd LABS
python -m venv .venv && source .venv/bin/activate
pip install --extra-index-url https://download.pytorch.org/whl/cpu -r requirements-dev.txt
pip install -e .
```

Run it:

```bash
labs
```

The service answers on `http://localhost:8000`. Interactive documentation is at
`/docs`, the machine-readable schema at `/openapi.json`.

Screen a track — free, synchronous, ~1.5 s:

```bash
curl -X POST http://localhost:8000/v1/screen -F "file=@track.mp3"
```

The deep analysis — asynchronous:

```bash
curl -X POST http://localhost:8000/v1/analyses \
  -F "file=@track.mp3" \
  -F "mode=full" \
  -F "wait=20"
```

## Running with Docker

Three build targets, because the tiers have very different dependency
footprints and an autoscaling fleet pays for that on every cold start:

```bash
docker build --target screen -t labs-screen .   # Level 1 only, ~350 MB, no torch
docker build --target worker -t labs-worker .   # SQS consumer, serves no HTTP
docker build -t labs-api .                      # both tiers in one process (default)
```

For the single-container deployment:

```bash
cp deploy/.env.example deploy/.env
# fill in deploy/.env, then:
docker compose -f deploy/docker-compose.yml up -d --build
```

Model weights land on a named volume, so replacing the container does not
re-download them.

## Documentation

| Document | Covers |
|---|---|
| [docs/flow.md](docs/flow.md) | **End-to-end trace**: one upload through both levels, code / architecture / AWS |
| [docs/api.md](docs/api.md) | Every endpoint, parameter, response shape and error code |
| [docs/architecture.md](docs/architecture.md) | How the service is put together and why |
| [docs/deployment.md](docs/deployment.md) | Topologies, AWS autoscaling, **cost**, concurrency, going live |
| [docs/configuration.md](docs/configuration.md) | Every environment variable |
| [docs/operations.md](docs/operations.md) | Health, keys, incidents, what to check when |
| [docs/testing.md](docs/testing.md) | Running the suite; validating a model change |
| [docs/security.md](docs/security.md) | The threat model and what is enforced |

## Layout

```
src/labs/
  application.py     ASGI app: logging, lifespan, middleware, routers
  tiers.py           routes a request through Level 1, then Level 2
  verdicts.py        the shared verdict vocabulary (imports nothing, on purpose)
  worker.py          the distributed SQS consumer; serves no HTTP
  api/               HTTP surface — errors, dependencies, field selection
    v1/              screen, analyses, tools, system
  core/              config, authentication, upload staging, outbound URL vetting
  screen/            LEVEL 1 — the free tier. No torch anywhere in here.
  ml/                LEVEL 2 — architecture, checkpoints, detector, cascade
  audio/             beat tracking and segmentation
  analysis/          the measurement passes behind a report
  artist/            genre models and derived commercial insight
  tools/             one module per tool, one registry
  services/          jobs, the SQS queue, persistence, caching, verification
  screen/weights/    the two Level-1 ONNX graphs (1.2 MB, package data)
  cli/               key management and scheduled corpus jobs
tests/
  unit/              fast, no model, no network
  integration/       the real ASGI app through TestClient
deploy/              compose file, nginx, environment template
docs/                the table above
```

## Requirements

- Python 3.11
- ffmpeg and libsndfile for decoding
- ~6 GB RAM resident with weights loaded; 12 GB is a comfortable ceiling
- MongoDB, to deduplicate repeat submissions and retain results

CPU only. No GPU is required and none is used.

## Tests

```bash
pytest                    # fast suite, no weights, no network
pytest -m slow            # real inference against checkpoints
pytest --cov=labs         # with coverage
```

---

<p align="center">
  <sub>LABS is a product and property of Beat22. All rights reserved.</sub>
</p>
