'use client'

import React, { useState } from 'react'
import { clock, mono, num, pct } from '../lib/format.js'

const W = 760, H = 200, PL = 44, PR = 12, PT = 14, PB = 26
const pw = W - PL - PR, ph = H - PT - PB

export function Panel({ title, sub, note, children, dark }) {
  return (
    <div className={`card${dark ? ' card--dark' : ''}`}>
      <div className="card-head">
        <div>
          <h3 className="h-section">{title}</h3>
          {sub && <p className="caption" style={{ marginTop: 2 }}>{sub}</p>}
        </div>
        {note && <span className="caption mono">{note}</span>}
      </div>
      <div className="card-body">{children}</div>
    </div>
  )
}

/* ------------------------------------------------------- perceptual radar */
export function RadarChart({ radar }) {
  const [hover, setHover] = useState(null)
  if (!radar?.length) return null

  // The viewBox is deliberately wider than the plot circle. Axis labels sit
  // outside the outer ring, and the longest of them ("Groove looseness") is
  // anchored end-aligned on the left side, so it extends further left than
  // its anchor point. Without this side margin it renders past x=0 and
  // spills outside the card.
  const W = 440, H = 300
  const cx = W / 2, cy = H / 2, R = 96
  const n = radar.length
  const pt = (i, r) => {
    const a = (Math.PI * 2 * i) / n - Math.PI / 2
    return [cx + Math.cos(a) * R * r, cy + Math.sin(a) * R * r]
  }
  const poly = radar.map((d, i) => pt(i, d.value).join(',')).join(' ')

  return (
    <Panel title="Character profile"
           sub="Perceptual axes, each derived from a measured value">
      <div className="chart-wrap" onMouseLeave={() => setHover(null)}>
        <svg className="chart-svg" viewBox={`0 0 ${W} ${H}`} role="img"
             aria-label="Perceptual character radar">
          {[0.25, 0.5, 0.75, 1].map((r) => (
            <polygon key={r} className="grid-line" fill="none"
                     points={radar.map((_, i) => pt(i, r).join(',')).join(' ')} />
          ))}
          {radar.map((_, i) => {
            const [x, y] = pt(i, 1)
            return <line key={i} className="grid-line" x1={cx} y1={cy} x2={x} y2={y} />
          })}
          <polygon className="radar-fill" points={poly}
                   fill="rgba(255,255,255,0.16)"
                   stroke="var(--white)" strokeWidth="1.8" strokeLinejoin="round" />
          {radar.map((d, i) => {
            const [x, y] = pt(i, d.value)
            const [lx, ly] = pt(i, 1.22)
            const anchor = lx > cx + 6 ? 'start' : lx < cx - 6 ? 'end' : 'middle'
            return (
              <g key={d.axis}>
                <circle className="radar-dot" cx={x} cy={y} r={hover === i ? 5 : 3}
                        fill="var(--white)" onMouseEnter={() => setHover(i)} />
                <text className="axis-label" x={lx} y={ly} textAnchor={anchor}
                      dominantBaseline="middle">{d.axis}</text>
              </g>
            )
          })}
        </svg>
        {hover !== null && (
          <div className="tip" style={{
            left: `${(pt(hover, radar[hover].value)[0] / W) * 100}%`,
            top: `${(pt(hover, radar[hover].value)[1] / H) * 100}%`,
          }}>
            <div><span className="tip-val">{radar[hover].axis}</span></div>
            <div><span className="tip-key">value </span>
                 <span className="tip-val">{num(radar[hover].value, 2)}</span></div>
            <div><span className="tip-key">{radar[hover].basis}</span></div>
          </div>
        )}
      </div>
    </Panel>
  )
}

