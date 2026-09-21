"""G4C-03/G4C-04 isolated import smoke checks."""
import json, os, subprocess, time, uuid
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

ROOT=Path(__file__).resolve().parents[1]; PROJECT="f21-g4-import-"+uuid.uuid4().hex[:8]
PORT="18093"; BASE=f"http://127.0.0.1:{PORT}/api/v1"; C="10000000-0000-4000-8000-000000000001"; U="3d7a8826-cee4-525a-98d7-8cdb937e677e"
ENV={**os.environ,"SERVER_PORT":PORT,"WEB_PORT":"15183","WORKER_ENABLED":"false","JWT_SECRET":uuid.uuid4().hex+uuid.uuid4().hex}
COMPOSE=["docker","compose","-p",PROJECT,"-f","compose.yaml","-f","tests/compose-g1.yaml"]
def compose(*args,check=True): return subprocess.run(COMPOSE+list(args),cwd=ROOT,env=ENV,capture_output=True,text=True,check=check)
def req(path,body=None,token=None,key=None,ctype="application/json"):
 h={"Content-Type":ctype};
 if token:h["Authorization"]="Bearer "+token
 if key:h["Idempotency-Key"]=key
 r=Request(BASE+path,data=None if body is None else (body.encode() if isinstance(body,str) else json.dumps(body).encode()),headers=h,method="POST" if body is not None else "GET")
 try:x=urlopen(r,timeout=20)
 except HTTPError as e:x=e
 with x:return x.status,json.loads(x.read())
def sql(q):
 r=compose("exec","-T","mysql","mysql","-uf21","-pf21_local_demo_only","-Df21_prep","-N","-B","-e",q);return r.stdout.strip()
def main():
 try:
  compose("up","-d","--build","mysql","server"); deadline=time.time()+120
  while time.time()<deadline:
   try:
    if req("/health")[1].get("data",{}).get("database")=="UP":break
   except Exception:pass
   time.sleep(2)
  status,login=req("/auth/login",{"username":"admin01","password":"Learn@12345"}); assert status==200
  admin=login["data"]["accessToken"]
  event={"eventId":str(uuid.uuid4()),"userId":U,"courseId":C,"catalogVersion":"java-g1-v1","eventType":"COURSE_ENROLLED","sourceService":"lms-import","occurredAt":"2026-01-01T00:00:00Z","objectType":"COURSE","objectId":C}
  status,value=req("/admin/event-imports",[event],admin,str(uuid.uuid4())); assert status==200 and value["data"]["status"]=="SUCCEEDED",value
  csv_event={**event,"eventId":str(uuid.uuid4()),"objectType":"COURSE","objectId":"course,with,comma"}
  csv="eventId,userId,courseId,catalogVersion,eventType,sourceService,occurredAt,objectType,objectId\n"+','.join(csv_event[k] for k in ("eventId","userId","courseId","catalogVersion","eventType","sourceService","occurredAt","objectType"))+',"'+csv_event["objectId"]+'"\n'
  status,value=req("/admin/event-imports",csv,admin,str(uuid.uuid4()),"text/csv"); assert status==200 and value["data"]["status"]=="SUCCEEDED",value
  assert sql("SELECT COUNT(*) FROM import_job WHERE job_type='EVENT_CSV' AND status='SUCCEEDED';")=="1"
  print(json.dumps({"jsonStatus":"SUCCEEDED","csvStatus":"SUCCEEDED","csvJobType":"EVENT_CSV"}))
 finally: subprocess.run(COMPOSE+["down"],cwd=ROOT,env=ENV,capture_output=True,text=True)
if __name__=="__main__":main()
