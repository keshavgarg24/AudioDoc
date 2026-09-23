// The one line from each tool's payload that is worth putting on the board.
//
// The split-flap board at the top of a result is not a summary - it is the
// single figure the visitor opened the tool to get. Everything else stays in
// the tables below it. Each entry returns short lines because the board wraps
// on whole words and truncates past its row count; keeping them under about
// sixteen characters is what stops a line dropping off.
//
// `label` is what a screen reader gets, so it is written as a sentence rather
// than as board text.

const num = (v, d = 1) =>
  v === null || v === undefined || Number.isNaN(v) ? null : Number(v).toFixed(d)

const ok = (v) => v !== null && v !== undefined && v !== '' && v !== '--'

/** @returns {{lines: string[], label: string} | null} */
export function resultHeadline(slug, d) {
  if (!d) return null

  switch (slug) {
    case 'master-check': {
      const lufs = num(d.loudness?.integrated_lufs)
      if (!ok(lufs)) return null
      const tp = num(d.loudness?.true_peak_dbtp, 1)
      const status = d.headline?.status
      return {
        lines: [
          `${lufs} LUFS`,
          ok(tp) ? `${tp} DBTP` : '',
          ok(status) ? String(status).toUpperCase() : '',
        ].filter(Boolean),
        label: `Integrated loudness ${lufs} LUFS, true peak ${tp} dBTP${
          ok(status) ? `, ${status}` : ''}.`,
      }
    }

    case 'tempo-lab': {
      const bpm = num(d.tempo?.bpm, 0)
      if (!ok(bpm)) return null
      const sig = d.meter?.time_signature
      const conf = d.tempo?.confidence?.label
      return {
        lines: [
          `${bpm} BPM`,
          ok(sig) ? String(sig) : '',
          ok(conf) ? `${conf} confidence` : '',
        ].filter(Boolean),
        label: `${bpm} BPM${ok(sig) ? `, ${sig}` : ''}${
          ok(conf) ? `, ${conf} confidence` : ''}.`,
      }
    }

    case 'key-lab': {
      const name = d.key?.name
      if (!ok(name)) return null
      const camelot = d.key?.camelot
      return {
        lines: [String(name), ok(camelot) ? `CAMELOT ${camelot}` : ''].filter(Boolean),
        label: `Key ${name}${ok(camelot) ? `, Camelot ${camelot}` : ''}.`,
      }
    }

    case 'reference-match': {
      const score = num(d.headline?.match_score, 0)
      if (!ok(score)) return null
      const gap = num(d.loudness?.delta?.integrated_lu)
      return {
        lines: [
          'MATCH',
          `${score} / 100`,
          ok(gap) ? `${gap > 0 ? '+' : ''}${gap} LU GAP` : '',
        ].filter(Boolean),
        label: `Match score ${score} out of 100.`,
      }
    }

    case 'vocal-lab': {
      const cents = num(d.pitch?.tuning?.mean_abs_cents, 0)
      if (!ok(cents)) return null
      const range = d.pitch?.range?.label
      return {
        lines: [
          `${cents} CENTS OFF`,
          ok(d.pitch?.tuning?.label) ? String(d.pitch.tuning.label) : '',
          ok(range) ? String(range) : '',
        ].filter(Boolean),
        label: `Pitch accuracy ${cents} cents${ok(range) ? `, ${range} range` : ''}.`,
      }
    }

    case 'beat-vocal-fit': {
      const fit = num(d.headline?.fit_score, 0)
      if (!ok(fit)) return null
      const band = d.headline?.worst_masking_band
      return {
        lines: ['FIT', `${fit} / 100`, ok(band) ? `CLASH ${band}` : ''].filter(Boolean),
        label: `Fit score ${fit} out of 100${ok(band) ? `, worst clash at ${band}` : ''}.`,
      }
    }

    default:
      return null
  }
}
