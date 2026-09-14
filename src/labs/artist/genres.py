"""Genre reference profiles.

These are curated production references, not statistics measured from a
licensed corpus. They encode widely agreed working ranges: the tempo a drill
beat sits at, where a house record is mastered, how wide a modern pop mix is.
They are good enough to tell an artist "your track is 12 BPM slow for this
genre and 3 LU quieter than the norm", which is the useful part.

When a reference corpus becomes available, replace the numbers here with
measured percentiles from it. The consuming code in artist.py reads only the
keys below, so swapping the source changes nothing downstream.

Each profile carries:
  bpm            (lo, hi) working range, and `bpm_typical`
  lufs           integrated loudness target the genre is usually mastered to
  lra            (lo, hi) acceptable loudness range in LU
  centroid_hz    (lo, hi) spectral centroid, a proxy for overall brightness
  width_pct      (lo, hi) stereo width
  swing_pct      (lo, hi) expected swing; 50 is straight
  intro_s        (lo, hi) intro length before the first main event
  sections       (lo, hi) number of distinct arrangement sections
  energy         (lo, hi) 0..1 from industry.catalogue_features
  danceability   (lo, hi) 0..1
  quantization   expected feel: "tight" | "loose" | "either"
  signature      the two or three things that actually define the genre
  build          concrete production moves to push a track toward this genre
"""
from __future__ import annotations

from typing import Dict, List, Optional

