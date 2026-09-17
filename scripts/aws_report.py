#!/usr/bin/env python3
"""Render a benchmark run into a PDF.

Reads the results.json written by aws_benchmark.py and produces a report with
the per-track detail intact. The detail is the point: an aggregate over 100
tracks can hide a bimodal latency distribution, a handful of cold starts, or
the one track that took the slow path through the cascade, and those are the
things worth knowing about a deployment.

Nothing here computes a verdict or re-derives a score. It reports what the
service said, and where it says "correct" it means "agreed with the corpus
label", which for an all-AI corpus is recall and nothing more.
"""
from __future__ import annotations

import argparse
import io
import json
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.enums import TA_LEFT  # noqa: E402
from reportlab.lib.pagesizes import A4, landscape  # noqa: E402
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.lib.utils import ImageReader  # noqa: E402
from reportlab.platypus import (  # noqa: E402
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

INK = colors.HexColor("#14161a")
MUTED = colors.HexColor("#6b7280")
RULE = colors.HexColor("#d7dae0")
BAND_FILL = colors.HexColor("#f4f5f7")
AI = colors.HexColor("#b4453a")
HUMAN = colors.HexColor("#2f6f4f")
UNSURE = colors.HexColor("#b08324")

BAND_COLOR = {
    "strong-ai": "#b4453a", "likely-ai": "#d08c66", "uncertain": "#b08324",
    "likely-human": "#6d9f7e", "strong-human": "#2f6f4f",
}


# ------------------------------------------------------------------ helpers --
def pct(n: int, total: int) -> str:
    return f"{(100.0 * n / total):.0f}%" if total else "-"


def stat_block(values: List[float]) -> Dict[str, Optional[float]]:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return {k: None for k in ("n", "min", "p50", "p90", "p99", "max", "mean")}

    def q(p: float) -> float:
        if len(vals) == 1:
            return vals[0]
        idx = min(len(vals) - 1, max(0, int(round(p * (len(vals) - 1)))))
        return vals[idx]

    return {"n": len(vals), "min": vals[0], "p50": q(0.50), "p90": q(0.90),
            "p99": q(0.99), "max": vals[-1], "mean": statistics.fmean(vals)}


def fmt(v: Any, nd: int = 2) -> str:
    if v is None:
        return "-"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def chart(fig, width_mm: float = 165.0) -> Image:
    """Render a figure to a flowable at a fixed width.

    The height is computed from the saved PNG rather than from the figure size:
    bbox_inches="tight" crops whatever the axes did not use, so the figure's
    declared aspect ratio is not the one that ends up on the page.
    """
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=170, bbox_inches="tight",
                facecolor="white")
    plt.close(fig)
    buf.seek(0)
    reader = ImageReader(buf)
    px_w, px_h = reader.getSize()
    buf.seek(0)
    return Image(buf, width=width_mm * mm,
                 height=width_mm * mm * (px_h / px_w))


def style_axes(ax) -> None:
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color("#b8bcc4")
    ax.tick_params(colors="#4b5563", labelsize=8)
    ax.grid(axis="y", color="#e6e8ec", linewidth=0.7)
    ax.set_axisbelow(True)


