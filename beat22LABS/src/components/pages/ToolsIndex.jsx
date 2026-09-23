'use client'

import React from 'react'
import Link from 'next/link'
import { TOOLS } from '../../toolConfig.js'
import ToolVisual from '../visuals/ToolVisual.jsx'

export default function ToolsIndex() {
  return (
    <main className="tools-index">
      <header className="tool-hero">
        <h1>Tools</h1>
        <p className="tool-intro">
          Measurement tools for producers, artists and engineers. Each one states
          what it measures precisely and what it cannot tell you, because these
          are meant to be checked against your own ears.
        </p>
      </header>

      <div className="tool-grid">
        {TOOLS.map((t) => (
          <Link key={t.slug} href={t.route} className="tool-card">
            <h2>{t.name}</h2>
            <p className="tool-card-tag">{t.tagline}</p>
            <p className="tool-card-desc">{t.intro}</p>
            <div className="tool-card-visual">
              <ToolVisual slug={t.slug} />
            </div>
            <div className="tool-card-foot">
              <span>{t.inputs.length === 2 ? 'Two files' : 'One file'}</span>
              <span>Free</span>
            </div>
          </Link>
        ))}
      </div>
    </main>
  )
}