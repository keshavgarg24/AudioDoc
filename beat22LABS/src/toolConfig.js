// Page content for each tool.
//
// This is the SEO surface: the copy here is what a search engine indexes and
// what a visitor reads before deciding to upload anything. It is deliberately
// kept out of the components so the same table can drive the router, the tools
// index, the sitemap and a future prerender step.
//
// The `honest` field on each tool is not marketing hedging. Every tool states
// what it cannot do, in the page itself, because these are sold to people who
// can check the claims against their own ears.

export const TOOLS = [
  {
    slug: 'master-check',
    route: '/mastering-check',
    typicalSeconds: [5, 20],
    name: 'Master Check',
    tagline: 'Is your master ready to release?',
    metaTitle: 'Free Mastering Check - LUFS, True Peak & Streaming Loudness Analyzer',
    metaDescription:
      'Check your master free. Measure integrated LUFS, true peak, dynamic range and '
      + 'stereo width, and see exactly how Spotify, Apple Music, YouTube and TikTok '
      + 'will change your loudness. Results in seconds, no signup.',
    keywords: ['lufs meter online', 'true peak checker', 'mastering analyzer',
      'spotify loudness check', 'free mastering tool'],
    inputs: [{ name: 'file', label: 'Your master', hint: 'WAV, MP3, FLAC or AIFF up to 50 MB' }],
    intro:
      'Upload a master and get the numbers every streaming platform uses to decide '
      + 'how loud your track will actually play. Integrated loudness, true peak, '
      + 'loudness range and stereo behaviour, measured to the same standard the '
      + 'platforms measure with.',
    valueProps: [
      { t: 'Know your loudness before you upload',
        d: 'See your integrated LUFS against every major platform target, and exactly how many dB each one will turn you down.' },
      { t: 'Catch true peak overs',
        d: 'True peak is measured with 4x oversampling, which exposes the intersample peaks that distort after MP3 or AAC encoding.' },
      { t: 'Find delivery blockers',
        d: 'Clipping, phase problems, wide sub bass and over-compression are flagged before a distributor rejects the file.' },
    ],
    howItWorks: [
      'Your file is decoded at full rate, because loudness is defined on the delivered signal.',
      'Integrated loudness, short-term and momentary loudness are measured to ITU-R BS.1770-4.',
      'True peak is measured on a 4x oversampled signal to catch intersample overs.',
      'Your loudness is compared against each platform’s published normalisation target.',
    ],
    honest: {
      strong: 'This is measurement, not estimation. It implements a published broadcast '
        + 'standard, so the numbers match any other compliant meter to within 0.1 LU.',
      limits: 'Tonal-balance commentary is guidance benchmarked against genre profiles, '
        + 'not a measured standard. The measurements and the advice are labelled separately.',
    },
    faq: [
      { q: 'What LUFS should I master to?',
        a: 'Most streaming platforms normalise to around -14 LUFS integrated. Mastering much louder than that does not make you louder on Spotify, it just reduces your dynamic range after the platform turns you down. Aim for the target of the platform that matters most to you, and keep true peak at or below -1.0 dBTP.' },
      { q: 'Why is true peak different from sample peak?',
        a: 'Sample peak only looks at the samples in the file. When a lossy encoder reconstructs the waveform it can overshoot between samples, producing peaks that were never in the original. True peak measures that by oversampling, which is why delivery specifications are written in dBTP.' },
      { q: 'Is this the same as a real mastering engineer?',
        a: 'No. This measures your master against objective delivery standards and tells you where it sits. It does not make creative decisions about tone, balance or arrangement, which is what a mastering engineer is actually for.' },
      { q: 'Is it free?',
        a: 'Yes. Upload a file and get the full measurement set with no account.' },
    ],
  },

  {
    slug: 'tempo-lab',
    route: '/bpm-finder',
    typicalSeconds: [1, 5],
    name: 'Tempo Lab',
    tagline: 'Find the BPM, and the timing behind it.',
    metaTitle: 'Free BPM Finder & Tempo Analyzer - Detect Song Tempo Online',
    metaDescription:
      'Find any song’s BPM free. Detects tempo, half and double time, tempo drift '
      + 'across the track, swing percentage and how tight the timing is against the '
      + 'grid. Upload any audio file, no signup.',
    keywords: ['bpm finder', 'tempo detector online', 'song bpm analyzer',
      'swing percentage', 'free bpm counter'],
    inputs: [{ name: 'file', label: 'Your track', hint: 'WAV, MP3, FLAC or AIFF up to 50 MB' }],
    intro:
      'Get the tempo, plus the parts a BPM number leaves out: whether the track '
      + 'could equally be read at half or double time, whether the tempo drifts, '
      + 'and how far the performance sits off the grid.',
    valueProps: [
      { t: 'Half and double time, both shown',
        d: 'Whether a track is 70 or 140 is often genuinely ambiguous. Rather than guessing, every plausible reading is shown so you can pick the one you count.' },
      { t: 'Tempo drift across the track',
        d: 'A programmed track holds one tempo. A live take does not. The tempo curve shows you which you have.' },
      { t: 'Swing and grid tightness',
        d: 'Measured against the detected beats, so it is correct regardless of which metrical level you read the tempo at.' },
    ],
    howItWorks: [
      'Onset strength is computed across the track and autocorrelated to find the pulse.',
      'A beat grid is tracked, and tempo is measured in windows to expose drift.',
      'Every onset is measured against its nearest beat using that beat’s own local interval.',
      'Swing is derived from where offbeat onsets land between grid positions.',
    ],
    honest: {
      strong: 'Around 90% accurate on steady 4/4 electronic, pop and hip-hop. Grid '
        + 'tightness and swing are measured relative to the detected beats, so they '
        + 'stay correct even when the tempo octave is ambiguous.',
      limits: 'Weaker on live, rubato, classical and ambient material, where a single '
        + 'steady pulse may not exist. When the pulse is unclear the confidence '
        + 'says so rather than returning a confident wrong number.',
    },
    faq: [
      { q: 'Why does it show two or three different BPMs?',
        a: 'Because more than one can be correct. A trap beat at 140 BPM is frequently counted as 70. Both describe the same music, they just count a different pulse. The reading marked primary is the one the tracker settled on; the others are the same tempo at a different metrical level.' },
      { q: 'What is swing percentage?',
        a: 'It measures how far offbeat notes are pushed from the exact halfway point between beats. 50% is perfectly straight. Around 54-58% is a subtle groove. 62% and above is a hard shuffle, the kind you hear in classic boom bap.' },
      { q: 'Why is my BPM slightly off, like 119.8 instead of 120?',
        a: 'If the track was played rather than programmed, its real average tempo genuinely is not a round number. If it was programmed, a small offset usually means the detected grid is landing between beats. Check the tempo stability reading: locked means the tempo is steady and the number is reliable.' },
    ],
  },

  {
    slug: 'key-lab',
    route: '/key-finder',
    typicalSeconds: [2, 8],
    name: 'Key Lab',
    tagline: 'Find the key, and what mixes with it.',
    metaTitle: 'Free Key Finder - Detect Song Key, Scale & Camelot Code Online',
    metaDescription:
      'Find any song’s musical key free. Detects key and scale, gives the Camelot '
      + 'code for harmonic mixing, and shows compatible keys for your next track. '
      + 'Ranked alternatives with confidence, not one guess.',
    keywords: ['key finder', 'song key detector', 'camelot wheel',
      'harmonic mixing', 'free key finder online'],
    inputs: [{ name: 'file', label: 'Your track', hint: 'WAV, MP3, FLAC or AIFF up to 50 MB' }],
    intro:
      'Get the key, the scale notes, the Camelot code and the keys that mix with '
      + 'it. Always with ranked alternatives, because key detection is genuinely '
      + 'ambiguous more often than most tools admit.',
    valueProps: [
      { t: 'Camelot code for harmonic mixing',
        d: 'Get the code and the three standard compatible moves, so you know what to play next.' },
      { t: 'Ranked alternatives, not one guess',
        d: 'The top three candidates are always shown with their scores, so you can see how close the call was.' },
      { t: 'Better tonic detection',
        d: 'Bass register and phrase edges are weighted separately, which is what separates C major from A minor when the notes are identical.' },
    ],
    howItWorks: [
      'A constant-Q chroma profile is built across the whole track.',
      'A second profile restricted to the low octaves votes on the root separately.',
      'The opening and closing windows are weighted more heavily, since popular music tends to resolve to the tonic there.',
      'All 24 keys are scored and ranked, and the margin between the top two sets the confidence.',
    ],
    honest: {
      strong: 'Around 75% exact, rising to roughly 90% when relative-major and '
        + 'perfect-fifth relationships count as near matches.',
      limits: 'Relative major and minor share all seven notes and cannot be fully '
        + 'separated from pitch content alone. For harmonic mixing this matters '
        + 'less than it sounds: they share a Camelot code, so the most common '
        + 'error does not change the mixing answer.',
    },
    faq: [
      { q: 'What is a Camelot code?',
        a: 'A number and a letter that make harmonic mixing simple. Keys with adjacent codes sound good together. Same number with the other letter is the relative major or minor, and plus or minus one on the same letter is the neighbouring key. Both are safe transitions.' },
      { q: 'Why does it sometimes say low confidence?',
        a: 'Usually because the top two candidates are a relative major and minor pair, which contain exactly the same seven notes. Deciding between them requires hearing which note the music resolves to, and that is genuinely ambiguous in a lot of loop-based music. For mixing purposes either answer works, because they share a Camelot code.' },
      { q: 'Can it detect chords?',
        a: 'A rough progression is estimated, but chord detection from audio is materially harder than key detection and is labelled approximate. Trust the key and the Camelot code; treat the chord list as a starting point.' },
    ],
  },

  {
    slug: 'reference-match',
    route: '/reference-track-analyzer',
    typicalSeconds: [4, 15],
    name: 'Reference Match',
    tagline: 'Hear the gap. Now see it.',
    metaTitle: 'Reference Track Analyzer - Compare Your Mix to Any Song Free',
    metaDescription:
      'Compare your mix against a reference track free. See the exact dB difference '
      + 'in every frequency band, loudness, dynamics and stereo width, with the EQ '
      + 'moves that close the gap. Level matched automatically.',
    keywords: ['reference track analyzer', 'compare mix to reference',
      'tonal balance', 'mix comparison tool', 'frequency comparison'],
    inputs: [
      { name: 'file', label: 'Your track', hint: 'The mix you are working on' },
      { name: 'reference', label: 'Reference track', hint: 'A song you want to sound closer to' },
    ],
    intro:
      'Upload your mix and a track you want to sound closer to. Get the exact '
      + 'difference in every frequency band, in dB, with the moves that close it. '
      + 'Both files are level matched first, so you are comparing tone, not volume.',
    valueProps: [
      { t: 'Level matched before comparison',
        d: 'Without this every commercial reference reads as louder in all seven bands, which tells you nothing. Both files are normalised to the same loudness first.' },
      { t: 'Band-by-band dB differences',
        d: 'Seven bands from sub to air, each with the exact difference and the EQ move that closes it.' },
      { t: 'Dynamics and stereo too',
        d: 'Loudness range, peak-to-loudness and stereo width are compared alongside the frequency content.' },
    ],
    howItWorks: [
      'Both files are decoded and normalised to the same integrated loudness.',
      'Energy is measured in seven bands from 20 Hz to 16 kHz on each file.',
      'The difference per band is reported in dB, largest gap first.',
      'Loudness range, stereo width and phase are compared as separate axes.',
    ],
    honest: {
      strong: 'The differences are measurement-grade. Both files are level matched, '
        + 'then compared band by band, and the numbers are exact.',
      limits: 'A spectral difference cannot distinguish a mixing choice from an '
        + 'arrangement one. If the reference has a bright lead vocal and your track '
        + 'is instrumental, the difference in that band is not a mixing problem. '
        + 'Compare tracks of similar genre and arrangement density.',
    },
    faq: [
      { q: 'Does my reference track get stored?',
        a: 'No. Reference audio is measured and discarded. Only the derived numbers are kept, which is both cheaper and the right answer for copyrighted material you do not own.' },
      { q: 'Why does it say I need more high mid when my mix sounds bright?',
        a: 'Almost always because the reference has a source your track does not, usually a lead vocal. The measurement is correct, but it is describing an arrangement difference rather than an EQ problem. This is the main limitation of any reference comparison and why the tool states it explicitly.' },
      { q: 'What makes a good reference track?',
        a: 'Same genre, similar arrangement density, and a master you actually like the sound of. Comparing a sparse acoustic demo against a dense festival mix produces accurate numbers that are not useful advice.' },
    ],
  },

  {
    slug: 'vocal-lab',
    route: '/vocal-analyzer',
    typicalSeconds: [12, 35],
    name: 'Vocal Lab',
    tagline: 'What your take is actually doing.',
    metaTitle: 'Free Vocal Analyzer - Pitch Accuracy, Range & Recording Quality',
    metaDescription:
      'Analyze your vocal free. Measures pitch accuracy in cents, vocal range, '
      + 'vibrato, timing against the beat, sibilance, plosives and noise floor. '
      + 'Upload an isolated vocal and see exactly what needs work.',
    keywords: ['vocal analyzer', 'pitch accuracy test', 'vocal range finder',
      'singing analysis', 'check vocal recording quality'],
    inputs: [{ name: 'file', label: 'Your vocal', hint: 'An isolated vocal, not a full mix' }],
    intro:
      'Upload an isolated vocal and get measurable feedback: how far off pitch you '
      + 'are in cents, your range and comfortable register, vibrato rate, timing '
      + 'against the beat, and the recording problems worth fixing.',
    valueProps: [
      { t: 'Pitch accuracy in cents',
        d: 'Not "you sound flat" but "you average 18 cents flat", measured against equal temperament across the whole take.' },
      { t: 'Range and tessitura',
        d: 'Your full range and, more usefully, where your voice actually lives most of the time.' },
      { t: 'Recording problems, found',
        d: 'Sibilance, plosives and noise floor measured, each with the specific fix.' },
    ],
    howItWorks: [
      'Fundamental frequency is tracked frame by frame across the singing range.',
      'Each frame is compared to the nearest equal-tempered semitone, in cents.',
      'Syllable onsets are measured against the beat grid where one exists.',
      'Sibilant, plosive and noise-floor energy are measured in their own bands.',
    ],
    honest: {
      strong: 'Pitch, range, timing, sibilance and plosive measurements are '
        + 'measurement-grade on a clean isolated vocal.',
      limits: 'This tool does not score tone, emotion, character or commercial '
        + 'appeal. Those are not measurable from audio, and a number for them '
        + 'would be invented. It also needs an isolated vocal: on a full mix the '
        + 'pitch tracker follows whatever is loudest.',
    },
    faq: [
      { q: 'Can I upload a full song?',
        a: 'You can, but the pitch results will describe whatever pitched source is loudest, which is often the bass or a synth rather than the voice. The tool checks for this and warns you when the input does not look like an isolated vocal.' },
      { q: 'What is a good pitch accuracy score?',
        a: 'Under 12 cents average deviation is tight, at or beyond the level of a polished commercial vocal. Under 22 is good. Above 35 means pitch is wandering enough that a retake will usually beat correction.' },
      { q: 'Does it tell me if I am a good singer?',
        a: 'No, and it deliberately will not. It measures pitch, timing and recording quality. Tone, emotion and appeal are not measurable from a waveform, so no number is offered for them.' },
    ],
  },

  {
    slug: 'beat-vocal-fit',
    route: '/beat-vocal-fit',
    typicalSeconds: [4, 15],
    name: 'Beat and Vocal Fit',
    tagline: 'Make your vocal sit on your beat.',
    metaTitle: 'Beat & Vocal Fit Checker - Match Your Vocal to Any Instrumental',
    metaDescription:
      'Upload a beat and a vocal separately and find out what to change. Key match, '
      + 'tempo match, timing offset in milliseconds, and the exact frequencies where '
      + 'your beat is burying your vocal.',
    keywords: ['vocal beat matching', 'frequency masking', 'mix vocal to beat',
      'key matching tool', 'vocal sitting in mix'],
    inputs: [
      { name: 'beat', label: 'The beat', hint: 'Your instrumental, no vocal' },
      { name: 'vocal', label: 'The vocal', hint: 'Your isolated vocal take' },
    ],
    intro:
      'Upload your instrumental and your vocal as separate files. Get the key '
      + 'difference in semitones, the tempo difference, how many milliseconds your '
      + 'vocal is off the grid, and exactly which frequencies your beat is fighting '
      + 'your vocal for.',
    valueProps: [
      { t: 'Find the frequency clash',
        d: 'The one thing that is hard to hear and easy to measure: where the beat holds energy in the band your vocal needs to be understood in.' },
      { t: 'Key and tempo, in actionable numbers',
        d: 'Not "they clash" but "pitch the beat up 2 semitones" and "a 3.1% stretch closes the tempo gap".' },
      { t: 'Timing offset in milliseconds',
        d: 'How far ahead or behind the beat your vocal sits on average, and which way to nudge it.' },
    ],
    howItWorks: [
      'Both files are analysed separately for key, tempo and beat grid.',
      'Vocal onsets are cross-referenced against the beat’s grid to find the global offset.',
      'Both are level matched, then compared band by band for spectral overlap.',
      'Competition inside the 1-4 kHz intelligibility range is weighted most heavily.',
    ],
    honest: {
      strong: 'Key, tempo and timing relationships are near-exact. Masking is an '
        + 'exact measurement of where two spectra overlap.',
      limits: 'Both files must be uploaded separately. A vocal already mixed into '
        + 'the beat cannot be analysed this way, and no stem separation is '
        + 'performed because separating a mix would add artefacts and then every '
        + 'number would describe the artefacts. Recommendations follow standard '
        + 'mixing practice; they are not a judgement of taste.',
    },
    faq: [
      { q: 'Why do I need to upload two separate files?',
        a: 'Because measuring the beat and the vocal independently is what makes the results exact. Separating a finished mix into stems introduces artefacts, and the analysis would then describe those artefacts rather than your music. If you made the track, you already have both files.' },
      { q: 'What is frequency masking?',
        a: 'When two sources hold significant energy in the same frequency range, the louder one hides the quieter one. For vocals it matters most between roughly 1 and 4 kHz, which is where intelligibility lives. If your beat is dense there, the vocal sounds buried no matter how far you push the fader.' },
      { q: 'It says my keys do not match. Which should I change?',
        a: 'Usually the beat, because pitching an instrumental is less noticeable than pitching a voice. The tool gives you the semitone shift in the direction that requires the smallest move.' },
    ],
  },


]

export const BY_SLUG = Object.fromEntries(TOOLS.map((t) => [t.slug, t]))
export const BY_ROUTE = Object.fromEntries(TOOLS.map((t) => [t.route, t]))
