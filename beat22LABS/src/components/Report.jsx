'use client'

import React from 'react'
import Verdict from './Verdict.jsx'
import ArtistPanel from './ArtistPanel.jsx'
import Findings from './Findings.jsx'
import Timeline from './Timeline.jsx'
import Heatmap from './Heatmap.jsx'
import {
  BandChart, ChromaChart, LoudnessChart, NoveltyChart, SpectrumChart,
} from './Charts.jsx'
import {
  ArrangementChart, DrumGrid, GrooveChart, LoudnessTargets, Panel,
  Progression, RadarChart, StereoBands, TempoChart,
} from './MusicCharts.jsx'
import { clock, hz, num, pct } from '../lib/format.js'
import { exportHtml, exportJson, exportPdf } from '../lib/export.js'

function Section({ title, note, children }) {
  return (
    <section className="section rise">
      <div className="section-title">
        <p className="eyebrow">{title}</p>
        <span className="section-rule" />
        {note && <span className="caption mono">{note}</span>}
      </div>
      {children}
    </section>
  )
}

const MODE_LABEL = {
  ai: 'AI detection', audio: 'Audio analysis', full: 'Complete report',
}

export default function Report({ report, onReset }) {
  const { rhythm, source, runtime } = report
  const f = report.features || {}
  const mus = report.musical || {}
  const prod = report.production || {}
  const ch = report.character || {}
  const ind = report.industry || {}
  const det = report.detection || {}
  const cat = ind.catalogue_features, qc = ind.quality_control
  const vocal = ind.vocal_presence, beat = ind.structure
  const verification = det.verification

  const sp = f.spectral, dy = f.dynamics, tn = f.tonal, on = f.onsets
  const harmony = mus.harmony, groove = mus.groove, drums = mus.drums
  const arrangement = mus.arrangement, mrhythm = mus.rhythm
  const loud = prod.loudness, stereo = prod.stereo
  const enc = prod.encoding, sd = prod.sound_design, ready = prod.release_readiness

  const hasAi = Boolean(report.prediction)
  const hasAudio = Boolean(report.features || report.musical || report.production)

  const displayBpm = mrhythm?.bpm || rhythm?.bpm
  const displayKey = harmony?.key || tn?.key

  return (
    <div>
      {/* Only the buttons are hidden in print; the metadata prints, since
          otherwise the exported PDF would open with no title at all. */}
      <div className="report-head">
        <div>
          <p className="print-only print-brand">Beat22 LABS</p>
          <p className="eyebrow">{MODE_LABEL[report.mode] || 'Analysis report'}</p>
          <div className="file-meta"><span className="file-name">{source.filename}</span></div>
          <div className="file-meta">
            <span className="pill num">{clock(source.duration_seconds)}</span>
            {displayBpm ? <span className="pill num">{num(displayBpm, 0)} BPM</span> : null}
            {displayKey ? <span className="pill">{displayKey}</span> : null}
            {harmony?.camelot ? <span className="pill">{harmony.camelot}</span> : null}
            <span className="pill num">{num(runtime.elapsed_seconds, 0)}s</span>
          </div>
        </div>
        <div className="report-actions no-print">
          <button className="btn btn--sm" onClick={() => exportPdf()}>PDF</button>
          <button className="btn btn--sm" onClick={() => exportHtml(report)}>HTML</button>
          <button className="btn btn--sm" onClick={() => exportJson(report)}>JSON</button>
          <button className="btn btn--solid btn--sm" onClick={onReset}>New analysis</button>
        </div>
      </div>

      {ch.summary && (
        <p className="oneliner">{ch.summary}</p>
      )}

      {hasAi && <Verdict report={report} />}

      {report.artist && (
        <Section title="For the artist"
                 note={report.artist.hit_potential?.available
                   ? `readiness ${Math.round(report.artist.hit_potential.score)}`
                   : 'genre and release'}>
          <ArtistPanel artist={report.artist} />
        </Section>
      )}

      {hasAi && (
        <Section title="Detection findings" note="primary analysis">
          <Findings title="What the analysis found"
                    sub="Each point references a measured value shown elsewhere in this report"
                    findings={report.findings} />
        </Section>
      )}

      {report.signal_findings?.length > 0 && (
        <Section title="Signal forensics" note="measured from the audio">
          <Findings title="What the waveform shows"
                    sub="Measured directly from the audio"
                    summary={report.signal_summary?.note}
                    findings={report.signal_findings} />
        </Section>
      )}

      {hasAi && (
        <Section title="Verdict over time">
          <Timeline timeline={report.timeline} />
        </Section>
      )}

      {hasAi && (
        <Section title="Structural analysis">
          <div className="grid grid--2">
            <Heatmap structure={report.structure}
                     segmentSeconds={runtime.window_seconds} />
            <NoveltyChart structure={report.structure} />
          </div>
        </Section>
      )}

      {ch.radar?.length > 0 && (
        <Section title="Character">
          <div className="grid grid--2">
            <RadarChart radar={ch.radar} />
            <div className="grid" style={{ gap: '1rem', alignContent: 'start' }}>
              {ch.moods?.length > 0 && (
                <Panel title="Mood" sub="Weighted from measured spectral and rhythmic traits">
                  {ch.moods.map((m) => (
                    <div key={m.mood} className="moodrow">
                      <span className="mood-name">{m.mood}</span>
                      <span className="mood-track">
                        <span className="mood-fill" style={{ width: `${m.weight * 100}%` }} />
                      </span>
                      <span className="mood-val num">{num(m.weight, 2)}</span>
                    </div>
                  ))}
                </Panel>
              )}
              {ch.vocal_space && (
                <Panel title="Vocal space" note={pct(ch.vocal_space.midrange_occupancy, 1)}>
                  <p className="body">{ch.vocal_space.verdict}</p>
                  <p className="caption" style={{ marginTop: '0.5rem' }}>
                    Midrange occupancy {ch.vocal_space.midrange_occupancy_pct}%.
                    {' '}{ch.vocal_space.eq_suggestion}.
                  </p>
                  {ch.suitable_for?.length > 0 && (
                    <div style={{ marginTop: '0.9rem' }}>
                      <p className="eyebrow" style={{ marginBottom: '0.4rem' }}>
                        Suited for
                      </p>
                      {ch.suitable_for.map((s, i) => (
                        <p key={i} className="caption">{s}</p>
                      ))}
                    </div>
                  )}
                </Panel>
              )}
            </div>
          </div>
        </Section>
      )}

      {verification && (
        <Section title="Deeper verification"
                 note={det.consensus?.agreement === 'agree' ? 'confirmed' : 'review'}>
          <div className="grid grid--2">
            <Panel title="Cross-check result"
                   note={verification.prediction === 'ai_generated'
                     ? `${num(verification.ai_probability, 1)}% AI`
                     : 'confirmed human'}>
              <Row k="Result" v={verification.prediction === 'ai_generated'
                ? 'AI-generated' : 'Human-made, confirmed by deep analysis'} />
              {verification.prediction === 'ai_generated' && verification.likely_source && (
                <Row k="Likely source" v={verification.likely_source} />
              )}
              {det.consensus && <>
                <Row k="Agreement with primary" v={det.consensus.agreement} />
                <Row k="Combined verdict" v={det.consensus.verdict} />
                <Row k="Recommended action" v={det.consensus.recommended_action} />
              </>}
              {det.escalation?.detail && (
                <p className="caption" style={{ marginTop: '0.8rem' }}>
                  {det.escalation.detail}
                </p>
              )}
            </Panel>
            {verification.prediction === 'ai_generated' && verification.source_probabilities?.length > 0 && (
              <Panel title="Source attribution"
                     sub="How strongly the audio matches each known generator">
                {verification.source_probabilities.map((s) => (
                  <div key={s.source} className="moodrow">
                    <span className="mood-name">{s.source}</span>
                    <span className="mood-track">
                      <span className="mood-fill"
                            style={{ width: `${Math.min(100, s.probability)}%` }} />
                    </span>
                    <span className="mood-val num">{num(s.probability, 1)}%</span>
                  </div>
                ))}
              </Panel>
            )}
          </div>
        </Section>
      )}

      {qc && (
        <Section title="Quality control" note={qc.gate}>
          <div className={`readiness${qc.gate === 'pass' ? ' is-ready' : ''}`}>
            <p className="readiness-status">{qc.summary}</p>
            <div className="qc-list">
              {qc.checks.map((c, i) => (
                <div key={i} className={`qc-row is-${c.status}`}>
                  <span className="qc-status">{c.status}</span>
                  <span className="qc-name">{c.check.replace(/_/g, ' ')}</span>
                  <span className="qc-detail caption">{c.detail}</span>
                </div>
              ))}
            </div>
          </div>
        </Section>
      )}

      {ready && (
        <Section title="Release readiness"
                 note={ready.status === 'ready' ? 'no blockers' : 'needs work'}>
          <div className={`readiness${ready.status === 'ready' ? ' is-ready' : ''}`}>
            <p className="readiness-status">
              {ready.status === 'ready'
                ? 'No delivery blockers detected'
                : 'Issues to resolve before release'}
            </p>
            <ul className="readiness-list">
              {ready.issues.map((i, k) => <li key={k} className="body">{i}</li>)}
            </ul>
          </div>
        </Section>
      )}

      {cat && (
        <Section title="Catalogue features" note="estimated locally">
          <div className="grid grid--2">
            <Panel title="Track profile" sub={cat.note}>
              <div className="grid grid--4" style={{ marginBottom: '1rem' }}>
                <Stat label="Energy" value={num(cat.energy, 2)} />
                <Stat label="Danceability" value={num(cat.danceability, 2)} />
                <Stat label="Valence" value={num(cat.valence, 2)} />
                <Stat label="Acousticness" value={num(cat.acousticness, 2)} />
              </div>
              <Row k="Instrumentalness" v={num(cat.instrumentalness, 2)} />
              <Row k="Speechiness" v={num(cat.speechiness, 2)} />
              <Row k="Liveness" v={num(cat.liveness, 2)} />
            </Panel>
            <Panel title="Placement"
                   note={vocal ? vocal.verdict : null}>
              {vocal && <>
                <p className="body">{vocal.note}</p>
                <p className="caption" style={{ margin: '0.5rem 0 0.9rem' }}>
                  {vocal.caveat}
                </p>
              </>}
              {beat && <>
                {beat.intro_length_s != null && (
                  <Row k="Intro length" v={`${num(beat.intro_length_s, 1)} s`} />
                )}
                {beat.first_peak_s != null && (
                  <Row k="First peak at" v={clock(beat.first_peak_s)} />
                )}
                {beat.total_bars != null && <Row k="Total bars" v={beat.total_bars} />}
                {beat.hook_hint && (
                  <p className="caption" style={{ marginTop: '0.7rem' }}>
                    {beat.hook_hint}
                  </p>
                )}
              </>}
            </Panel>
          </div>
        </Section>
      )}

      {loud && (
        <Section title="Loudness and mastering"
                 note={`${num(loud.integrated_lufs, 1)} LUFS, ${num(loud.true_peak_dbtp, 2)} dBTP`}>
          <div className="grid" style={{ gap: '1rem' }}>
            <div className="grid grid--4">
              <Stat label="Integrated" value={num(loud.integrated_lufs, 1)} unit="LUFS" />
              <Stat label="True peak" value={num(loud.true_peak_dbtp, 2)} unit="dBTP" />
              <Stat label="Loudness range" value={num(loud.loudness_range_lu, 1)} unit="LU" />
              <Stat label="Peak to loudness" value={num(loud.plr_db, 1)} unit="dB" />
            </div>
            <LoudnessTargets loudness={loud} />
            {dy && <LoudnessChart dynamics={dy} />}
          </div>
        </Section>
      )}

      {(mrhythm || groove) && (
        <Section title="Tempo and groove"
                 note={mrhythm ? `${num(mrhythm.bpm, 1)} BPM, ${mrhythm.metrical_level}` : null}>
          <div className="grid" style={{ gap: '1rem' }}>
            {mrhythm && <TempoChart rhythm={mrhythm} />}
            <div className="grid grid--2">
              {groove?.available && <GrooveChart groove={groove} />}
              <Panel title="Timing detail">
                {mrhythm && <>
                  <Row k="Notated tempo" v={`${num(mrhythm.bpm, 2)} BPM`} />
                  <Row k="Tracked pulse" v={`${num(mrhythm.bpm_tracked, 2)} BPM`} />
                  <Row k="Metrical level" v={mrhythm.metrical_level} />
                  <Row k="Beat jitter" v={`${num(mrhythm.beat_jitter_ms, 2)} ms`} />
                  <Row k="Constant tempo" v={mrhythm.is_constant_tempo ? 'yes' : 'no'} />
                  <Row k="Bars" v={mrhythm.bar_count} />
                </>}
                {groove?.available && <>
                  <Row k="Grid" v={groove.grid} />
                  <Row k="Swing" v={`${num(groove.swing_pct, 1)}%`} />
                  <Row k="Quantisation" v={groove.quantization_class} />
                  <Row k="Pocket" v={groove.pocket} />
                  <Row k="Off beatness" v={num(groove.offbeatness, 2)} />
                  <Row k="Rhythmic entropy" v={num(groove.rhythmic_entropy, 2)} />
                </>}
              </Panel>
            </div>
            {drums?.available && <DrumGrid drums={drums} />}
          </div>
        </Section>
      )}

      {harmony && (
        <Section title="Harmony and tonality" note={harmony.camelot}>
          <div className="grid grid--2">
            <Progression harmony={harmony} />
            <ChromaChart tonal={{
              key: harmony.key, key_clarity: harmony.key_clarity,
              chroma: harmony.chroma,
            }} />
          </div>
        </Section>
      )}

      {arrangement?.available && (
        <Section title="Arrangement">
          <ArrangementChart arrangement={arrangement}
                            duration={source.duration_seconds} />
        </Section>
      )}

      {sp && (
        <Section title="Spectrum" note={`ceiling ${hz(sp.ceiling_hz)}`}>
          <div className="grid" style={{ gap: '1rem' }}>
            <SpectrumChart spectral={sp} />
            <div className="grid grid--2">
              <BandChart spectral={sp} />
              {stereo?.is_stereo && <StereoBands stereo={stereo} />}
            </div>
          </div>
        </Section>
      )}

      {(enc || sd) && (
        <Section title="Encoding and sound design">
          <div className="grid grid--2">
            {enc && (
              <Panel title="Encoding history"
                     sub="What has already been done to this file">
                <Row k="Container" v={enc.container_format || 'unknown'} />
                {enc.codec && <Row k="Codec" v={enc.codec} />}
                {enc.declared_bitrate_bps &&
                  <Row k="Declared bitrate" v={`${Math.round(enc.declared_bitrate_bps / 1000)} kbps`} />}
                {enc.source_sample_rate_hz &&
                  <Row k="Sample rate" v={`${enc.source_sample_rate_hz} Hz`} />}
                {enc.lowpass_shelf_hz && <Row k="Lowpass shelf" v={hz(enc.lowpass_shelf_hz)} />}
                {enc.inferred_encoding_history &&
                  <Row k="Inferred history" v={enc.inferred_encoding_history} />}
                <Row k="Transcode suspected" v={enc.transcode_suspected ? 'yes' : 'no'} />
                {enc.transcode_note &&
                  <p className="caption" style={{ marginTop: '0.7rem' }}>{enc.transcode_note}</p>}
              </Panel>
            )}
            {sd && (
              <Panel title="Processing" sub="Space and saturation on the master">
                {sd.reverb_rt60_ms && <Row k="Reverb RT60" v={`${num(sd.reverb_rt60_ms, 0)} ms`} />}
                {sd.reverb_character && <Row k="Space" v={sd.reverb_character} />}
                {sd.thd_ratio != null && <Row k="Harmonic distortion" v={pct(sd.thd_ratio, 1)} />}
                {sd.saturation && <Row k="Saturation" v={sd.saturation} />}
                {stereo?.is_stereo && <>
                  <Row k="Phase correlation" v={num(stereo.correlation, 3)} />
                  <Row k="Stereo width" v={`${num(stereo.width_pct, 1)}%`} />
                  <Row k="Mono compatible" v={stereo.mono_compatible ? 'yes' : 'no'} />
                </>}
                {sd.modulation_hz?.length > 0 && (
                  <div style={{ marginTop: '0.8rem' }}>
                    <p className="eyebrow" style={{ marginBottom: '0.4rem' }}>
                      Modulation detected
                    </p>
                    <div className="chords">
                      {sd.modulation_hz.map((m, i) => (
                        <span key={i} className="chord">{num(m, 2)} Hz</span>
                      ))}
                    </div>
                  </div>
                )}
              </Panel>
            )}
          </div>
        </Section>
      )}

      {(on || dy) && (
        <Section title="Measurements">
          <div className="grid grid--2">
            <Panel title="Dynamics">
              {dy && <>
                <Row k="Peak" v={`${num(dy.peak_db, 1)} dBFS`} />
                <Row k="Crest factor" v={`${num(dy.crest_factor_db, 1)} dB`} />
                <Row k="Dynamic range" v={`${num(dy.dynamic_range_db, 1)} dB`} />
                <Row k="Level deviation" v={`${num(dy.loudness_std_db, 2)} dB`} />
              </>}
              {loud && <Row k="Clipped samples" v={loud.clipped_samples} />}
            </Panel>
            <Panel title="Texture">
              {sp && <>
                <Row k="Spectral centroid" v={hz(sp.centroid_hz)} />
                <Row k="Rolloff 85%" v={hz(sp.rolloff85_hz)} />
                <Row k="Spectral flatness" v={`${num(sp.flatness_db, 1)} dB`} />
              </>}
              {tn && <Row k="Harmonic to percussive"
                          v={`${pct(tn.harmonic_ratio, 0)} to ${pct(tn.percussive_ratio, 0)}`} />}
              {on && <>
                <Row k="Onset density" v={`${num(on.onset_density, 2)} per second`} />
                <Row k="Onset regularity" v={pct(on.onset_regularity, 0)} />
              </>}
            </Panel>
          </div>
        </Section>
      )}

      <div className="footer">
        <p className="caption">
          Beat22 LABS. {hasAi ? 'Detection across beat aligned windows, ' : ''}
          {hasAudio ? 'with independent signal, musical and mastering analysis. ' : ''}
          Statistical analysis is evidence, not proof of origin. Weigh any
          verdict against its reliability score and treat a single indicator as
          supporting evidence rather than a conclusion.
        </p>
      </div>
    </div>
  )
}

function Stat({ label, value, unit }) {
  return (
    <div>
      <p className="eyebrow">{label}</p>
      <p className="stat-value num">
        {value}{unit && <span className="stat-unit">{unit}</span>}
      </p>
    </div>
  )
}

function Row({ k, v }) {
  return (
    <div className="kv"><span className="kv-k">{k}</span>
      <span className="kv-v num">{v}</span></div>
  )
}