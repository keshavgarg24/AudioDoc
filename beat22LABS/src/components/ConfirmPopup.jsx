'use client'

import React from 'react'
import { MODES } from './ModeSelect.jsx'
import { bytes } from '../lib/format.js'

export default function ConfirmPopup({ file, mode, verify, onModeChange, onVerifyChange, onConfirm, onCancel }) {
  const needsModel = mode !== 'audio'
  const modeInfo = MODES.find((m) => m.id === mode)

  return (
    <div className="popup-overlay" onClick={onCancel}>
      <div className="popup" onClick={(e) => e.stopPropagation()}>
        <div className="popup-header">
          <p className="eyebrow">Confirm analysis</p>
          <button className="popup-close" onClick={onCancel} aria-label="Close">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none"
                 stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M18 6 6 18M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div className="popup-body">
          <div className="popup-file">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none"
                 stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"
                 strokeLinejoin="round" style={{ flexShrink: 0, opacity: 0.6 }}>
              <path d="M9 18V5l12-2v13" />
              <circle cx="6" cy="18" r="3" /><circle cx="18" cy="16" r="3" />
            </svg>
            <div>
              <p className="popup-filename">{file.name}</p>
              <p className="caption">{bytes(file.size)}</p>
            </div>
          </div>

          <div className="popup-section">
            <p className="popup-label">Analysis mode</p>
            <div className="popup-modes">
              {MODES.map((m) => (
                <button key={m.id} type="button"
                        className={`popup-mode${mode === m.id ? ' is-active' : ''}`}
                        onClick={() => onModeChange(m.id)}>
                  <span className="popup-mode-dot" />
                  <span>
                    <span className="popup-mode-name">{m.label}</span>
                    <span className="popup-mode-time mono">{m.time}</span>
                  </span>
                </button>
              ))}
            </div>
            {modeInfo && <p className="caption" style={{ marginTop: '0.5rem' }}>{modeInfo.desc}</p>}
          </div>

          {needsModel && (
            <div className="popup-section">
              <label className="popup-verify">
                <input type="checkbox" checked={verify}
                       onChange={(e) => onVerifyChange(e.target.checked)} />
                <span className="verify-box" aria-hidden="true">
                  <svg width="10" height="10" viewBox="0 0 24 24" fill="none"
                       stroke="currentColor" strokeWidth="3.5" strokeLinecap="round"
                       strokeLinejoin="round">
                    <path d="M20 6 9 17l-5-5" />
                  </svg>
                </span>
                <span style={{ display: 'flex', flexDirection: 'column', gap: '0.15rem' }}>
                  <span style={{ fontSize: '0.8rem', fontWeight: 600 }}>
                    Run deeper verification
                  </span>
                  <span className="caption">
                    Cross-checks the result and identifies the likely source. Adds time.
                  </span>
                </span>
              </label>
            </div>
          )}
        </div>

        <div className="popup-footer">
          <button className="btn btn--ghost btn--sm" onClick={onCancel}>Cancel</button>
          <button className="btn btn--solid" onClick={onConfirm}>
            Proceed with analysis
          </button>
        </div>
      </div>
    </div>
  )
}