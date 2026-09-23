'use client'

import React from 'react'
import { num, pct } from '../lib/format.js'

const AGREEMENT_LABEL = {
  agree: 'Confirmed by deeper verification',
  disagree: 'Deeper verification returned a different result',
  single_provider: 'Deeper verification did not return a usable result',
  no_result: 'Deeper verification did not return a usable result',
}

function VerificationBanner({ detection }) {
  if (!detection) return null
  const { verification, verification_error, consensus } = detection
  if (!verification && !verification_error) return null

  if (verification_error) {
    return (
      <div className="verify-banner is-warn">
        <span className="verify-banner-title">Deeper verification unavailable</span>
        <span className="caption">{verification_error.message}</span>
      </div>
    )
  }

  const disputed = consensus?.verdict === 'disputed'
  const isHuman = verification.prediction !== 'ai_generated'
  return (
    <div className={`verify-banner${disputed ? ' is-warn' : ''}`}>
      <div className="verify-banner-row">
        <span className="verify-banner-title">
          {AGREEMENT_LABEL[consensus?.agreement] || 'Deeper verification result'}
        </span>
        {isHuman ? (
          <span className="pill">Human-made, confirmed by deep analysis</span>
        ) : (
          <span className="pill num">{num(verification.ai_probability, 1)}% AI</span>
        )}
        {!isHuman && verification.likely_source && verification.likely_source !== 'Human' && (
          <span className="pill">likely source: {verification.likely_source}</span>
        )}
      </div>
      {consensus?.detail && <p className="caption">{consensus.detail}</p>}
    </div>
  )
}

export default function Verdict({ report }) {
  const isFake = report.prediction === 'Fake'
  const rel = report.reliability
  const detection = report.detection
  // A Level-1 response carries a verdict and the probability split, but none
  // of the deep evidence below.
  const hasDeepEvidence = Boolean(rel && report.timeline)

  return (
    <div>
      <VerificationBanner detection={detection} />
      <div className="verdict">
        <div className="verdict-main">
          <p className="eyebrow">Verdict</p>
          <div className="verdict-label">
            <span className="verdict-name">
              {isFake ? 'AI-generated' : 'Human-made'}
            </span>
          </div>

          <div className="conf-row">
            <span className="conf-num num">{num(report.confidence, 1)}%</span>
            <span className="caption">confidence</span>
          </div>

          <div className="prob-bar" role="img"
               aria-label={`AI ${pct(report.fake_probability)}, human ${pct(report.real_probability)}`}>
            <div className="prob-seg prob-seg--fake"
                 style={{ width: `${report.fake_probability * 100}%` }} />
            <div className="prob-seg prob-seg--real"
                 style={{ width: `${report.real_probability * 100}%` }} />
          </div>
          <div className="prob-legend">
            <span className="legend-item">
              <span className="swatch swatch--fake" />
              AI-generated <strong className="num">{pct(report.fake_probability, 1)}</strong>
            </span>
            <span className="legend-item">
              <span className="swatch swatch--real" />
              Human-made <strong className="num">{pct(report.real_probability, 1)}</strong>
            </span>
          </div>

          <p className="body" style={{ marginTop: '1.2rem' }}>{report.summary}</p>
        </div>

        {/* Everything in this panel - reliability, the per-window counts,
            the raw logit, coverage - is Level-2 evidence. A Level-1 answer is
            a complete answer and simply does not have it, so the panel is
            omitted rather than rendered against undefined. */}
        {hasDeepEvidence && (
        <div className="verdict-side">
          <div>
            <div className="meter-row" style={{ marginBottom: '0.5rem' }}>
              <span className="eyebrow">Reliability</span>
              <span className="num" style={{ fontSize: '0.8rem', fontWeight: 600 }}>
                {rel.label}
              </span>
            </div>
            <div className="meter">
              <div className="meter-fill" style={{ width: `${rel.score}%` }} />
            </div>
            <ul style={{ margin: '0.7rem 0 0', paddingLeft: '1rem' }}>
              {rel.notes.map((n, i) => (
                <li key={i} className="caption" style={{ marginBottom: '0.2rem' }}>{n}</li>
              ))}
            </ul>
          </div>

          <div>
            <div className="kv"><span className="kv-k">Windows analysed</span>
              <span className="kv-v num">{report.timeline.count}</span></div>
            <div className="kv"><span className="kv-k">Leaning AI</span>
              <span className="kv-v num">
                {report.timeline.fake_segments} / {report.timeline.count}
              </span></div>
            <div className="kv"><span className="kv-k">Raw logit</span>
              <span className="kv-v num mono">
                {report.raw_logit > 0 ? '+' : ''}{num(report.raw_logit, 3)}
              </span></div>
            <div className="kv"><span className="kv-k">Coverage</span>
              <span className="kv-v num">{pct(report.source?.coverage, 0)}</span></div>
          </div>
        </div>
        )}
      </div>
    </div>
  )
}