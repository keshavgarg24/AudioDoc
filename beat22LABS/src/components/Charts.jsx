'use client'

import React, { useState } from 'react'
import { hz, mono, num, pct } from '../lib/format.js'

const W = 760, H = 200, PL = 44, PR = 10, PT = 14, PB = 26
const pw = W - PL - PR, ph = H - PT - PB

function Frame({ title, sub, legend, children, foot, dark }) {
  return (
    <div className={`card spot${dark ? ' card--dark' : ''}`} onMouseMove={(e) => {
      const r = e.currentTarget.getBoundingClientRect()
      e.currentTarget.style.setProperty('--mx', `${e.clientX - r.left}px`)
      e.currentTarget.style.setProperty('--my', `${e.clientY - r.top}px`)
    }}>
      <div className="card-head">
        <div>
          <h3 className="h-section">{title}</h3>
          {sub && <p className="caption" style={{ marginTop: 2 }}>{sub}</p>}
        </div>
        {legend}
      </div>
      <div className="card-body">
        {children}
        {foot}
      </div>
    </div>
  )
}

/* ------------------------------------------------- long-term average spectrum */
export function SpectrumChart({ spectral }) {
  const [hover, setHover] = useState(null)
  const pts = spectral.spectrum
  if (!pts?.length) return null

  const minDb = -90, maxDb = 2
  const x = (i) => PL + (i / (pts.length - 1)) * pw
  const y = (db) => PT + (1 - (Math.max(minDb, db) - minDb) / (maxDb - minDb)) * ph

  const line = pts.map((p, i) => `${i ? 'L' : 'M'}${x(i)},${y(p.db)}`).join(' ')
  const area = `${line} L${x(pts.length - 1)},${PT + ph} L${PL},${PT + ph} Z`

  // Mark the measured ceiling so the brick-wall claim is visible, not asserted.
  const ceilIdx = pts.findIndex((p) => p.hz >= spectral.ceiling_hz)
  const ceilX = ceilIdx > 0 ? x(ceilIdx) : null

  const ticks = [100, 1000, 10000]

  return (
    <Frame title="Frequency response"
           sub="Long-term average spectrum, where the energy actually sits"
           legend={<span className="caption mono">ceiling {hz(spectral.ceiling_hz)}</span>}>
      <div className="chart-wrap" onMouseLeave={() => setHover(null)}>
        <svg className="chart-svg" viewBox={`0 0 ${W} ${H}`} role="img"
             aria-label="Long-term average frequency spectrum">
          {[-80, -60, -40, -20, 0].map((db) => (
            <g key={db}>
              <line className="grid-line" x1={PL} x2={W - PR} y1={y(db)} y2={y(db)} />
              <text className="axis-label" x={PL - 6} y={y(db) + 3} textAnchor="end">{db}</text>
            </g>
          ))}
          <path d={area} fill="rgba(255,255,255,0.09)" />
          <path d={line} fill="none" stroke="var(--white)" strokeWidth="2"
                strokeLinejoin="round" />
          {ceilX && (
            <>
              <line x1={ceilX} x2={ceilX} y1={PT} y2={PT + ph}
                    stroke="var(--n-400)" strokeWidth="1" strokeDasharray="4 3" />
              <text className="axis-label" x={ceilX + 4} y={PT + 10}>ceiling</text>
            </>
          )}
          {ticks.map((t) => {
            const i = pts.findIndex((p) => p.hz >= t)
            return i < 0 ? null : (
              <text key={t} className="axis-label" x={x(i)} y={H - 8} textAnchor="middle">
                {hz(t)}
              </text>
            )
          })}
          {pts.map((p, i) => (
            <rect key={i} x={x(i) - pw / pts.length / 2} y={PT}
                  width={pw / pts.length} height={ph} fill="transparent"
                  onMouseEnter={() => setHover({ i, p })} />
          ))}
          {hover && <circle cx={x(hover.i)} cy={y(hover.p.db)} r="3.5" fill="var(--white)" />}
        </svg>
        {hover && (
          <div className="tip" style={{ left: `${(x(hover.i) / W) * 100}%`,
                                        top: `${(y(hover.p.db) / H) * 100}%` }}>
            <div><span className="tip-val">{hz(hover.p.hz)}</span></div>
            <div><span className="tip-key">level </span>
                 <span className="tip-val">{num(hover.p.db, 1)} dB</span></div>
          </div>
        )}
      </div>
    </Frame>
  )
}

/* ---------------------------------------------------------- band distribution */
export function BandChart({ spectral }) {
  const bands = spectral.bands
  const max = Math.max(...bands.map((b) => b.share)) || 1
  const bh = 26

  return (
    <Frame dark title="Band balance" sub="Share of total energy per mixing band">
      <svg className="chart-svg" viewBox={`0 0 ${W} ${bands.length * bh + 12}`}
           role="img" aria-label="Energy share per frequency band">
        {bands.map((b, i) => {
          const bw = (b.share / max) * (pw - 120)
          return (
            <g key={b.name}>
              <text className="axis-label" x={0} y={i * bh + 18}
                    style={{ fontSize: 11, fill: 'var(--ink-2)' }}>{b.name}</text>
              <rect x={92} y={i * bh + 7} width={Math.max(2, bw)} height={13}
                    rx={3} fill={mono(0.35 + (b.share / max) * 0.65)} />
              <text className="axis-label" x={92 + Math.max(2, bw) + 8} y={i * bh + 18}
                    style={{ fill: 'var(--ink-2)' }}>{pct(b.share, 1)}</text>
              <text className="axis-label" x={W - PR} y={i * bh + 18} textAnchor="end">
                {b.low} to {b.high} Hz
              </text>
            </g>
          )
        })}
      </svg>
    </Frame>
  )
}

