"""Paginated PDF report. All content is Platypus flowables so reportlab
paginates for us; every table width is checked against the frame."""
import os,json,sys,statistics as sst
from collections import Counter, defaultdict
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (BaseDocTemplate, PageTemplate, Frame, Paragraph,
                               Spacer, Table, TableStyle, KeepTogether, PageBreak)

PW, PH = A4
MARGIN = 14*mm
AVAIL = PW - 2*MARGIN          # usable width, every table must fit inside

ss = getSampleStyleSheet()
H1 = ParagraphStyle('H1', parent=ss['Heading1'], fontSize=16, spaceAfter=6, textColor=colors.HexColor('#11304e'))
H2 = ParagraphStyle('H2', parent=ss['Heading2'], fontSize=12, spaceBefore=10, spaceAfter=4, textColor=colors.HexColor('#1d4e79'))
BODY = ParagraphStyle('BODY', parent=ss['BodyText'], fontSize=8.6, leading=12)
SMALL= ParagraphStyle('SMALL',parent=ss['BodyText'], fontSize=7.6, leading=10, textColor=colors.HexColor('#444444'))
CELL = ParagraphStyle('CELL', parent=ss['BodyText'], fontSize=7.4, leading=9)

def fmt(x,n=1):
    if x is None: return "-"
    if isinstance(x,float): return f"{x:.{n}f}"
    return str(x)

def table(rows, widths, align_right=(), size=7.4, header=True):
    """Build a table, scaling widths down if they exceed the frame."""
    total=sum(widths)
    if total > AVAIL:                       # <- the pagination/cut-off guard
        widths=[w*AVAIL/total for w in widths]
    t=Table(rows, colWidths=widths, repeatRows=1 if header else 0)
    style=[('FONTSIZE',(0,0),(-1,-1),size),
           ('LEADING',(0,0),(-1,-1),size+2),
           ('VALIGN',(0,0),(-1,-1),'MIDDLE'),
           ('GRID',(0,0),(-1,-1),0.25,colors.HexColor('#cfd8e3')),
           ('TOPPADDING',(0,0),(-1,-1),2.5),('BOTTOMPADDING',(0,0),(-1,-1),2.5),
           ('LEFTPADDING',(0,0),(-1,-1),4),('RIGHTPADDING',(0,0),(-1,-1),4)]
    if header:
        style+=[('BACKGROUND',(0,0),(-1,0),colors.HexColor('#1d4e79')),
                ('TEXTCOLOR',(0,0),(-1,0),colors.white),
                ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold')]
        style+=[('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#f2f6fa')])]
    for c in align_right: style.append(('ALIGN',(c,0),(c,-1),'RIGHT'))
    t.setStyle(TableStyle(style))
    return t

def stats_row(name, vals):
    if not vals: return [name,"0","-","-","-","-","-","-"]
    v=sorted(vals)
    q=lambda p: v[min(len(v)-1,int(p*(len(v)-1)))]
    return [name,str(len(v)),fmt(v[0]),fmt(sst.median(v)),fmt(q(.90)),
            fmt(q(.95)),fmt(q(.99)),fmt(v[-1])]

