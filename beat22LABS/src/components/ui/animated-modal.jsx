'use client'

// Animated modal (Aceternity), adapted to this project.
//
// Two deliberate changes from the upstream component:
//   - Colours come from the site's CSS variables rather than Tailwind's
//     palette, because this design system is strictly monochrome.
//   - Body scroll is restored to its previous value on close instead of being
//     forced to "auto", which would override the drawer's own scroll lock if
//     both were ever open.

import { motion } from 'motion/react'
import React, { createContext, useContext, useEffect, useRef, useState } from 'react'
import { cn } from '../../lib/utils.js'

const ModalContext = createContext(undefined)

export const ModalProvider = ({ children }) => {
  const [open, setOpen] = useState(false)
  return (
    <ModalContext.Provider value={{ open, setOpen }}>
      {children}
    </ModalContext.Provider>
  )
}

export const useModal = () => {
  const context = useContext(ModalContext)
  if (!context) throw new Error('useModal must be used within a ModalProvider')
  return context
}

export function Modal({ children }) {
  return <ModalProvider>{children}</ModalProvider>
}

export const ModalTrigger = ({ children, className }) => {
  const { setOpen } = useModal()
  return (
    <button
      type="button"
      className={cn('am-trigger', className)}
      onClick={() => setOpen(true)}
    >
      {children}
    </button>
  )
}

export const ModalBody = ({ children, className }) => {
  const { open, setOpen } = useModal()
  const modalRef = useRef(null)

  useEffect(() => {
    if (!open) return
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { document.body.style.overflow = previous }
  }, [open])

  // Escape closes, which the upstream component leaves out.
  useEffect(() => {
    if (!open) return
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, setOpen])

  useOutsideClick(modalRef, () => setOpen(false))

  if (!open) return null

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.13 }}
      className="am-portal"
      role="dialog"
      aria-modal="true"
    >
      <Overlay />
      <motion.div
        ref={modalRef}
        className={cn('am-panel', className)}
        initial={{ opacity: 0, scale: 0.94, rotateX: 12, y: 22 }}
        animate={{ opacity: 1, scale: 1, rotateX: 0, y: 0 }}
        transition={{ type: 'spring', stiffness: 300, damping: 26 }}
      >
        <CloseIcon />
        {children}
      </motion.div>
    </motion.div>
  )
}

export const ModalContent = ({ children, className }) => (
  <div className={cn('am-content', className)}>{children}</div>
)

export const ModalFooter = ({ children, className }) => (
  <div className={cn('am-footer', className)}>{children}</div>
)

const Overlay = ({ className }) => (
  <motion.div
    initial={{ opacity: 0, backdropFilter: 'blur(0px)' }}
    animate={{ opacity: 1, backdropFilter: 'blur(10px)' }}
    transition={{ duration: 0.18 }}
    className={cn('am-overlay', className)}
  />
)

const CloseIcon = () => {
  const { setOpen } = useModal()
  return (
    <button type="button" onClick={() => setOpen(false)}
            className="am-close" aria-label="Close">
      <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"
           viewBox="0 0 24 24" fill="none" stroke="currentColor"
           strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M18 6l-12 12" /><path d="M6 6l12 12" />
      </svg>
    </button>
  )
}

export const useOutsideClick = (ref, callback) => {
  useEffect(() => {
    const listener = (event) => {
      if (!ref.current || ref.current.contains(event.target)) return
      callback(event)
    }
    document.addEventListener('mousedown', listener)
    document.addEventListener('touchstart', listener)
    return () => {
      document.removeEventListener('mousedown', listener)
      document.removeEventListener('touchstart', listener)
    }
  }, [ref, callback])
}