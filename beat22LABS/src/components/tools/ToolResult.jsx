'use client'

// Result renderers, one per tool, sharing a small set of display primitives.
//
// Each renderer reads only from the documented response shape, so a backend
// field that goes missing degrades to a hidden row rather than a crash. That
// matters because these pages are the product surface: a thrown exception in a
// renderer would lose a result the user waited a minute for.

import React from 'react'

const n = (v, d = 1) => (v === null || v === undefined || Number.isNaN(v)
  ? '--' : Number(v).toFixed(d))

// ------------------------------------------------------------- primitives --
export function Stat({ label, value, unit, hint, tone }) {
  return (
    <div className={`tool-stat${tone ? ` tone-${tone}` : ''}`}>
      <div className="tool-stat-label">{label}</div>
      <div className="tool-stat-value">
        {value}{unit ? <span className="tool-stat-unit">{unit}</span> : null}
      </div>
      {hint ? <div className="tool-stat-hint">{hint}</div> : null}
    </div>
  )
}

export function StatRow({ children }) {
  return <div className="tool-stat-row">{children}</div>
}

export function Section({ title, note, children }) {
  return (
    <section className="tool-section">
      <h3>{title}</h3>
      {note ? <p className="tool-note">{note}</p> : null}
      {children}
    </section>
  )
}

/** Signed bar centred on zero, for dB deltas. */
function DeltaBar({ value, max = 8 }) {
  const clamped = Math.max(-max, Math.min(max, value || 0))
  const pct = (Math.abs(clamped) / max) * 50
  const positive = clamped >= 0
  return (
    <div className="delta-bar">
      <div className="delta-bar-track">
        <div className="delta-bar-zero" />
        <div
          className={`delta-bar-fill ${positive ? 'pos' : 'neg'}`}
          style={{
            width: `${pct}%`,
            left: positive ? '50%' : `${50 - pct}%`,
          }}
        />
      </div>
    </div>
  )
}

/** 0-100 meter. */
function Meter({ value, tone = 'accent' }) {
  return (
    <div className="tool-meter">
      <div className={`tool-meter-fill tone-${tone}`}
           style={{ width: `${Math.max(0, Math.min(100, value || 0))}%` }} />
    </div>
  )
}

/** A percentile ruler with the reference ladder marked underneath.
 *
 * A bare percentage says "15th percentile" and stops. Drawing the p10, median
 * and p90 of the reference set on the same axis says what the 15th percentile
 * is fifteen percent *of*, in the unit the user actually works in.
 */
