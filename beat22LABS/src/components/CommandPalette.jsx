'use client'

// Command palette, opened from the header or with Cmd/Ctrl-K.
//
// The index is built from the tool table plus a short list of pages and
// concepts, so a new tool becomes searchable without touching this file.
// Matching is a light subsequence score rather than exact substring: typing
// "bvf" or "mask" should still find Beat and Vocal Fit.

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import { motion } from 'motion/react'
import { TOOLS } from '../toolConfig.js'

const PAGES = [
  { title: 'AI music detection', to: '/', group: 'Pages',
    keywords: 'detect ai generated suno udio verdict upload analyse' },
  { title: 'All tools', to: '/tools', group: 'Pages',
    keywords: 'browse list every tool' },
  { title: 'API reference', to: '/docs/api', group: 'Pages',
    keywords: 'endpoints rest curl authentication webhook rate limit errors' },
  { title: 'Documentation', to: '/docs', group: 'Pages',
    keywords: 'guide how it works report' },
]

const CONCEPTS = [
  { title: 'What is LUFS?', to: '/mastering-check', group: 'Answers',
    keywords: 'loudness integrated streaming target spotify normalisation' },
  { title: 'What is a Camelot code?', to: '/key-finder', group: 'Answers',
    keywords: 'harmonic mixing wheel relative key dj compatible' },
  { title: 'What is frequency masking?', to: '/beat-vocal-fit', group: 'Answers',
    keywords: 'vocal buried muddy clash intelligibility sit in mix' },
  { title: 'Why is my BPM half or double?', to: '/bpm-finder', group: 'Answers',
    keywords: 'tempo octave metrical level 70 140 ambiguous' },
  { title: 'Can you predict a hit?', to: '/benchmark', group: 'Answers',
    keywords: 'hit potential chart percentile prediction commercial success' },
  { title: 'Check my vocal pitch', to: '/vocal-analyzer', group: 'Answers',
    keywords: 'cents flat sharp tuning intonation range vibrato sibilance' },
  { title: 'Compare against a reference', to: '/reference-track-analyzer',
    group: 'Answers',
    keywords: 'sound like reference eq tonal balance match commercial' },
]

function buildIndex() {
  return [
    ...TOOLS.map((t) => ({
      title: t.name, subtitle: t.tagline, to: t.route, group: 'Tools',
      keywords: `${t.slug} ${t.tagline} ${(t.keywords || []).join(' ')}`,
    })),
    ...PAGES,
    ...CONCEPTS,
  ]
}

/** Subsequence match with a small bonus for contiguous and prefix hits. */
function score(query, item) {
  const q = query.toLowerCase().trim()
  if (!q) return 0
  const hay = `${item.title} ${item.subtitle || ''} ${item.keywords || ''}`.toLowerCase()

  if (hay.includes(q)) {
    return item.title.toLowerCase().startsWith(q) ? 1000
      : item.title.toLowerCase().includes(q) ? 700 : 400
  }

  let qi = 0, streak = 0, best = 0
  for (let i = 0; i < hay.length && qi < q.length; i += 1) {
    if (hay[i] === q[qi]) { qi += 1; streak += 1; best = Math.max(best, streak) }
    else streak = 0
  }
  return qi === q.length ? 100 + best * 5 : -1
}

