'use client'

import React from 'react'

// What the user is choosing between, in their language.
//
// Deliberately says nothing about how any of it works - no model names, no
// architecture, no talk of stages or tiers. What someone needs in order to
// choose is how long it takes, what they get, and how far they can lean on
// it. `quick` is first and is the default: the deeper pass costs real time,
// so it should be something a person opts into rather than something that
// happens to them.
export const MODES = [
  {
    id: 'screen',
    label: 'Quick check',
    time: 'a few seconds',
    desc: 'A fast first read. Settles the obvious cases immediately, and tells '
      + 'you when a track needs a closer look - which you can start from the '
      + 'result, without uploading again.',
  },
  {
    id: 'ai',
    label: 'Full check',
    time: 'about a minute',
    desc: 'The quick check, and then a much closer pass over the whole track '
      + 'whenever the first read is not conclusive. This is the answer to act on.',
  },
  {
    id: 'audio',
    label: 'Audio analysis',
    time: 'under a minute',
    desc: 'No verdict on origin. Tempo, key, groove, loudness, stereo field '
      + 'and whether the master is ready to release.',
  },
  {
    id: 'full',
    label: 'Complete report',
    time: '2 to 3 min',
    desc: 'Everything, in one document: the full origin check alongside the '
      + 'complete production breakdown.',
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