'use client'

// Ambient motion behind the landing hero.
//
// Drifting waveform layers, drawn once as SVG paths and animated with CSS
// transforms only. Transforms are composited off the main thread, so this
// stays smooth while the page is doing real work, and it does not repaint on
// scroll. Hidden entirely under prefers-reduced-motion.

import React from 'react'

const LAYERS = [
  { d: 'M0 90 C120 40, 240 140, 360 80 S600 30, 720 90 S960 150, 1080 80 S1320 40, 1440 96',
    cls: 'aw-1' },
  { d: 'M0 110 C140 70, 260 150, 400 100 S640 60, 780 120 S1020 160, 1160 104 S1360 70, 1440 118',
    cls: 'aw-2' },
  { d: 'M0 70 C160 120, 300 30, 440 84 S680 130, 840 62 S1100 20, 1240 88 S1400 120, 1440 74',
    cls: 'aw-3' },
]

export default function AmbientWaves() {
  return (
    <div className="ambient-waves" aria-hidden="true">
      <svg viewBox="0 0 1440 180" preserveAspectRatio="none">
        {LAYERS.map((l) => (
          <g key={l.cls} className={`aw-layer ${l.cls}`}>
            <path d={l.d} />
            {/* A second copy offset by one viewport makes the drift seamless. */}
            <path d={l.d} transform="translate(1440 0)" />
          </g>
        ))}
      </svg>
    </div>
  )
}