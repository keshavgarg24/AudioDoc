'use client'

// One page shell for every tool, driven by toolConfig.
//
// The marketing copy sits above the upload, not below it: a visitor arriving
// from search has to be able to read what the tool does and what it will not
// claim before deciding to hand over a file.
//
// The hero, the uploader and the results all sit on one centred column. A
// tool page is read top to bottom in a single pass - there is no second
// column of content to justify splitting the axis - and centring keeps the
// upload target, which is the only thing on the page a visitor must find,
// on the line their eye is already following.

import React, { useCallback, useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { runTool } from '../../lib/tools.js'
import { resultHeadline } from '../../lib/headline.js'
import ToolResult from '../tools/ToolResult.jsx'
import ToolVisual from '../visuals/ToolVisual.jsx'
import SplitFlapBoard from '../ui/split-flap-board.jsx'

const STAGE_LABELS = {
  queued: 'Queued',
  starting: 'Starting',
  hashing: 'Checking for a previous run',
  decoding: 'Decoding audio',
  analysing: 'Analysing',
  measuring: 'Measuring',
  finalising: 'Finishing up',
}

function bytes(size) {
  if (size < 1e6) return `${Math.max(1, Math.round(size / 1e3))} KB`
  return `${(size / 1e6).toFixed(1)} MB`
}

function FileSlot({ input, file, onPick, onClear, disabled }) {
  const ref = useRef(null)
  const [over, setOver] = useState(false)

  const take = (f) => { if (f) onPick(f) }

  return (
    <div
      className={`tool-slot${over ? ' over' : ''}${file ? ' filled' : ''}`}
      onDragOver={(e) => { e.preventDefault(); setOver(true) }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault(); setOver(false)
        if (!disabled) take(e.dataTransfer.files?.[0])
      }}
      onClick={() => !disabled && ref.current?.click()}
      role="button"
      aria-label={file ? `${input.label}: ${file.name}` : `Choose ${input.label}`}
      tabIndex={disabled ? -1 : 0}
      onKeyDown={(e) => {
        if (disabled) return
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); ref.current?.click() }
      }}
    >
      <input
        ref={ref} type="file" accept="audio/*" hidden disabled={disabled}
        onChange={(e) => take(e.target.files?.[0])}
      />

      <div className="tool-slot-label">{input.label}</div>

      {file ? (
        <div className="tool-slot-file">
          <span className="tool-slot-icon" aria-hidden="true">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none"
                 stroke="currentColor" strokeWidth="1.6">
              <path d="M9 18V6l10-2v12" />
              <circle cx="6.5" cy="18" r="2.5" />
              <circle cx="16.5" cy="16" r="2.5" />
            </svg>
          </span>
          <span className="tool-slot-name">
            <strong>{file.name}</strong>
            <span>{bytes(file.size)}</span>
          </span>
          {!disabled ? (
            <button
              type="button" className="tool-slot-clear" aria-label="Remove file"
              onClick={(e) => { e.stopPropagation(); onClear() }}
            >&times;</button>
          ) : null}
        </div>
      ) : (
        <div className="tool-slot-empty">
          <span className="tool-slot-icon" aria-hidden="true">
            <svg viewBox="0 0 24 24" width="22" height="22" fill="none"
                 stroke="currentColor" strokeWidth="1.5">
              <path d="M12 16V4m0 0L7.5 8.5M12 4l4.5 4.5" />
              <path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3" />
            </svg>
          </span>
          <strong>Drop a file or click to browse</strong>
          <span>{input.hint}</span>
        </div>
      )}
    </div>
  )
}

