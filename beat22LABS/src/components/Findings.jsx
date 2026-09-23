'use client'

import React from 'react'

const MARK = {
  verdict: 'V', agreement: 'A', consistency: 'C', structure: 'S',
  rhythm: 'R', anomaly: '!', bandwidth: 'B', brightness: 'T',
  flatness: 'F', dynamics: 'D', 'loudness-movement': 'L',
  clipping: '!', key: 'K', texture: 'H', stereo: 'W',
}

const FLAG_LABEL = {
  'synthetic-leaning': 'synthetic-leaning',
  'human-leaning': 'human-leaning',
}

export default function Findings({ title, sub, findings, summary }) {
  if (!findings?.length) return null
  return (
    <div className="card">
      <div className="card-head">
        <div>
          <h3 className="h-section">{title}</h3>
          {sub && <p className="caption" style={{ marginTop: 2 }}>{sub}</p>}
        </div>
        {summary && <span className="caption mono">{summary}</span>}
      </div>
      <div className="card-body">
        {findings.map((f, i) => (
          <div key={i} className={`finding finding--${f.weight}`}>
            <span className="finding-mark" aria-hidden="true">{MARK[f.type] || '·'}</span>
            <div>
              <p className="finding-title">
                {f.title}
                {FLAG_LABEL[f.flag] && (
                  <span className={`flag ${f.flag === 'synthetic-leaning'
                    ? 'flag--synthetic' : 'flag--human'}`}>
                    {FLAG_LABEL[f.flag]}
                  </span>
                )}
              </p>
              <p className="body">{f.detail}</p>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}