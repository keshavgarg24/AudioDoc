"""The verdict vocabulary, shared by both tiers.

Both levels answer the same question, so they answer it in the same words: a
frontend renders either tier with one code path, and a report that mixes them
does not read as two unrelated systems bolted together.

This module exists SEPARATELY and imports nothing. Level 1 is deliberately
torch-free - that is what lets the free tier ship as a ~300 MB image instead of
~2.5 GB - and if these constants lived in `ml.detector` then importing the
vocabulary would import torch, transformers and pytorch-lightning with it.
Keeping them here is the difference between a screen-only container being
possible and not.
"""

# What the service concluded.
VERDICT_AI = "ai-generated"
VERDICT_HUMAN = "human-made"
VERDICT_INCONCLUSIVE = "inconclusive"
VERDICT_UNAVAILABLE = "unavailable"

VERDICTS = (VERDICT_AI, VERDICT_HUMAN, VERDICT_INCONCLUSIVE, VERDICT_UNAVAILABLE)

# How strong the answer is, independent of whether it cleared the bar for a
# verdict. A queue sorted by `band` puts the tracks worth a human minute at the
# top; a queue sorted by `verdict` cannot, because `inconclusive` covers both
# "almost certainly generated but one detector dissented" and "no idea".
BAND_STRONG_AI = "strong-ai"
BAND_LIKELY_AI = "likely-ai"
BAND_UNCERTAIN = "uncertain"
BAND_LIKELY_HUMAN = "likely-human"
BAND_STRONG_HUMAN = "strong-human"

BANDS = (BAND_STRONG_AI, BAND_LIKELY_AI, BAND_UNCERTAIN,
         BAND_LIKELY_HUMAN, BAND_STRONG_HUMAN)

# What should happen next. Level 1 emits these; the tier orchestrator acts on
# them. `return` is a claim that a deeper pass could not change the answer, so
# it is only ever set for a decisive AI verdict that survived the exit gate.
NEXT_RETURN = "return"
NEXT_ESCALATE = "escalate"
