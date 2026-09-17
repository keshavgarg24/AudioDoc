"""Threshold probes: find where the API defends itself, and confirm the
documented caps are the ones actually enforced."""
import os,json,os,time,threading,uuid,urllib.request,urllib.error
BASE=os.environ.get("LABS_BASE_URL","http://localhost:8000")
keys=[l.strip() for l in open(os.environ.get('LABS_KEYS_FILE','/tmp/keys50/keys.txt')) if l.strip()]
beats=[l.strip() for l in open(os.environ.get('LABS_BEATS_FILE','/tmp/beats50.txt')) if l.strip()]
probes=[]; lock=threading.Lock()

def get(url,key,timeout=30):
    r=urllib.request.Request(url,headers={'X-API-Key':key})
    try:
        with urllib.request.urlopen(r,timeout=timeout) as resp: return resp.status
    except urllib.error.HTTPError as e: return e.code
    except Exception: return 0

def post_screen(key,path,timeout=180):
    b=uuid.uuid4().hex
    body=f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="s.mp3"\r\nContent-Type: audio/mpeg\r\n\r\n'.encode()
    body+=open(path,'rb').read()+f'\r\n--{b}--\r\n'.encode()
    r=urllib.request.Request(BASE+'/v1/screen',data=body,headers={'X-API-Key':key,'Content-Type':f'multipart/form-data; boundary={b}'})
    try:
        with urllib.request.urlopen(r,timeout=timeout) as resp: return resp.status
    except urllib.error.HTTPError as e: return e.code
    except Exception: return 0

def post_analysis(key,path,timeout=180):
    b=uuid.uuid4().hex
    body=f'--{b}\r\nContent-Disposition: form-data; name="mode"\r\n\r\nai\r\n'.encode()
    body+=f'--{b}\r\nContent-Disposition: form-data; name="no_cache"\r\n\r\ntrue\r\n'.encode()
    body+=f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="a.mp3"\r\nContent-Type: audio/mpeg\r\n\r\n'.encode()
    body+=open(path,'rb').read()+f'\r\n--{b}--\r\n'.encode()
    r=urllib.request.Request(BASE+'/v1/analyses',data=body,headers={'X-API-Key':key,'Content-Type':f'multipart/form-data; boundary={b}'})
    try:
        with urllib.request.urlopen(r,timeout=timeout) as resp: return resp.status
    except urllib.error.HTTPError as e: return e.code
    except Exception: return 0

def record(name,codes,note):
    ok=sum(1 for c in codes if 200<=c<300)
    rl=sum(1 for c in codes if c==429)
    other=len(codes)-ok-rl
    probes.append({'name':name,'n':len(codes),'ok':ok,'rate_limited':rl,'other':other,'note':note})
    print(f"  {name}: n={len(codes)} 2xx={ok} 429={rl} other={other}  |  {note}",flush=True)

# 1) per-key rate limit on a cheap read endpoint
print("probe 1: per-key rate limit (documented 60/min)",flush=True)
codes=[];k=keys[0]
for i in range(90): codes.append(get(f"{BASE}/v1/analyses?limit=1",k))
first=next((i+1 for i,c in enumerate(codes) if c==429),None)
record("per-key rate limit",codes,f"first 429 at request #{first}" if first else "no 429 in 90 rapid reads")

# 2) in-flight cap on one key (documented 4)
print("probe 2: max in-flight per key (documented 4)",flush=True)
codes=[k2 for k2 in (post_analysis(keys[1],beats[i%len(beats)]) for i in range(7))]
first=next((i+1 for i,c in enumerate(codes) if c==429),None)
record("in-flight cap (1 key)",codes,f"first 429 at submission #{first}" if first else "no 429 within 7 submissions")

# 3) screen concurrency across many keys
print("probe 3: concurrent L1 screens across 30 keys",flush=True)
res=[]
def w(i):
    c=post_screen(keys[i%len(keys)],beats[i%len(beats)])
    with lock: res.append(c)
th=[threading.Thread(target=w,args=(i,)) for i in range(30)]
for t in th: t.start()
for t in th: t.join()
record("L1 burst x30",res,"429 = screen_busy, per-container CPU slots")

# 4) health under burst (no auth, cheapest path)
print("probe 4: /health burst x120",flush=True)
res2=[]
def h(_):
    try:
        with urllib.request.urlopen(BASE+'/health',timeout=20) as r: c=r.status
    except urllib.error.HTTPError as e: c=e.code
    except Exception: c=0
    with lock: res2.append(c)
th=[threading.Thread(target=h,args=(i,)) for i in range(120)]
t0=time.time()
for t in th: t.start()
for t in th: t.join()
record("/health burst x120",res2,f"{120/max(0.001,time.time()-t0):.0f} req/s sustained, unauthenticated")

json.dump({'probes':probes},open('/tmp/run50/stress.json','w'),indent=2)
print("wrote /tmp/run50/stress.json",flush=True)
