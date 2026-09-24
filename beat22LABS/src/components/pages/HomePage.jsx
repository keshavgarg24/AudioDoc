'use client'

import React, { useCallback, useEffect, useRef, useState } from 'react'
import Dropzone from '../Dropzone.jsx'
import ModeSelect from '../ModeSelect.jsx'
import Processing from '../Processing.jsx'
import Report from '../Report.jsx'
import HeroPanels from '../visuals/HeroPanels.jsx'
import ConfirmPopup from '../ConfirmPopup.jsx'
import AmbientWaves from '../visuals/AmbientWaves.jsx'
import FeaturedTool from '../FeaturedTool.jsx'
import { asReport, detect, escalate } from '../../lib/api.js'
import { useServiceStatus } from '../../lib/useServiceStatus.js'
import Link from 'next/link'
import { TOOLS } from '../../toolConfig.js'
import ToolVisual from '../visuals/ToolVisual.jsx'
import HeroHighlight, { Highlight } from '../ui/hero-highlight.jsx'

const STORAGE_KEY = 'beat22labs_report'

function loadSaved() {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY)
    return raw ? JSON.parse(raw) : null
  } catch { return null }
}

function saveReport(report) {
  try { sessionStorage.setItem(STORAGE_KEY, JSON.stringify(report)) } catch {}
}

function clearSaved() {
  try { sessionStorage.removeItem(STORAGE_KEY) } catch {}
}

const CAPABILITIES = [
  { n: '01', t: 'AI detection verdict',
    d: 'A clear call on whether a track was machine generated, with a confidence score and a reliability rating that tells you how far to trust it.' },
  { n: '02', t: 'Timeline across the track',
    d: 'Every ten second window scored on its own, so a partially generated or spliced track shows exactly where it changes.' },
  { n: '03', t: 'Signal forensics',
    d: 'Bandwidth ceiling, roll off, crest factor, stereo width and spectral flatness, measured directly from the audio rather than inferred.' },
  { n: '04', t: 'Tempo, key and groove',
    d: 'Tracked tempo, Camelot key, chord progression, grid and swing, plus how far the performance sits off the grid.' },
  { n: '05', t: 'Mastering and delivery',
    d: 'Integrated loudness, true peak, loudness range and per platform targets for Spotify, Apple Music, YouTube and club playback.' },
  { n: '06', t: 'Arrangement map',
    d: 'Section boundaries with energy and density, so intros, drops and breakdowns are laid out along the timeline.' },
  { n: '07', t: 'Character profile',
    d: 'A perceptual radar, mood weighting and how much midrange room a vocal would have over the track.' },
  { n: '08', t: 'Exportable case file',
    d: 'The whole report as PDF, a self contained HTML file, or the raw JSON with every number the analysis produced.' },
]

