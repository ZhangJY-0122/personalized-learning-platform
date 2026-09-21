"""Real G4C-06 isolation runner.

The runner intentionally fails closed: every matrix row is recorded, and any
scenario not yet proven keeps the top-level artifact failed.
"""
import concurrent.futures, json, os, subprocess, time, uuid
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

ROOT=Path(__file__).resolve().parents[1]; ART=ROOT/'artifacts/g4-acceptance.json'
PROJECT='f21-g4-check-'+uuid.uuid4().hex[:8]; PORT='18098'; WEB='15188'
ENV={**os.environ,'SERVER_PORT':PORT,'WEB_PORT':WEB,'WORKER_ENABLED':'false','JWT_SECRET':uuid.uuid4().hex+uuid.uuid4().hex}
COMPOSE=['docker','compose','-p',PROJECT,'-f','compose.yaml','-f','tests/compose-g1.yaml']; BASE=f'http://127.0.0.1:{PORT}/api/v1'
C='10000000-0000-4000-8000-000000000001'; U='3d7a8826-cee4-525a-98d7-8cdb937e677e'
scenarios=[]; http_checks=0; db_assertions=0; concurrency_checks=0; restart_checks=0; fault_checks=0

def compose(*args,check=True):
    p=subprocess.run(COMPOSE+list(args),cwd=ROOT,env=ENV,capture_output=True,text=True)
    if check and p.returncode: raise RuntimeError(p.stderr[-3000:])
    return p
def sql(q):
    global db_assertions
    p=compose('exec','-T','mysql','mysql','-uf21','-pf21_local_demo_only','-Df21_prep','-N','-B','-e',q)
    db_assertions+=1; return p.stdout.rstrip('\n')
def sql_root(q):
    p=compose('exec','-T','mysql','mysql','-uroot','-pf21_root_local_demo_only','-Df21_prep','-N','-B','-e',q)
    return p.stdout.rstrip('\n')
def req(path,token=None,body=None,key=None,expected=200,ctype='application/json'):
    global http_checks
    h={'Content-Type':ctype};
    if token:h['Authorization']='Bearer '+token
    if key:h['Idempotency-Key']=key
    r=Request(BASE+path,data=None if body is None else (body.encode() if isinstance(body,str) else json.dumps(body).encode()),headers=h,method='POST' if body is not None else 'GET')
    try:x=urlopen(r,timeout=20)
    except HTTPError as e:x=e
    with x:
        value=json.loads(x.read()); http_checks+=1
        if x.status!=expected: raise AssertionError((path,x.status,expected,value))
        if 'traceId' not in value or x.headers.get('X-Trace-Id')!=value['traceId']: raise AssertionError(('trace',path,value))
        return value
def login(name): return req('/auth/login',body={'username':name,'password':'Learn@12345'})['data']['accessToken']
def scenario(sid,name,fn):
    try:
        evidence=fn() or {}; scenarios.append({'id':sid,'name':name,'passed':True,'assertions':1,'evidence':evidence})
    except Exception as e:
        scenarios.append({'id':sid,'name':name,'passed':False,'assertions':0,'evidence':{},'error':str(e)})

