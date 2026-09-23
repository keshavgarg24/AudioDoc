'use client'

import React from 'react'
import Link from 'next/link'

function Code({ children }) {
  return <code className="docs-inline">{children}</code>
}

function Badge({ post }) {
  return <span className={`docs-badge${post ? ' docs-badge--post' : ''}`}>{post ? 'POST' : 'GET'}</span>
}

const NAV = [
  { href: '#overview', label: 'Overview' },
  { href: '#quickstart', label: 'Quick start' },
  { group: 'Concepts' },
  { href: '#modes', label: 'Analysis modes' },
  { href: '#verification', label: 'Deeper verification' },
  { href: '#async', label: 'Synchronous vs asynchronous' },
  { group: 'Reference' },
  { href: '#health', label: 'Health and readiness' },
  { href: '#predict', label: 'Synchronous analysis' },
  { href: '#v1-submit', label: 'Submit an analysis' },
  { href: '#v1-status', label: 'Fetch a result' },
  { href: '#errors', label: 'Error codes' },
  { href: '#response', label: 'Response shape' },
  { href: '#fields', label: 'Field selection' },
  { group: 'Operations' },
  { href: '#auth', label: 'Authentication' },
  { href: '#limits', label: 'Limits and concurrency' },
  { href: '#webhooks', label: 'Webhooks' },
]

