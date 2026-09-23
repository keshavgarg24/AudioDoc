import { TOOLS } from '../toolConfig.js'

// Generated rather than hand-maintained. The static public/sitemap.xml had to
// be edited by hand every time a tool was added or a route changed, and it had
// already drifted. This cannot drift: it is the same table that builds the
// routes.
const SITE = process.env.NEXT_PUBLIC_SITE_URL || 'https://labs.beat22.com'

export default function sitemap() {
  const now = new Date()

  return [
    { url: `${SITE}/`, lastModified: now, changeFrequency: 'weekly', priority: 1.0 },
    { url: `${SITE}/tools`, lastModified: now, changeFrequency: 'weekly', priority: 0.9 },
    ...TOOLS.map((t) => ({
      url: `${SITE}${t.route}`,
      lastModified: now,
      changeFrequency: 'weekly',
      priority: 0.8,
    })),
    { url: `${SITE}/docs`, lastModified: now, changeFrequency: 'monthly', priority: 0.5 },
    { url: `${SITE}/docs/api`, lastModified: now, changeFrequency: 'monthly', priority: 0.5 },
  ]
}
