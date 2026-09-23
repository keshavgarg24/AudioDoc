'use client'

// Split-flap (Solari) board, for the one line a visitor actually came for.
//
// Every tool result opens with a single measured headline. Rendering it as a
// departure board buys attention for the number rather than for the chrome
// around it, and the settle doubles as a "this was measured just now" cue.
//
// Ported from the supplied Tailwind component, with two deliberate changes:
//
//   1. It is styled with the design system's own tokens instead of `dark:`
//      utilities. `dark:` in Tailwind v4 keys off prefers-color-scheme, and
//      this site is dark unconditionally - on a machine set to light mode the
//      original renders near-white text on a near-white board.
//   2. The scramble tint is drawn from the accent family rather than a
//      seven-colour rainbow, which would be the loudest thing on a page whose
//      whole colour rule is "purple means interactive".
//
// The board is decorative markup as far as assistive tech is concerned - a
// grid of single characters reads as gibberish - so the full string is also
// emitted into a visually hidden element and the grid is aria-hidden.

import React, { useEffect, useMemo, useRef, useState } from 'react'
import { motion } from 'motion/react'

const FLAP_CHARS = ' ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$()-+&=;:%,./?°'

const BASE_COL_DELAY = 30
const BASE_ROW_DELAY = 20
const BASE_STEP_MS = 55
const BASE_FLIP_S = 0.35

// Tint applied to a cell mid-scramble, never to a settled one.
const SCRAMBLE_TINTS = ['sf-tint-a', 'sf-tint-b', 'sf-tint-c']

function usePrefersReducedMotion() {
  const [reduced, setReduced] = useState(false)
  useEffect(() => {
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)')
    const sync = () => setReduced(mq.matches)
    sync()
    mq.addEventListener('change', sync)
    return () => mq.removeEventListener('change', sync)
  }, [])
  return reduced
}

/* ------------------------------------------------------------- one cell -- */

const FlapCell = React.memo(
  function FlapCell({ target, delay, stepMs, flipDuration, still }) {
    const [current, setCurrent] = useState(still ? target : ' ')
    const [prev, setPrev] = useState(' ')
    const [flipId, setFlipId] = useState(0)
    const [tint, setTint] = useState(null)
    const [prevTint, setPrevTint] = useState(null)

    const curRef = useRef(' ')
    const tgtRef = useRef(null)
    const tintRef = useRef(null)
    const startTimer = useRef(null)
    const stepTimer = useRef(null)
    const settleTimer = useRef(null)

    useEffect(() => {
      const normalized = FLAP_CHARS.includes(target.toUpperCase())
        ? target.toUpperCase()
        : ' '

      if (still) {
        curRef.current = normalized
        tgtRef.current = normalized
        setCurrent(normalized)
        return
      }

      clearTimeout(startTimer.current)
      clearTimeout(stepTimer.current)
      clearTimeout(settleTimer.current)

      if (normalized === tgtRef.current) return
      tgtRef.current = normalized
      if (normalized === ' ' && curRef.current === ' ') return

      const scrambleCount =
        normalized === ' '
          ? 6 + Math.floor(Math.random() * 6)
          : 18 + Math.floor(Math.random() * 12)

      const runStep = (i) => {
        const isLast = i === scrambleCount
        const ch = isLast
          ? normalized
          : FLAP_CHARS[1 + Math.floor(Math.random() * (FLAP_CHARS.length - 1))]
        const nextTint = isLast
          ? null
          : Math.random() < 0.18
            ? SCRAMBLE_TINTS[Math.floor(Math.random() * SCRAMBLE_TINTS.length)]
            : null

        setPrev(curRef.current)
        setPrevTint(tintRef.current)
        curRef.current = ch
        tintRef.current = nextTint
        setCurrent(ch)
        setTint(nextTint)
        setFlipId((n) => n + 1)

        if (!isLast) {
          stepTimer.current = setTimeout(() => runStep(i + 1), stepMs)
          return
        }

        // Drop the two flap overlays once the last flip has had time to land.
        // Leaving them mounted (as the source component does) means a settled
        // character is only legible while its transform sits where the
        // animation left it - and rAF stops in a backgrounded tab, so a board
        // that finishes while you are elsewhere can be found frozen mid-flip.
        // A settled cell is now just its two static halves.
        settleTimer.current = setTimeout(
          () => setFlipId(0), flipDuration * 1000 + 120)
      }

      startTimer.current = setTimeout(() => runStep(1), delay)

      return () => {
        clearTimeout(startTimer.current)
        clearTimeout(stepTimer.current)
        clearTimeout(settleTimer.current)
        tgtRef.current = null
      }
    }, [target, delay, stepMs, still, flipDuration])

    const show = current === ' ' ? ' ' : current
    const showPrev = prev === ' ' ? ' ' : prev
    const bottomDelay = flipDuration * 0.5

    return (
      <div className="sf-cell">
        <div className="sf-stage">
          {/* Settled character, top half. */}
          <div className={`sf-half sf-half--top ${tint || ''}`}>
            <span className="sf-glyph sf-glyph--top">{show}</span>
          </div>

          {/* Settled character, bottom half. */}
          <div className={`sf-half sf-half--bottom ${tint || ''}`}>
            <span className="sf-glyph sf-glyph--bottom">{show}</span>
          </div>

          {flipId > 0 && (
            <>
              {/* Old character's top half, falling. */}
              <motion.div
                key={flipId}
                className={`sf-flap sf-flap--top ${prevTint || ''}`}
                initial={{ rotateX: 0 }}
                animate={{ rotateX: -100 }}
                transition={{ duration: flipDuration, ease: [0.55, 0.055, 0.675, 0.19] }}
              >
                <span className="sf-glyph sf-glyph--top">{showPrev}</span>
                <span className="sf-shade sf-shade--fall" />
              </motion.div>

              {/* New character's bottom half, rising. */}
              <motion.div
                key={`b${flipId}`}
                className={`sf-flap sf-flap--bottom ${tint || ''}`}
                initial={{ rotateX: 90 }}
                animate={{ rotateX: 0 }}
                transition={{
                  duration: flipDuration * 0.85,
                  delay: bottomDelay,
                  ease: [0.33, 1.55, 0.64, 1],
                }}
              >
                <span className="sf-glyph sf-glyph--bottom">{show}</span>
              </motion.div>
            </>
          )}

          <div className="sf-split" />
        </div>
        <div className="sf-ribs" />
      </div>
    )
  },
  (a, b) =>
    a.target === b.target &&
    a.delay === b.delay &&
    a.stepMs === b.stepMs &&
    a.flipDuration === b.flipDuration &&
    a.still === b.still,
)

