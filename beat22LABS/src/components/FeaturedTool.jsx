'use client'

// The rotating spotlight on the tools.
//
// This used to be a fixed panel for Beat and Vocal Fit, which meant six of the
// seven tools never got a sentence of their own anywhere above the card grid.
// It now cycles through all seven: same layout, same comet-card tilt, content
// swapped on a timer with a cross-fade.
//
// Rules the rotation follows:
//   - it pauses while the pointer or keyboard focus is inside the section, so
//     it never yanks the copy out from under someone reading it;
//   - it does not rotate at all under prefers-reduced-motion, and the dots
//     still work, so the content is reachable without the animation;
//   - the dots are real buttons with labels, not decoration.

import React, { useCallback, useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { AnimatePresence, motion } from 'motion/react'
import { CometCard } from './ui/comet-card.jsx'
import ToolVisual from './visuals/ToolVisual.jsx'
import HonestyModal from './HonestyModal.jsx'
import { TOOLS } from '../toolConfig.js'

const ROTATE_MS = 2500

// Beat and Vocal Fit leads, because it is the most differentiated thing here
// and the hardest to do by ear. The rest follow in the order they are used.
const ORDER = [
  'beat-vocal-fit', 'master-check', 'reference-match',
  'vocal-lab', 'tempo-lab', 'key-lab',
]

// Per-tool pitch. `title` is deliberately two short lines - it sits at display
// size and a third line pushes the card below the fold on a laptop. `example`
// is illustrative output, labelled as such on the card.
const PITCH = {
  'beat-vocal-fit': {
    title: ['Your vocal is not buried.', 'It is being masked.'],
    sub: 'Upload the beat and the vocal as separate files. Get the exact frequency '
      + 'band where they compete, the semitone shift that puts them in the same key, '
      + 'and how far off the grid the take sits.',
    points: [
      'Where the beat buries the vocal, in dB',
      'Semitones to shift, in the smaller direction',
      'Milliseconds the vocal sits off the grid',
      'Level gap against the norm for your genre',
    ],
    example: [['Clash', '2–3 kHz'], ['Key shift', '+2 st'], ['Nudge', '−40 ms']],
  },
  'master-check': {
    title: ['Loud is not the same', 'as loud on Spotify.'],
    sub: 'Every platform turns you down to its own target. See your integrated '
      + 'loudness and true peak measured to the broadcast standard, and exactly '
      + 'how many dB each service will take off before anyone hears it.',
    points: [
      'Integrated LUFS to ITU-R BS.1770-4',
      'True peak at 4x oversampling',
      'Per-platform gain change, in dB',
      'Delivery blockers before a distributor finds them',
    ],
    example: [['Integrated', '−9.2 LUFS'], ['True peak', '−0.4 dBTP'], ['Spotify', '−4.8 dB']],
  },
  'reference-match': {
    title: ['Hear the difference,', 'then read it in dB.'],
    sub: 'Put your track next to a reference you already trust. The gap is '
      + 'reported band by band, in decibels, with the loudness difference '
      + 'removed first so you are comparing tone and not level.',
    points: [
      'Band-by-band difference, in dB',
      'Loudness matched before comparing',
      'Dynamics and stereo width gaps',
      'Ordered by how much each one matters',
    ],
    example: [['Match', '82 / 100'], ['Low mid', '+2.4 dB'], ['Width', '−9%']],
  },
  'vocal-lab': {
    title: ['Every take has a number.', 'This is yours.'],
    sub: 'Pitch accuracy in cents, the range you actually sing in rather than '
      + 'the range you can reach, vibrato rate, and the recording problems that '
      + 'survive a mix.',
    points: [
      'Mean pitch error, in cents',
      'Comfortable range against full range',
      'Vibrato rate and depth',
      'Sibilance, plosives and noise floor',
    ],
    example: [['Accuracy', '18 cents'], ['Range', 'A2–E5'], ['Vibrato', '5.4 Hz']],
  },
  'tempo-lab': {
    title: ['A BPM number', 'leaves half of it out.'],
    sub: 'The tempo, plus the parts a counter cannot tell you: whether the track '
      + 'reads equally at half or double time, whether it drifts, and how far '
      + 'the performance sits off the grid.',
    points: [
      'Tempo with every valid metrical reading',
      'Drift and tempo switches across the track',
      'Swing percentage and grid resolution',
      'Mean deviation from the grid, in ms',
    ],
    example: [['Tempo', '128 BPM'], ['Swing', '54%'], ['Off-grid', '11 ms']],
  },
  'key-lab': {
    title: ['Key detection is ambiguous', 'more often than tools admit.'],
    sub: 'The key and its Camelot code, the ranked runners-up with their scores, '
      + 'and the harmonic neighbours you can mix into without a clash.',
    points: [
      'Key and Camelot code',
      'Top three candidates, with scores',
      'Confidence, stated rather than implied',
      'Harmonic neighbours for mixing',
    ],
    example: [['Key', 'F♯ minor'], ['Camelot', '11A'], ['Confidence', 'high']],
  },
}

const SLIDES = ORDER.map((slug) => {
  const tool = TOOLS.find((t) => t.slug === slug)
  return { ...tool, ...PITCH[slug] }
}).filter((s) => s.slug && s.title)

export default function FeaturedTool() {
  const [index, setIndex] = useState(0)
  const [paused, setPaused] = useState(false)
  const [reduced, setReduced] = useState(false)
  const sectionRef = useRef(null)

  useEffect(() => {
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)')
    const sync = () => setReduced(mq.matches)
    sync()
    mq.addEventListener('change', sync)
    return () => mq.removeEventListener('change', sync)
  }, [])

  useEffect(() => {
    if (paused || reduced || SLIDES.length < 2) return
    const id = setInterval(() => setIndex((i) => (i + 1) % SLIDES.length), ROTATE_MS)
    return () => clearInterval(id)
  }, [paused, reduced])

  // A tab that is not visible should not burn a timer, and coming back to a
  // slide three positions on from where you left is disorienting.
  useEffect(() => {
    const sync = () => setPaused(document.hidden)
    document.addEventListener('visibilitychange', sync)
    return () => document.removeEventListener('visibilitychange', sync)
  }, [])

  const go = useCallback((i) => setIndex(((i % SLIDES.length) + SLIDES.length) % SLIDES.length), [])

  const onKeyDown = (e) => {
    if (e.key === 'ArrowRight') { e.preventDefault(); go(index + 1) }
    if (e.key === 'ArrowLeft') { e.preventDefault(); go(index - 1) }
  }

  const s = SLIDES[index]
  if (!s) return null

  return (
    <section
      className="featured"
      id="featured"
      ref={sectionRef}
      aria-roledescription="carousel"
      aria-label="Tool spotlight"
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocusCapture={() => setPaused(true)}
      onBlurCapture={(e) => {
        if (!sectionRef.current?.contains(e.relatedTarget)) setPaused(false)
      }}
      onKeyDown={onKeyDown}
    >
      <div className="featured-grid">
        <div className="featured-copy">
          <p className="eyebrow">
            The one worth trying first
            <span className="featured-count">
              {index + 1} / {SLIDES.length}
            </span>
          </p>

          <AnimatePresence mode="wait">
            <motion.div
              key={s.slug}
              className="featured-slide"
              initial={{ opacity: 0, y: reduced ? 0 : 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: reduced ? 0 : -8 }}
              transition={{ duration: reduced ? 0 : 0.34, ease: [0.22, 1, 0.36, 1] }}
            >
              <h2 className="featured-title">
                {s.title[0]}<br />{s.title[1]}
              </h2>
              <p className="body featured-sub">{s.sub}</p>

              <ul className="featured-points">
                {s.points.map((p) => (
                  <li key={p}>
                    <span className="featured-tick" aria-hidden="true">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
                           strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M4 12.5 9 17.5 20 6.5" />
                      </svg>
                    </span>
                    {p}
                  </li>
                ))}
              </ul>
            </motion.div>
          </AnimatePresence>

          <div className="featured-actions">
            <Link href={s.route} className="featured-cta">
              Try {s.name}
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                   stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <path d="M5 12h14M13 6l6 6-6 6" />
              </svg>
            </Link>
            <HonestyModal />
          </div>

          <div className="featured-dots" role="tablist" aria-label="Choose a tool">
            {SLIDES.map((slide, i) => (
              <button
                key={slide.slug}
                type="button"
                role="tab"
                aria-selected={i === index}
                aria-label={slide.name}
                className={`featured-dot${i === index ? ' is-on' : ''}`}
                onClick={() => go(i)}
              >
                <span className="featured-dot-fill"
                      style={{ animationDuration: `${ROTATE_MS}ms` }} />
              </button>
            ))}
          </div>
        </div>

        <CometCard className="featured-card-wrap">
          <div className="featured-card">
            <div className="featured-card-top">
              <span className="featured-card-tag">{s.name}</span>
              <span className="featured-card-files">
                {s.inputs.length === 2 ? '2 files' : '1 file'}
              </span>
            </div>

            <AnimatePresence mode="wait">
              <motion.div
                key={s.slug}
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: reduced ? 0 : 0.28 }}
              >
                <ToolVisual slug={s.slug} />
                <div className="featured-card-foot">
                  {s.example.map(([k, v]) => (
                    <div key={k}>
                      <span className="fc-k">{k}</span>
                      <span className="fc-v">{v}</span>
                    </div>
                  ))}
                </div>
              </motion.div>
            </AnimatePresence>

            <p className="featured-card-note">Example output</p>
          </div>
        </CometCard>
      </div>
    </section>
  )
}
