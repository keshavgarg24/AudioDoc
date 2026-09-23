export const pct = (v, d = 1) => `${((v ?? 0) * 100).toFixed(d)}%`
export const num = (v, d = 2) => Number(v ?? 0).toFixed(d)

export const clock = (s) => {
  const t = Math.max(0, Math.round(s || 0))
  return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, '0')}`
}

export const bytes = (b) =>
  b < 1024 ? `${b} B` : b < 1048576 ? `${(b / 1024).toFixed(0)} KB`
    : `${(b / 1048576).toFixed(1)} MB`

export const hz = (v) => (v >= 1000 ? `${(v / 1000).toFixed(1)} kHz` : `${Math.round(v)} Hz`)

/** Monochrome magnitude ramp: 0 -> near-black, 1 -> white.
 *  Gamma-corrected so mid values stay distinguishable on a black surface. */
export const mono = (t) => {
  const k = Math.max(0, Math.min(1, t))
  const v = Math.round(255 * Math.pow(k, 0.75))
  return `rgb(${v}, ${v}, ${v})`
}

/** Lightness for a diverging value about 0.5. Both poles are light against
 *  the black surface; the AI/human distinction is carried by fill TEXTURE and
 *  position relative to the midline, never by hue. */
export const divergingMono = (p) => {
  const d = Math.abs((p ?? 0) - 0.5) * 2      // 0 at the midpoint, 1 at either end
  return mono(0.28 + d * 0.72)
}

export const isFakeLeaning = (p) => (p ?? 0) > 0.5
