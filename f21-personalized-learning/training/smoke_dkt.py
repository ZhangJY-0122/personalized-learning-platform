"""Synthetic CPU smoke experiment, not ASSISTments and not final research."""
import json,random,hashlib,sys
from pathlib import Path
import numpy as np
import torch
from torch import nn
from sklearn.metrics import roc_auc_score,accuracy_score,mean_squared_error,log_loss
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from preflight import update
torch.set_num_threads(1)
def dataset():
    rng=random.Random(20260909); out=[]
    for student in range(36):
        ability=[rng.uniform(.1,.7) for _ in range(3)]; seq=[]
        for t in range(60):
            k=rng.randrange(3); correct=int(rng.random()<(.15+.75*ability[k]))
            seq.append((k,correct)); ability[k]=min(.96,ability[k]+.025)
        out.append(seq)
    return out
class DKT(nn.Module):
    def __init__(self):
        super().__init__(); self.embed=nn.Embedding(6,8);self.lstm=nn.LSTM(8,16,batch_first=True);self.head=nn.Linear(16,3)
    def forward(self,x): return self.head(self.lstm(self.embed(x))[0])
def metrics(y,p):
    return {"auc":float(roc_auc_score(y,p)) if len(set(y))==2 else None,
      "accuracy":float(accuracy_score(y,np.asarray(p)>=.5)),
      "rmse":float(mean_squared_error(y,p)**.5),"logLoss":float(log_loss(y,np.clip(p,1e-6,1-1e-6))),
      "samples":len(y)}
def main():
    seq=dataset(); encoded=torch.tensor([[k+3*c for k,c in row] for row in seq])
    skills=torch.tensor([[k for k,c in row] for row in seq])
    labels=torch.tensor([[c for k,c in row] for row in seq],dtype=torch.float32)
    results=[]; out=ROOT/"artifacts";out.mkdir(exist_ok=True)
    for seed in [11,22,33]:
        random.seed(seed);np.random.seed(seed);torch.manual_seed(seed)
        model=DKT(); opt=torch.optim.Adam(model.parameters(),lr=.01); best=float("inf"); best_state=None
        for epoch in range(12):
            model.train();opt.zero_grad()
            # Train targets 1..41. Inputs are only previous answers 0..40.
            logits=model(encoded[:,:41]).gather(2,skills[:,1:42,None]).squeeze(-1)
            loss=nn.functional.binary_cross_entropy_with_logits(logits,labels[:,1:42]);loss.backward();opt.step()
            model.eval()
            with torch.no_grad():
                pred=model(encoded[:,:47])[:,41:47].gather(2,skills[:,42:48,None]).squeeze(-1)
                val=nn.functional.binary_cross_entropy_with_logits(pred,labels[:,42:48]).item()
            if val<best: best=val;best_state={k:v.detach().clone() for k,v in model.state_dict().items()}
        model.load_state_dict(best_state); model.eval()
        with torch.no_grad():
            probs=model(encoded[:,:59])[:,47:59].gather(2,skills[:,48:60,None]).sigmoid().squeeze(-1)
        y=labels[:,48:60].flatten().tolist()
        result={"seed":seed,"validationLogLoss":best,"DKT":metrics(y,probs.flatten().tolist())}
        checkpoint=out/f"dkt-smoke-{seed}.pt"
        torch.save({"state":model.state_dict(),"skills":["variables","conditions","loops"],"dataType":"SYNTHETIC_DEMO"},checkpoint)
        restored=DKT();restored.load_state_dict(torch.load(checkpoint,weights_only=True)["state"]);restored.eval()
        with torch.no_grad():
            assert torch.equal(model(encoded[:,:59]),restored(encoded[:,:59]))
        result["reloadEqual"]=True;results.append(result)
    # Every baseline predicts before revealing the target; same test targets as DKT.
    y=[];bkt=[];rule=[]
    for row in seq:
        state=[.2]*3; histories=[[] for _ in range(3)]
        for t,(k,c) in enumerate(row):
            if t>=48:
                y.append(c);bkt.append(state[k]*.9+(1-state[k])*.2)
                h=histories[k][-20:];rule.append((1+sum(h))/(2+len(h)))
            state[k]=update(state[k],bool(c));histories[k].append(c)
    summary={"purpose":"PIPELINE_SMOKE_ONLY","dataType":"SYNTHETIC_DEMO","students":36,"interactions":2160,
      "split":{"train":[0,42],"validation":[42,48],"test":[48,60]},
      "seed":20260909,"datasetHash":hashlib.sha256(json.dumps(seq).encode()).hexdigest(),
      "baselineParameters":"fixed demo parameters; fitted BKT deferred to final research",
      "window":"60-step smoke sequence, next-step targets; final experiment window100",
      "rules":metrics(y,rule),"BKT":metrics(y,bkt),"runs":results,
      "limitations":["Synthetic data only","small hidden16 model for smoke; final architecture separate","not evidence of teaching benefit","not final tuned/fitted experiment"],
      "torch":torch.__version__,"python":sys.version}
    summary["DKT_mean_std"]={k:{"mean":float(np.mean([r["DKT"][k] for r in results])),
       "std":float(np.std([r["DKT"][k] for r in results],ddof=1))} for k in ["auc","accuracy","rmse","logLoss"]}
    (out/"training-smoke.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps({"rules":summary["rules"],"BKT":summary["BKT"],"DKT":summary["DKT_mean_std"],"reloadEqual":True}))
if __name__=="__main__": main()
