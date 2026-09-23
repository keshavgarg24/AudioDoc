'use client'

import React from 'react'
import Link from 'next/link'
import { TOOLS } from '../toolConfig.js'

// Tools promoted on the landing page. Without this the tool pages exist only
// as URLs: a visitor who lands on "/" has no way to discover them, and the
// internal links a crawler needs to reach them do not exist either.
export default function ToolsSection() {
  return (
    <section className="home-tools rise rise-4" id="tools">
      <div className="home-tools-head">
        <div>
          <p className="eyebrow">Free tools</p>
          <h3 className="home-tools-title">
            Measure the things you can only guess at
          </h3>
          <p className="body home-tools-sub">
            Seven tools for producers and artists. Each one states exactly what
            it measures and what it cannot tell you, so you can check it against
            your own ears.
          </p>
        </div>
        <Link href="/tools" className="home-tools-all">
          All tools
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
               stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <path d="M5 12h14M13 6l6 6-6 6" />
          </svg>
        </Link>
      </div>

      <div className="home-tools-grid">
        {TOOLS.map((t) => (
          <Link key={t.slug} href={t.route} className="home-tool">
            <div className="home-tool-top">
              <span className="home-tool-name">{t.name}</span>
              <span className="home-tool-files">
                {t.inputs.length === 2 ? '2 files' : '1 file'}
              </span>
            </div>
            <p className="home-tool-tag">{t.tagline}</p>
            <span className="home-tool-go">
              Open
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
                   stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
                <path d="M5 12h14M13 6l6 6-6 6" />
              </svg>
            </span>
          </Link>
        ))}
      </div>
    </section>
  )
}