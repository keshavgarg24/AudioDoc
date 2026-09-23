'use client'

// Full API reference.
//
// Deliberately exhaustive: this is what an integrator reads instead of asking.
// Every endpoint documents its auth, its errors, its limits and a working
// example. Nothing here names the detection architecture or any third-party
// provider, which are internal details.

import React from 'react'
import { TOOLS } from '../../toolConfig.js'

function Code({ children }) {
  return <pre className="api-code"><code>{children}</code></pre>
}

function Endpoint({ method, path, auth, summary, children }) {
  return (
    <div className="api-endpoint" id={path.replace(/[^\w]/g, '-')}>
      <div className="api-endpoint-head">
        <span className={`api-method ${method.toLowerCase()}`}>{method}</span>
        <code className="api-path">{path}</code>
        {auth ? <span className="api-auth">{auth}</span> : null}
      </div>
      <p className="api-summary">{summary}</p>
      {children}
    </div>
  )
}

function Table({ head, rows }) {
  return (
    <table className="api-table">
      <thead><tr>{head.map((h) => <th key={h}>{h}</th>)}</tr></thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i}>{r.map((c, j) => <td key={j}>{c}</td>)}</tr>
        ))}
      </tbody>
    </table>
  )
}

export default function ApiDocs() {
  return (
    <main className="api-docs">
      <header className="tool-hero">
        <h1>API reference</h1>
        <p className="tool-intro">
          One REST API for every analysis this platform performs. All endpoints
          return JSON, all errors share one envelope, and every long-running
          operation is asynchronous so no request depends on a connection
          staying open.
        </p>
      </header>

      <nav className="api-toc">
        <a href="#getting-started">Getting started</a>
        <a href="#auth">Authentication</a>
        <a href="#async">Async model</a>
        <a href="#errors">Errors</a>
        <a href="#limits">Rate limits</a>
        <a href="#dedup">Caching</a>
        <a href="#screen">Screen (L1)</a>
        <a href="#analyses">Analyses (L2)</a>
        <a href="#tools">Tools</a>
        <a href="#system">System</a>
        <a href="#webhooks">Webhooks</a>
      </nav>

      {/* ---------------------------------------------------------------- */}
      <section id="getting-started">
        <h2>Getting started</h2>
        <p>
          Base URL for every endpoint below. Version is in the path; breaking
          changes ship as a new version and the old one keeps working.
        </p>
        <Code>{`https://api.beat22labs.com/v1`}</Code>
        <p>Submit a track and poll for the result:</p>
        <Code>{`# 1. Submit
curl -X POST https://api.beat22labs.com/v1/analyses \\
  -H "X-API-Key: labs_live_..." \\
  -F "file=@track.wav" \\
  -F "mode=full"

# {"id":"a1b2c3...","status":"queued","poll_url":"/v1/analyses/a1b2c3..."}

# 2. Poll
curl https://api.beat22labs.com/v1/analyses/a1b2c3 \\
  -H "X-API-Key: labs_live_..."`}</Code>
      </section>

      {/* ---------------------------------------------------------------- */}
      <section id="auth">
        <h2>Authentication</h2>
        <p>
          Pass your key in the <code>X-API-Key</code> header. Keys are shown
          once at creation and only a hash is stored, so a lost key must be
          replaced rather than recovered.
        </p>
        <Code>{`X-API-Key: labs_live_xxxxxxxxxxxxxxxxxxxxxxxx`}</Code>
        <p>
          Each key carries scopes. <code>analyze</code> permits submission,
          <code> read</code> permits fetching results, and <code>admin</code>{' '}
          implies both.
        </p>
        <p className="api-note">
          A deployment with no keys configured and{' '}
          <code>LABS_REQUIRE_AUTH=false</code> serves anonymous callers. That is
          intended for local development. Enable auth before exposing the
          service publicly.
        </p>
      </section>

      {/* ---------------------------------------------------------------- */}
      <section id="async">
        <h2>The async model</h2>
        <p>
          A full analysis is tens of seconds of computation. Holding an HTTP
          connection open for that long fails behind most proxies and load
          balancers, so every submission returns immediately with a job id.
        </p>
        <Table
          head={['Status', 'Meaning']}
          rows={[
            ['queued', 'Accepted, waiting for a worker.'],
            ['running', 'In progress. The progress field names the current stage.'],
            ['succeeded', 'Finished. The result field holds the payload.'],
            ['failed', 'Finished with an error. The error field explains why.'],
            ['duplicate', 'An Idempotency-Key matched an earlier submission.'],
          ]}
        />
        <p>
          Tool endpoints additionally accept <code>wait</code>, a number of
          seconds up to 25. If the tool finishes inside that window the result
          is returned inline with status 200; otherwise you get the job id and
          poll as normal. This never turns into a timeout.
        </p>
      </section>

      {/* ---------------------------------------------------------------- */}
      <section id="errors">
        <h2>Errors</h2>
        <p>Every error, from every endpoint, uses one shape.</p>
        <Code>{`{
  "error": {
    "code": "unsupported_media_type",
    "message": "Unsupported file type '.txt'. Allowed: .wav, .mp3, .flac, .m4a, .aac, .ogg, .opus."
  }
}`}</Code>
        <Table
          head={['HTTP', 'Code', 'Meaning']}
          rows={[
            ['400', 'empty_file', 'The uploaded file contained no bytes.'],
            ['401', 'missing_api_key', 'No key supplied and auth is enabled.'],
            ['401', 'invalid_api_key', 'Key not recognised.'],
            ['401', 'revoked_api_key', 'Key exists but has been revoked.'],
            ['403', 'insufficient_scope', 'Key lacks the required scope.'],
            ['404', 'not_found', 'No job or analysis with that id.'],
            ['404', 'unknown_tool', 'No tool with that slug. See GET /v1/tools.'],
            ['413', 'payload_too_large', 'File exceeds the 50 MB limit.'],
            ['415', 'unsupported_media_type', 'File extension not accepted.'],
            ['422', 'invalid_mode', 'mode must be ai, audio or full.'],
            ['422', 'invalid_genre', 'Unknown genre. See GET /v1/genres.'],
            ['422', 'missing_input', 'A two-file tool was sent only one file.'],
            ['429', 'rate_limited', 'Per-minute request limit exceeded.'],
            ['429', 'too_many_inflight', 'Too many concurrent jobs for this key.'],
            ['429', 'quota_exceeded', 'Daily quota for this key is spent.'],
            ['503', 'model_loading', 'Detection model still loading. Use mode=audio or retry.'],
          ]}
        />
      </section>

      {/* ---------------------------------------------------------------- */}
      <section id="limits">
        <h2>Rate limits and quotas</h2>
        <Table
          head={['Limit', 'Default', 'Response when exceeded']}
          rows={[
            ['Requests per minute, per key', '60', '429 rate_limited with Retry-After'],
            ['Concurrent jobs per key', '4', '429 too_many_inflight'],
            ['Daily quota', 'Per key, 0 = unlimited', '429 quota_exceeded'],
            ['Upload size', '50 MB', '413 payload_too_large'],
            ['Audio duration', '900 seconds', '422 with an explanation'],
          ]}
        />
      </section>

      {/* ---------------------------------------------------------------- */}
      <section id="dedup">
        <h2>Caching and deduplication</h2>
        <p>
          Every upload is hashed. Submitting byte-identical audio to the same
          endpoint with the same options returns the stored result immediately
          rather than recomputing it, flagged with <code>cached: true</code>.
        </p>
        <Code>{`# Force a fresh run
-F "no_cache=true"`}</Code>
        <p className="api-note">
          Matching is exact, on the SHA-256 of the bytes. A re-export at a
          different bitrate, a re-encode, or a one-sample trim is a different
          file and will be analysed again. Recognising those as the same
          recording requires acoustic fingerprinting, which this API does not
          currently do.
        </p>
        <p>
          Use <code>Idempotency-Key</code> for safe retries. A repeated key
          returns the original job rather than starting a second one.
        </p>
        <Code>{`Idempotency-Key: your-unique-request-id`}</Code>
      </section>

      {/* ---------------------------------------------------------------- */}
      <section id="screen">
        <h2>Screen — Level 1</h2>
        <p>
          Two small models over two different representations of the audio,
          fused so that agreement raises confidence and disagreement lowers
          it. <strong>Synchronous</strong>: the answer is in the response, so
          this is the endpoint to call from a browser on upload.
        </p>

        <Endpoint method="POST" path="/v1/screen" auth="screen"
                  summary="Return a Level-1 verdict in one round trip, in 1-3 seconds.">
          <Table
            head={['Field', 'Type', 'Default', 'Description']}
            rows={[
              ['file', 'file', 'required', 'Audio file to screen.'],
              ['reference', 'string', '—', 'Your own identifier, echoed back.'],
            ]}
          />
          <Code>{`curl -X POST https://api.beat22labs.com/v1/screen \\
  -H "X-API-Key: labs_live_..." \\
  -F "file=@track.mp3"`}</Code>
          <p>
            Branch on <code>next_step</code>. <code>return</code> means the
            verdict is decisive and the deep model would restate it rather
            than revise it. <code>escalate</code> means Level 1 could not
            settle the track — submit it to <code>/v1/analyses</code>.
          </p>
          <p className="api-note">
            A Level-1 <code>human-made</code> result <strong>always</strong>{' '}
            returns <code>escalate</code>, however confident it looks. Both
            Level-1 models only recognise generators they were trained on, so
            their silence is not evidence: a generator neither has seen is
            indistinguishable from human audio to them. Level 1 never
            publishes an exoneration on its own.
          </p>
        </Endpoint>
      </section>

      {/* ---------------------------------------------------------------- */}
      <section id="analyses">
        <h2>Analyses — Level 2</h2>

        <Endpoint method="POST" path="/v1/analyses" auth="analyze"
                  summary="Submit a track for detection and full analysis.">
          <Table
            head={['Field', 'Type', 'Default', 'Description']}
            rows={[
              ['file', 'file', 'required', 'Audio file to analyse.'],
              ['mode', 'string', 'ai', 'ai, audio or full. audio skips detection entirely.'],
              ['verify', 'string', 'never', 'never, auto or always. Secondary verification policy.'],
              ['genre', 'string', 'null', 'Target genre for the transformation guide.'],
              ['webhook_url', 'string', 'null', 'POSTed on completion, signed.'],
              ['reference', 'string', 'null', 'Your own id, echoed back in the result.'],
              ['fields', 'string', 'null', 'Comma-separated sections to return.'],
              ['no_cache', 'bool', 'false', 'Skip the content-hash cache.'],
            ]}
          />
          <Code>{`curl -X POST https://api.beat22labs.com/v1/analyses \\
  -H "X-API-Key: labs_live_..." \\
  -H "Idempotency-Key: my-request-001" \\
  -F "file=@track.wav" \\
  -F "mode=full" \\
  -F "genre=trap"`}</Code>
        </Endpoint>

        <Endpoint method="GET" path="/v1/analyses/{id}" auth="read"
                  summary="Fetch status, then the completed result.">
          <p>
            While running, returns status and progress. On completion the
            <code> result</code> field holds the full report. After the
            in-memory window expires this falls back to stored records.
          </p>
        </Endpoint>

        <Endpoint method="GET" path="/v1/analyses" auth="read"
                  summary="List stored analyses for your key, newest first.">
          <Table
            head={['Query', 'Type', 'Description']}
            rows={[
              ['limit', 'int', 'Max 200, default 50.'],
              ['skip', 'int', 'Offset for pagination.'],
              ['is_ai', 'bool', 'Filter by detection verdict.'],
              ['genre', 'string', 'Filter by detected primary genre.'],
            ]}
          />
        </Endpoint>
      </section>

      {/* ---------------------------------------------------------------- */}
      <section id="tools">
        <h2>Tools</h2>
        <p>
          Every tool is submitted and polled identically, so integrating once
          gains you each new tool without a client change.
        </p>

        <Endpoint method="GET" path="/v1/tools"
                  summary="List every tool with its inputs, runtime and stated accuracy.">
          <p>
            The <code>accuracy</code> and <code>limitations</code> fields are
            part of the contract. Surface them rather than writing your own,
            so what your users are told matches what the tool actually does.
          </p>
        </Endpoint>

        <Endpoint method="POST" path="/v1/tools/{slug}" auth="analyze"
                  summary="Run one tool.">
          <Table
            head={['Field', 'Type', 'Description']}
            rows={[
              ['file', 'file', 'Primary audio, for single-file tools.'],
              ['reference', 'file', 'Second file for reference-match.'],
              ['beat', 'file', 'Instrumental for beat-vocal-fit.'],
              ['vocal', 'file', 'Vocal for beat-vocal-fit.'],
              ['genre', 'string', 'Target genre, where the tool uses one.'],
              ['wait', 'float', 'Seconds to block, max 25, before returning a job id.'],
              ['no_cache', 'bool', 'Skip the cache and force a fresh run.'],
            ]}
          />
          <Code>{`curl -X POST https://api.beat22labs.com/v1/tools/reference-match \\
  -H "X-API-Key: labs_live_..." \\
  -F "file=@my-mix.wav" \\
  -F "reference=@reference-track.wav" \\
  -F "wait=20"`}</Code>
        </Endpoint>

        <Endpoint method="GET" path="/v1/tools/results/{id}" auth="read"
                  summary="Fetch a tool run's status and result." />

        <h3>Available tools</h3>
        <Table
          head={['Slug', 'Inputs', 'Typical time', 'What it measures']}
          rows={TOOLS.map((t) => [
            <code key={t.slug}>{t.slug}</code>,
            t.inputs.map((i) => i.name).join(', '),
            `${t.typicalSeconds[0]}-${t.typicalSeconds[1]}s`,
            t.tagline,
          ])}
        />
      </section>

      {/* ---------------------------------------------------------------- */}
      <section id="system">
        <h2>System</h2>
        <Endpoint method="GET" path="/v1/health"
                  summary="Service state, model readiness, storage and corpus coverage." />
        <Endpoint method="GET" path="/v1/ready"
                  summary="200 once the service can accept work, 503 while loading." />
        <Endpoint method="GET" path="/v1/genres"
                  summary="Every genre key accepted by the genre parameter." />
        <Endpoint method="GET" path="/v1/usage" auth="read"
                  summary="Your key's usage today, against its quota." />
      </section>

      {/* ---------------------------------------------------------------- */}
      <section id="webhooks">
        <h2>Webhooks</h2>
        <p>
          Pass <code>webhook_url</code> when submitting and the completed result
          is POSTed there. Deliveries are signed, retried with exponential
          backoff, and carry a stable id so you can deduplicate.
        </p>
        <Table
          head={['Header', 'Description']}
          rows={[
            ['X-Webhook-Id', 'Stable per delivery. Use it to deduplicate retries.'],
            ['X-Webhook-Timestamp', 'Unix seconds. Reject anything too old.'],
            ['X-Webhook-Signature', 'HMAC-SHA256 of "{timestamp}.{body}" using your secret.'],
          ]}
        />
        <p>Verify before trusting a delivery:</p>
        <Code>{`import hmac, hashlib

def verify(secret, timestamp, raw_body, signature):
    expected = hmac.new(
        secret.encode(),
        f"{timestamp}.{raw_body}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)`}</Code>
        <p className="api-note">
          Compare with a constant-time function. A plain <code>==</code> leaks
          timing information that can be used to forge a signature.
        </p>
      </section>
    </main>
  )
}