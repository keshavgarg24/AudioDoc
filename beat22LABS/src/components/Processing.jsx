'use client'

import React, { useEffect, useMemo, useRef, useState } from 'react'
import { clock } from '../lib/format.js'

/* The step list, and which of the service's own stages each step belongs to.

   The service reports four stages and no more: `screening` while the quick
   check runs, `analysing` for the whole deep pass, then `storing` and
   `finalising`. `analysing` alone covers forty to ninety seconds, so showing
   it as one step left the longest part of the wait looking frozen.

   So each step declares the stage it belongs to, and the display advances
   through the steps of the *current* stage on a timer while refusing to
   leave that stage until the service actually moves on. The effect is a
   progression that reads as fine-grained but can never claim to have
   finished work that is still running - which is what the old purely
   time-paced list did, sometimes completing every step a full minute before
   the answer arrived.

   `secs` is only the dwell time on a step within its stage, never a promise
   about the whole run. */
const STAGE_ORDER = ['queued', 'starting', 'screening', 'analysing', 'storing', 'finalising', 'complete']
const stageRank = (s) => {
  const i = STAGE_ORDER.indexOf(s)
  return i === -1 ? 0 : i
}

const DECODE = { name: 'Cracking the seal', tech: 'decode to 24 kHz mono', secs: 4, viz: 'bars', stage: 'screening' }
const BEATS = { name: 'Finding the pulse', tech: 'beat and downbeat tracking', secs: 10, viz: 'pulse', stage: 'analysing' }
const SLICE = { name: 'Slicing the evidence', tech: 'beat aligned windows', secs: 6, viz: 'cells', stage: 'analysing' }
// The deep pass, broken out. One 46-second step here read as a hang.
const EMBED = { name: 'Listening window by window', tech: 'across the whole track', secs: 14, viz: 'scan', stage: 'analysing' }
const SCORE = { name: 'Scoring each window', tech: 'per-window verdicts', secs: 14, viz: 'cells', stage: 'analysing' }
const SEQUENCE = { name: 'Reading the sequence', tech: 'how the windows relate', secs: 12, viz: 'merge', stage: 'analysing' }
const STAGE2 = { name: 'Weighing the evidence', tech: 'one verdict from many windows', secs: 10, viz: 'merge', stage: 'analysing' }
const SPECTRAL = { name: 'Mapping the spectrum', tech: 'band and ceiling analysis', secs: 9, viz: 'spectrum', stage: 'analysing' }
const GROOVE = { name: 'Feeling the groove', tech: 'grid, swing and drum voices', secs: 9, viz: 'grid', stage: 'analysing' }
const HARMONY = { name: 'Naming the key', tech: 'chroma and chord templates', secs: 8, viz: 'chroma', stage: 'analysing' }
const MASTER = { name: 'Metering the master', tech: 'LUFS, true peak and stereo', secs: 8, viz: 'meter', stage: 'analysing' }
const VERIFY = { name: 'Cross-checking the result', tech: 'a second opinion', secs: 14, viz: 'verify', stage: 'analysing' }
const COMPILE = { name: 'Building the case file', tech: 'assembling the report', secs: 4, viz: 'compile', stage: 'storing' }

/* Level 1 only: measured at 1.07-3.20 s end to end, so this list is paced in
   seconds rather than tens of them. Selected the moment the service says the
   quick check settled the track - see `phases` below. */
const L1_DECODE = { name: 'Cracking the seal', tech: 'decode to 24 kHz mono', secs: 1, viz: 'bars', stage: 'screening' }
const L1_MODELS = { name: 'Reading the fingerprints', tech: 'two listens, two ways', secs: 2, viz: 'scan', stage: 'screening' }
const L1_FUSE = { name: 'Fusing the opinions', tech: 'agreement raises confidence', secs: 1, viz: 'merge', stage: 'screening' }

export const PIPELINES = {
  screen: [L1_DECODE, L1_MODELS, L1_FUSE],
  ai: [DECODE, BEATS, SLICE, EMBED, SCORE, SEQUENCE, STAGE2, COMPILE],
  audio: [DECODE, BEATS, SPECTRAL, GROOVE, HARMONY, MASTER, COMPILE],
  full: [DECODE, BEATS, SLICE, EMBED, SCORE, SEQUENCE, STAGE2,
         SPECTRAL, GROOVE, HARMONY, MASTER, COMPILE],
}

