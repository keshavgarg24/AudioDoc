// Per-page metadata and structured data.
//
// The Vite build injected these tags into the DOM at route time, which meant a
// crawler that does not execute JavaScript saw an empty shell. Under the App
// Router the same information is rendered on the server, so the tags are in the
// initial HTML for every route. These helpers just shape `toolConfig` entries
// into the objects Next expects.

/** Metadata for one tool page, derived from its toolConfig entry. */
export function toolMetadata(tool) {
  return {
    title: tool.metaTitle,
    description: tool.metaDescription,
    keywords: tool.keywords,
    alternates: { canonical: tool.route },
    openGraph: {
      title: `${tool.metaTitle} | Beat22 LABS`,
      description: tool.metaDescription,
      url: tool.route,
      type: 'website',
    },
  }
}

/** Metadata for any non-tool page. */
export function pageMetadata({ title, description, path, keywords }) {
  return {
    title,
    description,
    keywords,
    alternates: { canonical: path },
    openGraph: {
      title: `${title} | Beat22 LABS`,
      description,
      url: path,
      type: 'website',
    },
  }
}

/** Schema.org WebApplication block for a tool. */
export function toolJsonLd(tool, origin = '') {
  return {
    '@context': 'https://schema.org',
    '@type': 'WebApplication',
    name: tool.name,
    applicationCategory: 'MultimediaApplication',
    operatingSystem: 'Any',
    url: `${origin}${tool.route}`,
    description: tool.metaDescription,
    offers: { '@type': 'Offer', price: '0', priceCurrency: 'USD' },
  }
}

/** Schema.org FAQPage block. Google only renders this when the answers are
 *  visible on the page, which they are — the FAQ section is not collapsed
 *  behind a fetch. */
export function faqJsonLd(faq) {
  return {
    '@context': 'https://schema.org',
    '@type': 'FAQPage',
    mainEntity: faq.map((f) => ({
      '@type': 'Question',
      name: f.q,
      acceptedAnswer: { '@type': 'Answer', text: f.a },
    })),
  }
}

/** Renders one or more JSON-LD blocks into the document.
 *
 *  Next hoists this into <head> automatically when it appears in a server
 *  component, so it lands in the initial HTML rather than being appended after
 *  hydration the way the old implementation did.
 *
 *  The payload is built from `toolConfig`, which is static and authored in the
 *  repo, so it is not attacker-controlled. `<` is escaped regardless: a literal
 *  `</script>` anywhere in a description would otherwise close the tag early
 *  and spill the rest of the JSON into the document as markup.
 */
export function JsonLd({ data }) {
  const json = JSON.stringify(data).replace(/</g, '\\u003c')
  return (
    <script
      type="application/ld+json"
      dangerouslySetInnerHTML={{ __html: json }}
    />
  )
}
