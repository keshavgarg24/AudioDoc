import ApiDocs from '../../../components/pages/ApiDocs.jsx'
import { pageMetadata } from '../../../lib/seo.js'

export const metadata = pageMetadata({
  title: 'API Reference',
  description:
    'Full HTTP API reference: endpoints, authentication, error codes, rate '
    + 'limits and worked examples for every analysis and tool endpoint.',
  path: '/docs/api',
})

export default function Page() {
  return <ApiDocs />
}