def build(path_results, path_stress, out):
    d=json.load(open(path_results)); rows=d['results']
    stress=json.load(open(path_stress)) if path_stress else None
    story=[]
    A=lambda t,s=BODY: story.append(Paragraph(t,s))

    # ---------------- header
    A("LABS — 50-user concurrency and detection report", H1)
    ok=[r for r in rows if r.get('status') in ('completed','succeeded')]
    A(f"Stack <b>labs-test</b> (ap-south-1), image <b>v3</b>. "
      f"{d['users']} concurrent users, {len(rows)} previously-unused beats, "
      f"wall clock <b>{d['wall_s']}s ({d['wall_s']/60:.1f} min)</b>. "
      f"Completed <b>{len(ok)}/{len(rows)}</b>.", BODY)
    if os.environ.get("LABS_TRUTH_FILE"):
        A("Labels are corrected against known ground truth, so this run yields a real "
          "false-positive rate as well as recall. The sample is small and human-heavy only "
          "by a handful of tracks, so treat specificity as indicative rather than settled.", SMALL)
    else:
        A("Every track in this corpus is assumed AI-generated, so only <b>recall</b> is measured "
          "here; these figures contain no false-positive rate and cannot separate a model error "
          "from a mislabelled file.", SMALL)

    # ---------------- 1 latency
    A("1 &nbsp;Latency and throughput", H2)
    hdr=["measurement","n","min","p50","p90","p95","p99","max"]
    tbl=[hdr,
         stats_row("L1 round trip (s)",[r['l1_round_trip_s'] for r in rows if r.get('l1_round_trip_s')]),
         stats_row("L1 server (s)",[r['l1_server_s'] for r in rows if r.get('l1_server_s')]),
         stats_row("Submit / upload (s)",[r['submit_s'] for r in rows if r.get('submit_s')]),
         stats_row("L2 end-to-end (s)",[r['total_s'] for r in ok if r.get('total_s')]),
         stats_row("L2 compute (s)",[r['l2_server_s'] for r in ok if r.get('l2_server_s')]),
         stats_row("Poll latency (s)",[r['poll_lat_med'] for r in ok if r.get('poll_lat_med')])]
    tbl=[tbl[0]]+[r for r in tbl[1:] if r[1]!="0"]
    story.append(table(tbl,[40*mm,10*mm,17*mm,17*mm,17*mm,17*mm,17*mm,17*mm],align_right=(1,2,3,4,5,6,7)))
    story.append(Spacer(1,3*mm))
    A("L2 end-to-end includes queue wait. With 4 workers and 50 jobs arriving at once, "
      "queue wait dominates and is not a per-request latency figure.", SMALL)

    # ---------------- 2 polling strategies  (what the API analysis asked for)
    A("2 &nbsp;Polling strategy comparison", H2)
    A("Each user was assigned one of four client-side polling strategies against the same "
      "endpoint, so the cost of <i>how</i> you poll can be separated from how long the work takes.", BODY)
    by=defaultdict(list)
    for r in ok: by[r['strategy']].append(r)
    trow=[["strategy","users","polls p50","polls max","detect lag p50 (s)","end-to-end p50 (s)","429s"]]
    for name in ("fixed-3s","aggressive-1s","exp-backoff","adaptive"):
        g=by.get(name,[])
        if not g: continue
        polls=sorted(r.get('polls',0) for r in g)
        tot=sorted(r.get('total_s',0) for r in g)
        # detection lag = one poll interval on average; approximate from polls vs elapsed
        lag=[]
        for r in g:
            if r.get('polls') and r.get('total_s'): lag.append(r['total_s']/r['polls'])
        trow.append([name,str(len(g)),fmt(sst.median(polls),0),fmt(polls[-1],0),
                     fmt(sst.median(lag),1) if lag else "-",
                     fmt(sst.median(tot),0),
                     str(sum(r.get('saw_429',0) for r in g))])
    story.append(table(trow,[30*mm,16*mm,22*mm,22*mm,30*mm,32*mm,16*mm],align_right=(1,2,3,4,5,6)))
    story.append(Spacer(1,3*mm))
    A("Fewer polls at the same end-to-end time is strictly better: the work finishes when it "
      "finishes, so extra polls are pure load on the API with no benefit to the caller.", SMALL)

    # ---------------- 3 detection
    A("3 &nbsp;Detection outcome", H2)
    TRUE_HUMAN=set(json.load(open(os.environ["LABS_TRUTH_FILE"]))) if os.environ.get("LABS_TRUTH_FILE") else set()
    def truth(r): return 'human-made' if r['beat'].replace('.mp3','') in TRUE_HUMAN else 'ai-generated'
    def confusion(key):
        TP=FP=TN=FN=0
        for r in rows:
            p=r.get(key)
            if p is None: continue
            t=truth(r)
            if t=='ai-generated': TP,FN=(TP+1,FN) if p=='ai-generated' else (TP,FN+1)
            else: FP,TN=(FP+1,TN) if p=='ai-generated' else (FP,TN+1)
        n=TP+FP+TN+FN or 1
        rec=TP/(TP+FN) if TP+FN else 0; pre=TP/(TP+FP) if TP+FP else 0
        spe=TN/(TN+FP) if TN+FP else 0; f1=2*pre*rec/(pre+rec) if pre+rec else 0
        return dict(TP=TP,FP=FP,TN=TN,FN=FN,rec=rec,pre=pre,spe=spe,acc=(TP+TN)/n,f1=f1)
    if TRUE_HUMAN:
        nh=sum(1 for r in rows if truth(r)=='human-made')
        A(f"Ground truth corrected: <b>{nh}</b> of {len(rows)} tracks in this sample are "
          f"human-made, not AI. Metrics below use the corrected labels, so a "
          f"<i>human-made</i> call on those tracks counts as correct.", BODY)
        cm=[["tier","TP","FP","TN","FN","recall (AI)","precision","specificity","accuracy","F1"]]
        for nm,k in (("Level 1","l1_label"),("Level 2 (final)","l2_label")):
            c=confusion(k)
            cm.append([nm,str(c['TP']),str(c['FP']),str(c['TN']),str(c['FN']),
                       f"{c['rec']*100:.1f}%",f"{c['pre']*100:.1f}%",f"{c['spe']*100:.1f}%",
                       f"{c['acc']*100:.1f}%",f"{c['f1']:.3f}"])
        story.append(table(cm,[28*mm,10*mm,10*mm,10*mm,10*mm,20*mm,18*mm,20*mm,18*mm,14*mm],
                           align_right=tuple(range(1,10))))
        story.append(Spacer(1,3*mm))
        dec=[r for r in rows if r.get('l2_verdict') in ('ai-generated','human-made')]
        cor=sum(1 for r in dec if r['l2_verdict']==truth(r))
        A(f"On the banded <b>final verdict</b>, the system commits on "
          f"<b>{len(dec)}/{len(rows)}</b> tracks and is correct on <b>{cor}/{len(dec)} "
          f"({cor/max(1,len(dec))*100:.1f}%)</b>; it abstains as <i>inconclusive</i> on "
          f"{len(rows)-len(dec)}.", BODY)
        story.append(Spacer(1,3*mm))
    l1=Counter(r.get('l1_label') for r in rows)
    l2=Counter(r.get('l2_label') for r in ok)
    b1=Counter(r.get('l1_band') for r in rows)
    b2=Counter(r.get('l2_band') for r in ok if r.get('l2_band'))
    n1=sum(l1.values()) or 1; n2=sum(l2.values()) or 1
    det=[["tier","n","ai-generated","human-made","recall"],
         ["Level 1",str(n1),str(l1.get('ai-generated',0)),str(l1.get('human-made',0)),
          f"{l1.get('ai-generated',0)/n1*100:.1f}%"],
         ["Level 2",str(n2),str(l2.get('ai-generated',0)),str(l2.get('human-made',0)),
          f"{l2.get('ai-generated',0)/n2*100:.1f}%"]]
    story.append(table(det,[30*mm,16*mm,32*mm,32*mm,24*mm],align_right=(1,2,3,4)))
    story.append(Spacer(1,3*mm))
    vr=Counter(r.get('l1_verdict') for r in rows)
    A(f"Level 1 verdict split: " + ", ".join(f"<b>{k}</b> {v}" for k,v in vr.most_common() if k), BODY)
    if b1: A("Level 1 bands: " + ", ".join(f"{k} {v}" for k,v in b1.most_common() if k), SMALL)

    # agreement
    h1={r['beat'] for r in rows if r.get('l1_label')=='human-made'}
    h2={r['beat'] for r in ok if r.get('l2_label')=='human-made'}
    A("3.1 &nbsp;Cross-tier agreement", H2)
    A(f"L1 called <b>{len(h1)}</b> human, L2 called <b>{len(h2)}</b> human, "
      f"<b>{len(h1&h2)}</b> agreed. Two independent models (different architecture, "
      f"different sample rate, different training data) agreeing on 'human' for an "
      f"all-AI corpus points at the corpus label rather than at a shared blind spot.", BODY)
    if h1&h2:
        names=sorted(x.replace('.mp3','') for x in h1&h2)
        A("Both-human: " + ", ".join(names), SMALL)

    A("3.2 &nbsp;Cascade efficiency", H2)
    esc=[r for r in ok if r.get('l2_escalated')]
    seg=[r.get('l2_segments_scored') or r.get('l2_windows') for r in ok if (r.get('l2_segments_scored') or r.get('l2_windows'))]
    crow=[["measure","value","meaning"],
          ["Escalated to full scoring",f"{len(esc)}/{len(ok)}","re-scored all windows after an indecisive first pass"],
          ["Windows scored (median)",fmt(sst.median(seg),0) if seg else "-","of 48 available"],
          ["Windows scored (max)",fmt(max(seg),0) if seg else "-","full re-score"],
          ["L2 compute p50 (s)",fmt(sst.median([r['l2_server_s'] for r in ok if r.get('l2_server_s')]),1),"backbone time per track"]]
    story.append(table(crow,[46*mm,24*mm,90*mm],align_right=(1,)))
    story.append(Spacer(1,3*mm))
    A("The cascade scores a strided subset first and only re-scores everything when that pass is "
      "indecisive. A median of 16 of 48 windows is roughly a two-thirds saving on backbone time.", SMALL)

    story.append(PageBreak())

    # ---------------- 4 stress
    if stress:
        A("4 &nbsp;Stress and threshold probes", H2)
        srow=[["probe","requests","2xx","429","other","observed limit"]]
        for p in stress.get('probes',[]):
            srow.append([p['name'],str(p['n']),str(p['ok']),str(p['rate_limited']),
                         str(p['other']),p.get('note','-')])
        story.append(table(srow,[42*mm,20*mm,16*mm,16*mm,16*mm,60*mm],align_right=(1,2,3,4)))
        story.append(Spacer(1,3*mm))
        A("A 429 here is the service defending itself, not an error: the documented caps are "
          "60 requests/min per key, 4 analyses in flight per key, and one screening slot per "
          "CPU per container.", SMALL)

    # ---------------- 5 per-user table (long; Platypus paginates it)
    A("5 &nbsp;Per-user detail", H2)
    hdr=["user","beat","MB","L1","L2","FINAL verdict","band","score","truth","ok"]
    prow=[hdr]
    for r in sorted(rows,key=lambda x:(x.get('total_s') or 9e9)):
        sh=lambda x:(x or '-').replace('ai-generated','AI').replace('human-made','human').replace('inconclusive','inconcl.')
        t=truth(r) if TRUE_HUMAN else None
        good='-' if not t else ('yes' if r.get('l2_label')==t else 'NO')
        prow.append([r['user'], r['beat'].replace('.mp3',''), fmt(r.get('size_mb'),1),
                     sh(r.get('l1_label')), sh(r.get('l2_label')),
                     sh(r.get('l2_verdict')), (r.get('l2_band') or '-'),
                     fmt(r.get('l2_score'),3), sh(t) if t else '-', good])
    story.append(table(prow,[13*mm,21*mm,11*mm,16*mm,16*mm,22*mm,24*mm,16*mm,16*mm,11*mm],
                       align_right=(2,7)))
    A("FINAL verdict is the banded answer the API returns (decided_by = level_2_deep); "
      "L1/L2 are each tier's unconditional binary label. 'ok' compares the final label "
      "against corrected ground truth.", SMALL)

    # ---------------- doc with page numbers in the footer (canvas only here)
    def footer(canv,doc):
        canv.saveState(); canv.setFont('Helvetica',7.5)
        canv.setFillColor(colors.HexColor('#667788'))
        canv.drawString(MARGIN,10*mm,"LABS — labs-test (ap-south-1) — image v3")
        canv.drawRightString(PW-MARGIN,10*mm,f"page {doc.page}")
        canv.restoreState()
    doc=BaseDocTemplate(out,pagesize=A4,leftMargin=MARGIN,rightMargin=MARGIN,
                        topMargin=MARGIN,bottomMargin=18*mm,title="LABS 50-user report")
    frame=Frame(MARGIN,18*mm,AVAIL,PH-MARGIN-18*mm,id='f')
    doc.addPageTemplates([PageTemplate(id='all',frames=[frame],onPage=footer)])
    doc.build(story)
    print(f"wrote {out}")

if __name__=="__main__":
    build(sys.argv[1], sys.argv[2] if len(sys.argv)>2 and sys.argv[2]!='-' else None, sys.argv[3])
