// Every tool page, at its keyword URL.
//
// The keyword URLs (/bpm-finder, /key-finder, ...) sit at the root because they
// are the ones that rank, and moving them under a namespace would throw away
// that work. A root dynamic segment is safe here: Next matches static segments
// before dynamic ones, so /tools and /docs still resolve to their own folders.
//
// `dynamicParams = false` means anything not in generateStaticParams 404s
// rather than rendering an empty tool shell.

import { notFound } from 'next/navigation'
import { BY_SLUG, TOOLS } from '../../toolConfig.js'
import { JsonLd, faqJsonLd, toolJsonLd, toolMetadata } from '../../lib/seo.js'
import ToolPage from '../../components/pages/ToolPage.jsx'

export const dynamicParams = false

// The route table is derived from toolConfig, so adding a tool needs no
// routing change: one entry there produces the page, the metadata, the
// sitemap row and the /tools/<slug> alias.
export function generateStaticParams() {
  return TOOLS.map((t) => ({ slug: t.route.replace(/^\//, '') }))
}

export async function generateMetadata({ params }) {
  const { slug } = await params
  const tool = TOOLS.find((t) => t.route === `/${slug}`)
  if (!tool) return {}
  return toolMetadata(tool)
}

export default async function Page({ params }) {
  const { slug } = await params
  const tool = TOOLS.find((t) => t.route === `/${slug}`)
  if (!tool) notFound()

  return (
    <>
      <JsonLd data={toolJsonLd(tool)} />
      <JsonLd data={faqJsonLd(tool.faq)} />
      <ToolPage tool={tool} />
    </>
  )
}
