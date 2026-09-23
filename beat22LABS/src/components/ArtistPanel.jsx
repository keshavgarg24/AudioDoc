'use client'

import React from 'react'
import { Panel } from './MusicCharts.jsx'
import { num } from '../lib/format.js'

/* Artist-facing analytics: commercial benchmark, genre fit, transformation
   guide, release blockers and sync readiness. Every number here is measured;
   the copy alongside it is the action to take. */

function ScoreDial({ score, grade, label }) {
  const pct = Math.max(0, Math.min(100, Number(score) || 0))
  const R = 42
  const C = 2 * Math.PI * R
  return (
    <div className="dial">
      <svg viewBox="0 0 110 110" className="dial-svg" role="img"
           aria-label={`${label} ${pct} out of 100`}>
        <circle cx="55" cy="55" r={R} className="dial-track" />
        <circle cx="55" cy="55" r={R} className="dial-fill"
                strokeDasharray={`${(pct / 100) * C} ${C}`}
                transform="rotate(-90 55 55)" />
      </svg>
      <div className="dial-centre">
        <span className="dial-num num">{num(pct, 0)}</span>
        <span className="dial-grade">{grade}</span>
      </div>
    </div>
  )
}

function FactorBar({ f }) {
  return (
    <div className="factor">
      <div className="factor-head">
        <span className="factor-name">{f.label}</span>
        <span className="factor-score num">{num(f.score, 0)}</span>
      </div>
      <div className="factor-track">
        <div className={`factor-fill is-${f.grade.replace(' ', '-')}`}
             style={{ width: `${Math.max(2, f.score)}%` }} />
      </div>
      <p className="caption factor-note">{f.verdict}</p>
    </div>
  )
}

/* Percentile-style strip: where this track sits against the genre range. */
function RangeStrip({ label, current, targetLo, targetHi, unit, status }) {
  const lo = Number(targetLo), hi = Number(targetHi), cur = Number(current)
  const span = Math.max(hi - lo, 1e-6)
  const pad = span * 0.9
  const min = lo - pad, max = hi + pad
  const pos = ((cur - min) / (max - min)) * 100
  const bandL = ((lo - min) / (max - min)) * 100
  const bandW = ((hi - lo) / (max - min)) * 100
  return (
    <div className="strip">
      <div className="strip-head">
        <span className="strip-name">{label}</span>
        <span className={`strip-val num is-${status}`}>
          {num(cur, 1)} {unit}
        </span>
      </div>
      <div className="strip-track">
        <div className="strip-band" style={{ left: `${bandL}%`, width: `${bandW}%` }} />
        <div className="strip-marker"
             style={{ left: `${Math.max(0, Math.min(100, pos))}%` }} />
      </div>
      <div className="strip-scale caption">
        <span>target {num(lo, 1)}</span><span>{num(hi, 1)}</span>
      </div>
    </div>
  )
}