# --------------------------------------------------------------------------
# Profiles
# --------------------------------------------------------------------------
GENRES: Dict[str, Dict] = {
    "trap": {
        "label": "Trap",
        "family": "hip hop",
        "bpm": (130, 160), "bpm_typical": 140, "half_time": True,
        "lufs": -8.0, "lra": (3.0, 7.0),
        "centroid_hz": (1800, 3200), "width_pct": (35, 65),
        "swing_pct": (48, 54), "intro_s": (4, 16), "sections": (4, 8),
        "energy": (0.6, 0.9), "danceability": (0.6, 0.9),
        "quantization": "tight",
        "signature": [
            "808 sub bass with pitch glides carrying the bassline",
            "rolling hi hats with triplet and 1/32 bursts",
            "sparse, hard-hitting snare or clap on the 3",
        ],
        "build": [
            "Replace the bass with a tuned 808 and glide between root notes.",
            "Program hi hats at 1/16 with triplet rolls every 2 or 4 bars.",
            "Move the backbeat to beat 3 only, so the groove reads half time.",
            "Keep the low end mono below 120 Hz so the 808 stays centred.",
        ],
    },
    "drill": {
        "label": "Drill",
        "family": "hip hop",
        "bpm": (138, 150), "bpm_typical": 142, "half_time": True,
        "lufs": -8.0, "lra": (3.0, 7.0),
        "centroid_hz": (1600, 3000), "width_pct": (30, 60),
        "swing_pct": (48, 54), "intro_s": (4, 12), "sections": (4, 7),
        "energy": (0.6, 0.9), "danceability": (0.6, 0.85),
        "quantization": "tight",
        "signature": [
            "sliding 808 that moves in the second half of the bar",
            "syncopated hi hats with wide rhythmic gaps",
            "dark, minor, often two-chord loop",
        ],
        "build": [
            "Slide the 808 down a fifth or octave on the offbeat of 3.",
            "Use a sparse hat pattern with rests, not a constant roll.",
            "Write the melody in natural or harmonic minor, two chords maximum.",
            "Pull the snare slightly late for the characteristic lurch.",
        ],
    },
    "boom_bap": {
        "label": "Boom Bap",
        "family": "hip hop",
        "bpm": (82, 98), "bpm_typical": 90, "half_time": False,
        "lufs": -10.0, "lra": (5.0, 10.0),
        "centroid_hz": (1200, 2400), "width_pct": (30, 60),
        "swing_pct": (54, 62), "intro_s": (4, 16), "sections": (4, 7),
        "energy": (0.45, 0.75), "danceability": (0.55, 0.8),
        "quantization": "loose",
        "signature": [
            "dusty sampled loop with audible filtering",
            "hard kick on 1, snare on 2 and 4, swung 1/16 feel",
            "minimal sub, weight comes from the kick body",
        ],
        "build": [
            "Add 8 to 12 percent swing so the 1/16s breathe.",
            "Roll off above 12 kHz and add light saturation for the sampled feel.",
            "Put the snare firmly on 2 and 4 and let it ring.",
            "Leave the mix narrower than modern pop; keep it close to centre.",
        ],
    },
    "lofi_hiphop": {
        "label": "Lo-fi Hip Hop",
        "family": "hip hop",
        "bpm": (70, 90), "bpm_typical": 82, "half_time": False,
        "lufs": -14.0, "lra": (5.0, 11.0),
        "centroid_hz": (900, 1900), "width_pct": (25, 55),
        "swing_pct": (55, 65), "intro_s": (2, 10), "sections": (2, 5),
        "energy": (0.2, 0.5), "danceability": (0.5, 0.75),
        "quantization": "loose",
        "signature": [
            "heavy high frequency rolloff and tape wobble",
            "jazz chords, seventh and ninth voicings",
            "soft, unhurried swung drums",
        ],
        "build": [
            "Low pass around 10 to 12 kHz and add gentle wow and flutter.",
            "Voice the chords as minor 7ths and 9ths rather than triads.",
            "Push swing past 58 percent and soften transients.",
            "Master conservatively; this genre is not loud.",
        ],
    },
    "phonk": {
        "label": "Phonk",
        "family": "hip hop",
        "bpm": (130, 160), "bpm_typical": 140, "half_time": True,
        "lufs": -7.5, "lra": (2.5, 6.0),
        "centroid_hz": (1600, 3200), "width_pct": (30, 65),
        "swing_pct": (48, 56), "intro_s": (2, 10), "sections": (3, 6),
        "energy": (0.7, 0.95), "danceability": (0.6, 0.9),
        "quantization": "tight",
        "signature": [
            "distorted cowbell melody",
            "heavily saturated, clipped 808",
            "Memphis rap vocal chops, lo-fi and pitched down",
        ],
        "build": [
            "Write the hook on a distorted cowbell rather than a synth lead.",
            "Drive the 808 into clipping so it distorts on every hit.",
            "Pitch vocal chops down and degrade them heavily.",
        ],
    },
    "afrobeats": {
        "label": "Afrobeats",
        "family": "global pop",
        "bpm": (98, 118), "bpm_typical": 106, "half_time": False,
        "lufs": -9.0, "lra": (4.0, 8.0),
        "centroid_hz": (1600, 3000), "width_pct": (45, 80),
        "swing_pct": (52, 60), "intro_s": (4, 14), "sections": (4, 7),
        "energy": (0.6, 0.85), "danceability": (0.7, 0.95),
        "quantization": "loose",
        "signature": [
            "three-two clave-derived percussion pattern",
            "log drum or muted plucked bass",
            "bright, dry, percussion-forward mix",
        ],
        "build": [
            "Build the groove on shaker and conga layers, not hi hats.",
            "Use a short, muted bass rather than a sustained sub.",
            "Add light swing so the percussion pushes and pulls.",
            "Widen the percussion and keep the vocal dry and forward.",
        ],
    },
    "amapiano": {
        "label": "Amapiano",
        "family": "global pop",
        "bpm": (108, 118), "bpm_typical": 112, "half_time": False,
        "lufs": -9.0, "lra": (4.0, 9.0),
        "centroid_hz": (1400, 2800), "width_pct": (50, 85),
        "swing_pct": (52, 60), "intro_s": (8, 24), "sections": (3, 6),
        "energy": (0.5, 0.8), "danceability": (0.7, 0.95),
        "quantization": "loose",
        "signature": [
            "log drum bassline as the lead hook",
            "airy jazz piano chords with long tails",
            "shaker-driven groove with wide space",
        ],
        "build": [
            "Make the log drum the melodic hook, not a support element.",
            "Voice piano chords high and wide with heavy reverb.",
            "Let sections run long; this genre builds slowly.",
        ],
    },
    "reggaeton": {
        "label": "Reggaeton",
        "family": "latin",
        "bpm": (88, 100), "bpm_typical": 94, "half_time": False,
        "lufs": -8.0, "lra": (3.0, 7.0),
        "centroid_hz": (1600, 3000), "width_pct": (40, 75),
        "swing_pct": (48, 56), "intro_s": (2, 10), "sections": (4, 7),
        "energy": (0.65, 0.9), "danceability": (0.75, 0.95),
        "quantization": "tight",
        "signature": [
            "dembow pattern: kick on 1 and 3, snare in 3-3-2",
            "short, punchy sub bass locked to the kick",
            "dense, dry percussion",
        ],
        "build": [
            "Program the dembow: kick on the quarter, snare in 3-3-2 subdivision.",
            "Shorten the bass so it sits inside the kick, not under it.",
            "Keep everything tight to the grid; this groove is machine-quantized.",
        ],
    },
    "house": {
        "label": "House",
        "family": "dance",
        "bpm": (118, 128), "bpm_typical": 124, "half_time": False,
        "lufs": -8.0, "lra": (3.0, 7.0),
        "centroid_hz": (1800, 3400), "width_pct": (55, 90),
        "swing_pct": (50, 58), "intro_s": (16, 40), "sections": (4, 8),
        "energy": (0.65, 0.9), "danceability": (0.75, 0.95),
        "quantization": "tight",
        "signature": [
            "four on the floor kick",
            "offbeat open hat on the and of every beat",
            "long DJ-friendly intro and outro",
        ],
        "build": [
            "Put a kick on every quarter note.",
            "Place an open hat on every offbeat eighth.",
            "Extend the intro to at least 16 bars of beat for DJ mixing.",
            "Add a clap layered on 2 and 4 over the kick.",
        ],
    },
    "tech_house": {
        "label": "Tech House",
        "family": "dance",
        "bpm": (124, 130), "bpm_typical": 127, "half_time": False,
        "lufs": -7.5, "lra": (2.5, 6.0),
        "centroid_hz": (2000, 3600), "width_pct": (50, 85),
        "swing_pct": (50, 56), "intro_s": (16, 48), "sections": (4, 7),
        "energy": (0.7, 0.92), "danceability": (0.8, 0.97),
        "quantization": "tight",
        "signature": [
            "rolling, syncopated bassline under a straight kick",
            "minimal top end, heavy groove focus",
            "tight, dry percussion with short tails",
        ],
        "build": [
            "Write a rolling 1/16 bass that ducks hard against the kick.",
            "Strip the arrangement back; tech house is subtractive.",
            "Sidechain everything to the kick for pump.",
        ],
    },
    "deep_house": {
        "label": "Deep House",
        "family": "dance",
        "bpm": (118, 125), "bpm_typical": 122, "half_time": False,
        "lufs": -9.0, "lra": (4.0, 9.0),
        "centroid_hz": (1400, 2800), "width_pct": (55, 90),
        "swing_pct": (52, 60), "intro_s": (16, 40), "sections": (3, 6),
        "energy": (0.45, 0.75), "danceability": (0.7, 0.9),
        "quantization": "loose",
        "signature": [
            "warm, filtered pads and Rhodes chords",
            "soft kick with a rounded sub",
            "swung hats, relaxed groove",
        ],
        "build": [
            "Voice chords as 7ths and 9ths on Rhodes or a warm pad.",
            "Soften the kick transient and let the sub carry the weight.",
            "Add 5 to 8 percent swing to the hats.",
        ],
    },
    "techno": {
        "label": "Techno",
        "family": "dance",
        "bpm": (128, 145), "bpm_typical": 134, "half_time": False,
        "lufs": -7.5, "lra": (2.5, 6.0),
        "centroid_hz": (1800, 3600), "width_pct": (45, 85),
        "swing_pct": (48, 54), "intro_s": (16, 48), "sections": (3, 6),
        "energy": (0.75, 0.97), "danceability": (0.7, 0.92),
        "quantization": "tight",
        "signature": [
            "relentless four on the floor with minimal variation",
            "industrial, metallic percussion and noise layers",
            "long, gradual filter and reverb builds",
        ],
        "build": [
            "Reduce harmonic content; techno is texture and rhythm led.",
            "Automate filters and reverb over 16 to 32 bar spans.",
            "Use metallic, noisy percussion rather than tuned samples.",
        ],
    },
    "dnb": {
        "label": "Drum and Bass",
        "family": "dance",
        "bpm": (168, 178), "bpm_typical": 174, "half_time": False,
        "lufs": -7.5, "lra": (3.0, 7.0),
        "centroid_hz": (2000, 3800), "width_pct": (50, 90),
        "swing_pct": (48, 56), "intro_s": (8, 32), "sections": (4, 7),
        "energy": (0.8, 0.98), "danceability": (0.6, 0.85),
        "quantization": "tight",
        "signature": [
            "breakbeat with kick on 1 and snare on the and of 2",
            "deep sub bass at half the drum tempo",
            "two drops with a mid-track breakdown",
        ],
        "build": [
            "Program a two-step break: kick on 1, snare on the and of 2 and on 4.",
            "Write the bassline at half time against the drums.",
            "Structure around an intro, first drop, breakdown, second drop.",
        ],
    },
    "dubstep": {
        "label": "Dubstep",
        "family": "dance",
        "bpm": (138, 146), "bpm_typical": 140, "half_time": True,
        "lufs": -7.0, "lra": (3.0, 8.0),
        "centroid_hz": (1600, 3600), "width_pct": (55, 95),
        "swing_pct": (48, 54), "intro_s": (8, 24), "sections": (4, 7),
        "energy": (0.8, 0.99), "danceability": (0.55, 0.8),
        "quantization": "tight",
        "signature": [
            "half time drums with snare on the 3",
            "modulated, screaming bass as the lead",
            "long build into a hard drop",
        ],
        "build": [
            "Halve the drum feel: snare on 3 only.",
            "Make the bass the lead voice with heavy LFO or formant modulation.",
            "Build tension for 8 bars, then drop hard with a full stop before it.",
        ],
    },
    "future_bass": {
        "label": "Future Bass",
        "family": "dance",
        "bpm": (140, 160), "bpm_typical": 150, "half_time": True,
        "lufs": -8.0, "lra": (4.0, 9.0),
        "centroid_hz": (2000, 3800), "width_pct": (65, 98),
        "swing_pct": (48, 56), "intro_s": (4, 16), "sections": (4, 7),
        "energy": (0.7, 0.92), "danceability": (0.6, 0.85),
        "quantization": "tight",
        "signature": [
            "detuned supersaw chords with heavy pitch modulation",
            "wide, bright, lush stereo image",
            "vocal chop hook over the drop",
        ],
        "build": [
            "Use detuned supersaw stacks with per-note pitch bend on the chords.",
            "Widen aggressively; this genre lives on stereo width.",
            "Chop a vocal into the drop as the melodic hook.",
        ],
    },
    "edm_festival": {
        "label": "Festival EDM",
        "family": "dance",
        "bpm": (126, 132), "bpm_typical": 128, "half_time": False,
        "lufs": -7.0, "lra": (2.5, 6.0),
        "centroid_hz": (2000, 3800), "width_pct": (60, 95),
        "swing_pct": (48, 53), "intro_s": (16, 40), "sections": (5, 8),
        "energy": (0.85, 0.99), "danceability": (0.7, 0.92),
        "quantization": "tight",
        "signature": [
            "huge supersaw lead on the drop",
            "long 16 to 32 bar build with riser and snare roll",
            "maximum loudness, minimal dynamic range",
        ],
        "build": [
            "Extend the build to 16 bars with a rising riser and accelerating snare.",
            "Drop onto a wide supersaw lead doubled an octave up.",
            "Master loud and dense; this genre is mixed for large systems.",
        ],
    },
    "uk_garage": {
        "label": "UK Garage",
        "family": "dance",
        "bpm": (128, 138), "bpm_typical": 133, "half_time": False,
        "lufs": -8.5, "lra": (3.5, 8.0),
        "centroid_hz": (1800, 3400), "width_pct": (50, 88),
        "swing_pct": (58, 68), "intro_s": (8, 24), "sections": (4, 7),
        "energy": (0.65, 0.9), "danceability": (0.8, 0.97),
        "quantization": "loose",
        "signature": [
            "heavily swung two-step drum pattern",
            "skippy, syncopated hats and shakers",
            "chopped, pitched vocal hooks",
        ],
        "build": [
            "Push swing to 60 percent or higher; this is the defining feature.",
            "Drop the kick from beat 3 to create the two-step skip.",
            "Chop and pitch the vocal rather than looping it whole.",
        ],
    },
    "jersey_club": {
        "label": "Jersey Club",
        "family": "dance",
        "bpm": (130, 145), "bpm_typical": 138, "half_time": False,
        "lufs": -8.0, "lra": (3.0, 7.0),
        "centroid_hz": (1800, 3400), "width_pct": (40, 75),
        "swing_pct": (48, 54), "intro_s": (2, 10), "sections": (3, 6),
        "energy": (0.75, 0.95), "danceability": (0.8, 0.97),
        "quantization": "tight",
        "signature": [
            "five-kick triplet bed pattern",
            "bed squeak and vocal chop stabs",
            "short, high energy arrangement",
        ],
        "build": [
            "Program the signature kick pattern: three kicks then two on the offbeat.",
            "Chop a recognisable vocal into rhythmic stabs.",
            "Keep it short; sections turn over every 8 bars.",
        ],
    },
    "pop": {
        "label": "Pop",
        "family": "pop",
        "bpm": (95, 125), "bpm_typical": 112, "half_time": False,
        "lufs": -9.0, "lra": (4.0, 9.0),
        "centroid_hz": (1800, 3200), "width_pct": (50, 85),
        "swing_pct": (48, 56), "intro_s": (2, 10), "sections": (5, 9),
        "energy": (0.6, 0.88), "danceability": (0.6, 0.88),
        "quantization": "tight",
        "signature": [
            "chorus arrives inside the first 45 seconds",
            "vocal is the loudest element throughout",
            "clear verse, pre-chorus, chorus contrast",
        ],
        "build": [
            "Cut the intro to under 10 seconds and reach the chorus fast.",
            "Carve 2 to 4 kHz in the instrumental so the vocal sits on top.",
            "Add a pre-chorus that strips back before the chorus lifts.",
            "Make the chorus measurably wider and louder than the verse.",
        ],
    },
    "synth_pop": {
        "label": "Synth Pop",
        "family": "pop",
        "bpm": (100, 128), "bpm_typical": 116, "half_time": False,
        "lufs": -9.5, "lra": (4.0, 9.0),
        "centroid_hz": (1800, 3400), "width_pct": (55, 90),
        "swing_pct": (48, 54), "intro_s": (4, 14), "sections": (4, 8),
        "energy": (0.55, 0.85), "danceability": (0.6, 0.88),
        "quantization": "tight",
        "signature": [
            "analogue-style poly synth pads and arpeggios",
            "gated reverb on the snare",
            "bright, chorused, wide production",
        ],
        "build": [
            "Replace acoustic drums with gated, reverb-heavy electronic hits.",
            "Add a 1/16 arpeggio running under the chords.",
            "Use chorus and wide stereo on the pads.",
        ],
    },
    "rnb": {
        "label": "R&B",
        "family": "soul",
        "bpm": (60, 95), "bpm_typical": 76, "half_time": True,
        "lufs": -10.0, "lra": (5.0, 11.0),
        "centroid_hz": (1200, 2400), "width_pct": (45, 80),
        "swing_pct": (54, 64), "intro_s": (4, 16), "sections": (4, 8),
        "energy": (0.35, 0.7), "danceability": (0.5, 0.8),
        "quantization": "loose",
        "signature": [
            "lush extended chords, 9ths and 11ths",
            "laid back, behind-the-beat drum feel",
            "stacked vocal harmonies",
        ],
        "build": [
            "Extend the harmony past triads into 9ths and 11ths.",
            "Pull the snare 10 to 20 ms late for the behind-the-beat pocket.",
            "Leave headroom; R&B is not mastered as loud as pop.",
            "Stack vocal harmonies wide on the chorus.",
        ],
    },
    "neo_soul": {
        "label": "Neo Soul",
        "family": "soul",
        "bpm": (65, 95), "bpm_typical": 80, "half_time": True,
        "lufs": -11.0, "lra": (6.0, 13.0),
        "centroid_hz": (1100, 2200), "width_pct": (40, 75),
        "swing_pct": (56, 66), "intro_s": (4, 20), "sections": (3, 7),
        "energy": (0.3, 0.65), "danceability": (0.5, 0.78),
        "quantization": "loose",
        "signature": [
            "loose, deliberately unquantized drums",
            "Rhodes and warm analogue keys",
            "walking or melodic bass with real phrasing",
        ],
        "build": [
            "Un-quantize the drums; the human drift is the genre.",
            "Play the bass melodically rather than locking to the kick.",
            "Preserve dynamic range; do not compress it flat.",
        ],
    },
    "rock": {
        "label": "Rock",
        "family": "band",
        "bpm": (100, 150), "bpm_typical": 124, "half_time": False,
        "lufs": -9.5, "lra": (5.0, 11.0),
        "centroid_hz": (1600, 3200), "width_pct": (45, 85),
        "swing_pct": (48, 55), "intro_s": (4, 20), "sections": (4, 8),
        "energy": (0.7, 0.95), "danceability": (0.4, 0.7),
        "quantization": "loose",
        "signature": [
            "real kit with audible room and cymbal wash",
            "double-tracked guitars panned hard",
            "dynamic contrast between verse and chorus",
        ],
        "build": [
            "Double-track the rhythm guitars and pan them hard left and right.",
            "Use a real or convincingly humanized kit with room ambience.",
            "Keep the verse noticeably quieter than the chorus.",
        ],
    },
    "indie": {
        "label": "Indie",
        "family": "band",
        "bpm": (95, 140), "bpm_typical": 118, "half_time": False,
        "lufs": -11.0, "lra": (6.0, 13.0),
        "centroid_hz": (1500, 3000), "width_pct": (45, 85),
        "swing_pct": (48, 58), "intro_s": (4, 24), "sections": (4, 8),
        "energy": (0.45, 0.8), "danceability": (0.45, 0.75),
        "quantization": "loose",
        "signature": [
            "textured, slightly lo-fi guitar tones",
            "natural performance dynamics",
            "unconventional song structure",
        ],
        "build": [
            "Leave performance imperfections in rather than editing them out.",
            "Master quieter with more dynamic range than mainstream pop.",
            "Use texture and reverb over polish.",
        ],
    },
    "cinematic": {
        "label": "Cinematic",
        "family": "score",
        "bpm": (60, 110), "bpm_typical": 85, "half_time": False,
        "lufs": -16.0, "lra": (9.0, 20.0),
        "centroid_hz": (900, 2200), "width_pct": (60, 98),
        "swing_pct": (48, 54), "intro_s": (8, 40), "sections": (3, 7),
        "energy": (0.2, 0.8), "danceability": (0.1, 0.45),
        "quantization": "either",
        "signature": [
            "very wide dynamic range with a long build",
            "orchestral or hybrid orchestral textures",
            "no repeating vocal hook",
        ],
        "build": [
            "Do not compress; the dynamic swing is the point.",
            "Build over the full length rather than looping sections.",
            "Widen the strings and brass; keep percussion centred.",
        ],
    },
    "ambient": {
        "label": "Ambient",
        "family": "score",
        "bpm": (50, 90), "bpm_typical": 70, "half_time": False,
        "lufs": -18.0, "lra": (8.0, 20.0),
        "centroid_hz": (600, 1800), "width_pct": (65, 99),
        "swing_pct": (48, 54), "intro_s": (10, 60), "sections": (2, 5),
        "energy": (0.05, 0.35), "danceability": (0.05, 0.35),
        "quantization": "either",
        "signature": [
            "little or no percussion",
            "long evolving pads and drones",
            "very quiet mastering with wide dynamics",
        ],
        "build": [
            "Remove or heavily bury the drums.",
            "Stretch pad movement across 30 seconds or more.",
            "Master very quietly; loudness works against this genre.",
        ],
    },
    "country": {
        "label": "Country",
        "family": "band",
        "bpm": (75, 140), "bpm_typical": 110, "half_time": False,
        "lufs": -10.0, "lra": (5.0, 11.0),
        "centroid_hz": (1600, 3000), "width_pct": (40, 78),
        "swing_pct": (50, 60), "intro_s": (4, 16), "sections": (4, 8),
        "energy": (0.5, 0.82), "danceability": (0.45, 0.75),
        "quantization": "loose",
        "signature": [
            "acoustic guitar as the rhythmic bed",
            "pedal steel or telecaster fills between vocal lines",
            "vocal is central, dry and intelligible",
        ],
        "build": [
            "Put a strummed acoustic at the centre of the arrangement.",
            "Leave gaps between vocal phrases for instrumental fills.",
            "Keep the lead vocal dry and forward.",
        ],
    },
    "hyperpop": {
        "label": "Hyperpop",
        "family": "pop",
        "bpm": (140, 175), "bpm_typical": 160, "half_time": False,
        "lufs": -6.5, "lra": (2.0, 6.0),
        "centroid_hz": (2400, 4400), "width_pct": (60, 98),
        "swing_pct": (48, 54), "intro_s": (0, 8), "sections": (3, 7),
        "energy": (0.8, 0.99), "danceability": (0.6, 0.9),
        "quantization": "tight",
        "signature": [
            "extreme pitch-shifted vocals",
            "deliberate clipping and distortion",
            "very bright, very loud, very short",
        ],
        "build": [
            "Pitch the vocal up hard and add heavy autotune.",
            "Clip the master deliberately; distortion is aesthetic here.",
            "Cut the track under two minutes and start on the hook.",
        ],
    },
}

