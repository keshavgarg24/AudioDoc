'use client'

// The landing hero graphic: a fanned stack of analysis panels that spreads as
// you point at it.
//
// This replaces the ported FeyCards block. The interaction is kept exactly as
// it was - same left-offset stagger, same spring, same hover-to-shift, same
// touch fallback - but the artwork is no longer six remote screenshots of a
// generic AI-generated dashboard pulled from assets.aceternity.com and a
// third-party Cloudinary account. Three reasons that had to change:
//
//   - it showed a product that is not this one, at a size where none of it is
//     legible, which is what made it read as a pasted screenshot rather than
//     as part of the page;
//   - it put six large cross-origin images on the critical path of the single
//     most important page here, all of them above the fold;
//   - it made the hero depend on two CDNs nobody here controls.
//
// The panels are inline SVG drawn from what the tools actually report, so they
// cost no requests, scale cleanly, and carry the site's own colours. Idle and
// active are the same drawing under a filter rather than two separate assets.

import { useEffect, useState } from 'react'
import { motion } from 'motion/react'

const CARD_SPACING = 34
const CARD_W = 170
const CARD_H = 480

const defaultSpring = { type: 'spring', visualDuration: 0.5, bounce: 0.2 }

const frame = (children) => (
  <svg viewBox={`0 0 ${CARD_W} ${CARD_H}`} className="hp-svg" aria-hidden="true"
       preserveAspectRatio="xMidYMid slice">
    <rect width={CARD_W} height={CARD_H} fill="#1e1e1e" />
    <rect x="14" y="18" width="52" height="6" rx="3" fill="#333" />
    <circle cx={CARD_W - 20} cy="21" r="3.5" fill="#333" />
    <line x1="0" y1="40" x2={CARD_W} y2="40" stroke="#262626" strokeWidth="1" />
    {children}
  </svg>
)

/* Panel 1 — the detection verdict, as a dial. */
const Verdict = () => {
  const r = 44
  const c = 2 * Math.PI * r
  return frame(
    <>
      <g transform={`translate(${CARD_W / 2} 130)`}>
        <circle r={r} fill="none" stroke="#282828" strokeWidth="9" />
        <circle r={r} fill="none" stroke="var(--accent)" strokeWidth="9"
                strokeLinecap="round" strokeDasharray={`${c * 0.76} ${c}`}
                transform="rotate(-90)" />
        <text y="6" className="hp-big">76%</text>
      </g>
      <text x={CARD_W / 2} y="200" className="hp-cap">CONFIDENCE</text>

      {[0, 1, 2, 3, 4].map((i) => (
        <g key={i} transform={`translate(0 ${240 + i * 44})`}>
          <rect x="16" y="0" width={78 - i * 9} height="6" rx="3" fill="#2e2e2e" />
          <rect x="16" y="16" width={CARD_W - 32} height="10" rx="5" fill="#232323" />
          <rect x="16" y="16" width={(CARD_W - 32) * (0.8 - i * 0.13)} height="10"
                rx="5" fill={i === 0 ? 'var(--green-2)' : '#3d3d3d'} />
        </g>
      ))}
    </>,
  )
}

/* Panel 2 — the per-window timeline. */
const Timeline = () => {
  const rows = [62, 41, 78, 55, 88, 33, 70, 47, 92, 58, 36, 74, 51, 66, 44]
  return frame(
    <>
      <text x="16" y="70" className="hp-cap hp-cap--left">TIMELINE</text>
      {rows.map((w, i) => (
        <g key={i} transform={`translate(16 ${86 + i * 24})`}>
          <rect width={CARD_W - 32} height="12" rx="6" fill="#212121" />
          <rect width={(CARD_W - 32) * (w / 100)} height="12" rx="6"
                fill={w > 80 ? 'var(--accent)' : w > 55 ? '#4a2a7a' : '#2f2f2f'} />
        </g>
      ))}
      <line x1="16" y1="452" x2={CARD_W - 16} y2="452" stroke="#2b2b2b" />
      <text x="16" y="468" className="hp-tiny hp-cap--left">0:00</text>
      <text x={CARD_W - 16} y="468" className="hp-tiny hp-cap--right">3:41</text>
    </>,
  )
}

