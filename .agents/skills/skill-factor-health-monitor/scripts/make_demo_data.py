from __future__ import annotations
import argparse
from pathlib import Path
import numpy as np
import pandas as pd

def main():
    p=argparse.ArgumentParser(); p.add_argument("--output",default="data/decay_demo.csv"); p.add_argument("--days",type=int,default=520); p.add_argument("--symbols",type=int,default=80); p.add_argument("--seed",type=int,default=7); a=p.parse_args()
    rng=np.random.default_rng(a.seed); dates=pd.bdate_range("2023-01-02",periods=a.days); rows=[]; names=["decay_slow","decay_fast","decay_late","stable"]
    for t,date in enumerate(dates):
        strength=np.array([.06 if t<280 else .06-(t-280)*.00035,.055 if t<320 else .055-(t-320)*.0007,.05 if t<360 else .05-(t-360)*.00055,.045]); x=rng.normal(size=(a.symbols,4)); ret=sum(strength[j]*x[:,j] for j in range(4))/20+rng.normal(0,.012,a.symbols)
        for i in range(a.symbols):
            for j,name in enumerate(names): rows.append((date,f"S{i:04d}",name,x[i,j],ret[i]))
    out=Path(a.output); out.parent.mkdir(parents=True,exist_ok=True); pd.DataFrame(rows,columns=["date","symbol","factor","value","next_return"]).to_csv(out,index=False); print(out)
if __name__=="__main__": main()
