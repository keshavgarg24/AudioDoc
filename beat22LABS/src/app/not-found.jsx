// A real 404. Previously the catch-all rendered the landing page, which meant
// a mistyped or stale tool URL silently showed the home page under the site
// nav - two stacked headers and no indication anything was wrong.
//
// This is a server component: `usePathname` is not available here, and the
// path is not worth a client boundary just to echo it back.

import Link from 'next/link'
import { TOOLS } from '../toolConfig.js'

export const metadata = {
  title: 'Page not found',
  robots: { index: false, follow: true },
}

export default function NotFound() {
  return (
    <main className="tool-page notfound">
      <header className="tool-hero">
        <p className="eyebrow">404</p>
        <h1>That page does not exist</h1>
        <p className="tool-intro">
          That address does not match anything here. It may have moved, or the
          link may be out of date.
        </p>
      </header>

      <div className="notfound-actions">
        <Link href="/" className="tool-run notfound-primary">
          Go to AI detection
        </Link>
        <Link href="/tools" className="tool-reset">Browse all tools</Link>
      </div>

      <section className="tool-copy">
        <h2>Looking for a tool?</h2>
        <div className="tool-grid">
          {TOOLS.map((t) => (
            <Link key={t.slug} href={t.route} className="tool-card">
              <h2>{t.name}</h2>
              <p className="tool-card-tag">{t.tagline}</p>
            </Link>
          ))}
        </div>
      </section>
    </main>
  )
}
