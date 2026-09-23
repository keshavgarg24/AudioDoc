import ToolsIndex from '../../components/pages/ToolsIndex.jsx'
import { pageMetadata } from '../../lib/seo.js'

export const metadata = pageMetadata({
  title: 'Free Music Production Tools - BPM, Key, Mastering & Mix Analysis',
  description:
    'Free tools for producers and artists. Find BPM and key, check your master '
    + 'against streaming targets, compare your mix to a reference, analyse a '
    + 'vocal, and see how your production compares to what is charting.',
  path: '/tools',
})

export default function Page() {
  return <ToolsIndex />
}
