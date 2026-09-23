'use client'

import React, { useState } from 'react'
import { clock, mono, num } from '../lib/format.js'

export default function Heatmap({ structure, segmentSeconds }) {
  const [hover, setHover] = useState(null)
  const m = structure.matrix
  const n = m.length
  const SIZE = 460
  const cell = SIZE / n

  // Stretch the ramp across the track's actual range - the raw values sit in a
  // narrow high band, so a 0-1 mapping would render as one flat colour.
  const lo = structure.min_similarity
  const hi = structure.max_similarity
  const span = Math.max(1e-6, hi - lo)
  const norm = (v) => (v - lo) / span

  return (
    <div className="card card--dark">
      <div className="card-head">
        <div>
          <h3 className="h-section">Structural self-similarity</h3>
          <p className="caption" style={{ marginTop: 2 }}>
            Every window compared against every other
          </p>
        </div>
        <div className="scale-legend">
          <span className="num">{num(lo, 2)}</span>
          <span className="scale-bar" style={{
            background: `linear-gradient(90deg, ${mono(0)}, ${mono(0.5)}, ${mono(1)})`,
          }} />
          <span className="num">{num(hi, 2)}</span>
        </div>
      </div>

      <div className="card-body">
        <div className="chart-wrap" style={{ maxWidth: SIZE, margin: '0 auto' }}
             onMouseLeave={() => setHover(null)}>
          <svg className="chart-svg" viewBox={`0 0 ${SIZE} ${SIZE}`}
               role="img"
               aria-label={`Self-similarity matrix across ${n} windows`}>
            {m.map((row, i) =>
              row.map((v, j) => (
                <rect key={`${i}-${j}`}
                      x={j * cell} y={i * cell}
                      width={Math.ceil(cell)} height={Math.ceil(cell)}
                      fill={mono(norm(v))}
                      onMouseEnter={() => setHover({ i, j, v })} />
              ))
            )}
            {hover && (
              <>
                <rect x={hover.j * cell} y={0} width={cell} height={SIZE}
                      fill="rgba(255,255,255,0.16)" pointerEvents="none" />
                <rect x={0} y={hover.i * cell} width={SIZE} height={cell}
                      fill="rgba(255,255,255,0.16)" pointerEvents="none" />
              </>
            )}
          </svg>

          {hover && (
            <div className="tip" style={{
              left: `${((hover.j * cell + cell / 2) / SIZE) * 100}%`,
              top: `${((hover.i * cell) / SIZE) * 100}%`,
            }}>
              <div><span className="tip-key">Window </span>
                   <span className="tip-val">{hover.i + 1} vs {hover.j + 1}</span></div>
              <div><span className="tip-key">
                     {clock(hover.i * segmentSeconds)} vs {clock(hover.j * segmentSeconds)}
                   </span></div>
              <div><span className="tip-key">Similarity </span>
                   <span className="tip-val">{num(hover.v, 3)}</span></div>
            </div>
          )}
        </div>

        <div className="grid grid--4" style={{ marginTop: '1.2rem' }}>
          <Cell label="Homogeneity" value={num(structure.homogeneity, 3)}
                note="mean similarity" />
          <Cell label="Contrast" value={num(structure.structural_contrast, 3)}
                note="sd, higher = distinct sections" />
          <Cell label="Sections" value={structure.section_count}
                note="from novelty peaks" />
          <Cell label="Range" value={`${num(lo, 2)} to ${num(hi, 2)}`}
                note="min to max pair" />
        </div>
      </div>
    </div>
  )
}

function Cell({ label, value, note }) {
  return (
    <div>
      <p className="eyebrow">{label}</p>
      <p className="stat-value num" style={{ fontSize: '1.15rem' }}>{value}</p>
      <p className="caption">{note}</p>
    </div>
  )
}