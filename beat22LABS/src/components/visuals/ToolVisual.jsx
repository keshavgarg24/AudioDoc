'use client'

// One animated illustration per tool, drawn from what that tool actually
// measures: a loudness meter for Master Check, a beat grid for Tempo Lab, a
// Camelot wheel for Key Lab, and so on. A generic stock graphic would tell a
// visitor nothing about which page they are on.
//
// All motion is SVG/CSS with no JavaScript loop, so these cost nothing on the
// main thread, and every animation is disabled under prefers-reduced-motion
// (see the media query in styles.css).

import React from 'react'

const FRAME = 'tool-visual'

/* ------------------------------------------------------- Master Check ---- */
// A loudness meter: bars breathing under a fixed true-peak ceiling.
function MasterCheckVisual() {
  const bars = [38, 62, 48, 74, 55, 82, 60, 45, 68, 52, 76, 41]
  return (
    <svg className={FRAME} viewBox="0 0 320 180" role="img"
         aria-label="Animated loudness meter">
      <line x1="16" y1="42" x2="304" y2="42" className="tv-ceiling" />
      <text x="16" y="34" className="tv-label">TRUE PEAK CEILING</text>

      {bars.map((h, i) => (
        <rect key={i} className="tv-bar" x={20 + i * 24} width="14"
              rx="2" y={150 - h} height={h}
              style={{ '--h': `${h}px`, animationDelay: `${i * 0.11}s` }} />
      ))}

      <line x1="16" y1="150" x2="304" y2="150" className="tv-axis" />
      <g className="tv-needle-group">
        <line x1="0" y1="46" x2="0" y2="150" className="tv-needle" />
        <circle cx="0" cy="46" r="3.5" className="tv-needle-dot" />
      </g>
      <text x="16" y="170" className="tv-label">INTEGRATED LUFS</text>
    </svg>
  )
}

/* ---------------------------------------------------------- Tempo Lab ---- */
// A beat grid with a playhead sweeping across it, downbeats accented.
function TempoLabVisual() {
  const beats = Array.from({ length: 17 }, (_, i) => 20 + i * 17.5)
  return (
    <svg className={FRAME} viewBox="0 0 320 180" role="img"
         aria-label="Animated beat grid">
      <line x1="20" y1="118" x2="300" y2="118" className="tv-axis" />

      {beats.map((x, i) => {
        const down = i % 4 === 0
        return (
          <g key={i}>
            <line x1={x} y1={down ? 66 : 88} x2={x} y2="118"
                  className={down ? 'tv-beat tv-beat--down' : 'tv-beat'} />
            <circle cx={x} cy={down ? 66 : 88} r={down ? 4 : 2.5}
                    className="tv-beat-dot"
                    style={{ animationDelay: `${(i % 8) * 0.25}s` }} />
          </g>
        )
      })}

      <g className="tv-playhead-group">
        <line x1="0" y1="52" x2="0" y2="132" className="tv-playhead" />
      </g>

      <text x="20" y="46" className="tv-label">BEAT GRID</text>
      <text x="20" y="152" className="tv-label">DOWNBEAT EVERY 4</text>
      <g className="tv-pulse-group">
        <circle cx="282" cy="46" r="7" className="tv-pulse" />
      </g>
    </svg>
  )
}

/* ------------------------------------------------------------ Key Lab ---- */
// The Camelot wheel, rotating slowly with one segment lit.
function KeyLabVisual() {
  const segments = Array.from({ length: 12 }, (_, i) => i)
  const R_OUT = 62
  const R_IN = 38
  const cx = 160
  const cy = 90

  const wedge = (i, rIn, rOut) => {
    const a0 = (i * 30 - 90 - 15) * (Math.PI / 180)
    const a1 = ((i + 1) * 30 - 90 - 15) * (Math.PI / 180)
    const p = (r, a) => `${cx + r * Math.cos(a)} ${cy + r * Math.sin(a)}`
    return `M ${p(rIn, a0)} L ${p(rOut, a0)} A ${rOut} ${rOut} 0 0 1 ${p(rOut, a1)} L ${p(rIn, a1)} A ${rIn} ${rIn} 0 0 0 ${p(rIn, a0)} Z`
  }

  return (
    <svg className={FRAME} viewBox="0 0 320 180" role="img"
         aria-label="Animated Camelot wheel">
      <g className="tv-wheel">
        {segments.map((i) => (
          <path key={`o${i}`} d={wedge(i, R_IN + 3, R_OUT)}
                className="tv-wedge"
                style={{ animationDelay: `${i * 0.4}s` }} />
        ))}
        {segments.map((i) => (
          <path key={`i${i}`} d={wedge(i, 20, R_IN)}
                className="tv-wedge tv-wedge--inner"
                style={{ animationDelay: `${i * 0.4 + 0.2}s` }} />
        ))}
      </g>
      <circle cx={cx} cy={cy} r="15" className="tv-wheel-hub" />
      <text x={cx} y={cy + 4} className="tv-hub-text">8A</text>
      <text x="20" y="24" className="tv-label">CAMELOT WHEEL</text>
      <text x="20" y="168" className="tv-label">HARMONIC NEIGHBOURS</text>
    </svg>
  )
}

