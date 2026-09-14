# Security

What this service is exposed to, and what is enforced against it.

---

## Threat model

The service accepts untrusted files from the internet, runs them through large
C parsers, stores results, and makes outbound HTTP requests to caller-supplied
URLs from inside a private network holding an instance role.

Four surfaces follow from that:

| Surface | Risk |
|---|---|
| Uploads | Malformed input reaching ffmpeg or libsndfile |
| Outbound webhooks | Server-side request forgery against internal services |
| Authentication | Key recovery, replay, privilege escalation |
| Responses | Disclosure of infrastructure detail |

---

## Uploads

Two filters run before a file reaches a decoder.

**Size**, enforced as the stream is consumed rather than after. An oversized
upload is refused without ever being written in full, so a malformed 50 MB file
costs one buffer rather than a full disk write.

**Container signature**, checked against the leading bytes on the first chunk.
The extension is a claim by the caller; a `.wav` that begins `PK\x03\x04` is a
zip and is refused. Accepted signatures cover wav, flac, ogg/opus, mp3 with and
without an ID3 tag, ISO-BMFF (where the `ftyp` tag sits at offset 4, not 0) and
ADTS AAC.

Neither substitutes for the decoder's own validation. They are the cheap
filters that keep obvious junk away from the expensive, riskier code.

Rejections leave nothing on disk. Staged files carry a shared prefix, and a
startup sweeper removes any older than an hour, so a crash mid-upload does not
accumulate. The prefix and the sweeper's glob are defined once, together,
because a change to one and not the other stops the sweep silently.

The container runs as a non-root user with all capabilities dropped and
`no-new-privileges` set. If a parser is ever exploited, the blast radius should
not include the filesystem or the ability to install packages.

---

## Outbound requests

`webhook_url` is a URL chosen by the caller that this service fetches from
inside the VPC. Unguarded, that is an SSRF primitive: on a cloud host the
sharpest edge is the instance metadata service on the link-local range, but the
wider problem is every internal load balancer, admin port and database that is
reachable only because this process is inside the perimeter.

Enforced in `core/net.py`:

- **Scheme** must be `https`. Plain HTTP needs an explicit opt-in, because the
  analysis travels in the request body.
- **Resolution, not string matching.** The hostname is resolved and *every*
  address it resolves to must be globally routable. Checking the literal string
  is useless — a hostname under the caller's control can point at loopback.
- **IPv6 wrappers are unwrapped** before judging. A v4-mapped address like
  `::ffff:127.0.0.1`, a 6to4 address, or a Teredo address each embed a v4
  target that `is_global` alone reports incorrectly. This is a real bypass, and
  there are tests for each form.
- **Redirects are not followed.** A permitted host can otherwise `302` straight
  to a blocked one.
- **Service ports are refused** outright: ssh, telnet, smtp, mysql, postgres,
  redis, elasticsearch, memcached, mongodb.
- **Credentials in the URL are refused** rather than stripped, because they end
  up in logs.

Validation runs twice — at submission, so the caller gets a `422` they can act
on, and again immediately before delivery, because minutes pass in between and
that is ample time to repoint a DNS record.

Rejections **do not echo the resolved address**. Naming it would turn the
endpoint into an internal network scanner with a clean oracle: submit a
hostname, read the error, learn what is behind the perimeter.

### A documented residual gap

The delivery request keeps the original hostname rather than substituting the
resolved IP, because swapping in a literal address breaks TLS certificate
verification. That leaves a narrow window between the check and urllib3's
connect in which a name could re-resolve.

Closing it entirely needs a pinning transport adapter. It is not closed here,
deliberately: the exposure is a millisecond race for the privilege of receiving
one analysis report, while the attack this actually defends against — naming an
internal or link-local host outright — is fully blocked.

`LABS_WEBHOOK_ALLOW_PRIVATE` disables the address check. It exists for a
service-mesh sidecar on loopback and should not be set otherwise.

---

## The ungated tier

`POST /v1/screen` is reachable without a key when `LABS_REQUIRE_AUTH` is off.
That is a deliberate product decision, and it changes the threat model for
that one path: it is the endpoint a stranger reaches first.

What it is allowed to touch is correspondingly minimal.

| | Screen task | API task | Worker |
|---|---|---|---|
| SQS send | — | yes | — |
| SQS receive | — | **no** | yes |
| S3 write | — | yes | **no** |
| S3 read | — | yes | yes |
| MongoDB | — | yes | yes |

The screen task in the AWS topology holds **no IAM policy at all**. It decodes
a file, runs two ONNX graphs and answers inline.

Three further properties of that path:

- **No subprocess on attacker-supplied audio, except ffprobe.** The exit
  gate's perturbations are computed in the numpy domain rather than through
  ffmpeg, specifically because every subprocess spawned on a stranger's upload
  is attack surface. Codec-level augmentation lives in the gated full tier.
- **Admission control before staging.** A saturated instance answers `429`
  before writing the upload to disk, so load shedding costs no disk or
  bandwidth.
