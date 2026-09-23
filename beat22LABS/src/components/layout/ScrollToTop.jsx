'use client'

import { useEffect } from 'react'
import { usePathname } from 'next/navigation'

// Client-side navigation keeps the previous scroll position, which lands you
// halfway down a page you have never seen. Next restores scroll on back/forward
// by design; this only resets it on a forward navigation to a new route.
export default function ScrollToTop() {
  const pathname = usePathname()

  useEffect(() => {
    window.scrollTo({ top: 0, behavior: 'instant' })
  }, [pathname])

  return null
}