/* -------------------------------------------------------------- wrapping -- */

function wrapParagraph(paragraph, maxCols) {
  const lines = []
  let line = ''
  for (const word of paragraph.split(/[ \t]+/).filter(Boolean)) {
    if (word.length > maxCols) {
      if (line) { lines.push(line); line = '' }
      lines.push(word.slice(0, maxCols))
      continue
    }
    if (!line) line = word
    else if (line.length + 1 + word.length <= maxCols) line += ' ' + word
    else { lines.push(line); line = word }
  }
  if (line) lines.push(line)
  return lines
}

function wrapText(input, maxCols) {
  return input
    .split('\n')
    .flatMap((p) => (p.trim() === '' ? [''] : wrapParagraph(p, maxCols)))
}

/* ----------------------------------------------------------------- board -- */

export default function SplitFlapBoard({
  text = '',
  rows = 3,
  cols = 18,
  className = '',
  duration,
  label,
}) {
  const reduced = usePrefersReducedMotion()

  const baseTotal =
    ((cols - 1) * BASE_COL_DELAY + (rows - 1) * BASE_ROW_DELAY + 8 * BASE_STEP_MS) / 1000
  const scale = duration ? duration / baseTotal : 1
  const colDelay = BASE_COL_DELAY * scale
  const rowDelay = BASE_ROW_DELAY * scale
  const stepMs = BASE_STEP_MS * scale
  const flipDur = Math.min(0.6, Math.max(0.15, BASE_FLIP_S * scale))

  const grid = useMemo(() => {
    const g = Array.from({ length: rows }, () => Array.from({ length: cols }, () => ' '))
    const lines = wrapText(String(text).toUpperCase(), cols).slice(0, rows)
    const startRow = Math.max(0, Math.floor((rows - lines.length) / 2))
    lines.forEach((line, i) => {
      const r = startRow + i
      if (r >= rows) return
      const startCol = Math.max(0, Math.floor((cols - line.length) / 2))
      for (let c = 0; c < line.length && startCol + c < cols; c++) {
        g[r][startCol + c] = line[c]
      }
    })
    return g
  }, [text, rows, cols])

  return (
    <div className={`sf-board ${className}`.trim()}>
      <div
        className="sf-grid"
        aria-hidden="true"
        style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}
      >
        {grid.map((row, r) =>
          row.map((ch, c) => (
            <FlapCell
              key={`${r}-${c}`}
              target={ch}
              delay={c * colDelay + r * rowDelay}
              stepMs={stepMs}
              flipDuration={flipDur}
              still={reduced}
            />
          )),
        )}
      </div>
      <p className="sr-only">{label || text}</p>
    </div>
  )
}
