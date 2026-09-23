'use client'

// Site footer. Carries the logo, every tool link, and the honesty line.
//
// The tool links matter for more than navigation: they are the internal links
// a crawler follows to find the tool pages from anywhere on the site.

import React from 'react'
import Link from 'next/link'
import { TOOLS } from '../../toolConfig.js'

export default function SiteFooter() {
  const year = new Date().getFullYear()

  return (
    <footer className="site-ftr">
      <div className="ftr-inner">
        <div className="ftr-brand">
          <Link href="/" className="ftr-logo" aria-label="Beat22 LABS home">
            <img src="/brand/logo.svg" alt="" />
          </Link>
          <p className="ftr-tagline">
            Measurement tools for people who make music. Every result states
            what it measures and what it cannot tell you.
          </p>
          <div className="ftr-status">
            <span className="ftr-pill">No signup</span>
            <span className="ftr-pill">Free to use</span>
            <span className="ftr-pill">Audio not shared</span>
          </div>
        </div>

        <nav className="ftr-cols" aria-label="Footer">
          <div className="ftr-col">
            <h3>Tools</h3>
            <ul>
              {TOOLS.slice(0, 4).map((t) => (
                <li key={t.slug}><Link href={t.route}>{t.name}</Link></li>
              ))}
            </ul>
          </div>

          <div className="ftr-col">
            <h3>More tools</h3>
            <ul>
              {TOOLS.slice(4).map((t) => (
                <li key={t.slug}><Link href={t.route}>{t.name}</Link></li>
              ))}
              <li><Link href="/tools">All tools</Link></li>
            </ul>
          </div>

          <div className="ftr-col">
            <h3>Product</h3>
            <ul>
              <li><Link href="/">AI music detection</Link></li>
              <li><Link href="/docs/api">API reference</Link></li>
              <li><Link href="/docs">Documentation</Link></li>
            </ul>
          </div>
        </nav>
      </div>

      <div className="ftr-bottom">
        <span>&copy; {year} Beat22 LABS</span>
        <span className="ftr-note">
          Detection results are probabilistic and are not proof of authorship.
        </span>
      </div>
    </footer>
  )
}