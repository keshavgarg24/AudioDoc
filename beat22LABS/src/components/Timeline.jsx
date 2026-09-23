'use client'

import React, { useState } from 'react'
import { clock, num } from '../lib/format.js'

/* Per-window AI probability over time, diverging about 0.5. No hue: bars above
   the midline are solid white (AI-leaning), bars below are hatched grey
   (human-leaning). Direction + fill texture carry the distinction, so it
   survives greyscale, print and any colour-vision deficiency. */
export default function Timeline({ timeline }) {
  const [hover, setHover] = useState(null)
  const [showTable, setShowTable] = useState(false)

  const segs = timeline.segments
  const W = 900, H = 220, PAD_L = 34, PAD_R = 8, PAD_T = 12, PAD_B = 26
  const plotW = W - PAD_L - PAD_R
  const plotH = H - PAD_T - PAD_B
  const bw = plotW / segs.length
  const y = (p) => PAD_T + (1 - p) * plotH

  return (
    <div className="card">
      <div className="card-head">
        <div>
          <h3 className="h-section">Per-window verdict</h3>
          <p className="caption" style={{ marginTop: 2 }}>
            Each 10-second window scored on its own
          </p>
        </div>
        <div className="prob-legend" style={{ justifyContent: 'flex-end' }}>
          <span className="legend-item">
            <span className="swatch swatch--fake" /> above midline = AI
          </span>
          <span className="legend-item">
            <span className="swatch swatch--real" /> below = human
          </span>
        </div>
      </div>

      <div className="card-body">
        <div className="chart-wrap" onMouseLeave={() => setHover(null)}>
          <svg className="chart-svg" viewBox={`0 0 ${W} ${H}`}
               role="img" aria-label="AI probability per window over time">
            <defs>
              <pattern id="hatch" width="5" height="5" patternUnits="userSpaceOnUse"
                       patternTransform="rotate(135)">
                <rect width="5" height="5" fill="var(--n-600)" />
                {/* var(), not a hardcoded rgba: keeps the stripe visible
                    against its own base under the print colour remap. */}
                <line x1="0" y1="0" x2="0" y2="5" stroke="var(--white)"
                      strokeOpacity="0.45" strokeWidth="2" />
              </pattern>
            </defs>
            {[0, 0.25, 0.5, 0.75, 1].map((t) => (
              <g key={t}>
                <line className={t === 0.5 ? 'mid-line' : 'grid-line'}
                      x1={PAD_L} x2={W - PAD_R} y1={y(t)} y2={y(t)} />
                <text className="axis-label" x={PAD_L - 7} y={y(t) + 3} textAnchor="end">
                  {t.toFixed(2)}
                </text>
              </g>
            ))}

            {segs.map((s, i) => {
              const p = s.fake_probability
              const top = Math.min(y(p), y(0.5))
              const h = Math.max(1.5, Math.abs(y(p) - y(0.5)))
              return (
                <rect key={i}
                      x={PAD_L + i * bw + 0.75}
                      y={top}
                      width={Math.max(1, bw - 1.5)}
                      height={h}
                      rx={Math.min(3, bw / 3)}
                      fill={p > 0.5 ? 'var(--white)' : 'url(#hatch)'}
                      opacity={hover === null || hover === i ? 1 : 0.4}
                      style={{ transition: 'opacity .15s' }} />
              )
            })}

            {/* Wide invisible hit targets - the bars themselves are too thin. */}
            {segs.map((s, i) => (
              <rect key={`h${i}`} x={PAD_L + i * bw} y={PAD_T}
                    width={bw} height={plotH} fill="transparent"
                    onMouseEnter={() => setHover(i)} />
            ))}

            <text className="axis-label" x={PAD_L} y={H - 8}>0:00</text>
            <text className="axis-label" x={W - PAD_R} y={H - 8} textAnchor="end">
              {clock(segs[segs.length - 1]?.end)}
            </text>
          </svg>

          {hover !== null && (
            <div className="tip" style={{
              left: `${((PAD_L + hover * bw + bw / 2) / W) * 100}%`,
              top: `${(y(segs[hover].fake_probability) / H) * 100}%`,
            }}>
              <div><span className="tip-key">Window {hover + 1}</span></div>
              <div><span className="tip-key">Time </span>
                   <span className="tip-val">{clock(segs[hover].start)} to {clock(segs[hover].end)}</span></div>
              <div><span className="tip-key">AI prob </span>
                   <span className="tip-val">{num(segs[hover].fake_probability, 4)}</span></div>
              <div><span className="tip-key">Leaning </span>
                   <span className="tip-val">{segs[hover].leaning}</span></div>
            </div>
          )}
        </div>

        <div className="table-toggle">
          <button className="btn btn--ghost btn--sm"
                  aria-expanded={showTable}
                  onClick={() => setShowTable((v) => !v)}>
            {showTable ? 'Hide' : 'Show'} data table
          </button>
        </div>
        {showTable && (
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr><th>Window</th><th>Start</th><th>End</th>
                    <th>AI probability</th><th>Leaning</th></tr>
              </thead>
              <tbody>
                {segs.map((s, i) => (
                  <tr key={i}>
                    <td>{i + 1}</td><td className="num">{clock(s.start)}</td>
                    <td className="num">{clock(s.end)}</td>
                    <td className="num">{num(s.fake_probability, 4)}</td>
                    <td>{s.leaning}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}