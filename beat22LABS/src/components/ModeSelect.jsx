'use client'

import React from 'react'

export const MODES = [
  {
    id: 'ai',
    label: 'AI detection',
    time: '1 to 2 min',
    desc: 'A verdict on whether the track was AI generated, with a timeline across it.',
  },
  {
    id: 'audio',
    label: 'Audio analysis',
    time: '40 to 60 sec',
    desc: 'Tempo, key, groove, mastering, stereo field and delivery readiness.',
  },
  {
    id: 'full',
    label: 'Complete report',
    time: '2 to 3 min',
    desc: 'Everything above in a single case file, detection alongside production.',
  },
]

export default function ModeSelect({ value, onChange, disabled }) {
  return (
    <div className="modes" role="radiogroup" aria-label="Analysis type">
      {MODES.map((m) => {
        const active = value === m.id
        return (
          <button
            key={m.id}
            type="button"
            role="radio"
            aria-checked={active}
            disabled={disabled}
            className={`mode${active ? ' is-active' : ''}`}
            onClick={() => onChange(m.id)}
          >
            <span className="mode-head">
              <span className="mode-dot" aria-hidden="true" />
              <span className="mode-label">{m.label}</span>
              <span className="mode-time mono">{m.time}</span>
            </span>
            <span className="mode-desc">{m.desc}</span>
          </button>
        )
      })}
    </div>
  )
}