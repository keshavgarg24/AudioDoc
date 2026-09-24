// Root layout: fonts, global chrome, and the metadata defaults every route
// inherits.
//
// The header and footer live here rather than in each page, so they are one
// object across the whole site instead of seven that drift apart. This is the
// direct replacement for the old react-router `Layout` route element.

import { Inter } from 'next/font/google'
import SiteHeader from '../components/layout/SiteHeader.jsx'
import SiteFooter from '../components/layout/SiteFooter.jsx'
import ScrollToTop from '../components/layout/ScrollToTop.jsx'
import '../styles.css'

// next/font self-hosts the files at build time, so there is no runtime request
// to a font CDN and no flash of unstyled text. The metric overrides match the
// Angular app's `_font_family.scss` so line boxes are identical across both.
const inter = Inter({
  subsets: ['latin'],
  weight: ['300', '400', '500', '600', '700', '800'],
  display: 'swap',
  variable: '--font-inter',
  adjustFontFallback: false,
})

export const metadata = {
  metadataBase: new URL(
    process.env.NEXT_PUBLIC_SITE_URL || 'http://localhost:5173',
  ),
  title: {
    default: 'Beat22 LABS — AI Music Detection',
    template: '%s | Beat22 LABS',
  },
  description:
    'Forensic AI-generated music detection. Per-window verdicts, structural '
    + 'analysis and signal measurements.',
  openGraph: { type: 'website', siteName: 'Beat22 LABS' },
  twitter: { card: 'summary_large_image' },
}

export const viewport = {
  themeColor: '#131313',
  width: 'device-width',
  initialScale: 1,
}

// Browser extensions inject scripts into every page and their failures
// surface as this page's unhandled errors. A wallet extension that cannot
// find its own wallet reports "Failed to connect to MetaMask" here, and in
// development Next forwards it to the terminal as an unhandledRejection from
// the app - which is noise at best and, when something in this codebase
// actually breaks, an error log that has to be read past to find it.
//
// Only rejections that originate from an extension URL are swallowed. An
// error from our own code is left entirely alone, because suppressing those
// is how a real fault becomes invisible.
const SILENCE_EXTENSION_ERRORS = `
(function () {
  var fromExtension = function (v) {
    try {
      var s = (v && (v.stack || v.message)) || String(v || '');
      return s.indexOf('chrome-extension://') !== -1
          || s.indexOf('moz-extension://') !== -1
          || s.indexOf('safari-web-extension://') !== -1;
    } catch (e) { return false; }
  };
  window.addEventListener('unhandledrejection', function (e) {
    if (fromExtension(e.reason)) e.preventDefault();
  });
  window.addEventListener('error', function (e) {
    if (fromExtension(e.error) || fromExtension(e.filename)) e.preventDefault();
  }, true);
})();
`

export default function RootLayout({ children }) {
  return (
    <html lang="en" data-theme="dark" className={inter.variable}>
      <head>
        {/* Installed before anything else runs, since an extension can fail
            before React has mounted. */}
        <script dangerouslySetInnerHTML={{ __html: SILENCE_EXTENSION_ERRORS }} />
      </head>
      <body>
        <ScrollToTop />
        <div className="site">
          <a href="#main" className="skip-link">Skip to content</a>
          <SiteHeader />
          <div id="main" className="site-main">
            {children}
          </div>
          <SiteFooter />
        </div>
      </body>
    </html>
  )
}