/* ------------------------------------------------------------ tempo curve */
export function TempoChart({ rhythm }) {
  const [hover, setHover] = useState(null)
  const curve = rhythm?.bpm_curve
  if (!curve?.length) return null

  const vals = curve.map((d) => d.bpm)
  const lo = Math.min(...vals) - 4, hi = Math.max(...vals) + 4
  const x = (i) => PL + (i / Math.max(curve.length - 1, 1)) * pw
  const y = (v) => PT + (1 - (v - lo) / (hi - lo)) * ph
  const line = curve.map((d, i) => `${i ? 'L' : 'M'}${x(i)},${y(d.bpm)}`).join(' ')

  return (
    <Panel title="Tempo over time"
           sub="Tracked pulse per beat, median smoothed over five beats"
           note={`${rhythm.is_constant_tempo ? 'constant' : 'variable'}, sigma ${num(rhythm.bpm_stability_sigma, 1)}`}>
      <div className="chart-wrap" onMouseLeave={() => setHover(null)}>
        <svg className="chart-svg" viewBox={`0 0 ${W} ${H}`} role="img"
             aria-label="Tempo over time">
          {[lo, (lo + hi) / 2, hi].map((v) => (
            <g key={v}>
              <line className="grid-line" x1={PL} x2={W - PR} y1={y(v)} y2={y(v)} />
              <text className="axis-label" x={PL - 6} y={y(v) + 3} textAnchor="end">
                {Math.round(v)}
              </text>
            </g>
          ))}
          <line className="mid-line" x1={PL} x2={W - PR}
                y1={y(rhythm.bpm_tracked)} y2={y(rhythm.bpm_tracked)} />
          <path d={line} fill="none" stroke="var(--white)" strokeWidth="1.8"
                strokeLinejoin="round" />
          {curve.map((d, i) => (
            <rect key={i} x={x(i) - pw / curve.length / 2} y={PT}
                  width={pw / curve.length} height={ph} fill="transparent"
                  onMouseEnter={() => setHover(i)} />
          ))}
          {hover !== null && (
            <circle cx={x(hover)} cy={y(curve[hover].bpm)} r="4" fill="var(--white)" />
          )}
          <text className="axis-label" x={PL} y={H - 8}>0:00</text>
          <text className="axis-label" x={W - PR} y={H - 8} textAnchor="end">
            {clock(curve[curve.length - 1].time)}
          </text>
        </svg>
        {hover !== null && (
          <div className="tip" style={{ left: `${(x(hover) / W) * 100}%`,
                                        top: `${(y(curve[hover].bpm) / H) * 100}%` }}>
            <div><span className="tip-val">{num(curve[hover].bpm, 1)} BPM</span></div>
            <div><span className="tip-key">at {clock(curve[hover].time)}</span></div>
          </div>
        )}
      </div>
    </Panel>
  )
}

/* --------------------------------------------------- groove deviation plot */
export function GrooveChart({ groove }) {
  const hist = groove?.deviation_histogram
  if (!hist?.length) return null
  const max = Math.max(...hist.map((h) => h.count)) || 1
  const bw = pw / hist.length

  return (
    <Panel dark title="Timing against the grid"
           sub="How far each onset sits from its nearest subdivision"
           note={`${groove.quantization_class}, ${num(groove.mean_abs_deviation_ms, 1)} ms mean`}>
      <svg className="chart-svg" viewBox={`0 0 ${W} ${H}`} role="img"
           aria-label="Distribution of timing deviation from the grid">
        <line className="mid-line" x1={PL + pw / 2} x2={PL + pw / 2} y1={PT} y2={PT + ph} />
        {hist.map((h, i) => {
          const bh = (h.count / max) * ph
          return (
            <rect key={i} x={PL + i * bw + 1} y={PT + ph - bh}
                  width={Math.max(1, bw - 2)} height={Math.max(1, bh)} rx={2}
                  fill={mono(0.4 + (h.count / max) * 0.6)} />
          )
        })}
        <line className="grid-line" x1={PL} x2={W - PR} y1={PT + ph} y2={PT + ph} />
        <text className="axis-label" x={PL} y={H - 8}>early, -40 ms</text>
        <text className="axis-label" x={PL + pw / 2} y={H - 8} textAnchor="middle">on grid</text>
        <text className="axis-label" x={W - PR} y={H - 8} textAnchor="end">late, +40 ms</text>
      </svg>
      <p className="caption" style={{ marginTop: '0.6rem' }}>{groove.quantization_note}</p>
    </Panel>
  )
}

