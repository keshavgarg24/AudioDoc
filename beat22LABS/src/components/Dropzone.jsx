'use client'

import React, { useCallback, useRef, useState } from 'react'
import { bytes } from '../lib/format.js'

const ACCEPT = ['.wav', '.mp3', '.flac', '.m4a', '.aac', '.ogg', '.opus']
const MAX_BYTES = 50 * 1024 * 1024

const MODE_CTA = {
  ai: 'Detect AI generation',
  audio: 'Analyse the audio',
  full: 'Run the complete report',
}

export default function Dropzone({ onFile, disabled, mode = 'ai', verify, onVerifyChange }) {
  const [over, setOver] = useState(false)
  const [error, setError] = useState(null)
  const inputRef = useRef(null)
  const showVerify = mode !== 'audio'

  const accept = useCallback((f) => {
    if (!f) return
    const ext = '.' + (f.name.split('.').pop() || '').toLowerCase()
    if (!ACCEPT.includes(ext)) {
      setError(`${ext || 'That file type'} is not supported. Use ${ACCEPT.join(', ')}.`)
      return
    }
    if (f.size > MAX_BYTES) {
      setError(`That file is ${bytes(f.size)}. The limit is 50 MB.`)
      return
    }
    setError(null)
    // Hand off immediately: the Processing view is the feedback for what
    // happens next, so the dropzone itself does not need its own animation.
    onFile(f)
  }, [onFile])

  return (
    <div>
      <div
        role="button"
        tabIndex={disabled ? -1 : 0}
        aria-disabled={disabled}
        aria-label="Upload an audio file for analysis"
        className={`dropzone${over ? ' is-over' : ''}${error ? ' is-error' : ''}`}
        onClick={() => !disabled && inputRef.current?.click()}
        onKeyDown={(e) => {
          if (disabled) return
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault(); inputRef.current?.click()
          }
        }}
        onDragOver={(e) => { e.preventDefault(); if (!disabled) setOver(true) }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault(); setOver(false)
          if (!disabled) accept(e.dataTransfer.files?.[0])
        }}
      >
        <div className="dz-inner">
          <div className="dz-icon" aria-hidden="true">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none"
                 stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"
                 strokeLinejoin="round">
              <path d="M12 16V4M12 4 7 9M12 4l5 5" />
              <path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3" />
            </svg>
          </div>

          <p className="dz-title">{over ? 'Release to begin' : 'Drop an audio file'}</p>
          <p className="caption">
            or click to browse, then {MODE_CTA[mode].toLowerCase()}
          </p>

          <div className="formats">
            {ACCEPT.map((f) => <span key={f} className="pill">{f}</span>)}
          </div>
        </div>

        <input ref={inputRef} type="file" hidden accept={ACCEPT.join(',')}
               onChange={(e) => { accept(e.target.files?.[0]); e.target.value = '' }} />
      </div>

      {showVerify && (
        <label className="verify-toggle" onClick={(e) => e.stopPropagation()}>
          <input
            type="checkbox"
            checked={verify}
            disabled={disabled}
            onChange={(e) => onVerifyChange?.(e.target.checked)}
          />
          <span className="verify-box" aria-hidden="true">
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                 strokeWidth="3.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M20 6 9 17l-5-5" />
            </svg>
          </span>
          <span className="verify-text">
            <span className="verify-title">Run deeper verification</span>
            <span className="caption">
              Cross-checks the result and attempts to identify the likely
              generation source. Adds to analysis and research time.
            </span>
          </span>
        </label>
      )}

      {error && (
        <div className="alert" role="alert">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
               strokeWidth="2" strokeLinecap="round">
            <circle cx="12" cy="12" r="9" /><path d="M12 8v4M12 16h.01" />
          </svg>
          <span>{error}</span>
        </div>
      )}
    </div>
  )
}