export default function ToolPage({ tool }) {
  const [files, setFiles] = useState({})
  const [phase, setPhase] = useState('idle')
  const [stage, setStage] = useState(null)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const abortRef = useRef(null)
  const resultsRef = useRef(null)

  // A result that lands below the fold looks like nothing happened.
  useEffect(() => {
    if (result && resultsRef.current) {
      resultsRef.current.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  }, [result])

  const ready = tool.inputs.every((i) => files[i.name])

  const submit = useCallback(async () => {
    if (!ready) return
    setPhase('working'); setError(null); setResult(null); setStage('queued')

    const ctrl = new AbortController()
    abortRef.current = ctrl
    try {
      const r = await runTool(tool.slug, files, {
        signal: ctrl.signal,
        onProgress: setStage,
      })
      setResult(r)
      setPhase('done')
    } catch (e) {
      if (e.name === 'AbortError') { setPhase('idle'); return }
      setError(e.message)
      setPhase('idle')
    } finally {
      abortRef.current = null
    }
  }, [files, ready, tool.slug])

  const cancel = () => abortRef.current?.abort()

  const reset = () => {
    setFiles({}); setResult(null); setError(null); setPhase('idle'); setStage(null)
  }

  const working = phase === 'working'
  const headline = result ? resultHeadline(tool.slug, result.data) : null

  return (
    <main className="tool-page">
      <nav className="tool-crumbs" aria-label="Breadcrumb">
        <Link href="/">Home</Link>
        <span aria-hidden="true">/</span>
        <Link href="/tools">Tools</Link>
        <span aria-hidden="true">/</span>
        <span aria-current="page">{tool.name}</span>
      </nav>

      <div className="tool-hero-row">
        <header className="tool-hero">
          <p className="tool-eyebrow">{tool.tagline}</p>
          <h1>{tool.name}</h1>
          <p className="tool-intro">{tool.intro}</p>
          <div className="tool-hero-meta">
            <span className="tool-meta-pill">
              {tool.inputs.length === 2 ? '2 files' : '1 file'}
            </span>
            <span className="tool-meta-pill">Free</span>
            <span className="tool-meta-pill">No signup</span>
            <span className="tool-meta-pill">Nothing stored</span>
          </div>
        </header>

        <div className="tool-hero-visual" aria-hidden="true">
          <ToolVisual slug={tool.slug} animating={working} />
        </div>
      </div>

      <section className="tool-uploader" aria-label={`Run ${tool.name}`}>
        <div className={`tool-slots count-${tool.inputs.length}`}>
          {tool.inputs.map((input) => (
            <FileSlot
              key={input.name}
              input={input}
              file={files[input.name]}
              disabled={working}
              onPick={(f) => setFiles((s) => ({ ...s, [input.name]: f }))}
              onClear={() => setFiles((s) => {
                const next = { ...s }; delete next[input.name]; return next
              })}
            />
          ))}
        </div>

        {error ? <div className="tool-error" role="alert">{error}</div> : null}

        <div className="tool-controls">
          <button
            className="tool-run"
            disabled={!ready || working}
            onClick={submit}
          >
            {working
              ? (STAGE_LABELS[stage] || 'Working') + '...'
              : `Run ${tool.name}`}
          </button>
          {working ? (
            <button className="tool-reset" onClick={cancel}>Cancel</button>
          ) : (result || error) ? (
            <button className="tool-reset" onClick={reset}>Start over</button>
          ) : null}
        </div>

      </section>

      {result ? (
        <section className="tool-results" ref={resultsRef}>
          <div className="tool-results-head">
            <h2>Results</h2>
            <span className="tool-meta">
              {result.cached
                ? 'Returned from a previous run of this exact file'
                : `Measured in ${result.durationSeconds}s`}
            </span>
          </div>

          {headline ? (
            <div className="tool-headline">
              <SplitFlapBoard
                text={headline.lines.join('\n')}
                rows={headline.lines.length}
                cols={16}
                label={headline.label}
              />
              <p className="tool-headline-note">
                The figure this tool exists to produce. Everything behind it is below.
              </p>
            </div>
          ) : null}

          <ToolResult slug={tool.slug} result={result.data} />
        </section>
      ) : null}

      <section className="tool-copy">
        <h2>Why use {tool.name}</h2>
        <div className="tool-props">
          {tool.valueProps.map((v) => (
            <div key={v.t} className="tool-prop">
              <h3>{v.t}</h3>
              <p>{v.d}</p>
            </div>
          ))}
        </div>

        <h2>How it works</h2>
        <ol className="tool-steps">
          {tool.howItWorks.map((s, i) => <li key={i}>{s}</li>)}
        </ol>

        <h2>What it is good at, and what it is not</h2>
        <div className="tool-honesty">
          <div className="honest-block good">
            <h3>Reliable</h3>
            <p>{tool.honest.strong}</p>
          </div>
          <div className="honest-block limit">
            <h3>Limits</h3>
            <p>{tool.honest.limits}</p>
          </div>
        </div>

        <h2>Questions</h2>
        <div className="tool-faq">
          {tool.faq.map((f) => (
            <details key={f.q}>
              <summary>{f.q}</summary>
              <p>{f.a}</p>
            </details>
          ))}
        </div>
      </section>
    </main>
  )
}