/* Panel 3 — the spectrum. */
const Spectrum = () => {
  const bars = [22, 38, 30, 52, 44, 68, 58, 80, 71, 92, 84, 74, 66, 54, 47, 35, 28, 20]
  const bw = (CARD_W - 32) / bars.length
  return frame(
    <>
      <text x="16" y="70" className="hp-cap hp-cap--left">SPECTRUM</text>
      {[0, 1, 2, 3].map((i) => (
        <line key={i} x1="16" y1={140 + i * 70} x2={CARD_W - 16} y2={140 + i * 70}
              stroke="#232323" />
      ))}
      {bars.map((h, i) => (
        <rect key={i} x={16 + i * bw + 1} width={bw - 2}
              y={420 - h * 3} height={h * 3} rx="2"
              fill={h > 78 ? 'var(--accent-2)' : h > 55 ? '#4a2a7a' : '#333'} />
      ))}
      <line x1="16" y1="420" x2={CARD_W - 16} y2="420" stroke="#3a3a3a" />
      <text x="16" y="444" className="hp-tiny hp-cap--left">20 HZ</text>
      <text x={CARD_W - 16} y="444" className="hp-tiny hp-cap--right">20 KHZ</text>
    </>,
  )
}

/* Panel 4 — loudness against the delivery ceiling. */
const Loudness = () => {
  const platforms = [['SPOTIFY', 0.82], ['APPLE', 0.66], ['YOUTUBE', 0.74], ['TIKTOK', 0.58]]
  return frame(
    <>
      <text x="16" y="70" className="hp-cap hp-cap--left">LOUDNESS</text>
      <text x="16" y="118" className="hp-big hp-cap--left">−9.2</text>
      <text x="16" y="140" className="hp-tiny hp-cap--left">LUFS INTEGRATED</text>

      <rect x="16" y="164" width={CARD_W - 32} height="16" rx="8" fill="#212121" />
      <rect x="16" y="164" width={(CARD_W - 32) * 0.78} height="16" rx="8"
            fill="var(--accent)" />
      <line x1={16 + (CARD_W - 32) * 0.88} y1="158" x2={16 + (CARD_W - 32) * 0.88}
            y2="186" stroke="var(--yellow)" strokeWidth="2" />

      {platforms.map(([name, v], i) => (
        <g key={name} transform={`translate(16 ${226 + i * 56})`}>
          <text y="0" className="hp-tiny hp-cap--left">{name}</text>
          <rect y="10" width={CARD_W - 32} height="10" rx="5" fill="#212121" />
          <rect y="10" width={(CARD_W - 32) * v} height="10" rx="5"
                fill={v > 0.7 ? 'var(--green-2)' : '#3d3d3d'} />
        </g>
      ))}

      <rect x="16" y="452" width={CARD_W - 32} height="12" rx="6" fill="#212121" />
      <rect x="16" y="452" width={(CARD_W - 32) * 0.62} height="12" rx="6" fill="#333" />
    </>,
  )
}