/* Which of the two detection levels is running, and what Level 1 concluded.
   Level 1 answers in a couple of seconds and Level 2 takes up to ninety, so
   without this the user watches a spinner with no idea that a verdict already
   exists and is being checked. */
function LevelStrip({ stage, mode }) {
  if (!stage || mode === 'audio') return null

  const one = stage.stage === 1
  const done = stage.status === 'done'
  const skipped = stage.status === 'skipped'
  const verdict = stage.result?.assessment?.verdict || stage.result?.verdict

  return (
    <div className="proc-levels">
      <div className={`proc-level${one && !done ? ' is-active' : ''}${done || stage.stage === 2 ? ' is-done' : ''}${skipped ? ' is-skipped' : ''}`}>
        <span className="proc-level-n mono">L1</span>
        <span className="proc-level-name">Screen</span>
        <span className="caption">
          {skipped ? 'not available on this deployment'
            : done ? `${verdict || 'answered'}${stage.escalating ? ' — not decisive' : ' — decisive'}`
              : 'two models, two representations'}
        </span>
      </div>
      <div className={`proc-level${stage.stage === 2 && stage.status === 'running' ? ' is-active' : ''}${stage.status === 'unavailable' ? ' is-skipped' : ''}`}>
        <span className="proc-level-n mono">L2</span>
        <span className="proc-level-name">Deep analysis</span>
        <span className="caption">
          {stage.status === 'unavailable' ? 'not available to this key'
            : stage.stage === 2 ? 'transformer over the full track'
              : done && !stage.escalating ? 'not needed'
                : 'waiting'}
        </span>
      </div>
    </div>
  )
}

export default function Processing({
  filename, mode, verify, stage, progress, onCancel, onHide,
}) {
  // Two ways the run ends at Level 1, and both collapse the list: the quick
  // check settled the track, or the deeper pass is not available to this key.
  // `skipped` is deliberately not here - that means Level 1 was unavailable
  // and the deep pass is about to run, which is the long list.
  const stoppedAtLevel1 = Boolean(
    stage && (
      (stage.stage === 1 && stage.status === 'done' && !stage.escalating)
      || stage.status === 'unavailable'
    ),
  )

  const phases = useMemo(() => {
    if (stoppedAtLevel1) return PIPELINES.screen
    const base = PIPELINES[mode] || PIPELINES.ai
    return verify && mode !== 'audio'
      ? [...base.slice(0, -1), VERIFY, base[base.length - 1]]
      : base
  }, [mode, verify, stoppedAtLevel1])

  const [elapsed, setElapsed] = useState(0)
  const started = useRef(Date.now())

  useEffect(() => {
    started.current = Date.now()
    setElapsed(0)
  }, [mode, verify])

  useEffect(() => {
    const id = setInterval(() => {
      setElapsed((Date.now() - started.current) / 1000)
    }, 150)
    return () => clearInterval(id)
  }, [])

  // How far the SERVICE says we are. Until the first poll comes back this is
  // `queued`, which allows nothing past the first step - so the display can
  // never run ahead of work that has not started.
  const reached = stageRank(progress || 'queued')

  // The last step the service's own stage permits. Everything after it is
  // work that demonstrably has not happened yet.
  let ceiling = 0
  for (let i = 0; i < phases.length; i++) {
    if (stageRank(phases[i].stage) <= reached) ceiling = i
  }
  // `complete` means the answer is in hand, so every step is legitimately done.
  const finished = reached >= stageRank('complete')

  // Within what the service permits, walk the steps on their own dwell times
  // so a forty-second stage reads as movement rather than a freeze. The walk
  // is clamped to `ceiling`: it can look busy, it cannot claim to have
  // finished something still running.
  let acc = 0
  let timed = phases.length - 1
  for (let i = 0; i < phases.length; i++) {
    acc += phases[i].secs
    if (elapsed < acc) { timed = i; break }
  }
  const current = finished ? phases.length - 1 : Math.min(timed, ceiling)

  // Progress reads off the steps actually cleared, not off a stopwatch, so it
  // cannot sit at 100% while the analysis is still going.
  const overall = finished
    ? 100
    : Math.min(99, Math.round((current / Math.max(1, phases.length)) * 100))

  return (
    <div className="proc">
      <div className="proc-head">
        <p className="eyebrow">Analysis in progress</p>
        <h2 className="proc-file">{filename}</h2>
        <p className="caption">
          {phases.length} steps. The report appears the moment the answer does.
        </p>
      </div>

      <LevelStrip stage={stage} mode={mode} />

      <div className="prog" style={{ '--steps': phases.length }}>
        {phases.map((p, i) => (
          <div key={p.name} className="prog-seg">
            <div className="prog-seg-fill"
                 style={{ transform: `scaleX(${i < current ? 1 : i === current ? 0.5 : 0})` }} />
          </div>
        ))}
      </div>

      <div className="proc-meta">
        <span className="caption num">{clock(elapsed)} elapsed</span>
        <span className="caption num">{Math.round(overall)}%</span>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: '0.5rem' }}>
          {onHide && <button className="btn btn--ghost btn--sm" onClick={onHide}>Hide</button>}
          <button className="btn btn--ghost btn--sm" onClick={onCancel}>Cancel</button>
        </div>
      </div>

      <div className="proc-split">
        <div className="proc-left">
          <div className="stage-visual">
            <Viz kind={phases[current].viz} />
          </div>

          <div className="proc-now">
            <span className="proc-now-idx mono">
              {String(current + 1).padStart(2, '0')} / {String(phases.length).padStart(2, '0')}
            </span>
            <span className="proc-now-name">{phases[current].name}</span>
            <span className="proc-now-tech mono">{phases[current].tech}</span>
          </div>
        </div>

        <ol className="steps" role="status" aria-live="polite">
          {phases.map((p, i) => (
            <li key={p.name}
                className={`step${i === current ? ' is-active' : ''}${i < current ? ' is-done' : ''}`}>
              <span className="step-idx mono">
                {i < current ? <Tick /> : String(i + 1).padStart(2, '0')}
              </span>
              <span className="step-name">{p.name}</span>
              <span className="step-tech mono">{p.tech}</span>
            </li>
          ))}
        </ol>
      </div>
    </div>
  )
}