export default function CommandPalette({ open, onClose }) {
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const inputRef = useRef(null)
  const listRef = useRef(null)
  const router = useRouter()
  const index = useMemo(buildIndex, [])

  const results = useMemo(() => {
    if (!query.trim()) {
      return index.filter((i) => i.group === 'Tools').slice(0, 7)
    }
    return index
      .map((item) => ({ item, s: score(query, item) }))
      .filter((r) => r.s >= 0)
      .sort((a, b) => b.s - a.s)
      .slice(0, 8)
      .map((r) => r.item)
  }, [query, index])

  useEffect(() => { setActive(0) }, [query])

  useEffect(() => {
    if (!open) return
    setQuery('')
    // Focus after the entry animation starts, or iOS ignores it.
    const id = setTimeout(() => inputRef.current?.focus(), 40)
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      clearTimeout(id)
      document.body.style.overflow = previous
    }
  }, [open])

  const go = useCallback((item) => {
    if (!item) return
    onClose()
    router.push(item.to)
  }, [router, onClose])

  const onKeyDown = (e) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault(); setActive((i) => (i + 1) % Math.max(results.length, 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActive((i) => (i - 1 + results.length) % Math.max(results.length, 1))
    } else if (e.key === 'Enter') {
      e.preventDefault(); go(results[active])
    } else if (e.key === 'Escape') {
      e.preventDefault(); onClose()
    }
  }

  // Keep the highlighted row in view when navigating by keyboard.
  useEffect(() => {
    listRef.current?.querySelector('[data-active="true"]')
      ?.scrollIntoView({ block: 'nearest' })
  }, [active])

  let lastGroup = null

  // Mount follows `open` exactly. Two earlier approaches - AnimatePresence,
  // then a timer-based unmount - both left this node in the DOM after close.
  // It is position:fixed inset:0, so a leftover made the whole page
  // unclickable. An entrance animation is worth having; a 100ms fade-out is
  // not worth that risk.
  if (!open) return null

  return (
    <motion.div
      className="cp-portal"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.12 }}
      // Never let a fading-out overlay intercept clicks.
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
          <motion.div
            className="cp-panel"
            role="dialog" aria-modal="true" aria-label="Search"
            initial={{ opacity: 0, y: -12, scale: 0.985 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            transition={{ type: 'spring', stiffness: 420, damping: 30 }}
          >
            <div className="cp-input-row">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none"
                   stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <circle cx="11" cy="11" r="7" /><path d="M20 20l-3.5-3.5" />
              </svg>
              <input
                ref={inputRef}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={onKeyDown}
                placeholder="Search tools, docs, or ask a question"
                aria-label="Search"
                autoComplete="off" spellCheck="false"
              />
              <kbd className="cp-esc">ESC</kbd>
            </div>

            <div className="cp-results" ref={listRef} role="listbox">
              {results.length === 0 ? (
                <p className="cp-empty">
                  Nothing matches “{query}”. Try “bpm”, “loudness” or “vocal”.
                </p>
              ) : results.map((item, i) => {
                const header = item.group !== lastGroup ? item.group : null
                lastGroup = item.group
                return (
                  <React.Fragment key={`${item.group}-${item.title}`}>
                    {header && <p className="cp-group">{header}</p>}
                    <button
                      type="button"
                      role="option"
                      aria-selected={i === active}
                      data-active={i === active}
                      className={`cp-item${i === active ? ' is-active' : ''}`}
                      onMouseEnter={() => setActive(i)}
                      onClick={() => go(item)}
                    >
                      <span className="cp-item-main">
                        <span className="cp-item-title">{item.title}</span>
                        {item.subtitle && (
                          <span className="cp-item-sub">{item.subtitle}</span>
                        )}
                      </span>
                      <svg width="13" height="13" viewBox="0 0 24 24" fill="none"
                           stroke="currentColor" strokeWidth="2"
                           strokeLinecap="round" className="cp-item-go">
                        <path d="M5 12h14M13 6l6 6-6 6" />
                      </svg>
                    </button>
                  </React.Fragment>
                )
              })}
            </div>

            <div className="cp-foot">
              <span><kbd>↑</kbd><kbd>↓</kbd> navigate</span>
              <span><kbd>↵</kbd> open</span>
              <span><kbd>esc</kbd> close</span>
            </div>
          </motion.div>
    </motion.div>
  )
}

/** Global Cmd/Ctrl-K, ignored while typing in a field. */
export function useCommandPalette() {
  const [open, setOpen] = useState(false)

  useEffect(() => {
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setOpen((v) => !v)
        return
      }
      // "/" is a common shortcut too, but must not hijack real typing.
      if (e.key === '/' && !open) {
        const t = e.target
        const typing = t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA'
          || t.isContentEditable)
        if (!typing) { e.preventDefault(); setOpen(true) }
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open])

  return { open, setOpen }
}