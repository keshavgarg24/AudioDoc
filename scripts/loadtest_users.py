"""50 users / 50 unused beats: L1 + L2, four polling strategies, stress probes."""
import os,json,os,time,threading,uuid,urllib.request,urllib.error,statistics as st

BASE=os.environ.get("LABS_BASE_URL","http://localhost:8000")
OUT="/tmp/run50"
os.makedirs(OUT,exist_ok=True)
keys=[l.strip() for l in open(os.environ.get('LABS_KEYS_FILE','/tmp/keys50/keys.txt')) if l.strip()]
beats=[l.strip() for l in open(os.environ.get('LABS_BEATS_FILE','/tmp/beats50.txt')) if l.strip()]
N=min(50,len(keys),len(beats))
lock=threading.Lock()
def log(m):
    line=f"[{time.strftime('%H:%M:%S')}] {m}"
    print(line,flush=True)
    with lock: open(f"{OUT}/run.log","a").write(line+"\n")

def _multipart(path,fields,fname):
    b=uuid.uuid4().hex; body=b''
    for k,v in fields.items():
        body+=f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    body+=f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{fname}"\r\nContent-Type: audio/mpeg\r\n\r\n'.encode()
    body+=open(path,'rb').read()+f'\r\n--{b}--\r\n'.encode()
    return b,body

def post(url,key,path,fields,timeout=300):
    b,body=_multipart(path,fields,os.path.basename(path))
    r=urllib.request.Request(url,data=body,headers={'X-API-Key':key,'Content-Type':f'multipart/form-data; boundary={b}'})
    t=time.time()
    try:
        with urllib.request.urlopen(r,timeout=timeout) as resp:
            return resp.status,json.load(resp),round(time.time()-t,3)
    except urllib.error.HTTPError as e:
        try: body=json.loads(e.read())
        except Exception: body={}
        return e.code,body,round(time.time()-t,3)
    except Exception as e:
        return 0,{'err':str(e)[:80]},round(time.time()-t,3)

def get(url,key,timeout=45):
    r=urllib.request.Request(url,headers={'X-API-Key':key})
    t=time.time()
    try:
        with urllib.request.urlopen(r,timeout=timeout) as resp:
            return resp.status,json.load(resp),round(time.time()-t,3)
    except urllib.error.HTTPError as e:
        return e.code,{},round(time.time()-t,3)
    except Exception as e:
        return 0,{'err':str(e)[:60]},round(time.time()-t,3)

# ---- four polling strategies: (name, next_interval_fn) -------------------
def s_fixed3(n,el):   return 3.0
def s_fixed1(n,el):   return 1.0
def s_backoff(n,el):  return min(2.0*(1.6**n),30.0)
def s_adaptive(n,el): return 2.0 if el<60 else (5.0 if el<180 else 10.0)
STRATS=[("fixed-3s",s_fixed3),("aggressive-1s",s_fixed1),
        ("exp-backoff",s_backoff),("adaptive",s_adaptive)]

results={}
def user(uid,key,beat,strat_name,strat_fn,barrier):
    rec={'user':uid,'beat':os.path.basename(beat),'strategy':strat_name,
         'size_mb':round(os.path.getsize(beat)/1e6,2)}
    barrier.wait()
    # ---------- Level 1 (synchronous)
    st1,b1,rt=post(f"{BASE}/v1/screen",key,beat,{})
    rec.update({'l1_http':st1,'l1_round_trip_s':rt,'l1_label':b1.get('label'),
                'l1_verdict':b1.get('verdict'),'l1_band':b1.get('band'),
                'l1_score':b1.get('score'),'l1_server_s':b1.get('elapsed_s'),
                'l1_next_step':b1.get('next_step')})
    # ---------- Level 2 (submit + poll)
    t0=time.time()
    st2,b2,srt=post(f"{BASE}/v1/analyses",key,beat,{'mode':'ai'})
    rec.update({'submit_http':st2,'submit_s':srt,'job_id':b2.get('id'),
                'submit_status':b2.get('status'),'cached':b2.get('cached')})
    if st2 not in (200,202) or not b2.get('id'):
        rec['status']='submit-failed'; rec['err']=b2.get('err') or b2
        with lock: results[uid]=rec
        return
    jid=b2['id']; polls=0; wasted=0; lat=[]
    while time.time()-t0 < 2400:
        el=time.time()-t0
        time.sleep(strat_fn(polls,el)); polls+=1
        sc,sb,prt=get(f"{BASE}/v1/analyses/{jid}",key); lat.append(prt)
        s=sb.get('status')
        if sc==200 and s in ('completed','succeeded'):
            v=sb.get('verdict') or {}
            rec.update({'status':s,'total_s':round(time.time()-t0,1),'polls':polls,
                        'wasted_polls':wasted,'poll_lat_med':round(st.median(lat),3),
                        'l2_label':'ai-generated' if v.get('is_ai') else 'human-made',
                        'l2_score':v.get('fake_probability'),'l2_logit':v.get('raw_logit'),
                        'l2_conf':v.get('confidence'),
                        'l2_server_s':(sb.get('timing') or {}).get('analysis_seconds')})
            break
        if sc==200 and s=='failed':
            rec.update({'status':'failed','total_s':round(time.time()-t0,1),'polls':polls}); break
        if sc==404: rec['saw_404']=rec.get('saw_404',0)+1
        if sc==429: rec['saw_429']=rec.get('saw_429',0)+1
        wasted+=1
    else:
        rec.update({'status':'timeout','total_s':round(time.time()-t0,1),'polls':polls})
    with lock:
        results[uid]=rec
        open(f"{OUT}/partial.jsonl","a").write(json.dumps(rec)+"\n")
    log(f"  {uid} {rec['beat']} done in {rec.get('total_s')}s polls={rec.get('polls')} [{strat_name}]")

log(f"=== 50 users / {N} beats / 4 polling strategies ===")
bar=threading.Barrier(N)
th=[]
for i in range(N):
    sn,sf=STRATS[i%len(STRATS)]
    th.append(threading.Thread(target=user,args=(f"u{i+1}",keys[i],beats[i],sn,sf,bar)))
t0=time.time()
for t in th: t.start()
for t in th: t.join()
wall=round(time.time()-t0,1)
log(f"=== all finished in {wall}s ===")
json.dump({'wall_s':wall,'users':N,'results':[results[k] for k in sorted(results,key=lambda x:int(x[1:]))]},
          open(f"{OUT}/results.json","w"),indent=2)
log("wrote /tmp/run50/results.json")