# Aliases so callers can pass loose names.
ALIASES = {
    "hip hop": "boom_bap", "hiphop": "boom_bap", "rap": "trap",
    "uk drill": "drill", "lofi": "lofi_hiphop", "lo-fi": "lofi_hiphop",
    "edm": "edm_festival", "dance": "house", "electronic": "house",
    "drum and bass": "dnb", "d&b": "dnb", "jungle": "dnb",
    "r&b": "rnb", "rnb": "rnb", "soul": "neo_soul",
    "garage": "uk_garage", "2step": "uk_garage",
    "orchestral": "cinematic", "score": "cinematic", "trailer": "cinematic",
    "afrobeat": "afrobeats", "afro": "afrobeats",
}


def resolve(name: Optional[str]) -> Optional[str]:
    """Map a free-form genre name onto a profile key."""
    if not name:
        return None
    k = name.strip().lower().replace(" ", "_").replace("-", "_")
    if k in GENRES:
        return k
    flat = name.strip().lower()
    if flat in ALIASES:
        return ALIASES[flat]
    if k in ALIASES:
        return ALIASES[k]
    for key in GENRES:
        if k in key or key in k:
            return key
    return None


def profile(name: str) -> Optional[Dict]:
    key = resolve(name)
    return GENRES.get(key) if key else None


def all_keys() -> List[str]:
    return sorted(GENRES.keys())


def labels() -> Dict[str, str]:
    return {k: v["label"] for k, v in GENRES.items()}
