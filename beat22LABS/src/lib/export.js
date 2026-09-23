import { clock, hz, num, pct } from './format.js'

function save(blob, filename) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  // Revoke on the next tick so Safari has finished reading the blob.
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

const stem = (r) =>
  (r.source?.filename || 'report').replace(/\.[^.]+$/, '').replace(/[^\w.-]+/g, '_')

export function exportJson(report) {
  save(new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' }),
       `${stem(report)}-beat22labs.json`)
}

/** Print to PDF. The browser's own engine gives far better type and vector
 *  output than a canvas rasteriser, and needs no dependency. The print rules
 *  in styles.css restyle the page for paper. */
export function exportPdf() {
  window.print()
}

const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))

const MODE_LABEL = {
  ai: 'AI Detection Report',
  audio: 'Audio Analysis Report',
  full: 'Complete Analysis Report',
}

/** Self contained single file HTML report. No scripts and no external assets
 *  beyond the webfont, so it opens anywhere and prints cleanly.
 *
 *  Every section is guarded: which data exists depends on the analysis mode,
 *  so nothing here may assume the detection or the audio half is present. */
export function exportHtml(report) {
  const r = report
  const f = r.features || {}
  const mus = r.musical || {}
  const prod = r.production || {}
  const ch = r.character || {}

  const ind = r.industry || {}
  const det = r.detection || {}

  const sp = f.spectral, dy = f.dynamics, tn = f.tonal, on = f.onsets
  const harmony = mus.harmony, groove = mus.groove, mrhythm = mus.rhythm
  const arrangement = mus.arrangement, drums = mus.drums
  const loud = prod.loudness, stereo = prod.stereo
  const enc = prod.encoding, sd = prod.sound_design, ready = prod.release_readiness
  const cat = ind.catalogue_features, qc = ind.quality_control
  const vocal = ind.vocal_presence, beat = ind.structure

  const kv = (k, v) =>
    (v === undefined || v === null || v === '') ? ''
      : `<tr><th>${esc(k)}</th><td>${esc(v)}</td></tr>`

  const findingRows = (list) => (list || []).map((x) => `
    <div class="f">
      <h4>${esc(x.title)}${x.flag && x.flag !== 'neutral'
        ? `<span class="flag ${x.flag === 'synthetic-leaning' ? 'syn' : 'hum'}">${
            esc(x.flag)}</span>` : ''}</h4>
      <p>${esc(x.detail)}</p>
    </div>`).join('')

  // ---- verdict block, detection modes only -------------------------------
  const verdictBlock = r.prediction ? `
<h2>Verdict</h2>
<h3>${r.prediction === 'Fake' ? 'AI-generated' : 'Human-made'}</h3>
<div class="conf">${num(r.confidence, 1)}% confidence</div>
<div class="bar">
  <div class="a" style="width:${r.fake_probability * 100}%"></div>
  <div class="h" style="width:${r.real_probability * 100}%"></div>
</div>
<div class="lg"><span>AI-generated ${pct(r.fake_probability)}</span>
                <span>Human-made ${pct(r.real_probability)}</span></div>
${r.reliability ? `<h2>Reliability, ${esc(r.reliability.label)} (${num(r.reliability.score, 0)} of 100)</h2>
<p>${(r.reliability.notes || []).map(esc).join(' ')}</p>` : ''}` : ''

  const timelineChart = r.timeline?.segments?.length ? (() => {
    const segs = r.timeline.segments
    const W = 760, H = 120, PL = 30, PR = 8, PT = 6, PB = 18
    const pw2 = W - PL - PR, ph2 = H - PT - PB
    const bw2 = pw2 / segs.length
    const yp = (p) => PT + (1 - p) * ph2
    const mid = yp(0.5)
    const bars = segs.map((s, i) => {
      const p = s.fake_probability
      const top = Math.min(yp(p), mid)
      const h = Math.max(1, Math.abs(yp(p) - mid))
      return `<rect x="${PL + i * bw2 + 0.5}" y="${top}" width="${Math.max(1, bw2 - 1)}" height="${h}" rx="1.5" fill="${p > 0.5 ? '#000' : '#bbb'}" />`
    }).join('')
    return `<svg viewBox="0 0 ${W} ${H}" style="max-width:100%;height:auto;margin:8px 0 12px">
      <line x1="${PL}" x2="${W - PR}" y1="${mid}" y2="${mid}" stroke="#ccc" stroke-dasharray="4 3" />
      ${bars}
      <text x="${PL}" y="${H - 4}" style="font-size:9px;fill:#888">${clock(segs[0].start)}</text>
      <text x="${W - PR}" y="${H - 4}" style="font-size:9px;fill:#888" text-anchor="end">${clock(segs[segs.length - 1].end)}</text>
    </svg>`
  })() : ''

  const timelineBlock = r.timeline?.segments?.length ? `
<h2>Per window timeline</h2>
${timelineChart}
<table class="tl"><thead><tr><th>Window</th><th>Start</th><th>End</th>
<th>AI probability</th><th>Leaning</th></tr></thead><tbody>
${r.timeline.segments.map((s, i) =>
    `<tr><td>${i + 1}</td><td>${clock(s.start)}</td><td>${clock(s.end)}</td>
     <td>${num(s.fake_probability, 4)}</td><td>${esc(s.leaning)}</td></tr>`).join('')}
</tbody></table>` : ''

  // ---- deeper verification ----------------------------------------------
  const v = det.verification
  const verificationBlock = (v || det.verification_error) ? `
<h2>Deeper verification</h2>
${det.verification_error
    ? `<p>Not available: ${esc(det.verification_error.message)}</p>`
    : `${det.consensus ? `<p><strong>${esc(det.consensus.detail)}</strong></p>` : ''}
<table>
${kv('Result', v.prediction === 'ai_generated' ? 'AI-generated' : 'Human-made, confirmed by deep analysis')}
${v.prediction === 'ai_generated' ? kv('AI probability', `${num(v.ai_probability, 2)}%`) : ''}
${v.prediction === 'ai_generated' && v.likely_source ? kv('Likely source', v.likely_source) : ''}
${det.consensus ? kv('Agreement with primary', det.consensus.agreement) : ''}
${det.consensus ? kv('Combined verdict', det.consensus.verdict) : ''}
${det.consensus ? kv('Recommended action', det.consensus.recommended_action) : ''}
${det.escalation ? kv('Why it ran', det.escalation.detail) : ''}
</table>
${v.prediction === 'ai_generated' && (v.source_probabilities || []).length ? `
<h3 style="font-size:11px;color:#666;margin:14px 0 4px;text-transform:uppercase;letter-spacing:.1em">Source attribution</h3>
<table>${v.source_probabilities.map(
      (sp2) => kv(sp2.source, `${num(sp2.probability, 2)}%`)).join('')}</table>` : ''}
${v.prediction === 'ai_generated' && (v.segments || []).length > 1 ? `
<h3 style="font-size:11px;color:#666;margin:14px 0 4px;text-transform:uppercase;letter-spacing:.1em">Per segment</h3>
<table class="tl"><thead><tr><th>Start</th><th>End</th><th>Result</th><th>AI probability</th></tr></thead>
<tbody>${v.segments.map((s) =>
      `<tr><td>${clock(s.start)}</td><td>${clock(s.end)}</td>
       <td>${esc(s.prediction)}</td><td>${num(s.ai_probability, 2)}%</td></tr>`).join('')}
</tbody></table>` : ''}`}` : ''

  // ---- readiness ---------------------------------------------------------
  const readyBlock = ready ? `
<h2>Release readiness, ${esc(ready.status)}</h2>
<ul>${(ready.issues || []).map((i) => `<li>${esc(i)}</li>`).join('')}</ul>` : ''

  // ---- quality control and catalogue features ----------------------------
  const qcBlock = qc ? `
<h2>Quality control, ${esc(qc.gate)}</h2>
<p>${esc(qc.summary)}</p>
<table class="tl"><thead><tr><th>Check</th><th>Status</th><th>Detail</th></tr></thead>
<tbody>${(qc.checks || []).map((c) =>
    `<tr><td>${esc(c.check)}</td><td>${esc(c.status)}</td>
     <td style="text-align:left">${esc(c.detail)}</td></tr>`).join('')}
</tbody></table>` : ''

  const catBlock = cat ? `
<h2>Catalogue features</h2>
<p style="font-size:11px;color:#666">${esc(cat.note || '')}</p>
<div class="grid">
<table>
${kv('Energy', num(cat.energy, 2))}
${kv('Danceability', num(cat.danceability, 2))}
${kv('Valence', num(cat.valence, 2))}
${kv('Acousticness', num(cat.acousticness, 2))}
</table>
<table>
${kv('Instrumentalness', num(cat.instrumentalness, 2))}
${kv('Speechiness', num(cat.speechiness, 2))}
${kv('Liveness', num(cat.liveness, 2))}
${vocal ? kv('Vocal presence', vocal.verdict) : ''}
</table>
</div>
${beat ? `<table>
${beat.intro_length_s != null ? kv('Intro length', `${num(beat.intro_length_s, 1)} s`) : ''}
${beat.first_peak_s != null ? kv('First peak at', clock(beat.first_peak_s)) : ''}
${beat.total_bars != null ? kv('Total bars', beat.total_bars) : ''}
${beat.hook_hint ? kv('Hook', beat.hook_hint) : ''}
</table>` : ''}` : ''

  // ---- delivery targets --------------------------------------------------
  const targetBlock = loud?.platform_targets?.length ? `
<h2>Delivery targets</h2>
<table><thead><tr><th>Platform</th><th>Target</th><th>Delta</th><th>Verdict</th></tr></thead>
<tbody>${loud.platform_targets.map((t) =>
    `<tr><td>${esc(t.platform)}</td><td>${num(t.target_lufs, 1)} LUFS</td>
     <td>${t.delta_lu > 0 ? '+' : ''}${num(t.delta_lu, 1)} LU</td>
     <td>${esc(t.verdict)}</td></tr>`).join('')}</tbody></table>` : ''

  // ---- arrangement -------------------------------------------------------
  const arrangeBlock = arrangement?.sections?.length ? `
<h2>Arrangement</h2>
<table><thead><tr><th>Section</th><th>Start</th><th>End</th><th>Energy</th></tr></thead>
<tbody>${arrangement.sections.map((s) =>
    `<tr><td>${esc(s.label)}</td><td>${clock(s.start)}</td><td>${clock(s.end)}</td>
     <td>${num(s.energy, 2)}</td></tr>`).join('')}</tbody></table>
<p>${esc(arrangement.phrasing_note)}</p>` : ''

  const spectrumChart = sp?.spectrum?.length ? (() => {
    const pts = sp.spectrum
    const W = 760, H2 = 140, PL2 = 36, PR2 = 8, PT2 = 10, PB2 = 22
    const pw3 = W - PL2 - PR2, ph3 = H2 - PT2 - PB2
    const minDb = -90, maxDb = 2
    const xp = (i) => PL2 + (i / (pts.length - 1)) * pw3
    const yp = (db) => PT2 + (1 - (Math.max(minDb, db) - minDb) / (maxDb - minDb)) * ph3
    const line = pts.map((p, i) => `${i ? 'L' : 'M'}${xp(i)},${yp(p.db)}`).join(' ')
    const area = `${line} L${xp(pts.length - 1)},${PT2 + ph3} L${PL2},${PT2 + ph3} Z`
    return `<svg viewBox="0 0 ${W} ${H2}" style="max-width:100%;height:auto;margin:8px 0">
      ${[-80, -60, -40, -20, 0].map((db) =>
        `<line x1="${PL2}" x2="${W - PR2}" y1="${yp(db)}" y2="${yp(db)}" stroke="#e8e8e8" />
         <text x="${PL2 - 4}" y="${yp(db) + 3}" text-anchor="end" style="font-size:8px;fill:#888">${db}</text>`).join('')}
      <path d="${area}" fill="rgba(0,0,0,0.06)" />
      <path d="${line}" fill="none" stroke="#000" stroke-width="1.5" stroke-linejoin="round" />
    </svg>`
  })() : ''

  const radarChart = ch.radar?.length ? (() => {
    const axes = ch.radar
    const CX = 110, CY = 100, R = 70
    const n = axes.length
    const pt = (i, r2) => {
      const a = (Math.PI * 2 * i) / n - Math.PI / 2
      return [CX + Math.cos(a) * R * r2, CY + Math.sin(a) * R * r2]
    }
    const rings = [0.25, 0.5, 0.75, 1].map((r2) =>
      `<polygon points="${axes.map((_, i) => pt(i, r2).join(',')).join(' ')}" fill="none" stroke="#e0e0e0" />`).join('')
    const shape = `<polygon points="${axes.map((a, i) => pt(i, a.value).join(',')).join(' ')}" fill="rgba(0,0,0,0.08)" stroke="#000" stroke-width="1.5" />`
    const labels = axes.map((a, i) => {
      const [lx, ly] = pt(i, 1.25)
      const anchor = lx < CX - 5 ? 'end' : lx > CX + 5 ? 'start' : 'middle'
      return `<text x="${lx}" y="${ly + 3}" text-anchor="${anchor}" style="font-size:8px;fill:#555">${esc(a.axis)}</text>`
    }).join('')
    const dots = axes.map((a, i) => {
      const [dx, dy] = pt(i, a.value)
      return `<circle cx="${dx}" cy="${dy}" r="2.5" fill="#000" />`
    }).join('')
    return `<svg viewBox="0 0 220 200" style="max-width:220px;height:auto;margin:8px 0">
      ${rings}${shape}${dots}${labels}
    </svg>`
  })() : ''

  const bandBlock = sp?.bands?.length ? `
<h2>Frequency band distribution</h2><table>
${sp.bands.map((b) => kv(`${b.name} (${b.low} to ${b.high} Hz)`, pct(b.share, 1))).join('')}
</table>` : ''

  const chordBlock = harmony?.progression?.length ? `
<h2>Harmony</h2>
<p class="chords">${harmony.progression.map((c) => `<span>${esc(c)}</span>`).join('')}</p>
<table>
${kv('Key', harmony.key)}
${kv('Camelot', harmony.camelot)}
${kv('Key confidence', num(harmony.key_correlation, 3))}
${kv('Scale conformance', pct(harmony.scale_conformance, 0))}
${kv('Harmonic rhythm', `${num(harmony.harmonic_rhythm_per_bar, 2)} per bar`)}
</table>` : ''

  const metaBits = [
    esc(r.source.filename),
    clock(r.source.duration_seconds),
    mrhythm?.bpm ? `${num(mrhythm.bpm, 0)} BPM` : (r.rhythm?.bpm ? `${num(r.rhythm.bpm, 0)} BPM` : null),
    harmony?.key || tn?.key || null,
    harmony?.camelot || null,
    `analysed in ${num(r.runtime.elapsed_seconds, 0)}s`,
  ].filter(Boolean).join(' &middot; ')

  const html = `<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Beat22 LABS, ${esc(r.source.filename)}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="https://rsms.me/inter/inter.css">
<style>
  :root{--l:#dcdcdc}
  *{box-sizing:border-box}
  body{font-family:'Inter',system-ui,sans-serif;background:#fff;color:#000;
       margin:0;padding:40px 24px;font-feature-settings:'tnum' 1;
       -webkit-font-smoothing:antialiased}
  .wrap{max-width:840px;margin:0 auto}
  header{border-bottom:1px solid #000;padding-bottom:14px;margin-bottom:22px}
  .logo{font-size:20px;font-weight:800;letter-spacing:-.045em;text-align:right}
  .logo span{font-weight:300;color:#666}
  .sub{font-size:9.5px;letter-spacing:.2em;text-transform:uppercase;color:#666;margin-top:4px}
  h2{font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:#666;
     margin:30px 0 10px;border-bottom:1px solid var(--l);padding-bottom:6px;font-weight:600}
  h3{font-size:42px;letter-spacing:-.04em;margin:6px 0 0;font-weight:750}
  h4{font-size:13px;margin:0 0 4px;font-weight:650}
  p{font-size:12.5px;line-height:1.6;color:#222;margin:0}
  ul{font-size:12.5px;line-height:1.6;color:#222;margin:6px 0;padding-left:18px}
  .meta{font-size:11px;color:#555;margin-top:8px}
  .oneliner{font-size:14px;color:#333;margin:14px 0 0}
  .conf{font-size:21px;font-weight:650;margin-top:10px}
  .bar{display:flex;height:10px;border-radius:999px;overflow:hidden;
       background:#eee;margin:12px 0 6px}
  .bar .a{background:#000}
  .bar .h{background:repeating-linear-gradient(135deg,#999 0 2px,#e8e8e8 2px 5px)}
  .lg{display:flex;justify-content:space-between;font-size:11px;color:#444}
  table{width:100%;border-collapse:collapse;font-size:11.5px;margin-top:8px}
  th,td{text-align:left;padding:5px 8px;border-bottom:1px solid #eee}
  td{text-align:right}
  th{color:#555;font-weight:600}
  thead th{text-align:left}
  table.tl td,table.tl th{text-align:right}
  table.tl td:first-child,table.tl th:first-child{text-align:left}
  .f{padding:11px 0;border-top:1px solid #eee}
  .f:first-of-type{border-top:0}
  .flag{font-size:9px;letter-spacing:.07em;text-transform:uppercase;padding:2px 5px;
        border-radius:3px;margin-left:7px;vertical-align:middle;font-weight:600}
  .flag.syn{background:#000;color:#fff}
  .flag.hum{background:#eee;color:#333}
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:0 28px}
  .chords span{display:inline-block;font-family:ui-monospace,Menlo,monospace;
       font-size:10.5px;border:1px solid var(--l);border-radius:3px;
       padding:2px 5px;margin:0 4px 4px 0}
  footer{margin-top:32px;padding-top:12px;border-top:1px solid var(--l);
         font-size:10.5px;color:#666}
  @media print{@page{margin:14mm}body{padding:0}
    .f,table,h2{page-break-inside:avoid}h2{page-break-after:avoid}}
</style></head><body><div class="wrap">
<header>
  <div class="logo">beat22<span>LABS</span></div>
  <div class="sub">${esc(MODE_LABEL[r.mode] || 'Analysis Report')}</div>
</header>

<p class="meta">${metaBits}</p>
${ch.summary ? `<p class="oneliner">${esc(ch.summary)}</p>` : ''}
${verdictBlock}
${r.summary && !r.prediction ? `<p style="margin-top:12px">${esc(r.summary)}</p>` : ''}

${(r.findings || []).length ? `<h2>Model findings</h2>${findingRows(r.findings)}` : ''}
${(r.signal_findings || []).length ? `<h2>Signal forensics</h2>
${r.signal_summary ? `<p style="margin-bottom:10px"><em>${esc(r.signal_summary.note)}</em></p>` : ''}
${findingRows(r.signal_findings)}` : ''}

${verificationBlock}
${readyBlock}
${qcBlock}

<h2>Measurements</h2>
<div class="grid">
<table>
${r.timeline ? kv('Windows analysed', r.timeline.count) : ''}
${r.timeline ? kv('Leaning AI', `${r.timeline.fake_segments} of ${r.timeline.count}`) : ''}
${r.timeline ? kv('Mean window probability', num(r.timeline.mean_fake_probability, 4)) : ''}
${r.raw_logit !== undefined ? kv('Raw logit', num(r.raw_logit, 3)) : ''}
${r.structure ? kv('Homogeneity', num(r.structure.homogeneity, 3)) : ''}
${r.structure ? kv('Structural contrast', num(r.structure.structural_contrast, 3)) : ''}
${mrhythm ? kv('Notated tempo', `${num(mrhythm.bpm, 2)} BPM`) : ''}
${mrhythm ? kv('Tracked pulse', `${num(mrhythm.bpm_tracked, 2)} BPM`) : ''}
${mrhythm ? kv('Beat jitter', `${num(mrhythm.beat_jitter_ms, 2)} ms`) : ''}
${groove?.available ? kv('Grid', groove.grid) : ''}
${groove?.available ? kv('Swing', `${num(groove.swing_pct, 1)}%`) : ''}
${groove?.available ? kv('Quantisation', groove.quantization_class) : ''}
${groove?.available ? kv('Timing deviation', `${num(groove.mean_abs_deviation_ms, 1)} ms`) : ''}
${drums?.available ? kv('Drum hits', drums.total_hits) : ''}
${drums?.available ? kv('Kick pattern', drums.kick_pattern) : ''}
</table>
<table>
${loud ? kv('Integrated loudness', `${num(loud.integrated_lufs, 2)} LUFS`) : ''}
${loud ? kv('True peak', `${num(loud.true_peak_dbtp, 2)} dBTP`) : ''}
${loud ? kv('Loudness range', `${num(loud.loudness_range_lu, 2)} LU`) : ''}
${loud ? kv('Peak to loudness', `${num(loud.plr_db, 2)} dB`) : ''}
${loud ? kv('Clipped samples', loud.clipped_samples) : ''}
${dy ? kv('Crest factor', `${num(dy.crest_factor_db, 1)} dB`) : ''}
${dy ? kv('Dynamic range', `${num(dy.dynamic_range_db, 1)} dB`) : ''}
${sp ? kv('Spectral centroid', hz(sp.centroid_hz)) : ''}
${sp ? kv('High frequency ceiling', hz(sp.ceiling_hz)) : ''}
${sp ? kv('Roll off cliff', `${num(sp.rolloff_cliff_db, 1)} dB`) : ''}
${stereo?.is_stereo ? kv('Phase correlation', num(stereo.correlation, 3)) : ''}
${stereo?.is_stereo ? kv('Stereo width', `${num(stereo.width_pct, 1)}%`) : ''}
${stereo?.is_stereo ? kv('Low end mono', stereo.low_end_mono ? 'yes' : 'no') : ''}
${on ? kv('Onset density', `${num(on.onset_density, 2)} per second`) : ''}
</table>
</div>

${spectrumChart ? `<h2>Frequency response</h2>${spectrumChart}` : ''}
${targetBlock}
${chordBlock}
${arrangeBlock}
${bandBlock}
${catBlock}
${radarChart ? `<h2>Character profile</h2>${radarChart}` : ''}

${enc ? `<h2>Encoding history</h2><table>
${kv('Container', enc.container_format)}
${kv('Codec', enc.codec)}
${enc.declared_bitrate_bps ? kv('Declared bitrate', `${Math.round(enc.declared_bitrate_bps / 1000)} kbps`) : ''}
${enc.lowpass_shelf_hz ? kv('Lowpass shelf', hz(enc.lowpass_shelf_hz)) : ''}
${kv('Inferred history', enc.inferred_encoding_history)}
${kv('Transcode suspected', enc.transcode_suspected ? 'yes' : 'no')}
</table>` : ''}

${sd && Object.keys(sd).length ? `<h2>Sound design</h2><table>
${sd.reverb_rt60_ms ? kv('Reverb RT60', `${num(sd.reverb_rt60_ms, 0)} ms`) : ''}
${kv('Space', sd.reverb_character)}
${sd.thd_ratio != null ? kv('Harmonic distortion', pct(sd.thd_ratio, 1)) : ''}
${kv('Saturation', sd.saturation)}
${sd.modulation_hz?.length ? kv('Modulation', sd.modulation_hz.map((m) => `${num(m, 2)} Hz`).join(', ')) : ''}
</table>` : ''}

${ch.moods?.length ? `<h2>Mood</h2>
${ch.moods.map((m) => `<div style="display:flex;align-items:center;gap:8px;margin:4px 0">
  <span style="font-size:11px;width:90px;flex:none">${esc(m.mood)}</span>
  <span style="flex:1;height:8px;background:#eee;border-radius:4px;overflow:hidden">
    <span style="display:block;width:${m.weight * 100}%;height:100%;background:#000;border-radius:4px"></span>
  </span>
  <span style="font-size:10px;width:32px;text-align:right">${num(m.weight, 2)}</span>
</div>`).join('')}
${ch.vocal_space ? `<table style="margin-top:12px">
${kv('Midrange occupancy', `${ch.vocal_space.midrange_occupancy_pct}%`)}
${kv('Vocal space', ch.vocal_space.verdict)}
</table>` : ''}
${ch.suitable_for?.length ? `<p style="margin-top:8px">Suited for: ${
  ch.suitable_for.map(esc).join('; ')}.</p>` : ''}` : ''}

${timelineBlock}

<footer>
  Generated by Beat22 LABS.
  ${r.prediction ? 'Detection across beat aligned windows of the full track. ' : ''}
  Statistical analysis is evidence, not proof of origin. Weigh any verdict
  against its reliability score and treat a single indicator as supporting
  evidence rather than a conclusion.
</footer>
</div></body></html>`

  save(new Blob([html], { type: 'text/html' }), `${stem(r)}-beat22labs.html`)
}
