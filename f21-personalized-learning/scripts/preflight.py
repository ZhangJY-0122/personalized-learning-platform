"""Deterministic pre-development scenario; generated outputs are not application state."""
import json, hashlib
from pathlib import Path
from fractions import Fraction as F
from uuid import UUID, uuid5
ROOT=Path(__file__).resolve().parents[1]
NS=UUID("10000000-0000-4000-8000-000000000001")
def uid(name): return str(uuid5(NS,name))
def write(path, value):
    p=ROOT/path; p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(value,ensure_ascii=False,indent=2)+"\n")
def update(l, correct, w=1):
    if not 0<=l<=1 or not 0<w<=1: raise ValueError("invalid probability")
    p=l*.9+(1-l)*.2
    q=l*.9/p if correct else l*.1/(1-p)
    return l+w*(q+(1-q)*.1-l)
def exact(l, correct, w=F(1)):
    p=l*F(9,10)+(1-l)*F(1,5)
    q=l*F(9,10)/p if correct else l*F(1,10)/(1-p)
    return l+w*(q+(1-q)*F(1,10)-l)
def topological(nodes,edges):
    degree={n:0 for n in nodes}
    for a,b in edges:
        if a not in degree or b not in degree or a==b: raise ValueError("bad edge")
        degree[b]+=1
    ordered=[]
    while len(ordered)<len(nodes):
        available=sorted(n for n in nodes if degree[n]==0 and n not in ordered)
        if not available: raise ValueError("cycle")
        n=available[0]; ordered.append(n)
        for a,b in edges:
            if a==n: degree[b]-=1
    return ordered
def replay(events):
    seen={}; l=.2; rows=[]
    for e in sorted(events,key=lambda e:(e["occurredAt"],e["eventSeq"],e["eventId"])):
        digest=json.dumps(e,sort_keys=True)
        if e["eventId"] in seen:
            if seen[e["eventId"]]!=digest: raise ValueError("event conflict")
            continue
        seen[e["eventId"]]=digest
        if e["eventType"]!="QUESTION_ANSWERED": continue
        before=l; l=update(l,e["correct"])
        rows.append({"eventId":e["eventId"],"before":before,"after":l})
    return l,rows
def main():
    knowledge=[{"knowledgeId":uid(n),"name":title,"order":i} for i,(n,title) in enumerate([
      ("variables","变量"),("conditions","条件判断"),("loops","循环")])]
    events=[]
    # Three errors establish sufficient evidence; six new correct answers demonstrate recovery.
    answers=[False]*3+[True]*6
    for i,c in enumerate(answers):
        events.append({"schemaVersion":1,"eventId":uid(f"event-{i}"),"eventSeq":i+1,
         "eventType":"QUESTION_ANSWERED","userId":uid("student"),"courseId":str(NS),
         "objectType":"QUESTION","objectId":uid(f"loop-question-{i}"),
         "occurredAt":f"2026-09-09T09:{i:02d}:00+08:00","correct":c,
         "knowledgeItems":[{"knowledgeId":uid("loops"),"weight":1.0}],
         "catalogVersion":"java-prep-v1","sourceService":"local-practice","traceId":uid(f"trace-{i}")})
    package={"dataType":"SYNTHETIC_DEMO","seed":20260909,"courseId":str(NS),
      "studentId":uid("student"),"catalogVersion":"java-prep-v1","knowledge":knowledge,
      "edges":[[uid("variables"),uid("conditions")],[uid("conditions"),uid("loops")]],
      "resources":[{"resourceId":uid("loop-resource"),"knowledgeId":uid("loops"),
       "title":"循环入门","difficulty":.2,"content":"for循环由初始化、条件和更新组成。练习：计算0到2的累加结果。"}],
      "questions":[{"questionId":uid(f"loop-question-{i}"),"knowledgeId":uid("loops"),
       "stem":f"从0累加到{i+2}的结果是？","options":[str((i+2)*(i+3)//2),"-1"],
       "answer":"A","difficulty":.2+i*.05} for i in range(9)],
      "events":events}
    prior=.2
    for _ in range(6): prior=update(prior,True)
    package['preconditions']=[{'knowledgeId':uid(k),'correctSequence':[True]*6,
       'mastery':prior,'evidenceCount':6,'evidenceWeight':6} for k in ['variables','conditions']]
    write(Path("data/demo/scenario.json"),package)
    vectors=[]; l=F(1,5)
    for c in answers:
        out=exact(l,c); vectors.append({"before":float(l),"correct":c,"weight":1.0,
          "expected":float(out),"exactNumerator":str(out.numerator),"exactDenominator":str(out.denominator)})
        l=out
    out=exact(F(1,5),False,F(1,2))
    vectors.append({"before":.2,"correct":False,"weight":.5,"expected":float(out)})
    write(Path("tests/fixtures/bkt-vectors.json"),vectors)
    write(Path("server/src/test/resources/bkt-vectors.json"),vectors)
    final,history=replay(events)
    weak=history[2]["after"]
    # Fixed actual component values from the three-error scenario, not fake model scores.
    weakness=.6*(1-weak)+.2*1+.1*.5+.1*0
    detail={"weaknessMatch":weakness,"difficultyMatch":1-abs(.2-weak),"contentMatch":1}
    score=(.45*detail["weaknessMatch"]+.25*detail["difficultyMatch"]+.2)/.9
    report={"stage":"ALGORITHM_SPIKE_NOT_APPLICATION","dataType":"SYNTHETIC_DEMO",
      "sha256":hashlib.sha256((ROOT/"data/demo/scenario.json").read_bytes()).hexdigest(),
      "afterThreeWrong":weak,"evidenceCount":3,"weak":weak<.6,
      "finalMastery":final,"afterRecoveryAdequate":final>=.6,"history":history,
      "recommendation":{"resourceId":uid("loop-resource"),"score":score,"components":detail,
       "disabledComponents":["preference: no evidence"],
       "reasons":[f"循环相关3次作答错误，BKT估计为{weak:.4f}","资源关联循环知识点，标注难度0.2"]},
      "topologicalPath":topological([k["knowledgeId"] for k in knowledge],package["edges"]),
      "activeTargetPath":[uid('loops')],"prerequisitesSatisfied":all(x['mastery']>=.6 for x in package['preconditions']),
      "duplicateInvariant":replay(events+events)==replay(events),
      "orderInvariant":replay(list(reversed(events)))==replay(events)}
    write(Path("artifacts/preflight-result.json"),report)
    print(json.dumps({k:report[k] for k in ["afterThreeWrong","finalMastery","duplicateInvariant","orderInvariant"]}))
if __name__=="__main__": main()