/* ------------------------------------------------------------- loudness over time */
export function LoudnessChart({ dynamics }) {
  const s = dynamics.rms_series
  if (!s?.length) return null
  const lo = Math.min(...s) - 2, hi = Math.max(...s) + 2
  const x = (i) => PL + (i / (s.length - 1)) * pw
  const y = (v) => PT + (1 - (v - lo) / (hi - lo)) * ph
  const line = s.map((v, i) => `${i ? 'L' : 'M'}${x(i)},${y(v)}`).join(' ')

  return (
    <Frame title="Loudness contour"
           sub="Short-term RMS across the track, how much the arrangement breathes"
           legend={<span className="caption mono">
             σ {num(dynamics.loudness_std_db, 2)} dB</span>}>
      <svg className="chart-svg" viewBox={`0 0 ${W} ${H}`} role="img"
           aria-label="Short-term loudness over time">
        {[dynamics.rms_percentiles.p95, dynamics.rms_percentiles.p50,
          dynamics.rms_percentiles.p10].map((v, i) => (
          <g key={i}>
            <line className="grid-line" x1={PL} x2={W - PR} y1={y(v)} y2={y(v)} />
            <text className="axis-label" x={PL - 6} y={y(v) + 3} textAnchor="end">
              {num(v, 0)}
            </text>
          </g>
        ))}
        <path d={`${line} L${x(s.length - 1)},${PT + ph} L${PL},${PT + ph} Z`}
              fill="rgba(255,255,255,0.08)" />
        <path d={line} fill="none" stroke="var(--white)" strokeWidth="1.8"
              strokeLinejoin="round" />
        <text className="axis-label" x={PL} y={H - 8}>start</text>
        <text className="axis-label" x={W - PR} y={H - 8} textAnchor="end">end</text>
      </svg>
    </Frame>
  )
}

/* ------------------------------------------------------------------- chroma */
export function ChromaChart({ tonal }) {
  const c = tonal.chroma
  const max = Math.max(...c.map((p) => p.weight)) || 1
  const bw = pw / 12
  const root = (tonal.key || '').split(' ')[0]

  return (
    <Frame dark title="Pitch-class distribution"
           sub={`Chroma energy, estimated key ${tonal.key}`}
           legend={<span className="caption mono">
             clarity {num(tonal.key_clarity, 3)}</span>}>
      <svg className="chart-svg" viewBox={`0 0 ${W} ${H}`} role="img"
           aria-label="Chroma energy per pitch class">
        <line className="grid-line" x1={PL} x2={W - PR} y1={PT + ph} y2={PT + ph} />
        {c.map((p, i) => {
          const h = (p.weight / max) * ph
          const isRoot = p.pitch === root
          return (
            <g key={p.pitch}>
              <rect x={PL + i * bw + 5} y={PT + ph - h} width={bw - 10} height={Math.max(2, h)}
                    rx={3} fill={isRoot ? 'var(--white)' : mono(0.3 + (p.weight / max) * 0.4)} />
              <text className="axis-label" x={PL + i * bw + bw / 2} y={H - 8}
                    textAnchor="middle"
                    style={{ fill: isRoot ? 'var(--white)' : 'var(--n-500)',
                             fontWeight: isRoot ? 700 : 400 }}>{p.pitch}</text>
            </g>
          )
        })}
      </svg>
      <p className="caption" style={{ marginTop: '0.6rem' }}>
        The highlighted bar is the estimated tonic. A flat distribution suggests
        chromatic or modulating material rather than a single stable key.
      </p>
    </Frame>
  )
}

/* --------------------------------------------------------------- novelty curve */
export function NoveltyChart({ structure }) {
  const n = structure.novelty_curve
  if (!n?.length) return null
  const max = Math.max(...n, structure.novelty_cutoff) * 1.15 || 1
  const bw = pw / n.length
  const y = (v) => PT + (1 - v / max) * ph
  const cut = structure.novelty_cutoff

  return (
    <Frame title="Sectional novelty"
           sub="Change between consecutive windows, peaks are section boundaries"
           legend={<span className="caption mono">
             {structure.section_count} sections</span>}>
      <svg className="chart-svg" viewBox={`0 0 ${W} ${H}`} role="img"
           aria-label="Novelty between consecutive windows">
        <line className="mid-line" x1={PL} x2={W - PR} y1={y(cut)} y2={y(cut)} />
        <text className="axis-label" x={W - PR} y={y(cut) - 5} textAnchor="end">
          boundary threshold
        </text>
        {n.map((v, i) => (
          <rect key={i} x={PL + i * bw + 1} y={y(v)} width={Math.max(1, bw - 2)}
                height={PT + ph - y(v)} rx={2}
                fill={v > cut ? 'var(--white)' : 'var(--n-600)'} />
        ))}
        <line className="grid-line" x1={PL} x2={W - PR} y1={PT + ph} y2={PT + ph} />
        <text className="axis-label" x={PL} y={H - 8}>window 1</text>
        <text className="axis-label" x={W - PR} y={H - 8} textAnchor="end">
          window {n.length + 1}
        </text>
      </svg>
      <p className="caption" style={{ marginTop: '0.6rem' }}>
        Bars above the dashed line are scored as section changes. The threshold
        adapts to this track (mean + 2σ of its own novelty), because absolute
        cutoffs are meaningless on LayerNormed embeddings.
      </p>
    </Frame>
  )
}