import HomePage from '../components/pages/HomePage.jsx'

export const metadata = {
  title: 'Beat22 LABS — AI Music Detection',
  description:
    'Forensic AI-generated music detection. Per-window verdicts, structural '
    + 'analysis and signal measurements. Upload a track and see where the '
    + 'evidence sits, second by second.',
  alternates: { canonical: '/' },
}

export default function Page() {
  return <HomePage />
}
