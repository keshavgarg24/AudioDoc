import Docs from '../../components/Docs.jsx'
import { pageMetadata } from '../../lib/seo.js'

// The long-form guide, previously reachable only through a view flag on the
// landing page. It has a URL now, so it can be linked and indexed.
export const metadata = pageMetadata({
  title: 'Documentation',
  description:
    'How Beat22 LABS analyses audio: detection modes, analysis pipeline, '
    + 'response format, and how to read a report.',
  path: '/docs',
})

export default function Page() {
  return <Docs />
}