function Tick() {
  return (
    <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="3.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M20 6 9 17l-5-5" />
    </svg>
  )
}

/* ------------------------------------------------------------------ visuals
   All CSS driven. Each has a visible resting state so a throttled frame shows
   a sensible figure rather than an empty box. */
function Viz({ kind }) {
  switch (kind) {
    case 'bars': return <VizBars />
    case 'pulse': return <VizPulse />
    case 'cells': return <VizCells />
    case 'scan': return <VizScan />
    case 'merge': return <VizMerge />
    case 'spectrum': return <VizSpectrum />
    case 'grid': return <VizGrid />
    case 'chroma': return <VizChroma />
    case 'meter': return <VizMeter />
    case 'verify': return <VizVerify />
    case 'compile': return <VizCompile />
    default: return <VizCells />
  }
}

function VizBars() {
  const h = useMemo(
    () => Array.from({ length: 44 }, (_, i) => 0.2 + Math.abs(Math.sin(i * 0.7)) * 0.8), [])
  return (
    <div className="viz viz-bars">
      {h.map((v, i) => (
        <span key={i} className="viz-bar"
              style={{ '--h': `${v * 100}%`, animationDelay: `${i * 0.035}s` }} />
      ))}
    </div>
  )
}

function VizPulse() {
  return (
    <div className="viz viz-pulse">
      {[0, 1, 2].map((i) => (
        <span key={i} className="viz-ring" style={{ animationDelay: `${i * 0.63}s` }} />
      ))}
      <span className="viz-core" />
    </div>
  )
}

function VizCells() {
  return (
    <div className="viz viz-cells">
      {Array.from({ length: 48 }).map((_, i) => (
        <span key={i} className="viz-cell" style={{ animationDelay: `${(i % 24) * 0.07}s` }} />
      ))}
    </div>
  )
}