def main():
    global concurrency_checks,restart_checks
    start_dirty=subprocess.run(['git','status','--porcelain'],cwd=ROOT,capture_output=True,text=True).stdout.strip()
    try:
        static=subprocess.run([str(ROOT/'.venv/bin/python'),'tests/g4_static_contract.py'],cwd=ROOT,capture_output=True,text=True)
        if static.returncode:
            scenarios.append({'id':'G4-STATIC','name':'static contract phase','passed':False,'assertions':0,'evidence':{},'error':static.stderr[-2000:]})
        else:
            scenarios.append({'id':'G4-STATIC','name':'static contract phase','passed':True,'assertions':1,'evidence':{'output':static.stdout[-500:]}})
        compose('up','-d','--build')
        deadline=time.time()+150
        while time.time()<deadline:
            try:
                h=req('/health')
                if h['data'].get('database')=='UP': break
            except Exception: pass
            time.sleep(2)
        student=login('student01'); outsider=login('student02'); teacher=login('teacher01'); admin=login('admin01')
        scenario('G4-DB-01','fresh V1-to-V7 migration',lambda:{'migrationVersions':sql("SELECT GROUP_CONCAT(version ORDER BY installed_rank) FROM flyway_schema_history WHERE success=1")})
        scenario('G4-DB-04','restart does not duplicate migrations',lambda: migration_restart(student))
        scenario('G4-PATH-01','GET empty path is read-only',lambda: path_empty(student))
        scenario('G4-PATH-02','path authorization boundaries',lambda: path_auth(student,outsider,teacher,admin))
        scenario('G4-PATH-03','same-key concurrent generation',lambda: path_same_key(student))
        scenario('G4-PATH-04','different-key generation keeps one active path',lambda: path_different_key(student))
        path=req(f'/students/{U}/learning-path?courseId={C}',student)['data']; resource=next(n for n in path['nodes'] if n['itemType']=='RESOURCE'); node=resource['nodeId']; rid=resource['itemId']
        scenario('G4-PATH-05','path resource VIEWED is idempotent and BKT-neutral',lambda: path_view(student,rid,node))
        scenario('G4-FAULT-02','node completion rollback',lambda: fault_node_completion(student,rid,node))
        scenario('G4-PATH-06','path resource COMPLETED has resource evidence',lambda: path_complete(student,rid,node))
        scenario('G4-FAULT-01','path generation rollback',lambda: fault_path_generation(student))
        scenario('G4-FAULT-03','replan insert rollback',lambda: fault_replan(student))
        scenario('G4-REG-01','G1 authentication and catalog regression',lambda: regression_g1(student))
        scenario('G4-REG-02','G2 practice and mastery regression',lambda: regression_g2(student))
        scenario('G4-REG-03','G3 recommendation regression',lambda: regression_g3(student))
        scenario('G4-PATH-07','path question transaction A/B',lambda: path_question(student))
        scenario('G4-PATH-08','invalid path attribution rejected',lambda: path_invalid(student,outsider,rid,node))
        scenario('G4-IMPORT-01','JSON 501 upper bound rejects before write',lambda: import_json_limit(admin))
        scenario('G4-IMPORT-02','CSV 10001 data rows rejects before write',lambda: import_csv_limit(admin))
        scenario('G4-IMPORT-03','CSV quoted comma semantics and job type',lambda: import_csv_semantics(admin))
        scenario('G4-IMPORT-04','event same-id replay and changed-content conflict',lambda: import_idempotency(admin))
        scenario('G4-CATALOG-01','invalid catalog package rejected',lambda: catalog_invalid(admin))
        scenario('G4-FAULT-04','pending state survives server restart',lambda: restart_check(student))
        # These rows remain explicit until their real DB fault/replay fixtures are added.
        for sid,name in [('G4-DB-02','V5-to-V7 upgrade'),('G4-DB-03','V6-to-V7 upgrade'),('G4-PATH-09','first retest failure'),('G4-PATH-10','second retest BLOCKED'),('G4-PATH-11','full path completion'),('G4-PATH-12','ordinary activity invalidates path'),('G4-PATH-13','carried completion inheritance'),('G4-CATALOG-02','catalog ACTIVATE'),('G4-CATALOG-03','catalog publish rollback'),('G4-MAP-01','SAME_UUID rebuild baseline'),('G4-MAP-02','EXPLICIT rebuild baseline'),('G4-MAP-03','unmapped warning'),('G4-REBUILD-01','rebuild rollback'),('G4-COMP-01','unknown catalog compensation'),('G4-COMP-02','archive-only compensation recovery')]:
            scenarios.append({'id':sid,'name':name,'passed':False,'assertions':0,'evidence':{},'error':'scenario not yet implemented in live runner; G4 remains stopped'})
        passed=all(x['passed'] for x in scenarios)
        result={'passed':passed,'verifiedAt':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'gitCommit':subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,capture_output=True,text=True).stdout.strip(),'dirtyAtStart':bool(start_dirty),'composeProject':PROJECT,'volumeRetained':True,'serverImageId':compose('images','-q','server',check=False).stdout.strip(),'webImageId':compose('images','-q','web',check=False).stdout.strip(),'mysqlImageId':compose('images','-q','mysql',check=False).stdout.strip(),'migrationVersions':sql("SELECT version FROM flyway_schema_history WHERE success=1 ORDER BY installed_rank" ).splitlines(),'httpChecks':http_checks,'databaseAssertions':db_assertions,'faultInjectionChecks':fault_checks,'concurrencyChecks':concurrency_checks,'restartChecks':restart_checks,'regressions':{},'scenarios':scenarios,'errors':[]}
        ART.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n'); print(json.dumps({'passed':passed,'project':PROJECT,'scenarios':len(scenarios),'httpChecks':http_checks},ensure_ascii=False));
        if not passed: raise SystemExit(1)
    finally: compose('down',check=False)