export default function App() {
  const saved = useRef(loadSaved())
  // Shared with the header: one poll loop for the whole app.
  const status = useServiceStatus()
  const [mode, setMode] = useState('screen')
  const [verify, setVerify] = useState(false)
  const [phase, setPhase] = useState(saved.current ? 'done' : 'idle')
  const [file, setFile] = useState(null)
  const [report, setReport] = useState(saved.current)
  const [error, setError] = useState(null)
  const [pendingFile, setPendingFile] = useState(null)
  const [isMinimized, setIsMinimized] = useState(false)

  const [result, setResult] = useState(null)
  const [phasesDone, setPhasesDone] = useState(false)
  // What the two levels are doing right now. Stage 1 answers in a couple of
  // seconds, so its verdict is shown while Stage 2 is still running rather
  // than holding a bare spinner for the full 90 s.
  const [stage, setStage] = useState(null)
  // Set when the quick check could not settle the track. The deeper pass is
  // then offered on the result rather than run automatically.
  const [canEscalate, setCanEscalate] = useState(false)
  const [escalating, setEscalating] = useState(false)
  const abortRef = useRef(null)

  useEffect(() => {
    if (phase === 'working' && result && phasesDone) {
      setReport(result)
      saveReport(result)
      setPhase('done')
      window.scrollTo({ top: 0, behavior: 'smooth' })
    }
  }, [phase, result, phasesDone])

  const handleFilePicked = useCallback((f) => {
    setPendingFile(f)
  }, [])

  const startAnalysis = useCallback(async () => {
    const f = pendingFile
    if (!f) return
    setPendingFile(null)
    setFile(f)
    setError(null)
    setReport(null)
    clearSaved()
    setResult(null)
    setPhasesDone(false)
    setStage(null)
    setCanEscalate(false)
    setPhase('working')
    setIsMinimized(false)

    const ctrl = new AbortController()
    abortRef.current = ctrl
    try {
      const { screen, report: deep, canEscalate: more } = await detect(f, {
        mode, verify, signal: ctrl.signal, onStage: setStage,
      })
      setCanEscalate(Boolean(more))
      // Stage 2 supersedes Stage 1 when it ran: its response already carries
      // the Level-1 evidence under `detection.level_1`, plus
      // `level_agreement` comparing the two. When Stage 1 settled the track
      // there is no deep report and the screen response is the answer.
      setResult(deep || asReport(screen))
    } catch (e) {
      if (e.name === 'AbortError') { setPhase('idle'); return }
      setError(e.message)
      setPhase('idle')
    } finally {
      abortRef.current = null
    }
  }, [pendingFile, mode, verify])

  // The deeper pass, started from the result rather than on upload. The file
  // is re-sent because the quick check holds nothing server-side.
  const runEscalation = useCallback(async () => {
    if (!file || escalating) return
    setEscalating(true)
    setError(null)
    setCanEscalate(false)
    // Back to the working view, exactly as a fresh upload does. Leaving the
    // finished report on screen behind a disabled button meant a minute of
    // analysis with no progress, no elapsed time and no way to cancel - the
    // one part of the run where the wait is longest.
    setResult(null)
    setPhasesDone(false)
    setStage(null)
    setPhase('working')
    setIsMinimized(false)
    window.scrollTo({ top: 0, behavior: 'smooth' })

    const ctrl = new AbortController()
    abortRef.current = ctrl
    try {
      const { report: deep } = await escalate(file, {
        mode: 'ai', verify, signal: ctrl.signal, onStage: setStage,
      })
      setResult(deep)
    } catch (e) {
      if (e.name === 'AbortError') {
        // Cancelled: the quick-check verdict is still valid and still on
        // screen, so go back to it rather than to an empty page.
        setPhase('done')
        setCanEscalate(true)
        return
      }
      setError(e.message)
      setPhase('done')
      setCanEscalate(true)
    } finally {
      setEscalating(false)
      abortRef.current = null
    }
  }, [file, verify, escalating])

  const reset = () => {
    setPhase('idle'); setReport(null); setResult(null)
    setPhasesDone(false); setFile(null); setError(null)
    setIsMinimized(false); setStage(null); setCanEscalate(false)
    clearSaved()
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  const needsModel = mode !== 'audio'
  // Allow upload always — the backend will queue the request. Only block when
  // the service is completely offline (not just still loading weights).
  const canRun = status.status !== 'offline'

  return (
    <div className="shell">
      <main className="page">
        {(phase === 'idle' || isMinimized) && (
          <div>
            <HeroHighlight containerClassName="home-hero-hl">
            <div className="hero-split">
              <AmbientWaves />
              <div className="hero-copy">
                {/* The landing page's h1. It was an h2, which left the site's
                    most important page with no top-level heading at all. */}
                <h1 className="h-display rise rise-1">
                  Every track<br /><Highlight>leaves a trace.</Highlight>
                </h1>
                <p className="body rise rise-2">
                  Upload audio for a full breakdown. A verdict timeline across
                  the track, the structure behind it, complete spectral and
                  mastering measurements, and a reliability score that tells
                  you how far to trust the result.
                </p>
              </div>
              <div className="hero-visual rise rise-2">
                <HeroPanels />
              </div>
            </div>
            </HeroHighlight>

            {error && (
              <div className="alert" role="alert">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none"
                     stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                  <circle cx="12" cy="12" r="9" /><path d="M12 8v4M12 16h.01" />
                </svg>
                <span>{error}</span>
              </div>
            )}

            <div className="rise rise-3">
              <p className="eyebrow" style={{ marginBottom: '0.7rem' }}>
                Choose an analysis
              </p>
              <ModeSelect value={mode} onChange={setMode} />
            </div>

            <div className="rise rise-4" style={{ marginTop: '1.1rem' }}>
              <Dropzone onFile={handleFilePicked} disabled={!canRun} mode={mode}
                        verify={verify} onVerifyChange={setVerify} />
            </div>

            {!canRun && (
              <p className="caption center" style={{ marginTop: '0.9rem' }}>
                {status.status === 'offline'
                  ? 'The analysis service is not responding. Audio analysis needs no model and will run as soon as the service is reachable.'
                  : 'Preparing the detection model. Audio analysis is available now.'}
              </p>
            )}

            <div className="feature-grid rise rise-4">
              {CAPABILITIES.map((c) => (
                <div key={c.n} className="feature">
                  <div className="feature-n mono">{c.n}</div>
                  <div className="feature-t">{c.t}</div>
                  <p className="caption">{c.d}</p>
                </div>
              ))}
            </div>

            <FeaturedTool />
            
            <div className="rise rise-5" style={{ marginTop: '4rem' }}>
              <div className="tool-grid">
                {TOOLS.map((t) => (
                  <Link key={t.slug} href={t.route} className="tool-card">
                    <h2>{t.name}</h2>
                    <p className="tool-card-tag">{t.tagline}</p>
                    <p className="tool-card-desc">{t.intro}</p>
                    <div className="tool-card-visual">
                      <ToolVisual slug={t.slug} />
                    </div>
                    <div className="tool-card-foot">
                      <span>{t.inputs.length === 2 ? 'Two files' : 'One file'}</span>
                      <span>Free</span>
                    </div>
                  </Link>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* During an escalation the chosen mode is still `screen`, but the
            work actually running is the deep pass, so the phases shown have
            to be the deep ones. */}
        {phase === 'working' && !isMinimized && (
          <Processing filename={file?.name || 'audio'}
                      mode={escalating ? 'ai' : mode} verify={verify}
                      stage={stage}
                      onCancel={() => abortRef.current?.abort()}
                      onHide={() => setIsMinimized(true)}
                      onPhasesDone={() => setPhasesDone(true)} />
        )}

        {phase === 'working' && isMinimized && (
          <div className="proc-minimized" onClick={() => setIsMinimized(false)}>
            <div className="proc-min-spinner"></div>
            <span>Analyzing {file?.name || 'audio'}...</span>
            <button className="proc-min-expand" onClick={(e) => { e.stopPropagation(); setIsMinimized(false); }}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/></svg>
            </button>
          </div>
        )}

        {phase === 'done' && report && (
          <Report report={report} onReset={reset}
                  canEscalate={canEscalate} onEscalate={runEscalation}
                  escalating={escalating} />
        )}

        {pendingFile && (
          <ConfirmPopup
            file={pendingFile}
            mode={mode}
            verify={verify}
            onModeChange={setMode}
            onVerifyChange={setVerify}
            onConfirm={startAnalysis}
            onCancel={() => setPendingFile(null)}
          />
        )}
      </main>
    </div>
  )
}