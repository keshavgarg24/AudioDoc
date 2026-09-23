// /tools/<slug> is the URL shape people guess and type. It permanently
// redirects to the keyword URL rather than 404ing or serving the same page at
// two addresses, which would split ranking between them.

import { permanentRedirect } from 'next/navigation'
import { BY_SLUG, TOOLS } from '../../../toolConfig.js'

export const dynamicParams = false

export function generateStaticParams() {
  return TOOLS.map((t) => ({ slug: t.slug }))
}

export default async function Page({ params }) {
  const { slug } = await params
  const tool = BY_SLUG[slug]
  permanentRedirect(tool ? tool.route : '/tools')
}
