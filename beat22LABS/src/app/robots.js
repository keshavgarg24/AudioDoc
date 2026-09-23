const SITE = process.env.NEXT_PUBLIC_SITE_URL || 'https://labs.beat22.com'

export default function robots() {
  return {
    rules: [{ userAgent: '*', allow: '/' }],
    sitemap: `${SITE}/sitemap.xml`,
  }
}