function PercentileScale({ percentile, ladder, unit }) {
  const pct = Math.max(0, Math.min(100, percentile ?? 0))
  const far = Math.abs(pct - 50) > 35
  const marks = [
    { at: 10, label: ladder?.p10 },
    { at: 50, label: ladder?.p50 },
    { at: 90, label: ladder?.p90 },
  ].filter((m) => m.label !== null && m.label !== undefined)

  return (
    <div className="pscale">
      <div className="pscale-track">
        <div className="pscale-band" />
        {marks.map((m) => (
          <span key={m.at} className="pscale-tick" style={{ left: `${m.at}%` }} />
        ))}
        <div className={`pscale-you${far ? ' far' : ''}`} style={{ left: `${pct}%` }}>
          <span className="pscale-you-dot" />
        </div>
      </div>
      {marks.length ? (
        <div className="pscale-legend">
          {marks.map((m) => (
            <span key={m.at} className="pscale-legend-item"
                  style={{ left: `${m.at}%` }}>
              <em>{m.at === 50 ? 'median' : `p${m.at}`}</em>
              {n(m.label, 1)}{unit ? ` ${unit}` : ''}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  )
}

/** Reference track card: artwork, name, and why it is in the set. */
function RefTrack({ t }) {
  // Artwork is served by a third party. A dead URL must collapse to the
  // placeholder rather than leave a broken-image glyph in the results.
  const [artOk, setArtOk] = React.useState(true)
  const meta = []
  if (t.peak_rank) meta.push(`peaked #${t.peak_rank}`)
  if (t.days_on_chart) meta.push(`${t.days_on_chart}d charting`)
  if (t.countries) meta.push(`${t.countries} ${t.countries === 1 ? 'market' : 'markets'}`)

  const body = (
    <>
      {t.artwork && artOk
        ? <img className="ref-art" src={t.artwork} alt="" loading="lazy"
               width="44" height="44" onError={() => setArtOk(false)} />
        : <span className="ref-art ref-art--none" aria-hidden="true" />}
      <span className="ref-text">
        <strong className="ref-title">{t.title}</strong>
        <span className="ref-artist">{t.artist}</span>
        <span className="ref-meta">{meta.join(' · ')}</span>
      </span>
      {t.bpm ? <span className="ref-bpm">{n(t.bpm, 0)}<em>BPM</em></span> : null}
    </>
  )

  return t.url ? (
    <a className="ref-card" href={t.url} target="_blank" rel="noopener noreferrer">
      {body}
    </a>
  ) : (
    <div className="ref-card">{body}</div>
  )
}

function Actions({ items }) {
  if (!items?.length) return null
  return (
    <Section title="What to change" note="Ordered by impact.">
      <ol className="tool-actions">
        {items.map((a, i) => (
          <li key={i} className={`tool-action prio-${a.priority || 'medium'}`}>
            <div className="tool-action-head">
              <span className={`tool-chip ${a.priority}`}>{a.priority}</span>
              <strong>{a.title}</strong>
            </div>
            {a.detail ? <p>{a.detail}</p> : null}
          </li>
        ))}
      </ol>
    </Section>
  )
}

function Limits({ items }) {
  if (!items?.length) return null
  return (
    <Section title="What this cannot tell you">
      <ul className="tool-limits">
        {items.map((l, i) => <li key={i}>{l}</li>)}
      </ul>
    </Section>
  )
}

// ---------------------------------------------------------- master check --
function MasterCheck({ d }) {
  const l = d.loudness || {}
  const h = d.headline || {}
  const im = d.instrumental_mode || {}
  return (
    <>
      {im.detected ? (
        <div className="tool-warning">
          <strong>Instrumental detected, so judge loudness differently.</strong>
          <p>{im.note}</p>
        </div>
      ) : null}

      <StatRow>
        <Stat label="Integrated loudness" value={n(l.integrated_lufs)} unit=" LUFS" />
        <Stat label="True peak" value={n(l.true_peak_dbtp, 2)} unit=" dBTP"
              tone={l.true_peak_dbtp > -1 ? 'warn' : 'ok'} />
        <Stat label="Loudness range" value={n(l.loudness_range_lu)} unit=" LU" />
        <Stat label="Status" value={h.status || '--'}
              tone={h.status === 'ready' ? 'ok' : 'warn'} />
      </StatRow>

      {l.platform_targets?.length ? (
        <Section title="How each platform will play it"
                 note="Streaming services normalise to their own target. This is what happens to your loudness after they do.">
          <div className="platform-list">
            {l.platform_targets.map((p) => (
              <div key={p.platform} className="platform-row">
                <span className="platform-name">{p.platform}</span>
                <span className="platform-target">{p.target_lufs} LUFS</span>
                <span className={`platform-verdict ${
                  p.verdict === 'on target' ? 'ok' : 'warn'}`}>{p.verdict}</span>
              </div>
            ))}
          </div>
        </Section>
      ) : null}

      {d.tonal_balance?.bands?.length ? (
        <Section title="Tonal balance" note={d.tonal_balance.note}>
          <div className="band-list">
            {d.tonal_balance.bands.map((b) => (
              <div key={b.label} className="band-row">
                <span className="band-label">{b.label}</span>
                <Meter value={b.share_pct * 2} />
                <span className="band-value">{n(b.share_pct)}%</span>
              </div>
            ))}
          </div>
        </Section>
      ) : null}

      {d.release_readiness?.issues?.length ? (
        <Section title="Delivery check">
          <ul className="tool-limits">
            {d.release_readiness.issues.map((i, k) => <li key={k}>{i}</li>)}
          </ul>
        </Section>
      ) : null}

      {d.guidance?.notes?.length ? (
        <Section title="Guidance" note={d.guidance.disclaimer}>
          <ul className="tool-limits">
            {d.guidance.notes.map((g, k) => <li key={k}>{g.message}</li>)}
          </ul>
        </Section>
      ) : null}
    </>
  )
}

// -------------------------------------------------------------- tempo lab --
function TempoLab({ d }) {
  const t = d.tempo || {}
  return (
    <>
      <StatRow>
        <Stat label="Tempo" value={n(t.bpm)} unit=" BPM" />
        <Stat label="Confidence" value={t.confidence?.label || '--'}
              tone={t.confidence?.label === 'high' ? 'ok' : 'warn'} />
        <Stat label="Stability" value={t.stability?.label || '--'} />
        <Stat label="Time signature" value={d.meter?.time_signature || '--'} />
      </StatRow>

      {t.alternatives?.length > 1 ? (
        <Section title="Metrical readings"
                 note="More than one of these can be correct. They describe the same music counted at a different pulse.">
          <div className="alt-list">
            {t.alternatives.map((a) => (
              <div key={a.bpm} className={`alt-row${a.primary ? ' primary' : ''}`}>
                <strong>{a.bpm} BPM</strong>
                <span>{a.relationship}</span>
              </div>
            ))}
          </div>
        </Section>
      ) : null}

      {t.confidence?.note ? <p className="tool-note">{t.confidence.note}</p> : null}

      {d.tempo?.changes?.count > 0 ? (
        <Section title="Tempo switches" note={d.tempo.changes.note}>
          <div className="alt-list">
            {d.tempo.changes.switches.map((c, i) => (
              <div key={i} className="alt-row">
                <strong>{c.at_s}s</strong>
                <span>{c.from_bpm} to {c.to_bpm} BPM</span>
                <span className="dim">{c.relationship}</span>
              </div>
            ))}
          </div>
        </Section>
      ) : null}

      {d.timing?.available ? (
        <Section title="Timing and groove" note={d.timing.note}>
          <StatRow>
            <Stat label="Swing" value={n(d.timing.swing_pct)} unit="%"
                  hint={d.timing.swing_classification} />
            <Stat label="Grid" value={d.timing.grid || '--'} />
            <Stat label="Off-grid" value={n(d.timing.mean_abs_deviation_ms)} unit=" ms" />
            <Stat label="Feel" value={d.timing.quantization_class || '--'} />
          </StatRow>
          {d.timing.quantization_note
            ? <p className="tool-note">{d.timing.quantization_note}</p> : null}
        </Section>
      ) : (
        <p className="tool-note">{d.timing?.note}</p>
      )}
    </>
  )
}

// ---------------------------------------------------------------- key lab --
function KeyLab({ d }) {
  if (d.available === false) return <p className="tool-note">{d.note}</p>
  const k = d.key || {}
  return (
    <>
      <StatRow>
        <Stat label="Key" value={k.name || '--'} />
        <Stat label="Camelot" value={k.camelot || '--'} />
        <Stat label="Confidence" value={k.confidence?.label || '--'}
              tone={k.confidence?.label === 'high' ? 'ok' : 'warn'} />
        <Stat label="Scale notes" value={(d.scale?.notes || []).join(' ')} />
      </StatRow>

      {k.confidence?.note ? <p className="tool-note">{k.confidence.note}</p> : null}

      {d.candidates?.length ? (
        <Section title="Ranked candidates"
                 note="Key detection is ambiguous more often than most tools admit. These are the top three.">
          <div className="alt-list">
            {d.candidates.map((c, i) => (
              <div key={c.key} className={`alt-row${i === 0 ? ' primary' : ''}`}>
                <strong>{c.key}</strong>
                <span>{c.camelot}</span>
                <span>score {n(c.score, 3)}</span>
              </div>
            ))}
          </div>
        </Section>
      ) : null}

      {d.chords?.available ? (
        <Section title="Chord progression" note={d.chords.note}>
          <div className="chord-row">
            {d.chords.progression.map((c, i) => (
              <span key={i} className="chord-chip">{c}</span>
            ))}
          </div>
        </Section>
      ) : null}

      {d.harmonic_mixing?.available ? (
        <Section title="Mixes well with" note={d.harmonic_mixing.note}>
          <div className="alt-list">
            {d.harmonic_mixing.compatible.map((c) => (
              <div key={c.camelot} className="alt-row">
                <strong>{c.camelot}</strong>
                <span>{c.relationship}</span>
                <span className="dim">{c.note}</span>
              </div>
            ))}
          </div>
        </Section>
      ) : null}
    </>
  )
}

// -------------------------------------------------------- reference match --
function ReferenceMatch({ d }) {
  const h = d.headline || {}
  return (
    <>
      <StatRow>
        <Stat label="Match score" value={n(h.match_score)} unit="/100" />
        <Stat label="Loudness gap" value={n(d.loudness?.delta?.integrated_lu)} unit=" LU" />
        <Stat label="Dynamics gap" value={n(d.loudness?.delta?.loudness_range_lu)} unit=" LU" />
        <Stat label="Width gap" value={n(d.stereo?.delta?.width_pct)} unit="%" />
      </StatRow>

      <p className="tool-note">{d.method?.note}</p>

      {d.tonal_balance?.bands?.length ? (
        <Section title="Frequency differences"
                 note="Positive means your track has more energy there than the reference.">
          <div className="band-list">
            {d.tonal_balance.bands.map((b) => (
              <div key={b.band} className={`band-row sev-${b.severity}`}>
                <span className="band-label">{b.label}</span>
                <DeltaBar value={b.delta_db} />
                <span className="band-value">
                  {b.delta_db > 0 ? '+' : ''}{n(b.delta_db)} dB
                </span>
              </div>
            ))}
          </div>
        </Section>
      ) : null}

      <Actions items={d.actions} />
      <Limits items={d.limitations} />
    </>
  )
}

// ------------------------------------------------------------- vocal lab --
function VocalLab({ d }) {
  const p = d.pitch || {}
  return (
    <>
      {d.input_check && !d.input_check.looks_isolated ? (
        <div className="tool-warning">
          <strong>This may not be an isolated vocal.</strong>
          <ul>{(d.input_check.warnings || []).map((w, i) => <li key={i}>{w}</li>)}</ul>
        </div>
      ) : null}

      <StatRow>
        <Stat label="Range" value={p.range?.label || '--'}
              hint={p.range ? `${n(p.range.octaves, 2)} octaves` : null} />
        <Stat label="Pitch accuracy" value={n(p.tuning?.mean_abs_cents)} unit=" cents"
              tone={p.tuning?.label === 'tight' ? 'ok' : 'warn'}
              hint={p.tuning?.label} />
        <Stat label="Vibrato" value={n(p.vibrato?.rate_hz, 2)} unit=" Hz"
              hint={p.vibrato?.label} />
        <Stat label="Sibilance" value={d.hygiene?.sibilance?.label || '--'} />
      </StatRow>

      {p.tuning?.note ? <p className="tool-note">{p.tuning.note}</p> : null}

      {p.available ? (
        <Section title="Where your voice lives" note={p.tessitura?.note}>
          <StatRow>
            <Stat label="Comfortable low" value={p.tessitura?.low_note || '--'} />
            <Stat label="Comfortable high" value={p.tessitura?.high_note || '--'} />
            <Stat label="Full low" value={p.range?.low_note || '--'} />
            <Stat label="Full high" value={p.range?.high_note || '--'} />
          </StatRow>
        </Section>
      ) : null}

      <Section title="Recording quality">
        <ul className="tool-limits">
          {d.hygiene?.sibilance?.note ? <li>{d.hygiene.sibilance.note}</li> : null}
          {d.hygiene?.plosives?.note ? <li>{d.hygiene.plosives.note}</li> : null}
          {d.hygiene?.noise_floor?.note ? <li>{d.hygiene.noise_floor.note}</li> : null}
          {d.dynamics?.note ? <li>{d.dynamics.note}</li> : null}
        </ul>
      </Section>

      {d.timing?.grid?.available ? (
        <Section title="Timing against the beat">
          <StatRow>
            <Stat label="Feel" value={d.timing.grid.feel} />
            <Stat label="Average offset" value={n(d.timing.grid.mean_signed_ms)} unit=" ms" />
          </StatRow>
          <p className="tool-note">{d.timing.grid.note}</p>
        </Section>
      ) : null}

      <Limits items={d.not_measured} />
    </>
  )
}

// -------------------------------------------------------- beat vocal fit --
function BeatVocalFit({ d }) {
  const h = d.headline || {}
  return (
    <>
      <StatRow>
        <Stat label="Fit score" value={n(h.fit_score)} unit="/100" />
        <Stat label="Key" value={d.key?.compatible ? 'compatible' : 'needs work'}
              tone={d.key?.compatible ? 'ok' : 'warn'} />
        <Stat label="Tempo" value={d.tempo?.compatible ? 'compatible' : 'needs work'}
              tone={d.tempo?.compatible ? 'ok' : 'warn'} />
        <Stat label="Worst clash" value={h.worst_masking_band || '--'} />
      </StatRow>

      <Section title="Key">
        <StatRow>
          <Stat label="Beat" value={d.key?.beat?.key || '--'}
                hint={d.key?.beat?.camelot} />
          <Stat label="Vocal" value={d.key?.vocal?.key || '--'}
                hint={d.key?.vocal?.camelot} />
          <Stat label="Shift needed" value={d.key?.semitone_shift ?? '--'}
                unit=" st" />
        </StatRow>
        <p className="tool-note">{d.key?.note}</p>
      </Section>

      <Section title="Tempo and timing">
        <StatRow>
          <Stat label="Beat" value={n(d.tempo?.beat_bpm)} unit=" BPM" />
          <Stat label="Vocal" value={n(d.tempo?.vocal_bpm)} unit=" BPM" />
          <Stat label="Stretch" value={n(d.tempo?.stretch_pct, 2)} unit="%" />
          <Stat label="Nudge" value={n(d.timing?.suggested_nudge_ms, 0)} unit=" ms" />
        </StatRow>
        <p className="tool-note">{d.tempo?.note}</p>
        {d.timing?.note ? <p className="tool-note">{d.timing.note}</p> : null}
      </Section>

      {d.masking?.bands?.length ? (
        <Section title="Where they compete" note={d.masking.note}>
          <div className="band-list">
            {d.masking.bands.map((b) => (
              <div key={b.band} className={`band-row sev-${b.severity}`}>
                <span className="band-label">
                  {b.label}
                  {b.intelligibility_band
                    ? <em className="band-tag">speech</em> : null}
                </span>
                <Meter value={(b.contention || 0) * 900}
                       tone={b.severity === 'heavy' ? 'warn' : 'accent'} />
                <span className="band-value">{b.severity}</span>
              </div>
            ))}
          </div>
        </Section>
      ) : null}

      {d.eq_curve?.available ? (
        <Section title="Suggested EQ moves" note={d.eq_curve.note}>
          <div className="eq-list">
            {d.eq_curve.moves.map((m) => (
              <div key={m.band} className="eq-row">
                <span className="eq-band">{m.label}</span>
                <span className="eq-freq">{m.centre_hz} Hz</span>
                <span className="eq-gain">{m.gain_db} dB</span>
                <span className="eq-q">Q {m.q}</span>
                <span className="eq-target">on the {m.apply_to}</span>
              </div>
            ))}
          </div>
        </Section>
      ) : null}

      <Actions items={d.actions} />
      <Limits items={d.limitations} />
    </>
  )
}

// --------------------------------------------------------------- hit lab --

// -------------------------------------------------------------- dispatch --
const RENDERERS = {
  'master-check': MasterCheck,
  'tempo-lab': TempoLab,
  'key-lab': KeyLab,
  'reference-match': ReferenceMatch,
  'vocal-lab': VocalLab,
  'beat-vocal-fit': BeatVocalFit,
}

export default function ToolResult({ slug, result }) {
  const Renderer = RENDERERS[slug]
  if (!Renderer) return <pre className="tool-raw">{JSON.stringify(result, null, 2)}</pre>
  try {
    return <Renderer d={result || {}} />
  } catch (e) {
    // A rendering bug must not destroy a result the user waited for.
    return (
      <div className="tool-warning">
        <strong>This result could not be displayed.</strong>
        <p>The raw data is below and is complete.</p>
        <pre className="tool-raw">{JSON.stringify(result, null, 2)}</pre>
      </div>
    )
  }
}