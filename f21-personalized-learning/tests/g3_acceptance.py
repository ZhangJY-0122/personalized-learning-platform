"""G3 acceptance on an isolated database. Writes never target the main demo volume."""
import concurrent.futures,hashlib,json,os,re,subprocess,sys,time,uuid
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from jsonschema import Draft202012Validator,FormatChecker
ROOT=Path(__file__).resolve().parents[1]
PROJECT='f21-g3-check-'+uuid.uuid4().hex[:8]
ENV={**os.environ,'SERVER_PORT':'18086','WEB_PORT':'15176','WORKER_ENABLED':'true'}
COMPOSE=['docker','compose','-p',PROJECT,'-f','compose.yaml','-f','tests/compose-g1.yaml']
BASE='http://127.0.0.1:18086/api/v1'
SPEC=json.loads((ROOT/'docs/openapi.json').read_text())
SCENARIO=json.loads((ROOT/'data/demo/scenario.json').read_text())
C=SCENARIO['courseId'];U=SCENARIO['studentId'];Q=SCENARIO['questions'][0]['questionId'];K=SCENARIO['knowledge'][2]['knowledgeId']
checks=[]
def compose(*args):
    p=subprocess.run(COMPOSE+list(args),cwd=ROOT,env=ENV,capture_output=True,text=True)
    if p.returncode:raise RuntimeError(p.stderr[-3000:])
    return p.stdout
def sql(statement,as_root=False):
    command='MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql --default-character-set=utf8mb4 -u root "$MYSQL_DATABASE" -N -B' if as_root else 'MYSQL_PWD="$MYSQL_PASSWORD" mysql --default-character-set=utf8mb4 -u "$MYSQL_USER" "$MYSQL_DATABASE" -N -B'
    p=subprocess.run(COMPOSE+['exec','-T','mysql','sh','-c',command],input=statement,cwd=ROOT,env=ENV,capture_output=True,text=True)
    if p.returncode:raise RuntimeError(p.stderr[-3000:])
    return p.stdout.strip()
