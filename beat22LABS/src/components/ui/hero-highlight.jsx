'use client'

// Dot field behind the landing hero, with a purple spotlight that tracks the
// pointer.
//
// Ported from the supplied HeroHighlight. The light-theme half of the original
// is dropped (this site has one theme) and the hover dot colour is the design
// system's accent rather than indigo-500. The spotlight is hover-only, which
// is what the rest of the page's motion policy already asks for - nothing on
// this page animates until you point at it.

import React from 'react'
import { motion, useMotionTemplate, useMotionValue } from 'motion/react'

const dots = (fill) =>
  `url("data:image/svg+xml;charset=utf-8,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32' width='16' height='16' fill='none'%3E%3Ccircle fill='${fill}' cx='10' cy='10' r='2.5'/%3E%3C/svg%3E")`

const REST = dots('%232a2a2a')
const LIT = dots('%237000FF')

export function HeroHighlight({ children, className = '', containerClassName = '' }) {
  const mouseX = useMotionValue(0)
  const mouseY = useMotionValue(0)

  const mask = useMotionTemplate`radial-gradient(220px circle at ${mouseX}px ${mouseY}px, black 0%, transparent 100%)`

  const onMove = ({ currentTarget, clientX, clientY }) => {
    if (!currentTarget) return
    const { left, top } = currentTarget.getBoundingClientRect()
    mouseX.set(clientX - left)
    mouseY.set(clientY - top)
  }

  return (
    <div className={`hh-root ${containerClassName}`.trim()} onMouseMove={onMove}>
      <div className="hh-dots" style={{ backgroundImage: REST }} aria-hidden="true" />
      <motion.div
        className="hh-dots hh-dots--lit"
        aria-hidden="true"
        style={{ backgroundImage: LIT, WebkitMaskImage: mask, maskImage: mask }}
      />
      <div className={`hh-content ${className}`.trim()}>{children}</div>
    </div>
  )
}

/** Marker sweep behind a phrase. Used once, on the landing page h1.
 *
 *  Two changes from the source component:
 *
 *  1. It sweeps a band anchored near the baseline instead of filling the whole
 *     line box (`0% 100%` to `100% 100%`). At the display size this heading is
 *     set in, a full-height fill paints a purple slab taller than the cap
 *     height that swallows the letterforms.
 *  2. The sweep is a CSS animation rather than a motion one. motion cannot
 *     interpolate a background-size whose two components carry different units
 *     (`0% 0.32em` to `100% 0.32em`) - it writes the initial value and never
 *     advances - and a percentage height would resolve against the whole
 *     multi-line inline box, so the band has to be in em. CSS animates the
 *     mixed value correctly, and gets reduced-motion handling for free.
 */
export function Highlight({ children, className = '' }) {
  return <span className={`hh-mark ${className}`.trim()}>{children}</span>
}

export default HeroHighlight
