'use client'

// The analysis pipeline, as a sticky scroll reveal.
//
// The sticky panel holds the same diagrams the tool pages use, so the steps
// are illustrated by the actual measurements rather than stock artwork.

import React from 'react'
import { StickyScroll } from './ui/sticky-scroll-reveal.jsx'
import ToolVisual from './visuals/ToolVisual.jsx'

const STEPS = [
  {
    title: 'Beat-aligned segmentation',
    description:
      'The track is decoded and cut into windows that start on downbeats '
      + 'rather than on a fixed clock. Aligning to the music means each window '
      + 'holds a comparable musical unit, so a verdict on one window means the '
      + 'same thing as a verdict on the next.',
    content: <ToolVisual slug="tempo-lab" />,
  },
  {
    title: 'Every window scored on its own',
    description:
      'Each window is analysed independently, then the sequence is read as a '
      + 'whole. A track that is part human and part generated shows up as a '
      + 'change along the timeline instead of being averaged into one '
      + 'misleading number.',
    content: <ToolVisual slug="vocal-lab" />,
  },
  {
    title: 'Measured, not guessed',
    description:
      'Loudness follows ITU-R BS.1770-4. True peak is measured on a 4x '
      + 'oversampled signal. Key comes from constant-Q chroma with the bass '
      + 'register weighted separately. These are published methods, so the '
      + 'numbers agree with any other compliant tool.',
    content: <ToolVisual slug="master-check" />,
  },
  {
    title: 'A reliability score, not just a verdict',
    description:
      'Short files, heavy compression and narrow bandwidth all reduce how much '
      + 'the signal can support. Every report says how far to trust it, and '
      + 'every tool states in writing what it cannot tell you.',
    content: <ToolVisual slug="key-lab" />,
  },
]

export default function HowItWorks() {
  return (
    <section className="how-section" id="how-it-works">
      <div className="how-head">
        <p className="eyebrow">How it works</p>
        <h2 className="how-title">Four steps, all of them measurable</h2>
        <p className="body how-sub">
          Nothing here is a black box you have to take on faith. Each stage is
          a published method you can check against another tool.
        </p>
      </div>
      <StickyScroll content={STEPS} />
    </section>
  )
}