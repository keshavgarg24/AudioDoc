#!/usr/bin/env python
"""Measure both tiers over a folder of audio, and report what each one said.

Run against the repo's own `audio/` folder:

    python scripts/benchmark.py audio/

Verification is forced off. ACRCloud is a network call to a third party whose
latency belongs to them, and mixing it into a timing table makes the numbers
describe the internet rather than this code.

The output is deliberately per-file rather than aggregate: a mean over ten
tracks hides a bimodal distribution, and the distribution is the interesting
part.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, List

# Force a clean, comparable configuration BEFORE labs imports read it.
os.environ.setdefault("LABS_VERIFY_DEFAULT", "never")
os.environ.setdefault("LABS_ACR_ENABLED", "0")
os.environ.setdefault("LABS_MONGO_URI", "")
os.environ.setdefault("LABS_EAGER_LOAD", "false")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def _row(name: str, screen, deep, t1: float, t2: float) -> Dict:
    d = deep or {}
    detection = d.get("detection") or {}
    level1 = detection.get("level_1") or {}
    return {
        "file": name,
        "level_1": {
            "seconds": round(t1, 2),
            "verdict": screen.verdict,
            "probability": screen.probability,
            "confidence": round(screen.confidence, 3),
            "next_step": screen.next_step,
            "models": {k: v.get("probability")
                       for k, v in (screen.models or {}).items()},
            "agreement": (screen.ensemble or {}).get("agreement"),
            "robustness_stable": (screen.robustness or {}).get("stable"),
            "decided_by": (screen.decision or {}).get("decided_by"),
        },
        "level_2": {
            "seconds": round(t2, 2),
            "verdict": d.get("verdict"),
            "prediction": d.get("prediction"),
            "fake_probability": d.get("fake_probability"),
            "raw_logit": d.get("raw_logit"),
            "windows": d.get("segments_used"),
            "coverage": (d.get("segment_plan") or {}).get("coverage"),
            "escalated": (d.get("cascade") or {}).get("escalated"),
            "cascade_note": (d.get("cascade") or {}).get("note"),
        },
        "agreement": (detection.get("level_agreement") or {}).get("state"),
        "level_1_overhead_pct": round(t1 / t2 * 100, 1) if t2 else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--mode", default="full",
                    help="full runs both tiers and never short-circuits; "
                         "ai lets a decisive Level 1 end the request.")
    ap.add_argument("--json", help="write the rows here as well")
    args = ap.parse_args()

    from labs.core.config import get_settings
    from labs.ml.detector import get_detector
    from labs.screen import pipeline as screen_pipeline

    settings = get_settings()

    files = sorted(Path(args.folder).glob("*.mp3")) + \
        sorted(Path(args.folder).glob("*.wav"))
    if not files:
        print(f"No audio in {args.folder}", file=sys.stderr)
        return 1

    print(f"Loading the backbone (1.29 GB)...", flush=True)
    t0 = time.time()
    detector = get_detector(settings)
    detector.load()
    print(f"Loaded in {time.time() - t0:.1f}s\n", flush=True)

    rows: List[Dict] = []
    for path in files:
        print(f"--- {path.name}", flush=True)

        t = time.time()
        screen = screen_pipeline.run(str(path), settings.screen)
        t1 = time.time() - t
        print(f"    L1 {t1:5.2f}s  {screen.verdict:<14} "
              f"p={screen.probability} conf={screen.confidence:.3f} "
              f"-> {screen.next_step}", flush=True)

        t = time.time()
        deep = detector.predict(str(path), mode=args.mode,
                                display_name=path.name)
        t2 = time.time() - t
        print(f"    L2 {t2:5.2f}s  {deep.get('verdict'):<14} "
              f"p={deep.get('fake_probability')} "
              f"logit={deep.get('raw_logit')}", flush=True)

        rows.append(_row(path.name, screen, deep, t1, t2))

    _summarise(rows)
    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=2))
        print(f"\nWrote {args.json}")
    return 0


def _summarise(rows: List[Dict]) -> None:
    def med(xs):
        xs = sorted(x for x in xs if x is not None)
        return xs[len(xs) // 2] if xs else None

    l1 = [r["level_1"]["seconds"] for r in rows]
    l2 = [r["level_2"]["seconds"] for r in rows]

    print("\n" + "=" * 78)
    print(f"{'file':<12} {'L1 s':>6} {'L1 verdict':<14} {'L2 s':>7} "
          f"{'L2 verdict':<14} {'agree':<12}")
    print("-" * 78)
    for r in rows:
        print(f"{r['file']:<12} {r['level_1']['seconds']:>6.2f} "
              f"{str(r['level_1']['verdict']):<14} "
              f"{r['level_2']['seconds']:>7.2f} "
              f"{str(r['level_2']['verdict']):<14} "
              f"{str(r['agreement']):<12}")
    print("-" * 78)
    print(f"L1  mean {sum(l1)/len(l1):.2f}s  median {med(l1):.2f}s  "
          f"max {max(l1):.2f}s")
    print(f"L2  mean {sum(l2)/len(l2):.2f}s  median {med(l2):.2f}s  "
          f"max {max(l2):.2f}s")
    print(f"L1 is {sum(l2)/sum(l1):.0f}x cheaper in wall clock")


if __name__ == "__main__":
    raise SystemExit(main())