# -------------------------------------------------------------- the report --
class Report:
    def __init__(self, data: Dict, out: Path):
        self.meta = data["meta"]
        self.rows = data["rows"]
        self.out = out
        self.flow: List[Any] = []
        ss = getSampleStyleSheet()
        self.s = {
            "title": ParagraphStyle("t", parent=ss["Title"], fontName="Helvetica-Bold",
                                    fontSize=25, leading=29, textColor=INK,
                                    spaceAfter=2),
            "sub": ParagraphStyle("sub", parent=ss["Normal"], fontSize=11,
                                  leading=15, textColor=MUTED, spaceAfter=14),
            "h1": ParagraphStyle("h1", parent=ss["Heading1"], fontName="Helvetica-Bold",
                                 fontSize=15, leading=19, textColor=INK,
                                 spaceBefore=16, spaceAfter=7),
            "h2": ParagraphStyle("h2", parent=ss["Heading2"], fontName="Helvetica-Bold",
                                 fontSize=11.5, leading=15, textColor=INK,
                                 spaceBefore=11, spaceAfter=4),
            "p": ParagraphStyle("p", parent=ss["Normal"], fontSize=9.6,
                                leading=14.2, textColor=INK, alignment=TA_LEFT,
                                spaceAfter=6),
            "note": ParagraphStyle("n", parent=ss["Normal"], fontSize=8.6,
                                   leading=12.4, textColor=MUTED, spaceAfter=6),
            "cell": ParagraphStyle("c", parent=ss["Normal"], fontSize=7.2,
                                   leading=9.2, textColor=INK),
        }

    # -- building blocks ---------------------------------------------------
    def h1(self, t): self.flow.append(Paragraph(t, self.s["h1"]))
    def h2(self, t): self.flow.append(Paragraph(t, self.s["h2"]))
    def p(self, t): self.flow.append(Paragraph(t, self.s["p"]))
    def note(self, t): self.flow.append(Paragraph(t, self.s["note"]))
    def gap(self, h=5): self.flow.append(Spacer(1, h * mm))

    def table(self, rows, widths, align_right=(), header=True, size=8.2):
        t = Table(rows, colWidths=widths, repeatRows=1 if header else 0,
                  hAlign="LEFT")
        cmds = [
            ("FONT", (0, 0), (-1, -1), "Helvetica", size),
            ("TEXTCOLOR", (0, 0), (-1, -1), INK),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 3.4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3.4),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("LINEBELOW", (0, 0), (-1, -2), 0.4, RULE),
        ]
        if header:
            cmds += [
                ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", size),
                ("BACKGROUND", (0, 0), (-1, 0), BAND_FILL),
                ("LINEBELOW", (0, 0), (-1, 0), 0.9, INK),
            ]
        for c in align_right:
            cmds.append(("ALIGN", (c, 0), (c, -1), "RIGHT"))
        t.setStyle(TableStyle(cmds))
        self.flow.append(t)
        return t

    def kv(self, pairs, widths=(58 * mm, 105 * mm)):
        self.table([[Paragraph(f"<b>{k}</b>", self.s["cell"]),
                     Paragraph(str(v), self.s["cell"])] for k, v in pairs],
                   list(widths), header=False, size=8.6)

    def dist(self, title, counter: Counter, total: int, color_map=None):
        self.h2(title)
        rows = [["value", "tracks", "share", ""]]
        for k, n in counter.most_common():
            bar = "█" * max(1, round(28 * n / total)) if total else ""
            rows.append([str(k), str(n), pct(n, total), bar])
        t = self.table(rows, [52 * mm, 20 * mm, 20 * mm, 62 * mm],
                       align_right=(1, 2))
        if color_map:
            for i, (k, _) in enumerate(counter.most_common(), start=1):
                if k in color_map:
                    t.setStyle(TableStyle([("TEXTCOLOR", (3, i), (3, i),
                                            colors.HexColor(color_map[k]))]))

    def stats_table(self, title, groups: Dict[str, List[float]], unit="s"):
        self.h2(title)
        rows = [["measurement", "n", "min", "p50", "p90", "p99", "max", "mean"]]
        for name, vals in groups.items():
            s = stat_block(vals)
            rows.append([name, fmt(s["n"], 0),
                         *[f"{fmt(s[k])}{unit}" if s[k] is not None else "-"
                           for k in ("min", "p50", "p90", "p99", "max", "mean")]])
        self.table(rows, [46 * mm, 12 * mm, 17 * mm, 17 * mm, 17 * mm, 17 * mm,
                          17 * mm, 17 * mm],
                   align_right=(1, 2, 3, 4, 5, 6, 7))

    # -- sections ----------------------------------------------------------
    def cover(self):
        m = self.meta
        self.flow.append(Paragraph("LABS &mdash; AI Music Detection", self.s["title"]))
        self.flow.append(Paragraph(
            f"Deployed-stack benchmark over {m['sampled']} tracks &middot; "
            f"{m.get('started_iso', '')}", self.s["sub"]))
        self.kv([
            ("Endpoint", m["base_url"]),
            ("Request mode", f"{m['mode']} &mdash; Level 1, then Level 2 when "
                             f"Level 1 does not settle it"),
            ("Corpus", f"{m['dataset']}"),
            ("Corpus size", f"{m['corpus_size']} files"),
            ("Sampled", f"{m['sampled']} at random, seed {m['seed']} (re-runnable)"),
            ("Corpus label", "every track is AI-generated"),
            ("Started", m.get("started_iso", "-")),
            ("Finished", m.get("finished_iso", "-")),
            ("Total wall clock", f"{m.get('total_s', 0) / 60:.1f} min"),
        ])
        self.gap(4)
        self.note(
            "Every track in this corpus is AI-generated, so there are no true "
            "negatives in it. A percentage here is recall: the share of known-AI "
            "tracks the service placed on the AI side. It says nothing about the "
            "false-positive rate, which needs a human-made corpus to measure and "
            "is the error this system is tuned to avoid. Read the two numbers "
            "together or not at all.")

    def summary(self):
        rows, n = self.rows, len(self.rows)
        done = [r for r in rows if r.get("l2_final_status") in
                ("completed", "succeeded")]
        l1_ok = [r for r in rows if r.get("l1_http_status") == 200]

        l1_ai = sum(1 for r in l1_ok if r.get("l1_label") == "ai-generated")
        l2_ai = sum(1 for r in done if r.get("l2_label") == "ai-generated")
        l1_verdict_ai = sum(1 for r in l1_ok if r.get("l1_verdict") == "ai-generated")
        l2_verdict_ai = sum(1 for r in done if r.get("l2_verdict") == "ai-generated")
        early = sum(1 for r in done if r.get("l2_early_exit"))

        self.h1("Headline")
        self.p(
            f"<b>{len(done)} of {n}</b> analyses completed end to end. "
            f"Level 1 answered for <b>{len(l1_ok)}</b> of {n} on the synchronous "
            f"route.")
        self.table([
            ["", "binary label (score &gt; 0.5)", "published verdict (banded)"],
            ["Level 1", f"{l1_ai}/{len(l1_ok)}  ({pct(l1_ai, len(l1_ok))})",
             f"{l1_verdict_ai}/{len(l1_ok)}  ({pct(l1_verdict_ai, len(l1_ok))})"],
            ["Level 2", f"{l2_ai}/{len(done)}  ({pct(l2_ai, len(done))})",
             f"{l2_verdict_ai}/{len(done)}  ({pct(l2_verdict_ai, len(done))})"],
        ], [30 * mm, 66 * mm, 66 * mm])
        self.gap(3)
        self.note(
            "The two columns are the same score read two ways. <b>label</b> is the "
            "side of 0.5 and always commits. <b>verdict</b> applies an uncertainty "
            "band and may return <i>inconclusive</i>, which is why its count is "
            "lower. Neither is more correct than the other; they encode different "
            "tolerances for being wrong.")
        self.gap(3)
        self.kv([
            ("Early exits at Level 1",
             f"{early} of {len(done)} ({pct(early, len(done))}) &mdash; "
             f"backbone never loaded"),
            ("Level-2 analyses that ran",
             f"{len(done) - early} ({pct(len(done) - early, len(done))})"),
        ])

    def level1(self):
        ok = [r for r in self.rows if r.get("l1_http_status") == 200]
        if not ok:
            return
        self.flow.append(PageBreak())
        self.h1("Level 1 &mdash; the free screen")
        self.p(
            "Two ONNX models over one decode: a spectral-comb detector "
            "(<i>fakeprint</i>) and a CQT-texture CNN (<i>cepstrum</i>). They are "
            "fused by agreement rather than by max(), then banded by a policy "
            "whose AI and human bars are deliberately asymmetric.")
        n = len(ok)
        self.dist("Published verdict",
                  Counter(r.get("l1_verdict") for r in ok), n)
        self.dist("Binary label at the 0.5 boundary",
                  Counter(r.get("l1_label") for r in ok), n)
        self.dist("Strength band", Counter(r.get("l1_band") for r in ok), n,
                  BAND_COLOR)
        self.dist("Detector agreement",
                  Counter(_dig(r, "l1_ensemble", "agreement") for r in ok), n)
        self.dist("Routing decision", Counter(r.get("l1_next_step") for r in ok), n)

        scores = [r["l1_score"] for r in ok if r.get("l1_score") is not None]
        if scores:
            self.gap(3)
            self.flow.append(self._score_hist(
                scores, "Level-1 fused score", 0.80, 0.20))

        vetoes = Counter()
        for r in ok:
            for v in (r.get("l1_vetoes") or []):
                vetoes[v.split(" - ")[0][:60]] += 1
        if vetoes:
            self.dist("Vetoes raised", vetoes, n)

    def level2(self):
        done = [r for r in self.rows if r.get("l2_final_status") in
                ("completed", "succeeded")]
        ran = [r for r in done if not r.get("l2_early_exit")]
        if not done:
            return
        self.flow.append(PageBreak())
        self.h1("Level 2 &mdash; the deep tier")
        self.p(
            "A 170M-parameter MERT backbone over beat-aligned 10 s windows, then "
            "a two-stage classifier. Its output is an unbounded logit; the score "
            "below is that logit through a sigmoid, and the verdict is the same "
            "logit after an uncertainty band at |logit| &lt; 2.")
        self.dist("Published verdict", Counter(r.get("l2_verdict") for r in done),
                  len(done))
        self.dist("Binary label at the 0.5 boundary",
                  Counter(r.get("l2_label") for r in done), len(done))
        self.dist("Strength band", Counter(r.get("l2_band") for r in done),
                  len(done), BAND_COLOR)

        if ran:
            self.dist("Cascade behaviour",
                      Counter("escalated to full density"
                              if _dig(r, "l2_cascade", "escalated")
                              else "early exit at first pass" for r in ran),
                      len(ran))
            logits = [r["l2_raw_logit"] for r in ran
                      if r.get("l2_raw_logit") is not None]
            if logits:
                self.gap(3)
                self.flow.append(self._logit_hist(logits))

        agree = Counter(r.get("l2_level_agreement") for r in done
                        if r.get("l2_level_agreement"))
        if agree:
            self.dist("Level 1 vs Level 2 (banded verdicts)", agree,
                      sum(agree.values()))
        lab = Counter("same side of 0.5" if r.get("l2_label_agreement") is True
                      else "opposite sides" if r.get("l2_label_agreement") is False
                      else "not comparable" for r in done)
        self.dist("Level 1 vs Level 2 (binary labels)", lab, len(done))

    def timing(self):
        rows = self.rows
        self.flow.append(PageBreak())
        self.h1("Timing")
        self.p(
            "Client-side round trip and server-reported elapsed are both shown. "
            "The gap between them is network plus load-balancer time, which on a "
            "cross-region client is most of the Level-1 latency and none of the "
            "Level-2 latency.")
        self.stats_table("Level 1, synchronous route", {
            "HTTP round trip": [r.get("l1_round_trip_s") for r in rows],
            "server elapsed": [r.get("l1_server_elapsed_s") for r in rows],
        })
        self.stats_table("Level 2, asynchronous route", {
            "submit round trip": [r.get("submit_round_trip_s") for r in rows],
            "end to end (submit to result)": [r.get("end_to_end_s") for r in rows],
            "server elapsed": [r.get("l2_server_elapsed_s") for r in rows],
        })

        stages = {}
        for r in rows:
            for k, v in (r.get("l1_stage_timings") or {}).items():
                stages.setdefault(k, []).append(v)
        if stages:
            ordered = dict(sorted(stages.items(),
                                  key=lambda kv: -statistics.fmean(kv[1])))
            self.stats_table("Level-1 stages, server-side", ordered)

        e2e = [r["end_to_end_s"] for r in rows if r.get("end_to_end_s")]
        if e2e:
            self.gap(3)
            self.flow.append(self._latency_hist(e2e))
        self.gap(3)
        tl = self._timeline()
        if tl:
            self.flow.append(tl)

    def infra(self):
        m, rows = self.meta, self.rows
        self.flow.append(PageBreak())
        self.h1("AWS behaviour")
        done = [r for r in rows if r.get("end_to_end_s")]
        drain = None
        if done:
            starts = [r["submitted_at"] for r in rows if r.get("submitted_at")]
            ends = [r["l2_completed_at"] for r in rows if r.get("l2_completed_at")]
            if starts and ends:
                drain = max(ends) - min(starts)
        self.kv([
            ("Level-1 phase", f"{m.get('screen_phase_s', 0):.0f}s "
                              f"(paced client-side under the 30/min route limit)"),
            ("Queue fill", f"{m.get('submit_phase_s', 0):.0f}s to accept "
                           f"{len(rows)} uploads"),
            ("Queue drain", f"{drain:.0f}s" if drain else "-"),
            ("Effective throughput",
             f"{len(done) / (drain / 60):.1f} analyses/min" if drain else "-"),
        ])
        self.gap(3)
        self.p(
            "The queue was filled as fast as the API would accept uploads and "
            "then left to drain. That is deliberate: a paced submission would "
            "measure the pacing, not the fleet. What the drain time shows is the "
            "worker fleet's real throughput including cold starts.")
        errs = Counter()
        for r in rows:
            if r.get("l1_http_status") not in (200, None):
                errs[f"Level 1 HTTP {r['l1_http_status']}"] += 1
            if r.get("submit_http_status") not in (200, 201, 202, None):
                errs[f"submit HTTP {r['submit_http_status']}"] += 1
            if r.get("l2_final_status") not in ("completed", "succeeded", None):
                errs[f"job {r.get('l2_final_status')}"] += 1
        self.h2("Failures")
        if errs:
            self.table([["failure", "count"]] +
                       [[k, str(v)] for k, v in errs.most_common()],
                       [110 * mm, 25 * mm], align_right=(1,))
        else:
            self.p("None. Every request returned and every job reached a "
                   "terminal state.")

    def appendix(self):
        self.flow.append(PageBreak())
        self.h1("Per-track detail")
        self.note(
            "One row per track. <b>L1</b> and <b>L2</b> columns give the binary "
            "label and, in brackets, the published verdict when it differs. "
            "<b>e2e</b> is submit-to-result wall clock.")
        self.gap(2)
        head = ["#", "file", "L1 score", "L1 label / verdict", "L1 band",
                "L2 score", "L2 logit", "L2 label / verdict", "L2 band",
                "exit", "L1 s", "e2e s"]
        body = [[Paragraph(f"<b>{h}</b>", self.s["cell"]) for h in head]]
        for i, r in enumerate(sorted(self.rows, key=lambda x: x["file"]), 1):
            body.append([
                Paragraph(str(i), self.s["cell"]),
                Paragraph(r["file"].replace(".mp3", ""), self.s["cell"]),
                Paragraph(fmt(r.get("l1_score"), 3), self.s["cell"]),
                Paragraph(_pair(r.get("l1_label"), r.get("l1_verdict")),
                          self.s["cell"]),
                Paragraph(str(r.get("l1_band") or "-"), self.s["cell"]),
                Paragraph(fmt(r.get("l2_score"), 3), self.s["cell"]),
                Paragraph(fmt(r.get("l2_raw_logit"), 2), self.s["cell"]),
                Paragraph(_pair(r.get("l2_label"), r.get("l2_verdict")),
                          self.s["cell"]),
                Paragraph(str(r.get("l2_band") or "-"), self.s["cell"]),
                Paragraph("L1" if r.get("l2_early_exit") else "L2", self.s["cell"]),
                Paragraph(fmt(r.get("l1_round_trip_s"), 1), self.s["cell"]),
                Paragraph(fmt(r.get("end_to_end_s"), 0), self.s["cell"]),
            ])
        t = Table(body, colWidths=[8 * mm, 25 * mm, 17 * mm, 34 * mm, 24 * mm,
                                   17 * mm, 16 * mm, 34 * mm, 24 * mm, 11 * mm,
                                   13 * mm, 14 * mm],
                  repeatRows=1, hAlign="LEFT")
        cmds = [("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("BACKGROUND", (0, 0), (-1, 0), BAND_FILL),
                ("LINEBELOW", (0, 0), (-1, 0), 0.8, INK),
                ("LINEBELOW", (0, 1), (-1, -2), 0.3, RULE)]
        for i in range(2, len(body), 2):
            cmds.append(("BACKGROUND", (0, i), (-1, i),
                         colors.HexColor("#fafbfc")))
        t.setStyle(TableStyle(cmds))
        self.flow.append(t)

    # -- charts ------------------------------------------------------------
    def _score_hist(self, scores, title, ai_line, human_line):
        fig, ax = plt.subplots(figsize=(7.2, 2.5))
        ax.hist(scores, bins=[i / 20 for i in range(21)],
                color="#5a6b8c", edgecolor="white", linewidth=0.8)
        ax.axvline(0.5, color=INK.hexval().replace("0x", "#")[:7],
                   linewidth=1.4, linestyle="--", label="label boundary 0.5")
        ax.axvline(ai_line, color="#b4453a", linewidth=1.1,
                   label=f"AI verdict bar {ai_line}")
        ax.axvline(human_line, color="#2f6f4f", linewidth=1.1,
                   label=f"human verdict bar {human_line}")
        ax.set_xlabel(title, fontsize=9)
        ax.set_ylabel("tracks", fontsize=9)
        ax.legend(fontsize=7, frameon=False)
        style_axes(ax)
        return chart(fig)

    def _logit_hist(self, logits):
        fig, ax = plt.subplots(figsize=(7.2, 2.5))
        lo, hi = min(logits + [-8]), max(logits + [8])
        ax.hist(logits, bins=28, range=(lo, hi), color="#5a6b8c",
                edgecolor="white", linewidth=0.8)
        ax.axvspan(-2, 2, color="#b08324", alpha=0.16,
                   label="inconclusive band |logit| < 2")
        ax.axvline(0, color="#14161a", linewidth=1.3, linestyle="--",
                   label="label boundary")
        ax.set_xlabel("Level-2 raw logit  (negative = human, positive = AI)",
                      fontsize=9)
        ax.set_ylabel("tracks", fontsize=9)
        ax.legend(fontsize=7, frameon=False)
        style_axes(ax)
        return chart(fig)

    def _latency_hist(self, e2e):
        fig, ax = plt.subplots(figsize=(7.2, 2.5))
        ax.hist(e2e, bins=26, color="#4f7a68", edgecolor="white", linewidth=0.8)
        med = statistics.median(e2e)
        ax.axvline(med, color="#b4453a", linewidth=1.3,
                   label=f"median {med:.0f}s")
        ax.set_xlabel("end-to-end seconds (submit to result, includes queue wait)",
                      fontsize=9)
        ax.set_ylabel("tracks", fontsize=9)
        ax.legend(fontsize=7, frameon=False)
        style_axes(ax)
        return chart(fig)

    def _timeline(self):
        pts = [(r["l2_completed_at"], r) for r in self.rows
               if r.get("l2_completed_at")]
        if len(pts) < 5:
            return None
        pts.sort()
        t0 = min(r["submitted_at"] for r in self.rows if r.get("submitted_at"))
        xs = [(t - t0) / 60 for t, _ in pts]
        ys = list(range(1, len(pts) + 1))
        fig, ax = plt.subplots(figsize=(7.2, 2.4))
        ax.step(xs, ys, where="post", color="#5a6b8c", linewidth=1.6)
        ax.set_xlabel("minutes since the first upload was accepted", fontsize=9)
        ax.set_ylabel("analyses complete", fontsize=9)
        ax.set_title("Queue drain", fontsize=9.5, loc="left", color="#14161a")
        style_axes(ax)
        return chart(fig)

    # -- output ------------------------------------------------------------
    def build(self):
        self.cover()
        self.summary()
        self.level1()
        self.level2()
        self.timing()
        self.infra()
        self.appendix()
        doc = SimpleDocTemplate(
            str(self.out), pagesize=A4, title="LABS deployed-stack benchmark",
            author="LABS", leftMargin=20 * mm, rightMargin=20 * mm,
            topMargin=18 * mm, bottomMargin=18 * mm)
        doc.build(self.flow, onLaterPages=_footer, onFirstPage=_footer)


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(20 * mm, 11 * mm, "LABS - deployed-stack benchmark")
    canvas.drawRightString(A4[0] - 20 * mm, 11 * mm, f"{doc.page}")
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.4)
    canvas.line(20 * mm, 14.5 * mm, A4[0] - 20 * mm, 14.5 * mm)
    canvas.restoreState()


def _pair(label, verdict):
    if not label:
        return "-"
    if verdict and verdict != label:
        return f"{label}<br/><font color='#6b7280'>({verdict})</font>"
    return str(label)


def _dig(d, *path, default=None):
    for k in path:
        if not isinstance(d, dict):
            return default
        d = d.get(k)
        if d is None:
            return default
    return d


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--results", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    data = json.loads(Path(a.results).read_text())
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    Report(data, out).build()
    print(f"wrote {out}  ({out.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