function VizScan() {
  const ticks = useMemo(
    () => Array.from({ length: 60 }, (_, i) => 14 + Math.abs(Math.sin(i * 1.3)) * 74), [])
  return (
    <div className="viz viz-scan">
      {ticks.map((h, i) => (
        <span key={i} className="viz-tick"
              style={{ left: `${(i / ticks.length) * 100}%`, height: `${h}%` }} />
      ))}
      <span className="viz-scanline" />
    </div>
  )
}

function VizMerge() {
  return (
    <div className="viz viz-merge">
      <span className="viz-stream">
        {[0, 1, 2, 3].map((i) => (
          <span key={i} className="viz-seg" style={{ animationDelay: `${i * 0.12}s` }} />
        ))}
      </span>
      <span className="viz-node" />
      <span className="viz-stream">
        {[0, 1, 2, 3].map((i) => (
          <span key={i} className="viz-seg viz-seg--alt"
                style={{ animationDelay: `${i * 0.12}s` }} />
        ))}
      </span>
    </div>
  )
}

/* Spectrum sweep: a falling response curve with a travelling highlight. */
function VizSpectrum() {
  const bars = useMemo(
    () => Array.from({ length: 40 }, (_, i) => Math.max(0.12, 1 - Math.pow(i / 40, 0.65))), [])
  return (
    <div className="viz viz-spectrum">
      {bars.map((v, i) => (
        <span key={i} className="viz-sbar"
              style={{ '--h': `${v * 100}%`, animationDelay: `${i * 0.045}s` }} />
      ))}
    </div>
  )
}

/* Step sequencer filling in, for the groove and drum phase. */
function VizGrid() {
  const on = useMemo(() => {
    const rows = [
      [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0],
      [0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0],
      [1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0],
    ]
    return rows
  }, [])
  return (
    <div className="viz viz-seq">
      {on.map((row, r) => (
        <span key={r} className="viz-seq-row">
          {row.map((v, c) => (
            <span key={c} className={`viz-step${v ? ' is-on' : ''}`}
                  style={{ animationDelay: `${c * 0.075}s` }} />
          ))}
        </span>
      ))}
    </div>
  )
}

/* Twelve pitch classes settling, for the harmony phase. */
function VizChroma() {
  const h = useMemo(
    () => [0.9, 0.3, 0.55, 0.35, 0.7, 0.45, 0.25, 0.8, 0.3, 0.6, 0.35, 0.5], [])
  return (
    <div className="viz viz-chroma">
      {h.map((v, i) => (
        <span key={i} className="viz-cbar"
              style={{ '--h': `${v * 100}%`, animationDelay: `${i * 0.09}s` }} />
      ))}
    </div>
  )
}

/* A level meter for the mastering phase. */
function VizMeter() {
  return (
    <div className="viz viz-meter">
      {[0, 1, 2].map((i) => (
        <span key={i} className="viz-meter-row">
          <span className="viz-meter-fill" style={{ animationDelay: `${i * 0.25}s` }} />
        </span>
      ))}
    </div>
  )
}

/* Shield with scanning rings for the verification phase. */
function VizVerify() {
  return (
    <div className="viz viz-verify">
      <span className="viz-shield">
        <svg width="42" height="48" viewBox="0 0 24 28" fill="none" stroke="currentColor"
             strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M12 2 L3 7 L3 14 C3 20 12 26 12 26 C12 26 21 20 21 14 L21 7 Z" />
          <path className="viz-shield-check" d="M8 14 L11 17 L16 11" strokeWidth="2" />
        </svg>
      </span>
      {[0, 1, 2].map((i) => (
        <span key={i} className="viz-verify-ring" style={{ animationDelay: `${i * 0.7}s` }} />
      ))}
      <span className="viz-verify-sweep" />
    </div>
  )
}

/* Stacking document layers for the compile / report-assembly phase. */
function VizCompile() {
  return (
    <div className="viz viz-compile">
      {[0, 1, 2, 3, 4].map((i) => (
        <span key={i} className="viz-doc" style={{ animationDelay: `${i * 0.25}s` }}>
          {[0, 1, 2].map((j) => (
            <span key={j} className="viz-doc-line"
                  style={{ width: j === 2 ? '55%' : '85%' }} />
          ))}
        </span>
      ))}
      <span className="viz-compile-glow" />
    </div>
  )
}