/* ------------------------------------------------------- drum step grid */
export function DrumGrid({ drums }) {
  const grid = drums?.pattern_grid
  if (!grid?.voices) return null
  const names = Object.keys(grid.voices)
  const steps = grid.steps_per_bar

  return (
    <Panel title="Drum pattern"
           sub={`First ${grid.bars} bars, ${steps} steps per bar`}
           note={drums.kick_pattern}>
      <div className="seq">
        {names.map((name) => (
          <div key={name} className="seq-voice">
            <span className="seq-name">{name}</span>
            <div className="seq-bars">
              {grid.voices[name].map((row, b) => (
                <div key={b} className="seq-bar">
                  {row.map((v, i) => (
                    <span key={i}
                          className={`seq-cell${v ? ' is-on' : ''}`
                                     + `${i % 4 === 0 ? ' is-beat' : ''}`} />
                  ))}
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
      <p className="caption" style={{ marginTop: '0.8rem' }}>
        Voices are approximated from band limited onset detection rather than
        source separation, so hits from other instruments in the same range can
        appear. {drums.humanization_note}.
      </p>
    </Panel>
  )
}

/* ------------------------------------------------ arrangement section map */
export function ArrangementChart({ arrangement, duration }) {
  const [hover, setHover] = useState(null)
  const secs = arrangement?.sections
  if (!secs?.length) return null
  const total = duration || secs[secs.length - 1].end || 1
  const maxE = Math.max(...secs.map((s) => s.energy)) || 1

  return (
    <Panel dark title="Arrangement"
           sub="Sections by energy, drawn from timbral segmentation"
           note={`${arrangement.section_count} sections`}>
      <div className="chart-wrap" onMouseLeave={() => setHover(null)}>
        <svg className="chart-svg" viewBox={`0 0 ${W} 150`} role="img"
             aria-label="Arrangement sections over time">
          {secs.map((s, i) => {
            const x0 = (s.start / total) * W
            const w = Math.max(2, ((s.end - s.start) / total) * W)
            const h = Math.max(6, (s.energy / maxE) * 100)
            return (
              <g key={i} onMouseEnter={() => setHover(i)}>
                <rect x={x0 + 1} y={118 - h} width={w - 2} height={h} rx={3}
                      fill={mono(0.3 + (s.energy / maxE) * 0.7)}
                      opacity={hover === null || hover === i ? 1 : 0.45} />
                <rect x={x0 + 1} y={0} width={w - 2} height={130} fill="transparent" />
              </g>
            )
          })}
          <line className="grid-line" x1={0} x2={W} y1={118} y2={118} />
          <text className="axis-label" x={0} y={140}>0:00</text>
          <text className="axis-label" x={W} y={140} textAnchor="end">{clock(total)}</text>
        </svg>
        {hover !== null && (
          <div className="tip" style={{
            left: `${(((secs[hover].start + secs[hover].end) / 2) / total) * 100}%`,
            top: '10%',
          }}>
            <div><span className="tip-val">{secs[hover].label}</span></div>
            <div><span className="tip-key">
              {clock(secs[hover].start)} to {clock(secs[hover].end)}</span></div>
            <div><span className="tip-key">energy </span>
                 <span className="tip-val">{num(secs[hover].energy, 2)}</span></div>
          </div>
        )}
      </div>
      <p className="caption" style={{ marginTop: '0.6rem' }}>
        {arrangement.phrasing_note}
      </p>
    </Panel>
  )
}

/* --------------------------------------------------- platform loudness */
export function LoudnessTargets({ loudness }) {
  const targets = loudness?.platform_targets
  if (!targets?.length) return null
  const lufs = loudness.integrated_lufs
  const span = 10

  return (
    <Panel title="Delivery targets"
           sub="Integrated loudness against each platform's normalisation"
           note={`${num(lufs, 1)} LUFS`}>
      <div className="targets">
        {targets.map((t) => {
          const d = Math.max(-span, Math.min(span, t.delta_lu))
          const centre = 50
          const w = Math.abs(d / span) * 50
          const left = d >= 0 ? centre : centre - w
          const ok = Math.abs(t.delta_lu) <= 1
          return (
            <div key={t.platform} className="target">
              <span className="target-name">{t.platform}</span>
              <span className="target-track">
                <span className="target-centre" />
                <span className={`target-bar${ok ? ' is-ok' : ''}`}
                      style={{ left: `${left}%`, width: `${Math.max(1.5, w)}%` }} />
              </span>
              <span className="target-val num">
                {t.delta_lu > 0 ? '+' : ''}{num(t.delta_lu, 1)} LU
              </span>
              <span className="target-verdict caption">{t.verdict}</span>
            </div>
          )
        })}
      </div>
      <p className="caption" style={{ marginTop: '0.8rem' }}>
        Bars right of centre are louder than the target and will be turned down
        on playback. Bars left of centre are quieter and will be turned up.
      </p>
    </Panel>
  )
}

/* ------------------------------------------------- stereo width per band */
export function StereoBands({ stereo }) {
  const bands = stereo?.band_width
  if (!bands?.length) return null
  const bh = 26

  return (
    <Panel dark title="Stereo width by band"
           sub="Side energy relative to mid, per frequency range"
           note={`correlation ${num(stereo.correlation, 2)}`}>
      <svg className="chart-svg" viewBox={`0 0 ${W} ${bands.length * bh + 12}`}
           role="img" aria-label="Stereo width per frequency band">
        {bands.map((b, i) => {
          const bw = b.width * (pw - 130)
          return (
            <g key={b.name}>
              <text className="axis-label" x={0} y={i * bh + 18}
                    style={{ fontSize: 11, fill: 'var(--ink-2)' }}>{b.name}</text>
              <rect x={92} y={i * bh + 7} width={Math.max(2, bw)} height={13} rx={3}
                    fill={mono(0.35 + b.width * 0.65)} />
              <text className="axis-label" x={92 + Math.max(2, bw) + 8} y={i * bh + 18}
                    style={{ fill: 'var(--ink-2)' }}>{pct(b.width, 1)}</text>
              <text className="axis-label" x={W - PR} y={i * bh + 18} textAnchor="end">
                {b.low} to {b.high} Hz
              </text>
            </g>
          )
        })}
      </svg>
      <p className="caption" style={{ marginTop: '0.7rem' }}>
        {stereo.low_end_mono
          ? 'Low end is effectively mono, which is what a club system and a vinyl cut both need.'
          : 'Low end carries side energy, which is unstable on club systems and in a vinyl cut.'}
      </p>
    </Panel>
  )
}

/* ------------------------------------------------------ chord progression */
export function Progression({ harmony }) {
  const prog = harmony?.progression
  if (!prog?.length) return null
  return (
    <Panel title="Harmony"
           sub={`${harmony.key}, Camelot ${harmony.camelot}`}
           note={`${harmony.chord_count} changes`}>
      <div className="chords">
        {prog.map((c, i) => (
          <span key={i} className="chord">{c}</span>
        ))}
      </div>
      <div style={{ marginTop: '1rem' }}>
        <Row k="Key confidence" v={num(harmony.key_correlation, 3)} />
        <Row k="Key clarity" v={num(harmony.key_clarity, 3)} />
        <Row k="Scale conformance" v={pct(harmony.scale_conformance, 0)} />
        <Row k="Harmonic rhythm" v={`${num(harmony.harmonic_rhythm_per_bar, 2)} per bar`} />
        <Row k="Harmonic complexity" v={num(harmony.harmonic_complexity, 2)} />
      </div>
    </Panel>
  )
}

function Row({ k, v }) {
  return (
    <div className="kv"><span className="kv-k">{k}</span>
      <span className="kv-v num">{v}</span></div>
  )
}