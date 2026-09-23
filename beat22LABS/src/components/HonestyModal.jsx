'use client'

// "What we will not claim", in a modal.
//
// This is the product's position stated plainly: what is measurement, what is
// estimation, and what is not measurable at all. Putting it behind a button on
// the landing page rather than burying it in a docs footnote is deliberate -
// every competitor overclaims, and being checkable is the differentiator.

import React from 'react'
import {
  Modal, ModalBody, ModalContent, ModalFooter, ModalTrigger,
} from './ui/animated-modal.jsx'
import { motion } from 'motion/react'
import Link from 'next/link'

const TIERS = [
  {
    emoji: '📐',
    tier: 'Measurement',
    verdict: 'Trust the number',
    body: 'Loudness, true peak, dynamic range, spectral balance, stereo width, '
      + 'pitch in cents, timing in milliseconds. These follow published '
      + 'standards and match any other compliant tool.',
  },
  {
    emoji: '🎯',
    tier: 'Estimation',
    verdict: 'Check the confidence',
    body: 'Key, tempo and the AI verdict. Each has a real error rate, so each '
      + 'ships with a confidence score and ranked alternatives instead of one '
      + 'confident answer.',
  },
  {
    emoji: '🚫',
    tier: 'Not measurable',
    verdict: 'We do not score it',
    body: 'Tone, emotion, vocal appeal, and whether a track will chart. No '
      + 'number is offered for any of these, because inventing one would make '
      + 'the honest numbers untrustworthy too.',
  },
]

export default function HonestyModal() {
  return (
    <Modal>
      <ModalTrigger className="honesty-trigger">
        <span className="honesty-trigger-label">What we will not claim</span>
        <div className="honesty-trigger-slide" aria-hidden="true">🔍</div>
      </ModalTrigger>

      <ModalBody>
        <ModalContent>
          <h4 className="honesty-h">
            Three tiers of certainty, labelled{' '}
            <span className="honesty-mark">everywhere</span> 🎧
          </h4>

          <div className="honesty-tiers">
            {TIERS.map((t, i) => (
              <motion.div
                key={t.tier}
                className="honesty-tier"
                initial={{ opacity: 0, y: 14 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: 0.05 + i * 0.08, duration: 0.3 }}
                whileHover={{ y: -3 }}
              >
                <span className="honesty-emoji" aria-hidden="true">{t.emoji}</span>
                <div className="honesty-tier-body">
                  <div className="honesty-tier-head">
                    <strong>{t.tier}</strong>
                    <span className="honesty-verdict">{t.verdict}</span>
                  </div>
                  <p>{t.body}</p>
                </div>
              </motion.div>
            ))}
          </div>

          <p className="honesty-foot-note">
            Every result names which tier it came from. A percentile built from
            140 measured tracks and an editorial genre estimate are both useful,
            but you should always know which one you are reading.
          </p>
        </ModalContent>

        <ModalFooter>
          <Link href="/docs" className="honesty-btn honesty-btn--ghost">
            Read the docs
          </Link>
          <Link href="/tools" className="honesty-btn honesty-btn--solid">
            Browse tools
          </Link>
        </ModalFooter>
      </ModalBody>
    </Modal>
  )
}