- **Generic error text.** A decode traceback is a description of the parsing
  stack in front of the service. `/v1/screen` returns
  `503 screen_failed` and logs the detail.

### Retention on the free tier

Only a *decisive* Level-1 result is retained — the case where the caller was
cut short and has no deep result. Every other path already escalates to a
stored analysis, so retaining them here would duplicate rather than preserve.

Retention defaults to **forever** (`LABS_SCREEN_RETAIN_S=0`). A retained screen
plus the appeal it attracts is one labelled example of the free tier being
wrong, and that is the only such data the system produces — no offline
evaluation set gives you the population of tracks confident enough for Level 1
to have stopped and wrong enough for a person to complain.

This is a deliberate trade against the obvious counter-argument: stored audio
is other people's work, and a breach exposes everything ever submitted rather
than a recent window. If a retention or privacy obligation applies to your
deployment, set `LABS_SCREEN_RETAIN_S`, `LABS_RESULT_TTL_DAYS` and
`LABS_AUDIO_TTL_DAYS` to positive values and the TTL indexes and S3 lifecycle
rule activate. See [configuration](configuration.md#retention-defaults-to-keeping-everything).

An appeal for another owner's `screen_id` answers `404`, not `403`. `403`
would confirm the id exists, which is free information about someone else's
traffic.

## Authentication

Keys are `labs_<env>_<32 random bytes, url-safe>`. A key is returned once, at
creation; only a SHA-256 digest is stored, so a leaked database does not leak
usable keys.

**Comparison is constant time.** A plain `in` test against the static key list
short-circuits on the first differing byte, which leaks how much of a guess was
correct and allows a key to be recovered a byte at a time. Every candidate is
compared with `secrets.compare_digest`, and the loop does not break on a match,
so neither the bytes nor the key's position in the list becomes an oracle.
There are tests asserting both properties against the source.

**Fingerprints are bounded.** The non-secret identifier stored in the database,
returned by the key commands and written to logs contains the prefix, the
environment and only the first six characters of the secret. The bound is load
bearing: `secrets.token_urlsafe` draws from an alphabet that includes the
underscore, so splitting the key on `_` without a limit cuts the *secret* apart
as well as the prefix, and rejoining all but the last piece publishes most of
it. There is a test for this.

**Scopes** are checked per route. `admin` implies the rest; everything else is
exact. `/v1/diagnostics` requires `admin` rather than merely authentication,
because it names the weight repository and storage layout.

**Quotas and rate limits** are per key: a sliding request window, plus a
concurrent-analysis bound. Both are in-process, which is correct for a single
container and is called out in the scaling notes as something to move to Redis
before running two.

---

## Webhook authenticity

Deliveries are signed `HMAC-SHA256(secret, "{timestamp}.{raw_body}")`, with the
timestamp in `X-Webhook-Timestamp` and the signature in `X-Webhook-Signature`.

Receivers should compare in constant time and reject a timestamp older than a
few minutes, so a captured delivery cannot be replayed. Verification code is in
[api.md](api.md#webhooks).

Without `LABS_WEBHOOK_SECRET` set, deliveries are unsigned and a receiver
cannot distinguish a real callback from a forged one. Set it wherever webhooks
are used.

---

## Response hygiene

`/health` is unauthenticated, so it carries only liveness, model readiness and
queue depth. It reports `degraded: true` rather than the load error itself: a
caller needs to know whether a failure is theirs or ours, not what failed. The
error text carried file paths and exception detail.

Checkpoint provenance, the mirror URI, storage reachability and the queue
breakdown live behind `admin` on `/v1/diagnostics`.

The secondary verification provider is never named in any response, error or
log line that reaches a caller. Failures surface as
`verification_unavailable`.

Every error uses one envelope with a stable `code`. Internal errors return a
fixed message; exception detail goes to the logs, not to the caller.

---

## CORS

`LABS_CORS_ORIGINS=*` automatically disables credentials, and logs a warning.

This is not defensive formality. Starlette does not send
`Access-Control-Allow-Origin: *` when credentials are enabled — the
specification forbids the literal wildcard alongside credentials — so it
reflects the requesting origin instead. The effect of `*` plus credentials is
therefore not "no CORS restriction" but "every origin on the internet is
allow-listed, with credentials, and may read the responses". Disabling
credentials keeps an open deployment usable without handing it that property.

Name exact origins in production.

---

## Dependencies

Runtime dependencies are pinned to exact versions, not floors. Two are load
bearing beyond the usual reproducibility argument: the backbone loads through
`trust_remote_code`, so the checkpoint carries code expecting a specific
library API; and torch's CPU numerics have shifted across minor versions
before, which moves a thresholded probability.

A `>=` here means an image rebuilt six months from now runs a model nobody
tested. Bump deliberately, re-validate against labelled audio, then commit the
new pin.

---

## Reporting

Send security reports to Beat22 privately rather than opening an issue.
