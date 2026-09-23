'use client'

// Sticky scroll reveal (Aceternity), adapted to this project.
//
// Changes from upstream:
//   - The coloured gradient backdrop is dropped. This design system is
//     monochrome, and the sticky panel here holds a real diagram rather than
//     a decorative block.
//   - Progress is driven by the page scroll, not an inner scroll container.
//     A nested scroll area inside a long page traps the wheel on desktop and
//     is close to unusable on a touchpad.
//   - The panel is hidden below the lg breakpoint and each step renders its
//     own visual inline instead, so mobile is not left with text only.

import React, { useRef, useState } from 'react'
import { motion, useMotionValueEvent, useScroll } from 'motion/react'
import { cn } from '../../lib/utils.js'

export const StickyScroll = ({ content, contentClassName }) => {
  const [activeCard, setActiveCard] = useState(0)
  const ref = useRef(null)

  const { scrollYProgress } = useScroll({
    target: ref,
    offset: ['start center', 'end center'],
  })

  useMotionValueEvent(scrollYProgress, 'change', (latest) => {
    const breakpoints = content.map((_, i) => i / content.length)
    const closest = breakpoints.reduce((acc, bp, i) => (
      Math.abs(latest - bp) < Math.abs(latest - breakpoints[acc]) ? i : acc
    ), 0)
    setActiveCard(closest)
  })

  return (
    <div ref={ref} className="ss-wrap">
      <div className="ss-steps">
        {content.map((item, index) => (
          <div key={item.title} className="ss-step">
            <motion.div
              animate={{ opacity: activeCard === index ? 1 : 0.32 }}
              transition={{ duration: 0.3 }}
              className="ss-step-inner"
            >
              <span className="ss-step-num">
                {String(index + 1).padStart(2, '0')}
              </span>
              <h3 className="ss-step-title">{item.title}</h3>
              <p className="ss-step-desc">{item.description}</p>

              {/* Mobile: the panel is hidden, so the visual rides inline. */}
              <div className="ss-inline-visual">{item.content}</div>
            </motion.div>
          </div>
        ))}
      </div>

      <div className={cn('ss-panel', contentClassName)}>
        <div className="ss-panel-sticky">
          {content.map((item, index) => (
            <motion.div
              key={item.title}
              className="ss-panel-slot"
              initial={false}
              animate={{
                opacity: activeCard === index ? 1 : 0,
                scale: activeCard === index ? 1 : 0.97,
              }}
              transition={{ duration: 0.35, ease: [0.2, 0.8, 0.2, 1] }}
              style={{ pointerEvents: activeCard === index ? 'auto' : 'none' }}
            >
              {item.content}
            </motion.div>
          ))}
          <div className="ss-progress" aria-hidden="true">
            {content.map((_, i) => (
              <span key={i} className={i === activeCard ? 'is-active' : ''} />
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}