/* --------------------------------------------------- Reference Match ---- */
// Two spectra: yours morphing toward the reference curve.
function ReferenceMatchVisual() {
  const ref = 'M20 118 C60 74, 84 62, 110 70 S162 96, 196 78 S256 52, 300 66'
  const you = 'M20 130 C60 116, 84 96, 110 104 S162 70, 196 112 S256 96, 300 92'
  return (
    <svg className={FRAME} viewBox="0 0 320 180" role="img"
         aria-label="Two spectra being compared">
      <defs>
        <linearGradient id="tvRefFill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="currentColor" stopOpacity="0.16" />
          <stop offset="100%" stopColor="currentColor" stopOpacity="0" />
        </linearGradient>
      </defs>

      {[0, 1, 2, 3, 4, 5, 6].map((i) => (
        <line key={i} x1={20 + i * 47} y1="46" x2={20 + i * 47} y2="140"
              className="tv-grid" />
      ))}

      <path d={`${ref} L300 140 L20 140 Z`} fill="url(#tvRefFill)"
            className="tv-ref-fill" />
      <path d={ref} className="tv-curve tv-curve--ref" />
      <path d={you} className="tv-curve tv-curve--you">
        <animate attributeName="d" dur="5s" repeatCount="indefinite"
                 values={`${you};${ref};${you}`}
                 calcMode="spline"
                 keySplines="0.4 0 0.2 1;0.4 0 0.2 1" keyTimes="0;0.5;1" />
      </path>

      <line x1="20" y1="140" x2="300" y2="140" className="tv-axis" />
      <text x="20" y="34" className="tv-label">REFERENCE</text>
      <text x="20" y="166" className="tv-label">YOUR MIX</text>
      <circle cx="96" cy="30" r="3" className="tv-key tv-key--ref" />
      <circle cx="96" cy="162" r="3" className="tv-key tv-key--you" />
    </svg>
  )
}

/* --------------------------------------------------------- Vocal Lab ---- */
// A pitch contour with vibrato, tracked against the semitone grid.
function VocalLabVisual() {
  const lines = [58, 76, 94, 112, 130]
  return (
    <svg className={FRAME} viewBox="0 0 320 180" role="img"
         aria-label="Animated pitch contour">
      {lines.map((y, i) => (
        <line key={i} x1="20" y1={y} x2="300" y2={y} className="tv-grid" />
      ))}

      <path className="tv-pitch"
            d="M20 112 C48 112, 56 94, 78 94 S104 76, 128 76
               S150 94, 172 94 S196 58, 224 58 S252 76, 276 76 L300 76" />

      <g className="tv-vibrato">
        <path d="M224 58 q6 -7 12 0 t12 0 t12 0" className="tv-vibrato-wave" />
      </g>

      <g className="tv-head-group">
        <circle cx="0" cy="0" r="4.5" className="tv-pitch-head" />
      </g>

      <text x="20" y="40" className="tv-label">PITCH IN CENTS</text>
      <text x="20" y="164" className="tv-label">SEMITONE GRID</text>
    </svg>
  )
}

/* ---------------------------------------------------- Beat + Vocal Fit --- */
// Two spectra overlapping, with the contested band flashing.
function BeatVocalFitVisual() {
  const beat = [70, 84, 66, 52, 40, 30, 22]
  const vocal = [10, 18, 44, 72, 64, 38, 20]
  return (
    <svg className={FRAME} viewBox="0 0 320 180" role="img"
         aria-label="Frequency masking between two sources">
      <rect x="126" y="40" width="84" height="102" rx="4"
            className="tv-clash-zone" />
      <text x="168" y="34" className="tv-label tv-label--center">
        INTELLIGIBILITY
      </text>

      {beat.map((h, i) => (
        <rect key={`b${i}`} x={26 + i * 42} width="17" rx="2"
              y={142 - h} height={h} className="tv-stack tv-stack--beat"
              style={{ '--h': `${h}px`, animationDelay: `${i * 0.14}s` }} />
      ))}
      {vocal.map((h, i) => (
        <rect key={`v${i}`} x={45 + i * 42} width="17" rx="2"
              y={142 - h} height={h} className="tv-stack tv-stack--vocal"
              style={{ '--h': `${h}px`, animationDelay: `${i * 0.14 + 0.07}s` }} />
      ))}

      <line x1="20" y1="142" x2="300" y2="142" className="tv-axis" />
      <text x="20" y="164" className="tv-label">BEAT</text>
      <text x="262" y="164" className="tv-label">VOCAL</text>
      <circle cx="46" cy="160" r="3" className="tv-key tv-key--ref" />
      <circle cx="290" cy="160" r="3" className="tv-key tv-key--you" />
    </svg>
  )
}

/* ------------------------------------------------------------ Hit Lab ---- */
// A distribution with a marker sliding to its percentile position.

const VISUALS = {
  'master-check': MasterCheckVisual,
  'tempo-lab': TempoLabVisual,
  'key-lab': KeyLabVisual,
  'reference-match': ReferenceMatchVisual,
  'vocal-lab': VocalLabVisual,
  'beat-vocal-fit': BeatVocalFitVisual,
}

export default function ToolVisual({ slug, animating = false }) {
  const V = VISUALS[slug]
  if (!V) return null
  return (
    <div className="tool-visual-wrap" data-motion="hover"
         data-animating={animating ? 'true' : 'false'} aria-hidden="false">
      <V />
    </div>
  )
}