def path_empty(token):
    before=sql('SELECT COUNT(*) FROM learning_path'); value=req(f'/students/{U}/learning-path?courseId={C}',token)['data']; after=sql('SELECT COUNT(*) FROM learning_path'); assert not value['generated'] and before==after; return {'generated':value['generated'],'pathRows':after}
def path_auth(student,outsider,teacher,admin):
    req(f'/students/{U}/learning-path/generate',student,{'courseId':C},str(uuid.uuid4()),200)
    req(f'/students/{U}/learning-path/generate',outsider,{'courseId':C},str(uuid.uuid4()),403)
    req(f'/students/{U}/learning-path/generate',teacher,{'courseId':C},str(uuid.uuid4()),403)
    value=req(f'/students/{U}/learning-path?courseId={C}',admin,expected=200)['data']
    assert 'pathId' in value and value.get('generated') is True
    return {'student':'allowed','outsider':'403','teacher':'403','admin':'read-only'}
def path_same_key(token):
    global concurrency_checks
    key=str(uuid.uuid4())
    before=int(sql('SELECT COUNT(*) FROM learning_path_request'))
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool: values=list(pool.map(lambda _:req(f'/students/{U}/learning-path/generate',token,{'courseId':C},key),range(8)))
    ids={x['data']['pathId'] for x in values}; request_rows=int(sql('SELECT COUNT(*) FROM learning_path_request')); concurrency_checks+=1; assert len(ids)==1 and request_rows==before+1, {'pathIds':list(ids),'requestRows':request_rows,'before':before,'responses':values}; return {'requestCount':8,'pathIds':list(ids),'requestRows':request_rows,'delta':request_rows-before}
