"""Inspect a byte-range sample, never label it a full dataset audit."""
import csv,io,json,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
p=ROOT/'data/raw/assistments-header-sample.csv'
raw=p.read_bytes()
text=raw[:raw.rfind(b'\n')+1].decode('utf-8-sig')
reader=csv.DictReader(io.StringIO(text)); rows=list(reader)
valid=[r for r in rows if all(r.get(k) not in (None,'','NA') for k in ['user_id','problem_id','skill_id','correct','order_id']) and r['correct'] in ('0','1')]
result={'source':'https://drive.usercontent.google.com/download?id=1NNXHFRxcArrU0ZJSb9BIL56vmUt5FhlE&export=download',
 'dataType':'PUBLIC_DATASET','scope':'FIRST_1MIB_ONLY_NOT_FULL_AUDIT','sha256':hashlib.sha256(raw).hexdigest(),
 'bytes':len(raw),'completeRows':len(rows),'validCoreRows':len(valid),'fields':reader.fieldnames,
 'fieldMapping':{'student_id':'user_id','item_id':'problem_id','knowledge_id':'skill_id (split underscore; deterministic primary skill)','correct':'correct','sequence_order':'order_id','elapsed_ms':'ms_first_response'},
 'hasTimestamp':False,'sequenceDecision':'Use order_id, report sequence split rather than real-time split',
 'multiSkillRows':sum('_' in r['skill_id'] for r in valid),
 'limitations':['Only first 1MiB inspected','Final partial line omitted','Do not infer full-dataset missing rates','Raw sample excluded from Git; redistribution permission not assessed']}
(ROOT/'artifacts/public-data-inspection.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:result[k] for k in ['completeRows','validCoreRows','hasTimestamp','multiSkillRows']}))
