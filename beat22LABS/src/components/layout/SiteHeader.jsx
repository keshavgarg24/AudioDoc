'use client'

// The one header, on every page.
//
// Kept deliberately sparse: logo, search, two links, one action. The previous
// version also carried a service-status pill and a separate API link, which
// made six competing elements fight for a 64px bar. Status now lives where it
// is actionable - beside the upload control - and API sits under Docs.

import React, { useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { TOOLS } from '../../toolConfig.js'
import CommandPalette, { useCommandPalette } from '../CommandPalette.jsx'

export default function SiteHeader() {
  const [menuOpen, setMenuOpen] = useState(false)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [scrolled, setScrolled] = useState(false)
  const [isMac, setIsMac] = useState(true)
  const menuRef = useRef(null)
  const pathname = usePathname()
  const palette = useCommandPalette()

  useEffect(() => {
    setIsMac(/Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent))
  }, [])

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8)
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  // Close everything on navigation, or the drawer stays open over the new page.
  useEffect(() => {
    setMenuOpen(false)
    setDrawerOpen(false)
  }, [pathname])

  useEffect(() => {
    if (!menuOpen) return
    const away = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) setMenuOpen(false)
    }
    const esc = (e) => { if (e.key === 'Escape') setMenuOpen(false) }
    document.addEventListener('mousedown', away)
    document.addEventListener('keydown', esc)
    return () => {
      document.removeEventListener('mousedown', away)
      document.removeEventListener('keydown', esc)
    }
  }, [menuOpen])

  // A drawer that scrolls the page behind it feels broken on iOS.
  useEffect(() => {
    if (!drawerOpen) return
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { document.body.style.overflow = previous }
  }, [drawerOpen])

  return (
    <>
      <header className={`site-hdr${scrolled ? ' is-scrolled' : ''}`}>
        <div className="hdr-inner">
          <Link href="/" className="hdr-logo" aria-label="Beat22 LABS home">
            <img src="/brand/logo.svg" alt="" />
          </Link>

          <button type="button" className="hdr-search"
                  onClick={() => palette.setOpen(true)}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                 stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <circle cx="11" cy="11" r="7" /><path d="M20 20l-3.5-3.5" />
            </svg>
            <span className="hdr-search-text">Search tools</span>
            <kbd className="hdr-kbd">{isMac ? '⌘' : 'Ctrl'} K</kbd>
          </button>

          <nav className="hdr-nav" aria-label="Primary">
            <div className="hdr-dd" ref={menuRef}>
              <button
                type="button"
                className={`hdr-link hdr-dd-btn${menuOpen ? ' is-open' : ''}`}
                aria-expanded={menuOpen}
                aria-haspopup="true"
                onClick={() => setMenuOpen((v) => !v)}
              >
                Tools
                <svg width="9" height="9" viewBox="0 0 24 24" fill="none"
                     stroke="currentColor" strokeWidth="3" strokeLinecap="round">
                  <path d="M6 9l6 6 6-6" />
                </svg>
              </button>

              {menuOpen && (
                <div className="hdr-mega" role="menu">
                  <div className="hdr-mega-grid">
                    {TOOLS.map((t) => (
                      <Link key={t.slug} href={t.route} className="hdr-mega-item"
                            role="menuitem">
                        <span className="hdr-mega-name">{t.name}</span>
                        <span className="hdr-mega-tag">{t.tagline}</span>
                      </Link>
                    ))}
                  </div>
                  <Link href="/tools" className="hdr-mega-foot">
                    Compare all seven tools
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
                         stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                      <path d="M5 12h14M13 6l6 6-6 6" />
                    </svg>
                  </Link>
                </div>
              )}
            </div>

            <Link href="/docs" className="hdr-link">Docs</Link>
          </nav>

          <div className="hdr-right">
            <button type="button" className="hdr-icon-btn hdr-search-mobile"
                    aria-label="Search"
                    onClick={() => palette.setOpen(true)}>
              <svg width="17" height="17" viewBox="0 0 24 24" fill="none"
                   stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <circle cx="11" cy="11" r="7" /><path d="M20 20l-3.5-3.5" />
              </svg>
            </button>

            <button
              type="button"
              className="hdr-burger"
              aria-label={drawerOpen ? 'Close menu' : 'Open menu'}
              aria-expanded={drawerOpen}
              onClick={() => setDrawerOpen((v) => !v)}
            >
              <span className={drawerOpen ? 'is-x' : ''} />
              <span className={drawerOpen ? 'is-x' : ''} />
            </button>
          </div>
        </div>
      </header>

      {/* Mobile drawer.
          The clip wrapper is viewport-sized with overflow hidden. Without it
          the closed drawer, parked off-screen by a transform, still counts
          toward document width - and being position:fixed, overflow-x on the
          body cannot clip it. That produced a horizontal scrollbar on every
          page at every width. */}
      <div className={`hdr-drawer-clip${drawerOpen ? ' is-open' : ''}`}>
        <div className={`hdr-drawer${drawerOpen ? ' is-open' : ''}`}
             role="dialog" aria-modal="true" aria-label="Menu">
          <div className="hdr-drawer-panel">
            <p className="hdr-drawer-label">Tools</p>
            {TOOLS.map((t) => (
              <Link key={t.slug} href={t.route} className="hdr-drawer-item">
                <strong>{t.name}</strong>
                <span>{t.tagline}</span>
              </Link>
            ))}
            <p className="hdr-drawer-label">More</p>
            <Link href="/tools" className="hdr-drawer-item"><strong>All tools</strong></Link>
            <Link href="/docs" className="hdr-drawer-item"><strong>Documentation</strong></Link>
            <Link href="/docs/api" className="hdr-drawer-item"><strong>API reference</strong></Link>
          </div>
        </div>
      </div>
      {drawerOpen && (
        <div className="hdr-scrim" onClick={() => setDrawerOpen(false)} />
      )}

      <CommandPalette open={palette.open} onClose={() => palette.setOpen(false)} />
    </>
  )
}