export default function Docs() {
  return (
    <div className="docs-page">
      <div className="docs-layout">
        <nav className="docs-side">
          {NAV.map((item, i) => item.group
            ? <div key={i} className="docs-grp">{item.group}</div>
            : <a key={i} href={item.href}>{item.label}</a>)}
            
          <div className="docs-grp" style={{ marginTop: '2rem' }}>More</div>
          <Link href="/docs/api" style={{ color: 'var(--white)', fontWeight: 500, display: 'block', marginTop: '8px' }}>
            Full API Reference &rarr;
          </Link>
        </nav>

        <main className="docs-main">
          <section id="overview">
            <p className="docs-badge">API reference</p>
            <h1 className="docs-hero-title">Beat22 LABS</h1>
            <p>
              A REST API for AI-generated music detection and full audio analysis.
              Every response is plain JSON. Errors share one envelope. Analyses can
              run synchronously for quick interactive use, or asynchronously with a
              job id and an optional webhook for production pipelines.
            </p>
            <div className="docs-note">
              Base URL in these examples is <Code>http://localhost:8000</Code>.
              Replace it with your deployment's address.
            </div>
          </section>

          <section id="quickstart">
            <h2>Quick start</h2>
            <p>Submit a track and get a verdict back directly:</p>
            <pre>curl -F "file=@track.mp3" "http://localhost:8000/predict?mode=ai"</pre>
            <p>Or queue it asynchronously, which is the recommended path for a production pipeline:</p>
            <pre>{'curl -F "file=@track.mp3" -F "mode=full" -F "reference=beat-1042" \\\n     http://localhost:8000/v1/analyses'}</pre>
            <pre>curl http://localhost:8000/v1/analyses/&lt;job id&gt;</pre>
          </section>

          <section id="modes">
            <h2>Analysis modes</h2>
            <p>Every request selects one <Code>mode</Code>:</p>
            <table>
              <thead><tr><th>Mode</th><th>Runs</th><th>Typical time</th></tr></thead>
              <tbody>
                <tr><td><Code>ai</Code></td><td>Detection verdict, per-window timeline, structural analysis</td><td>1 to 2 min</td></tr>
                <tr><td><Code>audio</Code></td><td>Tempo, key, groove, mastering, stereo, encoding, character. No model weights required.</td><td>40 to 60 sec</td></tr>
                <tr><td><Code>full</Code></td><td>Both of the above in a single report</td><td>2 to 3 min</td></tr>
              </tbody>
            </table>
            <p>Times are measured on CPU. They fall to a few seconds per track on a GPU deployment.</p>
          </section>

          <section id="verification">
            <h2>Deeper verification</h2>
            <p>
              On top of the primary model, a request can opt into a secondary
              verification pass that cross-checks the result and attempts to
              identify the likely generation source. It never runs on its own and
              never replaces the primary verdict; it is combined into a
              {' '}<Code>consensus</Code> block.
            </p>
            <p>Set it with the <Code>verify</Code> parameter:</p>
            <table>
              <thead><tr><th>Value</th><th>Behaviour</th></tr></thead>
              <tbody>
                <tr><td><Code>never</Code> (default)</td><td>Primary model only, fastest path</td></tr>
                <tr><td><Code>auto</Code></td><td>Runs only when the primary result is weak (near the decision boundary, low reliability, or the model's two internal stages disagree)</td></tr>
                <tr><td><Code>always</Code></td><td>Runs on every request</td></tr>
              </tbody>
            </table>
            <p>
              On the synchronous <Code>/predict</Code> endpoint this is a
              boolean: <Code>verify=true</Code> runs it, anything else does not.
            </p>
            <div className="docs-note">
              When the secondary pass runs and responds, its verdict is treated as
              authoritative. Treat a response with <Code>"agreement": "verification_used"</Code>{' '}
              as one where the two passes disagreed and the secondary result won out.
            </div>
          </section>

          <section id="async">
            <h2>Synchronous vs asynchronous</h2>
            <p>
              <Code>POST /predict</Code> blocks until the analysis
              finishes and returns the full report directly. It powers the bundled
              web interface and is fine for interactive use, but a long-running
              synchronous request can be cut by a load balancer or reverse proxy
              with a shorter idle timeout, which is common at 30 to 60 seconds.
            </p>
            <p>
              <Code>POST /v1/analyses</Code> returns immediately with
              a job id. Poll <Code>GET /v1/analyses/{'{id}'}</Code>, or
              supply a <Code>webhook_url</Code> to be notified on
              completion. This is the recommended integration for an upload pipeline.
            </p>
          </section>

          <section id="health">
            <h2>Health and readiness</h2>
            <p><Badge /> <Code>/health</Code></p>
            <p>Liveness only. Always returns 200 while the process is running.</p>
            <p><Badge /> <Code>/ready</Code></p>
            <p>200 once the detection model is loaded, 503 while it is still loading.</p>
            <p><Badge /> <Code>/v1/health</Code></p>
            <p>Model state, verification availability and current queue depth:</p>
            <pre>{`{
  "status": "ok",
  "model": { "ready": true, "device": "cpu", "load_seconds": 9.2, "error": null },
  "verification": { "available": true },
  "queue": { "total": 0, "by_status": {} },
  "modes": ["ai", "audio", "full"],
  "version": "1.0.0"
}`}</pre>
          </section>

          <section id="predict">
            <h2>Synchronous analysis</h2>
            <p><Badge post /> <Code>/predict</Code></p>
            <table>
              <thead><tr><th>Field</th><th>Type</th><th>Default</th><th>Description</th></tr></thead>
              <tbody>
                <tr><td><Code>file</Code></td><td>file</td><td>required</td><td>Audio file, multipart form data</td></tr>
                <tr><td><Code>mode</Code></td><td>string</td><td><Code>ai</Code></td><td><Code>ai</Code>, <Code>audio</Code> or <Code>full</Code></td></tr>
                <tr><td><Code>verify</Code></td><td>bool</td><td><Code>false</Code></td><td>Run deeper verification on top of the primary result</td></tr>
                <tr><td><Code>fields</Code></td><td>string</td><td>all</td><td>Comma-separated list of sections to return (see <a href="#fields">Field selection</a>)</td></tr>
              </tbody>
            </table>
            <pre>curl -F "file=@track.mp3" "http://localhost:8000/predict?mode=full&verify=true"</pre>
            <p>Fetch only the verdict and detection result:</p>
            <pre>curl -F "file=@track.mp3" "http://localhost:8000/predict?mode=ai&fields=verdict,detection"</pre>
          </section>

          <section id="v1-submit">
            <h2>Submit an analysis</h2>
            <p><Badge post /> <Code>/v1/analyses</Code></p>
            <table>
              <thead><tr><th>Field</th><th>Type</th><th>Default</th><th>Description</th></tr></thead>
              <tbody>
                <tr><td><Code>file</Code></td><td>file</td><td>required</td><td>Audio file, multipart form data</td></tr>
                <tr><td><Code>mode</Code></td><td>string</td><td><Code>ai</Code></td><td><Code>ai</Code>, <Code>audio</Code> or <Code>full</Code></td></tr>
                <tr><td><Code>verify</Code></td><td>string</td><td><Code>never</Code></td><td><Code>never</Code>, <Code>auto</Code> or <Code>always</Code></td></tr>
                <tr><td><Code>fields</Code></td><td>string</td><td>all</td><td>Comma-separated sections to return (see <a href="#fields">Field selection</a>)</td></tr>
                <tr><td><Code>webhook_url</Code></td><td>string</td><td>none</td><td>POSTed the full job status on completion</td></tr>
                <tr><td><Code>reference</Code></td><td>string</td><td>none</td><td>Your own id, echoed back unchanged</td></tr>
              </tbody>
            </table>
            <p>Returns <Code>202 Accepted</Code>:</p>
            <pre>{`{
  "id": "d4304b9d40474dbab24a1fb630d1c82c",
  "status": "queued",
  "mode": "full",
  "filename": "track.mp3",
  "poll_url": "/v1/analyses/d4304b9d40474dbab24a1fb630d1c82c",
  "created_at": "2026-08-21T08:00:16Z"
}`}</pre>
          </section>

          <section id="v1-status">
            <h2>Fetch a result</h2>
            <p><Badge /> <Code>/v1/analyses/{'{id}'}</Code></p>
            <p><Code>status</Code> is one of <Code>queued</Code>, <Code>running</Code>, <Code>succeeded</Code>, <Code>failed</Code>. The full report appears under <Code>result</Code> once <Code>succeeded</Code>.</p>
            <pre>{`{
  "id": "d4304b9d...",
  "status": "succeeded",
  "progress": "complete",
  "reference": "beat-1042",
  "queue_seconds": 0.1,
  "duration_seconds": 103.4,
  "result": { "...": "the full analysis report" }
}`}</pre>
            <p><Badge /> <Code>/v1/analyses</Code> returns queue statistics only.</p>
          </section>

          <section id="response">
            <h2>Response shape</h2>
            <p>Which sections are present depends on the mode that ran.</p>
            <table>
              <thead><tr><th>Section</th><th>Present when</th><th>Contains</th></tr></thead>
              <tbody>
                <tr><td><Code>prediction</Code>, <Code>confidence</Code>, <Code>raw_logit</Code></td><td>mode is <Code>ai</Code> or <Code>full</Code></td><td>Primary verdict</td></tr>
                <tr><td><Code>reliability</Code></td><td>same as above</td><td>How much to trust the verdict, independent of confidence</td></tr>
                <tr><td><Code>timeline</Code></td><td>same as above</td><td>Per-window verdicts across the track</td></tr>
                <tr><td><Code>structure</Code></td><td>same as above</td><td>Self-similarity matrix the model consumes internally</td></tr>
                <tr><td><Code>detection</Code></td><td><Code>verify</Code> was requested</td><td>Primary result, verification result, escalation reason and consensus</td></tr>
                <tr><td><Code>musical</Code></td><td>mode is <Code>audio</Code> or <Code>full</Code></td><td>Tempo, key, groove, arrangement, drums</td></tr>
                <tr><td><Code>production</Code></td><td>same as above</td><td>Loudness, stereo field, encoding, sound design</td></tr>
                <tr><td><Code>character</Code></td><td>same as above</td><td>Perceptual radar, mood, vocal presence</td></tr>
                <tr><td><Code>industry</Code></td><td>same as above</td><td>Catalogue-style features and quality-control checks</td></tr>
              </tbody>
            </table>
          </section>

          <section id="fields">
            <h2>Field selection</h2>
            <p>
              By default the API returns the full report. Pass a
              {' '}<Code>fields</Code> parameter with a comma-separated list
              of section names to receive only what you need. This is useful when you
              only want the AI score for an upload pipeline and do not need the full
              musical analysis.
            </p>
            <p>
              <Code>mode</Code>, <Code>source</Code> and
              {' '}<Code>runtime</Code> are always included so the response is
              self-describing.
            </p>
            <table>
              <thead><tr><th>Group</th><th>Keys included</th></tr></thead>
              <tbody>
                <tr><td><Code>verdict</Code></td><td><Code>prediction</Code>, <Code>confidence</Code>, <Code>raw_logit</Code>, <Code>fake_probability</Code>, <Code>real_probability</Code>, <Code>summary</Code></td></tr>
                <tr><td><Code>reliability</Code></td><td><Code>reliability</Code></td></tr>
                <tr><td><Code>timeline</Code></td><td><Code>timeline</Code></td></tr>
                <tr><td><Code>structure</Code></td><td><Code>structure</Code></td></tr>
                <tr><td><Code>findings</Code></td><td><Code>findings</Code>, <Code>signal_findings</Code>, <Code>signal_summary</Code></td></tr>
                <tr><td><Code>detection</Code></td><td><Code>detection</Code> (verification, consensus, escalation)</td></tr>
                <tr><td><Code>musical</Code></td><td><Code>musical</Code>, <Code>rhythm</Code></td></tr>
                <tr><td><Code>production</Code></td><td><Code>production</Code></td></tr>
                <tr><td><Code>character</Code></td><td><Code>character</Code></td></tr>
                <tr><td><Code>industry</Code></td><td><Code>industry</Code></td></tr>
                <tr><td><Code>features</Code></td><td><Code>features</Code></td></tr>
              </tbody>
            </table>
            <p>Example: get only the AI verdict and reliability for an upload gate:</p>
            <pre>{'curl -F "file=@track.mp3" \\\n     "http://localhost:8000/predict?mode=ai&fields=verdict,reliability"'}</pre>
            <p>Response:</p>
            <pre>{`{
  "mode": "ai",
  "source": { "filename": "track.mp3", "duration_seconds": 137.3 },
  "runtime": { "device": "cpu", "elapsed_seconds": 64.2 },
  "prediction": "Fake",
  "confidence": 59.7,
  "fake_probability": 0.597,
  "real_probability": 0.403,
  "raw_logit": 0.393,
  "summary": "...",
  "reliability": { "score": 30.0, "label": "Low", "notes": ["..."] }
}`}</pre>
          </section>

          <section id="errors">
            <h2>Error codes</h2>
            <p>Every error uses one envelope:</p>
            <pre>{'{ "error": { "code": "unsupported_media_type", "message": "..." } }'}</pre>
            <table>
              <thead><tr><th>Status</th><th>Code</th><th>Meaning</th></tr></thead>
              <tbody>
                <tr><td>400</td><td><Code>empty_file</Code></td><td>Uploaded file has no content</td></tr>
                <tr><td>401</td><td><Code>unauthorized</Code></td><td>Missing or invalid API key</td></tr>
                <tr><td>404</td><td><Code>not_found</Code></td><td>Unknown job id, or its result has expired</td></tr>
                <tr><td>413</td><td><Code>payload_too_large</Code></td><td>File exceeds the configured size limit</td></tr>
                <tr><td>415</td><td><Code>unsupported_media_type</Code></td><td>File extension not accepted</td></tr>
                <tr><td>422</td><td><Code>invalid_mode</Code>, <Code>invalid_verify_policy</Code>, <Code>invalid_audio</Code></td><td>Bad request parameters, or the audio could not be analysed</td></tr>
                <tr><td>503</td><td><Code>model_loading</Code></td><td>Detection model not ready yet; <Code>mode=audio</Code> is unaffected</td></tr>
              </tbody>
            </table>
          </section>

          <section id="auth">
            <h2>Authentication</h2>
            <p>
              When the deployment sets an API key, send it as
              {' '}<Code>X-API-Key</Code>. Requests without it receive
              {' '}<Code>401</Code>. When no key is configured, the API is open.
            </p>
            <pre>curl -H "X-API-Key: your-key" -F "file=@track.mp3" http://localhost:8000/predict</pre>
          </section>

          <section id="limits">
            <h2>Limits and concurrency</h2>
            <table>
              <thead><tr><th>Limit</th><th>Default</th></tr></thead>
              <tbody>
                <tr><td>Max upload size</td><td>50 MB</td></tr>
                <tr><td>Max audio duration</td><td>15 minutes</td></tr>
                <tr><td>Accepted formats</td><td>.wav .mp3 .flac .m4a .aac .ogg .opus</td></tr>
                <tr><td>Concurrent analyses</td><td>Configurable per deployment</td></tr>
              </tbody>
            </table>
            <p>
              Every response carries <Code>X-Request-Id</Code>, echoed
              from the request if supplied, useful for correlating a submission with
              server-side logs.
            </p>
          </section>

          <section id="webhooks">
            <h2>Webhooks</h2>
            <p>
              Pass <Code>webhook_url</Code> to
              {' '}<Code>POST /v1/analyses</Code> and the full job status,
              including the result, is POSTed there once the job finishes. If the
              webhook is unreachable nothing is lost: poll
              {' '}<Code>GET /v1/analyses/{'{id}'}</Code> instead, results are
              retained for a limited window after completion.
            </p>
          </section>
        </main>
      </div>
    </div>
  )
}