/* Panel 5 — arrangement: waveform with section boundaries. */
const Structure = () => {
  const wave = Array.from({ length: 46 }, (_, i) =>
    18 + Math.round(46 * Math.abs(Math.sin(i * 0.55) * Math.cos(i * 0.19))))
  // Widths only. Five labels across 138px collide at any font size that is
  // still legible, so the sections are read by colour and the caption below
  // says what the colours mean.
  const sections = [[0.14, '#333'], [0.24, '#4a2a7a'], [0.2, 'var(--accent)'],
                    [0.22, '#4a2a7a'], [0.2, '#333']]
  let x = 16
  return frame(
    <>
      <text x="16" y="70" className="hp-cap hp-cap--left">ARRANGEMENT</text>

      <g transform="translate(0 200)">
        {wave.map((h, i) => (
          <rect key={i} x={16 + i * ((CARD_W - 32) / 46)} y={-h / 2}
                width={(CARD_W - 32) / 46 - 1} height={h} rx="1"
                fill={h > 50 ? '#5a5a5a' : '#343434'} />
        ))}
        <line x1="16" y1="0" x2={CARD_W - 16} y2="0" stroke="#2b2b2b" />
      </g>

      {sections.map(([w, fill], i) => {
        const width = (CARD_W - 32) * w
        const el = (
          <rect key={i} x={x} y="286" width={width - 2} height="18" rx="4" fill={fill} />
        )
        x += width
        return el
      })}
      <text x="16" y="322" className="hp-tiny hp-cap--left">INTRO · VERSE · DROP</text>

      <g transform="translate(16 360)">
        <text y="0" className="hp-tiny hp-cap--left">KEY</text>
        <text x={CARD_W - 32} y="0" className="hp-tiny hp-cap--right">F♯m · 11A</text>
        <text y="30" className="hp-tiny hp-cap--left">TEMPO</text>
        <text x={CARD_W - 32} y="30" className="hp-tiny hp-cap--right">128 BPM</text>
        <text y="60" className="hp-tiny hp-cap--left">SWING</text>
        <text x={CARD_W - 32} y="60" className="hp-tiny hp-cap--right">54%</text>
      </g>
    </>,
  )
}

const PANELS = [
  { key: 'verdict', label: 'Detection verdict', Art: Verdict },
  { key: 'timeline', label: 'Timeline across the track', Art: Timeline },
  { key: 'spectrum', label: 'Spectral measurements', Art: Spectrum },
  { key: 'loudness', label: 'Loudness and delivery', Art: Loudness },
  { key: 'structure', label: 'Arrangement, key and tempo', Art: Structure },
]

export default function HeroPanels({ spring = defaultSpring, shiftDistance = 60,
                                     entranceStagger = 0.2 }) {
  const [activeIndex, setActiveIndex] = useState(null)

  // Hover never fires on touch, so on a phone the stack would sit in its idle
  // state forever. Cycle it there instead, so the interaction is still visible.
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 900px)')
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)')
    let id = null
    const start = () => {
      if (id || reduce.matches) return
      let i = 0
      id = setInterval(() => {
        i = (i + 1) % PANELS.length
        setActiveIndex(i)
      }, 2500)
    }
    const stop = () => {
      if (id) { clearInterval(id); id = null }
      setActiveIndex(null)
    }
    const sync = () => (mq.matches ? start() : stop())
    sync()
    mq.addEventListener('change', sync)
    reduce.addEventListener('change', sync)
    return () => {
      mq.removeEventListener('change', sync)
      reduce.removeEventListener('change', sync)
      stop()
    }
  }, [])

  return (
    <div className="hero-panels-wrap">
      <motion.div
        className="hero-panels"
        variants={{ hidden: {}, visible: { transition: { staggerChildren: entranceStagger, staggerDirection: -1 } } }}
        initial="hidden"
        animate="visible"
      >
        {PANELS.map(({ key, label, Art }, index) => {
          const shouldShift = activeIndex !== null && index > activeIndex
          const isActive = activeIndex === index
          return (
            <motion.button
              type="button"
              key={key}
              className={`hp-card${isActive ? ' is-active' : ''}`}
              style={{ left: index * CARD_SPACING }}
              aria-label={label}
              variants={{
                hidden: (offset) => ({ x: offset }),
                visible: { x: 0, transition: spring },
              }}
              custom={-index * CARD_SPACING}
              onMouseEnter={() => setActiveIndex(index)}
              onMouseLeave={() => setActiveIndex(null)}
              onFocus={() => setActiveIndex(index)}
              onBlur={() => setActiveIndex(null)}
              onClick={() => setActiveIndex((c) => (c === index ? null : index))}
            >
              <motion.span
                className="hp-card-inner"
                animate={{ x: shouldShift ? shiftDistance : 0 }}
                transition={spring}
              >
                <Art />
              </motion.span>
            </motion.button>
          )
        })}
      </motion.div>
    </div>
  )
}