export default function ArtistPanel({ artist }) {
  if (!artist) return null
  const hit = artist.hit_potential
  const fit = artist.genre_fit
  const tr = artist.genre_transform
  const rr = artist.release_readiness
  const sync = artist.sync_readiness
  const mix = artist.mix_report

  return (
    <>
      {/* ---- commercial benchmark ---- */}
      {hit?.available && (
        <div className="grid grid--2">
          <Panel title="Commercial readiness"
                 sub={`Benchmarked against ${hit.benchmarked_against}`}
                 note={hit.coverage}>
            <div className="hit-head">
              <ScoreDial score={hit.score} grade={hit.grade} label="Readiness" />
              <div className="hit-actions">
                <p className="eyebrow">Do these first</p>
                {(hit.priority_actions || []).slice(0, 3).map((a, i) => (
                  <div key={i} className="hit-action">
                    <span className="hit-action-score num">{num(a.score, 0)}</span>
                    <div>
                      <div className="hit-action-name">{a.factor}</div>
                      <p className="caption">{a.do}</p>
                    </div>
                  </div>
                ))}
                {!(hit.priority_actions || []).length && (
                  <p className="caption">No weak factors. This is competitive.</p>
                )}
              </div>
            </div>
            <p className="caption" style={{ marginTop: '1rem', opacity: 0.7 }}>
              {hit.disclaimer}
            </p>
          </Panel>

          <Panel title="Factor breakdown"
                 sub="Each measured against the genre reference range">
            {(hit.factors || []).map((f) => <FactorBar key={f.factor} f={f} />)}
          </Panel>
        </div>
      )}

      {/* ---- genre fit ---- */}
      {fit?.primary && (
        <div className="grid grid--2" style={{ marginTop: '1rem' }}>
          <Panel title="Genre fit"
                 note={`${num(fit.primary.match, 0)}% ${fit.primary.label}`}>
            {(fit.ranked || []).map((r) => (
              <div key={r.genre} className="moodrow">
                <span className="mood-name">{r.label}</span>
                <span className="mood-track">
                  <span className="mood-fill" style={{ width: `${r.match}%` }} />
                </span>
                <span className="mood-val num">{num(r.match, 0)}%</span>
              </div>
            ))}
            <p className="caption" style={{ marginTop: '0.8rem' }}>{fit.note}</p>
          </Panel>

          {mix?.harmonic_mixing && (
            <Panel title="Mix and groove"
                   sub="Opening, feel and harmonic compatibility">
              {mix.opening && (
                <>
                  <div className="kv"><span className="kv-k">First peak</span>
                    <span className="kv-v num">{mix.opening.first_peak_s}s
                      <em className={`tag is-${mix.opening.verdict}`}>
                        {mix.opening.verdict}</em></span></div>
                  <p className="caption" style={{ margin: '0.4rem 0 0.9rem' }}>
                    {mix.opening.note}</p>
                </>
              )}
              <div className="kv"><span className="kv-k">Feel</span>
                <span className="kv-v">{mix.groove?.feel}</span></div>
              <div className="kv"><span className="kv-k">Swing</span>
                <span className="kv-v num">{num(mix.groove?.swing_pct, 1)}%</span></div>
              <div className="kv"><span className="kv-k">Key</span>
                <span className="kv-v">{mix.harmonic_mixing.key}
                  {' '}({mix.harmonic_mixing.camelot})</span></div>
              <div className="kv"><span className="kv-k">Mixes with</span>
                <span className="kv-v mono">
                  {(mix.harmonic_mixing.compatible_camelot || []).join(' · ')}
                </span></div>
              <p className="caption" style={{ marginTop: '0.7rem' }}>
                {mix.harmonic_mixing.note}</p>
            </Panel>
          )}
        </div>
      )}

      {/* ---- transformation guide ---- */}
      {tr && !tr.error && (
        <Panel title={`Make it ${tr.target_label}`}
               sub={tr.summary}
               note={`effort: ${tr.effort}`}>
          <div className="grid grid--2">
            <div>
              <p className="eyebrow">Parameters to change</p>
              {(tr.parameter_changes || []).map((c, i) => (
                <RangeStrip key={i} label={c.parameter} current={c.current}
                            targetLo={String(c.target).split(' to ')[0]}
                            targetHi={String(c.target).split(' to ')[1]}
                            unit={c.unit} status={c.status} />
              ))}
              {!(tr.parameter_changes || []).length && (
                <p className="caption">Every measured parameter is already in range.</p>
              )}
              {!!(tr.already_correct || []).length && (
                <p className="caption" style={{ marginTop: '0.8rem' }}>
                  Already correct:{' '}
                  {tr.already_correct.map((c) => c.parameter).join(', ')}.
                </p>
              )}
            </div>
            <div>
              <p className="eyebrow">Production moves</p>
              <ul className="move-list">
                {(tr.production_moves || []).map((m, i) => <li key={i}>{m}</li>)}
              </ul>
              {tr.feel_change && (
                <p className="caption note-block">{tr.feel_change}</p>
              )}
              <p className="eyebrow" style={{ marginTop: '1.1rem' }}>
                What defines the genre
              </p>
              <ul className="move-list">
                {(tr.defining_traits || []).map((s, i) => <li key={i}>{s}</li>)}
              </ul>
            </div>
          </div>
        </Panel>
      )}

      {/* ---- release + sync ---- */}
      <div className="grid grid--2" style={{ marginTop: '1rem' }}>
        {rr && (
          <Panel title="Release readiness" note={rr.status}>
            <p className="body" style={{ marginBottom: '0.8rem' }}>{rr.summary}</p>
            {(rr.blockers || []).map((b, i) => (
              <div key={i} className="issue is-blocker">
                <div className="issue-name">{b.check.replace(/_/g, ' ')}</div>
                <p className="caption">{b.detail}</p>
                <p className="caption issue-fix">{b.fix}</p>
              </div>
            ))}
            {(rr.warnings || []).map((w, i) => (
              <div key={i} className="issue is-warn">
                <div className="issue-name">{w.check.replace(/_/g, ' ')}</div>
                <p className="caption">{w.detail}</p>
                <p className="caption issue-fix">{w.fix}</p>
              </div>
            ))}
            {!(rr.blockers || []).length && !(rr.warnings || []).length && (
              <p className="caption">Nothing to fix.</p>
            )}
          </Panel>
        )}

        <Panel title="Platform loudness"
               sub="What each service does to your master on playback">
          {(rr?.platform_loudness || []).map((p) => (
            <div key={p.platform} className="plat">
              <span className="plat-name">{p.platform}</span>
              <span className="plat-bar">
                <span className={`plat-fill ${p.gain_applied_db < 0 ? 'is-down' : 'is-up'}`}
                      style={{ width: `${Math.min(100, Math.abs(p.gain_applied_db) * 12)}%` }} />
              </span>
              <span className="plat-val num">
                {p.gain_applied_db > 0 ? '+' : ''}{num(p.gain_applied_db, 1)} dB
              </span>
            </div>
          ))}
          {sync && (
            <>
              <p className="eyebrow" style={{ marginTop: '1.2rem' }}>
                Sync readiness
              </p>
              <div className="kv"><span className="kv-k">Score</span>
                <span className="kv-v num">{num(sync.score, 0)} ({sync.grade})</span></div>
              {(sync.notes || []).map((n, i) => (
                <p key={i} className="caption">{n}</p>
              ))}
              {!!(sync.instrumental_windows || []).length && (
                <p className="caption" style={{ marginTop: '0.5rem' }}>
                  Instrumental windows:{' '}
                  {sync.instrumental_windows
                    .map((w) => `${w.label} ${w.start}s–${w.end}s`).join(', ')}
                </p>
              )}
            </>
          )}
        </Panel>
      </div>
    </>
  )
}