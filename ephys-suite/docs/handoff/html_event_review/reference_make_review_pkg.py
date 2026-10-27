"""Build a standalone, install-free sEPSC review package (HTML viewer + per-cell event snippets) for cells 03-14."""
import os, glob, json, base64, shutil
import numpy as np, pandas as pd, pyabf
from scipy.signal import butter, sosfiltfilt, resample_poly
SRC="/Users/kkaganovsky/Desktop/Personal Claude Workspace/ephys/hFA_sEPSC_final/"
OUT="/Users/kkaganovsky/Desktop/Personal Claude Workspace/ephys/hFA_sEPSC_review_package/"
FS=10000; PRE, POST = 100, 200                       # ms shown before / after the event
shutil.rmtree(OUT, ignore_errors=True)
for d in ("data","recordings","labels"): os.makedirs(OUT+d)
cells=[]
for csvp in sorted(glob.glob(SRC+"CNN_v3_candidates/*.candidates.csv")):
    tag=os.path.basename(csvp).replace(".candidates.csv",""); n=tag[:2]
    if n in ("01","02"): continue
    abf=SRC+"EPSC_files/"+tag+".abf"; rec_name=tag.split("__")[1]+".abf"; label=tag.split("__")[0][3:].replace("_"," ").replace("n1","#1").replace("n2","#2").replace("n3","#3").replace("n5","#5")
    shutil.copy2(abf, OUT+"recordings/"+tag+".abf"); shutil.copy2(csvp, OUT+"labels/"+os.path.basename(csvp))
    c=pd.read_csv(csvp); thr=0.99 if n in ("13","14") else 0.9
    if n=="13": t=c.time_in_sweep_ms/1000; c=c[~((t>=100)&(t<130))]
    c=c[c.score>=0.5].copy(); c["tier"]=np.where(c.score>=thr,"detected","maybe"); c=c.sort_values(["sweep","time_in_sweep_ms"]).reset_index(drop=True)
    a=pyabf.ABF(abf); fs=int(a.dataRate); sos=butter(4,3000,fs=fs,output="sos"); traces={}
    for s in sorted(c.sweep.unique()):
        a.setSweep(int(s)-1); y=sosfiltfilt(sos,a.sweepY.astype(float))
        traces[s]=y if fs==FS else resample_poly(y,FS,fs)
    L=int((PRE+POST)*FS/1000); buf=np.zeros((len(c),L),np.int16); ev=[]
    for i,r in enumerate(c.itertuples()):
        y=traces[r.sweep]; i0=int(round((r.time_in_sweep_ms-PRE)*FS/1000)); seg=np.full(L,np.nan)
        a0,a1=max(i0,0),min(i0+L,len(y)); seg[a0-i0:a1-i0]=y[a0:a1]
        med=np.nanmedian(seg); seg=np.nan_to_num(seg-med,nan=0.0)
        buf[i]=np.clip(np.round(seg*20),-32767,32767).astype(np.int16)
        ev.append([int(r.sweep),round(float(r.time_in_sweep_ms),2),round(float(r.score),4),r.tier])
    b64=base64.b64encode(buf.astype("<i2").tobytes()).decode()
    meta=dict(id=n,label=label,recording=rec_name,file=tag+".abf",n=len(ev),n_detected=int((c.tier=="detected").sum()),n_maybe=int((c.tier=="maybe").sum()),
              thr=thr,note=("100-130 s skipped (very noisy stretch)" if n=="13" else ""))
    open(OUT+"data/cell%s.js"%n,"w").write("window.CELLDATA=window.CELLDATA||{};window.CELLDATA['%s']=%s;window.CELLDATA['%s'].events=%s;window.CELLDATA['%s'].b64='%s';"%(n,json.dumps(meta),n,json.dumps(ev),n,b64))
    cells.append(meta); print(n,label,meta["n_detected"],meta["n_maybe"],flush=True)
open(OUT+"data/cells.js","w").write("window.CELLS=%s;window.SNIP={fs:%d,pre:%d,post:%d,scale:20};"%(json.dumps(cells),FS,PRE,POST))