def request(path,token=None,body=None,key=None,status=200):
    headers={'Content-Type':'application/json'}
    if token:headers['Authorization']='Bearer '+token
    if key:headers['Idempotency-Key']=key
    r=Request(BASE+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
    try:response=urlopen(r,timeout=20)
    except HTTPError as e:response=e
    with response:
        value=json.loads(response.read());assert response.status in (status if isinstance(status,tuple) else (status,)),(path,response.status,status,value)
        status=response.status
        assert response.headers.get('X-Trace-Id')==value['traceId']
        for template,ops in SPEC['paths'].items():
            if re.fullmatch(re.sub(r'\{[^}]+\}',r'[^/]+',template),path.split('?')[0]):
                op=ops.get(r.get_method().lower())
                if op and str(status) in op['responses']:
                    schema=op['responses'][str(status)]['content']['application/json']['schema']
                    Draft202012Validator({**schema,'components':SPEC['components']},format_checker=FormatChecker()).validate(value)
    checks.append({'path':path,'status':status})
    return value.get('data',value)
def login(name):return request('/auth/login',body={'username':name,'password':'Learn@12345'})['accessToken']
def wait(fn,timeout=40):
    deadline=time.monotonic()+timeout;last=None
    while time.monotonic()<deadline:
        try:
            result=fn()
            if result:return result
        except Exception as e:last=e
        time.sleep(.3)
    raise AssertionError(('condition timed out',str(last)))
def ready():return wait(lambda:request('/health')['database']=='UP',120)
def worker(enabled):
    ENV['WORKER_ENABLED']='true' if enabled else 'false'
    compose('up','-d','--no-deps','--no-build','--force-recreate','server');ready()
def body(answer='A',question=Q,version='java-g1-v1'):return {'questionId':question,'answer':answer,'catalogVersion':version}
def submit(answer='A',question=Q,key=None):return request('/practice/submissions',student,body(answer,question),key or str(uuid.uuid4()))
def state(token=None):return request('/students/'+U+'/mastery?courseId='+C,token or student)
def item():return next(i for i in state()['items'] if i['knowledgeId']==K)
def finished(s):return request('/practice/submissions/'+s['submissionId'],student)['processingStatus']=='SUCCEEDED'
def baseline(user,course):
    return sql("SELECT CONCAT(state_revision,':',replay_generation,':',status) FROM learner_course_state WHERE user_id='"+user+"' AND course_id='"+course+"';")
def counts():
    return [int(sql('SELECT COUNT(*) FROM '+t+';')) for t in ['practice_submission','event_consume_log','learning_interaction','mastery_history']]
def bkt(l,correct):
    p=l*.9+(1-l)*.2;q=l*.9/p if correct else l*.1/(1-p)
    return max(1e-6,min(1-1e-6,q+(1-q)*.1))

def recs():return request('/students/'+U+'/recommendations?courseId='+C,student)
def generate(key=None):return request('/students/'+U+'/recommendations/generate',student,{'courseId':C},key or str(uuid.uuid4()))
def feedback(rec,kind,source=None,key=None,status=200):
    return request('/recommendations/'+rec+'/feedback',student,{'feedbackType':kind,**({'sourceEventId':source} if source else {})},key or str(uuid.uuid4()),status)
def activity(rid,kind,rec=None,key=None,status=200):
    return request('/resources/'+rid+'/'+kind,student,{'recommendationId':rec} if rec else {},key or str(uuid.uuid4()),status)
def g3counts():return [int(sql('SELECT COUNT(*) FROM '+t+';')) for t in ['recommendation_batch','recommendation_item','recommendation_feedback','recommendation_request','resource_activity']]
passed=False
try:
    compose('up','-d','--no-build');ready()
    assert sql("SELECT COUNT(*) FROM flyway_schema_history WHERE success=1 AND version='5';")=='1'
    student=login('student01');outsider=login('student02');teacher=login('teacher01');admin=login('admin01')
    before=g3counts()
    assert recs()['batchId'] is None and recs()['items']==[]
    assert before==g3counts() and sql('SELECT COUNT(*) FROM learner_course_state;')=='0'
    # A learner may already have state but no recommendation batch. GET stays read-only.
    sql("INSERT INTO learner_course_state(user_id,course_id,catalog_version,state_revision,status) VALUES ('"+U+"','"+C+"','java-g1-v1',7,'REBUILD_REQUIRED');")
    empty=recs();assert empty['batchId'] is None and empty['meta']['stateRevision']==7 and empty['meta']['status']=='PROCESSING' and empty['meta']['stale']
    sql("UPDATE learner_course_state SET status='READY' WHERE user_id='"+U+"' AND course_id='"+C+"';")
    empty=recs();assert empty['meta']['stateRevision']==7 and not empty['meta']['stale'] and before==g3counts()
    sql("DELETE FROM learner_course_state WHERE user_id='"+U+"' AND course_id='"+C+"';")
    request('/students/'+U+'/recommendations?courseId='+C,outsider,status=403)
    request('/students/'+U+'/recommendations/generate',teacher,{'courseId':C},str(uuid.uuid4()),403)
    request('/students/'+U+'/recommendations/generate',student,{'courseId':C},status=400)
    key=str(uuid.uuid4())
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        batches=list(pool.map(lambda _:generate(key),range(8)))
    first=batches[0];assert all(x==first for x in batches)
    assert g3counts()[0]==1 and first['mode']=='DIAGNOSTIC' and len(first['items'])==5
    assert {i['itemType'] for i in first['items']}=={'RESOURCE','QUESTION'}
    for i in first['items']:
        assert len(i['reasons'])>=2
        assert abs(sum(x['effectiveWeight']*(x['value'] or 0) for x in i['scoreDetails'].values())-i['score'])<1e-12
    old=g3counts();assert recs()==first and g3counts()==old
    resource=next(i for i in first['items'] if i['itemType']=='RESOURCE')
    question=next(i for i in first['items'] if i['itemType']=='QUESTION')
    rid=resource['itemId'];rr=resource['recommendationId'];qr=question['recommendationId']
    # Same key with another authorized course conflicts, rather than being silently reused.
    other=sql("SELECT course_id FROM course WHERE course_id<>'"+C+"' LIMIT 1;")
    sql("INSERT INTO course_enrollment(user_id,course_id,status) VALUES ('"+U+"','"+other+"','ACTIVE');")
    request('/students/'+U+'/recommendations/generate',student,{'courseId':other},key,409)
    request('/recommendations/'+rr+'/feedback',outsider,{'feedbackType':'HELPFUL'},str(uuid.uuid4()),403)
    request('/recommendations/'+rr+'/feedback',student,{'feedbackType':'COMPLETED','trusted':True},str(uuid.uuid4()),400)
    start=state();revision=baseline(U,C)
    activity(rid,'completions',rr,status=409)
    feedback(rr,'COMPLETED',str(uuid.uuid4()),status=409)
    fkey=str(uuid.uuid4());f=feedback(rr,'COMPLETED',key=fkey)
    assert not f['trusted'] and not f['updatedMastery']
    assert feedback(rr,'COMPLETED',key=fkey)==f
    feedback(rr,'HELPFUL',key=fkey,status=409)
    feedback(rr,'HELPFUL');feedback(rr,'NOT_HELPFUL');feedback(rr,'IGNORED')
    assert next(i for i in recs()['items'] if i['recommendationId']==rr)['opinion']=='NOT_HELPFUL'
    assert state()==start and baseline(U,C)==revision
    viewkey=str(uuid.uuid4());view=activity(rid,'views',rr,viewkey)
    assert activity(rid,'views',rr,viewkey)==view
    activity(rid,'completions',rr,viewkey,status=409)
    donekey=str(uuid.uuid4());done=activity(rid,'completions',rr,donekey)
    assert activity(rid,'completions',rr,donekey)==done and g3counts()[-1]==2
    assert state()['items']==start['items'] and recs()['meta']['stale']
    assert feedback(rr,'COMPLETED',done['sourceEventId'])['trusted']
    feedback(qr,'COMPLETED',done['sourceEventId'],status=409)
    assert request('/students/'+U+'/profile?courseId='+C,student)['resourcePreference']
    second=generate()
    assert all(i['itemId']!=rid for i in second['items'])
    assert generate(key)==first and recs()['batchId']==second['batchId']
    # Draft edits do not modify the published resource snapshot.
    content=request('/resources/'+rid,student)['content']
    sql("UPDATE resource SET content_text='G3 draft changed' WHERE resource_id='"+rid+"';")
    assert request('/resources/'+rid,student)['content']==content
    # Availability changes mark persisted batches stale; no hidden regeneration.
    current=second['items'][0];table='resource' if current['itemType']=='RESOURCE' else 'question';column=table+'_id'
    sql("UPDATE "+table+" SET status='INACTIVE' WHERE "+column+"='"+current['itemId']+"';")
    assert recs()['meta']['stale'] and not next(i for i in recs()['items'] if i['recommendationId']==current['recommendationId'])['available']
    sql("UPDATE "+table+" SET status='ACTIVE' WHERE "+column+"='"+current['itemId']+"';")
    # Trusted question completion is atomic with event consumption and idempotent on rebuild.
    worker(False)
    request('/practice/submissions',student,{**body(question=Q),'recommendationId':qr},str(uuid.uuid4()),409)
    attempt=request('/practice/submissions',student,{**body(question=question['itemId']),'recommendationId':qr},str(uuid.uuid4()))
    request('/students/'+U+'/recommendations/generate',student,{'courseId':C},str(uuid.uuid4()),409)
    assert recs()['meta']['stale']
    sql("CREATE TRIGGER g3_fail_trusted BEFORE INSERT ON recommendation_feedback FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='G3 trusted rollback';",as_root=True)
    worker(True);wait(lambda:sql("SELECT status FROM event_consume_log WHERE event_id='"+attempt['eventId']+"';")=='FAILED')
    assert counts()[2:]==[0,0] and sql("SELECT COUNT(*) FROM recommendation_feedback WHERE source_event_id='"+attempt['eventId']+"';")=='0'
    sql('DROP TRIGGER g3_fail_trusted;',as_root=True)
    request('/admin/events/'+attempt['eventId']+'/retry',admin,{})
    wait(lambda:finished(attempt))
    assert feedback(qr,'COMPLETED',attempt['eventId'])['trusted']
    saved_feedback=g3counts()[2]
    request('/admin/students/'+U+'/rebuild?courseId='+C,admin,{})
    assert g3counts()[2]==saved_feedback and counts()[2]==1
    # A batch write fault rolls back batch/items/receipt together.
    before=g3counts()
    sql("CREATE TRIGGER g3_fail_item BEFORE INSERT ON recommendation_item FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='G3 generation rollback';",as_root=True)
    request('/students/'+U+'/recommendations/generate',student,{'courseId':C},str(uuid.uuid4()),500)
    assert before==g3counts()
    sql('DROP TRIGGER g3_fail_item;',as_root=True)
    latest=generate()
    sql("UPDATE recommendation_batch SET expires_at=UTC_TIMESTAMP()-INTERVAL 1 SECOND WHERE batch_id='"+latest['batchId']+"';")
    assert recs()['meta']['stale']
    feedback(latest['items'][0]['recommendationId'],'HELPFUL')
    assert recs()['meta']['stale']
    saved=recs();savedcounts=g3counts()
    compose('restart','mysql','server');ready()
    assert recs()==saved and g3counts()==savedcounts
    # Verify catalog-version change is stale and refuses generation until explicit migration.
    sql("UPDATE course SET catalog_version='unpublished' WHERE course_id='"+C+"';")
    assert recs()['meta']['stale'] and all(not i['available'] for i in recs()['items'])
    request('/students/'+U+'/recommendations/generate',student,{'courseId':C},str(uuid.uuid4()),409)
    sql("UPDATE course SET catalog_version='java-g1-v1' WHERE course_id='"+C+"';")
    # Exercise the final lock-order change with real consumption, reads, generation and feedback.
    before_interactions=counts()[2]
    mixed_attempts=[]
    def mixed(index):
        if index%4==0:
            attempt=request('/practice/submissions',student,{**body(question=question['itemId']),'recommendationId':qr},str(uuid.uuid4()))
            mixed_attempts.append(attempt)
        elif index%4==1:recs()
        elif index%4==2:feedback(qr,'HELPFUL')
        else:request('/students/'+U+'/recommendations/generate',student,{'courseId':C},str(uuid.uuid4()),(200,409))
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(mixed,range(32)))
    for attempt in mixed_attempts:wait(lambda:finished(attempt))
    assert counts()[2]==before_interactions+8
    for attempt in mixed_attempts:
        assert sql("SELECT COUNT(*) FROM recommendation_feedback WHERE source_event_id='"+attempt['eventId']+"' AND trusted=1;")=='1'
        assert sql("SELECT retry_count FROM event_consume_log WHERE event_id='"+attempt['eventId']+"';")=='0'
    # Concurrent feedback and resource retries also persist exactly once.
    fk=str(uuid.uuid4());vk=str(uuid.uuid4());before=g3counts()
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        repeated=list(pool.map(lambda _:feedback(rr,'IGNORED',key=fk),range(8)))
        views=list(pool.map(lambda _:activity(rid,'views',rr,vk),range(8)))
    assert all(x==repeated[0] for x in repeated) and all(x==views[0] for x in views)
    assert g3counts()[2]==before[2]+1 and g3counts()[-1]==before[-1]+1
    if os.environ.get('PLAYWRIGHT_MODULE'):
        subprocess.run(['node','tests/g3-ui.cjs'],cwd=ROOT,env={**ENV,'G3_URL':'http://127.0.0.1:15176','G3_PROJECT':PROJECT},check=True)
    passed=True
    report={'passed':True,'verifiedAt':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'project':PROJECT,'httpChecks':len(checks),'migrations':5,'getReadOnly':True,'emptyBatchStateAccurate':True,'concurrentIdempotency':True,'mixedConcurrencyWithoutRetries':True,'concurrentFeedbackAndResourceIdempotency':True,'resourceDoesNotUpdateBkt':True,'trustedCompletionRollback':True,'rebuildDoesNotDuplicateFeedback':True,'generationRollback':True,'staleAndExpiry':True,'restartConsistency':True,'desktopBrowser':bool(os.environ.get('PLAYWRIGHT_MODULE')),'volumeRetained':PROJECT+'_f21-data','checks':checks}
    Path(os.environ.get('G3_OUTPUT', str(ROOT/'artifacts/g3-acceptance.json'))).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print('G3 ACCEPTANCE PASS:',len(checks),'HTTP checks')
finally:
    if not passed:print(compose('logs','--tail=60','server')[-7000:])
    compose('down')
