'use client'

// Comet card (Aceternity): tilt-on-hover with a moving specular glare.
//
// Two adjustments: the effect is disabled for coarse pointers (on touch there
// is no hover, and the tilt would only fire mid-tap), and under
// prefers-reduced-motion, where a card that pitches under the cursor is
// exactly the kind of motion that setting exists to suppress.

import React, { useRef } from 'react'
import {
  motion, useMotionTemplate, useMotionValue, useSpring, useTransform,
} from 'motion/react'
import { cn } from '../../lib/utils.js'

function usePointerTiltEnabled() {
  if (typeof window === 'undefined' || !window.matchMedia) return true
  return window.matchMedia('(hover: hover) and (pointer: fine)').matches
    && !window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

export const CometCard = ({
  rotateDepth = 14,
  translateDepth = 14,
  className,
  children,
}) => {
  const ref = useRef(null)
  const enabled = usePointerTiltEnabled()

  const x = useMotionValue(0)
  const y = useMotionValue(0)
  const mouseXSpring = useSpring(x, { stiffness: 220, damping: 22 })
  const mouseYSpring = useSpring(y, { stiffness: 220, damping: 22 })

  const rotateX = useTransform(mouseYSpring, [-0.5, 0.5],
    [`-${rotateDepth}deg`, `${rotateDepth}deg`])
  const rotateY = useTransform(mouseXSpring, [-0.5, 0.5],
    [`${rotateDepth}deg`, `-${rotateDepth}deg`])
  const translateX = useTransform(mouseXSpring, [-0.5, 0.5],
    [`-${translateDepth}px`, `${translateDepth}px`])
  const translateY = useTransform(mouseYSpring, [-0.5, 0.5],
    [`${translateDepth}px`, `-${translateDepth}px`])

  const glareX = useTransform(mouseXSpring, [-0.5, 0.5], [0, 100])
  const glareY = useTransform(mouseYSpring, [-0.5, 0.5], [0, 100])
  const glareBackground = useMotionTemplate`radial-gradient(circle at ${glareX}% ${glareY}%, rgba(255,255,255,0.22) 8%, rgba(255,255,255,0.08) 26%, rgba(255,255,255,0) 72%)`

  const handleMouseMove = (e) => {
    if (!enabled || !ref.current) return
    const rect = ref.current.getBoundingClientRect()
    x.set((e.clientX - rect.left) / rect.width - 0.5)
    y.set((e.clientY - rect.top) / rect.height - 0.5)
  }

  const handleMouseLeave = () => { x.set(0); y.set(0) }

  return (
    <div className={cn('comet-perspective', className)}>
      <motion.div
        ref={ref}
        onMouseMove={handleMouseMove}
        onMouseLeave={handleMouseLeave}
        style={enabled ? { rotateX, rotateY, translateX, translateY } : undefined}
        initial={{ scale: 1, z: 0 }}
        whileHover={enabled ? { scale: 1.02, z: 40, transition: { duration: 0.2 } } : undefined}
        className="comet-card"
      >
        {children}
        {enabled && (
          <motion.div
            className="comet-glare"
            style={{ background: glareBackground }}
            transition={{ duration: 0.2 }}
          />
        )}
      </motion.div>
    </div>
  )
}