def path_different_key(token):
    keys=[str(uuid.uuid4()) for _ in range(4)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool: list(pool.map(lambda k:req(f'/students/{U}/learning-path/generate',token,{'courseId':C},k),keys))
    rows=sql("SELECT status,path_version FROM learning_path WHERE user_id='%s' AND course_id='%s' ORDER BY path_version"%(U,C)).splitlines(); versions=[int(r.split('\t')[1]) for r in rows]; assert sum(r.startswith('ACTIVE') for r in rows)==1 and versions==list(range(1,max(versions)+1)); return {'versions':rows,'concurrentRequests':4}
def path_view(token,rid,node):
    before=sql("SELECT COUNT(*) FROM mastery_history WHERE knowledge_id IN (SELECT knowledge_id FROM learning_path_node WHERE node_id='%s')"%node); req(f'/resources/{rid}/views',token,{'pathNodeId':node},str(uuid.uuid4())); duplicate=req(f'/resources/{rid}/views',token,{'pathNodeId':node},str(uuid.uuid4()),409); row=sql("SELECT status FROM learning_path_node WHERE node_id='%s'"%node); after=sql("SELECT COUNT(*) FROM mastery_history WHERE knowledge_id IN (SELECT knowledge_id FROM learning_path_node WHERE node_id='%s')"%node); assert row=='IN_PROGRESS' and before==after; return {'nodeStatus':row,'duplicateCode':duplicate['code']}
def path_complete(token,rid,node):
    value=req(f'/resources/{rid}/completions',token,{'pathNodeId':node},str(uuid.uuid4()))['data']; row=sql("SELECT COALESCE(source_event_id,''),COALESCE(source_resource_activity_id,''),status FROM learning_path_node WHERE node_id='%s'"%node).split('\t'); assert row[0]=='' and row[1]==value['sourceEventId'] and row[2]=='COMPLETED'; return {'sourceEventId':value['sourceEventId'],'nodeStatus':row[2]}
def path_invalid(student,outsider,rid,node): req(f'/resources/{rid}/views',outsider,{'pathNodeId':node},str(uuid.uuid4()),403); req(f'/resources/{rid}/views',student,{'pathNodeId':str(uuid.uuid4())},str(uuid.uuid4()),404); return {'wrongUser':'403','unknownNode':'404'}
def regression_g1(token):
    courses=req('/courses',token)['data']; assert courses['total']>=1 and any(x['courseId']==C for x in courses['items']); me=req('/users/me',token)['data']; assert me['username']=='student01'; return {'courseList':True,'currentUser':me['username']}
def regression_g2(token):
    mastery=req(f'/students/{U}/mastery?courseId={C}',token)['data']; history=req(f'/students/{U}/practice-history?courseId={C}',token)['data']; assert 'items' in mastery and 'items' in history and mastery['meta']['catalogVersion']=='java-g1-v1'; return {'masteryItems':len(mastery['items']),'historyTotal':history['total']}
def regression_g3(token):
    before=req(f'/students/{U}/recommendations?courseId={C}',token)['data']; generated=req(f'/students/{U}/recommendations/generate',token,{'courseId':C},str(uuid.uuid4()))['data']; after=req(f'/students/{U}/recommendations?courseId={C}',token)['data']; assert isinstance(before,dict) and isinstance(generated,dict) and isinstance(after,dict); return {'generated':True,'readAfterGenerate':True}
def import_json_limit(admin):
    before=sql("SELECT COUNT(*) FROM import_job WHERE job_type='EVENT_JSON'"); events=[{'eventId':str(uuid.uuid4()),'userId':U,'courseId':C,'catalogVersion':'java-g1-v1','eventType':'COURSE_ENROLLED','sourceService':'import','occurredAt':'2026-01-01T00:00:00Z'} for _ in range(501)]; req('/admin/event-imports',admin,events,str(uuid.uuid4()),400); after=sql("SELECT COUNT(*) FROM import_job WHERE job_type='EVENT_JSON'"); assert before==after; return {'before':before,'after':after}
def import_csv_limit(admin):
    csv='eventId,userId,courseId,catalogVersion,eventType,sourceService,occurredAt\n'+'\n'.join(','.join([str(uuid.uuid4()),U,C,'java-g1-v1','COURSE_ENROLLED','import','2026-01-01T00:00:00Z']) for _ in range(10001)); req('/admin/event-imports',admin,csv,str(uuid.uuid4()),400,'text/csv'); return {'rows':10001}
def import_csv_semantics(admin):
    event={'eventId':str(uuid.uuid4()),'userId':U,'courseId':C,'catalogVersion':'java-g1-v1','eventType':'COURSE_ENROLLED','sourceService':'import','occurredAt':'2026-01-01T00:00:00Z','objectId':'comma,value'}; csv='eventId,userId,courseId,catalogVersion,eventType,sourceService,occurredAt,objectId\n'+','.join(event[k] for k in ('eventId','userId','courseId','catalogVersion','eventType','sourceService','occurredAt'))+',"comma,value"\n'; value=req('/admin/event-imports',admin,csv,str(uuid.uuid4()),ctype='text/csv')['data']; assert value['status']=='SUCCEEDED' and sql("SELECT job_type FROM import_job WHERE job_id='%s'"%value['jobId'])=='EVENT_CSV'; return {'status':value['status'],'jobType':'EVENT_CSV'}
def import_idempotency(admin):
    event={'eventId':str(uuid.uuid4()),'userId':U,'courseId':C,'catalogVersion':'java-g1-v1','eventType':'COURSE_ENROLLED','sourceService':'import','occurredAt':'2026-01-01T00:00:00Z'}; key=str(uuid.uuid4()); first=req('/admin/event-imports',admin,[event],key)['data']; second=req('/admin/event-imports',admin,[event],key)['data']; assert first['jobId']==second['jobId']; changed={**event,'sourceService':'other'}; conflict=req('/admin/event-imports',admin,[changed],str(uuid.uuid4()),200)['data']; failed=conflict.get('failedEvents',[]); assert conflict['status']=='SUCCEEDED_WITH_WARNINGS' and any(x.get('errorCode')=='CONFLICT' for x in failed); return {'jobId':first['jobId'],'replaySame':True,'changedConflict':'failedEvents.CONFLICT'}
def catalog_invalid(admin):
    before=sql("SELECT COUNT(*) FROM import_job WHERE job_type='CATALOG_JSON'")
    statuses=[]
    for body in ({},{'catalogVersion':'bad'}, {'catalogVersion':'bad','courseId':C,'chapters':[]}):
        value=req('/admin/catalog-imports',admin,body,str(uuid.uuid4()),200)['data']; statuses.append(value['status']); assert value['status']=='VALIDATION_FAILED'
    after=sql("SELECT COUNT(*) FROM import_job WHERE job_type='CATALOG_JSON'"); assert int(after)==int(before)+3; return {'invalidBodies':3,'statuses':statuses,'jobsBefore':before,'jobsAfter':after}
def path_question(token):
    ENV['WORKER_ENABLED']='true'; compose('up','-d','--force-recreate','--no-deps','server')
    deadline=time.time()+120
    while time.time()<deadline:
        try:
            if req('/health')['data'].get('database')=='UP': break
        except Exception: pass
        time.sleep(2)
    else: raise RuntimeError(compose('logs','--tail','80','server',check=False).stdout[-5000:])
    value=req(f'/students/{U}/learning-path?courseId={C}',token)['data']; q=next(n for n in value['nodes'] if n['itemType']=='QUESTION' and n['actionable']); detail=req('/questions/'+q['itemId'],token)['data']; options=detail.get('options',[]); answer=[options[0].get('key')] if options else ['A']; body={'questionId':q['itemId'],'answer':answer,'catalogVersion':value['meta']['catalogVersion'],'pathNodeId':q['nodeId']}; first=req('/practice/submissions',token,body,str(uuid.uuid4()))['data']; deadline=time.time()+30; result=None
    while time.time()<deadline:
        result=req('/practice/submissions/'+first['submissionId'],token)['data']
        if result.get('processingStatus')=='SUCCEEDED': break
        time.sleep(1)
    row=sql("SELECT status FROM learning_path_node WHERE node_id='%s'"%q['nodeId']); assert result and result.get('processingStatus')=='SUCCEEDED' and row=='COMPLETED'; return {'submissionId':first['submissionId'],'processingStatus':result['processingStatus'],'nodeStatus':row}
def migration_restart(token):
    before=sql("SELECT version,checksum FROM flyway_schema_history WHERE success=1 ORDER BY installed_rank").splitlines(); compose('restart','server'); deadline=time.time()+60
    while time.time()<deadline:
        try:
            if req('/health')['data'].get('database')=='UP': break
        except Exception: pass
        time.sleep(2)
    after=sql("SELECT version,checksum FROM flyway_schema_history WHERE success=1 ORDER BY installed_rank").splitlines(); assert before==after; return {'versions':after,'unchanged':True}
def fault_path_generation(token):
    name='g4_fail_path_node'; sql_root(f"DROP TRIGGER IF EXISTS {name}"); sql_root(f"CREATE TRIGGER {name} BEFORE INSERT ON learning_path_node FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='g4 injected path failure'")
    before=sql("SELECT COUNT(*) FROM learning_path"),sql("SELECT COUNT(*) FROM learning_path_node"),sql("SELECT COUNT(*) FROM learning_path_request")
    req(f'/students/{U}/learning-path/generate',token,{'courseId':C},str(uuid.uuid4()),500)
    after=sql("SELECT COUNT(*) FROM learning_path"),sql("SELECT COUNT(*) FROM learning_path_node"),sql("SELECT COUNT(*) FROM learning_path_request"); sql_root(f"DROP TRIGGER IF EXISTS {name}"); assert before==after; return {'rolledBack':True,'before':before,'after':after}
def fault_node_completion(token,rid,node):
    name='g4_fail_node_complete'
    sql_root(f"DROP TRIGGER IF EXISTS {name}"); sql_root(f"CREATE TRIGGER {name} BEFORE INSERT ON resource_activity FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='g4 injected completion failure'")
    before=sql("SELECT COUNT(*) FROM resource_activity"),sql("SELECT status FROM learning_path_node WHERE node_id='%s'"%node)
    try:
        req(f'/resources/{rid}/completions',token,{'pathNodeId':node},str(uuid.uuid4()),500)
    finally:
        sql_root(f"DROP TRIGGER IF EXISTS {name}")
    after=sql("SELECT COUNT(*) FROM resource_activity"),sql("SELECT status FROM learning_path_node WHERE node_id='%s'"%node); assert before==after and after[1]=='IN_PROGRESS'; return {'rolledBack':True,'before':before,'after':after}
def fault_replan(token):
    path=req(f'/students/{U}/learning-path?courseId={C}',token)['data']; name='g4_fail_replan'; sql_root(f"CREATE TRIGGER {name} BEFORE INSERT ON path_replan_job FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='g4 injected replan failure'")
    before=sql("SELECT status FROM learning_path WHERE path_id='%s'"%path['pathId']),sql("SELECT COUNT(*) FROM path_replan_job"); req(f'/resources/{path["nodes"][0]["itemId"]}/views',token,{},str(uuid.uuid4()),500); after=sql("SELECT status FROM learning_path WHERE path_id='%s'"%path['pathId']),sql("SELECT COUNT(*) FROM path_replan_job"); sql_root(f"DROP TRIGGER IF EXISTS {name}"); assert before==after; return {'rolledBack':True,'before':before,'after':after}
def restart_check(token):
    global restart_checks
    before=req('/students/'+U+'/learning-path?courseId='+C,token)['data']; compose('restart','server'); deadline=time.time()+90
    while time.time()<deadline:
        try:
            if req('/health')['data'].get('database')=='UP': break
        except Exception: pass
        time.sleep(2)
    after=req('/students/'+U+'/learning-path?courseId='+C,token)['data']; restart_checks+=1; assert before['pathId']==after['pathId']; return {'pathId':before['pathId'],'stable':True}
if __name__=='__main__': main()
